"""Learning + training status endpoints.

Surfaces every Tier-1 learning signal in the UI so the founder can verify the
system is actually doing what we say it does — without SSH.

  - GET  /api/learning/status   →  stats + adaptive threshold + pattern list
  - POST /api/learning/retrain  →  re-run pattern detection + missed-payment
                                   detection synchronously against the
                                   tenant's full history. Returns the new
                                   counts so the UI can refresh.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import mean, stdev
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from common.models import BankTransaction, Insight, RecurringPattern, Vendor
from services.anomalies import _adaptive_z_threshold, rehumanize_existing_insights
from services.forecasting import seasonal_forecast
from services.recurring import (
    emit_missed_payment_insights,
    tag_recurring_transactions,
    upsert_patterns,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PatternRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    median_amount: Decimal
    expected_day_of_month: Optional[int] = None
    cadence: str
    observed_count: int
    last_seen_on: str  # ISO date
    days_since_last_seen: int
    is_overdue: bool


class ForecastPreviewPoint(BaseModel):
    date: str  # ISO date
    forecast: Decimal
    day_of_month: int
    is_recurring_day: bool  # true if any pattern fires on this day-of-month


class LearningStatusOut(BaseModel):
    # Volume
    bank_txn_count: int
    vendor_count: int
    insight_count: int

    # Trained
    pattern_count: int
    tagged_txn_count: int
    auto_categorized_count: int

    # Insight breakdown by type
    anomaly_insight_count: int
    missed_payment_insight_count: int

    # Adaptive anomaly threshold
    adaptive_z_threshold: float
    coefficient_of_variation: float
    threshold_explanation: str

    # The detected patterns themselves
    patterns: list[PatternRowOut] = Field(default_factory=list)

    # 30-day forecast preview
    forecast_preview: list[ForecastPreviewPoint] = Field(default_factory=list)


class RetrainOut(BaseModel):
    new_patterns: int          # patterns now in the table (after retrain)
    newly_tagged_txns: int     # txns moved to is_recurring=True in this run
    missed_payment_insights: int  # missed-payment cards emitted this run
    auto_categorized: int      # bank_transactions.category auto-applied this run
    rehumanized_insights: int  # legacy jargon-y insight bodies rewritten
    ran_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _explain_threshold(z: float, cv: float) -> str:
    """Human-readable why-this-threshold."""
    if cv <= 0.5:
        return (
            f"Your spending is very regular (CV={cv:.2f}). "
            f"Threshold tightened to {z:.1f}σ to catch even small anomalies."
        )
    if cv <= 1.0:
        return (
            f"Typical SMB cash-flow variability (CV={cv:.2f}). "
            f"Using default {z:.1f}σ threshold."
        )
    if cv <= 2.0:
        return (
            f"Lumpy cash flow (CV={cv:.2f}). "
            f"Threshold raised to {z:.1f}σ so normal swings don't false-alarm."
        )
    return (
        f"Very lumpy cash flow (CV={cv:.2f}) — mix of small and very large txns. "
        f"Threshold raised to {z:.1f}σ; only true outliers will be flagged."
    )


def _compute_cv(db: Session, *, org_id: uuid.UUID) -> float:
    cutoff = date.today() - timedelta(days=365)
    amounts = list(db.scalars(
        select(BankTransaction.amount).where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= cutoff,
        )
    ).all())
    if len(amounts) < 2:
        return 0.0
    floats = [float(a) for a in amounts]
    mu = mean(floats)
    if mu <= 0:
        return 0.0
    sigma = stdev(floats)
    return sigma / mu


def _build_forecast_preview(
    db: Session, *, org_id: uuid.UUID
) -> list[ForecastPreviewPoint]:
    """30-day seasonal forecast preview, with a flag on each point indicating
    whether any recurring pattern expects a payment that day. This is what
    powers the "see your training in action" preview chart."""
    today = date.today()
    pts = seasonal_forecast(db, org_id=org_id, starting_from=today, horizon_days=30)
    pattern_days = set(
        d for d in db.scalars(
            select(RecurringPattern.expected_day_of_month).where(
                RecurringPattern.org_id == org_id,
                RecurringPattern.expected_day_of_month.isnot(None),
            )
        ).all()
        if d is not None
    )
    out: list[ForecastPreviewPoint] = []
    for p in pts:
        out.append(
            ForecastPreviewPoint(
                date=p.date.isoformat(),
                forecast=p.forecast,
                day_of_month=p.date.day,
                is_recurring_day=p.date.day in pattern_days,
            )
        )
    return out


def _build_pattern_rows(
    db: Session, *, org_id: uuid.UUID
) -> list[PatternRowOut]:
    today = date.today()
    rows = list(db.scalars(
        select(RecurringPattern)
        .where(RecurringPattern.org_id == org_id)
        .order_by(RecurringPattern.median_amount.desc())
    ).all())
    out: list[PatternRowOut] = []
    for r in rows:
        days_since = (today - r.last_seen_on).days
        is_overdue = False
        if r.cadence == "monthly" and r.expected_day_of_month is not None:
            # Same logic as services/recurring._expected_next_date
            target_day = max(1, min(28, r.expected_day_of_month))
            y, m = r.last_seen_on.year, r.last_seen_on.month + 1
            if m > 12:
                y, m = y + 1, 1
            try:
                expected_next = date(y, m, target_day)
            except ValueError:
                expected_next = date(y, m, 28)
            is_overdue = (today - expected_next).days >= 5
        out.append(
            PatternRowOut(
                label=r.label,
                median_amount=r.median_amount,
                expected_day_of_month=r.expected_day_of_month,
                cadence=r.cadence,
                observed_count=r.observed_count,
                last_seen_on=r.last_seen_on.isoformat(),
                days_since_last_seen=days_since,
                is_overdue=is_overdue,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=LearningStatusOut, summary="Learning status")
def learning_status(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> LearningStatusOut:
    """Everything the founder needs to verify the system is learning."""
    bank_txn_count = int(
        db.scalar(select(func.count()).select_from(BankTransaction).where(BankTransaction.org_id == org_id)) or 0
    )
    vendor_count = int(
        db.scalar(select(func.count()).select_from(Vendor).where(Vendor.org_id == org_id)) or 0
    )
    insight_count = int(
        db.scalar(select(func.count()).select_from(Insight).where(Insight.org_id == org_id)) or 0
    )
    pattern_count = int(
        db.scalar(select(func.count()).select_from(RecurringPattern).where(RecurringPattern.org_id == org_id)) or 0
    )
    tagged_txn_count = int(
        db.scalar(
            select(func.count()).select_from(BankTransaction).where(
                BankTransaction.org_id == org_id,
                BankTransaction.is_recurring.is_(True),
            )
        ) or 0
    )
    auto_categorized_count = int(
        db.scalar(
            select(func.count()).select_from(BankTransaction).where(
                BankTransaction.org_id == org_id,
                BankTransaction.auto_tagged_by.isnot(None),
            )
        ) or 0
    )
    anomaly_insight_count = int(
        db.scalar(
            select(func.count()).select_from(Insight).where(
                Insight.org_id == org_id,
                Insight.type == "vendor_amount_anomaly",
            )
        ) or 0
    )
    missed_payment_insight_count = int(
        db.scalar(
            select(func.count()).select_from(Insight).where(
                Insight.org_id == org_id,
                Insight.type == "recurring_payment_missed",
            )
        ) or 0
    )

    z = _adaptive_z_threshold(db, org_id=org_id)
    cv = _compute_cv(db, org_id=org_id)

    return LearningStatusOut(
        bank_txn_count=bank_txn_count,
        vendor_count=vendor_count,
        insight_count=insight_count,
        pattern_count=pattern_count,
        tagged_txn_count=tagged_txn_count,
        auto_categorized_count=auto_categorized_count,
        anomaly_insight_count=anomaly_insight_count,
        missed_payment_insight_count=missed_payment_insight_count,
        adaptive_z_threshold=float(z),
        coefficient_of_variation=float(cv),
        threshold_explanation=_explain_threshold(z, cv),
        patterns=_build_pattern_rows(db, org_id=org_id),
        forecast_preview=_build_forecast_preview(db, org_id=org_id),
    )


@router.post("/retrain", response_model=RetrainOut, summary="Retrain on existing data")
def retrain(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> RetrainOut:
    """Re-run the recurring detector + missed-payment detector + auto-category
    inheritance against the org's full bank-transaction history. Idempotent."""
    started = datetime.utcnow()
    logger.info("Retraining org %s...", org_id)

    # 1) Pattern detection
    patterns = upsert_patterns(db, org_id=org_id)

    # 2) Tag existing matching txns
    all_txns = list(db.scalars(select(BankTransaction).where(BankTransaction.org_id == org_id)))
    newly_tagged = tag_recurring_transactions(db, org_id=org_id, txns=all_txns)

    # 3) Auto-categorize from vendor defaults
    auto_cat = 0
    vendors_with_cat = db.scalars(
        select(Vendor).where(
            Vendor.org_id == org_id,
            Vendor.default_expense_category.isnot(None),
        )
    ).all()
    for v in vendors_with_cat:
        n = db.execute(
            text(
                """
                UPDATE bank_transactions
                SET category = :cat,
                    auto_tagged_by = 'vendor_default'
                WHERE org_id = :org
                  AND matched_vendor_id = :vid
                  AND category IS NULL
                """
            ),
            {"cat": v.default_expense_category, "org": str(org_id), "vid": str(v.id)},
        ).rowcount
        auto_cat += n

    # 4) Missed-payment insights
    missed = emit_missed_payment_insights(db, org_id=org_id)

    # 5) Rewrite legacy jargon-y anomaly insights with the humanized format.
    rehumanized = rehumanize_existing_insights(db, org_id=org_id)

    db.commit()
    logger.info(
        "Retrain org=%s complete: patterns=%d tagged=%d auto_cat=%d missed=%d rehumanized=%d",
        org_id, len(patterns), newly_tagged, auto_cat, missed, rehumanized,
    )

    return RetrainOut(
        new_patterns=len(patterns),
        newly_tagged_txns=newly_tagged,
        missed_payment_insights=missed,
        auto_categorized=auto_cat,
        rehumanized_insights=rehumanized,
        ran_at=started,
    )
