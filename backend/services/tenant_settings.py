"""Per-tenant configuration store.

Replaces process-wide env vars for anything that varies per organization:
Setu API keys, Tally URLs, GSP credentials, feature flags. Stored in the
`tenant_settings` table; secret values are Fernet-encrypted at rest.

Falls back to environment variables transparently when a key isn't found
in the DB. This means today's single-tenant operation (Quantta) keeps
working with the existing env vars while we incrementally migrate to the
DB; new SaaS tenants seed the table and the env vars become defaults.

Usage::

    from services.tenant_settings import (
        read_tenant_setting, write_tenant_setting, is_feature_enabled,
    )

    tally_url = read_tenant_setting(db, org_id, "tally.aws_url",
                                    default=None)

    write_tenant_setting(db, org_id, "tally.aws_url",
                         "https://tally.quantta.io:9000",
                         description="Tally HTTP server on AWS")

    if is_feature_enabled(db, org_id, "approvals"):
        ...

Naming convention: dot-separated, lowercased, source-prefixed.
  - tally.aws_url
  - setu.fiu_handle
  - setu.api_key                  (encrypted)
  - gstn.gsp_provider             ('mastergst' | 'iris' | 'cleartax')
  - gstn.gsp_api_key              (encrypted)
  - features.approvals_enabled
  - features.canonical_ledger     (controls dual-read fallback)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import TenantSetting
from services import encryption

logger = logging.getLogger(__name__)


# Map of tenant-setting key → env-var name that holds the global default.
# When a tenant has no row for the key, we look in this env var.
# Keep additions explicit so a typo in a key doesn't silently leak a value
# from an unrelated env var.
_ENV_FALLBACKS: dict[str, str] = {
    "tally.aws_url": "TALLY_AWS_URL",
    "tally.company_name": "TALLY_COMPANY_NAME",
    "setu.fiu_handle": "SETU_FIU_HANDLE",
    "setu.api_key": "SETU_API_KEY",
    "setu.base_url": "SETU_BASE_URL",
    "gstn.gsp_provider": "GSTN_GSP_PROVIDER",
    "gstn.gsp_api_key": "GSTN_GSP_API_KEY",
    "gstn.gstin": "GSTN_GSTIN",
    "traces.tan": "TRACES_TAN",
    "traces.api_key": "TRACES_API_KEY",
    "cohere.api_key": "COHERE_API_KEY",
    "anthropic.api_key": "ANTHROPIC_API_KEY",
    # Feature flags — DB row wins; env fallback for backwards compat.
    "features.approvals_enabled": "FEATURE_APPROVALS_ENABLED",
    "features.canonical_ledger": "FEATURE_CANONICAL_LEDGER",
    "features.multi_entity_ui": "FEATURE_MULTI_ENTITY_UI",
}


# Keys whose values should be encrypted at rest. Anything looking like a
# secret should be added here. read_tenant_setting decrypts transparently.
_ENCRYPTED_KEYS: set[str] = {
    "setu.api_key",
    "gstn.gsp_api_key",
    "traces.api_key",
    "cohere.api_key",
    "anthropic.api_key",
}


def _is_encrypted_key(key: str) -> bool:
    return key in _ENCRYPTED_KEYS or key.endswith(".api_key") or key.endswith(".secret")


def read_tenant_setting(
    db: Session,
    org_id: uuid.UUID,
    key: str,
    default: Any = None,
) -> Any:
    """Read a tenant setting. Returns the value or `default`.

    Lookup order:
      1. tenant_settings table (decrypted if `encrypted=true`)
      2. _ENV_FALLBACKS env var
      3. `default`

    Returns the JSON-deserialized value (so JSON-typed settings work
    naturally — bool/int/str/list/dict). Plain string env-var fallbacks
    are returned as-is.
    """
    row = db.execute(
        select(TenantSetting).where(
            TenantSetting.org_id == org_id, TenantSetting.key == key
        )
    ).scalar_one_or_none()

    if row is not None:
        raw = row.value_json
        if row.encrypted and isinstance(raw, dict) and "cipher" in raw:
            # Encrypted value stored as {"cipher": "...", "scheme": "fernet-v1"}
            try:
                cipher_bytes = raw["cipher"].encode("ascii")
                plain = encryption.decrypt_bytes(cipher_bytes, {"scheme": raw.get("scheme", "fernet-v1")})
                # The plaintext is a UTF-8 JSON-encoded value
                return json.loads(plain.decode("utf-8"))
            except Exception:
                logger.exception("Failed to decrypt tenant setting %s/%s", org_id, key)
                return default
        return raw

    # Fallback to env var
    env_name = _ENV_FALLBACKS.get(key)
    if env_name:
        env_value = os.environ.get(env_name)
        if env_value is not None:
            # Heuristic: booleans for feature flags
            if key.startswith("features.") or env_name.startswith("FEATURE_"):
                return env_value.lower() in ("1", "true", "yes", "on")
            return env_value

    return default


def write_tenant_setting(
    db: Session,
    org_id: uuid.UUID,
    key: str,
    value: Any,
    description: Optional[str] = None,
    encrypted: Optional[bool] = None,
    commit: bool = True,
) -> TenantSetting:
    """Upsert a tenant setting.

    `value` is JSON-encoded for storage. If `encrypted` is True (or the key
    is in _ENCRYPTED_KEYS), the JSON-encoded value is Fernet-encrypted and
    stored as `{"cipher": "...", "scheme": "fernet-v1"}`.
    """
    should_encrypt = encrypted if encrypted is not None else _is_encrypted_key(key)
    if should_encrypt and not encryption.is_enabled():
        raise RuntimeError(
            f"Cannot store encrypted setting {key!r}: FILE_ENCRYPTION_KEY not set"
        )

    if should_encrypt:
        plain = json.dumps(value).encode("utf-8")
        cipher, meta = encryption.encrypt_bytes(plain)
        stored: dict = {"cipher": cipher.decode("ascii"), "scheme": meta.get("scheme", "fernet-v1")}
    else:
        stored = value

    existing = db.execute(
        select(TenantSetting).where(
            TenantSetting.org_id == org_id, TenantSetting.key == key
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.value_json = stored
        existing.encrypted = should_encrypt
        if description is not None:
            existing.description = description
        row = existing
    else:
        row = TenantSetting(
            org_id=org_id,
            key=key,
            value_json=stored,
            encrypted=should_encrypt,
            description=description,
        )
        db.add(row)

    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def delete_tenant_setting(
    db: Session, org_id: uuid.UUID, key: str, commit: bool = True
) -> bool:
    """Delete a tenant setting. Returns True if a row was removed."""
    row = db.execute(
        select(TenantSetting).where(
            TenantSetting.org_id == org_id, TenantSetting.key == key
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    if commit:
        db.commit()
    return True


def is_feature_enabled(
    db: Session,
    org_id: uuid.UUID,
    feature: str,
    default: bool = False,
) -> bool:
    """Check `features.<feature>_enabled`. Boolean-coerces env-var values."""
    key = f"features.{feature}_enabled"
    val = read_tenant_setting(db, org_id, key, default=default)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.lower() in ("1", "true", "yes", "on")
    return default


def list_tenant_settings(
    db: Session, org_id: uuid.UUID, prefix: Optional[str] = None
) -> list[TenantSetting]:
    """List all tenant settings for an org. Encrypted values are NOT
    decrypted — this is for admin/debug UIs that want to show what's set
    without leaking secrets.
    """
    q = select(TenantSetting).where(TenantSetting.org_id == org_id)
    if prefix:
        q = q.where(TenantSetting.key.like(f"{prefix}%"))
    return list(db.execute(q.order_by(TenantSetting.key)).scalars())
