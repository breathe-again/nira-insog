"""File storage helpers.

v0: writes files to a local directory mounted as a Docker volume.
v1 (Phase 1 prod): swaps this module to use S3 — callers don't need to change.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

UPLOAD_ROOT = Path(os.environ.get("UPLOAD_ROOT", "/app/uploads"))


def ensure_upload_root() -> Path:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return UPLOAD_ROOT


def save_upload(org_id: uuid.UUID, original_filename: str, src_path: Path) -> tuple[str, int]:
    """Move a temp file into permanent storage.

    Returns (storage_url, size_bytes). The url is `file://...` for the local
    backend; S3 will return `s3://bucket/key/...`.
    """
    ensure_upload_root()
    org_dir = UPLOAD_ROOT / str(org_id)
    org_dir.mkdir(parents=True, exist_ok=True)

    # Preserve the extension; randomize the basename to avoid collisions.
    ext = Path(original_filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dst_path = org_dir / stored_name

    shutil.move(str(src_path), dst_path)
    size = dst_path.stat().st_size
    return (f"file://{dst_path}", size)


def detect_file_type(filename: str, content_type: str | None = None) -> str:
    """Return a value from FileType based on filename / content type."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        return "image"
    if ext == ".csv":
        return "csv"
    if ext in {".xlsx", ".xls"}:
        return "xlsx"
    # Fallbacks via content type
    if content_type:
        if "pdf" in content_type:
            return "pdf"
        if content_type.startswith("image/"):
            return "image"
        if "csv" in content_type:
            return "csv"
        if "spreadsheet" in content_type or "excel" in content_type:
            return "xlsx"
    return "pdf"  # default; will be reclassified by extraction
