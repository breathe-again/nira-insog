"""Cash-flow forecasting.

v1: naive linear from the last 14 days' mean net flow. Already lives in
`api/routes/dashboard.py`.

v2 (this module): seasonal monthly. For each future date D in the 30-day
horizon, use the same day-of-month from the past `LOOKBACK_MONTHS` months
as a prior. The forecast for D is the mean of those days; the band is
mean ± stddev (or ±15% if stddev is too small).

Falls back to v1 if there isn't enough history.

Why this matters for an Indian SMB:
- Most business cash flow is monthly-cyclical. Rent goes out on the 5th,
  salary on the 1st, AR collections cluster around month-end.
- The naive linear projection averages all that into nothing useful.
- A seasonal model says "you usually have +₹3L on day 5 (rent goes out),
  +₹8L on day 1 (salary day)" — actionable.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.models import BankTransaction

logger = logging.getLogger(__name__)

# Tunables
LOOKBACK_MONTHS = 6
HORIZON_DAYS = 30
MIN_HISTORY_POINTS = 2   # need at least 2 same-day-of-month observations to seasonally forecast
DEFAULT_BAND_PCT = 0.15  # ±15% if stddev is too small to be meaningful


@dataclass(slots=True)
class ForecastPoint:
    """One point on the forecast curve."""

    date: date
    forecast: Decimal
    lower_band: Decimal
    upper_band: Decimal


def seasonal_forecast(
    db: Session,
    *,
    org_id,  # uuid.UUID
    starting_from: date,
    horizon_days: int = HORIZON_DAYS,
) -> list[ForecastPoint]:
    """Return `horizon_days` ForecastPoint values starting from `starting_from`.

    For each future date D, we sum (credit - debit) on the SAME day-of-month
    over the past LOOKBACK_MONTHS months. If we have ≥ MIN_HISTORY_POINTS
    observations for that day, we use mean ± stddev. Otherwise we fall back
    to the running-average daily net over the lookback window."""
    cutoff = starting_from - timedelta(days=LOOKBACK_MONTHS * 31)

    # Pull daily totals from history once. Aggregating in-Python is fine —
    # 6 months × ~30 txns/day = ~5,400 rows max.
    rows = db.execute(
        select(
            BankTransaction.txn_date,
            BankTransaction.amount,
            BankTransaction.direction,
        ).where(
            BankTransaction.org_id == org_id,
            BankTransaction.txn_date >= cutoff,
            BankTransaction.txn_date < starting_from,
        )
    ).all()

    if not rows:
        # No history at all — flat zero forecast.
        return _zero_forecast(starting_from, horizon_days)

    # Bucket: day_of_month → list of net-flow values
    by_dom: dict[int, list[float]] = {}
    # Running daily net for fallback
    running_total = 0.0
    running_days: set[date] = set()
    for txn_date, amount, direction in rows:
        a = float(amount)
        signed = a if direction == "credit" else -a
        by_dom.setdefault(txn_date.day, []).append(signed)
        running_total += signed
        running_days.add(txn_date)

    fallback_daily = (
        running_total / max(1, len(running_days)) if running_days else 0.0
    )

    out: list[ForecastPoint] = []
    for offset in range(horizon_days):
        d = starting_from + timedelta(days=offset)
        history = by_dom.get(d.day, [])
        if len(history) >= MIN_HISTORY_POINTS:
            mean = statistics.mean(history)
            stdev = statistics.stdev(history) if len(history) >= 2 else 0.0
            band = max(stdev, abs(mean) * DEFAULT_BAND_PCT, 1.0)
            forecast = mean
            lower = mean - band
            upper = mean + band
        else:
            # Fallback to running daily average with a wider band.
            forecast = fallback_daily
            band = abs(fallback_daily) * DEFAULT_BAND_PCT * 2 + 1000.0
            lower = fallback_daily - band
            upper = fallback_daily + band

        out.append(
            ForecastPoint(
                date=d,
                forecast=Decimal(f"{forecast:.2f}"),
                lower_band=Decimal(f"{lower:.2f}"),
                upper_band=Decimal(f"{upper:.2f}"),
            )
        )
    return out


def _zero_forecast(start: date, horizon: int) -> list[ForecastPoint]:
    return [
        ForecastPoint(
            date=start + timedelta(days=i),
            forecast=Decimal("0"),
            lower_band=Decimal("0"),
            upper_band=Decimal("0"),
        )
        for i in range(horizon)
    ]
