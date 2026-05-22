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
