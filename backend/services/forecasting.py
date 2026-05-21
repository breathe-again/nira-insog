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

    # Step 1: aggregate per-day net (credits minus debits) — one observation
    # per CALENDAR DAY, not per transaction. Without this, a day with 10
    # small debits would skew mean(history) by appearing as 10 separate
    # data points; a day with 1 large outflow would barely register.
    per_day_net: dict[date, float] = {}
    for txn_date, amount, direction in rows:
        a = float(amount)
        signed = a if direction == "credit" else -a
        per_day_net[txn_date] = per_day_net.get(txn_date, 0.0) + signed

    # Step 2: bucket those per-day nets by day-of-month so seasonality is
    # learned at the right granularity.
    by_dom: dict[int, list[float]] = {}
    for d, net in per_day_net.items():
        by_dom.setdefault(d.day, []).append(net)

    # Fallback: average across all observed days (not all calendar days in
    # the window — days with zero activity shouldn't dilute the mean).
    fallback_daily = (
        sum(per_day_net.values()) / max(1, len(per_day_net))
        if per_day_net
        else 0.0
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
