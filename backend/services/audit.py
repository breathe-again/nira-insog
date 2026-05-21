"""Audit log helper.

Writes `audit_events` rows. Callers pass the event type, the (optional)
acting user + org, and a metadata dict. We never raise — auditing must not
break the calling flow.

Event types we track (extend as needed):

  Auth:
    - auth.signup
    - auth.login.success
    - auth.login.failure
    - auth.login.locked
    - auth.logout
    - auth.refresh
    - auth.password_change

  Documents:
    - doc.upload
    - doc.delete
    - doc.edit                       (PATCH on document_type / vendor / category)

  Vendors:
    - vendor.create
    - vendor.rename
    - vendor.merge
    - vendor.alias_add

  Insights:
    - insight.dismiss
    - insight.mute_vendor

The metadata field stores anything else useful — old/new value diffs, the
specific field that changed, the http path, etc. Keep it small.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from common.models import AuditEvent

logger = logging.getLogger(__name__)


def record(
    db: Session,
    *,
    event_type: str,
    org_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    commit: bool = False,
) -> None:
    """Record an audit event. Never raises.

    By default we don't commit — the caller's transaction handles that, which
    keeps the audit row atomic with the action it describes. Pass commit=True
    for fire-and-forget events (failed login, etc) where the surrounding
    transaction will be rolled back.
    """
    try:
        event = AuditEvent(
            org_id=org_id,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=(ip_address or "")[:64] or None,
            user_agent=(user_agent or "")[:500] or None,
            meta=meta or None,
        )
        db.add(event)
        if commit:
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("audit.record failed for event_type=%s", event_type)
        if commit:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
