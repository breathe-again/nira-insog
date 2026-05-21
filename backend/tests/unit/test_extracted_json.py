"""Unit tests for the LLM-extracted JSON parser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.parsers.extracted_json import (
    ExtractedJSONError,
    InvoiceDraft,
    ReceiptDraft,
    parse_extracted_json,
)


# ---------------------------------------------------------------------------
# Invoice payloads
# ---------------------------------------------------------------------------


def test_purchase_invoice_minimal():
    payload = {
        "document_type": "purchase_invoice",
        "invoice_number": "INV-2026-031",
        "vendor": {"name": "Acme Traders", "gstin": "29AABCA1234B1Z5"},
        "issue_date": "2026-04-12",
        "total": 118000.00,
    }
    draft = parse_extracted_json(payload)
    assert isinstance(draft, InvoiceDraft)
    assert draft.type == "purchase"
    assert draft.invoice_number == "INV-2026-031"
    assert draft.counterparty is not None
    assert draft.counterparty.name == "Acme Traders"
    assert draft.counterparty.gstin == "29AABCA1234B1Z5"
    assert draft.issue_date == date(2026, 4, 12)
    assert draft.total == Decimal("118000.00")
    # Defaults
    assert draft.subtotal == Decimal("0")
    assert draft.tax == Decimal("0")
    assert draft.currency == "INR"


def test_sales_invoice_from_client_only_payload():
    """If only `client` is set (no explicit document_type), infer sales."""
    payload = {
        "invoice_number": "INV/2026/100",
        "client": {"name": "Globex Ltd"},
        "issue_date": "2026-04-05",
        "total": "86000",
    }
    draft = parse_extracted_json(payload)
    assert isinstance(draft, InvoiceDraft)
    assert draft.type == "sales"
    assert draft.counterparty.name == "Globex Ltd"


def test_invoice_with_line_items_and_indian_date():
    payload = {
        "document_type": "purchase_invoice",
        "invoice_number": "B-100",
        "vendor": "Solo Vendor",  # string vendor, no dict
        "issue_date": "12-04-2026",
        "subtotal": 1000,
        "tax": "180",
        "total": "1180",
        "line_items": [
            {"description": "Item A", "qty": 1, "unit_price": 1000, "amount": 1000}
        ],
    }
    draft = parse_extracted_json(payload)
    assert draft.counterparty.name == "Solo Vendor"
    assert draft.issue_date == date(2026, 4, 12)
    assert draft.subtotal == Decimal("1000")
    assert draft.tax == Decimal("180")
    assert draft.total == Decimal("1180")
    assert draft.line_items and len(draft.line_items) == 1


def test_invoice_missing_invoice_number_raises():
    with pytest.raises(ExtractedJSONError):
        parse_extracted_json(
            {
                "document_type": "purchase_invoice",
                "issue_date": "2026-04-12",
                "total": 100,
            }
        )


def test_invoice_missing_total_raises():
    with pytest.raises(ExtractedJSONError):
        parse_extracted_json(
            {
                "document_type": "purchase_invoice",
                "invoice_number": "X",
                "issue_date": "2026-04-12",
            }
        )


def test_invoice_bad_date_raises():
    with pytest.raises(ExtractedJSONError):
        parse_extracted_json(
            {
                "document_type": "purchase_invoice",
                "invoice_number": "X",
                "issue_date": "not-a-date",
                "total": 100,
            }
        )


# ---------------------------------------------------------------------------
# Receipt payloads
# ---------------------------------------------------------------------------


def test_receipt_full_payload():
    payload = {
        "document_type": "receipt",
        "vendor": {"name": "Cafe Coffee Day"},
        "date": "2026-05-18",
        "amount": 565.20,
        "tax": 51.40,
        "category": "meals",
        "payment_mode": "card",
        "notes": "Client meeting",
    }
    draft = parse_extracted_json(payload)
    assert isinstance(draft, ReceiptDraft)
    assert draft.counterparty.name == "Cafe Coffee Day"
    assert draft.date == date(2026, 5, 18)
    assert draft.amount == Decimal("565.20")
    assert draft.tax == Decimal("51.40")
    assert draft.category == "meals"
    assert draft.payment_mode == "card"
    assert draft.notes == "Client meeting"


def test_receipt_unknown_payment_mode_falls_back():
    draft = parse_extracted_json(
        {
            "document_type": "receipt",
            "date": "2026-05-18",
            "amount": 100,
            "payment_mode": "btc",  # not in our whitelist
        }
    )
    assert isinstance(draft, ReceiptDraft)
    assert draft.payment_mode == "unknown"


def test_receipt_payload_with_total_field_works():
    """Some receipt payloads use 'total' instead of 'amount'."""
    draft = parse_extracted_json(
        {
            "document_type": "receipt",
            "date": "2026-05-18",
            "total": 250,
        }
    )
    assert isinstance(draft, ReceiptDraft)
    assert draft.amount == Decimal("250")


# ---------------------------------------------------------------------------
# Dispatch / fallbacks
# ---------------------------------------------------------------------------


def test_unclassifiable_payload_raises():
    with pytest.raises(ExtractedJSONError):
        parse_extracted_json({"foo": "bar"})


def test_falls_back_to_receipt_when_no_invoice_number():
    draft = parse_extracted_json({"date": "2026-05-01", "amount": 50})
    assert isinstance(draft, ReceiptDraft)


def test_falls_back_to_invoice_when_invoice_number_present():
    draft = parse_extracted_json(
        {"invoice_number": "X", "issue_date": "2026-05-01", "total": 100}
    )
    assert isinstance(draft, InvoiceDraft)


def test_non_dict_payload_raises():
    with pytest.raises(ExtractedJSONError):
        parse_extracted_json([1, 2, 3])  # type: ignore[arg-type]


def test_fallback_document_type_used_when_payload_does_not_classify():
    draft = parse_extracted_json(
        {"invoice_number": "X", "issue_date": "2026-05-01", "total": 100},
        fallback_document_type="sales_invoice",
    )
    # Explicit invoice_number → invoice; sales/purchase decided by content.
    assert isinstance(draft, InvoiceDraft)


def test_currency_normalization():
    draft = parse_extracted_json(
        {
            "document_type": "purchase_invoice",
            "invoice_number": "X",
            "issue_date": "2026-05-01",
            "total": 100,
            "currency": "usd",
        }
    )
    assert draft.currency == "USD"
