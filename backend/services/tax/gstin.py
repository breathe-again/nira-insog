"""GSTIN format + checksum validation.

A GSTIN is a 15-character identifier:

  Position  | Meaning
  ----------+-----------------------------------------------------------------
  1-2       | State code (01-37 + a few special codes 96/97/99)
  3-12      | PAN of the entity (10 chars, e.g. ABCDE1234F)
  13        | Entity number for the PAN within the state (1-9, A-Z)
  14        | Default 'Z'
  15        | Checksum (computed from the first 14 chars; base-36)

The checksum follows the GSTN-published algorithm — values are remapped to
base 36 (0-9, A-Z), each position multiplies by 1 or 2 (Luhn-like), digits
of the product are added together, and the final check is `(36 - sum % 36) % 36`.

State code reference: https://www.gst.gov.in/help/helpmodules/registration
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Canonical Indian GST state codes. 35 = AN (Andaman), 38 = Ladakh (added 2020).
_STATE_CODES: dict[str, str] = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman & Diu",   # deprecated after 2020 merger w/ DNH
    "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (old)",  # deprecated post bifurcation
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre",  # used by Centre GST
}


_GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9A-Z]{1}Z[0-9A-Z]{1}$"
)


# Base-36 character set used by the GSTIN checksum algorithm.
_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class GSTINValidation:
    """Result of validating a candidate GSTIN string."""

    raw: str
    is_valid: bool
    reason: Optional[str] = None  # human-readable when invalid
    state_code: Optional[str] = None
    state_name: Optional[str] = None
    pan: Optional[str] = None     # extracted PAN if format checks pass

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "is_valid": self.is_valid,
            "reason": self.reason,
            "state_code": self.state_code,
            "state_name": self.state_name,
            "pan": self.pan,
        }


def _gstin_checksum(first14: str) -> str:
    """Compute the GSTIN check character from the first 14 chars.

    Algorithm (per GSTN documentation):
      1. Convert each char to its base-36 value.
      2. Multiply by a position-dependent factor: factor = 1 if position is
         odd (1-indexed), else 2. Equivalently: factor toggles 1,2,1,2,...
      3. If the product ≥ 36, sum its digits in base-36 (i.e. divmod by 36
         and add quotient + remainder).
      4. Sum all 14 contributions modulo 36.
      5. Check character = (36 - sum) % 36, mapped back via _CHARSET.

    Reference implementation matches what the GST portal uses.
    """
    total = 0
    factor = 1
    for ch in first14:
        if ch not in _CHARSET:
            raise ValueError(f"invalid character in GSTIN: {ch!r}")
        # Toggle factor BEFORE using it, so position 1 → factor 2, position 2 → factor 1.
        # GSTN spec actually starts with factor=2 at position 1; positions are 1-indexed.
        factor = 2 if factor == 1 else 1
        val = _CHARSET.index(ch) * factor
        # Sum digits in base 36 if val ≥ 36.
        if val >= 36:
            val = (val // 36) + (val % 36)
        total += val
    check_val = (36 - (total % 36)) % 36
    return _CHARSET[check_val]


def validate_gstin(candidate: Optional[str]) -> GSTINValidation:
    """Validate a GSTIN string. Returns a structured result — `is_valid=True`
    only when format AND checksum AND state code all check out.

    Empty input returns is_valid=False with reason='missing'. Callers
    typically distinguish missing-GSTIN vendors from invalid-GSTIN vendors
    so they can prompt for entry vs flag for correction.
    """
    if candidate is None or not candidate.strip():
        return GSTINValidation(raw="", is_valid=False, reason="missing")
    raw = candidate.strip().upper()

    if len(raw) != 15:
        return GSTINValidation(raw=raw, is_valid=False, reason=f"length {len(raw)} (must be 15)")
    if not _GSTIN_RE.match(raw):
        return GSTINValidation(
            raw=raw,
            is_valid=False,
            reason="format mismatch (expected 2-digit state + 10-char PAN + entity + Z + check)",
        )

    state_code = raw[:2]
    state_name = _STATE_CODES.get(state_code)
    if state_name is None:
        return GSTINValidation(
            raw=raw,
            is_valid=False,
            reason=f"unknown state code {state_code!r}",
            state_code=state_code,
        )
    pan = raw[2:12]

    # Verify checksum.
    expected = _gstin_checksum(raw[:14])
    if expected != raw[14]:
        return GSTINValidation(
            raw=raw,
            is_valid=False,
            reason=f"checksum mismatch (expected {expected!r}, got {raw[14]!r})",
            state_code=state_code,
            state_name=state_name,
            pan=pan,
        )

    return GSTINValidation(
        raw=raw,
        is_valid=True,
        state_code=state_code,
        state_name=state_name,
        pan=pan,
    )


def extract_pan_from_gstin(gstin: str) -> Optional[str]:
    """Pull out the PAN from a (possibly invalid) GSTIN. Useful as a fallback
    when a vendor's PAN field is empty but GSTIN is present."""
    if not gstin or len(gstin) < 12:
        return None
    candidate = gstin.strip().upper()[2:12]
    # PAN format: AAAAA9999A
    if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", candidate):
        return candidate
    return None
