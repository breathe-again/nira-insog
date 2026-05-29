"""Unit tests for the cash forecast engine.

Covers the pure-Python pieces: driver scenario shifts, bucket aggregation,
month-walk math. DB-dependent integration tests run against the live
docker stack in /tests (not /tests/unit).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from services.cash_forecast import (
    DriverDraft,
    PAYABLE_STRETCH_OPTIMISTIC,
    RECEIVABLE_LATENESS_OPTIMISTIC,
    RECEIVABLE_LATENESS_PESSIMISTIC,
    RECURRING_SCENARIO_AMOUNT_SPREAD,
    _add_month,
    _bucket_drivers_by_day,
)


# -----------------------------------------------------------------------------
# _add_month
# -----------------------------------------------------------------------------


def test_add_month_normal():
    assert _add_month(date(2026, 3, 15)) == date(2026, 4, 15)


def test_add_month_year_rollover():
    assert _add_month(date(2026, 12, 5)) == date(2027, 1, 5)


def test_add_month_31_to_short_month():
    # 31-Jan → 28-Feb (non-leap)
    assert _add_month(date(2025, 1, 31)) == date(2025, 2, 28)
    # 31-Jan → 29-Feb (leap)
    assert _add_month(date(2024, 1, 31)) == date(2024, 2, 29)


def test_add_month_30_to_feb():
    # 30-Jan → 28-Feb
    assert _add_month(date(2025, 1, 30)) == date(2025, 2, 28)


def test_add_month_31_to_april():
    assert _add_month(date(2026, 3, 31)) == date(2026, 4, 30)


# -----------------------------------------------------------------------------
# DriverDraft.date_for_scenario
# -----------------------------------------------------------------------------


def _mk(kind, direction="outflow", amount=1000, days=10, confidence=0.7):
    return DriverDraft(
        kind=kind,
        label=kind.replace("_", " ").title(),
        expected_date=date(2026, 6, 1) + timedelta(days=days),
        expected_amount_inr=Decimal(amount),
        direction=direction,
        confidence=Decimal(str(confidence)),
        source_kind="test",
    )


def test_receivable_pessimistic_pushes_date_later():
    # Not-yet-overdue invoice — should shift later in pessimistic.
    d = _mk("open_receivable", direction="inflow", days=0)
    d.supporting_data = {"days_overdue": 0}
    assert d.date_for_scenario("pessimistic") == d.expected_date + timedelta(
        days=RECEIVABLE_LATENESS_PESSIMISTIC
    )


def test_receivable_optimistic_pulls_date_earlier():
    d = _mk("open_receivable", direction="inflow", days=20)
    d.supporting_data = {"days_overdue": 0}
    assert d.date_for_scenario("optimistic") == d.expected_date + timedelta(
        days=RECEIVABLE_LATENESS_OPTIMISTIC
    )


def test_payable_optimistic_stretches_date():
    d = _mk("open_payable")
    d.supporting_data = {"days_overdue": 0}
    assert d.date_for_scenario("optimistic") == d.expected_date + timedelta(
        days=PAYABLE_STRETCH_OPTIMISTIC
    )


def test_overdue_receivable_does_not_shift_in_optimistic():
    """Regression: overdue invoices used to cluster at day 0 in optimistic
    because we'd shift them earlier even though they were already late.
    Fix: don't shift overdue invoices in either direction."""
    d = _mk("open_receivable", direction="inflow", days=10)
    d.supporting_data = {"days_overdue": 30}  # 30 days late
    assert d.date_for_scenario("optimistic") == d.expected_date
    assert d.date_for_scenario("pessimistic") == d.expected_date
    assert d.date_for_scenario("likely") == d.expected_date


def test_recurring_does_not_shift_date():
    d = _mk("recurring_outflow")
    for scenario in ("pessimistic", "likely", "optimistic"):
        assert d.date_for_scenario(scenario) == d.expected_date


