"""FastAPI dependencies — JWT-based auth + multi-tenant guard.

How auth flows:
  1. Browser presents either:
     - Authorization: Bearer <access_jwt> header (for API clients), OR
     - access_token cookie (for the React SPA), OR
     - both — the header wins.
  2. We decode the JWT (HS256). On any failure → 401.
  3. We load the user from the DB by sub claim; verify the org and session
     are still active. On any failure → 401.
  4. We expose the user/org to routes via `current_user` / `current_org_id`.

When `DEMO_MODE=1` (local dev only — refused in prod), we bypass the JWT
check entirely and use the demo user/org. This keeps the existing single-
tenant Phase-1 workflow alive for the founder's laptop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.db import get_db
from common.models import Organization, Session as DbSession, User

from .config import get_settings
from .security import TokenError, decode_access_token

# Deterministic UUIDs for the demo seed — used only when DEMO_MODE=1.
DEMO_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEMO_USER_EMAIL = "founder@demo.local"


@dataclass(slots=True)
class CurrentUser:
    """Lightweight view of the authenticated principal — passed into routes."""

    id: uuid.UUID
    org_id: uuid.UUID
    role: str
    email: str
    session_id: Optional[uuid.UUID]
    is_demo: bool


def ensure_demo_org(db: Session) -> Organization:
    """Create the demo organization + a founder user if they don't exist.

    Idempotent. Safe to call at every API startup."""
    org = db.get(Organization, DEMO_ORG_ID)
    if org is None:
        org = Organization(id=DEMO_ORG_ID, name="Demo Org", slug="demo", plan="trial")
        db.add(org)
        db.flush()

    user = db.get(User, DEMO_USER_ID)
    if user is None:
        existing = db.execute(
            select(User).where(User.email == DEMO_USER_EMAIL)
        ).scalar_one_or_none()
        if existing is None:
            user = User(
                id=DEMO_USER_ID,
                org_id=org.id,
                email=DEMO_USER_EMAIL,
                role="founder",
                is_active=True,
                # No password_hash — the demo account is reachable only via
                # DEMO_MODE bypass, never via the login form.
            )
            db.add(user)
            db.flush()

    db.commit()
    return org


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _extract_token(authorization: Optional[str], cookie_token: Optional[str]) -> Optional[str]:
    """Pull the access token out of either the Authorization header or cookie.

    Header wins; cookie is the fallback for the SPA.
    """
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    if cookie_token:
        return cookie_token
    return None


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Resolve the authenticated user.

    Raises HTTPException(401) when the caller is unauthenticated or the
    token is invalid/expired/revoked.
    """
    settings = get_settings()

    # ---- DEMO_MODE bypass (local dev only) ----------------------------
    if settings.demo_mode and not settings.is_prod:
        ensure_demo_org(db)
        return CurrentUser(
            id=DEMO_USER_ID,
            org_id=DEMO_ORG_ID,
            role="founder",
            email=DEMO_USER_EMAIL,
            session_id=None,
            is_demo=True,
        )

    token = _extract_token(authorization, access_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )

    try:
        payload = decode_access_token(token)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": 'Bearer realm="api", error="invalid_token"'},
        )

    try:
        user_id = uuid.UUID(payload["sub"])
        org_id = uuid.UUID(payload["org"])
        session_id = uuid.UUID(payload["sid"])
    except (KeyError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"bad token claims: {e}"
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or inactive"
        )

    if user.org_id != org_id:
        # The JWT and the DB disagree on the user's org → reject. Could
        # happen if a user was moved between orgs and an old token is replayed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="org mismatch"
        )

    # Refresh-session must be alive (not revoked, not expired).
    sess = db.get(DbSession, session_id)
    if sess is None or sess.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="session revoked"
        )
    sess_exp = sess.expires_at
    if sess_exp.tzinfo is None:
        sess_exp = sess_exp.replace(tzinfo=timezone.utc)
    if sess_exp < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired"
        )

    # Stash for downstream middleware / audit log.
    request.state.user_id = user.id
    request.state.org_id = user.org_id
    request.state.session_id = sess.id

    return CurrentUser(
        id=user.id,
        org_id=user.org_id,
        role=user.role,
        email=user.email,
        session_id=sess.id,
        is_demo=False,
    )


def current_user_id(user: CurrentUser = Depends(get_current_user)) -> uuid.UUID:
    return user.id


def current_org_id(user: CurrentUser = Depends(get_current_user)) -> uuid.UUID:
    return user.org_id


def require_role(*roles: str):
    """Dependency factory: require the caller to hold one of these roles.

    Example:
        @router.post("/admin/x", dependencies=[Depends(require_role("founder"))])
    """

    allowed = {r.lower() for r in roles}

    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role.lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {user.role!r} not permitted (need one of {sorted(allowed)})",
            )
        return user

    return _checker


# Re-export get_db so route modules only need to import from one place.
DbSessionDep = Depends(get_db)
