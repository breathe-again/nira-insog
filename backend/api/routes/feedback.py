"""Feedback / edit endpoints.

These routes let the user correct what the extraction got wrong:
  - PATCH /api/documents/{id}            edit a Document's document_type / vendor / category
  - PATCH /api/vendors/{id}              rename / set default category / add alias
  - POST  /api/vendors/{id}/merge        merge another vendor into this one
  - PATCH /api/insights/{id}             change severity / mute the linked vendor

All of these write FeedbackEvent rows AND security-relevant ones write
AuditEvent rows too.

Auth: every route is behind `get_current_user`. Tenancy: every entity is
fetched with `org_id == current.org_id` so a user can't touch another org's
data even with a guessed id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import CurrentUser, get_current_user
from api.schemas import (
    DocumentPatchIn,
    InsightPatchIn,
    VendorMergeIn,
    VendorPatchIn,
)
from common.db import get_db
from common.models import (
    BankTransaction,
    Document,
    Insight,
    Receipt,
    Vendor,
    VendorMute,
)
from services import audit, feedback

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Document edits
# ---------------------------------------------------------------------------


@router.patch("/api/documents/{document_id}", summary="Edit a document (feedback)")
def patch_document(
    document_id: uuid.UUID,
    body: DocumentPatchIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Apply a user correction. Writes FeedbackEvent + AuditEvent rows.

    Editable fields (any subset):
      - document_type   → also learns a FilenameHint for this org.
      - vendor_id       → updates the linked Receipt/Invoice/BankTxn.
      - category        → updates the linked Receipt or BankTxn; if a vendor
                          is set, also propagates to Vendor.default_expense_category.
    """
    doc = db.get(Document, document_id)
    if doc is None or doc.org_id != current.org_id:
        raise HTTPException(status_code=404, detail="document not found")

    changes: list[str] = []

    # ---- document_type ------------------------------------------------
    if body.document_type is not None and body.document_type != doc.document_type:
        feedback.record_event(
            db,
            org_id=current.org_id,
            user_id=current.id,
            entity_type="document",
            entity_id=doc.id,
            field="document_type",
            old_value=doc.document_type,
            new_value=body.document_type,
        )
        doc.document_type = body.document_type
        feedback.learn_filename_hint(
            db,
            org_id=current.org_id,
            original_filename=doc.original_filename,
            document_type=body.document_type,
        )
        changes.append("document_type")

    # ---- vendor_id ----------------------------------------------------
    # We update whichever linked entity exists for this doc — Receipt,
    # Invoice (purchase), or BankTransaction's matched_vendor_id.
    if body.vendor_id is not None:
        vendor = db.get(Vendor, body.vendor_id)
        if vendor is None or vendor.org_id != current.org_id:
            raise HTTPException(status_code=404, detail="vendor not found")

        receipt = db.execute(
            select(Receipt).where(Receipt.document_id == doc.id)
        ).scalar_one_or_none()
        if receipt is not None and receipt.vendor_id != vendor.id:
            feedback.record_event(
                db,
                org_id=current.org_id,
                user_id=current.id,
                entity_type="receipt",
                entity_id=receipt.id,
                field="vendor_id",
                old_value=receipt.vendor_id,
                new_value=vendor.id,
            )
            receipt.vendor_id = vendor.id
            changes.append("receipt.vendor_id")

    # ---- category -----------------------------------------------------
    if body.category is not None:
        receipt = db.execute(
            select(Receipt).where(Receipt.document_id == doc.id)
        ).scalar_one_or_none()
        if receipt is not None and receipt.category != body.category:
            feedback.record_event(
                db,
                org_id=current.org_id,
                user_id=current.id,
                entity_type="receipt",
                entity_id=receipt.id,
                field="category",
                old_value=receipt.category,
                new_value=body.category,
            )
            receipt.category = body.category
            # Also teach the vendor's default category for next time.
            if receipt.vendor_id is not None:
                feedback.learn_vendor_category(
                    db,
                    org_id=current.org_id,
                    vendor_id=receipt.vendor_id,
                    category=body.category,
                )
            changes.append("receipt.category")

    audit.record(
        db,
        event_type="doc.edit",
        org_id=current.org_id,
        user_id=current.id,
        entity_type="document",
        entity_id=doc.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta={"fields": changes},
    )

    db.commit()
    return {"updated": changes, "document_id": str(doc.id)}


# ---------------------------------------------------------------------------
# Vendor edits + merge
# ---------------------------------------------------------------------------


