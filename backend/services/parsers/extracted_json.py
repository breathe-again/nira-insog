"""Parser for LLM-extracted JSON payloads → Invoice / Receipt drafts.

The real LLM extractor (Week 5–6) will write JSON conforming to the schemas
in `data/schemas/`. Until then, this parser also tolerates the slightly
ad-hoc shapes the v0 stub produces, so the pipeline can be exercised end-to-end
without the LLM in the loop.

Expected shapes (all fields optional unless noted):

    # Purchase / sales invoice
    {
      "document_type": "purchase_invoice" | "sales_invoice",
      "invoice_number": "INV-2026-031",     # required
      "vendor": {"name": "Acme Traders", "gstin": "..."},   # for purchase
      "client": {"name": "Globex Ltd", "gstin": "..."},     # for sales
      "issue_date": "2026-04-12",            # required
      "due_date":   "2026-05-12",
      "currency": "INR",
      "subtotal": 100000.00,
      "tax":      18000.00,
      "total":    118000.00,                 # required
      "line_items": [{"description": "...", "qty": 1, "unit_price": 100, "amount": 100}, ...]
    }

    # Standalone receipt
    {
      "document_type": "receipt",
      "vendor": {"name": "Cafe Coffee Day"},
      "date":   "2026-05-18",                # required
      "amount": 565.20,                       # required
      "tax":    51.40,
      "category": "meals",
      "payment_mode": "card",
      "notes": "Client meeting"
    }

The parser DOES NOT touch the database. It returns drafts; the caller resolves
the vendor/client and persists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CounterpartyHint:
    """Parsed reference to a vendor or client — name + optional GSTIN."""

    name: str
    gstin: Optional[str] = None


@dataclass(slots=True)
class InvoiceDraft:
    type: str  # "sales" | "purchase"
    invoice_number: str
    issue_date: date
    total: Decimal
    counterparty: Optional[CounterpartyHint] = None
    due_date: Optional[date] = None
    subtotal: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    currency: str = "INR"
    line_items: Optional[list[dict]] = None

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "invoice_number": self.invoice_number,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "subtotal": str(self.subtotal),
            "tax": str(self.tax),
            "total": str(self.total),
            "currency": self.currency,
            "counterparty": (
                {"name": self.counterparty.name, "gstin": self.counterparty.gstin}
                if self.counterparty
                else None
            ),
            "line_items": self.line_items,
        }


@dataclass(slots=True)
class BankTxnFromLLM:
    """One transaction as the LLM returned it inside a bank_statement payload."""

    date: date
    description: str
    amount: Decimal
    direction: str  # "credit" | "debit"
    balance: Optional[Decimal] = None


@dataclass(slots=True)
class BankStatementDraft:
    """A whole bank statement extracted by the LLM."""

    account_holder: Optional[str]
    account_number_last4: Optional[str]
    currency: str
    period_start: Optional[date]
    period_end: Optional[date]
    transactions: list[BankTxnFromLLM] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "account_holder": self.account_holder,
            "account_number_last4": self.account_number_last4,
            "currency": self.currency,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "transactions": [
                {
                    "date": t.date.isoformat(),
                    "description": t.description,
                    "amount": str(t.amount),
                    "direction": t.direction,
                    "balance": str(t.balance) if t.balance is not None else None,
                }
                for t in self.transactions
            ],
        }


@dataclass(slots=True)
class ReceiptDraft:
    date: date
    amount: Decimal
    counterparty: Optional[CounterpartyHint] = None
    tax: Optional[Decimal] = None
    category: Optional[str] = None
    payment_mode: str = "unknown"
    notes: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "amount": str(self.amount),
            "tax": str(self.tax) if self.tax is not None else None,
            "category": self.category,
            "payment_mode": self.payment_mode,
            "notes": self.notes,
            "counterparty": (
                {"name": self.counterparty.name, "gstin": self.counterparty.gstin}
                if self.counterparty
                else None
            ),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ExtractedJSONError(ValueError):
    """Raised when a required field is missing or malformed."""


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y")


def _coerce_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise ExtractedJSONError(f"{field_name}: expected date string, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise ExtractedJSONError(f"{field_name}: empty date")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ExtractedJSONError(f"{field_name}: unrecognized date format '{value}'")


def _coerce_optional_date(value: Any, *, field_name: str) -> Optional[date]:
    if value is None or value == "":
        return None
    return _coerce_date(value, field_name=field_name)


def _coerce_decimal(value: Any, *, field_name: str, default: Optional[Decimal] = None) -> Decimal:
    if value is None or value == "":
        if default is not None:
            return default
        raise ExtractedJSONError(f"{field_name}: missing required amount")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        try:
            return Decimal(cleaned)
        except InvalidOperation as e:
            raise ExtractedJSONError(f"{field_name}: invalid amount '{value}'") from e
    raise ExtractedJSONError(
        f"{field_name}: expected number, got {type(value).__name__}"
    )


def _coerce_optional_decimal(value: Any, *, field_name: str) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return _coerce_decimal(value, field_name=field_name)


def _extract_counterparty(
    payload: dict,
    *,
    keys: tuple[str, ...] = ("vendor", "client", "counterparty", "merchant", "payee", "payer"),
) -> Optional[CounterpartyHint]:
    for key in keys:
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            name = raw.strip()
            if name:
                return CounterpartyHint(name=name)
        elif isinstance(raw, dict):
            name = str(raw.get("name", "")).strip()
            if name:
                return CounterpartyHint(name=name, gstin=raw.get("gstin"))
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def parse_extracted_json(
    payload: dict,
    *,
    fallback_document_type: Optional[str] = None,
) -> InvoiceDraft | ReceiptDraft | BankStatementDraft:
    """Dispatch on document_type. Raises ExtractedJSONError if it can't be classified."""
    if not isinstance(payload, dict):
        raise ExtractedJSONError(
            f"expected JSON object at top level, got {type(payload).__name__}"
        )

    doc_type = (
        str(payload.get("document_type") or fallback_document_type or "")
        .strip()
        .lower()
    )

    if doc_type in ("sales_invoice", "purchase_invoice", "invoice"):
        return _parse_invoice(payload, declared_type=doc_type)
    if doc_type == "receipt":
        return _parse_receipt(payload)
    if doc_type == "bank_statement":
        return _parse_bank_statement(payload)

    # Heuristic fallback: presence of transactions[] ⇒ bank statement; etc.
    if isinstance(payload.get("transactions"), list) and payload["transactions"]:
        return _parse_bank_statement(payload)
    if payload.get("invoice_number"):
        return _parse_invoice(payload, declared_type="invoice")
    if payload.get("amount") or payload.get("total"):
        return _parse_receipt(payload)

    raise ExtractedJSONError(
        f"could not classify extracted payload (document_type='{doc_type}', "
        f"keys={list(payload.keys())[:8]})"
    )


