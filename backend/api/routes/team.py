"""Team management endpoints — list members, create invite links, accept.

Flow:
  1. Founder hits POST /api/team/invites with {email, role}.
  2. We return a token + shareable URL — the founder copies it and sends to
     the teammate over whatever channel (email/Slack/WhatsApp).
  3. Teammate clicks the link → frontend lands on /accept-invite/<token>
     → calls GET /api/team/invites/by-token/<token> to verify, then
     POST /api/team/invites/<token>/accept with {password} to set up.

No email sending here (yet). The founder shares the link manually — keeps
the integration surface small and the security model auditable.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api import audit
from api.deps import CurrentUser, get_current_user
from api.security import PasswordPolicyError, hash_password, validate_password_policy
from common.db import get_db
from common.models import Invite, Organization, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/team", tags=["team"])


INVITE_TTL_DAYS = 7


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MemberOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    # The full shareable URL — frontend shows a "Copy link" button.
    invite_url: str
    token: str


class TeamOverviewOut(BaseModel):
    members: list[MemberOut]
    pending_invites: list[InviteOut]


class CreateInviteIn(BaseModel):
    email: EmailStr
    role: Literal["member", "accountant"] = "member"


class AcceptInviteIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class InviteCheckOut(BaseModel):
    org_name: str
    email: str
    role: str
    expires_at: datetime
    already_accepted: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_invite_url(token: str, request: Request) -> str:
    """Construct the user-facing invite link based on the request's origin.

    We assume the frontend is served from the same origin as the API in
    production (e.g. https://insig.nirabalance.com). When that's not true,
    the FRONTEND_ORIGIN env var (read elsewhere) would override — keeping
    this dumb for now since both this app's frontend and API live together.
    """
    # The frontend route is /accept-invite/<token>.
    origin = str(request.base_url).rstrip("/")
    # Strip any /api suffix that proxies leave behind.
    if origin.endswith("/api"):
        origin = origin[: -len("/api")]
    return f"{origin}/accept-invite/{token}"


def _invite_to_out(inv: Invite, request: Request) -> InviteOut:
    return InviteOut(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        created_at=inv.created_at,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        revoked_at=inv.revoked_at,
        invite_url=_build_invite_url(inv.token, request),
        token=inv.token,
    )


# ---------------------------------------------------------------------------
# Overview — members + pending invites in one call
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=TeamOverviewOut,
    summary="List org members + pending invites",
)
def overview(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamOverviewOut:
    now = datetime.now(timezone.utc)

    users = list(
        db.scalars(
            select(User)
            .where(User.org_id == current.org_id, User.is_active.is_(True))
            .order_by(User.created_at.asc())
        )
    )
    members = [
        MemberOut(
            id=u.id,
            email=u.email,
            role=u.role,
            is_active=u.is_active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in users
    ]

    pending = list(
        db.scalars(
            select(Invite)
            .where(
                Invite.org_id == current.org_id,
                Invite.accepted_at.is_(None),
                Invite.revoked_at.is_(None),
                Invite.expires_at > now,
            )
            .order_by(Invite.created_at.desc())
        )
    )
    return TeamOverviewOut(
        members=members,
        pending_invites=[_invite_to_out(i, request) for i in pending],
    )


# ---------------------------------------------------------------------------
# Create invite
# ---------------------------------------------------------------------------


@router.post(
    "/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shareable invite link",
)
def create_invite(
    body: CreateInviteIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteOut:
    """Only the founder can create invites. Returns the shareable URL —
    the founder copies it and sends to the teammate over their channel."""
    if current.role != "founder":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the founder can invite teammates",
        )

    email = body.email.strip().lower()
    now = datetime.now(timezone.utc)

    # Block inviting someone who's already a member.
    existing_user = db.scalar(
        select(User).where(
            User.org_id == current.org_id,
            User.email == email,
            User.is_active.is_(True),
        )
    )
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{email} is already a member of this workspace",
        )

    # Replace any existing pending invite for the same email — keeps the
    # unique partial index happy + avoids "which link is current?" confusion.
    existing_invite = db.scalar(
        select(Invite).where(
            Invite.org_id == current.org_id,
            Invite.email == email,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
    )
    if existing_invite is not None:
        existing_invite.revoked_at = now
        db.add(existing_invite)
        db.flush()

    token = secrets.token_hex(32)
    inv = Invite(
        org_id=current.org_id,
        email=email,
        role=body.role,
        token=token,
        created_by=current.id,
        expires_at=now + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(inv)
    audit.record(
        db,
        event_type="team.invite_create",
        org_id=current.org_id,
        user_id=current.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        meta={"email": email, "role": body.role},
    )
    db.commit()
    db.refresh(inv)
    return _invite_to_out(inv, request)


# ---------------------------------------------------------------------------
# Revoke an invite
# ---------------------------------------------------------------------------


@router.delete(
    "/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invite",
)
def revoke_invite(
    invite_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.role != "founder":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the founder can revoke invites",
        )
    inv = db.get(Invite, invite_id)
    if inv is None or inv.org_id != current.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    if inv.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot revoke an already-accepted invite",
        )
    if inv.revoked_at is None:
        inv.revoked_at = datetime.now(timezone.utc)
        db.add(inv)
        audit.record(
            db,
            event_type="team.invite_revoke",
            org_id=current.org_id,
            user_id=current.id,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            meta={"invite_id": str(invite_id)},
        )
        db.commit()
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Accept-flow: public endpoints (no auth required — they create auth)
# ---------------------------------------------------------------------------


# Sub-router that doesn't require auth — the accept page is reached by
# someone who isn't logged in yet.
public_router = APIRouter(prefix="/api/team", tags=["team"])


@public_router.get(
    "/invites/by-token/{token}",
    response_model=InviteCheckOut,
    summary="Verify an invite token (called by the accept page)",
)
def check_invite(
    token: str,
    db: Session = Depends(get_db),
) -> InviteCheckOut:
    """Public endpoint — let the accept page display the org name + email
    before the user sets a password. Doesn't reveal anything sensitive
    beyond what was already in the link."""
    inv = db.scalar(select(Invite).where(Invite.token == token))
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    now = datetime.now(timezone.utc)
    if inv.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite revoked")
    if inv.expires_at < now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite expired")
    org = db.get(Organization, inv.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="org not found")
    return InviteCheckOut(
        org_name=org.name,
        email=inv.email,
        role=inv.role,
        expires_at=inv.expires_at,
        already_accepted=inv.accepted_at is not None,
    )


@public_router.post(
    "/invites/{token}/accept",
    summary="Accept an invite — creates a User in the org",
)
def accept_invite(
    token: str,
    body: AcceptInviteIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Convert a pending invite into an active user. The invite carries the
    email + org; the recipient just sets a password.

    Returns the same TokensOut shape as /login so the frontend can drop
    the user straight onto the dashboard after acceptance."""
    inv = db.scalar(select(Invite).where(Invite.token == token))
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    now = datetime.now(timezone.utc)
    if inv.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite revoked")
    if inv.expires_at < now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite expired")
    if inv.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="invite already accepted — please log in instead",
        )

    # Block accepting if a user with this email already exists somewhere.
    existing = db.scalar(select(User).where(User.email == inv.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this email is already registered — log in and ask the founder to re-add you",
        )

    # Password strength check — uses the same policy as signup/change-password.
    try:
        validate_password_policy(body.password)
    except PasswordPolicyError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    user = User(
        org_id=inv.org_id,
        email=inv.email,
        role=inv.role,
        password_hash=hash_password(body.password),
        is_active=True,
        email_verified_at=now,  # accepting the link is verification
    )
    db.add(user)
    db.flush()

    inv.accepted_at = now
    inv.accepted_by_user_id = user.id
    db.add(inv)

    audit.record(
        db,
        event_type="team.invite_accept",
        org_id=inv.org_id,
        user_id=user.id,
        ip_address=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        meta={"invite_id": str(inv.id)},
    )
    db.commit()

    # Return a tiny success payload — frontend redirects to /login (or we
    # could auto-mint a session here; keeping it simple by asking them to
    # log in once, which exercises the password flow before they're "in").
    return {
        "ok": True,
        "user_id": str(user.id),
        "org_id": str(inv.org_id),
        "email": user.email,
        "message": "Account created. Please log in with your email + password.",
    }
