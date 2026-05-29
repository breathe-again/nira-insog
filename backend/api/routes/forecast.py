"""Cash forecast endpoints.

  POST /api/forecast/cash/run     — generate a fresh forecast
  GET  /api/forecast/cash         — latest forecast (or null) for current org
  GET  /api/forecast/cash/drivers — driver list for "Why this forecast?" panel

All read-only routes return the most recent run for the caller's org;
the POST creates a new run.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from services import cash_forecast

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CashForecastPointOut(BaseModel):
    date: str
    days_from_now: int
    pessimistic: str
    likely: str
    optimistic: str
    inflow: str
    outflow: str
    actual: Optional[str] = None


class CashForecastOut(BaseModel):
    run_id: str
    as_of_date: str
    horizon_days: int
    starting_cash_inr: str
    ending_cash_likely_inr: str
    ending_cash_pessimistic_inr: str
    ending_cash_optimistic_inr: str
    runway_zero_date: Optional[str] = None
    drivers_count: int
    inflows_total_inr: str
    outflows_total_inr: str
    created_at: str
    # Weekly buckets for the chart — Mon-Sun rolling.
    # The full daily points come as a separate list for callers that want them.
    points: list[CashForecastPointOut]


class ForecastDriverOut(BaseModel):
    id: str
    kind: str
    label: str
    direction: str
    expected_date: Optional[str] = None
    expected_amount_inr: str
    confidence: str
    source_kind: str
    vendor_id: Optional[str] = None
    client_id: Optional[str] = None
    supporting_data: Optional[dict] = None


# ---------------------------------------------------------------------------
# POST — generate a fresh run
# ---------------------------------------------------------------------------


@router.post(
    "/cash/run",
    response_model=CashForecastOut,
    summary="Generate a fresh 13-week cash forecast for the current org",
)
def run_cash_forecast(
    horizon_days: int = Query(default=91, ge=7, le=365),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> CashForecastOut:
    """Trigger a new forecast generation. Synchronous; the calculation is
    small enough (a few hundred rows for typical mid-market accounts) to
    run inline. Returns the freshly-generated run."""
    run = cash_forecast.generate_forecast(
        db, org_id=org_id, horizon_days=horizon_days, trigger="manual"
    )
    summary = cash_forecast.get_latest_forecast(db, org_id=org_id)
    if summary is None:
        # Shouldn't happen — we literally just generated it.
        raise HTTPException(status_code=500, detail="Forecast not retrievable after generation")
    return _summary_to_out(summary)


# ---------------------------------------------------------------------------
# GET — latest forecast
# ---------------------------------------------------------------------------


@router.get(
    "/cash",
    response_model=Optional[CashForecastOut],
    summary="Latest cash forecast for the current org (null if never run)",
)
def get_cash_forecast(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> Optional[CashForecastOut]:
    summary = cash_forecast.get_latest_forecast(db, org_id=org_id)
    if summary is None:
        return None
    return _summary_to_out(summary)


# ---------------------------------------------------------------------------
# GET — drivers for the latest run
# ---------------------------------------------------------------------------


@router.get(
    "/cash/drivers",
    response_model=list[ForecastDriverOut],
    summary="Drivers (inflows + outflows) for the most recent forecast run",
)
def get_forecast_drivers(
    kind: Optional[
        Literal[
            "recurring_inflow", "recurring_outflow",
            "open_receivable", "open_payable",
            "scheduled_tax", "opening_balance", "one_off",
        ]
    ] = Query(default=None),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> list[ForecastDriverOut]:
    summary = cash_forecast.get_latest_forecast(db, org_id=org_id)
    if summary is None:
        return []
    drivers = cash_forecast.get_drivers(db, run_id=summary.run_id, org_id=org_id)
    if kind:
        drivers = [d for d in drivers if d["kind"] == kind]
    return [ForecastDriverOut(**d) for d in drivers]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _summary_to_out(summary: cash_forecast.ForecastSummary) -> CashForecastOut:
    return CashForecastOut(
        run_id=str(summary.run_id),
        as_of_date=summary.as_of_date.isoformat(),
        horizon_days=summary.horizon_days,
        starting_cash_inr=str(summary.starting_cash_inr),
        ending_cash_likely_inr=str(summary.ending_cash_likely_inr),
        ending_cash_pessimistic_inr=str(summary.ending_cash_pessimistic_inr),
        ending_cash_optimistic_inr=str(summary.ending_cash_optimistic_inr),
        runway_zero_date=summary.runway_zero_date.isoformat() if summary.runway_zero_date else None,
        drivers_count=summary.drivers_count,
        inflows_total_inr=str(summary.inflows_total_inr),
        outflows_total_inr=str(summary.outflows_total_inr),
        created_at=summary.created_at.isoformat(),
        points=[CashForecastPointOut(**p) for p in summary.points],
    )
