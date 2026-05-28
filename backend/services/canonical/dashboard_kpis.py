"""Canonical-first KPI reads with dual-read fallback.

During the Phase-2 cutover we run BOTH the new canonical ledger and the
old bank-transactions/invoices tables. Dashboard widgets call these
helpers, which try the canonical layer first and fall back to the old
bottom-up reads when the canonical ledger has no data for a given
category.

This makes the dashboard self-healing: as soon as a customer uploads
their Tally Trial Balance, KPIs auto-switch to ledger truth without any
schema migration on the read side.

The fallback strategy is intentionally per-KPI rather than global:
  - Cash position works great from canonical (TB has Cash + Bank).
  - Receivables works from canonical (Sundry Debtors aggregate).
  - Payables works from canonical (Sundry Creditors aggregate).
  - Daily cash-flow requires Day Book movement, not TB — those widgets
    keep reading bank_transactions for now.

All helpers strictly filter on `org_id` (multi-tenant safety) and
optionally on `entity_id` (multi-entity scoping).
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from common.models import Account, LedgerEntry

logger = logging.getLogger(__name__)


def has_canonical_data(
    db: Session,
    org_id: uuid.UUID,
    categories: Optional[list[str]] = None,
    entity_id: Optional[uuid.UUID] = None,
) -> bool:
    """Quick existence check — does this org have any canonical ledger
    entries (optionally restricted to a set of account categories)?

    Used by widget code to decide whether to read canonical or fall back.
    Cheap because the index on (org_id, entity_id, entry_date) covers it.
    """
    join = LedgerEntry.__table__.join(
        Account.__table__, LedgerEntry.account_id == Account.id
    )
    q = select(func.count()).select_from(join).where(LedgerEntry.org_id == org_id)
    if entity_id is not None:
        q = q.where(LedgerEntry.entity_id == entity_id)
    if categories:
        q = q.where(Account.category.in_(categories))
    return (db.execute(q).scalar() or 0) > 0


def get_cash_position(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    """Cash on hand + bank balances. Asset-natural (debit-side).

    Reads from canonical when available; falls back to bank_transactions
    running_balance MAX otherwise.
    """
    if has_canonical_data(db, org_id, categories=["cash", "bank"], entity_id=entity_id):
        return _category_balance_debit_minus_credit(
            db, org_id, ["cash", "bank"], entity_id=entity_id, as_of=as_of
        )
    return _legacy_cash_position(db, org_id, as_of=as_of)


def get_receivables(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    """Outstanding receivables. Asset-natural (debit-side)."""
    if has_canonical_data(db, org_id, categories=["receivables"], entity_id=entity_id):
        return _category_balance_debit_minus_credit(
            db, org_id, ["receivables"], entity_id=entity_id, as_of=as_of
        )
    return _legacy_receivables(db, org_id, as_of=as_of)


def get_payables(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    """Outstanding payables. Liability-natural — we flip sign for display."""
    if has_canonical_data(db, org_id, categories=["payables"], entity_id=entity_id):
        # Liability: credit-natural, so flip to get a positive display number
        v = _category_balance_debit_minus_credit(
            db, org_id, ["payables"], entity_id=entity_id, as_of=as_of
        )
        return -v
    return _legacy_payables(db, org_id, as_of=as_of)


def get_loans_payable(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    """Total loans owed (secured + unsecured)."""
    if has_canonical_data(db, org_id, categories=["loans_payable"], entity_id=entity_id):
        v = _category_balance_debit_minus_credit(
            db, org_id, ["loans_payable"], entity_id=entity_id, as_of=as_of
        )
        return -v
    return Decimal("0")


def get_investments(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    """Investments held (MF + SGB + equity + bonds + warrants)."""
    if has_canonical_data(db, org_id, categories=["investment"], entity_id=entity_id):
        return _category_balance_debit_minus_credit(
            db, org_id, ["investment"], entity_id=entity_id, as_of=as_of
        )
    return Decimal("0")


def get_fixed_assets(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    if has_canonical_data(db, org_id, categories=["fixed_asset"], entity_id=entity_id):
        return _category_balance_debit_minus_credit(
            db, org_id, ["fixed_asset"], entity_id=entity_id, as_of=as_of
        )
    return Decimal("0")


def get_revenue_total(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> Decimal:
    """Total revenue / income in a period. Credit-natural — flipped for
    display.
    """
    if has_canonical_data(db, org_id, categories=["income"], entity_id=entity_id):
        v = _category_balance_debit_minus_credit(
            db, org_id, ["income"], entity_id=entity_id,
            as_of=period_end, period_start=period_start,
        )
        return -v
    return Decimal("0")


def get_expense_total(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> Decimal:
    """Total expenses in a period (direct + indirect + tax)."""
    cats = ["direct_expense", "indirect_expense", "tax_expense"]
    if has_canonical_data(db, org_id, categories=cats, entity_id=entity_id):
        return _category_balance_debit_minus_credit(
            db, org_id, cats, entity_id=entity_id,
            as_of=period_end, period_start=period_start,
        )
    return Decimal("0")


def get_data_freshness(
    db: Session,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
) -> dict:
    """Diagnostic: tells the UI which source backed the most recent
    canonical write. Used by the dashboard's "Tally synced 4 hours ago"
    label.
    """
    from common.models import SourceSystem

    q = (
        select(
            SourceSystem.system_type,
            SourceSystem.display_name,
            SourceSystem.last_sync_at,
            SourceSystem.last_sync_status,
        )
        .where(SourceSystem.org_id == org_id, SourceSystem.is_enabled.is_(True))
        .order_by(SourceSystem.last_sync_at.desc().nulls_last())
    )
    if entity_id is not None:
        q = q.where(SourceSystem.entity_id == entity_id)

    rows = list(db.execute(q.limit(8)))
    canonical_rows = db.execute(
        select(func.count()).select_from(LedgerEntry).where(LedgerEntry.org_id == org_id)
    ).scalar() or 0
    return {
        "canonical_entries": canonical_rows,
        "sources": [
            {
                "system_type": r.system_type,
                "display_name": r.display_name,
                "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                "last_sync_status": r.last_sync_status,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _category_balance_debit_minus_credit(
    db: Session,
    org_id: uuid.UUID,
    categories: list[str],
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
    period_start: Optional[date] = None,
) -> Decimal:
    """Sum (debit_inr - credit_inr) across accounts in given categories,
    plus their opening_balance_inr."""
    join = LedgerEntry.__table__.join(
        Account.__table__, LedgerEntry.account_id == Account.id
    )
    conds = [LedgerEntry.org_id == org_id, Account.category.in_(categories)]
    if entity_id is not None:
        conds.append(LedgerEntry.entity_id == entity_id)
    if as_of is not None:
        conds.append(LedgerEntry.entry_date <= as_of)
    if period_start is not None:
        conds.append(LedgerEntry.entry_date >= period_start)

    movement = db.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.debit_inr), 0)
            - func.coalesce(func.sum(LedgerEntry.credit_inr), 0)
        )
        .select_from(join)
        .where(and_(*conds))
    ).scalar() or Decimal("0")

    # Add opening balances of accounts in those categories
    opening_q = select(func.coalesce(func.sum(Account.opening_balance_inr), 0)).where(
        Account.org_id == org_id, Account.category.in_(categories)
    )
    if entity_id is not None:
        opening_q = opening_q.where(Account.entity_id == entity_id)
    opening = db.execute(opening_q).scalar() or Decimal("0")
    return Decimal(movement) + Decimal(opening)


def _legacy_cash_position(
    db: Session, org_id: uuid.UUID, as_of: Optional[date] = None
) -> Decimal:
    """Old bottom-up cash position read — kept for orgs that haven't
    uploaded a Trial Balance yet.
    """
    from common.models import BankTransaction

    q = (
        select(BankTransaction.running_balance)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.running_balance.isnot(None),
        )
        .order_by(BankTransaction.txn_date.desc(), BankTransaction.created_at.desc())
        .limit(1)
    )
    if as_of is not None:
        q = q.where(BankTransaction.txn_date <= as_of)
    row = db.execute(q).first()
    if row:
        return Decimal(row[0] or 0)

    # Net credits - debits as a last resort
    credits_q = select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
        BankTransaction.org_id == org_id,
        BankTransaction.direction == "credit",
    )
    debits_q = select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
        BankTransaction.org_id == org_id,
        BankTransaction.direction == "debit",
    )
    if as_of is not None:
        credits_q = credits_q.where(BankTransaction.txn_date <= as_of)
        debits_q = debits_q.where(BankTransaction.txn_date <= as_of)
    return Decimal(db.scalar(credits_q) or 0) - Decimal(db.scalar(debits_q) or 0)


def _legacy_receivables(
    db: Session, org_id: uuid.UUID, as_of: Optional[date] = None
) -> Decimal:
    from common.models import Invoice

    q = select(func.coalesce(func.sum(Invoice.total), 0)).where(
        Invoice.org_id == org_id,
        Invoice.type == "sales",
        Invoice.status != "paid",
    )
    if as_of is not None:
        q = q.where(Invoice.issue_date <= as_of)
    return Decimal(db.scalar(q) or 0)


def _legacy_payables(
    db: Session, org_id: uuid.UUID, as_of: Optional[date] = None
) -> Decimal:
    from common.models import Invoice

    q = select(func.coalesce(func.sum(Invoice.total), 0)).where(
        Invoice.org_id == org_id,
        Invoice.type == "purchase",
        Invoice.status != "paid",
    )
    if as_of is not None:
        q = q.where(Invoice.issue_date <= as_of)
    return Decimal(db.scalar(q) or 0)
