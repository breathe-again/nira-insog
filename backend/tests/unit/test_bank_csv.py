"""Unit tests for the bank-statement CSV parser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.parsers.bank_csv import (
    BankTxnDraft,
    extract_vendor_hint,
    parse_bank_csv,
)


# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------


SAMPLE_CSV = (
    "Date,Description,Debit,Credit,Balance\n"
    "2026-04-01,Opening Balance,,,4200000.00\n"
    "2026-04-02,NEFT IN - ACME CORP - INV/2026/031,,142000.00,4342000.00\n"
    "2026-04-02,UPI - ZOMATO - LUNCH MEETING,485.00,,4341515.00\n"
    "2026-04-03,SALARY - APRIL PAYROLL,840000.00,,3501515.00\n"
    "2026-04-09,RENT - OFFICE - APR 2026,160000.00,,3192515.00\n"
)


HDFC_LIKE_CSV = (
    "Txn Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
    "01-04-2026,Opening Balance,,,4200000.00\n"
    "02-04-2026,NEFT-CR-ACME CORP-INV2026031,,\"1,42,000.00\",\"43,42,000.00\"\n"
    "02-04-2026,UPI-ZOMATO-LUNCH,485.00,,4341515.00\n"
)


# ---------------------------------------------------------------------------
# parse_bank_csv
# ---------------------------------------------------------------------------


def test_sample_csv_parses_all_amount_rows():
    drafts, report = parse_bank_csv(SAMPLE_CSV)

    # The opening balance row has no debit or credit and is skipped.
    assert report.rows_total == 5
    assert report.rows_parsed == 4
    assert len(drafts) == 4
    assert report.errors == []


def test_directions_are_assigned_from_debit_credit_columns():
    """Two transactions share 2026-04-02 (one credit, one debit) — filter by direction."""
    drafts, _ = parse_bank_csv(SAMPLE_CSV)

    inflows = [d for d in drafts if d.direction == "credit"]
    debits = [d for d in drafts if d.direction == "debit"]

    assert len(inflows) == 1
    assert inflows[0].amount == Decimal("142000.00")
    assert inflows[0].txn_date == date(2026, 4, 2)

    salary = next(d for d in debits if "SALARY" in d.description)
    assert salary.amount == Decimal("840000.00")
    assert salary.txn_date == date(2026, 4, 3)


def test_vendor_hints_strip_channel_prefix():
    drafts, _ = parse_bank_csv(SAMPLE_CSV)
    hints = {(d.txn_date, d.direction): d.raw_vendor_hint for d in drafts}

    assert hints[(date(2026, 4, 2), "credit")] == "ACME CORP"
    # ZOMATO has same date as the NEFT row but direction=debit.
    assert hints[(date(2026, 4, 2), "debit")] == "ZOMATO"
    assert hints[(date(2026, 4, 3), "debit")] == "APRIL PAYROLL"  # SALARY is the channel
    # RENT isn't in the channel list — so the hint is "RENT" itself.
    # This is acceptable: the vendor resolver will create a "RENT" vendor row.
    assert hints[(date(2026, 4, 9), "debit")] == "RENT"


def test_running_balance_is_parsed():
    drafts, _ = parse_bank_csv(SAMPLE_CSV)
    by_date = {d.txn_date: d for d in drafts}
    salary = by_date[date(2026, 4, 3)]
    assert salary.running_balance == Decimal("3501515.00")


def test_alternative_header_names_and_indian_number_format():
    drafts, report = parse_bank_csv(HDFC_LIKE_CSV)
    assert report.rows_parsed == 2
    inflow = next(d for d in drafts if d.direction == "credit")
    assert inflow.amount == Decimal("142000.00")
    assert inflow.txn_date == date(2026, 4, 2)


def test_bytes_input_with_utf8_bom_is_handled():
    raw = ("﻿" + SAMPLE_CSV).encode("utf-8")
    drafts, report = parse_bank_csv(raw)
    assert report.rows_parsed == 4
    assert len(drafts) == 4


def test_empty_csv_reports_error_without_crashing():
    drafts, report = parse_bank_csv("")
    assert drafts == []
    assert report.errors


def test_missing_date_column_reports_error():
    bad = "Description,Amount\nfoo,100\n"
    drafts, report = parse_bank_csv(bad)
    assert drafts == []
    assert any("date" in e.lower() for e in report.errors)


def test_amount_and_type_columns_combined():
    """SBI-ish: single amount column with a Dr/Cr column."""
    csv_text = (
        "Date,Particulars,Amount,Type,Balance\n"
        "2026-05-01,XYZ Traders,1000.00,Dr,10000\n"
        "2026-05-02,Refund From XYZ,500.00,Cr,10500\n"
    )
    drafts, report = parse_bank_csv(csv_text)
    assert report.rows_parsed == 2
    by_dir = {d.direction: d for d in drafts}
    assert by_dir["debit"].amount == Decimal("1000.00")
    assert by_dir["credit"].amount == Decimal("500.00")


def test_parenthesized_negative_amount_is_debit():
    """Some banks render debits as '(485.00)' in a single amount column."""
    csv_text = (
        "Date,Description,Amount,Balance\n"
        "2026-05-01,Coffee Shop,(485.00),99515.00\n"
    )
    drafts, report = parse_bank_csv(csv_text)
    assert report.rows_parsed == 1
    assert drafts[0].direction == "debit"
    assert drafts[0].amount == Decimal("485.00")


def test_blank_rows_are_skipped_not_errored():
    csv_text = SAMPLE_CSV + "\n,,,,\n"
    _drafts, report = parse_bank_csv(csv_text)
    # Blank row is counted as skipped, not errored.
    assert report.errors == []


# ---------------------------------------------------------------------------
# extract_vendor_hint — direct unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("NEFT IN - ACME CORP - INV/2026/031", "ACME CORP"),
        ("UPI - ZOMATO - LUNCH MEETING", "ZOMATO"),
        ("RTGS OUT / GLOBEX LTD / Q1 SETTLEMENT", "GLOBEX LTD"),
        ("IMPS-PAYTM-WALLET LOAD", "PAYTM"),
        ("Opening Balance", None),
        ("", None),
        ("    ", None),
    ],
)
def test_extract_vendor_hint_variants(desc, expected):
    assert extract_vendor_hint(desc) == expected


def test_extract_vendor_hint_strips_trailing_ref_codes():
    assert (
        extract_vendor_hint("NEFT IN - ACME CORP UTR1234567890 - INV/2026/031")
        == "ACME CORP"
    )


# ---------------------------------------------------------------------------
# BankTxnDraft.as_dict — JSON-friendly shape
# ---------------------------------------------------------------------------


def test_draft_as_dict_is_json_serializable():
    drafts, _ = parse_bank_csv(SAMPLE_CSV)
    d = drafts[0].as_dict()
    assert isinstance(d["txn_date"], str)
    assert isinstance(d["amount"], str)
    assert d["direction"] in ("credit", "debit")
