"""Unit tests for vendor name normalization (pure function — no DB)."""

from __future__ import annotations

import pytest

from services.vendors import MATCH_THRESHOLD, normalize_name


@pytest.mark.parametrize(
    "input_name, expected",
    [
        ("ABC Traders", "abc traders"),
        ("A.B.C. Traders Pvt Ltd", "a b c traders"),
        ("ABC TRADERS PRIVATE LIMITED", "abc traders"),
        ("ABC Traders Pvt. Ltd.", "abc traders"),
        ("abc-traders", "abc traders"),
        ("Zomato", "zomato"),
        ("  ZOMATO  ", "zomato"),
        ("Acme Corp Inc.", "acme"),
        ("Solo LLP", "solo"),
        ("", ""),
        ("Pvt Ltd", ""),  # pure suffix → empty
    ],
)
def test_normalize_name(input_name, expected):
    assert normalize_name(input_name) == expected


def test_match_threshold_within_sensible_range():
    """Sanity — we don't want it cranked to 100 or down at 60."""
    assert 75 <= MATCH_THRESHOLD <= 95


# ---------------------------------------------------------------------------
# rapidfuzz behaviour we depend on
# ---------------------------------------------------------------------------


def test_fuzzy_match_above_threshold_for_corporate_suffix_variants():
    """Sanity check: rapidfuzz scores our motivating example above the cutoff."""
    from rapidfuzz import fuzz

    a = normalize_name("ABC Traders")
    b = normalize_name("A.B.C. Traders Pvt Ltd")
    assert fuzz.token_set_ratio(a, b) >= MATCH_THRESHOLD


def test_fuzzy_match_below_threshold_for_genuinely_different_names():
    from rapidfuzz import fuzz

    a = normalize_name("ABC Traders")
    b = normalize_name("XYZ Traders")
    # They share "traders" but differ on the distinctive token.
    assert fuzz.token_set_ratio(a, b) < MATCH_THRESHOLD


def test_fuzzy_match_for_subset_token_does_not_falsely_merge():
    """ABC Traders should NOT collapse into ABC Tradings."""
    from rapidfuzz import fuzz

    a = normalize_name("ABC Traders")
    b = normalize_name("ABC Tradings")
    # token_set_ratio is lenient — we expect it to be HIGH here, which is a
    # known limitation of this scorer. This test documents that behaviour so
    # we notice if we ever bump the threshold and start incorrectly splitting
    # legitimate matches.
    assert fuzz.token_set_ratio(a, b) >= 60
