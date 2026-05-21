"""Unit tests for anomalies.evaluate_amount — pure stats, no DB."""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.anomalies import (
    ABSOLUTE_FLOOR,
    MIN_HISTORY,
    SEVERITY_URGENT_Z,
    Z_THRESHOLD,
    evaluate_amount,
)


def _hist(*xs: float) -> list[Decimal]:
    return [Decimal(str(x)) for x in xs]


# ---------------------------------------------------------------------------
# No flag cases
# ---------------------------------------------------------------------------


def test_no_flag_when_history_too_short():
    verdict = evaluate_amount(Decimal("1000000"), _hist(100, 100, 100))
    assert verdict.flagged is False
    assert verdict.sample_size == 3


def test_no_flag_when_amount_below_floor():
    """A ₹50 outlier on a stable ₹100 vendor is below the noise floor."""
    history = _hist(100, 100, 100, 100, 100, 100)
    # Floor is ABSOLUTE_FLOOR (₹500) — sub-floor amount must not flag.
    verdict = evaluate_amount(ABSOLUTE_FLOOR - Decimal("1"), history)
    assert verdict.flagged is False


def test_no_flag_when_within_threshold():
    """Mean 100, stddev ~0 — exactly the same amount should NOT flag."""
    history = _hist(1000, 1000, 1000, 1000, 1000, 1000)
    verdict = evaluate_amount(Decimal("1000"), history)
    assert verdict.flagged is False


# ---------------------------------------------------------------------------
# Flag cases
# ---------------------------------------------------------------------------


def test_flags_classic_2_sigma_spike():
    """Mean ~10000, stddev ~500 — a 20000 amount is ~20σ above and flags urgent.

    Amounts must be above ABSOLUTE_FLOOR (₹500) — using ~10k values for clarity.
    """
    history = _hist(10000, 9500, 10500, 10000, 10000, 10200, 9800)
    verdict = evaluate_amount(Decimal("20000"), history)
    assert verdict.flagged is True
    assert verdict.severity == "urgent"
    assert verdict.z_score > Z_THRESHOLD


def test_severity_attention_for_borderline_2_to_4_sigma():
    """Construct a history where amount is ~2.5σ above mean."""
    # mean=1000, stddev computed from this set
    history = _hist(900, 950, 1000, 1050, 1100, 1000, 1000)
    # amount such that (amount - mean) / stddev is around 2.5
    import statistics

    mu = statistics.mean(float(x) for x in history)
    sigma = statistics.stdev(float(x) for x in history)
    amount = Decimal(str(mu + 2.5 * sigma))
    verdict = evaluate_amount(amount, history)
    assert verdict.flagged is True
    assert verdict.severity == "attention"
    assert Z_THRESHOLD < verdict.z_score < SEVERITY_URGENT_Z


def test_zero_variance_history_flags_on_50pct_jump():
    """Vendor that always charges exactly ₹1000 — anything ≥ ₹1500 is suspect."""
    history = _hist(1000, 1000, 1000, 1000, 1000, 1000)
    verdict = evaluate_amount(Decimal("1500"), history)
    assert verdict.flagged is True
    assert verdict.severity == "urgent"  # z=99 sentinel triggers urgent


def test_zero_variance_history_does_not_flag_small_change():
    """Same vendor — a small bump ≤ 50% should not flag."""
    history = _hist(1000, 1000, 1000, 1000, 1000, 1000)
    verdict = evaluate_amount(Decimal("1100"), history)
    assert verdict.flagged is False


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_constants_are_reasonable():
    assert MIN_HISTORY >= 3
    assert Z_THRESHOLD >= 1.5
    assert SEVERITY_URGENT_Z > Z_THRESHOLD
    assert ABSOLUTE_FLOOR > 0


# ---------------------------------------------------------------------------
# Parametric edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount,history,should_flag",
    [
        # Big outflow against a tight history — flag.
        (Decimal("50000"), _hist(1000, 1100, 950, 1050, 1000, 1020), True),
        # In-line amount — no flag (also: above floor).
        (Decimal("1000"), _hist(1000, 1100, 950, 1050, 1000, 1020), False),
        # Just barely above — close to threshold but not over.
        (Decimal("1100"), _hist(1000, 1100, 950, 1050, 1000, 1020), False),
        # Below ABSOLUTE_FLOOR — never flag.
        (Decimal("100"), _hist(10, 12, 11, 9, 10, 8), False),
    ],
)
def test_parametric_flag_decisions(amount, history, should_flag):
    verdict = evaluate_amount(amount, history)
    assert verdict.flagged is should_flag
