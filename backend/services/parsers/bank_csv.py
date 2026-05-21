"""Bank-statement CSV parser.

Designed to be tolerant of the messy real world. Indian banks emit a long tail
of CSV layouts; we don't try to handle them all, but we do handle the common
shapes:

    Date, Description, Debit, Credit, Balance              ← sample we ship
    Txn Date, Narration, Withdrawal, Deposit, Closing Bal  ← HDFC-ish
    Date, Particulars, Dr/Cr, Amount, Balance              ← SBI-ish
    Date, Description, Amount, Type, Balance               ← generic

The output is a list of `BankTxnDraft` — plain dataclasses, no ORM yet.
The caller is responsible for inserting them and attaching org_id + document_id.

What this parser DOES:
- Detects header column names (case/space/punct-insensitive).
- Parses dates from several common formats.
- Normalizes "1,42,000.00" / "(485.00)" / "485 Cr" into Decimal + direction.
- Drops opening-balance rows (no amount on either side).
- Extracts a best-guess vendor name from the description.

What it does NOT do:
- Match against existing vendors (that's vendors.resolve_vendor).
- Compute anomalies (that's anomalies.detect_for_transactions).
- Touch the database.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BankTxnDraft:
    """One parsed bank-statement row, ready for vendor resolution + insert."""

    txn_date: date
    description: str
    amount: Decimal
    direction: str  # "credit" or "debit"
    running_balance: Optional[Decimal] = None
    raw_vendor_hint: Optional[str] = None  # best guess from description
    row_number: int = 0  # 1-based, for error reporting

    def as_dict(self) -> dict:
        return {
            "txn_date": self.txn_date.isoformat(),
            "description": self.description,
            "amount": str(self.amount),
            "direction": self.direction,
            "running_balance": (
                str(self.running_balance) if self.running_balance is not None else None
            ),
            "raw_vendor_hint": self.raw_vendor_hint,
            "row_number": self.row_number,
        }


@dataclass(slots=True)
class ParseReport:
    """What the parser saw — useful for surfacing errors back to the user."""

    rows_total: int = 0
    rows_parsed: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------

_DATE_KEYS = ("date", "txn date", "transaction date", "value date", "posting date")
_DESC_KEYS = ("description", "narration", "particulars", "details", "remarks")
_DEBIT_KEYS = ("debit", "withdrawal", "withdrawal amt", "dr", "debit amount")
_CREDIT_KEYS = ("credit", "deposit", "deposit amt", "cr", "credit amount")
_AMOUNT_KEYS = ("amount", "txn amount", "transaction amount")
_TYPE_KEYS = ("type", "dr/cr", "dr cr", "txn type")
_BALANCE_KEYS = (
    "balance",
    "closing balance",
    "closing bal",
    "running balance",
    "available balance",
)


def _norm(header: str) -> str:
    return re.sub(r"[\s_\-./]+", " ", header.strip().lower())


def _pick(headers: list[str], candidates: tuple[str, ...]) -> Optional[int]:
    """Return the index of the first header that matches any candidate."""
    normalized = [_norm(h) for h in headers]
    for idx, h in enumerate(normalized):
        if h in candidates:
            return idx
    # Fallback: substring match — handles "Withdrawal Amt." vs "withdrawal amt"
    for idx, h in enumerate(normalized):
        for c in candidates:
            if c in h:
                return idx
    return None


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",   # ICICI export format
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
)


def _parse_date(value: str) -> Optional[date]:
    s = value.strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_AMOUNT_CLEAN_RE = re.compile(r"[^\d.\-()]")


def _parse_amount(value: str) -> Optional[Decimal]:
    """Tolerate '1,42,000.00', '(485.00)', '485 Cr', '485.00 -' etc."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "-", "--"):
        return None

    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1]
    if s.endswith("-"):
        is_negative = True
        s = s[:-1]

    cleaned = _AMOUNT_CLEAN_RE.sub("", s)
    if not cleaned or cleaned in (".", "-", "()"):
        return None
    try:
        d = Decimal(cleaned)
    except InvalidOperation:
        return None
    if is_negative:
        d = -d
    return d


# ---------------------------------------------------------------------------
# Vendor hint extraction
# ---------------------------------------------------------------------------