def test_scheduled_tax_does_not_shift_date():
    d = _mk("scheduled_tax")
    for scenario in ("pessimistic", "likely", "optimistic"):
        assert d.date_for_scenario(scenario) == d.expected_date


# -----------------------------------------------------------------------------
# DriverDraft.amount_for_scenario
# -----------------------------------------------------------------------------


def test_recurring_outflow_pessimistic_higher_amount():
    """Pessimistic: outflows up. So a recurring outflow's amount grows."""
    d = _mk("recurring_outflow", amount=10000, confidence=0.5)
    pess = d.amount_for_scenario("pessimistic")
    likely = d.amount_for_scenario("likely")
    opt = d.amount_for_scenario("optimistic")
    assert pess > likely == d.expected_amount_inr > opt


def test_recurring_inflow_pessimistic_lower_amount():
    """Pessimistic: inflows down. So a recurring inflow's amount shrinks."""
    d = _mk("recurring_inflow", direction="inflow", amount=10000, confidence=0.5)
    pess = d.amount_for_scenario("pessimistic")
    likely = d.amount_for_scenario("likely")
    opt = d.amount_for_scenario("optimistic")
    assert pess < likely == d.expected_amount_inr < opt


def test_high_confidence_recurring_amount_barely_moves():
    """A 0.95-confidence recurring driver should barely swing between scenarios."""
    d = _mk("recurring_outflow", amount=10000, confidence=0.95)
    pess = d.amount_for_scenario("pessimistic")
    opt = d.amount_for_scenario("optimistic")
    # Swing < 2% with confidence 0.95 and 15% spread
    swing = abs(pess - opt) / d.expected_amount_inr
    assert swing < Decimal("0.02"), f"swing was {swing}"


def test_low_confidence_recurring_amount_swings_wider():
    """A 0.4-confidence recurring driver should swing meaningfully."""
    d = _mk("recurring_outflow", amount=10000, confidence=0.4)
    pess = d.amount_for_scenario("pessimistic")
    opt = d.amount_for_scenario("optimistic")
    swing = abs(pess - opt) / d.expected_amount_inr
    # Should be > 15% with confidence 0.4 (because (1-0.4) * 0.15 = 9%, doubled = 18%)
    assert swing > Decimal("0.15"), f"swing was {swing}"


def test_open_receivable_amount_does_not_swing_only_date():
    """Open AR drivers shift only their DATE, not amount. The customer
    owes ₹X — we move when they pay, not how much."""
    d = _mk("open_receivable", direction="inflow", amount=85000)
    for scenario in ("pessimistic", "likely", "optimistic"):
        assert d.amount_for_scenario(scenario) == d.expected_amount_inr


# -----------------------------------------------------------------------------
# _bucket_drivers_by_day
# -----------------------------------------------------------------------------


def test_bucket_aggregates_same_day():
    today = date(2026, 6, 1)
    drafts = [
        DriverDraft(
            kind="recurring_outflow",
            label="Rent",
            expected_date=date(2026, 6, 5),
            expected_amount_inr=Decimal("50000"),
            direction="outflow",
            confidence=Decimal("0.9"),
            source_kind="recurring_pattern",
        ),
        DriverDraft(
            kind="recurring_outflow",
            label="Internet",
            expected_date=date(2026, 6, 5),
            expected_amount_inr=Decimal("2000"),
            direction="outflow",
            confidence=Decimal("0.9"),
            source_kind="recurring_pattern",
        ),
        DriverDraft(
            kind="recurring_inflow",
            label="Customer subscription",
            expected_date=date(2026, 6, 5),
            expected_amount_inr=Decimal("75000"),
            direction="inflow",
            confidence=Decimal("0.9"),
            source_kind="recurring_pattern",
        ),
    ]
    buckets = _bucket_drivers_by_day(drafts, today, 91, "likely")
    day = date(2026, 6, 5)
    assert day in buckets
    inflow, outflow = buckets[day]
    assert inflow == Decimal("75000")
    assert outflow == Decimal("52000")


