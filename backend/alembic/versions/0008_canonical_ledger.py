"""Canonical ledger — entities, accounts, ledger_entries, tenant_settings,
source_systems, reconciliation_findings.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-27

The architectural pivot: Nira stops reconstructing books from bank statements
and starts treating ledgers (Tally / Zoho / QuickBooks) as the source of truth.
This migration introduces the canonical layer every source feeds into and
every dashboard widget reads from.

Multi-tenancy disciplines kept from day one (cheap now, expensive to retrofit
later when CEO wants to sell as SaaS):

  - Every table carries `org_id` + index. No exceptions.
  - Every ledger-bearing table carries `entity_id` (multi-entity from day one).
    Quantta is one entity today; mid-market groups have 1-3 entities (operating
    co + investment vehicle + family LLP) — adding it later means a backfill.
  - Multi-currency on every monetary row: `currency_code` + `amount_native` +
    `amount_inr`. Pure-INR clients ignore the FX columns. Export-heavy clients
    (USD consulting, etc.) become possible without migration.
  - Source attribution on every row: `source_system`, `source_record_id`,
    `source_document_id`, `confidence`. When Tally says X and the bank says Y
    we know who said what.
  - Per-tenant config in `tenant_settings` (not env vars): Setu API keys,
    Tally URLs, GSP credentials, feature flags. Helper falls back to env vars
    for current single-org operation.
  - Approval workflow tables ship empty + behind a feature flag. Customers
    that need maker/checker on payments + invoices flip
    `tenant_settings.approvals_enabled` to true; no schema change required.

Bank_transactions / invoices / receipts get an optional `ledger_entry_id` FK
so document-level details (NEFT references, OCR scans, vendor GSTINs) can
enrich a canonical ledger row. Old tables stay unchanged otherwise — dual-read
strategy gives us 2 weeks to validate before deprecating the bottom-up path.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Canonical account categories.
#
# Every account in every customer's chart of accounts maps to exactly one of
# these. Tally's "Sundry Debtors", Zoho's "Accounts Receivable", and
# QuickBooks's "A/R" all collapse to "receivables" here. Dashboard widgets
# query by category, so they remain source-agnostic.
# ---------------------------------------------------------------------------
_ACCOUNT_CATEGORIES = [
    "cash",            # Cash-in-hand
    "bank",            # Bank accounts (current / savings / OD)
    "receivables",     # Sundry debtors, customer receivables
    "payables",        # Sundry creditors, vendor payables
    "loans_payable",   # Secured / unsecured loans
    "loans_receivable",
    "inventory",
    "fixed_asset",
    "investment",      # Mutual funds, SGB, warrants, equity
    "current_asset",   # Catch-all asset (advances, deposits, prepaid)
    "current_liability",
    "statutory_liability",   # PF, gratuity, GST payable, TDS payable
    "equity",          # Share capital, reserves
    "income",          # Revenue, other income
    "direct_expense",  # COGS, freight, direct labour
    "indirect_expense", # SG&A, rent, salaries, software
    "tax_expense",
    "suspense",        # Things we couldn't categorize yet
]


def upgrade() -> None:
    # -----------------------------------------------------------------
    # tenant_settings — per-org key/value config (replaces env vars
    # for anything per-tenant). The `encrypted` flag is set true for
    # rows whose value_json was encrypted with Fernet before insert.
    # -----------------------------------------------------------------
    op.create_table(
        "tenant_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value_json", JSONB, nullable=True),
        sa.Column("encrypted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_tenant_settings_org", "tenant_settings", ["org_id"], unique=False)
    op.create_index(
        "uq_tenant_settings_org_key",
        "tenant_settings",
        ["org_id", "key"],
        unique=True,
    )

    # -----------------------------------------------------------------
    # entities — legal entities under an org. An "organization" is the
    # Nira account; an "entity" is a legal entity it owns. Most users
    # have one; mid-market groups have several.
    # -----------------------------------------------------------------
    op.create_table(
        "entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=80), nullable=True),
        sa.Column("registration_number", sa.String(length=40), nullable=True),  # CIN / LLPIN
        sa.Column("pan", sa.String(length=15), nullable=True),
        sa.Column("gstin", sa.String(length=20), nullable=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default=sa.text("'INR'")),
        # For group consolidation
        sa.Column("parent_entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default=sa.text("'IN'")),
        sa.Column("financial_year_start_month", sa.SmallInteger, nullable=False, server_default=sa.text("4")),  # India = April
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_entities_org", "entities", ["org_id"], unique=False)
    op.create_index("ix_entities_org_pan", "entities", ["org_id", "pan"], unique=False)
    op.create_index("ix_entities_org_gstin", "entities", ["org_id", "gstin"], unique=False)

    # -----------------------------------------------------------------
    # source_systems — one row per (org, source) configuration.
    # Holds the cursor (last_sync_at, last_record_id) + auth blob.
    # auth_secrets_enc is Fernet-encrypted JSON.
    # -----------------------------------------------------------------
    op.create_table(
        "source_systems",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True),
        # Type discriminator. Examples: 'tally', 'zoho_books', 'quickbooks',
        # 'setu_aa', 'gstn', 'traces', 'bank_csv', 'manual_upload'.
        sa.Column("system_type", sa.String(length=40), nullable=False),
        # Free-form display name set by the user ("HDFC current account",
        # "Tally on AWS — Quantta")
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("config_json", JSONB, nullable=True),
        sa.Column("cursor_json", JSONB, nullable=True),  # connector-specific watermarks
        sa.Column("auth_secrets_enc", sa.Text, nullable=True),  # Fernet ciphertext
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),  # ok | error | partial
        sa.Column("last_sync_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_source_systems_org", "source_systems", ["org_id"], unique=False)
    op.create_index("ix_source_systems_org_type", "source_systems", ["org_id", "system_type"], unique=False)

    # -----------------------------------------------------------------
    # accounts — the canonical chart of accounts.
    #
    # An "account" is a node in the chart-of-accounts tree.  Each row maps
    # to one canonical category (cash/bank/...) and remembers the source's
    # native name + group path so we can show the user-familiar label
    # in the UI ("Sundry Debtors", "ICICI Bank — Curr A/c").
    # -----------------------------------------------------------------
    op.create_table(
        "accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        # Tree
        sa.Column("parent_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("depth", sa.SmallInteger, nullable=False, server_default=sa.text("0")),
        # Names
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),  # lowercase, stripped
        sa.Column("source_group_path", sa.String(length=500), nullable=True),  # "Primary>Assets>Current>Bank"
        # Classification
        sa.Column("category", sa.String(length=40), nullable=False),  # one of _ACCOUNT_CATEGORIES
        sa.Column(
            "nature",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'asset'"),
        ),  # asset | liability | income | expense | equity
        # Provenance
        sa.Column("source_system_id", UUID(as_uuid=True), sa.ForeignKey("source_systems.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_native_id", sa.String(length=200), nullable=True),  # Tally GUID, Zoho account_id, etc.
        # Display
        sa.Column("currency_code", sa.String(length=3), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("opening_balance_inr", sa.Numeric(20, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_accounts_org_entity", "accounts", ["org_id", "entity_id"], unique=False)
    op.create_index("ix_accounts_org_category", "accounts", ["org_id", "category"], unique=False)
    op.create_index("ix_accounts_org_normalized", "accounts", ["org_id", "entity_id", "normalized_name"], unique=False)
    # Uniqueness per source: don't double-create the same Tally ledger
    op.create_index(
        "uq_accounts_source_native",
        "accounts",
        ["org_id", "entity_id", "source_system_id", "source_native_id"],
        unique=True,
        postgresql_where=sa.text("source_system_id IS NOT NULL AND source_native_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------
    # transactions — the higher-level financial event.
    #
    # One transaction groups a balanced set of ledger_entries (i.e. a
    # voucher in Tally terminology, an invoice in Zoho, a journal entry
    # in QB). Single-leg ingestion (bank statement row) creates a 2-leg
    # transaction internally: debit Bank, credit suspense.
    # -----------------------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("txn_date", sa.Date, nullable=False),
        sa.Column("txn_type", sa.String(length=40), nullable=False),
        # ^ payment | receipt | sales | purchase | journal | contra | debit_note |
        #   credit_note | opening_balance | inter_entity
        sa.Column("voucher_number", sa.String(length=120), nullable=True),
        sa.Column("narration", sa.Text, nullable=True),
        sa.Column("party_name", sa.String(length=255), nullable=True),  # counterparty as stated by source
        sa.Column("party_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("fx_rate_to_inr", sa.Numeric(18, 8), nullable=False, server_default=sa.text("1")),
        sa.Column("amount_inr", sa.Numeric(20, 2), nullable=False, server_default=sa.text("0")),
        # Source attribution
        sa.Column("source_system_id", UUID(as_uuid=True), sa.ForeignKey("source_systems.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_native_id", sa.String(length=200), nullable=True),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default=sa.text("1.000")),
        sa.Column("financial_year", sa.SmallInteger, nullable=True),  # e.g. 2025 means FY 2025-26
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transactions_org_date", "transactions", ["org_id", "txn_date"], unique=False)
    op.create_index("ix_transactions_org_entity_date", "transactions", ["org_id", "entity_id", "txn_date"], unique=False)
    op.create_index("ix_transactions_org_type", "transactions", ["org_id", "txn_type"], unique=False)
    op.create_index("ix_transactions_org_fy", "transactions", ["org_id", "financial_year"], unique=False)
    op.create_index(
        "uq_transactions_source_native",
        "transactions",
        ["org_id", "source_system_id", "source_native_id"],
        unique=True,
        postgresql_where=sa.text("source_system_id IS NOT NULL AND source_native_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------
    # ledger_entries — the atomic double-entry row.
    #
    # Sum of debits = sum of credits PER (transaction_id) at the
    # application layer.  We don't enforce it as a DB constraint because
    # single-leg ingestion would fail it during write; the canonical
    # service guarantees balanced posts.
    #
    # period_start/period_end are NULL for daily entries; trial-balance
    # imports use them to mark the period the balance applies to.
    # -----------------------------------------------------------------
    op.create_table(
        "ledger_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("period_start", sa.Date, nullable=True),
        sa.Column("period_end", sa.Date, nullable=True),
        # Money. We always store INR for fast aggregation. Native columns
        # let us round-trip back to the source for non-INR books.
        sa.Column("currency_code", sa.String(length=3), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("debit_native", sa.Numeric(20, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("credit_native", sa.Numeric(20, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("debit_inr", sa.Numeric(20, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("credit_inr", sa.Numeric(20, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("fx_rate_to_inr", sa.Numeric(18, 8), nullable=False, server_default=sa.text("1")),
        sa.Column("narration", sa.Text, nullable=True),
        sa.Column("cost_centre", sa.String(length=120), nullable=True),
        # Provenance
        sa.Column("source_system_id", UUID(as_uuid=True), sa.ForeignKey("source_systems.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_native_id", sa.String(length=200), nullable=True),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        # Confidence: 1.000 for direct ledger imports (Tally TB), lower
        # for inferred entries (bank-CSV row mapped to a likely category).
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default=sa.text("1.000")),
        # Entry kind:
        #   'opening'  — opening balance at period_start
        #   'movement' — actual debit/credit during the period
        #   'closing'  — closing balance at period_end (computed in TB imports)
        #   'adjustment' — adjusting entries
        sa.Column("entry_kind", sa.String(length=20), nullable=False, server_default=sa.text("'movement'")),
        sa.Column("financial_year", sa.SmallInteger, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ledger_entries_org_date", "ledger_entries", ["org_id", "entry_date"], unique=False)
    op.create_index("ix_ledger_entries_org_account_date", "ledger_entries", ["org_id", "account_id", "entry_date"], unique=False)
    op.create_index("ix_ledger_entries_org_txn", "ledger_entries", ["org_id", "transaction_id"], unique=False)
    op.create_index("ix_ledger_entries_org_entity_date", "ledger_entries", ["org_id", "entity_id", "entry_date"], unique=False)
    op.create_index("ix_ledger_entries_org_fy", "ledger_entries", ["org_id", "financial_year"], unique=False)

    # -----------------------------------------------------------------
    # reconciliation_findings — diffs between sources.
    #
    # When Tally says cash = ₹79.91L and bank statements say ₹3.26L,
    # we write a finding with both numbers and a suggested resolution.
    # -----------------------------------------------------------------
    op.create_table(
        "reconciliation_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True),
        # 'cash_vs_bank' | 'gstr2b_vs_purchase' | '26as_vs_tds' | 'invoice_vs_payment' | ...
        sa.Column("finding_type", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default=sa.text("'info'")),
        # ^ info | warning | critical
        # Two sources we're comparing
        sa.Column("source_a_system_id", UUID(as_uuid=True), sa.ForeignKey("source_systems.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_b_system_id", UUID(as_uuid=True), sa.ForeignKey("source_systems.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_a_label", sa.String(length=120), nullable=True),
        sa.Column("source_b_label", sa.String(length=120), nullable=True),
        sa.Column("source_a_value_inr", sa.Numeric(20, 2), nullable=True),
        sa.Column("source_b_value_inr", sa.Numeric(20, 2), nullable=True),
        sa.Column("delta_inr", sa.Numeric(20, 2), nullable=True),
        sa.Column("as_of_date", sa.Date, nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("suggested_action", sa.Text, nullable=True),
        sa.Column("supporting_data", JSONB, nullable=True),
        # Lifecycle
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'open'")),
        # ^ open | investigating | resolved | dismissed | wont_fix
        sa.Column("resolved_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_recon_findings_org", "reconciliation_findings", ["org_id"], unique=False)
    op.create_index("ix_recon_findings_org_status", "reconciliation_findings", ["org_id", "status"], unique=False)
    op.create_index("ix_recon_findings_org_type", "reconciliation_findings", ["org_id", "finding_type"], unique=False)

    # -----------------------------------------------------------------
    # approvals — empty + behind feature flag.
    # Tables ship now so flipping `tenant_settings.approvals_enabled`
    # later requires no schema change.
    # -----------------------------------------------------------------
    op.create_table(
        "approval_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True),
        # 'invoice' | 'payment' | 'journal'
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rule_json", JSONB, nullable=False),  # {"amount_gte": 500000, "approvers": ["cfo"]}
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.SmallInteger, nullable=False, server_default=sa.text("100")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_approval_policies_org", "approval_policies", ["org_id"], unique=False)

    op.create_table(
        "approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", UUID(as_uuid=True), sa.ForeignKey("approval_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        # pending | approved | rejected | cancelled
        sa.Column("required_approvers", JSONB, nullable=False),  # list of user_id or role strings
        sa.Column("current_step", sa.SmallInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approvals_org", "approvals", ["org_id"], unique=False)
    op.create_index("ix_approvals_org_status", "approvals", ["org_id", "status"], unique=False)
    op.create_index("ix_approvals_subject", "approvals", ["subject_type", "subject_id"], unique=False)

    op.create_table(
        "approval_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("approval_id", UUID(as_uuid=True), sa.ForeignKey("approvals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),  # approve | reject | comment
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_approval_actions_approval", "approval_actions", ["approval_id"], unique=False)

    # -----------------------------------------------------------------
    # Link old bottom-up tables to canonical for the dual-read period.
    # Nullable FKs so legacy rows continue to work. Once Phase 2 lands
    # and the cutover is validated we can flip writes to canonical-only.
    # -----------------------------------------------------------------
    op.add_column(
        "bank_transactions",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), sa.ForeignKey("ledger_entries.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_bank_txns_ledger_entry", "bank_transactions", ["ledger_entry_id"], unique=False)

    op.add_column(
        "invoices",
        sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
    )

    # documents → which source produced this file
    op.add_column(
        "documents",
        sa.Column("source_system_id", UUID(as_uuid=True), sa.ForeignKey("source_systems.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
    )

    # Existing tenant-scoped tables get an optional entity_id so we can
    # group by entity later without a separate migration. NULL = "the
    # default entity" (resolved at query time as the org's first entity).
    op.add_column(
        "bank_accounts",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
    )

    # -----------------------------------------------------------------
    # Seed: create a default entity for every existing organization.
    # Quantta (and any other tenant) gets one auto-entity matching its
    # name so multi-entity discipline works from day one without
    # requiring the user to set up an entity manually.
    # -----------------------------------------------------------------
    op.execute(
        """
        INSERT INTO entities (
            id, org_id, legal_name, short_name, base_currency,
            country_code, financial_year_start_month, is_active,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            o.id,
            o.name,
            o.name,
            'INR',
            'IN',
            4,
            true,
            now(),
            now()
        FROM organizations o
        WHERE NOT EXISTS (
            SELECT 1 FROM entities e WHERE e.org_id = o.id
        )
        """
    )

    # Backfill entity_id on existing rows to the default entity.
    op.execute(
        """
        UPDATE bank_accounts ba
        SET entity_id = (SELECT id FROM entities e WHERE e.org_id = ba.org_id ORDER BY e.created_at LIMIT 1)
        WHERE entity_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE bank_transactions bt
        SET entity_id = (SELECT id FROM entities e WHERE e.org_id = bt.org_id ORDER BY e.created_at LIMIT 1)
        WHERE entity_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE invoices i
        SET entity_id = (SELECT id FROM entities e WHERE e.org_id = i.org_id ORDER BY e.created_at LIMIT 1)
        WHERE entity_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE receipts r
        SET entity_id = (SELECT id FROM entities e WHERE e.org_id = r.org_id ORDER BY e.created_at LIMIT 1)
        WHERE entity_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE documents d
        SET entity_id = (SELECT id FROM entities e WHERE e.org_id = d.org_id ORDER BY e.created_at LIMIT 1)
        WHERE entity_id IS NULL
        """
    )


def downgrade() -> None:
    # Drop linkage columns first
    op.drop_index("ix_bank_txns_ledger_entry", table_name="bank_transactions")
    op.drop_column("receipts", "entity_id")
    op.drop_column("receipts", "transaction_id")
    op.drop_column("invoices", "entity_id")
    op.drop_column("invoices", "transaction_id")
    op.drop_column("bank_transactions", "entity_id")
    op.drop_column("bank_transactions", "transaction_id")
    op.drop_column("bank_transactions", "ledger_entry_id")
    op.drop_column("bank_accounts", "entity_id")
    op.drop_column("documents", "entity_id")
    op.drop_column("documents", "source_system_id")

    # Drop new tables in reverse FK order
    op.drop_index("ix_approval_actions_approval", table_name="approval_actions")
    op.drop_table("approval_actions")
    op.drop_index("ix_approvals_subject", table_name="approvals")
    op.drop_index("ix_approvals_org_status", table_name="approvals")
    op.drop_index("ix_approvals_org", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_approval_policies_org", table_name="approval_policies")
    op.drop_table("approval_policies")

    op.drop_index("ix_recon_findings_org_type", table_name="reconciliation_findings")
    op.drop_index("ix_recon_findings_org_status", table_name="reconciliation_findings")
    op.drop_index("ix_recon_findings_org", table_name="reconciliation_findings")
    op.drop_table("reconciliation_findings")

    op.drop_index("ix_ledger_entries_org_fy", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_org_entity_date", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_org_txn", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_org_account_date", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_org_date", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.drop_index("uq_transactions_source_native", table_name="transactions")
    op.drop_index("ix_transactions_org_fy", table_name="transactions")
    op.drop_index("ix_transactions_org_type", table_name="transactions")
    op.drop_index("ix_transactions_org_entity_date", table_name="transactions")
    op.drop_index("ix_transactions_org_date", table_name="transactions")
    op.drop_table("transactions")

    op.drop_index("uq_accounts_source_native", table_name="accounts")
    op.drop_index("ix_accounts_org_normalized", table_name="accounts")
    op.drop_index("ix_accounts_org_category", table_name="accounts")
    op.drop_index("ix_accounts_org_entity", table_name="accounts")
    op.drop_table("accounts")

    op.drop_index("ix_source_systems_org_type", table_name="source_systems")
    op.drop_index("ix_source_systems_org", table_name="source_systems")
    op.drop_table("source_systems")

    op.drop_index("ix_entities_org_gstin", table_name="entities")
    op.drop_index("ix_entities_org_pan", table_name="entities")
    op.drop_index("ix_entities_org", table_name="entities")
    op.drop_table("entities")

    op.drop_index("uq_tenant_settings_org_key", table_name="tenant_settings")
    op.drop_index("ix_tenant_settings_org", table_name="tenant_settings")
    op.drop_table("tenant_settings")