def _parse_invoice(payload: dict, *, declared_type: str) -> InvoiceDraft:
    invoice_number = str(payload.get("invoice_number") or "").strip()
    if not invoice_number:
        raise ExtractedJSONError("invoice_number is required")

    # Sales vs purchase: explicit > presence of client (sales) vs vendor (purchase).
    if declared_type == "sales_invoice":
        kind = "sales"
    elif declared_type == "purchase_invoice":
        kind = "purchase"
    elif payload.get("client") is not None and payload.get("vendor") is None:
        kind = "sales"
    elif payload.get("vendor") is not None and payload.get("client") is None:
        kind = "purchase"
    else:
        # Default to purchase — most uploaded invoices are bills FROM vendors.
        kind = "purchase"

    cp_keys = ("vendor",) if kind == "purchase" else ("client",)
    counterparty = _extract_counterparty(payload, keys=cp_keys)
    # Fallback to the generic field if the kind-specific one isn't there.
    if counterparty is None:
        counterparty = _extract_counterparty(payload)

    issue_date = _coerce_date(payload.get("issue_date") or payload.get("date"), field_name="issue_date")
    due_date = _coerce_optional_date(payload.get("due_date"), field_name="due_date")

    subtotal = _coerce_decimal(payload.get("subtotal"), field_name="subtotal", default=Decimal("0"))
    tax = _coerce_decimal(payload.get("tax"), field_name="tax", default=Decimal("0"))
    total = _coerce_decimal(
        payload.get("total") or payload.get("amount"),
        field_name="total",
    )

    line_items = payload.get("line_items")
    if line_items is not None and not isinstance(line_items, list):
        raise ExtractedJSONError("line_items must be a list when present")

    return InvoiceDraft(
        type=kind,
        invoice_number=invoice_number,
        issue_date=issue_date,
        due_date=due_date,
        subtotal=subtotal,
        tax=tax,
        total=total,
        currency=str(payload.get("currency") or "INR")[:3].upper(),
        counterparty=counterparty,
        line_items=line_items,
    )