def test_bucket_clips_outside_horizon():
    today = date(2026, 6, 1)
    drafts = [
        DriverDraft(
            kind="recurring_outflow",
            label="Far-future",
            expected_date=date(2027, 1, 1),  # well past 91-day horizon
            expected_amount_inr=Decimal("100000"),
            direction="outflow",
            confidence=Decimal("0.9"),
            source_kind="recurring_pattern",
        ),
    ]
    buckets = _bucket_drivers_by_day(drafts, today, 91, "likely")
    assert buckets == {}


def test_bucket_clips_past_today():
    today = date(2026, 6, 1)
    drafts = [
        DriverDraft(
            kind="recurring_outflow",
            label="Past",
            expected_date=date(2026, 5, 25),  # past
            expected_amount_inr=Decimal("100000"),
            direction="outflow",
            confidence=Decimal("0.9"),
            source_kind="recurring_pattern",
        ),
    ]
    buckets = _bucket_drivers_by_day(drafts, today, 91, "likely")
    assert buckets == {}


def test_bucket_scenarios_differ_for_receivables():
    """Pessimistic scenario should push AR to a later day than optimistic.

    Since AR now smooths across a 5-day window, we compare the CENTRE of
    each scenario's distribution (max-amount day) rather than the only day.
    """
    today = date(2026, 6, 1)
    drafts = [
        DriverDraft(
            kind="open_receivable",
            label="Invoice X",
            expected_date=date(2026, 6, 15),
            expected_amount_inr=Decimal("85000"),
            direction="inflow",
            confidence=Decimal("0.85"),
            source_kind="invoice",
            supporting_data={"days_overdue": 0},
        ),
    ]
    pess = _bucket_drivers_by_day(drafts, today, 91, "pessimistic")
    likely = _bucket_drivers_by_day(drafts, today, 91, "likely")
    opt = _bucket_drivers_by_day(drafts, today, 91, "optimistic")

    def peak_day(buckets: dict) -> date:
        return max(buckets.keys(), key=lambda d: buckets[d][0] + buckets[d][1])

    assert peak_day(pess) > peak_day(likely) > peak_day(opt)


def test_smoothed_receivable_spreads_across_window():
    """A receivable should distribute across 5 days, not land on 1."""
    today = date(2026, 6, 1)
    drafts = [
        DriverDraft(
            kind="open_receivable",
            label="Big invoice",
            expected_date=date(2026, 6, 15),
            expected_amount_inr=Decimal("100000"),
            direction="inflow",
            confidence=Decimal("0.85"),
            source_kind="invoice",
            supporting_data={"days_overdue": 0},
        ),
    ]
    buckets = _bucket_drivers_by_day(drafts, today, 91, "likely")
    # Should occupy 5 consecutive days (10-14 around the centered day 12)
    assert len(buckets) == 5
    # Total inflow should still sum to the original amount (within rounding)
    total_inflow = sum(b[0] for b in buckets.values())
    assert abs(total_inflow - Decimal("100000")) < Decimal("0.01")
    # Centre day should have the largest share (weight 3 / total 9)
    centre_inflow = buckets[date(2026, 6, 15)][0]
    assert centre_inflow == Decimal("100000") * Decimal(3) / Decimal(9)


# -----------------------------------------------------------------------------
# Spread constants are sane
# -----------------------------------------------------------------------------


def test_constants_are_in_sane_ranges():
    # If anyone changes these to insane values we want a test to fail.
    assert 7 <= RECEIVABLE_LATENESS_PESSIMISTIC <= 60
    assert -30 <= RECEIVABLE_LATENESS_OPTIMISTIC <= 0
    assert 0 <= PAYABLE_STRETCH_OPTIMISTIC <= 45
    assert Decimal("0.05") <= RECURRING_SCENARIO_AMOUNT_SPREAD <= Decimal("0.30")
