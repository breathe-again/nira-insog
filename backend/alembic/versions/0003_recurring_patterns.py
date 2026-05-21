"""Recurring patterns + tenant learning columns.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21

Adds:
  - recurring_patterns: one row per detected monthly/weekly recurring spend.
    Updated by services/recurring.py after every bank-statement ingest.
  - bank_transactions.is_recurring: nullable bool, set True when a txn
    matches an existing recurring pattern (so the dashboard can skip them
    from its anomaly noise).
  - bank_transactions.auto_tagged_by: 'vendor_default' | 'recurring' | 'manual'
    audit trail for why a category was applied.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recurring_patterns",
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
            sa.ForeignKey("vendors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Canonical "label" for the recurring pattern. For vendor-matched
        # patterns this is the vendor name. For unmatched (e.g. "RENT") it's
        # the cleaned-up description prefix.
        sa.Column("label", sa.String(255), nullable=False),
        # 'monthly' for now; future: 'weekly', 'quarterly', 'annual'.
        sa.Column(
            "cadence", sa.String(20), nullable=False, server_default="monthly"
        ),
        sa.Column("expected_day_of_month", sa.Integer(), nullable=True),
        # Tight band around the typical amount. We flag a payment as recurring
        # when it falls within [median * (1 - tolerance), median * (1 + tolerance)].
        sa.Column("median_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount_tolerance_pct", sa.Numeric(5, 2), nullable=False, server_default="0.10"),
        sa.Column("observed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_on", sa.Date(), nullable=False),
        sa.Column("last_seen_on", sa.Date(), nullable=False),
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
        "ix_recurring_org_label", "recurring_patterns", ["org_id", "label"]
    )
    op.create_index(
        "ix_recurring_org_vendor",
        "recurring_patterns",
        ["org_id", "vendor_id"],
    )

    op.add_column(
        "bank_transactions",
        sa.Column("is_recurring", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("auto_tagged_by", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bank_transactions", "auto_tagged_by")
    op.drop_column("bank_transactions", "is_recurring")
    op.drop_index("ix_recurring_org_vendor", table_name="recurring_patterns")
    op.drop_index("ix_recurring_org_label", table_name="recurring_patterns")
    op.drop_table("recurring_patterns")