# Payment-channel prefixes that aren't vendor names. Order matters — longer first.
_CHANNEL_PREFIXES = (
    "NEFT IN",
    "NEFT OUT",
    "NEFT CR",
    "NEFT DR",
    "NEFT",
    "RTGS IN",
    "RTGS OUT",
    "RTGS",
    "IMPS IN",
    "IMPS OUT",
    "IMPS",
    "UPI IN",
    "UPI OUT",
    "UPI",
    "ACH DR",
    "ACH CR",
    "ACH",
    "NACH",
    "ATM",
    "POS",
    "CARD",
    "CHQ",
    "CHEQUE",
    "CASH DEP",
    "CASH WDL",
    "CASH",
    "SALARY",
    "TRANSFER",
    "TFR",
    "BIL/",
    "BIL ",
)

# Tail garbage we drop after the vendor token.
_TAIL_GARBAGE_RE = re.compile(
    r"\b("
    r"INV[-/]\S+|"  # invoice refs
    r"REF[-/:]?\S+|"
    r"UTR[-/:]?\S+|"
    r"TXN[-/:]?\S+|"
    r"\d{8,}"  # long numeric ids
    r")\b",
    re.IGNORECASE,
)


def extract_vendor_hint(description: str) -> Optional[str]:
    """Pull a best-guess vendor/counterparty name out of a free-text description.

    Two patterns we recognize:

      Pattern A (vendor at the front — most banks):
          "UPI/SWIGGY/PAYMENT/REF123"      → SWIGGY
          "NEFT-TATA POWER LTD-..."        → TATA POWER LTD
          "IMPS/PAYTM-..."                 → PAYTM

      Pattern B (vendor at the trailing segment — Tally Bank, some HDFC formats):
          "INF/NEFT/<ref>/<ifsc>/<otherref> by <user> from <bank>/Abhijit"   → Abhijit
          "INFT/<ref> by VINAYBAW from Tally Bank Plu/LaxmiNarayanS"        → LaxmiNarayanS

    We detect Pattern B by the presence of " by <name> from <name>" in the
    description — that's a signature of bank-side machine-generated narrations
    where the actual destination is the last "/" segment after "from <bank>".
    """
    if not description:
        return None

    s = description.strip()
    upper = s.upper()

    # ---- Pattern B detection: "by ... from <bank>/<destination>" -----------
    # If this is a bank-emitted INF/INFT/NEFT narration with a "by X from Y/Z"
    # tail, the destination is what comes after the LAST "/".
    if re.search(r"\bby\s+\S+\s+from\b", s, flags=re.IGNORECASE):
        # Take the segment after the last "/" — that's the destination name.
        tail = s.rsplit("/", 1)[-1].strip()
        # Drop any trailing alphanum-ref garbage.
        tail = _TAIL_GARBAGE_RE.sub("", tail).strip(" -:/|")
        if tail and len(tail) >= 2:
            # Camel-case names like "AbhijitC", "KaustavM" → split if possible.
            hint = re.sub(r"\s+", " ", tail)
            # Skip if the tail is itself a channel-code (means the narration
            # had no real destination name).
            if hint.upper() not in {"INF", "INFT", "NEFT", "RTGS", "IMPS", "TRF", "TRFR"}:
                return hint

    # ---- Pattern A: vendor at the front ---------------------------------
    # Strip a leading channel prefix and the separator that follows it.
    for prefix in _CHANNEL_PREFIXES:
        if upper.startswith(prefix):
            s = s[len(prefix) :].lstrip(" -:|/")
            upper = s.upper()
            break

    # Take everything up to the next separator (- / |), space-optional so we
    # handle both "UPI - ZOMATO - ..." and "IMPS-PAYTM-..." patterns.
    parts = re.split(r"\s*[-/|]\s*", s, maxsplit=1)
    hint = parts[0].strip()

    # Strip trailing dates / refs / numbers.
    hint = _TAIL_GARBAGE_RE.sub("", hint).strip(" -:/|")

    # Collapse whitespace.
    hint = re.sub(r"\s+", " ", hint)

    if not hint or len(hint) < 2:
        return None

    # Skip obvious non-vendor tokens.
    if hint.lower() in ("opening balance", "closing balance", "balance b/f", "balance c/f"):
        return None

    # Skip generic bank-side prefixes that aren't real vendors.
    if hint.upper() in {"INF", "INFT", "TRF", "TRFR"}:
        return None

    return hint


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_bank_csv(content: str | bytes) -> tuple[list[BankTxnDraft], ParseReport]:
    """Parse a bank-statement CSV into BankTxnDraft objects.

    Returns (drafts, report). The report carries per-row errors so callers can
    decide how much to tolerate before marking the document as 'error'.
    """
    if isinstance(content, bytes):
        # Most Indian bank CSVs are UTF-8 or ASCII; tolerate BOM.
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content.lstrip("﻿")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    report = ParseReport()
    drafts: list[BankTxnDraft] = []

    if not rows:
        report.errors.append("empty CSV")
        return drafts, report

    # Locate header row — first row with a recognizable date column.
    header_idx = None
    for i, row in enumerate(rows[:5]):  # only scan the first few rows
        if _pick(row, _DATE_KEYS) is not None:
            header_idx = i
            break
    if header_idx is None:
        report.errors.append("could not find a header row with a date column")
        return drafts, report

    headers = rows[header_idx]
    date_col = _pick(headers, _DATE_KEYS)
    desc_col = _pick(headers, _DESC_KEYS)
    debit_col = _pick(headers, _DEBIT_KEYS)
    credit_col = _pick(headers, _CREDIT_KEYS)
    amount_col = _pick(headers, _AMOUNT_KEYS)
    type_col = _pick(headers, _TYPE_KEYS)
    balance_col = _pick(headers, _BALANCE_KEYS)

    if desc_col is None:
        report.errors.append("could not find a description/narration column")
        return drafts, report
    if debit_col is None and credit_col is None and amount_col is None:
        report.errors.append(
            "could not find any amount column (debit/credit/amount)"
        )
        return drafts, report

    for raw_idx, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        report.rows_total += 1
        if not any((c or "").strip() for c in row):
            report.rows_skipped += 1
            continue

        try:
            draft = _parse_row(
                row,
                row_number=raw_idx,
                date_col=date_col,
                desc_col=desc_col,
                debit_col=debit_col,
                credit_col=credit_col,
                amount_col=amount_col,
                type_col=type_col,
                balance_col=balance_col,
            )
        except _RowError as e:
            report.rows_skipped += 1
            report.errors.append(f"row {raw_idx}: {e}")
            continue

        if draft is None:
            report.rows_skipped += 1
            continue

        drafts.append(draft)
        report.rows_parsed += 1

    return drafts, report


