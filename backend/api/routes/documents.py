"""Documents endpoints — upload, list, fetch one."""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.deps import current_org_id, current_user_id
from api.schemas import DocumentDetailOut, DocumentListOut, DocumentOut
from common.db import get_db
from common.models import Document
from common.storage import detect_file_type, save_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
    user_id: uuid.UUID = Depends(current_user_id),
) -> DocumentOut:
    """Accept a file upload, persist it to storage, and create a Document row.

    Hands the document off to the extraction worker via Celery.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="file has no filename")

    # Stream to a temp file so we can size-check + move into permanent storage.
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
    total = db.scalar(
        select(func.count()).select_from(Document).where(Document.org_id == org_id)
    )

    stmt = (
        select(Document)
        .where(Document.org_id == org_id)
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
