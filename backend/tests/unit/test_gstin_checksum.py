"""Regression tests for the GSTIN checksum algorithm.

Fixture GSTINs are REAL production identifiers pulled from the live
Tax → GSTIN Health page on Quantta's prod. Every one of these is a
publicly verifiable GSTIN that resolves to the named entity on the
GST portal.

Production incident — DO NOT regress: a prior bug toggled the
position-dependent `factor` before using it, inverting the expected
1,2,1,2,… sequence to 2,1,2,1,… Every GSTIN in Quantta's vendor list
(15+ checks) was reported as "checksum mismatch" until this was
caught by a real user looking at the GSTIN Health page.

If these tests fail, the algorithm is broken and prod will lie about
every vendor's GST status. The CI signal must catch this BEFORE the
deploy hits any customer.
"""
from __future__ import annotations

import pytest

from services.tax.gstin import _gstin_checksum, validate_gstin


# (gstin, expected_state, label) — drawn from real Quantta vendors.
REAL_GSTINS = [
    ("07AAJCA9880A1ZL", "Delhi", "Amazon Web Services India Private Limited"),
    ("06AABCF5150G1ZZ", "Haryana", "Facebook India Online Services Pvt. Ltd."),
    ("06AACCG0527D1Z8", "Haryana", "Google India Private Limited"),
    ("09AAACI1838D1ZU", "Uttar Pradesh", "Info Edge (India) Ltd"),
    ("19AADFJ9827D1Z6", "West Bengal", "JBS & Company"),
    ("19AANCS6391N1ZB", "West Bengal", "Quantta Analytics Private Limited"),
    ("19AAFFR7366B1Z2", "West Bengal", "R Kothari & Co LLP"),
    ("19ABFFS0915E1ZC", "West Bengal", "S.R & Associates"),
    ("06AAQCS2971P1ZH", "Haryana", "Scholiverse Educare Private Limited"),
    ("19DSWPS3663C1Z1", "West Bengal", "SMR IT Solutions"),
    ("07ACIFS0257R1ZI", "Delhi", "State Express"),
    ("07ABECS5030Q1ZZ", "Delhi", "Sushant Travels Private Limited"),
    ("27OANPS3547F1ZY", "Maharashtra", "Travel Line"),
    ("33AAACZ4322M2Z9", "Tamil Nadu", "Zoho Corporation Private Limited"),
]


@pytest.mark.parametrize("gstin,_state,_label", REAL_GSTINS)
def test_checksum_matches_real_gstin(gstin, _state, _label):
    """The computed check character must equal the actual GSTIN's last char."""
    assert _gstin_checksum(gstin[:14]) == gstin[14], (
        f"Checksum mismatch on real GSTIN {gstin!r}: "
        f"computed {_gstin_checksum(gstin[:14])!r} vs expected {gstin[14]!r}"
    )


@pytest.mark.parametrize("gstin,state,_label", REAL_GSTINS)
def test_validate_gstin_accepts_real(gstin, state, _label):
    """End-to-end validation must return is_valid=True for real GSTINs."""
    result = validate_gstin(gstin)
    assert result.is_valid, f"{gstin} ({_label}) rejected: {result.reason}"
    assert result.state_name == state


def test_invalid_checksum_is_rejected():
    """Flip the last char of a real GSTIN — must be rejected."""
    real = "07AAJCA9880A1ZL"  # Amazon — checksum is 'L'
    bad = real[:14] + "A"      # force a wrong check char
    r = validate_gstin(bad)
    assert not r.is_valid
    assert "checksum" in (r.reason or "")


def test_empty_input_is_missing_not_invalid():
    """Empty / None should return is_valid=False with reason='missing'."""
    for empty in (None, "", "   "):
        r = validate_gstin(empty)
        assert not r.is_valid
        assert r.reason == "missing"


def test_wrong_length_caught_before_checksum():
    r = validate_gstin("07AAJCA9880A1Z")  # 14 chars, missing check
    assert not r.is_valid
    assert "length" in (r.reason or "").lower()


def test_format_check_catches_garbled_input():
    r = validate_gstin("ABCDEFGHIJKLMNO")  # right length, wrong shape
    assert not r.is_valid
    assert "format" in (r.reason or "").lower()


def test_unknown_state_code_rejected():
    """A valid-looking format with an impossible state code should fail."""
    # Build a 15-char string with state code "99" (which is "Centre" — valid)
    # vs "50" which is not in the known list.
    # Need a GSTIN that passes the regex but has an unknown state.
    candidate = "50AAAAA0000A1Z0"
    r = validate_gstin(candidate)
    assert not r.is_valid
    assert "state" in (r.reason or "").lower()
