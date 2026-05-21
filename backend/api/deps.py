"""FastAPI dependencies.

v0: there is no real auth, so every request operates on a single 'demo' org.
When auth lands, `current_org_id` will resolve from the session/JWT instead.
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.db import get_db
from common.models import Organization, User

# Deterministic UUIDs so we don't have to plumb them around in v0.
DEMO_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEMO_USER_EMAIL = "founder@demo.local"


def ensure_demo_org(db: Session) -> Organization:
    """Create the demo organization + a founder user if they don't exist."""
    org = db.get(Organization, DEMO_ORG_ID)
    if org is None:
        org = Organization(id=DEMO_ORG_ID, name="Demo Org", plan="trial")
        db.add(org)
        db.flush()

    user = db.get(User, DEMO_USER_ID)
    if user is None:
        # Avoid email collision in case it already exists from a prior insert.
        existing = db.execute(
            select(User).where(User.email == DEMO_USER_EMAIL)
        ).scalar_one_or_none()
        if existing is None:
            user = User(
                id=DEMO_USER_ID,
                org_id=org.id,
                email=DEMO_USER_EMAIL,
                role="founder",
            )
            db.add(user)
            db.flush()

    db.commit()
    return org


def current_org_id() -> uuid.UUID:
    """Return the current org id. v0: always the demo org."""
    return DEMO_ORG_ID


def current_user_id() -> uuid.UUID:
    """Return the current user id. v0: always the demo user."""
    return DEMO_USER_ID


# Re-export get_db so route modules only need to import from one place.
DbSession = Depends(get_db)
