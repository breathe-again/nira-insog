"""Documents endpoints — upload, list, fetch one, duplicate review."""

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import case, delete, desc, func, select
from sqlalchemy.orm import Session

from api.deps import current_org_id, current_user_id
from api.schemas import DocumentDetailOut, DocumentListOut, DocumentOut
from common.db import get_db
from common.models import BankTransaction, Document, Invoice, Receipt
from common.storage import detect_file_type, read_document_bytes, save_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
    responses={
        409: {
            "description": "An identical file has already been uploaded for "
            "this org. Response body includes the existing document's id, "
            "filename, and upload time so the UI can link to it.",
        }
    },
)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
    user_id: uuid.UUID = Depends(current_user_id),
) -> DocumentOut:
    """Accept a file upload, persist it to storage, and create a Document row.

    Hands the document off to the extraction worker via Celery. Computes a
    SHA-256 hash of the raw file bytes during streaming and rejects the
    upload with HTTP 409 if the same hash already exists for this org —
    prevents accidentally re-ingesting the same MF/bank statement and
    inflating the dashboard with duplicate transactions.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="file has no filename")

    # Stream to a temp file so we can size-check + move into permanent storage.
    # We also feed every chunk into a SHA-256 so the hash is computed in one
    # pass instead of re-reading the file from disk afterward.
    hasher = hashlib.sha256()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_SIZE_BYTES:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"file too large (max {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB)",
                )
            tmp.write(chunk)
            hasher.update(chunk)
    content_hash = hasher.hexdigest()

    # Same-org, same-bytes, not soft-deleted? Don't re-ingest.
    existing = db.scalar(
        select(Document).where(
            Document.org_id == org_id,
            Document.content_sha256 == content_hash,
            Document.deleted_at.is_(None),
        )
    )
    if existing is not None:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "An identical file was already uploaded — refusing to "
                    "create a duplicate. Open the existing document instead."
                ),
                "existing_document_id": str(existing.id),
                "existing_filename": existing.original_filename,
                "uploaded_at": existing.created_at.isoformat() if existing.created_at else None,
            },
        )

    storage_url, size_bytes, encryption_meta = save_upload(
        org_id, file.filename, tmp_path
    )
    file_type = detect_file_type(file.filename, file.content_type)

    document = Document(
        org_id=org_id,
        uploaded_by=user_id,
        source="upload",
        original_filename=file.filename,
        file_url=storage_url,
        file_size_bytes=size_bytes,
        file_type=file_type,
        document_type="unknown",
        status="received",
        encryption_meta=encryption_meta or None,
        content_sha256=content_hash,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Hand off to the worker. Import locally so the API can boot even if Celery
    # config has a transient issue — the upload itself still succeeds.
    try:
        from worker.tasks import process_document

        process_document.delay(str(document.id))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not enqueue document %s: %s", document.id, e)

    return DocumentOut.model_validate(document)


@router.get("", response_model=DocumentListOut, summary="List documents")
def list_documents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> DocumentListOut:
    # Hide soft-deleted documents (from the duplicate-review queue).
    total = db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.org_id == org_id, Document.deleted_at.is_(None))
    )

    stmt = (
        select(Document)
        .where(Document.org_id == org_id, Document.deleted_at.is_(None))
        .order_by(desc(Document.created_at))
        .limit(limit)
        .offset(offset)
    )
    rows = list(db.scalars(stmt).all())
    return DocumentListOut(
        items=[DocumentOut.model_validate(r) for r in rows],
        total=int(total or 0),
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetailOut,
    summary="Get one document (with extraction)",
)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> DocumentDetailOut:
    doc = db.get(Document, document_id)
    if doc is None or doc.org_id != org_id:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentDetailOut.model_validate(doc)


# ---------------------------------------------------------------------------
# Duplicate review queue
#
# Two clustering strategies live side-by-side:
#   1. EXACT — group by content_sha256. Any two docs with the same hash are
#      byte-identical. This is the gold standard and catches all new dupes
#      uploaded after the hash column was added.
#   2. FUZZY — for legacy docs without a hash, group by their financial
#      fingerprint: (document_type, min_txn_date, max_txn_date, total_debit,
#      total_credit, txn_count). Two MF redemption statements covering the
#      same date range with the same totals are overwhelmingly the same
#      source uploaded twice (perhaps in different formats — PDF then HTML).
#
# Only clusters with ≥2 non-deleted docs are surfaced.
# ---------------------------------------------------------------------------


class DuplicateDocOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    document_type: str
    status: str
    file_size_bytes: int
    txn_count: int
    total_debit: Decimal
    total_credit: Decimal
    min_date: Optional[date] = None
    max_date: Optional[date] = None
    uploaded_at: datetime
    has_hash: bool


class DuplicateClusterOut(BaseModel):
    cluster_id: str   # deterministic: "hash:<sha>" or "fuzzy:<doc_type>:<minD>:<maxD>:<dr>:<cr>:<n>"
    cluster_type: str  # "exact" | "fuzzy"
    signature: str    # short human label, e.g. "₹3.00 Cr · 2025-04-17 → 2025-04-17 · 2 txns"
    docs: list[DuplicateDocOut]


class DuplicateClustersOut(BaseModel):
    clusters: list[DuplicateClusterOut]
    total_clusters: int
    total_duplicate_docs: int  # sum of (cluster.docs.count - 1) across clusters


def _signature_for(
    doc_type: str,
    min_d: Optional[date],
    max_d: Optional[date],
    debit: Decimal,
    credit: Decimal,
    n_txn: int,
) -> str:
    """Human-friendly one-line cluster summary."""
    parts: list[str] = []
    if debit > 0:
        parts.append(f"₹{float(debit):,.0f} out")
    if credit > 0:
        parts.append(f"₹{float(credit):,.0f} in")
    if min_d and max_d:
        parts.append(
            f"{min_d.isoformat()}"
            if min_d == max_d
            else f"{min_d.isoformat()} → {max_d.isoformat()}"
        )
    parts.append(f"{n_txn} {'txn' if n_txn == 1 else 'txns'}")
    parts.append(doc_type)
    return " · ".join(parts)


@router.get(
    "/duplicates",
    response_model=DuplicateClustersOut,
    summary="Find clusters of likely duplicate documents",
)
def list_duplicates(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> DuplicateClustersOut:
    """Cluster non-deleted documents in this org by (a) exact SHA-256 match
    where available, and (b) fuzzy financial fingerprint for legacy docs.

    Returns each cluster with the underlying docs so the user can pick which
    copy is canonical and which to delete. Clusters with only one doc are
    suppressed."""

    docs = list(
        db.scalars(
            select(Document).where(
                Document.org_id == org_id,
                Document.deleted_at.is_(None),
            )
        )
    )
    if not docs:
        return DuplicateClustersOut(clusters=[], total_clusters=0, total_duplicate_docs=0)

    # Pre-compute per-doc bank txn aggregates in ONE query so we don't
    # round-trip per document.
    agg_rows = db.execute(
        select(
            BankTransaction.document_id,
            func.min(BankTransaction.txn_date).label("min_d"),
            func.max(BankTransaction.txn_date).label("max_d"),
            func.coalesce(
                func.sum(
                    case(
                        (BankTransaction.direction == "debit", BankTransaction.amount),
                        else_=0,
                    )
                ),
                0,
            ).label("debit_total"),
            func.coalesce(
                func.sum(
                    case(
                        (BankTransaction.direction == "credit", BankTransaction.amount),
                        else_=0,
                    )
                ),
                0,
            ).label("credit_total"),
            func.count().label("n_txn"),
        )
        .where(BankTransaction.org_id == org_id)
        .group_by(BankTransaction.document_id)
    ).all()
    agg_by_doc: dict[uuid.UUID, dict] = {
        r[0]: {
            "min_d": r[1],
            "max_d": r[2],
            "debit": Decimal(r[3] or 0),
            "credit": Decimal(r[4] or 0),
            "n_txn": int(r[5] or 0),
        }
        for r in agg_rows
    }

    # Build a row-level view of each doc.
    def _row(d: Document) -> DuplicateDocOut:
        agg = agg_by_doc.get(d.id, {"min_d": None, "max_d": None, "debit": Decimal(0), "credit": Decimal(0), "n_txn": 0})
        return DuplicateDocOut(
            id=d.id,
            original_filename=d.original_filename,
            document_type=d.document_type or "unknown",
            status=d.status,
            file_size_bytes=int(d.file_size_bytes or 0),
            txn_count=agg["n_txn"],
            total_debit=agg["debit"],
            total_credit=agg["credit"],
            min_date=agg["min_d"],
            max_date=agg["max_d"],
            uploaded_at=d.created_at,
            has_hash=bool(d.content_sha256),
        )

    # --- Tier 1: exact hash clusters ---
    by_hash: dict[str, list[Document]] = defaultdict(list)
    for d in docs:
        if d.content_sha256:
            by_hash[d.content_sha256].append(d)

    clusters: list[DuplicateClusterOut] = []
    used_doc_ids: set[uuid.UUID] = set()
    for sha, ds in by_hash.items():
        if len(ds) < 2:
            continue
        # Sort: oldest first (likely canonical).
        ds.sort(key=lambda d: d.created_at)
        rows = [_row(d) for d in ds]
        ref = rows[0]
        clusters.append(
            DuplicateClusterOut(
                cluster_id=f"hash:{sha[:16]}",
                cluster_type="exact",
                signature=_signature_for(
                    ref.document_type,
                    ref.min_date,
                    ref.max_date,
                    ref.total_debit,
                    ref.total_credit,
                    ref.txn_count,
                ) + " · same bytes",
                docs=rows,
            )
        )
        for d in ds:
            used_doc_ids.add(d.id)

    # --- Tier 2: fuzzy fingerprint clusters (only for docs not already
    # captured by an exact hash cluster, and only for docs that have
    # bank-transaction children — receipts/invoices use a coarser key). ---
    # Round amounts to nearest ₹1 to absorb floating-point rounding
    # differences between OCR re-extractions.
    def _round(v: Decimal) -> int:
        return int(v.quantize(Decimal("1")))

    by_fuzzy: dict[tuple, list[Document]] = defaultdict(list)
    for d in docs:
        if d.id in used_doc_ids:
            continue
        agg = agg_by_doc.get(d.id)
        if agg is None or agg["n_txn"] == 0:
            # Skip docs with no extracted txns — too little signal to cluster.
            continue
        key = (
            d.document_type or "unknown",
            agg["min_d"],
            agg["max_d"],
            _round(agg["debit"]),
            _round(agg["credit"]),
            agg["n_txn"],
        )
        by_fuzzy[key].append(d)

    for key, ds in by_fuzzy.items():
        if len(ds) < 2:
            continue
        ds.sort(key=lambda d: d.created_at)
        rows = [_row(d) for d in ds]
        ref = rows[0]
        doc_type, min_d, max_d, dr, cr, n = key
        clusters.append(
            DuplicateClusterOut(
                cluster_id=f"fuzzy:{doc_type}:{min_d}:{max_d}:{dr}:{cr}:{n}",
                cluster_type="fuzzy",
                signature=_signature_for(
                    doc_type, min_d, max_d, Decimal(dr), Decimal(cr), n
                ),
                docs=rows,
            )
        )

    # Sort clusters by total amount involved, biggest first — most impactful
    # to clean up.
    def _cluster_weight(c: DuplicateClusterOut) -> Decimal:
        return c.docs[0].total_debit + c.docs[0].total_credit if c.docs else Decimal(0)

    clusters.sort(key=_cluster_weight, reverse=True)

    total_dup = sum(max(0, len(c.docs) - 1) for c in clusters)
    return DuplicateClustersOut(
        clusters=clusters,
        total_clusters=len(clusters),
        total_duplicate_docs=total_dup,
    )


class DeleteDuplicateOut(BaseModel):
    document_id: uuid.UUID
    txns_deleted: int
    invoices_unlinked: int
    receipts_unlinked: int


@router.post(
    "/{document_id}/delete-as-duplicate",
    response_model=DeleteDuplicateOut,
    summary="Mark a document as a duplicate and remove its transactions",
)
def delete_as_duplicate(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> DeleteDuplicateOut:
    """Soft-delete the document (sets deleted_at), and hard-delete the bank
    transactions linked to it. Invoices and receipts get their document_id
    unlinked (SET NULL) since they may be referenced from other workflows."""
    doc = db.get(Document, document_id)
    if doc is None or doc.org_id != org_id:
        raise HTTPException(status_code=404, detail="document not found")
    if doc.deleted_at is not None:
        raise HTTPException(status_code=400, detail="document is already deleted")

    # Hard-delete the bank txns — they're owned 1:1 by this doc and removing
    # them is the whole point of marking it a duplicate.
    txn_result = db.execute(
        delete(BankTransaction).where(
            BankTransaction.org_id == org_id,
            BankTransaction.document_id == document_id,
        )
    )
    txns_deleted = txn_result.rowcount or 0

    # Invoices/receipts can be referenced from other workflows (matched to
    # bank txns, sales pipeline, etc.) so we unlink rather than destroy.
    inv_result = db.execute(
        delete(Invoice).where(
            Invoice.org_id == org_id,
            Invoice.document_id == document_id,
        )
    )
    invoices_unlinked = inv_result.rowcount or 0

    rcpt_result = db.execute(
        delete(Receipt).where(
            Receipt.org_id == org_id,
            Receipt.document_id == document_id,
        )
    )
    receipts_unlinked = rcpt_result.rowcount or 0

    doc.deleted_at = datetime.now(timezone.utc)
    db.add(doc)
    db.commit()

    return DeleteDuplicateOut(
        document_id=document_id,
        txns_deleted=txns_deleted,
        invoices_unlinked=invoices_unlinked,
        receipts_unlinked=receipts_unlinked,
    )


class BackfillHashesOut(BaseModel):
    processed: int
    updated: int
    skipped: int  # files not found in storage
    errors: int


@router.post(
    "/backfill-hashes",
    response_model=BackfillHashesOut,
    summary="Compute SHA-256 for legacy documents (one-shot)",
)
def backfill_hashes(
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> BackfillHashesOut:
    """Walk documents without a content_sha256 hash and compute one from
    storage. Idempotent — safe to call repeatedly. Bounded by `limit` so it
    can be chunked over multiple calls if the org has many docs.

    Files we can't read (storage missing, encrypted with a key we no longer
    have) are skipped silently — they remain unhashed and the upload-time
    409 check just won't apply to them."""

    docs = list(
        db.scalars(
            select(Document)
            .where(
                Document.org_id == org_id,
                Document.content_sha256.is_(None),
                Document.deleted_at.is_(None),
            )
            .limit(limit)
        )
    )

    processed = 0
    updated = 0
    skipped = 0
    errors = 0
    for d in docs:
        processed += 1
        try:
            data = read_document_bytes(d.file_url, d.encryption_meta)
            d.content_sha256 = hashlib.sha256(data).hexdigest()
            db.add(d)
            updated += 1
        except FileNotFoundError:
            skipped += 1
        except Exception:  # noqa: BLE001
            logger.exception("backfill_hashes: failed for doc %s", d.id)
            errors += 1

    db.commit()
    return BackfillHashesOut(
        processed=processed, updated=updated, skipped=skipped, errors=errors
    )
