"""SQLAlchemy 2.0 ORM models for Nira Insig.

These mirror the entities described in docs/03-data-model.md.

Conventions:
- UUID primary keys (Postgres native uuid).
- `created_at` / `updated_at` on most entities, with server defaults.
- `org_id` on every tenant-scoped entity, indexed.
- Enum values stored as strings; the canonical set lives in common/enums.py.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _ts_now() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Organization & User
# ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    plan: Mapped[str] = mapped_column(String(20), nullable=False, default="trial")
    created_at: Mapped[datetime] = _ts_now()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="founder")
    # Auth fields (Phase A). password_hash is argon2id; nullable so the legacy
    # demo user (no password) keeps working when DEMO_MODE=1.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_login_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_now()


# ---------------------------------------------------------------------------
# Session — server-side refresh token registry (revocable)
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[uuid.UUID] = _org_fk()
    # sha256 of the refresh token — never store plaintext.
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _ts_now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Invite — link-based team invitation
# ---------------------------------------------------------------------------


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    # 32 random bytes hex (64 chars). Goes into the shareable URL.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (Index("ix_invites_org_email", "org_id", "email"),)


# ---------------------------------------------------------------------------
# AuditEvent — security-sensitive actions
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Column name in the DB is "metadata" but that's a reserved attribute on
    # SQLAlchemy Base subclasses — map it to .meta in Python.
    meta: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = _ts_now()


# ---------------------------------------------------------------------------
# FilenameHint — learned mapping from filename pattern → document_type
# ---------------------------------------------------------------------------


class FilenameHint(Base):
    __tablename__ = "filename_hints"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    hit_count: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# VendorMute — per-vendor anomaly silencing
# ---------------------------------------------------------------------------


class VendorMute(Base):
    __tablename__ = "vendor_mutes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule: Mapped[str] = mapped_column(String(60), nullable=False, default="anomaly")
    muted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_now()


# ---------------------------------------------------------------------------
# Counterparties
# ---------------------------------------------------------------------------


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    default_expense_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = _ts_now()

    __table_args__ = (Index("ix_vendors_org_name", "org_id", "name"),)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = _ts_now()

    __table_args__ = (Index("ix_clients_org_name", "org_id", "name"),)


# ---------------------------------------------------------------------------
# Bank accounts and transactions
# ---------------------------------------------------------------------------


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    # NULL = belongs to the org's default entity (resolved at read time).
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_number_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    current_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    created_at: Mapped[datetime] = _ts_now()


# ---------------------------------------------------------------------------
# Document — the atomic input unit
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received", index=True)
    raw_extraction_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Encryption-at-rest metadata: {scheme: "fernet-v1", key_id: "v1", ...}
    # NULL means the file is stored in plaintext (legacy uploads pre-Phase A).
    encryption_meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Which source produced this file. NULL for legacy uploads. Wired into
    # the canonical layer via 0008 — every new ingestion sets this.
    source_system_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # SHA-256 of the raw uploaded bytes. Lets us reject re-uploads of the same
    # physical file with HTTP 409. Nullable so legacy rows from before this
    # column existed remain valid; new uploads always populate it.
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Soft-delete timestamp. Set when the duplicate-review queue marks a doc
    # as a redundant copy. Dashboard queries filter out non-null deleted_at.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = _ts_now()
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_documents_org_created", "org_id", "created_at"),)


# ---------------------------------------------------------------------------
# Bank transactions
# ---------------------------------------------------------------------------


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    running_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    matched_invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    matched_vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    matched_client_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Tier 1 learning: did this txn match a known recurring pattern?
    is_recurring: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # 'vendor_default' | 'recurring' | 'manual' | None
    auto_tagged_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # Canonical-layer links (Phase 2). Bank txns that have been promoted
    # into the canonical ledger carry these FKs back to the canonical row.
    # During dual-read we may have both bank_transactions AND ledger_entries
    # for the same logical event; canonical wins.
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    ledger_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_now()

    __table_args__ = (Index("ix_bank_txns_org_date", "org_id", "txn_date"),)


# ---------------------------------------------------------------------------
# RecurringPattern — learned monthly/weekly recurring spend
# ---------------------------------------------------------------------------


class RecurringPattern(Base):
    __tablename__ = "recurring_patterns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    cadence: Mapped[str] = mapped_column(
        String(20), nullable=False, default="monthly"
    )
    expected_day_of_month: Mapped[Optional[int]] = mapped_column(nullable=True)
    median_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_tolerance_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.10")
    )
    observed_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    first_seen_on: Mapped[date] = mapped_column(Date, nullable=False)
    last_seen_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Invoices (sales + purchase)
# ---------------------------------------------------------------------------


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # sales | purchase
    invoice_number: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="issued")
    line_items: Mapped[Optional[list[dict]]] = mapped_column(JSONB, nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_now()

    __table_args__ = (
        Index("ix_invoices_org_status_due", "org_id", "status", "due_date"),
    )


# ---------------------------------------------------------------------------
# Receipts (standalone expense receipts)
# ---------------------------------------------------------------------------


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payment_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_now()

    __table_args__ = (Index("ix_receipts_org_date_cat", "org_id", "date", "category"),)


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


class Insight(Base):
    __tablename__ = "insights"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts_now()
    dismissed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_insights_org_created_dismissed", "org_id", "created_at", "dismissed_at"),
    )


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts_now()


# ===========================================================================
# CANONICAL LAYER (introduced in migration 0008)
# ===========================================================================
#
# This is Nira's source-of-truth representation of financial data. Sources
# (Tally, Zoho, bank CSVs, AA, GSTN, manual journals) all feed into this
# layer; dashboards and intelligence modules read from it.
#
# The architectural pivot: stop reconstructing the books from bank
# statements (bottom-up, ceiling at ~10% visibility). Start treating
# ledgers as the source of truth and use Nira to aggregate + intelligence
# + reconcile across multiple sources.
# ===========================================================================


class TenantSetting(Base):
    """Per-tenant key/value config. Replaces env vars for anything that
    varies per organization (Setu key, Tally URL, GSP creds, feature flags).

    `read_tenant_setting(org_id, key)` in services/tenant_settings.py
    handles encrypted-value decryption + env-var fallback transparently.
    """

    __tablename__ = "tenant_settings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    encrypted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("uq_tenant_settings_org_key", "org_id", "key", unique=True),
    )


class Entity(Base):
    """A legal entity under an organization. Mid-market groups have 1-3
    (operating co + investment vehicle + family LLP). The migration
    auto-seeds one entity per org so single-entity tenants don't need to
    set this up manually.
    """

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    registration_number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    pan: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    base_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    parent_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="IN", server_default="IN"
    )
    financial_year_start_month: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=4, server_default="4"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SourceSystem(Base):
    """One row per (org, source-system) configuration. Holds the cursor +
    Fernet-encrypted auth blob. Connectors read/write only this row when
    syncing.
    """

    __tablename__ = "source_systems"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
    )
    system_type: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    config_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    cursor_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    auth_secrets_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Account(Base):
    """A node in the canonical chart of accounts. Tally's 'Sundry Debtors',
    Zoho's 'Accounts Receivable', and QB's 'A/R' all map to category
    'receivables' here. Dashboard widgets query by category — source-
    agnostic.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    depth: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_group_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    # asset | liability | income | expense | equity
    nature: Mapped[str] = mapped_column(
        String(20), nullable=False, default="asset", server_default="asset"
    )
    source_system_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_native_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    opening_balance_inr: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Transaction(Base):
    """A higher-level financial event grouping a balanced set of
    ledger_entries (one Tally voucher = one Transaction here).

    For single-leg bank-CSV ingestion the canonical service creates a
    2-leg transaction internally: debit Bank, credit Suspense (until a
    payment/receipt classifier identifies the counterparty).
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    txn_type: Mapped[str] = mapped_column(String(40), nullable=False)
    voucher_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    narration: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    party_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    party_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    fx_rate_to_inr: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("1"), server_default="1"
    )
    amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    source_system_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_native_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("1.000"), server_default="1.000"
    )
    financial_year: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LedgerEntry(Base):
    """The atomic double-entry row. Sum of debit_inr = sum of credit_inr
    PER transaction_id at the application layer (not enforced in DB
    because single-leg ingestion needs to write provisional entries).

    period_start/period_end are NULL for daily entries; trial-balance
    imports populate them to mark the period a balance applies to.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    debit_native: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    credit_native: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    debit_inr: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    credit_inr: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    fx_rate_to_inr: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("1"), server_default="1"
    )
    narration: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cost_centre: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_system_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_native_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("1.000"), server_default="1.000"
    )
    entry_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="movement", server_default="movement"
    )
    # 'opening' | 'movement' | 'closing' | 'adjustment'
    financial_year: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ReconciliationFinding(Base):
    """When two sources disagree, we write a finding here. Example: Tally
    says cash = ₹79.91L, bank statements say ₹3.26L → finding type
    'cash_vs_bank', severity 'critical', delta ₹76.65L.
    """

    __tablename__ = "reconciliation_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
    )
    finding_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="info", server_default="info"
    )
    source_a_system_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_b_system_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_a_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_b_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_a_value_inr: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    source_b_value_inr: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    delta_inr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    as_of_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    supporting_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Approvals — empty + behind feature flag (tenant_settings.approvals_enabled)
# Tables ship now so flipping the flag later requires no schema change.
# ---------------------------------------------------------------------------


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
    )
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=100, server_default="100"
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
    )
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    required_approvers: Mapped[list] = mapped_column(JSONB, nullable=False)
    current_step: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ApprovalAction(Base):
    __tablename__ = "approval_actions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    approval_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acted_at: Mapped[datetime] = _ts_now()
