"""Nira's canonical ledger layer.

This is the source-of-truth layer for financial data in Nira. Every source
connector (Tally, Zoho, bank CSVs, AA, GSTN, manual journals) writes into
the canonical schema; dashboards and intelligence modules read from it.

The architectural pivot from bottom-up bank-statement reconstruction is
documented in ARCHITECTURE_PLAN.md at the repo root.

Public API::

    from services.canonical import ledger, accounts, entities

    # Resolve which entity a piece of data belongs to.
    entity = entities.get_default_entity(db, org_id)

    # Find or create an account (chart-of-accounts node).
    account = accounts.find_or_create(
        db, org_id=org_id, entity_id=entity.id,
        source_name="ICICI Bank — Curr A/c 1234",
        source_group_path="Primary>Assets>Current>Bank",
        source_system_id=source_id,
    )

    # Post a balanced double-entry transaction.
    ledger.post_journal(db, org_id=org_id, entity_id=entity.id,
                       txn_date=date(2026, 3, 31),
                       txn_type='payment',
                       legs=[
                           ledger.Leg(account_id=cash_id, credit=100000),
                           ledger.Leg(account_id=rent_id, debit=100000),
                       ])

    # Read trial balance.
    tb = ledger.get_trial_balance(db, org_id=org_id, entity_id=entity.id,
                                  as_of_date=date(2026, 3, 31))
"""

from services.canonical import accounts, entities, ledger

__all__ = ["ledger", "accounts", "entities"]
