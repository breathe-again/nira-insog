"""Background tasks — the real Phase-1 understanding pipeline.

`process_document` walks a Document through its status state machine:

    received → extracting → extracted → understood → indexed
                                          ↓
                                       error

What happens at each stage depends on the file type:

  CSV bank statement:
    1. extracting:   read the CSV (no OCR), parse into BankTxnDraft list.
    2. extracted:    persist raw_extraction_json (count + sample rows).
    3. understood:   resolve each draft to a Vendor (fuzzy), insert
                     BankTransaction rows, run per-vendor anomaly checks,
                     emit Insight rows.
    4. indexed:      done.

  PDF / image (invoice / receipt):
    1. extracting:   STUB — until Tesseract + LLM are wired (deferred to save
                     disk), we write a tiny placeholder JSON and move on.
                     If a real `raw_extraction_json` already exists on the
                     Document (e.g. set via a future API), we parse it here
                     and run the full understanding step below.
    2. extracted:    raw_extraction_json populated.
    3. understood:   if the extraction parses as an invoice/receipt, resolve
                     the counterparty and insert the typed row + run anomaly.
                     Otherwise mark understood with no entities.
    4. indexed:      done.

Failures at any step write the error to Document.error_message and set
status='error'. The task does NOT auto-retry data problems — retry only on
transient infra errors.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .app import celery_app
from common.db import SessionLocal
from common.models import (
    BankTransaction,
    Document,
    Invoice,
    Receipt,
)
from common.storage import open_document
from services.anomalies import check_bank_transaction, check_receipt
from services.extractors import llm_vision
from services.parsers.bank_csv import parse_bank_csv
from services.parsers.extracted_json import (
    BankStatementDraft,
    ExtractedJSONError,
    InvoiceDraft,
    ReceiptDraft,
    parse_extracted_json,
)
from services.parsers.bank_csv import extract_vendor_hint
from services.recurring import (
    emit_missed_payment_insights,
    tag_recurring_transactions,
    upsert_patterns,
)
from services.embeddings import (
    embed_batch as _embed_batch,
    is_enabled as _embeddings_enabled,
    set_txn_embedding as _set_txn_embedding,
)
from services.vendors import resolve_client, resolve_vendor

logger = logging.getLogger(__name__)


@celery_app.task(name="documents.process", bind=True, max_retries=2)
def process_document(self, document_id: str) -> dict:
    """Top-level Celery task. Returns a small summary dict for the result backend."""
    doc_uuid = uuid.UUID(document_id)
    logger.info("processing document %s", document_id)

    with SessionLocal() as db:
        doc = db.get(Document, doc_uuid)
        if doc is None:
            logger.error("document %s not found", document_id)
            return {"status": "not_found"}

        try:
            summary = _run_pipeline(db, doc)
            doc.status = "indexed"
            doc.processed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("document %s indexed: %s", document_id, summary)
            return {"status": "indexed", "document_id": document_id, **summary}

        except Exception as e:  # noqa: BLE001
            db.rollback()
            doc = db.get(Document, doc_uuid)
            if doc is not None:
                doc.status = "error"
                doc.error_message = f"{e.__class__.__name__}: {e}"[:1000]
                db.commit()
            logger.exception("error processing document %s", document_id)
            raise


# ---------------------------------------------------------------------------
# Pipeline dispatch
# ---------------------------------------------------------------------------


def _run_pipeline(db: Session, doc: Document) -> dict:
    """Dispatch on file_type. Returns a small dict of counts."""
    doc.status = "extracting"
    db.commit()

    if doc.file_type == "csv":
        return _run_bank_csv(db, doc)
    if doc.file_type in {"pdf", "image", "xlsx", "html"}:
        return _run_extracted(db, doc)

    # Unknown file type — record a stub extraction and move on.
    doc.raw_extraction_json = {
        "stub": True,
        "note": f"unsupported file_type={doc.file_type!r}; no extractor wired",
    }
    doc.status = "extracted"
    db.commit()
    doc.status = "understood"
    db.commit()
    return {"entities_created": 0}


# ---------------------------------------------------------------------------
# CSV bank-statement path
# ---------------------------------------------------------------------------


def _run_bank_csv(db: Session, doc: Document) -> dict:
    """Parse a bank-statement CSV into BankTransaction rows."""
    with open_document(doc.file_url, doc.encryption_meta) as path:
        if not path.exists():
            raise FileNotFoundError(f"file not on disk: {doc.file_url}")
        content = path.read_bytes()
    drafts, report = parse_bank_csv(content)

    if not drafts:
        # Don't kill the doc — record what went wrong and stop cleanly.
        doc.raw_extraction_json = {
            "kind": "bank_csv",
            "rows_total": report.rows_total,
            "rows_parsed": 0,
            "rows_skipped": report.rows_skipped,
            "errors": report.errors[:20],
        }
        doc.document_type = "bank_statement"
        doc.status = "extracted"
        db.commit()
        doc.status = "understood"
        db.commit()
        return {"entities_created": 0, "rows_parsed": 0, "errors": len(report.errors)}

    # Stage 2: extracted — record the raw parse summary.
    doc.document_type = "bank_statement"
    doc.raw_extraction_json = {
        "kind": "bank_csv",
        "rows_total": report.rows_total,
        "rows_parsed": report.rows_parsed,
        "rows_skipped": report.rows_skipped,
        "errors": report.errors[:20],
        "sample": [d.as_dict() for d in drafts[:3]],
    }
    doc.status = "extracted"
    db.commit()

    # Stage 3: understood — vendor resolution + insert + anomaly checks.
    inserted: list[BankTransaction] = []
    for draft in drafts:
        vendor = None
        if draft.raw_vendor_hint:
            # Only resolve to a vendor for outflows; inflows would map to clients.
            if draft.direction == "debit":
                vendor = resolve_vendor(db, doc.org_id, draft.raw_vendor_hint)
            else:
                # Credits → client. We still set matched_client_id, not vendor.
                resolve_client(db, doc.org_id, draft.raw_vendor_hint)

        # Tier-1 learning: inherit the vendor's default expense category.
        # This means founders who once said "Cafe Coffee Day is 'meals'" don't
        # have to re-tag every future Swiggy charge that maps to that vendor.
        inherited_category = (
            vendor.default_expense_category
            if vendor is not None and vendor.default_expense_category
            else None
        )

        txn = BankTransaction(
            org_id=doc.org_id,
            document_id=doc.id,
            txn_date=draft.txn_date,
            description=draft.description,
            amount=draft.amount,
            direction=draft.direction,
            running_balance=draft.running_balance,
            matched_vendor_id=vendor.id if vendor else None,
            category=inherited_category,
            auto_tagged_by=("vendor_default" if inherited_category else None),
        )
        db.add(txn)
        inserted.append(txn)

    db.flush()

    # Stage 3a: embed descriptions for the freshly-inserted txns (Tier 2).
    # Bulk-embed in one batch — much faster than per-row.
    _embed_new_txns(db, inserted)

    # Stage 3b: re-learn recurring patterns over the org's full history and
    # tag the freshly-inserted rows that match.
    upsert_patterns(db, org_id=doc.org_id)
    tag_recurring_transactions(db, org_id=doc.org_id, txns=inserted)

    # Stage 3c: anomaly detection runs after all rows are visible, so a
    # spike near the end of a statement can use the earlier rows as history.
    # We skip txns flagged as recurring — those are by definition normal.
    anomalies_emitted = 0
    for txn in inserted:
        if txn.is_recurring:
            continue
        result = check_bank_transaction(db, doc.org_id, txn)
        if result is not None:
            anomalies_emitted += 1

    # Stage 3d: emit missed-payment insights for any recurring pattern that's
    # overdue. This catches "rent payment is 4 days late" without needing the
    # user to set up a reminder.
    missed_emitted = emit_missed_payment_insights(db, org_id=doc.org_id)

    doc.status = "understood"
    db.commit()

    return {
        "entities_created": len(inserted),
        "rows_parsed": report.rows_parsed,
        "rows_skipped": report.rows_skipped,
        "anomalies": anomalies_emitted,
        "missed_recurring": missed_emitted,
    }


# ---------------------------------------------------------------------------
# PDF / image (invoice / receipt) path — extraction stubbed, understanding real
# ---------------------------------------------------------------------------


def _run_extracted(db: Session, doc: Document) -> dict:
    """Phase-1 path for PDFs / images.

    Order of precedence for `raw_extraction_json`:
      1. If the doc already has a real (non-stub) payload, use it.
      2. Else, if the LLM vision extractor is enabled (ANTHROPIC_API_KEY set),
         run it on the file and save the result.
      3. Else, write a stub payload and mark the doc 'understood' with no
         entities — same behaviour as before the LLM was wired.
    """
    # Stage 1+2: extracting → extracted.
    existing = doc.raw_extraction_json
    if not _looks_real(existing):
        # Try the LLM extractor. The extractor reads the file directly, so we
        # hand it a decrypted copy via `open_document` — for plaintext files
        # this is a zero-copy passthrough.
        if llm_vision.is_enabled():
            hint = _guess_document_type_from_filename(doc.original_filename)
            try:
                with open_document(doc.file_url, doc.encryption_meta) as path:
                    if path.exists():
                        payload = llm_vision.extract_safely(
                            path,
                            file_type=doc.file_type,
                            document_type_hint=hint,
                        )
                        if payload is not None:
                            doc.raw_extraction_json = payload
            except FileNotFoundError:
                logger.warning("file missing on disk for doc %s", doc.id)
        # Still nothing? Fall back to stub so the rest of the pipeline runs.
        if not _looks_real(doc.raw_extraction_json):
            doc.raw_extraction_json = {
                "stub": True,
                "note": (
                    "LLM extractor not enabled (set ANTHROPIC_API_KEY in .env) "
                    "or returned no usable JSON. The parser + understanding path "
                    "can still be tested by manually setting raw_extraction_json."
                ),
                "filename": doc.original_filename,
            }
    doc.status = "extracted"
    db.commit()

    if not _looks_real(doc.raw_extraction_json):
        # No real payload to understand — mark understood with zero entities.
        doc.document_type = _guess_document_type_from_filename(doc.original_filename)
        doc.status = "understood"
        db.commit()
        return {"entities_created": 0, "extraction": "stub"}

    # Stage 3: understood — parse the JSON, resolve, insert, check.
    try:
        draft = parse_extracted_json(
            doc.raw_extraction_json,
            fallback_document_type=_guess_document_type_from_filename(doc.original_filename),
        )
    except ExtractedJSONError as e:
        # Bad payload — record but don't crash the worker.
        doc.error_message = f"ExtractedJSONError: {e}"[:1000]
        doc.status = "understood"
        db.commit()
        return {"entities_created": 0, "extraction": "invalid"}

    entities_created = 0
    anomalies_emitted = 0

    if isinstance(draft, InvoiceDraft):
        invoice = _persist_invoice(db, doc, draft)
        entities_created += 1
        doc.document_type = (
            "sales_invoice" if draft.type == "sales" else "purchase_invoice"
        )
        _ = invoice  # currently no anomaly rule on invoice-level totals

    elif isinstance(draft, ReceiptDraft):
        receipt = _persist_receipt(db, doc, draft)
        entities_created += 1
        doc.document_type = "receipt"
        db.flush()
        if check_receipt(db, doc.org_id, receipt) is not None:
            anomalies_emitted += 1

    elif isinstance(draft, BankStatementDraft):
        # LLM-extracted bank statement from a PDF (e.g. ICICI OpTransactionHistory).
        # Same understanding path as CSV bank statements: resolve each txn's
        # vendor, persist BankTransaction rows, then run anomaly checks.
        doc.document_type = "bank_statement"
        inserted: list[BankTransaction] = []
        for txn_draft in draft.transactions:
            vendor = None
            hint = extract_vendor_hint(txn_draft.description)
            if hint:
                if txn_draft.direction == "debit":
                    vendor = resolve_vendor(db, doc.org_id, hint)
                else:
                    resolve_client(db, doc.org_id, hint)

            inherited_category = (
                vendor.default_expense_category
                if vendor is not None and vendor.default_expense_category
                else None
            )

            txn = BankTransaction(
                org_id=doc.org_id,
                document_id=doc.id,
                txn_date=txn_draft.date,
                description=txn_draft.description,
                amount=txn_draft.amount,
                direction=txn_draft.direction,
                running_balance=txn_draft.balance,
                matched_vendor_id=vendor.id if vendor else None,
                category=inherited_category,
                auto_tagged_by=("vendor_default" if inherited_category else None),
            )
            db.add(txn)
            inserted.append(txn)
        db.flush()
        entities_created = len(inserted)

        # Embed and recurring detection + tag fresh rows.
        _embed_new_txns(db, inserted)
        upsert_patterns(db, org_id=doc.org_id)
        tag_recurring_transactions(db, org_id=doc.org_id, txns=inserted)

        # Anomalies on non-recurring rows only
        for txn in inserted:
            if txn.is_recurring:
                continue
            if check_bank_transaction(db, doc.org_id, txn) is not None:
                anomalies_emitted += 1

        # Missed-payment insights
        emit_missed_payment_insights(db, org_id=doc.org_id)

    doc.status = "understood"
    db.commit()
    return {
        "entities_created": entities_created,
        "anomalies": anomalies_emitted,
        "extraction": "real",
    }


def _persist_invoice(db: Session, doc: Document, draft: InvoiceDraft) -> Invoice:
    vendor_id = None
    client_id = None
    if draft.counterparty:
        if draft.type == "purchase":
            v = resolve_vendor(
                db, doc.org_id, draft.counterparty.name, gstin=draft.counterparty.gstin
            )
            vendor_id = v.id if v else None
        else:
            c = resolve_client(
                db, doc.org_id, draft.counterparty.name, gstin=draft.counterparty.gstin
            )
            client_id = c.id if c else None

    invoice = Invoice(
        org_id=doc.org_id,
        document_id=doc.id,
        type=draft.type,
        invoice_number=draft.invoice_number,
        vendor_id=vendor_id,
        client_id=client_id,
        issue_date=draft.issue_date,
        due_date=draft.due_date,
        subtotal=draft.subtotal,
        tax=draft.tax,
        total=draft.total,
        currency=draft.currency,
        line_items=draft.line_items,
    )
    db.add(invoice)
    db.flush()
    return invoice


def _persist_receipt(db: Session, doc: Document, draft: ReceiptDraft) -> Receipt:
    vendor_id = None
    if draft.counterparty:
        v = resolve_vendor(
            db, doc.org_id, draft.counterparty.name, gstin=draft.counterparty.gstin
        )
        vendor_id = v.id if v else None

    receipt = Receipt(
        org_id=doc.org_id,
        document_id=doc.id,
        vendor_id=vendor_id,
        date=draft.date,
        amount=draft.amount,
        tax=draft.tax,
        category=draft.category,
        payment_mode=draft.payment_mode,
        notes=draft.notes,
    )
    db.add(receipt)
    db.flush()
    return receipt


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _embed_new_txns(db: Session, inserted: list[BankTransaction]) -> None:
    """Embed the descriptions of freshly-inserted bank txns in one batch.
    Safe no-op when sentence-transformers isn't installed."""
    if not inserted or not _embeddings_enabled():
        return
    try:
        texts = [t.description or "" for t in inserted]
        vectors = _embed_batch(texts)
        if vectors is None:
            return
        for txn, vec in zip(inserted, vectors):
            _set_txn_embedding(db, txn_id=txn.id, vector=vec)
        db.flush()
    except Exception:  # noqa: BLE001 — never fail the pipeline on embedding errors
        logger.exception("failed to embed newly inserted txns; continuing")


def _local_path(file_url: str) -> Optional[Path]:
    """Convert a `file://...` URL into a local Path."""
    if not file_url:
        return None
    if file_url.startswith("file://"):
        return Path(file_url[len("file://") :])
    if file_url.startswith("/"):
        return Path(file_url)
    return None


def _looks_real(payload) -> bool:  # type: ignore[no-untyped-def]
    """Return True if `payload` looks like a real extraction (not the stub)."""
    if not isinstance(payload, dict):
        return False
    if payload.get("stub") is True:
        return False
    return bool(payload)


def _guess_document_type_from_filename(filename: str) -> str:
    """Last-resort classifier — used only when extraction is stubbed."""
    f = (filename or "").lower()
    if "statement" in f or "bank" in f:
        return "bank_statement"
    if "invoice" in f:
        return "purchase_invoice"
    if "receipt" in f or "bill" in f:
        return "receipt"
    return "unknown"