def _parse_bank_statement(payload: dict) -> BankStatementDraft:
    """Parse Claude's bank_statement payload into a BankStatementDraft."""
    raw_txns = payload.get("transactions") or []
    if not isinstance(raw_txns, list):
        raise ExtractedJSONError("bank_statement.transactions must be a list")

    txns: list[BankTxnFromLLM] = []
    for i, raw in enumerate(raw_txns):
        if not isinstance(raw, dict):
            continue
        try:
            txn_date = _coerce_date(raw.get("date"), field_name=f"transactions[{i}].date")
            amount = _coerce_decimal(
                raw.get("amount"), field_name=f"transactions[{i}].amount"
            )
        except ExtractedJSONError:
            # Skip malformed rows but keep going — LLM extracts can have stragglers.
            logger.warning("skipped malformed transaction at index %d", i)
            continue

        direction = str(raw.get("direction", "")).strip().lower()
        if direction not in ("credit", "debit"):
            # Try to infer from amount sign.
            direction = "debit" if amount < 0 else "credit"
            amount = abs(amount)

        balance = _coerce_optional_decimal(
            raw.get("balance"), field_name=f"transactions[{i}].balance"
        )

        description = str(raw.get("description") or raw.get("narration") or "").strip()

        txns.append(
            BankTxnFromLLM(
                date=txn_date,
                description=description,
                amount=amount,
                direction=direction,
                balance=balance,
            )
        )

    period_start = _coerce_optional_date(
        payload.get("period_start"), field_name="period_start"
    )
    period_end = _coerce_optional_date(payload.get("period_end"), field_name="period_end")

    last4 = payload.get("account_number_last4")
    if last4 is not None:
        last4 = str(last4).strip()[-4:]

    return BankStatementDraft(
        account_holder=(payload.get("account_holder") or "").strip() or None,
        account_number_last4=last4,
        currency=str(payload.get("currency") or "INR")[:3].upper(),
        period_start=period_start,
        period_end=period_end,
        transactions=txns,
    )


def _parse_receipt(payload: dict) -> ReceiptDraft:
    counterparty = _extract_counterparty(payload)
    receipt_date = _coerce_date(
        payload.get("date") or payload.get("issue_date"), field_name="date"
    )
    amount = _coerce_decimal(
        payload.get("amount") or payload.get("total"), field_name="amount"
    )
    tax = _coerce_optional_decimal(payload.get("tax"), field_name="tax")

    payment_mode = str(payload.get("payment_mode") or "unknown").strip().lower()
    if payment_mode not in {"cash", "card", "upi", "bank_transfer", "unknown"}:
        payment_mode = "unknown"

    category = payload.get("category")
    if category is not None:
        category = str(category).strip() or None

    notes = payload.get("notes")
    if notes is not None:
        notes = str(notes).strip() or None

    return ReceiptDraft(
        date=receipt_date,
        amount=amount,
        counterparty=counterparty,
        tax=tax,
        category=category,
        payment_mode=payment_mode,
        notes=notes,
    )
