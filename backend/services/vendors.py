"""Vendor / client resolution via fuzzy matching.

The problem: bank descriptions, OCR'd invoices, and manual entry all spell
the same counterparty slightly differently:

    "ABC Traders"
    "A.B.C. Traders Pvt Ltd"
    "ABC TRADERS PRIVATE LIMITED"
    "abc-traders"

We want all four to resolve to the *same* Vendor row. This module:

1. Normalizes the candidate name (strip suffixes, punctuation, case).
2. Compares the normalized form against existing vendors' name + aliases.
3. If best score >= MATCH_THRESHOLD, returns that vendor and records the
   raw form as a new alias if it isn't already known.
4. Otherwise, inserts a new Vendor and returns it.

Uses rapidfuzz's `token_set_ratio` because it's robust to word order and
extra/missing words ("Pvt Ltd" vs not).

This module DOES touch the database — it's the bridge from parser drafts to
real Vendor rows.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import Client, Vendor

logger = logging.getLogger(__name__)


# Score (0–100) above which we trust the fuzzy match. Tuned against the
# examples in tests — 85 lets "A.B.C. Traders" merge with "ABC Traders Pvt Ltd"
# without merging "ABC Traders" and "ABC Tradings".
MATCH_THRESHOLD = 85


# Corporate suffixes / noise we strip before comparing. Order: longest first.
_SUFFIX_TOKENS = (
    "private limited",
    "pvt limited",
    "pvt. ltd.",
    "pvt ltd",
    "pvt.ltd",
    "pvt",
    "limited",
    "ltd.",
    "ltd",
    "llp",
    "inc.",
    "inc",
    "co.",
    "co",
    "corp.",
    "corp",
    "corporation",
    "company",
    "& co",
    "and co",
)

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation + corporate suffixes, collapse whitespace."""
    if not name:
        return ""
    s = name.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()

    # Strip suffixes from the end, repeatedly (handles "X Pvt Ltd Co").
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIX_TOKENS:
            if s.endswith(" " + suffix) or s == suffix:
                s = s[: -len(suffix)].strip()
                changed = True
                break

    return s


# ---------------------------------------------------------------------------
# Vendor resolution
# ---------------------------------------------------------------------------


def resolve_vendor(
    db: Session,
    org_id: uuid.UUID,
    raw_name: Optional[str],
    *,
    gstin: Optional[str] = None,
    create_if_missing: bool = True,
) -> Optional[Vendor]:
    """Find or create a Vendor that best matches `raw_name`.

    - GSTIN takes precedence: if a vendor with the same GSTIN exists in this
      org, return it and record the raw_name as an alias.
    - Otherwise fuzzy-match against name + aliases of existing vendors.
    - If best score >= MATCH_THRESHOLD: return that vendor (add alias).
    - Else if create_if_missing: insert a new Vendor.
    - Else: return None.

    The session is flushed but not committed — the caller decides the
    transaction boundary.
    """
    if not raw_name and not gstin:
        return None

    clean_raw = (raw_name or "").strip()

    # ---- GSTIN exact match ----
    if gstin:
        gstin_norm = gstin.strip().upper()
        existing = db.execute(
            select(Vendor).where(Vendor.org_id == org_id, Vendor.gstin == gstin_norm)
        ).scalar_one_or_none()
        if existing is not None:
            if clean_raw:
                _maybe_add_alias(existing, clean_raw)
            return existing

    # ---- Fuzzy name match ----
    if clean_raw:
        normalized = normalize_name(clean_raw)
        if not normalized:
            # Pure-suffix string ("Pvt Ltd") — refuse to create a vendor.
            return None

        candidates = list(
            db.execute(select(Vendor).where(Vendor.org_id == org_id)).scalars()
        )

        match = _best_match(normalized, candidates)
        if match is not None:
            vendor, score = match
            logger.debug(
                "vendor fuzzy match: %r ≈ %r (score=%d)",
                clean_raw,
                vendor.name,
                score,
            )
            _maybe_add_alias(vendor, clean_raw)
            return vendor

    # ---- Create new ----
    if not create_if_missing:
        return None

    new_vendor = Vendor(
        org_id=org_id,
        name=clean_raw or (gstin or "Unknown vendor"),
        aliases=[clean_raw] if clean_raw else None,
        gstin=gstin.strip().upper() if gstin else None,
    )
    db.add(new_vendor)
    db.flush()
    return new_vendor


def resolve_client(
    db: Session,
    org_id: uuid.UUID,
    raw_name: Optional[str],
    *,
    gstin: Optional[str] = None,
    create_if_missing: bool = True,
) -> Optional[Client]:
    """Same as resolve_vendor but for the Client table (sales-side counterparty)."""
    if not raw_name and not gstin:
        return None

    clean_raw = (raw_name or "").strip()

    if gstin:
        gstin_norm = gstin.strip().upper()
        existing = db.execute(
            select(Client).where(Client.org_id == org_id, Client.gstin == gstin_norm)
        ).scalar_one_or_none()
        if existing is not None:
            if clean_raw:
                _maybe_add_alias(existing, clean_raw)
            return existing

    if clean_raw:
        normalized = normalize_name(clean_raw)
        if not normalized:
            return None
        candidates = list(
            db.execute(select(Client).where(Client.org_id == org_id)).scalars()
        )
        match = _best_match(normalized, candidates)
        if match is not None:
            client, _score = match
            _maybe_add_alias(client, clean_raw)
            return client

    if not create_if_missing:
        return None

    new_client = Client(
        org_id=org_id,
        name=clean_raw or (gstin or "Unknown client"),
        aliases=[clean_raw] if clean_raw else None,
        gstin=gstin.strip().upper() if gstin else None,
    )
    db.add(new_client)
    db.flush()
    return new_client


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _candidate_keys(entity: Vendor | Client) -> list[str]:
    """Return all normalized strings we should compare against for this entity."""
    keys = [normalize_name(entity.name)]
    for alias in entity.aliases or []:
        n = normalize_name(alias)
        if n and n not in keys:
            keys.append(n)
    return [k for k in keys if k]


def _best_match(
    normalized_target: str,
    candidates: list[Vendor] | list[Client],
) -> Optional[tuple[Vendor | Client, int]]:
    """Find the candidate with the highest fuzzy score above MATCH_THRESHOLD."""
    if not candidates or not normalized_target:
        return None

    # Build a flat dict of {key_index: normalized_string}, then map back.
    flat_keys: list[str] = []
    key_to_entity: list[Vendor | Client] = []
    for entity in candidates:
        for key in _candidate_keys(entity):
            flat_keys.append(key)
            key_to_entity.append(entity)

    if not flat_keys:
        return None

    result = process.extractOne(
        normalized_target,
        flat_keys,
        scorer=fuzz.token_set_ratio,
        score_cutoff=MATCH_THRESHOLD,
    )
    if result is None:
        return None

    _matched_key, score, idx = result
    return key_to_entity[idx], int(score)


def _maybe_add_alias(entity: Vendor | Client, raw_name: str) -> None:
    """Append `raw_name` to entity.aliases if it's a new spelling."""
    if not raw_name:
        return
    existing = set(entity.aliases or [])
    if raw_name in existing or raw_name == entity.name:
        return
    # Also dedupe on normalized form so we don't accumulate near-duplicates.
    normalized = normalize_name(raw_name)
    if any(normalize_name(a) == normalized for a in existing):
        return
    if normalize_name(entity.name) == normalized:
        return
    entity.aliases = [*(entity.aliases or []), raw_name]
