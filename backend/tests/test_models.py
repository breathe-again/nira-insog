"""Unit-level checks that don't need the running stack.

These verify the SQLAlchemy schema is consistent and the storage helper
behaves correctly. Useful as a fast sanity check during refactors.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from common.db import Base
from common import models  # noqa: F401  (registers tables)
from common.enums import (
    DocumentStatus,
    DocumentType,
    FileType,
    OrgPlan,
)
from common.storage import detect_file_type, save_upload


def test_all_expected_tables_are_registered() -> None:
    expected = {
        "organizations",
        "users",
        "vendors",
        "clients",
        "bank_accounts",
        "documents",
        "bank_transactions",
        "invoices",
        "receipts",
        "insights",
        "feedback_events",
    }
    assert expected.issubset(Base.metadata.tables.keys())


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("statement.pdf", "pdf"),
        ("scan.PNG", "image"),
        ("photo.jpeg", "image"),
        ("txns.csv", "csv"),
        ("books.xlsx", "xlsx"),
        ("legacy.xls", "xlsx"),
    ],
)
def test_detect_file_type(filename: str, expected: str) -> None:
    assert detect_file_type(filename) == expected


def test_enums_have_expected_members() -> None:
    assert OrgPlan.TRIAL == "trial"
    assert DocumentStatus.INDEXED == "indexed"
    assert DocumentType.BANK_STATEMENT == "bank_statement"
    assert FileType.PDF == "pdf"


def test_save_upload_moves_file_and_returns_size(tmp_path: Path) -> None:
    # Override the storage root with a temp dir.
    import common.storage as storage

    original = storage.UPLOAD_ROOT
    try:
        storage.UPLOAD_ROOT = tmp_path / "uploads"
        src = tmp_path / "src.pdf"
        src.write_bytes(b"fake pdf bytes" * 10)
        size_in = src.stat().st_size

        url, size_out = save_upload(uuid.uuid4(), "test.pdf", src)

        assert size_out == size_in
        assert url.startswith("file://")
        # Source file is moved away.
        assert not src.exists()
    finally:
        storage.UPLOAD_ROOT = original
        shutil.rmtree(tmp_path, ignore_errors=True)
