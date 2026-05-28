"""Chart of accounts — canonical accounts table.

The mapping problem: Tally has ~30 standard groups under 28 ledger
categories. Zoho has its own ~15-group taxonomy. QuickBooks has another.
Nira collapses all of them into ~18 canonical categories (see _CATEGORIES
below) so every dashboard widget can query by category and stay source-
agnostic.

`find_or_create()` is the main entry point — connectors call it for each
ledger they see during ingestion. The classifier looks at:
  1. The Tally/Zoho/QB group path (most reliable signal)
  2. The account name itself (keyword-based fallback)
  3. The source-stated nature/category if the connector knows it

Idempotent: calling find_or_create() twice with the same
(org_id, entity_id, source_system_id, source_native_id) returns the
existing row.
"""
from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import Account

logger = logging.getLogger(__name__)


# Canonical categories — every account collapses to exactly one of these.
# Order matters: classifiers use the first matching rule.
_CATEGORIES = {
    "cash",
    "bank",
    "receivables",
    "payables",
    "loans_payable",
    "loans_receivable",
    "inventory",
    "fixed_asset",
    "investment",
    "current_asset",
    "current_liability",
    "statutory_liability",
    "equity",
    "income",
    "direct_expense",
    "indirect_expense",
    "tax_expense",
    "suspense",
}

# Per-category nature (asset / liability / income / expense / equity)
_NATURE: dict[str, str] = {
    "cash": "asset",
    "bank": "asset",
    "receivables": "asset",
    "loans_receivable": "asset",
    "inventory": "asset",
    "fixed_asset": "asset",
    "investment": "asset",
    "current_asset": "asset",
    "payables": "liability",
    "loans_payable": "liability",
    "current_liability": "liability",
    "statutory_liability": "liability",
    "equity": "equity",
    "income": "income",
    "direct_expense": "expense",
    "indirect_expense": "expense",
    "tax_expense": "expense",
    "suspense": "asset",  # treated as suspense-asset by convention
}


# Tally's 28 standard groups → canonical category.
# Source: Tally Prime Group Master defaults.
_TALLY_GROUP_MAP: dict[str, str] = {
    # Assets
    "cash-in-hand": "cash",
    "bank accounts": "bank",
    "bank ocaccounts": "bank",
    "bank oc a/c": "bank",
    "bank od accounts": "bank",
    "bank od a/c": "bank",
    "sundry debtors": "receivables",
    "loans & advances (asset)": "loans_receivable",
    "loans and advances (asset)": "loans_receivable",
    "deposits (asset)": "current_asset",
    "stock-in-hand": "inventory",
    "fixed assets": "fixed_asset",
    "investments": "investment",
    "current assets": "current_asset",
    "misc. expenses (asset)": "current_asset",
    "miscellaneous expenses (asset)": "current_asset",
    # Liabilities
    "sundry creditors": "payables",
    "duties & taxes": "statutory_liability",
    "duties and taxes": "statutory_liability",
    "provisions": "statutory_liability",
    "loans (liability)": "loans_payable",
    "secured loans": "loans_payable",
    "unsecured loans": "loans_payable",
    "current liabilities": "current_liability",
    "suspense a/c": "suspense",
    "suspense account": "suspense",
    # Equity
    "capital account": "equity",
    "reserves & surplus": "equity",
    "reserves and surplus": "equity",
    "branch / divisions": "equity",
    # Income
    "sales accounts": "income",
    "direct incomes": "income",
    "indirect incomes": "income",
    # Expenses
    "purchase accounts": "direct_expense",
    "direct expenses": "direct_expense",
    "indirect expenses": "indirect_expense",
}


