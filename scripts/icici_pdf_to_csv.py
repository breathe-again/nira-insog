#!/usr/bin/env python3
"""Convert an ICICI 'OpTransactionHistory' PDF into a CSV our parser understands.

Usage:
    python3 scripts/icici_pdf_to_csv.py /path/to/statement.pdf [-o /tmp/out.csv]

How it works:
- ICICI's PDF has selectable text but the table cells aren't bordered, so
  pdfplumber's table extractor returns nothing useful. We extract words with
  their (x, y) positions and group them by row using y-clustering.
- Once rows are reconstructed, we identify anchor rows (those containing a
  DD.MM.YYYY date) and treat multi-line descriptions as continuations of the
  anchor in the same "cell."
- Output: Date, Description, Debit, Credit, Balance — exactly what
  backend/services/parsers/bank_csv.py expects.

Why this exists:
- Net banking's CSV / Excel export is the preferred input. This script is a
  bridge for when you only have the PDF.
- Once Tesseract + LLM extraction lands, PDFs will be ingested directly.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pdfplumber  # type: ignore
except ImportError:
    print(
        "pdfplumber not installed. Run:\n"
        "  pip3 install --break-system-packages pdfplumber\n",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Match DD.MM.YYYY / DD-MM-YYYY / DD/MM/YYYY anchored to a whole word.
DATE_RE = re.compile(r"^\d{2}[./-]\d{2}[./-]\d{4}$")

# Strict amount: must have a decimal point + 2 decimals to avoid matching
# refs / phone numbers / dates. Allow any number of leading digits because
# ICICI prints uncommafied amounts like 13719.79.
AMOUNT_RE = re.compile(r"^-?\d+(?:,\d{2,3})*\.\d{2}$")

# Things to ignore entirely — page headers, footers, statement title.
SKIP_TOKENS = {
    "Statement", "Transactions", "Saving", "Account", "INR", "period",
    "Your", "Base", "Branch", "ICICI", "BANK", "LIMITED",
    "Cheque", "Number", "Transaction", "Remarks", "Withdrawal", "Deposit",
    "Amount", "Balance", "Date",
    "Legends", "Page", "End", "of", "statement",
}


# ---------------------------------------------------------------------------
# Word/row extraction
# ---------------------------------------------------------------------------


def _words_to_rows(words: list[dict]) -> list[list[dict]]:
    """Group word dicts into rows by clustering on the `top` coordinate.

    pdfplumber gives each word an exact (x0, x1, top, bottom). Two words on
    the same printed line have very close `top` values.
    """
    if not words:
        return []
    by_top: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        # Bucket by integer-rounded top — within 2pt = same line.
        bucket = round(w["top"] / 2) * 2
        by_top[bucket].append(w)
    rows = []
    for top in sorted(by_top.keys()):
        line = sorted(by_top[top], key=lambda w: w["x0"])
        rows.append(line)
    return rows


def _row_text(row: list[dict]) -> str:
    return " ".join(w["text"] for w in row)


def _find_date(row: list[dict]) -> tuple[int, str] | None:
    """Return (index_in_row, date_string) if this row has a DD.MM.YYYY token."""
    for i, w in enumerate(row):
        if DATE_RE.match(w["text"]):
            return i, w["text"]
    return None


def _find_amounts(row: list[dict], after_idx: int) -> list[tuple[float, str]]:
    """Return (x0, value) for every amount token after position `after_idx`."""
    out = []
    for w in row[after_idx + 1:]:
        if AMOUNT_RE.match(w["text"]):
            out.append((w["x0"], w["text"].replace(",", "")))
    return out


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_rows(pdf_path: Path) -> list[list[str]]:
    """Return [Date, Description, Debit, Credit, Balance] rows."""
    out: list[list[str]] = []

    # Need to know the x-position of each amount column so we can tell a
    # withdrawal from a deposit when only one is present. We learn it from
    # rows that have BOTH columns filled.
    debit_x_estimates: list[float] = []
    credit_x_estimates: list[float] = []
    balance_x_estimates: list[float] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(
                x_tolerance=2, y_tolerance=2, keep_blank_chars=False
            )
            rows = _words_to_rows(words)

            i = 0
            while i < len(rows):
                row = rows[i]
                dated = _find_date(row)
                if dated is None:
                    i += 1
                    continue

                date_idx, date_str = dated
                amounts = _find_amounts(row, date_idx)
                if not amounts:
                    # An anchor must have at least the balance amount.
                    i += 1
                    continue

                # Balance = rightmost amount.
                balance_x, balance = amounts[-1]
                balance_x_estimates.append(balance_x)

                debit = ""
                credit = ""
                if len(amounts) >= 3:
                    debit = amounts[-3][1]
                    credit = amounts[-2][1]
                    debit_x_estimates.append(amounts[-3][0])
                    credit_x_estimates.append(amounts[-2][0])
                elif len(amounts) == 2:
                    other_x, other_val = amounts[-2]
                    # Decide debit-vs-credit by comparing x to learned column.
                    # If we don't have estimates yet, fall back: amounts left
                    # of the balance column by > 60pt are usually withdrawal;
                    # closer than that, usually deposit.
                    dr_mean = (
                        sum(debit_x_estimates) / len(debit_x_estimates)
                        if debit_x_estimates else None
                    )
                    cr_mean = (
                        sum(credit_x_estimates) / len(credit_x_estimates)
                        if credit_x_estimates else None
                    )
                    if dr_mean is not None and cr_mean is not None:
                        # Pick the column we're closer to.
                        if abs(other_x - dr_mean) <= abs(other_x - cr_mean):
                            debit = other_val
                        else:
                            credit = other_val
                    else:
                        # Heuristic until we've seen a both-columns row.
                        if balance_x - other_x > 60:
                            debit = other_val
                        else:
                            credit = other_val

                # ---- Description ----
                # ICICI's layout: each transaction's narration starts on the
                # line ABOVE its anchor with a channel code (UPI/, SGB/, ...).
                # Continuation lines follow until the NEXT transaction's first
                # narration line — which sits ABOVE the next anchor.
                #
                # So a transaction's full description = the band of lines
                # between the PREVIOUS anchor and THIS anchor, trimmed to the
                # last channel-code start.
                desc_parts: list[str] = []
                j = i - 1
                while j >= 0:
                    prev = rows[j]
                    if _find_date(prev) is not None:
                        break  # hit previous anchor
                    text = " ".join(
                        w["text"] for w in prev
                        if w["text"] not in SKIP_TOKENS
                    ).strip()
                    if text and not all(t in SKIP_TOKENS for t in text.split()):
                        desc_parts.insert(0, text)
                    j -= 1

                description = _trim_to_last_channel(" ".join(desc_parts))

                # Strip a stray serial-number prefix if it leaked in.
                description = re.sub(r"^\s*\d{1,4}\s+", "", description).strip()

                out.append([date_str, description, debit, credit, balance])
                i += 1

    return out


# Codes that mark the start of an ICICI transaction narration. We trim
# preceding text to the LAST occurrence so each row gets its own narration.
_CHANNEL_CODES = (
    "UPI/", "BIL/", "IMPS/", "NEFT/", "RTGS/", "MMT/", "ACH/", "ATM/",
    "POS/", "INF/", "CHQ/", "TRF/", "SGB/", "NACH/", "MOB/", "CMS/",
    "MAT/", "INST/",
)


def _trim_to_last_channel(text: str) -> str:
    """If `text` contains a channel code (UPI/, BIL/, ...), return the text
    starting at the LAST such code. Otherwise return the text unchanged.
    """
    if not text:
        return text
    last_idx = -1
    for code in _CHANNEL_CODES:
        idx = text.rfind(code)
        if idx > last_idx:
            last_idx = idx
    if last_idx <= 0:
        return text
    return text[last_idx:].strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert an ICICI PDF statement to CSV.")
    ap.add_argument("pdf", type=Path, help="Path to the ICICI PDF.")
    ap.add_argument(
        "-o", "--out", type=Path, default=Path("/tmp/icici_extracted.csv"),
        help="Output CSV path (default: /tmp/icici_extracted.csv)",
    )
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"Not found: {args.pdf}", file=sys.stderr)
        return 1

    rows = extract_rows(args.pdf)
    if not rows:
        print(
            "No transaction-looking rows found.\n"
            "The PDF might be a scanned image (needs OCR) or use a layout "
            "this script doesn't recognize.\n"
            "Try re-downloading the statement as CSV from ICICI net banking.",
            file=sys.stderr,
        )
        return 3

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Debit", "Credit", "Balance"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows → {args.out}")
    print()
    print("Preview (first 6 transaction rows):")
    for row in rows[:6]:
        d = row[1][:70] + ("…" if len(row[1]) > 70 else "")
        print(
            f"  {row[0]} | {d:<71} | "
            f"Dr={row[2]!s:>10} | Cr={row[3]!s:>10} | Bal={row[4]}"
        )
    print()
    print("Now upload it:")
    print(f"  curl -F 'file=@{args.out}' http://localhost:8000/api/documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
