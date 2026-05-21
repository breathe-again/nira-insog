"""Insights endpoints — list, filter by severity, dismiss."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.deps import current_org_id, current_user_id
from api.schemas import InsightListOut, InsightOut
from common.db import get_db
from common.models import Insight

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("", response_model=InsightListOut, summary="List insights")
def list_insights(
    severity: Optional[str] = Query(default=None, description="info | attention | urgent"),
    type: Optional[str] = Query(default=None, description="Insight type, e.g. vendor_amount_anomaly"),
    include_dismissed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> InsightListOut:
    stmt = select(Insight).where(Insight.org_id == org_id)
    count_stmt = select(func.count()).select_from(Insight).where(Insight.org_id == org_id)

    if severity:
        stmt = stmt.where(Insight.severity == severity)
        count_stmt = count_stmt.where(Insight.severity == severity)
    if type:
        stmt = stmt.where(Insight.type == type)
        count_stmt = count_stmt.where(Insight.type == type)
    if not include_dismissed:
        stmt = stmt.where(Insight.dismissed_at.is_(None))
        count_stmt = count_stmt.where(Insight.dismissed_at.is_(None))

    total = db.scalar(count_stmt)
    stmt = stmt.order_by(desc(Insight.created_at)).limit(limit).offset(offset)
    rows = list(db.scalars(stmt).all())

    return InsightListOut(
        items=[InsightOut.model_validate(r) for r in rows],
        total=int(total or 0),
    )


@router.post("/{insight_id}/dismiss", response_model=InsightOut, summary="Dismiss an insight")
def dismiss_insight(
    insight_id: uuid.UUID,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
    user_id: uuid.UUID = Depends(current_user_id),
) -> InsightOut:
    insight = db.get(Insight, insight_id)
    if insight is None or insight.org_id != org_id:
        raise HTTPException(status_code=404, detail="insight not found")

    if insight.dismissed_at is None:
        insight.dismissed_at = datetime.now(timezone.utc)
        insight.dismissed_by = user_id
        db.commit()
        db.refresh(insight)

    return InsightOut.model_validate(insight)