# Name-keyword rules used when no group path is available, or as a
# secondary signal. Order matters; first match wins.
_KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcash[- ]in[- ]hand\b|\bpetty cash\b", re.I), "cash"),
    (re.compile(r"\bbank\b", re.I), "bank"),
    (re.compile(r"\bsundry debtor|\baccounts? receivable|\btrade receivable[s]?\b|\bdebtors\b", re.I), "receivables"),
    (re.compile(r"\bsundry creditor|\baccounts? payable|\btrade payable[s]?\b|\bcreditors\b", re.I), "payables"),
    (re.compile(r"\bsecured loan|\bunsecured loan|\bterm loan|\bworking capital loan", re.I), "loans_payable"),
    (re.compile(r"\bloan to\b|\bloans? & advances?\s*\(asset|\badvance to ", re.I), "loans_receivable"),
    (re.compile(r"\binventory|\bstock[- ]in[- ]hand|\bclosing stock|\bopening stock", re.I), "inventory"),
    (re.compile(r"\bfixed asset|\bplant\b|\bmachinery\b|\bbuilding\b|\bfurniture\b|\bvehicle\b|\bcomputer\b.*\basset", re.I), "fixed_asset"),
    # Equity must come BEFORE investment so "Equity Share Capital" doesn't
    # land in investment via the "equity share" partial.
    (re.compile(r"\bshare capital|\breserves? & surplus|\bequity (share )?capital|\bpartner.* capital|\bproprietor.* capital", re.I), "equity"),
    # Investment patterns — note "equity share" is removed in favour of
    # explicit phrasing like "equity investment" / "equity shares in <X>"
    # so "Equity Share Capital" doesn't get misclassified.
    (re.compile(r"\binvestment|\bmutual fund|\bsgb\b|\bsovereign gold|\bequity\s+(investment|holding|shares in)|\bwarrant\b|\bbond\b", re.I), "investment"),
    (re.compile(r"\bgst (payable|input|output)|\btds payable|\bpf payable|\bgratuity\b|\besic\b|\bprofessional tax|\bduties? & taxes?\b", re.I), "statutory_liability"),
    (re.compile(r"\bsales\b|\brevenue\b|\bservice income|\bturnover\b|\binterest received|\bother income", re.I), "income"),
    (re.compile(r"\bpurchase\b|\bcost of (goods|sales)|\bdirect (expense|cost)|\bfreight\b.*inward|\bcarriage\b.*inward|\bcustoms duty\b|\bimport duty\b", re.I), "direct_expense"),
    (re.compile(r"\bsalary|\brent\b|\belectricity\b|\binternet\b|\bsoftware\b|\bsubscription|\boffice (expense|supplies)|\bbank charge|\btravel\b|\bmarketing\b|\badvertising\b|\bprofessional (fee|charge|service)|\bconsultancy|\bauditor|\baccounting (charge|fee)|\bdepreciation\b", re.I), "indirect_expense"),
    (re.compile(r"\bincome tax\b|\btax expense\b|\btax provision\b", re.I), "tax_expense"),
    (re.compile(r"\bsuspense\b", re.I), "suspense"),
    (re.compile(r"\bdeposit\b|\bsecurity deposit|\bprepaid\b|\badvance\b.*paid|\badvance to (vendor|supplier)", re.I), "current_asset"),
    (re.compile(r"\bcurrent liab|\baccrued expense|\boutstanding expense|\baudit fee payable|\bsalary payable", re.I), "current_liability"),
]


def _normalize_group_path(path: str) -> list[str]:
    """Split a Tally-style group path 'Primary>Assets>Current>Bank' into
    lowercased trimmed segments. Accepts >, /, or | as separators.
    """
    parts = re.split(r"[>/|]", path or "")
    return [p.strip().lower() for p in parts if p.strip()]


