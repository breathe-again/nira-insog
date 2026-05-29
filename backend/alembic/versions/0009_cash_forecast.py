"""Cash forecast tables — runs, points, drivers.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-28

The 13-week (91-day) rolling cash forecast is Nira's most visible
finance-team-facing differentiator. Comparable tools (Trovata, Drivetrain,
Cube) lead with this. Mid-market CFOs will pay ₹25K+/month for it alone.

Architecture:

  cash_forecast_runs       one row per generation. Snapshots config
                           + starting cash + horizon + drivers count.
                           Subsequent reads ("show me the current
                           forecast") point at the most recent run.

  cash_forecast_points     one row per (run_id, day, scenario).
                           Three scenarios per day: pessimistic /
                           likely / optimistic. Day 0 = "today",
                           runs through Day 91 by default.

  forecast_drivers         the recurring inflows + outflows + open
                           AR/AP rows the engine identified during
                           this run, with their projected dates +
                           amounts. Surfaces in the "Why this
                           forecast?" UI panel — non-technical
                           CFOs need to see WHY the line moves.

Multi-tenant: every row carries org_id + entity_id.
Multi-currency: amounts stored in INR (we project from canonical
ledger which already normalises FX at posting time).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # cash_forecast_runs — one row per forecast generation.
    # -----------------------------------------------------------------
    op.create_table(
        "cash_forecast_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column(
            "horizon_days",
            sa.SmallInteger,
            nullable=False,
            server_default=sa.text("91"),
        ),
        sa.Column("starting_cash_inr", sa.Numeric(20, 2), nullable=False),
        # Snapshot of which canonical sources were used (Tally, bank CSV, AA, ...)
        sa.Column("source_systems_json", JSONB, nullable=True),
        # Engine config used (smoothing window, seasonality flags, etc.)
        sa.Column("config_json", JSONB, nullable=True),
        # Summary stats — show in UI without re-aggregating
        sa.Column("drivers_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("inflows_total_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("outflows_total_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("ending_cash_likely_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("ending_cash_pessimistic_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("ending_cash_optimistic_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        # When does the likely-scenario line cross zero (runway)?
        # NULL = never within the horizon = safe.
        sa.Column("runway_zero_date", sa.Date, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'ok'")),
        # 'ok' | 'partial' | 'error' | 'pending'
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "generated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # 'manual' | 'scheduled' | 'auto_on_upload'
        sa.Column("trigger", sa.String(20), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_cash_forecast_runs_org", "cash_forecast_runs", ["org_id"], unique=False)
    op.create_index(
        "ix_cash_forecast_runs_org_created",
        "cash_forecast_runs",
        ["org_id", "created_at"],
        unique=False,
    )

    # -----------------------------------------------------------------
    # cash_forecast_points — daily projection per scenario.
    # -----------------------------------------------------------------
    op.create_table(
        "cash_forecast_points",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cash_forecast_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("point_date", sa.Date, nullable=False),
        sa.Column("days_from_now", sa.SmallInteger, nullable=False),
        # The three scenarios. We store cash position AT END OF DAY.
        sa.Column("cash_pessimistic_inr", sa.Numeric(20, 2), nullable=False),
        sa.Column("cash_likely_inr", sa.Numeric(20, 2), nullable=False),
        sa.Column("cash_optimistic_inr", sa.Numeric(20, 2), nullable=False),
        # Inflows + outflows expected on this specific day (likely scenario).
        # Useful for tooltips: "On Jul 1 — +₹4L customer payment, -₹3.5L payroll"
        sa.Column("inflow_likely_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("outflow_likely_inr", sa.Numeric(20, 2), nullable=False, server_default="0"),
        # Once the date passes, we backfill actual_cash_inr from the canonical
        # ledger so the UI can show variance.
        sa.Column("actual_cash_inr", sa.Numeric(20, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_cash_fp_run_date", "cash_forecast_points", ["run_id", "point_date"], unique=True)
    op.create_index("ix_cash_fp_org_date", "cash_forecast_points", ["org_id", "point_date"], unique=False)

    # -----------------------------------------------------------------
    # forecast_drivers — recurring or scheduled inflows/outflows the
    # engine factored in. Powers the "Why this forecast?" panel.
    # -----------------------------------------------------------------
    op.create_table(
        "forecast_drivers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cash_forecast_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'recurring_inflow' | 'recurring_outflow' |
        # 'open_receivable' | 'open_payable' | 'scheduled_tax' |
        # 'opening_balance' | 'one_off'
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "client_id",
            UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expected_date", sa.Date, nullable=True),
        sa.Column("expected_amount_inr", sa.Numeric(20, 2), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),  # 'inflow' | 'outflow'
        # Confidence 0..1 — drives the spread between scenarios.
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default=sa.text("0.7")),
        # Source of this driver: 'recurring_pattern' | 'invoice' |
        # 'tally_voucher' | 'manual' | 'tax_calendar'
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column(
            "source_recurring_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recurring_patterns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("supporting_data", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_forecast_drivers_run", "forecast_drivers", ["run_id"], unique=False)
    op.create_index("ix_forecast_drivers_org", "forecast_drivers", ["org_id"], unique=False)
    op.create_index(
        "ix_forecast_drivers_run_kind",
        "forecast_drivers",
        ["run_id", "kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_forecast_drivers_run_kind", table_name="forecast_drivers")
    op.drop_index("ix_forecast_drivers_org", table_name="forecast_drivers")
    op.drop_index("ix_forecast_drivers_run", table_name="forecast_drivers")
    op.drop_table("forecast_drivers")

    op.drop_index("ix_cash_fp_org_date", table_name="cash_forecast_points")
    op.drop_index("ix_cash_fp_run_date", table_name="cash_forecast_points")
    op.drop_table("cash_forecast_points")

    op.drop_index("ix_cash_forecast_runs_org_created", table_name="cash_forecast_runs")
    op.drop_index("ix_cash_forecast_runs_org", table_name="cash_forecast_runs")
    op.drop_table("cash_forecast_runs")
