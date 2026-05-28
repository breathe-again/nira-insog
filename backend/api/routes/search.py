"""Hybrid search over a tenant's transactions + invoices + receipts.

  GET /api/search?q=<query>&limit=<n>

Now backed by services.search_hybrid which combines:
  1. BM25 / Postgres FTS leg  — exact-token + invoice-number matches
  2. Dense / pgvector leg     — semantic similarity for typos / synonyms
  3. Reciprocal Rank Fusion   — merges both ranks robustly
  4. Cohere Rerank (optional) — cross-encoder pass for fine ordering

When COHERE_API_KEY is unset the reranker no-ops; the RRF-fused list goes
straight through. When pgvector or sentence-transformers aren't available
the dense leg drops silently and BM25-only results return — search degrades
gracefully instead of failing.

Tenant strictly enforced — every leg filters by `current_org_id`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from services.search_hybrid import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


SearchSource = Literal["bank_txn", "invoice", "receipt"]


class SearchHitOut(BaseModel):
    id: str
    source: SearchSource = "bank_txn"
    txn_date: Optional[str] = None
    amount: Optional[str] = None
    direction: Optional[str] = None
    description: str
    matched_vendor_id: Optional[str] = None
    category: Optional[str] = None
    distance: Optional[float] = None  # 0 = identical, 2 = opposite
    rerank_score: Optional[float] = None  # populated when Cohere ran
    # Extra fields for invoice/receipt hits.
    document_id: Optional[str] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None


class SearchOut(BaseModel):
    query: str
    enabled: bool
    count: int
    hits: list[SearchHitOut]


# Dense-leg distance cutoff. Candidates from BM25 with bm25_score > 0
# bypass this — a strong literal match is always meaningful.
_DEFAULT_MAX_DISTANCE = 1.0


@router.get("", response_model=SearchOut, summary="Hybrid transaction search")
def search(
    q: str = Query(..., min_length=1, max_length=200, description="Natural-language search query"),
    limit: int = Query(default=20, ge=1, le=100),
    max_distance: float = Query(
        default=_DEFAULT_MAX_DISTANCE,
        ge=0.0,
        le=2.0,
        description="Cosine distance cutoff for the dense leg. Lower = stricter.",
    ),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> SearchOut:
    """Returns up to `limit` hits ranked by hybrid retrieval.

    Each hit carries:
      - distance     (cosine, present if dense leg saw it)
      - rerank_score (Cohere cross-encoder score 0.0-1.0, when reranker ran)
    The frontend uses either to render a 'Strong/Good/Loose/Weak match' badge.
    """
    raw = hybrid_search(
        db, org_id=org_id, query=q, limit=limit, max_distance=max_distance,
    )

    hits = [
        SearchHitOut(
            id=h["id"],
            source=h.get("source", "bank_txn"),
            txn_date=h.get("txn_date"),
            amount=h.get("amount"),
            direction=h.get("direction"),
            description=h.get("description", ""),
            matched_vendor_id=h.get("matched_vendor_id"),
            category=h.get("category"),
            distance=h.get("distance"),
            rerank_score=h.get("rerank_score"),
            document_id=h.get("document_id"),
            vendor_name=h.get("vendor_name"),
            invoice_number=h.get("invoice_number"),
        )
        for h in raw
    ]

    return SearchOut(
        query=q,
        enabled=True,  # BM25 always works; dense is silent fallback
        count=len(hits),
        hits=hits,
    )