def _normalize_name(name: str) -> str:
    """Lowercased, whitespace-collapsed name for fuzzy matching."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def classify(
    name: str,
    group_path: Optional[str] = None,
    hinted_category: Optional[str] = None,
) -> tuple[str, str]:
    """Classify a chart-of-accounts entry. Returns (category, nature).

    Priority:
      1. Caller-provided hint (if it's a valid category)
      2. Tally group path match (any segment of the path)
      3. Keyword match on the account name
      4. Fallback to 'suspense'
    """
    if hinted_category and hinted_category in _CATEGORIES:
        return hinted_category, _NATURE[hinted_category]

    # Group-path match
    if group_path:
        segments = _normalize_group_path(group_path)
        # Walk from most specific (rightmost) to most general (leftmost)
        for segment in reversed(segments):
            if segment in _TALLY_GROUP_MAP:
                cat = _TALLY_GROUP_MAP[segment]
                return cat, _NATURE[cat]
            # Try keyword pass on the segment itself
            for pat, cat in _KEYWORD_RULES:
                if pat.search(segment):
                    return cat, _NATURE[cat]

    # Name keyword match
    norm = _normalize_name(name)
    for pat, cat in _KEYWORD_RULES:
        if pat.search(norm):
            return cat, _NATURE[cat]

    return "suspense", _NATURE["suspense"]


def find_or_create(
    db: Session,
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    name: str,
    source_group_path: Optional[str] = None,
    source_system_id: Optional[uuid.UUID] = None,
    source_native_id: Optional[str] = None,
    hinted_category: Optional[str] = None,
    currency_code: str = "INR",
    opening_balance_inr: Optional[Decimal] = None,
    commit: bool = True,
) -> Account:
    """Find or create a canonical account row.

    Lookup order:
      1. By (org_id, entity_id, source_system_id, source_native_id) — exact
         match on source-side IDs. Idempotent re-runs of the same connector
         find the same row.
      2. By (org_id, entity_id, normalized_name) — for sources that don't
         expose a stable native id (CSV uploads, manual journals).
    """
    normalized = _normalize_name(name)

    # Strategy 1 — match by source native id
    if source_system_id is not None and source_native_id:
        row = db.execute(
            select(Account).where(
                Account.org_id == org_id,
                Account.entity_id == entity_id,
                Account.source_system_id == source_system_id,
                Account.source_native_id == source_native_id,
            )
        ).scalar_one_or_none()
        if row is not None:
            # Refresh display name + group path in case source renamed it.
            row.name = name
            row.source_group_path = source_group_path or row.source_group_path
            if opening_balance_inr is not None:
                row.opening_balance_inr = opening_balance_inr
            if commit:
                db.commit()
                db.refresh(row)
            return row

    # Strategy 2 — match by normalized name within entity
    row = db.execute(
        select(Account).where(
            Account.org_id == org_id,
            Account.entity_id == entity_id,
            Account.normalized_name == normalized,
        )
    ).scalar_one_or_none()
    if row is not None:
        # Attach source identity if we now have one and the row didn't.
        if source_system_id and not row.source_system_id:
            row.source_system_id = source_system_id
        if source_native_id and not row.source_native_id:
            row.source_native_id = source_native_id
        if source_group_path and not row.source_group_path:
            row.source_group_path = source_group_path
        if opening_balance_inr is not None:
            row.opening_balance_inr = opening_balance_inr
        if commit:
            db.commit()
            db.refresh(row)
        return row

    # Strategy 3 — create new
    category, nature = classify(name, source_group_path, hinted_category)
    account = Account(
        org_id=org_id,
        entity_id=entity_id,
        name=name,
        normalized_name=normalized,
        source_group_path=source_group_path,
        category=category,
        nature=nature,
        source_system_id=source_system_id,
        source_native_id=source_native_id,
        currency_code=currency_code,
        opening_balance_inr=opening_balance_inr or Decimal("0"),
        is_active=True,
    )
    db.add(account)
    if commit:
        db.commit()
        db.refresh(account)
    else:
        db.flush()
    return account


def get_account(
    db: Session, org_id: uuid.UUID, account_id: uuid.UUID
) -> Optional[Account]:
    """Get an account, org-scoped (returns None if cross-tenant)."""
    row = db.get(Account, account_id)
    if row is None or row.org_id != org_id:
        return None
    return row


def list_accounts_by_category(
    db: Session,
    org_id: uuid.UUID,
    category: str,
    entity_id: Optional[uuid.UUID] = None,
) -> list[Account]:
    q = select(Account).where(
        Account.org_id == org_id,
        Account.category == category,
        Account.is_active.is_(True),
    )
    if entity_id is not None:
        q = q.where(Account.entity_id == entity_id)
    return list(db.execute(q.order_by(Account.name)).scalars())


def all_categories() -> set[str]:
    return set(_CATEGORIES)
