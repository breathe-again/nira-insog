"""Password hashing + JWT helpers.

Security choices (with rationale):

- **argon2id** for passwords. OWASP-preferred for new systems; resistant to
  GPU/ASIC attacks. Default tuning from argon2-cffi is fine for an SMB
  finance app at our scale (≈50ms per hash on a t3.medium).
- **JWT HS256** for access tokens. Symmetric is simpler than RS256 and we
  only have one issuer. Access TTL is short (30 min) so leaks are bounded.
- **Opaque refresh tokens** stored as sha256 hashes in the `sessions` table —
  revocable, rotatable. Never store plaintext refresh tokens server-side.
- **Constant-time comparison** for hash/token lookups (argon2 + hmac.compare_digest).
- **No password complexity gymnastics** — we enforce minimum length (12) and
  NIST-style "common-password" rejection rather than the old "1 uppercase
  1 digit 1 symbol" rule that pushes users toward `Password1!`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import get_settings

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

# Single hasher with defaults. argon2-cffi's defaults are:
#   time_cost=2, memory_cost=64MB, parallelism=8, hash_len=16, salt_len=16.
# Re-hash if parameters change in the future via `hasher.check_needs_rehash`.
_HASHER = PasswordHasher()


# Tiny embedded list of the very-most-common passwords. A real deploy should
# wire HaveIBeenPwned k-anonymity API for the long tail.
_BAD_PASSWORDS = frozenset(
    [
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty",
        "qwerty123",
        "letmein",
        "admin",
        "welcome",
        "welcome1",
        "iloveyou",
        "monkey",
        "abc123",
        "111111",
        "000000",
        "passw0rd",
    ]
)


class PasswordPolicyError(ValueError):
    """Raised when a password violates the policy."""


def validate_password_policy(password: str) -> None:
    """Enforce the password policy. Raise PasswordPolicyError on violation.

    Policy:
      - 12+ characters (NIST 800-63B suggests >= 8 with no complexity rules;
        we go higher because the work factor is cheap and the value is high).
      - Not on the short common-passwords list.
      - No null bytes or control characters that the argon2 hasher would
        choke on.
    """
    if not isinstance(password, str):
        raise PasswordPolicyError("password must be a string")
    if len(password) < 12:
        raise PasswordPolicyError("password must be at least 12 characters")
    if len(password) > 256:
        raise PasswordPolicyError("password must be at most 256 characters")
    if password.lower() in _BAD_PASSWORDS:
        raise PasswordPolicyError("password is too common — pick something else")
    if any(ord(c) < 0x20 and c not in ("\t",) for c in password):
        raise PasswordPolicyError("password contains invalid control characters")


def hash_password(password: str) -> str:
    """Hash a plaintext password using argon2id. Validates policy first."""
    validate_password_policy(password)
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a plaintext password against a stored hash.

    Returns False on any failure (mismatch, bad hash, missing hash) — never
    raises so callers can treat this as a boolean.
    """
    if not password_hash:
        # Run a dummy hash anyway to keep timing consistent and stop the
        # "user-enumeration via response time" trick.
        try:
            _HASHER.verify(
                "$argon2id$v=19$m=65536,t=3,p=4$"
                "salt-padding-padding-padding=$"
                "hashdummy-padding-padding-padding-padding-padding=",
                password,
            )
        except Exception:  # noqa: BLE001
            pass
        return False
    try:
        _HASHER.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 — malformed hash etc.
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Returns True if the stored hash uses old parameters and should be re-hashed."""
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# JWT — access tokens
# ---------------------------------------------------------------------------


class TokenError(ValueError):
    """Raised when a JWT can't be decoded or fails validation."""


def issue_access_token(
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str,
    session_id: uuid.UUID,
) -> tuple[str, datetime]:
    """Mint a new access JWT. Returns (token, expires_at)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_ttl_minutes)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "sid": str(session_id),  # links the access token to a refresh session
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": settings.app_name,
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, exp


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access JWT. Raise TokenError on any failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
            options={"require": ["exp", "sub", "org", "sid", "typ"]},
        )
    except jwt.PyJWTError as e:
        raise TokenError(f"invalid access token: {e}") from e

    if payload.get("typ") != "access":
        raise TokenError("token is not an access token")
    return payload


# ---------------------------------------------------------------------------
# Refresh tokens — opaque random strings, stored server-side as sha256 hashes
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """Generate a cryptographically-strong refresh token (URL-safe).

    32 random bytes → 256 bits of entropy. URL-safe base64 → 43 chars.
    """
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """sha256 hex digest of a refresh token (for server-side storage)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_matches(stored_hash: str, presented_token: str) -> bool:
    """Constant-time compare of a presented refresh token to a stored hash."""
    return hmac.compare_digest(stored_hash, hash_refresh_token(presented_token))


# ---------------------------------------------------------------------------
# Account-lockout policy
# ---------------------------------------------------------------------------

# After N failed logins, lock the account for LOCK_WINDOW. Failed counter
# resets on a successful login.
MAX_FAILED_LOGINS = 10
LOCK_WINDOW = timedelta(minutes=15)
