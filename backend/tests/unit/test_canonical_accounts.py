"""Unit tests for the canonical chart-of-accounts classifier.

These tests exercise the pure-Python classification logic without
needing a database. Real DB integration is covered by /tests/test_*.py
running against the docker stack.

The fixtures here are drawn from Quantta Analytics's real Trial Balance
file (TrialBal.xlsx, 117 ledger accounts spanning ₹28.13 Cr) so the
test set reflects production data, not a toy example.
"""
from __future__ import annotations

import pytest

from services.canonical.accounts import classify, all_categories


# Real ledger names from Quantta's TrialBal.xlsx
QUANTTA_FIXTURES = [
    # name, group_path, expected_category
    ("Cash-in-Hand", "Primary>Cash-in-Hand", "cash"),
    ("Petty Cash", None, "cash"),
    ("HDFC Bank - 0046", "Primary>Bank Accounts", "bank"),
    ("ICICI Bank Current A/c", "Primary>Bank Accounts", "bank"),
    ("Sundry Debtors", "Primary>Sundry Debtors", "receivables"),
    ("Bawri Logistics Pvt Ltd", "Primary>Sundry Debtors", "receivables"),
    ("Sundry Creditors", "Primary>Sundry Creditors", "payables"),
    (
        "Lichee Construction Pvt Ltd",
        "Primary>Loans (Liability)>Unsecured Loans",
        "loans_payable",
    ),
    (
        "Vehicle Loan - Tata Capital",
        "Primary>Loans (Liability)>Secured Loans",
        "loans_payable",
    ),
    (
        "SGB Investment - Sovereign Gold Bond",
        "Primary>Investments",
        "investment",
    ),
    ("Mutual Fund - Parag Parikh", "Primary>Investments", "investment"),
    ("Warrants 10 Cr", "Primary>Investments", "investment"),
    ("Equity Share Capital", "Primary>Capital Account", "equity"),
    ("Reserves & Surplus", "Primary>Reserves & Surplus", "equity"),
    ("Analytics Services Revenue", "Primary>Sales Accounts", "income"),
    ("Salary", "Primary>Indirect Expenses", "indirect_expense"),
    ("Office Rent", "Primary>Indirect Expenses", "indirect_expense"),
    ("GST Payable", "Primary>Duties & Taxes", "statutory_liability"),
    ("Gratuity Provision", "Primary>Provisions", "statutory_liability"),
    ("TDS Payable - 194J", "Primary>Duties & Taxes", "statutory_liability"),
    ("Office Equipment", "Primary>Fixed Assets", "fixed_asset"),
    ("Computers & Peripherals", "Primary>Fixed Assets", "fixed_asset"),
    ("Security Deposit", "Primary>Deposits (Asset)", "current_asset"),
    (
        "Advance to Vendor",
        "Primary>Loans & Advances (Asset)",
        "loans_receivable",
    ),
    ("Suspense A/c", "Primary>Suspense A/c", "suspense"),
]


# Edge cases — name only, no group path
NAME_ONLY = [
    ("Petty cash drawer", "cash"),
    ("HDFC Bank xxxx0046", "bank"),
    ("Trade Receivables", "receivables"),
    ("Trade Payables", "payables"),
    ("Term Loan from HDFC", "loans_payable"),
    ("Closing Stock", "inventory"),
    ("Plant & Machinery", "fixed_asset"),
    ("Mutual Fund Investments", "investment"),
    ("Equity Share Capital", "equity"),
    ("Service Revenue", "income"),
    ("Office Salary", "indirect_expense"),
    ("Income tax expense", "tax_expense"),
    ("Random Ledger XYZ", "suspense"),
]


@pytest.mark.parametrize("name,group,expected", QUANTTA_FIXTURES)
def test_classify_quantta_real(name, group, expected):
    cat, _nature = classify(name, group_path=group)
    assert cat == expected, f"{name!r} group={group!r} → {cat} (want {expected})"


@pytest.mark.parametrize("name,expected", NAME_ONLY)
def test_classify_name_only(name, expected):
    cat, _nature = classify(name, group_path=None)
    assert cat == expected, f"{name!r} → {cat} (want {expected})"


def test_classify_caller_hint_overrides_inference():
    # Even though "Random Foo" would normally land in suspense, a valid
    # hint must win.
    cat, _ = classify("Random Foo", hinted_category="bank")
    assert cat == "bank"


def test_classify_invalid_hint_is_ignored():
    # Garbage hints fall through to the inference path.
    cat, _ = classify("HDFC Bank", hinted_category="not-a-real-category")
    assert cat == "bank"


def test_all_categories_valid_set():
    cats = all_categories()
    # Nature mapping must cover every category.
    from services.canonical.accounts import _NATURE
    for c in cats:
        assert c in _NATURE, f"category {c!r} missing from _NATURE"


def test_classify_returns_nature_consistent_with_category():
    """The (category, nature) pair must be internally consistent."""
    NATURES_EXPECTED = {
        "cash": "asset", "bank": "asset", "receivables": "asset",
        "payables": "liability", "loans_payable": "liability",
        "loans_receivable": "asset",
        "investment": "asset", "equity": "equity",
        "income": "income", "indirect_expense": "expense",
        "tax_expense": "expense", "fixed_asset": "asset",
        "current_asset": "asset", "current_liability": "liability",
        "statutory_liability": "liability", "inventory": "asset",
        "direct_expense": "expense", "suspense": "asset",
    }
    for name, group, _ in QUANTTA_FIXTURES:
        cat, nature = classify(name, group_path=group)
        assert nature == NATURES_EXPECTED[cat], (
            f"{name!r} → ({cat}, {nature}); expected nature {NATURES_EXPECTED[cat]}"
        )
