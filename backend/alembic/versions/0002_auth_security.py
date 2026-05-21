"""Auth + security tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21

Adds:
  - users.password_hash (argon2id), users.email_verified_at, users.is_active,
    users.last_login_at, users.failed_login_count, users.locked_until
  - organizations.slug (URL-safe identifier, unique per org)
  - sessions (refresh-token registry — revocable, rotatable)
  - audit_events (security-sensitive actions: logins, role changes, vendor
    merges, doc edits, ...)
  - filename_hints (learned: filename pattern → document_type)
  - vendor_mutes (per-vendor anomaly silencing)
  - documents.encryption_meta (encryption metadata: scheme, nonce, ...)

After this migration the legacy demo seed in `api/deps.py` still works for
local dev (with DEMO_MODE=1) but production reads from sessions/JWT.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users: auth columns ----------------------------------------------
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    # Normalize email lookups; CITEXT would be cleaner but lower() index works.
    op.create_index(
        "ix_users_email_lower", "users", [sa.text("lower(email)")], unique=True
    )

    # --- organizations: slug ----------------------------------------------
    op.add_column("organizations", sa.Column("slug", sa.String(64), nullable=True))
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    # --- sessions ---------------------------------------------------------
    # One row per active refresh token. Access tokens are stateless (JWT) but
    # refresh tokens are server-side so we can revoke them on logout / password
    # change / suspicious activity.
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Hash of the refresh token (sha256) — we never store the plaintext.
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sessions_token_hash", "sessions", ["refresh_token_hash"], unique=True
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id", "revoked_at"])

    # --- audit_events -----------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_audit_events_org_created", "audit_events", ["org_id", "created_at"]
    )
    op.create_index(
        "ix_audit_events_type_created", "audit_events", ["event_type", "created_at"]
    )

    # --- filename_hints ---------------------------------------------------
    op.create_table(
        "filename_hints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pattern", sa.String(255), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_filename_hints_org_pattern",
        "filename_hints",
        ["org_id", "pattern"],
        unique=True,
    )

    # --- vendor_mutes -----------------------------------------------------
    # Per-vendor anomaly silencing — set when a user says "this is normal".
    op.create_table(
        "vendor_mutes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule", sa.String(60), nullable=False, server_default="anomaly"),
        sa.Column(
            "muted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_vendor_mutes_vendor_rule",
        "vendor_mutes",
        ["vendor_id", "rule"],
        unique=True,
    )

    # --- documents: encryption metadata -----------------------------------
    op.add_column(
        "documents",
        sa.Column(
            "encryption_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "encryption_meta")
    op.drop_index("ix_vendor_mutes_vendor_rule", table_name="vendor_mutes")
    op.drop_table("vendor_mutes")
    op.drop_index("ix_filename_hints_org_pattern", table_name="filename_hints")
    op.drop_table("filename_hints")
    op.drop_index("ix_audit_events_type_created", table_name="audit_events")
    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_sessions_user", table_name="sessions")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_column("organizations", "slug")
    op.drop_index("ix_users_email_lower", table_name="users")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "password_hash")
