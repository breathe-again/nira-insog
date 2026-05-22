"""Auth endpoints — signup, login, logout, refresh, me, change-password.

All endpoints return access + (httpOnly) refresh tokens. The refresh token is
ALSO returned in the JSON body when `?include_refresh=1` is passed — useful
for CLI clients, never for the browser.

Security notes:
- Login + signup are rate-limited at the router level.
- Failed-login counter is per-user; locks the account for 15 min after 10
  failures.
- Refresh-token rotation: every refresh issues a NEW refresh token and
  revokes the old one. Token-reuse detection: if a revoked token is
  presented, ALL active sessions for that user are revoked (probable theft).
- Audit logging: every auth-relevant event lands in audit_events.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.deps import CurrentUser, get_current_user
from api.schemas import (
    AuthMeOut,
    ChangePasswordIn,
    LoginIn,
    SignupIn,
    TokensOut,
)
from api.security import (
    LOCK_WINDOW,
    MAX_FAILED_LOGINS,
    PasswordPolicyError,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    password_needs_rehash,
    refresh_token_matches,
    verify_password,
)
from common.db import get_db
from common.models import Organization, Session as DbSession, User
from services import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def _as_aware(dt: datetime) -> datetime:
    """Coerce a naive datetime to UTC-aware. SQLAlchemy on SQLite returns naive
    values; Postgres returns aware. Make the arithmetic work on both."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    access_expires_at: datetime,
    refresh_expires_at: datetime,
) -> None:
    settings = get_settings()
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
        "path": "/",
    }
    now = datetime.now(timezone.utc)
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=max(1, int((_as_aware(access_expires_at) - now).total_seconds())),
        **common,
    )
    # Scope the refresh cookie tightly so it's only sent to /api/auth/*.
    refresh_common = {**common, "path": "/api/auth"}
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max(1, int((_as_aware(refresh_expires_at) - now).total_seconds())),
        **refresh_common,
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        ACCESS_COOKIE, path="/", domain=settings.cookie_domain
    )
    response.delete_cookie(
        REFRESH_COOKIE, path="/api/auth", domain=settings.cookie_domain
    )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _create_session(
    db: Session,
    *,
    user: User,
    request: Request,
) -> tuple[DbSession, str]:
    """Mint a fresh server-side refresh session. Returns (Session, plaintext_token)."""
    settings = get_settings()
    refresh_plain = generate_refresh_token()
    sess = DbSession(
        user_id=user.id,
        org_id=user.org_id,
        refresh_token_hash=hash_refresh_token(refresh_plain),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        ip_address=(request.client.host if request.client else None),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_ttl_days),
    )
    db.add(sess)
    db.flush()
    return sess, refresh_plain


def _build_tokens_response(
    db: Session,
    *,
    user: User,
    org: Organization,
    sess: DbSession,
    refresh_plain: str,
    response: Response,
    include_refresh: bool,
) -> TokensOut:
    """Issue access JWT, set cookies, build the response body."""
    access_token, access_exp = issue_access_token(
        user_id=user.id,
        org_id=user.org_id,
        role=user.role,
        session_id=sess.id,
    )
    _set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_plain,
        access_expires_at=access_exp,
        refresh_expires_at=sess.expires_at,
    )

    me = AuthMeOut(
        user_id=user.id,
        org_id=user.org_id,
        email=user.email,
        role=user.role,
        org_name=org.name,
        org_plan=org.plan,
    )
    return TokensOut(
        access_token=access_token,
        access_token_expires_at=access_exp,
        refresh_token=refresh_plain if include_refresh else None,
        user=me,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9-]")


def _make_slug(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower().strip()).strip("-")
    return (slug or "org")[:48]


