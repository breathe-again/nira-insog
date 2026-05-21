"""File storage helpers.

v0: writes files to a local directory mounted as a Docker volume.
v1 (Phase 1 prod): swaps this module to use S3 — callers don't need to change.

**Encryption at rest:** when `FILE_ENCRYPTION_KEY` is set (see
`services/encryption.py`), files are encrypted in-place after the move and
the file size on disk reflects the ciphertext. The `size_bytes` we return
is the original plaintext size — that's what the user thinks of as "the
size of their file" and what we display in the UI.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

from services.encryption import (
    encrypt_file_in_place,
    is_enabled as encryption_enabled,
    read_decrypted,
)

logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path(os.environ.get("UPLOAD_ROOT", "/app/uploads"))


def ensure_upload_root() -> Path:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return UPLOAD_ROOT


def save_upload(
    org_id: uuid.UUID, original_filename: str, src_path: Path
) -> tuple[str, int, dict]:
    """Move a temp file into permanent storage and (optionally) encrypt it.

    Returns (storage_url, size_bytes, encryption_meta).
    - storage_url is `file://...` for the local backend; S3 will return `s3://...`.
    - size_bytes is the *plaintext* size (what the user uploaded).
    - encryption_meta is the dict to persist on the Document row; empty if
      encryption is disabled.
    """
    ensure_upload_root()
    org_dir = UPLOAD_ROOT / str(org_id)
    org_dir.mkdir(parents=True, exist_ok=True)

    # Preserve the extension; randomize the basename to avoid collisions.
    ext = Path(original_filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dst_path = org_dir / stored_name

    shutil.move(str(src_path), dst_path)
    plaintext_size = dst_path.stat().st_size

    enc_meta: dict = {}
    if encryption_enabled():
        try:
            enc_meta = encrypt_file_in_place(dst_path)
        except Exception:  # noqa: BLE001
            # If encryption fails, fall back to plaintext (with a loud log)
            # rather than losing the upload. Production validate_for_prod will
            # have refused to boot if the key was missing, so this branch is
            # for truly exceptional failures.
            logger.exception(
                "Failed to encrypt upload %s — stored as plaintext", stored_name
            )
            enc_meta = {}

    return (f"file://{dst_path}", plaintext_size, enc_meta)


def read_document_bytes(file_url: str, encryption_meta: dict | None) -> bytes:
    """Read a Document's file and decrypt if needed.

    Workers + extraction code should call this instead of `path.read_bytes()`
    so they don't have to know about encryption.
    """
    path = local_path(file_url)
    if path is None or not path.exists():
        raise FileNotFoundError(file_url)
    return read_decrypted(path, encryption_meta)


def local_path(file_url: str) -> Path | None:
    """Convert a `file://...` URL into a local Path. Returns None if not local."""
    if not file_url:
        return None
    if file_url.startswith("file://"):
        return Path(file_url[len("file://") :])
    if file_url.startswith("/"):
        return Path(file_url)
    return None


# ---------------------------------------------------------------------------
# Decryption context manager — gives workers a transparent path to a
# *plaintext* copy of the file. If encryption is disabled, returns the
# original path (zero-copy). Otherwise writes a temp file and deletes it on
# exit.
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402  (after the top-of-module imports)
import tempfile  # noqa: E402


@contextlib.contextmanager
def open_document(file_url: str, encryption_meta: dict | None):
    """Yield a Path pointing at the plaintext contents of a stored document.

    Usage:
        with open_document(doc.file_url, doc.encryption_meta) as path:
            run_extractor(path)

    If the file is stored plaintext, yields the original path (zero-copy).
    If encrypted, decrypts to a NamedTemporaryFile and yields that, deleting
    on scope exit. The temp file preserves the extension so extractors that
    sniff `path.suffix` (e.g. openpyxl, our image media-type map) still work.
    """
    path = local_path(file_url)
    if path is None:
        raise FileNotFoundError(file_url)
    if not encryption_meta:
        yield path
        return

    plain = read_decrypted(path, encryption_meta)
    suffix = path.suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(plain)
        tmp.flush()
        tmp.close()
        yield Path(tmp.name)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


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
    if ext in {".html", ".htm"}:
        return "html"
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
        if "html" in content_type:
            return "html"
    return "pdf"  # default; will be reclassified by extraction