class _RowError(ValueError):
    """Internal — signals a row we couldn't parse but want to report on."""


def _cell(row: list[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _parse_row(
    row: list[str],
    *,
    row_number: int,
    date_col: Optional[int],
    desc_col: int,
    debit_col: Optional[int],
    credit_col: Optional[int],
    amount_col: Optional[int],
    type_col: Optional[int],
    balance_col: Optional[int],
) -> Optional[BankTxnDraft]:
    txn_date = _parse_date(_cell(row, date_col))
    if txn_date is None:
        # Skip rows without a date (could be a sub-header or footnote).
        return None

    description = _cell(row, desc_col)

    # Resolve amount + direction.
    debit_amt = _parse_amount(_cell(row, debit_col)) if debit_col is not None else None
    credit_amt = _parse_amount(_cell(row, credit_col)) if credit_col is not None else None

    direction: Optional[str] = None
    amount: Optional[Decimal] = None

    if debit_amt and debit_amt != 0:
        direction = "debit"
        amount = abs(debit_amt)
    elif credit_amt and credit_amt != 0:
        direction = "credit"
        amount = abs(credit_amt)
    elif amount_col is not None:
        signed = _parse_amount(_cell(row, amount_col))
        if signed is None or signed == 0:
            return None  # opening balance / blank line
        type_cell = _cell(row, type_col).upper() if type_col is not None else ""
        if "CR" in type_cell or "CREDIT" in type_cell or "DEPOSIT" in type_cell:
            direction = "credit"
            amount = abs(signed)
        elif "DR" in type_cell or "DEBIT" in type_cell or "WITHDRAW" in type_cell:
            direction = "debit"
            amount = abs(signed)
        else:
            # Sign-based fallback: negative = debit.
            if signed < 0:
                direction = "debit"
                amount = abs(signed)
            else:
                direction = "credit"
                amount = signed

    if amount is None or direction is None:
        # No amount on this row — most commonly the opening-balance row.
        return None

    balance = _parse_amount(_cell(row, balance_col)) if balance_col is not None else None
    vendor_hint = extract_vendor_hint(description)

    return BankTxnDraft(
        txn_date=txn_date,
        description=description,
        amount=amount,
        direction=direction,
        running_balance=balance,
        raw_vendor_hint=vendor_hint,
        row_number=row_number,
    )


def iter_parse_bank_csv(content: str | bytes) -> Iterable[BankTxnDraft]:
    """Convenience iterator — same as parse_bank_csv but yields drafts only."""
    drafts, _ = parse_bank_csv(content)
    yield from drafts