@router.post("/signup", response_model=TokensOut, status_code=status.HTTP_201_CREATED)
def signup(
    body: SignupIn,
    request: Request,
    response: Response,
    include_refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> TokensOut:
    """Create a new organization + its first user (role=founder), then log in."""
    email_norm = body.email.lower().strip()

    existing = db.execute(
        select(User).where(User.email == email_norm)
    ).scalar_one_or_none()
    if existing is not None:
        # Do NOT reveal whether an email is registered to an unauth'd caller —
        # but we also can't silently succeed here. Return a generic 409.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="signup failed — account already exists or input invalid",
        )

    try:
        pw_hash = hash_password(body.password)
    except PasswordPolicyError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )

    # Mint a unique slug. Collisions are rare with the user name; we suffix
    # a short uuid on collision rather than looping.
    base_slug = _make_slug(body.org_name)
    candidate_slug = base_slug
    if (
        db.execute(
            select(Organization).where(Organization.slug == candidate_slug)
        ).scalar_one_or_none()
        is not None
    ):
        candidate_slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

    org = Organization(name=body.org_name, slug=candidate_slug, plan="trial")
    db.add(org)
    db.flush()

    user = User(
        org_id=org.id,
        email=email_norm,
        role="founder",
        password_hash=pw_hash,
        is_active=True,
    )
    db.add(user)
    db.flush()

    sess, refresh_plain = _create_session(db, user=user, request=request)

    audit.record(
        db,
        event_type="auth.signup",
        org_id=org.id,
        user_id=user.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )

    db.commit()
    db.refresh(user)
    db.refresh(org)

    return _build_tokens_response(
        db,
        user=user,
        org=org,
        sess=sess,
        refresh_plain=refresh_plain,
        response=response,
        include_refresh=include_refresh,
    )


