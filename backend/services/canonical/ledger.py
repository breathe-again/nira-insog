"""Canonical ledger — double-entry posting + balance queries.

The double-entry rule. Every Transaction we write must have:
  sum(debit_inr across legs) == sum(credit_inr across legs)
Posting helpers enforce this at the application level. The DB does NOT
constrain it because a few legitimate cases need provisional unbalanced
writes:
  - single-leg bank-CSV ingestion (the "other side" is a suspense leg
    until a classifier identifies the counterparty)
  - opening balances (only one side known at first import)
For those cases we write the missing leg to the org's Suspense account
so the books remain balanced from the outside.

INR is the aggregation currency. Native-currency columns let us
round-trip back to the source for non-INR books. `fx_rate_to_inr` of
1.0 is fine for INR-only tenants (which is everyone today).

Provenance. Every leg carries `source_system_id`, `source_native_id`,
`source_document_id`, and a `confidence` score:
  - 1.000 for direct Tally / Zoho imports (the source already balances)
  - 0.800 for inferred classifications (bank CSV → 'Indirect expense')
  - 0.500 for ambiguous fallbacks (we wrote to suspense)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from common.models import Account, Entity, LedgerEntry, Transaction
from services.canonical import accounts as accounts_svc

logger = logging.getLogger(__name__)

# Hairline tolerance for double-entry balancing. Rounding from native to
# INR can leave a few paise of imbalance — anything within 1 paisa is OK.
_BALANCE_EPS = Decimal("0.01")


@dataclass
class Leg:
    """One side of a double-entry transaction.

    Exactly one of `debit`/`credit` should be > 0 in a typical posting.
    Both being zero is allowed (used for memo-only entries with no money
    movement, e.g. closing inventory adjustments). Both being positive is
    NOT allowed — that's a sign of a posting bug.
    """

    account_id: uuid.UUID
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    narration: Optional[str] = None
    cost_centre: Optional[str] = None
    # Native-currency values default to INR equivalents
    debit_native: Optional[Decimal] = None
    credit_native: Optional[Decimal] = None
    currency_code: str = "INR"
    fx_rate_to_inr: Decimal = Decimal("1")
    source_native_id: Optional[str] = None


@dataclass
class PostResult:
    transaction_id: uuid.UUID
    entry_ids: list[uuid.UUID] = field(default_factory=list)


def _fy_for(d: date, fy_start_month: int = 4) -> int:
    """Indian financial year naming: FY 2025-26 starts 1-Apr-2025, ends
    31-Mar-2026. Return the starting year (2025 for FY25-26).
    """
    if d.month >= fy_start_month:
        return d.year
    return d.year - 1


def post_journal(
    db: Session,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    txn_date: date,
    txn_type: str,
    legs: list[Leg],
    voucher_number: Optional[str] = None,
    narration: Optional[str] = None,
    party_name: Optional[str] = None,
    party_account_id: Optional[uuid.UUID] = None,
    source_system_id: Optional[uuid.UUID] = None,
    source_native_id: Optional[str] = None,
    source_document_id: Optional[uuid.UUID] = None,
    confidence: Decimal = Decimal("1.000"),
    currency_code: str = "INR",
    fx_rate_to_inr: Decimal = Decimal("1"),
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    entry_kind: str = "movement",
    suspense_on_imbalance: bool = False,
    commit: bool = True,
) -> PostResult:
    """Post a balanced multi-leg transaction.

    Raises ValueError if the legs don't balance and suspense_on_imbalance
    is False. If True, the imbalance is posted to the org's Suspense
    account so partial ingestions still write something coherent.
    """
    if not legs:
        raise ValueError("post_journal requires at least one leg")

    # Idempotency on source_native_id: if we've already written this exact
    # voucher from this exact source, return the existing transaction.
    if source_system_id is not None and source_native_id:
        existing = db.execute(
            select(Transaction).where(
                Transaction.org_id == org_id,
                Transaction.source_system_id == source_system_id,
                Transaction.source_native_id == source_native_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            entries = list(
                db.execute(
                    select(LedgerEntry.id).where(
                        LedgerEntry.transaction_id == existing.id
                    )
                ).scalars()
            )
            return PostResult(transaction_id=existing.id, entry_ids=entries)

    # Validate balance
    total_dr = sum((leg.debit for leg in legs), Decimal("0"))
    total_cr = sum((leg.credit for leg in legs), Decimal("0"))
    imbalance = total_dr - total_cr

    if abs(imbalance) > _BALANCE_EPS:
        if not suspense_on_imbalance:
            raise ValueError(
                f"Unbalanced legs: debit={total_dr}, credit={total_cr}, "
                f"delta={imbalance}. Pass suspense_on_imbalance=True to "
                f"force-post the delta to the Suspense account."
            )
        # Append a suspense leg that flips the sign
        suspense = _get_or_create_suspense(db, org_id, entity_id, commit=False)
        if imbalance > 0:
            # debits exceed credits → credit Suspense for the delta
            legs = list(legs) + [Leg(account_id=suspense.id, credit=imbalance)]
        else:
            legs = list(legs) + [Leg(account_id=suspense.id, debit=-imbalance)]
        total_dr = sum((leg.debit for leg in legs), Decimal("0"))
        total_cr = sum((leg.credit for leg in legs), Decimal("0"))
        logger.info(
            "post_journal: balanced via suspense (org=%s entity=%s delta=%s)",
            org_id, entity_id, imbalance,
        )

    # Validate every leg's account belongs to this entity
    leg_account_ids = list({leg.account_id for leg in legs})
    accounts_rows = list(
        db.execute(
            select(Account).where(
                Account.org_id == org_id,
                Account.entity_id == entity_id,
                Account.id.in_(leg_account_ids),
            )
        ).scalars()
    )
    found_ids = {a.id for a in accounts_rows}
    missing = [aid for aid in leg_account_ids if aid not in found_ids]
    if missing:
        raise ValueError(
            f"Accounts not in org={org_id} entity={entity_id}: {missing}"
        )

    fy = _fy_for(txn_date)
    txn = Transaction(
        org_id=org_id,
        entity_id=entity_id,
        txn_date=txn_date,
        txn_type=txn_type,
        voucher_number=voucher_number,
        narration=narration,
        party_name=party_name,
        party_account_id=party_account_id,
        currency_code=currency_code,
        fx_rate_to_inr=fx_rate_to_inr,
        amount_inr=max(total_dr, total_cr),
        source_system_id=source_system_id,
        source_native_id=source_native_id,
        source_document_id=source_document_id,
        confidence=confidence,
        financial_year=fy,
    )
    db.add(txn)
    db.flush()  # we need txn.id for the legs

    entry_ids: list[uuid.UUID] = []
    for leg in legs:
        # Default native to INR equivalents when caller didn't set them
        dr_native = leg.debit_native if leg.debit_native is not None else leg.debit
        cr_native = leg.credit_native if leg.credit_native is not None else leg.credit
        entry = LedgerEntry(
            org_id=org_id,
            entity_id=entity_id,
            account_id=leg.account_id,
            transaction_id=txn.id,
            entry_date=txn_date,
            period_start=period_start,
            period_end=period_end,
            currency_code=leg.currency_code or currency_code,
            debit_native=dr_native,
            credit_native=cr_native,
            debit_inr=leg.debit,
            credit_inr=leg.credit,
            fx_rate_to_inr=leg.fx_rate_to_inr,
            narration=leg.narration or narration,
            cost_centre=leg.cost_centre,
            source_system_id=source_system_id,
            source_native_id=leg.source_native_id or source_native_id,
            source_document_id=source_document_id,
            confidence=confidence,
            entry_kind=entry_kind,
            financial_year=fy,
        )
        db.add(entry)
        db.flush()
        entry_ids.append(entry.id)

    if commit:
        db.commit()

    return PostResult(transaction_id=txn.id, entry_ids=entry_ids)


def post_opening_balance(
    db: Session,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    account_id: uuid.UUID,
    as_of: date,
    debit_inr: Decimal = Decimal("0"),
    credit_inr: Decimal = Decimal("0"),
    source_system_id: Optional[uuid.UUID] = None,
    source_document_id: Optional[uuid.UUID] = None,
    commit: bool = True,
) -> uuid.UUID:
    """Write a single opening-balance leg. The matching opposite-side leg
    is posted to the entity's Suspense account so the books balance.

    Used by Trial Balance imports — they give us only one side per ledger
    (the closing balance after a period of unknown internal movements).
    Once all TB rows are posted, Suspense should net to zero across all
    rows if the source TB itself was balanced.
    """
    suspense = _get_or_create_suspense(db, org_id, entity_id, commit=False)
    legs = [
        Leg(account_id=account_id, debit=debit_inr, credit=credit_inr),
        # Mirror leg on suspense
        Leg(account_id=suspense.id, debit=credit_inr, credit=debit_inr),
    ]
    result = post_journal(
        db,
        org_id=org_id,
        entity_id=entity_id,
        txn_date=as_of,
        txn_type="opening_balance",
        legs=legs,
        voucher_number=f"OPEN-{as_of.isoformat()}",
        narration="Opening balance",
        source_system_id=source_system_id,
        source_document_id=source_document_id,
        entry_kind="opening",
        period_start=as_of,
        period_end=as_of,
        commit=commit,
    )
    return result.transaction_id


def get_balance(
    db: Session,
    *,
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    as_of: Optional[date] = None,
) -> Decimal:
    """Return the balance for one account as of `as_of` (or all-time).

    Sign convention: positive = debit-side balance for asset/expense
    accounts and credit-side balance for liability/income/equity accounts.
    Callers can use account.nature to know which is "natural".

    Implementation: sum(debit_inr) - sum(credit_inr) + opening_balance_inr.
    """
    account = accounts_svc.get_account(db, org_id, account_id)
    if account is None:
        raise ValueError(f"Account {account_id} not in org {org_id}")

    q = select(
        func.coalesce(func.sum(LedgerEntry.debit_inr), 0) -
        func.coalesce(func.sum(LedgerEntry.credit_inr), 0),
    ).where(
        LedgerEntry.org_id == org_id,
        LedgerEntry.account_id == account_id,
    )
    if as_of is not None:
        q = q.where(LedgerEntry.entry_date <= as_of)

    delta = db.execute(q).scalar() or Decimal("0")
    return Decimal(delta) + (account.opening_balance_inr or Decimal("0"))


def get_category_total(
    db: Session,
    *,
    org_id: uuid.UUID,
    category: str,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
    period_start: Optional[date] = None,
) -> Decimal:
    """Total debit-side balance across all accounts in a canonical
    category. The natural reading depends on the category — callers
    typically just want a positive number to show in the UI:

      cash / bank / receivables / investments / fixed_asset
        → use as-is (debit-natural)
      payables / loans_payable / current_liability / equity / income
        → flip sign for display (credit-natural)
    """
    join = LedgerEntry.__table__.join(
        Account.__table__,
        LedgerEntry.account_id == Account.id,
    )
    conditions = [
        LedgerEntry.org_id == org_id,
        Account.category == category,
    ]
    if entity_id is not None:
        conditions.append(LedgerEntry.entity_id == entity_id)
    if as_of is not None:
        conditions.append(LedgerEntry.entry_date <= as_of)
    if period_start is not None:
        conditions.append(LedgerEntry.entry_date >= period_start)

    q = (
        select(
            func.coalesce(func.sum(LedgerEntry.debit_inr), 0)
            - func.coalesce(func.sum(LedgerEntry.credit_inr), 0)
        )
        .select_from(join)
        .where(and_(*conditions))
    )

    movement = db.execute(q).scalar() or Decimal("0")

    # Plus opening balances of accounts in this category
    opening_q = select(func.coalesce(func.sum(Account.opening_balance_inr), 0)).where(
        Account.org_id == org_id,
        Account.category == category,
    )
    if entity_id is not None:
        opening_q = opening_q.where(Account.entity_id == entity_id)
    opening = db.execute(opening_q).scalar() or Decimal("0")

    return Decimal(movement) + Decimal(opening)


def get_trial_balance(
    db: Session,
    *,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    as_of: Optional[date] = None,
) -> list[dict]:
    """Return a trial-balance dump: one row per account with computed
    debit/credit totals + closing balance.

    Each row::
        {
          "account_id": ...,
          "account_name": "ICICI Bank — Curr A/c 1234",
          "category": "bank",
          "nature": "asset",
          "debit_total_inr": Decimal,
          "credit_total_inr": Decimal,
          "closing_inr": Decimal,            # debit-credit + opening
          "currency_code": "INR",
        }

    Rows are returned even when both debit and credit totals are zero,
    so callers can see the entire chart of accounts. Filter out zero rows
    at the caller if you don't want them.
    """
    join = LedgerEntry.__table__.join(
        Account.__table__,
        LedgerEntry.account_id == Account.id,
        isouter=False,
    )
    # Sum legs grouped by account
    sum_q = (
        select(
            Account.id.label("account_id"),
            func.coalesce(func.sum(LedgerEntry.debit_inr), 0).label("dr"),
            func.coalesce(func.sum(LedgerEntry.credit_inr), 0).label("cr"),
        )
        .select_from(join)
        .where(LedgerEntry.org_id == org_id)
        .group_by(Account.id)
    )
    if entity_id is not None:
        sum_q = sum_q.where(LedgerEntry.entity_id == entity_id)
    if as_of is not None:
        sum_q = sum_q.where(LedgerEntry.entry_date <= as_of)

    sums_by_account: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for row in db.execute(sum_q):
        sums_by_account[row.account_id] = (Decimal(row.dr), Decimal(row.cr))

    # Now fetch every account so zero-movement accounts also show up
    acc_q = select(Account).where(Account.org_id == org_id)
    if entity_id is not None:
        acc_q = acc_q.where(Account.entity_id == entity_id)
    acc_q = acc_q.order_by(Account.category, Account.name)

    output: list[dict] = []
    for acc in db.execute(acc_q).scalars():
        dr, cr = sums_by_account.get(acc.id, (Decimal("0"), Decimal("0")))
        opening = acc.opening_balance_inr or Decimal("0")
        output.append(
            {
                "account_id": str(acc.id),
                "entity_id": str(acc.entity_id),
                "account_name": acc.name,
                "category": acc.category,
                "nature": acc.nature,
                "currency_code": acc.currency_code,
                "debit_total_inr": dr,
                "credit_total_inr": cr,
                "opening_inr": opening,
                "closing_inr": opening + dr - cr,
            }
        )
    return output


def _get_or_create_suspense(
    db: Session,
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    commit: bool,
) -> Account:
    """Lazy-create the entity's Suspense account. Idempotent."""
    return accounts_svc.find_or_create(
        db,
        org_id=org_id,
        entity_id=entity_id,
        name="Suspense",
        hinted_category="suspense",
        commit=commit,
    )
