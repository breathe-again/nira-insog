#!/usr/bin/env python3
"""End-to-end verification of the canonical ledger pivot.

Run this AFTER applying migration 0008 and ingesting the Quantta
Trial Balance to confirm the cash position widget now reflects ledger
truth (₹79.91L) instead of bank-CSV reconstruction (~₹3.26L).

Usage (inside the api container):
    docker compose exec api python -m scripts.verify_canonical_layer
or:
    cd backend && python scripts/verify_canonical_layer.py

Output is a one-page diagnostic that shows:
  - Migration status (does the canonical schema exist?)
  - Per-org entity setup (each org has at least one entity)
  - Source systems registered and their last sync
  - Canonical-ledger row counts per category
  - Cash position via canonical vs legacy bottom-up reads
  - Trial Balance balance check (debit total = credit total)
"""
from __future__ import annotations

import sys
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select, text

# Allow running from `backend/` dir
sys.path.insert(0, ".")

from common.db import SessionLocal
from common.models import (
    Account,
    BankTransaction,
    Entity,
    LedgerEntry,
    Organization,
    SourceSystem,
    Transaction,
)
from services.canonical import dashboard_kpis as kpis
from services.canonical import ledger as ledger_svc


def _money(d: Optional[Decimal]) -> str:
    if d is None:
        return "—"
    d = Decimal(d)
    # Indian comma format: 1,23,45,678.90 — fallback to plain comma
    sign = "-" if d < 0 else ""
    s = f"{abs(d):,.2f}"
    return f"{sign}₹{s}"


def _hdr(title: str) -> None:
    print()
    print(f"=== {title} ".ljust(78, "="))


def main() -> int:
    db = SessionLocal()
    errors = 0
    try:
        # ---- Schema sanity ---------------------------------------------------
        _hdr("Schema sanity")
        for tbl in (
            "entities", "accounts", "ledger_entries", "transactions",
            "source_systems", "tenant_settings", "reconciliation_findings",
            "approvals", "approval_policies", "approval_actions",
        ):
            exists = db.execute(
                text("SELECT to_regclass(:t)"), {"t": tbl}
            ).scalar()
            mark = "OK " if exists else "MISSING"
            print(f"  {mark:8s} table {tbl}")
            if not exists:
                errors += 1

        # ---- Orgs + entities -------------------------------------------------
        _hdr("Orgs + entities")
        orgs = list(db.execute(select(Organization).order_by(Organization.name)).scalars())
        if not orgs:
            print("  (no organizations in DB)")
        for org in orgs:
            ents = list(
                db.execute(
                    select(Entity).where(Entity.org_id == org.id).order_by(Entity.created_at)
                ).scalars()
            )
            print(f"  {org.name:40s}  ({len(ents)} entit{'y' if len(ents)==1 else 'ies'})")
            for e in ents:
                print(f"    └─ {e.legal_name}  base={e.base_currency}  fy_start_month={e.financial_year_start_month}")
            if not ents:
                print(f"    !! org has no entities — migration backfill may have failed")
                errors += 1

        # ---- Source systems --------------------------------------------------
        _hdr("Source systems")
        srcs = list(db.execute(select(SourceSystem).order_by(SourceSystem.system_type)).scalars())
        if not srcs:
            print("  (no source systems registered yet — TB upload not done?)")
        for s in srcs:
            org_name = next((o.name for o in orgs if o.id == s.org_id), "?")
            sync_at = s.last_sync_at.isoformat() if s.last_sync_at else "never"
            print(
                f"  [{s.system_type:25s}] {s.display_name or '-':40s} "
                f"org={org_name}  last_sync={sync_at}  status={s.last_sync_status or '-'}"
            )

        # ---- Per-org canonical KPIs ------------------------------------------
        _hdr("Per-org canonical KPIs (canonical-first reads)")
        for org in orgs:
            # Canonical-side counts
            n_accounts = db.execute(
                select(func.count()).select_from(Account).where(Account.org_id == org.id)
            ).scalar() or 0
            n_entries = db.execute(
                select(func.count()).select_from(LedgerEntry).where(LedgerEntry.org_id == org.id)
            ).scalar() or 0
            n_txns = db.execute(
                select(func.count()).select_from(Transaction).where(Transaction.org_id == org.id)
            ).scalar() or 0
            n_bank_txns = db.execute(
                select(func.count()).select_from(BankTransaction).where(BankTransaction.org_id == org.id)
            ).scalar() or 0

            print(f"\n  {org.name}")
            print(f"    canonical:  accounts={n_accounts:5d}  ledger_entries={n_entries:5d}  transactions={n_txns:5d}")
            print(f"    legacy:     bank_transactions={n_bank_txns:5d}")
            print(f"    cash position .... {_money(kpis.get_cash_position(db, org.id))}")
            print(f"    receivables ...... {_money(kpis.get_receivables(db, org.id))}")
            print(f"    payables ......... {_money(kpis.get_payables(db, org.id))}")
            print(f"    loans payable .... {_money(kpis.get_loans_payable(db, org.id))}")
            print(f"    investments ...... {_money(kpis.get_investments(db, org.id))}")
            print(f"    fixed assets ..... {_money(kpis.get_fixed_assets(db, org.id))}")

            # Trial balance integrity
            if n_entries > 0:
                tb = ledger_svc.get_trial_balance(db, org_id=org.id)
                total_dr = sum((row["debit_total_inr"] for row in tb), Decimal("0"))
                total_cr = sum((row["credit_total_inr"] for row in tb), Decimal("0"))
                delta = total_dr - total_cr
                ok = abs(delta) < Decimal("1.00")
                print(
                    f"    TB integrity:    Dr {_money(total_dr)}  Cr {_money(total_cr)}  "
                    f"Δ {_money(delta)}  {'✓ balanced' if ok else '✗ UNBALANCED'}"
                )
                if not ok:
                    errors += 1

        # ---- Data freshness diagnostics --------------------------------------
        _hdr("Data freshness per org")
        for org in orgs:
            fresh = kpis.get_data_freshness(db, org.id)
            print(f"  {org.name}: {fresh['canonical_entries']} canonical entries across {len(fresh['sources'])} sources")
            for src in fresh["sources"][:5]:
                print(f"    - {src['system_type']:25s} {src['last_sync_at'] or 'never':30s} {src['last_sync_status'] or '-'}")

        # ---- Summary --------------------------------------------------------
        _hdr("Summary")
        if errors:
            print(f"  {errors} issue(s) found.")
            return 1
        print("  All checks passed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
