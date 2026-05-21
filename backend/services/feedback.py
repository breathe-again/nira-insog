"""Feedback writer + learning helpers.

Whenever a user corrects something (re-categorizes a receipt, renames a
vendor, merges two vendors, changes a doc's type), we:

  1. Apply the change to the entity.
  2. Write a `FeedbackEvent` row capturing field + old_value + new_value.
  3. Optionally feed the change back into future-extraction signals:
       - Vendor.default_expense_category   (learn category by vendor)
       - Vendor.aliases                    (learn name variants)
       - FilenameHint                      (learn filename → doc_type)
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import (
    FeedbackEvent,
    FilenameHint,
    Receipt,
    Vendor,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def record_event(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    entity_type: str,
    entity_id: uuid.UUID,
    field: str,
    old_value: Any,
    new_value: Any,
) -> FeedbackEvent:
    """Insert a FeedbackEvent row. Caller commits."""
    event = FeedbackEvent(
        org_id=org_id,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        old_value=_jsonable(old_value),
        new_value=_jsonable(new_value),
    )
    db.add(event)
    return event


def _jsonable(v: Any) -> Optional[dict]:
    """Wrap a scalar in a `{value: ...}` dict (JSONB requires top-level dict)."""
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    if isinstance(v, uuid.UUID):
        return {"value": str(v)}
    if isinstance(v, (str, int, float, bool)):
        return {"value": v}
    return {"value": str(v)}


# ---------------------------------------------------------------------------
# Learners — small functions called by routes after they record feedback
# ---------------------------------------------------------------------------


def learn_vendor_category(
    db: Session, *, org_id: uuid.UUID, vendor_id: uuid.UUID, category: str
) -> None:
    """Update Vendor.default_expense_category — propagates to future receipts."""
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != org_id:
        return
    vendor.default_expense_category = category
    db.flush()


def learn_vendor_alias(
    db: Session, *, org_id: uuid.UUID, vendor_id: uuid.UUID, alias: str
) -> None:
    """Append `alias` to Vendor.aliases (deduplicated)."""
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != org_id:
        return
    alias_norm = alias.strip()
    if not alias_norm:
        return
    aliases = list(vendor.aliases or [])
    if alias_norm.lower() in {a.lower() for a in aliases}:
        return
    aliases.append(alias_norm)
    vendor.aliases = aliases
    db.flush()


def learn_filename_hint(
    db: Session,
    *,
    org_id: uuid.UUID,
    original_filename: str,
    document_type: str,
) -> None:
    """If the filename has a recognizable pattern (e.g. 'invoice_*.pdf'),
    insert/bump a FilenameHint row for next time.

    The pattern we extract is a coarse normalization of the filename — turns
    digits into `#`, sequences of letters into a slug, strips the extension.
    Two uploads like 'invoice_2026_04.pdf' and 'invoice_2026_05.pdf' end up
    with the same hint."""
    if not original_filename:
        return
    pattern = _normalize_filename(original_filename)
    if not pattern:
        return

    existing = db.execute(
        select(FilenameHint).where(
            FilenameHint.org_id == org_id, FilenameHint.pattern == pattern
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            FilenameHint(
                org_id=org_id,
                pattern=pattern,
                document_type=document_type,
                hit_count=1,
            )
        )
    else:
        if existing.document_type == document_type:
            existing.hit_count = (existing.hit_count or 0) + 1
        else:
            # Disagreement — switch to the latest user-confirmed type and
            # reset the counter. This means recent corrections beat history.
            existing.document_type = document_type
            existing.hit_count = 1
    db.flush()


def lookup_filename_hint(
    db: Session, *, org_id: uuid.UUID, original_filename: str
) -> Optional[str]:
    """Return the document_type previously learned for this filename pattern."""
    pattern = _normalize_filename(original_filename)
    if not pattern:
        return None
    hint = db.execute(
        select(FilenameHint).where(
            FilenameHint.org_id == org_id, FilenameHint.pattern == pattern
        )
    ).scalar_one_or_none()
    return hint.document_type if hint else None


_FILENAME_NORM_RE = re.compile(r"[A-Za-z]+|\d+|[^A-Za-z\d]")


def _normalize_filename(filename: str) -> str:
    """Reduce a filename to a coarse pattern. 'invoice_2026_04.pdf' →
    'invoice_#_#.pdf'.

    Letters stay; digit-runs become '#'; punctuation is preserved.
    """
    name = filename.lower().strip()
    parts = []
    for tok in _FILENAME_NORM_RE.findall(name):
        if tok.isdigit():
            parts.append("#")
        else:
            parts.append(tok)
    return "".join(parts)[:255]


# ---------------------------------------------------------------------------
# Vendor merge — re-points all references to the losing vendor
# ---------------------------------------------------------------------------


def merge_vendors(
    db: Session,
    *,
    org_id: uuid.UUID,
    winner_id: uuid.UUID,
    loser_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
) -> dict:
    """Re-point all references from `loser_id` to `winner_id`, archive loser's
    name as an alias of the winner, then delete the losing Vendor row.

    Returns a dict of how many rows were re-pointed in each table.
    """
    if winner_id == loser_id:
        return {"reassigned": 0}

    winner = db.get(Vendor, winner_id)
    loser = db.get(Vendor, loser_id)
    if winner is None or winner.org_id != org_id:
        raise LookupError("winner vendor not found")
    if loser is None or loser.org_id != org_id:
        raise LookupError("loser vendor not found")

    # 1) Re-point BankTransaction.matched_vendor_id
    from common.models import BankTransaction, Invoice  # local — avoid cycles

    bt_count = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.org_id == org_id,
            BankTransaction.matched_vendor_id == loser_id,
        )
        .update({"matched_vendor_id": winner_id}, synchronize_session=False)
    )

    # 2) Re-point Receipt.vendor_id
    r_count = (
        db.query(Receipt)
        .filter(Receipt.org_id == org_id, Receipt.vendor_id == loser_id)
        .update({"vendor_id": winner_id}, synchronize_session=False)
    )

    # 3) Re-point Invoice.vendor_id (purchase invoices)
    inv_count = (
        db.query(Invoice)
        .filter(Invoice.org_id == org_id, Invoice.vendor_id == loser_id)
        .update({"vendor_id": winner_id}, synchronize_session=False)
    )

    # 4) Save the loser's name as an alias on the winner.
    learn_vendor_alias(db, org_id=org_id, vendor_id=winner_id, alias=loser.name)
    for a in loser.aliases or []:
        learn_vendor_alias(db, org_id=org_id, vendor_id=winner_id, alias=a)

    # 5) Record one feedback event for the merge itself.
    record_event(
        db,
        org_id=org_id,
        user_id=user_id,
        entity_type="vendor",
        entity_id=winner_id,
        field="merged_in",
        old_value={"loser_name": loser.name, "loser_id": str(loser.id)},
        new_value={"winner_name": winner.name, "winner_id": str(winner.id)},
    )

    db.delete(loser)
    db.flush()

    return {
        "bank_transactions_reassigned": bt_count,
        "receipts_reassigned": r_count,
        "invoices_reassigned": inv_count,
        "winner_id": str(winner.id),
        "loser_id": str(loser_id),
    }
