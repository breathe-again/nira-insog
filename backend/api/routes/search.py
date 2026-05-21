"""Semantic search over a tenant's transactions.

  GET /api/search?q=<query>&limit=<n>

Embeds the query, runs a cosine-similarity search over bank_transactions in
the caller's org, returns the top matches.

Use cases:
  - "AWS" → finds every txn where the description even loosely mentions
    Amazon Web Services, even if the LLM wrote "Amazon" or "AMZN-CLOUD"
    or "INF/NEFT/.../AWS-INVOICE-...".
  - "rent" → finds all rent payments regardless of how the landlord's
    name was written in the description.
  - "salary" → all payroll outflows across descriptions like "INF/NEFT/.../
    Madhusmita" or "SALARY-MARCH-2026" or "TRFR TO:KOUSTAV MAITY".

Tenant strictly enforced — query is scoped to `current_org_id`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from services.embeddings import fully_enabled, search_txns_by_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchHitOut(BaseModel):
    id: str
    txn_date: Optional[str] = None
    amount: Optional[str] = None
    direction: Optional[str] = None
    description: str
    matched_vendor_id: Optional[str] = None
    category: Optional[str] = None
    distance: Optional[float] = None  # 0 = identical, 2 = opposite


class SearchOut(BaseModel):
    query: str
    enabled: bool
    count: int
    hits: list[SearchHitOut]


@router.get("", response_model=SearchOut, summary="Semantic transaction search")
def search(
    q: str = Query(..., min_length=1, max_length=200, description="Natural-language search query"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> SearchOut:
    if not fully_enabled(db):
        return SearchOut(query=q, enabled=False, count=0, hits=[])
    hits = search_txns_by_query(db, org_id=org_id, query=q, limit=limit)
    return SearchOut(
        query=q,
        enabled=True,
        count=len(hits),
        hits=[SearchHitOut(**h) for h in hits],
    )