@router.post("/login", response_model=TokensOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    include_refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> TokensOut:
    """Validate email+password. Issue tokens. Increment fail-counter on miss."""
    email_norm = body.email.lower().strip()
    user = db.execute(
        select(User).where(User.email == email_norm)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    # Same-shape failure no matter what (no user-enumeration leak via timing
    # or response shape — verify_password runs a dummy hash for the no-user
    # branch).
    bad_creds = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
    )

    if user is None or not user.is_active:
        verify_password(body.password, None)  # constant-time dummy
        audit.record(
            db,
            event_type="auth.login.failure",
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            meta={"reason": "no_user_or_inactive", "email": email_norm},
            commit=True,
        )
        raise bad_creds

    if user.locked_until is not None and user.locked_until > now:
        audit.record(
            db,
            event_type="auth.login.locked",
            org_id=user.org_id,
            user_id=user.id,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="account temporarily locked — try again later",
        )

    if not verify_password(body.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now + LOCK_WINDOW
            user.failed_login_count = 0  # reset after locking
        audit.record(
            db,
            event_type="auth.login.failure",
            org_id=user.org_id,
            user_id=user.id,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            meta={"reason": "bad_password"},
        )
        db.commit()
        raise bad_creds

    # Success path.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    # Re-hash with current params if old.
    if user.password_hash and password_needs_rehash(user.password_hash):
        try:
            user.password_hash = hash_password(body.password)
        except PasswordPolicyError:
            # User's existing password is grandfathered in — don't refuse login.
            pass

    org = db.get(Organization, user.org_id)
    if org is None:
        # Shouldn't happen — but if the org was deleted, kill the login.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="org not found"
        )

    sess, refresh_plain = _create_session(db, user=user, request=request)

    audit.record(
        db,
        event_type="auth.login.success",
        org_id=user.org_id,
        user_id=user.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )

    db.commit()

    return _build_tokens_response(
        db,
        user=user,
        org=org,
        sess=sess,
        refresh_plain=refresh_plain,
        response=response,
        include_refresh=include_refresh,
    )


@router.post("/refresh", response_model=TokensOut)
def refresh(
    request: Request,
    response: Response,
    include_refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> TokensOut:
    """Rotate the refresh token + issue a new access token.

    Token-reuse detection: if a *revoked* token is presented, all sessions
    for the user are revoked. This catches a stolen-token replay.
    """
    refresh_plain = request.cookies.get(REFRESH_COOKIE)
    if not refresh_plain:
        # Allow CLI clients to POST {"refresh_token": "..."} too.
        try:
            body_json = (request._json if False else None)  # noqa
        except Exception:  # noqa: BLE001
            body_json = None
        # Simpler approach: header.
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            refresh_plain = auth.split(None, 1)[1].strip()
    if not refresh_plain:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing refresh token"
        )

    token_hash = hash_refresh_token(refresh_plain)
    sess = db.execute(
        select(DbSession).where(DbSession.refresh_token_hash == token_hash)
    ).scalar_one_or_none()

    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown refresh token"
        )

    # Token-reuse: presenting a revoked token = probable theft. Burn all the
    # user's sessions.
    if sess.revoked_at is not None:
        all_sessions = db.execute(
            select(DbSession).where(
                DbSession.user_id == sess.user_id, DbSession.revoked_at.is_(None)
            )
        ).scalars().all()
        for s in all_sessions:
            s.revoked_at = datetime.now(timezone.utc)
        audit.record(
            db,
            event_type="auth.refresh_reuse",
            org_id=sess.org_id,
            user_id=sess.user_id,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            meta={"replayed_session_id": str(sess.id)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token reuse detected — re-authenticate",
        )

    now = datetime.now(timezone.utc)
    if _as_aware(sess.expires_at) < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token expired"
        )

    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not active"
        )

    # Constant-time confirm — defends against a hash collision attack
    # (very unlikely but free to add).
    if not refresh_token_matches(sess.refresh_token_hash, refresh_plain):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    # Rotate: revoke the old session, create a new one.
    sess.revoked_at = now
    sess.last_used_at = now

    new_sess, new_refresh = _create_session(db, user=user, request=request)
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="org not found"
        )

    audit.record(
        db,
        event_type="auth.refresh",
        org_id=user.org_id,
        user_id=user.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return _build_tokens_response(
        db,
        user=user,
        org=org,
        sess=new_sess,
        refresh_plain=new_refresh,
        response=response,
        include_refresh=include_refresh,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Revoke the caller's refresh session and clear cookies."""
    if current.session_id is not None:
        sess = db.get(DbSession, current.session_id)
        if sess is not None and sess.revoked_at is None:
            sess.revoked_at = datetime.now(timezone.utc)
            db.flush()
    audit.record(
        db,
        event_type="auth.logout",
        org_id=current.org_id,
        user_id=current.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    _clear_auth_cookies(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AuthMeOut)
def me(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthMeOut:
    """Return the authenticated user + org. Frontend calls this on mount."""
    org = db.get(Organization, current.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="org not found")
    return AuthMeOut(
        user_id=current.id,
        org_id=current.org_id,
        email=current.email,
        role=current.role,
        org_name=org.name,
        org_plan=org.plan,
    )


class _OrgPatchIn(BaseModel):
    """Fields the founder can edit on their own organization."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


@router.patch("/org", response_model=AuthMeOut, summary="Edit org details")
def patch_org(
    body: _OrgPatchIn,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthMeOut:
    """Edit the caller's organization. Only the founder can do this.

    Today only `name` is editable; plan/slug/gstin are managed elsewhere.
    Returns the refreshed AuthMeOut so the frontend can update its context."""
    if current.role != "founder":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the founder can edit org details",
        )
    org = db.get(Organization, current.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="org not found")
    if body.name is not None:
        org.name = body.name.strip()
    db.add(org)
    db.commit()
    db.refresh(org)
    return AuthMeOut(
        user_id=current.id,
        org_id=current.org_id,
        email=current.email,
        role=current.role,
        org_name=org.name,
        org_plan=org.plan,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Verify current password, set new one, and revoke OTHER sessions."""
    if current.is_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="demo user has no password",
        )

    user = db.get(User, current.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="user not found"
        )

    if not verify_password(body.current_password, user.password_hash):
        audit.record(
            db,
            event_type="auth.password_change_failed",
            org_id=user.org_id,
            user_id=user.id,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="wrong current password"
        )

    try:
        user.password_hash = hash_password(body.new_password)
    except PasswordPolicyError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )

    # Revoke all other sessions; keep current one alive so the user stays
    # logged in on this device.
    now = datetime.now(timezone.utc)
    others = db.execute(
        select(DbSession).where(
            DbSession.user_id == user.id,
            DbSession.revoked_at.is_(None),
            DbSession.id != current.session_id,
        )
    ).scalars().all()
    for s in others:
        s.revoked_at = now

    audit.record(
        db,
        event_type="auth.password_change",
        org_id=user.org_id,
        user_id=user.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        meta={"revoked_other_sessions": len(others)},
    )

    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Active sessions — list + per-session revoke
# ---------------------------------------------------------------------------


class SessionInfoOut(BaseModel):
    id: uuid.UUID
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: datetime
    is_current: bool


class SessionListOut(BaseModel):
    sessions: list[SessionInfoOut]
    total: int


@router.get(
    "/sessions",
    response_model=SessionListOut,
    summary="List the caller's active sessions",
)
def list_sessions(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionListOut:
    """Return every non-revoked, non-expired session for the calling user.
    The session matching the request's refresh-token cookie is flagged
    `is_current=True` so the UI can label and protect it."""
    now = datetime.now(timezone.utc)

    # Identify the current session by hashing the refresh cookie. Falls
    # back to None for cookieless clients (CLI) — they'll see no rows
    # tagged as current, which is the safe default.
    current_hash: Optional[str] = None
    refresh_plain = request.cookies.get(REFRESH_COOKIE)
    if refresh_plain:
        try:
            current_hash = hash_refresh_token(refresh_plain)
        except Exception:  # noqa: BLE001
            current_hash = None

    rows = (
        db.execute(
            select(DbSession)
            .where(
                DbSession.user_id == current.id,
                DbSession.revoked_at.is_(None),
                DbSession.expires_at > now,
            )
            .order_by(DbSession.last_used_at.desc().nullslast(), DbSession.created_at.desc())
        )
        .scalars()
        .all()
    )
    out = [
        SessionInfoOut(
            id=s.id,
            user_agent=s.user_agent,
            ip_address=s.ip_address,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            expires_at=s.expires_at,
            is_current=(current_hash is not None and s.refresh_token_hash == current_hash),
        )
        for s in rows
    ]
    return SessionListOut(sessions=out, total=len(out))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a specific session (sign out a remote device)",
)
def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Revoke one session by id. Cannot revoke the current session via this
    endpoint — use POST /logout for that. Cross-user revocation is rejected."""
    sess = db.get(DbSession, session_id)
    if sess is None or sess.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    # Block self-revoke via this endpoint to avoid surprising "you just signed
    # yourself out" behavior from the Settings UI. Use /logout for that.
    refresh_plain = request.cookies.get(REFRESH_COOKIE)
    if refresh_plain:
        try:
            if sess.refresh_token_hash == hash_refresh_token(refresh_plain):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="cannot revoke the current session — use POST /api/auth/logout instead",
                )
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            pass

    if sess.revoked_at is None:
        sess.revoked_at = datetime.now(timezone.utc)
        db.add(sess)
        audit.record(
            db,
            event_type="auth.session_revoke",
            org_id=current.org_id,
            user_id=current.id,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            meta={"target_session_id": str(session_id)},
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/revoke-others",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out every device except this one",
)
def revoke_other_sessions(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Bulk-revoke every session for this user except the one carrying the
    current refresh cookie. Useful for 'sign out everywhere else' after a
    suspected leak."""
    now = datetime.now(timezone.utc)
    current_hash: Optional[str] = None
    refresh_plain = request.cookies.get(REFRESH_COOKIE)
    if refresh_plain:
        try:
            current_hash = hash_refresh_token(refresh_plain)
        except Exception:  # noqa: BLE001
            current_hash = None

    stmt = select(DbSession).where(
        DbSession.user_id == current.id,
        DbSession.revoked_at.is_(None),
    )
    if current_hash:
        stmt = stmt.where(DbSession.refresh_token_hash != current_hash)
    others = db.execute(stmt).scalars().all()
    for s in others:
        s.revoked_at = now
    audit.record(
        db,
        event_type="auth.sessions_revoke_others",
        org_id=current.org_id,
        user_id=current.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        meta={"revoked_count": len(others)},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