@router.patch("/api/vendors/{vendor_id}", summary="Edit a vendor")
def patch_vendor(
    vendor_id: uuid.UUID,
    body: VendorPatchIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != current.org_id:
        raise HTTPException(status_code=404, detail="vendor not found")

    changes: list[str] = []
    if body.name is not None and body.name != vendor.name:
        feedback.record_event(
            db,
            org_id=current.org_id,
            user_id=current.id,
            entity_type="vendor",
            entity_id=vendor.id,
            field="name",
            old_value=vendor.name,
            new_value=body.name,
        )
        # The old name lives on as an alias.
        feedback.learn_vendor_alias(
            db, org_id=current.org_id, vendor_id=vendor.id, alias=vendor.name
        )
        vendor.name = body.name
        changes.append("name")

    if body.default_expense_category is not None and (
        vendor.default_expense_category != body.default_expense_category
    ):
        feedback.record_event(
            db,
            org_id=current.org_id,
            user_id=current.id,
            entity_type="vendor",
            entity_id=vendor.id,
            field="default_expense_category",
            old_value=vendor.default_expense_category,
            new_value=body.default_expense_category,
        )
        vendor.default_expense_category = body.default_expense_category
        changes.append("default_expense_category")

    if body.gstin is not None and body.gstin != vendor.gstin:
        feedback.record_event(
            db,
            org_id=current.org_id,
            user_id=current.id,
            entity_type="vendor",
            entity_id=vendor.id,
            field="gstin",
            old_value=vendor.gstin,
            new_value=body.gstin,
        )
        vendor.gstin = body.gstin
        changes.append("gstin")

    if body.add_alias:
        feedback.learn_vendor_alias(
            db, org_id=current.org_id, vendor_id=vendor.id, alias=body.add_alias
        )
        feedback.record_event(
            db,
            org_id=current.org_id,
            user_id=current.id,
            entity_type="vendor",
            entity_id=vendor.id,
            field="aliases",
            old_value=None,
            new_value={"added": body.add_alias},
        )
        changes.append("aliases")

    audit.record(
        db,
        event_type="vendor.edit",
        org_id=current.org_id,
        user_id=current.id,
        entity_type="vendor",
        entity_id=vendor.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta={"fields": changes},
    )
    db.commit()
    return {"updated": changes, "vendor_id": str(vendor.id)}


@router.post("/api/vendors/{vendor_id}/merge", summary="Merge another vendor into this one")
def merge_vendor(
    vendor_id: uuid.UUID,
    body: VendorMergeIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = feedback.merge_vendors(
            db,
            org_id=current.org_id,
            winner_id=vendor_id,
            loser_id=body.loser_id,
            user_id=current.id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))

    audit.record(
        db,
        event_type="vendor.merge",
        org_id=current.org_id,
        user_id=current.id,
        entity_type="vendor",
        entity_id=vendor_id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta=result,
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Insight edits
# ---------------------------------------------------------------------------


@router.patch("/api/insights/{insight_id}", summary="Edit an insight")
def patch_insight(
    insight_id: uuid.UUID,
    body: InsightPatchIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ins = db.get(Insight, insight_id)
    if ins is None or ins.org_id != current.org_id:
        raise HTTPException(status_code=404, detail="insight not found")

    changes: list[str] = []
    if body.severity is not None and ins.severity != body.severity:
        feedback.record_event(
            db,
            org_id=current.org_id,
            user_id=current.id,
            entity_type="insight",
            entity_id=ins.id,
            field="severity",
            old_value=ins.severity,
            new_value=body.severity,
        )
        ins.severity = body.severity
        changes.append("severity")

    if body.mute_vendor:
        # Find the vendor referenced by the insight (if any) via the
        # supporting_data payload — anomaly insights stash vendor_id there.
        vendor_id_raw = (ins.supporting_data or {}).get("vendor_id")
        if vendor_id_raw:
            try:
                vendor_id = uuid.UUID(str(vendor_id_raw))
            except ValueError:
                vendor_id = None
            if vendor_id is not None:
                # Upsert the mute row.
                existing = db.execute(
                    select(VendorMute).where(
                        VendorMute.vendor_id == vendor_id,
                        VendorMute.rule == "anomaly",
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(
                        VendorMute(
                            org_id=current.org_id,
                            vendor_id=vendor_id,
                            muted_by=current.id,
                            rule="anomaly",
                        )
                    )
                audit.record(
                    db,
                    event_type="insight.mute_vendor",
                    org_id=current.org_id,
                    user_id=current.id,
                    entity_type="vendor",
                    entity_id=vendor_id,
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                )
                changes.append("muted_vendor")

    audit.record(
        db,
        event_type="insight.edit",
        org_id=current.org_id,
        user_id=current.id,
        entity_type="insight",
        entity_id=ins.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta={"fields": changes},
    )
    db.commit()
    return {"updated": changes, "insight_id": str(ins.id)}
