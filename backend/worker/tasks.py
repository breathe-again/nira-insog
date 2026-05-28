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
from services.parsers.tally_xml import is_tally_xml, parse_tally_xml
from services.parsers.extracted_json import (
    BankStatementDraft,
    ComplianceDraft,
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
    if doc.file_type == "tally":
        return _run_tally_xml(db, doc)
    if doc.file_type == "xlsx":
        # Peek inside: is it a Tally Trial Balance? If yes, route to the
        # canonical-ledger connector (Phase 2). Otherwise fall through to
        # the generic XLSX extractor.
        if _is_tally_trial_balance_xlsx(doc):
            return _run_tally_trial_balance(db, doc)
        return _run_extracted(db, doc)
    if doc.file_type in {"pdf", "image", "html"}:
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

    # Stage 2: extracted — record the raw parse summary including the
    # balance-reconciliation check (P2).  If reconciliation fails, the doc
    # lands with a low parse_confidence; the inbox surfaces a "Review parse"
    # badge so the user can audit before trusting the data.
    doc.document_type = "bank_statement"
    doc.raw_extraction_json = {
        "kind": "bank_csv",
        "rows_total": report.rows_total,
        "rows_parsed": report.rows_parsed,
        "rows_skipped": report.rows_skipped,
        "errors": report.errors[:20],
        "sample": [d.as_dict() for d in drafts[:3]],
        # Balance reconciliation block — serialized as JSON-safe primitives.
        "reconciliation": {
            "opening_balance": (
                str(report.opening_balance) if report.opening_balance is not None else None
            ),
            "closing_balance": (
                str(report.closing_balance) if report.closing_balance is not None else None
            ),
            "computed_closing": (
                str(report.computed_closing) if report.computed_closing is not None else None
            ),
            "delta": (
                str(report.balance_delta) if report.balance_delta is not None else None
            ),
            "reconciled": report.reconciled,
        },
        "parse_confidence": report.parse_confidence,
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
# Tally XML path — Day Book / Voucher exports
# ---------------------------------------------------------------------------


def _run_tally_xml(db: Session, doc: Document) -> dict:
    """Parse a Tally Day Book / Voucher export and emit normalized rows.

    Vouchers route as:
      Payment / Receipt / Contra → BankTransaction
      Sales                       → Invoice (type='sales')
      Purchase                    → Invoice (type='purchase')
      Journal / Stock Journal     → skipped (not a cash event)

    Each voucher's PARTYLEDGERNAME is run through resolve_vendor/resolve_client
    so the same counterparty in Tally maps to the same Vendor/Client row that
    existing bank-statement uploads created — automatic merging.
    """
    from decimal import Decimal as _Dec

    with open_document(doc.file_url, doc.encryption_meta) as path:
        if not path.exists():
            raise FileNotFoundError(f"file not on disk: {doc.file_url}")
        content = path.read_bytes()

    if not is_tally_xml(content):
        # Not actually a Tally export — record gracefully and stop.
        doc.raw_extraction_json = {
            "kind": "xml",
            "note": "uploaded .xml is not a Tally export (no <TALLYREQUEST> / <VOUCHER> tags found)",
        }
        doc.document_type = "unknown"
        doc.status = "extracted"
        db.commit()
        doc.status = "understood"
        db.commit()
        return {"entities_created": 0}

    parsed = parse_tally_xml(content)

    # Stage 2: record extraction summary.
    doc.document_type = "tally_export"
    doc.raw_extraction_json = {
        "kind": "tally_xml",
        "vouchers": parsed.voucher_count,
        "bank_txns_parsed": len(parsed.bank_txns),
        "invoices_parsed": len(parsed.invoices),
        "skipped": parsed.skipped[:20],
        "errors": parsed.errors[:20],
    }
    doc.status = "extracted"
    db.commit()

    # Stage 3: understand — persist + resolve counterparties.
    inserted_txns: list[BankTransaction] = []
    inserted_invoices = 0

    for d in parsed.bank_txns:
        vendor = None
        client = None
        if d.counterparty_name:
            if d.direction == "debit":
                vendor = resolve_vendor(db, doc.org_id, d.counterparty_name)
            else:
                client = resolve_client(db, doc.org_id, d.counterparty_name)
        inherited_category = (
            vendor.default_expense_category
            if vendor is not None and vendor.default_expense_category
            else None
        )
        txn = BankTransaction(
            org_id=doc.org_id,
            document_id=doc.id,
            txn_date=d.txn_date,
            description=d.description,
            amount=_Dec(d.amount),
            direction=d.direction,
            running_balance=d.running_balance,
            matched_vendor_id=vendor.id if vendor else None,
            matched_client_id=client.id if client else None,
            category=inherited_category,
            auto_tagged_by=("vendor_default" if inherited_category else None),
        )
        db.add(txn)
        inserted_txns.append(txn)

    for inv_draft in parsed.invoices:
        vendor_id = None
        client_id = None
        if inv_draft.vendor_name:
            if inv_draft.type == "purchase":
                v = resolve_vendor(db, doc.org_id, inv_draft.vendor_name)
                vendor_id = v.id if v else None
            else:
                c = resolve_client(db, doc.org_id, inv_draft.vendor_name)
                client_id = c.id if c else None
        invoice = Invoice(
            org_id=doc.org_id,
            document_id=doc.id,
            type=inv_draft.type,
            invoice_number=inv_draft.invoice_number,
            vendor_id=vendor_id,
            client_id=client_id,
            issue_date=inv_draft.issue_date,
            subtotal=_Dec(inv_draft.total),
            tax=_Dec("0"),
            total=_Dec(inv_draft.total),
            currency="INR",
            line_items=None,
        )
        db.add(invoice)
        inserted_invoices += 1

    db.flush()

    # Stage 3a: embed the freshly-inserted bank txns for semantic search.
    _embed_new_txns(db, inserted_txns)

    # Stage 3b: re-learn recurring patterns + tag.
    upsert_patterns(db, org_id=doc.org_id)
    tag_recurring_transactions(db, org_id=doc.org_id, txns=inserted_txns)

    # Stage 3c: anomaly detection on the new bank rows only.
    anomalies_emitted = 0
    for txn in inserted_txns:
        if txn.is_recurring:
            continue
        result = check_bank_transaction(db, doc.org_id, txn)
        if result is not None:
            anomalies_emitted += 1

    doc.status = "understood"
    db.commit()

    # Stage 3d (Phase-2 dual-write): post each Day Book row into the
    # canonical ledger as a balanced movement entry — IF the tenant has
    # opted in via the per-tenant feature flag. Disabled by default
    # because period coordination with Trial Balance opening balances
    # needs careful tuning (TB closing already includes the Day Book
    # movements — naive dual-write would double-count).
    #
    # Tracking: ARCHITECTURE_PLAN.md → C1b. Will be flipped on per tenant
    # once we have a clear "TB-as-opening vs TB-as-closing" indicator on
    # uploads, plus reconciliation_findings emission for mismatches.
    try:
        from services.tenant_settings import is_feature_enabled

        if is_feature_enabled(db, doc.org_id, "canonical_day_book", default=False):
            from services.canonical import _post_day_book_to_canonical  # type: ignore[attr-defined]

            _post_day_book_to_canonical(db, doc, parsed, inserted_txns)
    except Exception:  # noqa: BLE001
        # The canonical dual-write must never break the legacy pipeline.
        logger.exception("Day Book canonical dual-write failed; legacy data preserved")

    return {
        "entities_created": len(inserted_txns) + inserted_invoices,
        "bank_txns": len(inserted_txns),
        "invoices": inserted_invoices,
        "vouchers_skipped": len(parsed.skipped),
        "anomalies": anomalies_emitted,
    }


# ---------------------------------------------------------------------------
# Tally Trial Balance (XLSX) path — canonical ledger ingestion (Phase 2)
# ---------------------------------------------------------------------------


def _is_tally_trial_balance_xlsx(doc: Document) -> bool:
    """Cheap detection: does this XLSX look like a Tally Trial Balance?

    We accept either:
      * Filename hints — anything matching "trial[\\s-]?bal" / "TrialBal" /
        "trialbalance" / "balance sheet (tally)"
      * Content hints — A1 contains the company name + A4 starts with
        "Trial Balance"

    Filename check is O(1); content check opens the workbook only when
    the filename is generic ("export.xlsx"). The bytes are not retained.
    """
    fname = (doc.original_filename or "").lower()
    if any(kw in fname for kw in ("trialbal", "trial bal", "trial-bal", "trial_bal")):
        return True

    try:
        import openpyxl

        with open_document(doc.file_url, doc.encryption_meta) as path:
            if not path.exists():
                return False
            wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
            ws = wb.active
            if ws is None:
                return False
            # First five rows for header signature
            for r in range(1, 6):
                cell = ws.cell(row=r, column=1).value
                if cell and "trial balance" in str(cell).lower():
                    return True
            return False
    except Exception:  # noqa: BLE001
        return False


def _run_tally_trial_balance(db: Session, doc: Document) -> dict:
    """Ingest a Tally Trial Balance XLSX into the canonical ledger.

    Delegates parsing + posting to the
    `services.connectors.tally_trial_balance` connector. We bridge to
    that connector here so the upload pipeline + Celery retries +
    document state machine all keep working unchanged.
    """
    from services.connectors.tally_trial_balance import ingest_trial_balance_xlsx

    with open_document(doc.file_url, doc.encryption_meta) as path:
        if not path.exists():
            raise FileNotFoundError(f"file not on disk: {doc.file_url}")
        path_str = str(path)

        result = ingest_trial_balance_xlsx(
            db,
            org_id=doc.org_id,
            file_path=path_str,
            document_id=doc.id,
            entity_id=doc.entity_id,
            display_name=f"Tally Trial Balance ({doc.original_filename or 'upload'})",
            original_filename=doc.original_filename,
        )

    # Mirror result into the document row so the inbox shows what happened.
    doc.document_type = "trial_balance"
    doc.raw_extraction_json = {
        "kind": "tally_trial_balance",
        "rows_processed": result.detail.get("rows_processed", 0),
        "accounts_upserted": result.accounts_upserted,
        "transactions_written": result.transactions_written,
        "ledger_entries_written": result.ledger_entries_written,
        "total_debit": result.detail.get("total_debit"),
        "total_credit": result.detail.get("total_credit"),
        "company_name": result.detail.get("company_name"),
        "period_text": result.detail.get("period_text"),
        "as_of": result.detail.get("as_of"),
        "suspense_count": result.detail.get("suspense_count", 0),
        "suspense_samples": result.detail.get("suspense_samples", []),
        "errors": result.errors[:20],
    }
    doc.status = "extracted"
    db.commit()
    doc.status = "understood"
    db.commit()
    doc.status = "indexed"
    doc.processed_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "entities_created": result.accounts_upserted,
        "transactions": result.transactions_written,
        "ledger_entries": result.ledger_entries_written,
        "errors": len(result.errors),
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

    if isinstance(draft, ComplianceDraft):
        # Government / tax / regulatory filing. We keep the raw payload on the
        # document for searchability but do NOT create Invoice/Receipt rows —
        # there's nothing to put on the dashboard.
        doc.document_type = "compliance"

    elif isinstance(draft, InvoiceDraft):
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
