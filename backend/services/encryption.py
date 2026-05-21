"""File encryption at rest.

Uses Fernet (AES-128 in CBC mode + HMAC-SHA256, with PKCS7 padding and a
versioned token format). This isn't the latest AEAD — but it's stdlib-quality
(via the `cryptography` package), constant-time, and easy to rotate. Trading
AES-128 for the simpler interface is acceptable for an SMB finance MVP; we
upgrade to KMS-backed AES-GCM when we move file storage to S3 (Phase F).

Threat model:
- Defends against: a server attacker who can read disk but not the
  application's environment (key is held only in env / secrets manager).
- Does NOT defend against: an attacker with full app access — by definition
  the running app needs to decrypt to process the file.

If `FILE_ENCRYPTION_KEY` is unset (dev convenience), the helpers no-op so the
file is stored plaintext. The startup checker logs a loud warning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Mirror of api/config.Settings.file_encryption_key. We read it directly here
# to avoid coupling the storage layer to the FastAPI settings object — keeps
# this module importable from Celery workers too.
_KEY_ENV = "FILE_ENCRYPTION_KEY"


def is_enabled() -> bool:
    return bool(os.environ.get(_KEY_ENV))


def _get_fernet():
    """Lazy-construct the Fernet instance. Raises ValueError if key invalid."""
    from cryptography.fernet import Fernet  # imported lazily

    key = os.environ.get(_KEY_ENV)
    if not key:
        raise ValueError(f"{_KEY_ENV} is not set")
    # Fernet.__init__ raises on invalid keys (wrong length, bad base64).
    return Fernet(key.encode("ascii"))


def encrypt_bytes(plain: bytes) -> tuple[bytes, dict]:
    """Encrypt bytes. Returns (ciphertext, metadata-to-persist).

    metadata is a small dict the caller stores alongside the file (e.g. in
    Document.encryption_meta) so we know how to decrypt later when we rotate
    keys.
    """
    if not is_enabled():
        return plain, {}
    f = _get_fernet()
    cipher = f.encrypt(plain)
    meta = {
        "scheme": "fernet-v1",
        "alg": "AES-128-CBC+HMAC-SHA256",
        # We don't include the key itself; if you have multiple key versions
        # you'd track key_id and use MultiFernet for rolling rotation.
        "key_id": "v1",
    }
    return cipher, meta


def decrypt_bytes(cipher: bytes, meta: Optional[dict]) -> bytes:
    """Decrypt bytes. If `meta` is empty/None, returns the input unchanged
    (i.e. the file was stored plaintext)."""
    if not meta:
        return cipher
    scheme = meta.get("scheme")
    if scheme != "fernet-v1":
        raise ValueError(f"unknown encryption scheme: {scheme!r}")
    f = _get_fernet()
    return f.decrypt(cipher)


def encrypt_file_in_place(path: Path) -> dict:
    """Read `path`, encrypt, overwrite with ciphertext. Returns metadata."""
    if not is_enabled():
        return {}
    data = path.read_bytes()
    cipher, meta = encrypt_bytes(data)
    path.write_bytes(cipher)
    return meta


def read_decrypted(path: Path, meta: Optional[dict]) -> bytes:
    """Read a (possibly encrypted) file and return plaintext bytes."""
    raw = path.read_bytes()
    return decrypt_bytes(raw, meta)


def warn_if_disabled() -> None:
    """Log a loud warning at startup if encryption is disabled.

    Production startup also fails outright (see Settings.validate_for_prod).
    """
    if not is_enabled():
        logger.warning(
            "FILE ENCRYPTION DISABLED — uploaded documents are stored plaintext. "
            "Set FILE_ENCRYPTION_KEY (Fernet.generate_key()) to enable."
        )
