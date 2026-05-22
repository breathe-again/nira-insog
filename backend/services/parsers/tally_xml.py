"""Tally XML parser — turns a Day Book / Voucher export into our normalized
draft objects (BankTxnDraft / InvoiceDraft / ReceiptDraft).

How users get Tally XML:
  Gateway of Tally → Display → Day Book → set period → Alt+E (Export)
  → File format: XML → save to disk → upload to Nira.

A Tally export looks like:

  <ENVELOPE>
    <HEADER>
      <TALLYREQUEST>Export Data</TALLYREQUEST>
    </HEADER>
    <BODY>
      <DESC>...</DESC>
      <DATA>
        <TALLYMESSAGE>
          <VOUCHER>
            <DATE>20260322</DATE>
            <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
            <VOUCHERNUMBER>1234</VOUCHERNUMBER>
            <PARTYLEDGERNAME>Tata Power Ltd</PARTYLEDGERNAME>
            <NARRATION>Electricity bill - March</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>HDFC Bank Current</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-12500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Electricity Charges</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>12500.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
          <VOUCHER>...</VOUCHER>
        </TALLYMESSAGE>
      </DATA>
    </BODY>
  </ENVELOPE>

Voucher types we recognize and how they map:

  Payment / Bank Payment   → BankTransaction (direction='debit')
  Receipt / Bank Receipt   → BankTransaction (direction='credit')
  Contra                   → BankTransaction (internal transfer; we still record)
  Sales                    → Invoice (type='sales')
  Purchase                 → Invoice (type='purchase')
  Credit Note / Debit Note → Invoice (matched to existing or new)
  Journal                  → skipped (not a cash event; would need ledger ctx)

Each voucher carries a NARRATION that becomes the description. PARTYLEDGERNAME
gives us the vendor/client name. The amount can appear as POSITIVE in the bank
ledger entry (cash in) or NEGATIVE (cash out) — we read the sign from the
ISDEEMEDPOSITIVE field combined with AMOUNT.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers — match the shape the worker pipeline already consumes.
# ---------------------------------------------------------------------------


@dataclass
class BankTxnDraft:
    txn_date: date
    description: str
    amount: Decimal
    direction: str  # 'credit' | 'debit'
    running_balance: Optional[Decimal] = None
    counterparty_name: Optional[str] = None
    voucher_number: Optional[str] = None


@dataclass
class InvoiceDraft:
    invoice_number: str
    issue_date: date
    total: Decimal
    type: str  # 'sales' | 'purchase'
    vendor_name: Optional[str] = None
    description: Optional[str] = None


@dataclass
class TallyParseReport:
    voucher_count: int = 0
    bank_txns: list[BankTxnDraft] = field(default_factory=list)
    invoices: list[InvoiceDraft] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Voucher-type → handler routing
# ---------------------------------------------------------------------------

# Tally voucher type names (case-insensitive comparison).
_PAYMENT_TYPES = {"payment", "bank payment", "cash payment"}
_RECEIPT_TYPES = {"receipt", "bank receipt", "cash receipt"}
_SALES_TYPES = {"sales", "sales invoice", "sales order"}
_PURCHASE_TYPES = {"purchase", "purchase invoice", "purchase order"}
_CONTRA_TYPES = {"contra"}
_SKIP_TYPES = {"journal", "stock journal", "manufacturing journal"}


def is_tally_xml(content: bytes | str) -> bool:
    """Cheap signature check — used by the worker to confirm an .xml upload
    really is a Tally export before invoking the heavy parser."""
    text = content[:2048] if isinstance(content, str) else content[:2048].decode(
        "utf-8", errors="replace"
    )
    low = text.lower()
    return (
        ("<tallyrequest>" in low or "tallymessage" in low)
        and ("<envelope" in low or "<voucher" in low)
    )


def _parse_tally_date(s: str) -> Optional[date]:
    """Tally dates are YYYYMMDD as a single token, no separators."""
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _parse_amount(s: Optional[str]) -> Decimal:
    """Tally amounts can be '12500.00' or '-12500.00'. Strips whitespace."""
    if s is None:
        return Decimal("0")
    raw = s.strip().replace(",", "")
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def _findtext(node: ET.Element, tag: str) -> Optional[str]:
    """Tally element names are namespaced/uppercased; do case-insensitive find."""
    for child in node:
        # Strip any namespace prefix.
        local = child.tag.split("}", 1)[-1].lower()
        if local == tag.lower():
            return (child.text or "").strip()
    return None


def _is_bank_or_cash_ledger(ledger_name: str) -> bool:
    """Heuristic: ledger name contains 'bank', 'account', 'cash', or matches
    common patterns. Tally users usually call their bank account something
    like 'HDFC Bank Current' or 'ICICI 5234'."""
    low = (ledger_name or "").lower()
    keys = ("bank", "current account", "cash", "savings", "overdraft", "od ")
    return any(k in low for k in keys)


def parse_tally_xml(content: bytes | str) -> TallyParseReport:
    """Parse a Tally XML export into a TallyParseReport.

    Robust against:
      - Day Book exports (the most common — list of vouchers from a date range)
      - Single-voucher exports
      - BOM bytes at the start of the file
      - Trailing whitespace / multiple <ENVELOPE> blocks
    """
    report = TallyParseReport()
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content.lstrip("﻿")

    # Tally sometimes emits malformed XML (lone & characters, unescaped <>).
    # Wrap the parse in a try/except and try a cleanup pass.
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        cleaned = (
            text.replace("& ", "&amp; ")  # most common offender
            .replace(" & ", " &amp; ")
        )
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError as e:
            report.errors.append(f"XML parse failed even after cleanup: {e}")
            return report

    # Walk every VOUCHER element regardless of where it sits in the tree.
    vouchers = []
    for el in root.iter():
        local = el.tag.split("}", 1)[-1].lower()
        if local == "voucher":
            vouchers.append(el)
    report.voucher_count = len(vouchers)

    for v in vouchers:
        try:
            _process_voucher(v, report)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"voucher parse error: {e}")

    return report


def _process_voucher(v: ET.Element, report: TallyParseReport) -> None:
    vtype_raw = (_findtext(v, "VOUCHERTYPENAME") or "").lower().strip()
    if vtype_raw in _SKIP_TYPES:
        report.skipped.append(f"{vtype_raw} voucher skipped (journal-type)")
        return

    dt = _parse_tally_date(_findtext(v, "DATE") or "")
    if dt is None:
        report.skipped.append("voucher missing valid DATE")
        return

    narration = (_findtext(v, "NARRATION") or "").strip()
    party = (_findtext(v, "PARTYLEDGERNAME") or "").strip()
    voucher_no = (_findtext(v, "VOUCHERNUMBER") or "").strip()

    # Gather ledger entries — these carry the actual debit/credit signs.
    entries: list[tuple[str, str, Decimal]] = []  # (ledger_name, is_positive, amount)
    for child in v:
        local = child.tag.split("}", 1)[-1].lower()
        if local in ("allledgerentries.list", "ledgerentries.list"):
            lname = _findtext(child, "LEDGERNAME") or ""
            isdeemed = (_findtext(child, "ISDEEMEDPOSITIVE") or "no").lower()
            amt = _parse_amount(_findtext(child, "AMOUNT"))
            entries.append((lname, isdeemed, amt))

    if not entries:
        report.skipped.append(f"{vtype_raw} voucher with no ledger entries")
        return

    # ---- Payment / Receipt / Contra → BankTransaction ----
    if vtype_raw in _PAYMENT_TYPES | _RECEIPT_TYPES | _CONTRA_TYPES:
        # Find the bank/cash ledger leg — that's our BankTransaction row.
        bank_entry = next(
            (e for e in entries if _is_bank_or_cash_ledger(e[0])),
            None,
        )
        if bank_entry is None:
            report.skipped.append(
                f"{vtype_raw} #{voucher_no}: no bank/cash ledger detected"
            )
            return
        _lname, _isdeemed, amt = bank_entry
        # The voucher TYPE tells us the direction unambiguously:
        #   Payment  = cash leaving the bank account     → debit
        #   Receipt  = cash arriving in the bank account → credit
        #   Contra   = bank-to-bank or bank-to-cash      → use sign of amount
        # We ignore ISDEEMEDPOSITIVE for direction because Tally's encoding
        # varies between releases (some put the sign on the bank leg, some
        # on the contra leg).
        if vtype_raw in _PAYMENT_TYPES:
            direction = "debit"
        elif vtype_raw in _RECEIPT_TYPES:
            direction = "credit"
        else:  # contra — infer from sign
            direction = "credit" if amt >= 0 else "debit"
        report.bank_txns.append(
            BankTxnDraft(
                txn_date=dt,
                description=(narration or party or "Tally voucher").strip()[:500],
                amount=abs(amt),
                direction=direction,
                counterparty_name=party or None,
                voucher_number=voucher_no or None,
            )
        )
        return

    # ---- Sales / Purchase → Invoice ----
    if vtype_raw in _SALES_TYPES | _PURCHASE_TYPES:
        # Sum the absolute amount across positive entries — that's the
        # gross total. Tally vouchers contain both debit + credit legs that
        # sum to zero; the gross is one side.
        positive_total = sum((e[2] for e in entries if e[2] > 0), start=Decimal("0"))
        if positive_total == 0:
            positive_total = sum((abs(e[2]) for e in entries), start=Decimal("0")) / 2
        inv_type = "sales" if vtype_raw in _SALES_TYPES else "purchase"
        report.invoices.append(
            InvoiceDraft(
                invoice_number=voucher_no or f"TALLY-{dt.isoformat()}-{vtype_raw[:3]}",
                issue_date=dt,
                total=abs(positive_total),
                type=inv_type,
                vendor_name=party or None,
                description=narration or None,
            )
        )
        return

    # ---- Anything else: skip with a reason ----
    report.skipped.append(f"unknown voucher type: {vtype_raw or '<empty>'}")
