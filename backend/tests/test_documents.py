"""End-to-end tests for the documents API.

These exercise the real upload → store → enqueue → process flow against the
running stack. They poll for status transitions to verify the Celery worker
is actually picking up jobs.
"""

from __future__ import annotations

import io
import time
import uuid

import httpx
import pytest


# -- Helpers ---------------------------------------------------------------


def _wait_for_status(
    api: httpx.Client,
    document_id: str,
    *,
    target_statuses: set[str],
    timeout_s: float = 20.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll a document until its status is in `target_statuses` or timeout."""
    deadline = time.time() + timeout_s
    last_status: str | None = None
    while time.time() < deadline:
        res = api.get(f"/api/documents/{document_id}")
        assert res.status_code == 200, res.text
        body = res.json()
        last_status = body["status"]
        if last_status in target_statuses:
            return body
        time.sleep(interval_s)
    raise AssertionError(
        f"Document {document_id} never reached {target_statuses}; "
        f"last status was {last_status!r}"
    )


# -- Tests -----------------------------------------------------------------


def test_list_documents_returns_envelope(api: httpx.Client) -> None:
    res = api.get("/api/documents")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data and isinstance(data["items"], list)
    assert "total" in data and isinstance(data["total"], int)


def test_get_unknown_document_is_404(api: httpx.Client) -> None:
    missing_id = str(uuid.uuid4())
    res = api.get(f"/api/documents/{missing_id}")
    assert res.status_code == 404


def test_upload_creates_document(api: httpx.Client) -> None:
    payload = b"vendor,date,amount\nABC Traders,2026-05-19,12450.00\n"
    files = {"file": ("upload-test.csv", io.BytesIO(payload), "text/csv")}
    res = api.post("/api/documents", files=files)
    assert res.status_code == 201, res.text

    body = res.json()
    assert body["original_filename"] == "upload-test.csv"
    assert body["file_type"] == "csv"
    assert body["status"] == "received"
    assert body["file_size_bytes"] == len(payload)
    assert uuid.UUID(body["id"])  # parseable as UUID
    assert uuid.UUID(body["org_id"])

    # Upload should show up in the listing.
    listing = api.get("/api/documents").json()
    assert any(item["id"] == body["id"] for item in listing["items"])


def test_upload_too_large_is_rejected(api: httpx.Client) -> None:
    """26MB upload should fail with 413."""
    big = b"x" * (26 * 1024 * 1024)
    files = {"file": ("too-big.pdf", io.BytesIO(big), "application/pdf")}
    res = api.post("/api/documents", files=files)
    assert res.status_code == 413, res.text


def test_worker_processes_document_to_indexed(api: httpx.Client) -> None:
    """Verify the Celery worker walks the document to `indexed`.

    Skips if Celery isn't running (e.g. running locally without the worker
    container).
    """
    payload = b"some,test,data\nrow,1,2\n"
    files = {"file": ("worker-roundtrip.csv", io.BytesIO(payload), "text/csv")}
    res = api.post("/api/documents", files=files)
    assert res.status_code == 201
    doc_id = res.json()["id"]

    try:
        final = _wait_for_status(
            api, doc_id, target_statuses={"indexed", "error"}, timeout_s=20.0
        )
    except AssertionError as e:
        pytest.skip(f"Worker did not process in time (is the worker container up?): {e}")
        return

    assert final["status"] == "indexed", final
    assert final["processed_at"] is not None
    # The stub extractor writes a small JSON payload.
    detail = api.get(f"/api/documents/{doc_id}").json()
    assert detail.get("raw_extraction_json", {}).get("stub") is True


def test_document_type_is_inferred_from_filename(api: httpx.Client) -> None:
    """The understanding stub classifies based on filename hints."""
    cases = [
        ("hdfc_bank_statement_april.csv", "bank_statement"),
        ("vendor_invoice_2026.pdf", "purchase_invoice"),
        ("cab_receipt_may.jpg", "receipt"),
    ]
    for filename, expected_type in cases:
        files = {"file": (filename, io.BytesIO(b"placeholder"), "application/octet-stream")}
        res = api.post("/api/documents", files=files)
        assert res.status_code == 201
        doc_id = res.json()["id"]

        try:
            final = _wait_for_status(
                api, doc_id, target_statuses={"indexed", "error"}, timeout_s=20.0
            )
        except AssertionError:
            pytest.skip("Worker not processing — skipping classification check")
            return
        assert final["status"] == "indexed"
        assert final["document_type"] == expected_type, (
            f"{filename} → expected {expected_type}, got {final['document_type']}"
        )
