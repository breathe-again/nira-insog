"""Hybrid retrieval for the /search endpoint.

Architecture (the production-grade upgrade described as P3 in our roadmap):

         ┌──────────────────┐
         │   user query     │
         └────────┬─────────┘
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
  ┌───────────┐       ┌──────────────┐
  │ BM25 / FTS │       │  Dense vec    │
  │ ts_rank_cd │       │  pgvector     │
  │   top-50  │       │   top-50      │
  └─────┬─────┘       └──────┬────────┘
        │                     │
        └──────────┬──────────┘
                   ▼
         ┌─────────────────────┐
         │ Reciprocal Rank     │
         │   Fusion (RRF)      │
         │   merged top-100    │
         └─────────┬───────────┘
                   ▼
         ┌─────────────────────┐
         │ Cohere Rerank (opt) │
         │ cross-encoder pass  │
         │   top-N             │
         └─────────┬───────────┘
                   ▼
              final hits

The Cohere reranker is opt-in (env COHERE_API_KEY). When unset, the
pipeline returns the RRF-fused list directly — still much better than
either leg alone.

Why this beats vanilla pgvector:
  - "AWS Invoice 03.05.2025" finds an exact filename match via BM25 even
    though MiniLM-L6 embeddings don't preserve surface tokens well.
  - "credit card payment" still finds semantically-similar rows like
    "Visa POS settlement" via dense embeddings even when no shared word.
  - Reranker breaks ties when the query is ambiguous, scoring each
    (query, candidate) pair with a cross-encoder.

Returns the same dict shape as services.embeddings.search_txns_by_query
so the API route can swap in without changing its response schema.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.embeddings import (
    embed_text,
    fully_enabled as dense_enabled,
    is_pgvector_available,
)

logger = logging.getLogger(__name__)


# RRF constant — `k` in the Reciprocal Rank Fusion formula:
#     score(d) = Σ over rankers r of  1 / (k + rank_r(d))
# k=60 is the standard recommendation from the original RRF paper.
_RRF_K = 60

# How many candidates each leg fetches before fusion.  Fusion picks the
# best N across both legs, so over-fetching here pays for recall.
_LEG_LIMIT = 50


# ---------------------------------------------------------------------------
# Postgres FTS (BM25-flavored) leg
# ---------------------------------------------------------------------------


def _bm25_bank_txns(
    db: Session, *, org_id: uuid.UUID, query: str, limit: int
) -> list[dict]:
    """Top-N bank_transactions ranked by Postgres ts_rank_cd against
    description_tsv.  Uses plainto_tsquery so users can type natural
    English without operator syntax."""
    rows = db.execute(
        text(
            """
            SELECT id, txn_date, amount, direction, description,
                   matched_vendor_id, category,
                   ts_rank_cd(description_tsv, plainto_tsquery('english', :q)) AS score
            FROM bank_transactions
            WHERE org_id = :org
              AND description_tsv @@ plainto_tsquery('english', :q)
            ORDER BY score DESC
            LIMIT :lim
            """
        ),
        {"org": str(org_id), "q": query, "lim": limit},
    ).all()
    return [
        {
            "source": "bank_txn",
            "id": str(r[0]),
            "txn_date": r[1].isoformat() if r[1] else None,
            "amount": str(r[2]) if r[2] is not None else None,
            "direction": r[3],
            "description": r[4],
            "matched_vendor_id": str(r[5]) if r[5] else None,
            "category": r[6],
            "bm25_score": float(r[7]) if r[7] is not None else 0.0,
            "distance": None,
        }
        for r in rows
    ]


def _bm25_invoices(
    db: Session, *, org_id: uuid.UUID, query: str, limit: int
) -> list[dict]:
    """Top-N invoices matched by invoice_number tsvector OR by vendor name
    tsvector (joined).  Vendor matches usually dominate the ranking for
    natural-language queries like 'AWS'."""
    rows = db.execute(
        text(
            """
            SELECT
                i.id, i.invoice_number, i.issue_date, i.total, i.type,
                i.document_id, i.vendor_id,
                v.name AS vendor_name,
                GREATEST(
                    ts_rank_cd(i.number_tsv, plainto_tsquery('english', :q)),
                    COALESCE(
                        ts_rank_cd(v.search_tsv, plainto_tsquery('english', :q)),
                        0
                    ) * 1.5
                ) AS score
            FROM invoices i
            LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE i.org_id = :org
              AND (
                i.number_tsv @@ plainto_tsquery('english', :q)
                OR v.search_tsv @@ plainto_tsquery('english', :q)
              )
            ORDER BY score DESC
            LIMIT :lim
            """
        ),
        {"org": str(org_id), "q": query, "lim": limit},
    ).all()
    return [
        {
            "source": "invoice",
            "id": str(r[0]),
            "invoice_number": r[1],
            "txn_date": r[2].isoformat() if r[2] else None,
            "amount": str(r[3]) if r[3] is not None else None,
            "direction": "debit" if r[4] == "purchase" else "credit",
            "description": f"Invoice {r[1]}" + (f" — {r[7]}" if r[7] else ""),
            "document_id": str(r[5]) if r[5] else None,
            "matched_vendor_id": str(r[6]) if r[6] else None,
            "vendor_name": r[7],
            "category": None,
            "bm25_score": float(r[8]) if r[8] is not None else 0.0,
            "distance": None,
        }
        for r in rows
    ]


def _bm25_receipts(
    db: Session, *, org_id: uuid.UUID, query: str, limit: int
) -> list[dict]:
    """Top-N receipts matched by notes_tsv OR vendor name."""
    rows = db.execute(
        text(
            """
            SELECT
                r.id, r.date, r.amount, r.notes, r.document_id, r.vendor_id,
                r.category, v.name AS vendor_name,
                GREATEST(
                    COALESCE(ts_rank_cd(r.notes_tsv, plainto_tsquery('english', :q)), 0),
                    COALESCE(ts_rank_cd(v.search_tsv, plainto_tsquery('english', :q)), 0) * 1.5
                ) AS score
            FROM receipts r
            LEFT JOIN vendors v ON v.id = r.vendor_id
            WHERE r.org_id = :org
              AND (
                r.notes_tsv @@ plainto_tsquery('english', :q)
                OR v.search_tsv @@ plainto_tsquery('english', :q)
              )
            ORDER BY score DESC
            LIMIT :lim
            """
        ),
        {"org": str(org_id), "q": query, "lim": limit},
    ).all()
    return [
        {
            "source": "receipt",
            "id": str(r[0]),
            "txn_date": r[1].isoformat() if r[1] else None,
            "amount": str(r[2]) if r[2] is not None else None,
            "direction": "debit",
            "description": (r[3] or r[7] or "Receipt")[:200],
            "document_id": str(r[4]) if r[4] else None,
            "matched_vendor_id": str(r[5]) if r[5] else None,
            "category": r[6],
            "vendor_name": r[7],
            "bm25_score": float(r[8]) if r[8] is not None else 0.0,
            "distance": None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Dense (pgvector) leg
# ---------------------------------------------------------------------------


def _dense_bank_txns(
    db: Session, *, org_id: uuid.UUID, query: str, limit: int
) -> list[dict]:
    """Reuse the existing pgvector cosine search — but cap nothing here,
    threshold filtering happens after fusion."""
    if not dense_enabled(db):
        return []
    vec = embed_text(query)
    if vec is None:
        return []
    arr = "[" + ",".join(f"{v:.7f}" for v in vec) + "]"
    rows = db.execute(
        text(
            """
            SELECT id, txn_date, amount, direction, description,
                   matched_vendor_id, category,
                   description_embedding <=> (:vec)::vector AS distance
            FROM bank_transactions
            WHERE org_id = :org
              AND description_embedding IS NOT NULL
            ORDER BY description_embedding <=> (:vec)::vector
            LIMIT :lim
            """
        ),
        {"org": str(org_id), "vec": arr, "lim": limit},
    ).all()
    return [
        {
            "source": "bank_txn",
            "id": str(r[0]),
            "txn_date": r[1].isoformat() if r[1] else None,
            "amount": str(r[2]) if r[2] is not None else None,
            "direction": r[3],
            "description": r[4],
            "matched_vendor_id": str(r[5]) if r[5] else None,
            "category": r[6],
            "distance": float(r[7]) if r[7] is not None else None,
            "bm25_score": None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def _fuse(*ranked_lists: list[dict]) -> list[dict]:
    """Merge multiple ranked lists into one via Reciprocal Rank Fusion.

    For each (source, id) appearing in any list, score = Σ 1/(k + rank).
    Higher score wins.  Stable across leg-count.
    """
    fused: dict[tuple[str, str], dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            key = (item.get("source", "?"), item["id"])
            score = 1.0 / (_RRF_K + rank + 1)
            if key in fused:
                fused[key]["_rrf"] += score
            else:
                copy = dict(item)
                copy["_rrf"] = score
                fused[key] = copy
    return sorted(fused.values(), key=lambda d: -d["_rrf"])


# ---------------------------------------------------------------------------
# Cohere reranker (optional)
# ---------------------------------------------------------------------------


def _cohere_enabled() -> bool:
    return bool(os.environ.get("COHERE_API_KEY"))


def _cohere_rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """Cross-encoder rerank via Cohere's /rerank endpoint.

    Safe to call with no API key — returns candidates unchanged.  Failures
    (network blips, quota exceeded) also fall back gracefully.
    """
    if not _cohere_enabled() or not candidates:
        return candidates[:top_n]
    try:
        import cohere
    except ImportError:
        logger.warning("cohere SDK not installed; skipping rerank")
        return candidates[:top_n]

    try:
        client = cohere.ClientV2(os.environ["COHERE_API_KEY"])
        docs = [c.get("description") or "" for c in candidates]
        resp = client.rerank(
            model="rerank-english-v3.0",
            query=query,
            documents=docs,
            top_n=min(top_n, len(candidates)),
        )
        reordered: list[dict] = []
        for result in resp.results:
            item = dict(candidates[result.index])
            item["rerank_score"] = float(result.relevance_score)
            reordered.append(item)
        return reordered
    except Exception as e:  # noqa: BLE001
        logger.warning("cohere rerank failed (%s) — returning RRF order", e)
        return candidates[:top_n]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def hybrid_search(
    db: Session,
    *,
    org_id: uuid.UUID,
    query: str,
    limit: int = 20,
    max_distance: float = 1.0,
) -> list[dict]:
    """Run BM25 (FTS) + dense (pgvector) over bank txns, invoices, and
    receipts; fuse with RRF; optionally rerank with Cohere; return top-N.

    `max_distance` still applies but only to candidates that came in via
    the dense leg with a distance score.  BM25-only candidates pass through
    regardless (a strong literal match is meaningful even with no vector).
    """
    query = (query or "").strip()
    if not query:
        return []

    # ---- BM25 legs (always available) ----
    bm25_bank = _bm25_bank_txns(db, org_id=org_id, query=query, limit=_LEG_LIMIT)
    bm25_inv = _bm25_invoices(db, org_id=org_id, query=query, limit=_LEG_LIMIT)
    bm25_rcpt = _bm25_receipts(db, org_id=org_id, query=query, limit=_LEG_LIMIT)

    # ---- Dense leg (when pgvector + embedding model are configured) ----
    dense = _dense_bank_txns(db, org_id=org_id, query=query, limit=_LEG_LIMIT)

    # ---- RRF fusion across all four ranked lists ----
    fused = _fuse(bm25_bank, bm25_inv, bm25_rcpt, dense)

    # Apply distance cutoff ONLY to candidates that came purely from the
    # dense leg (no BM25 score).  Mixed candidates always survive — a
    # literal match is signal regardless of cosine distance.
    filtered = []
    for c in fused:
        bm = c.get("bm25_score") or 0.0
        dist = c.get("distance")
        if bm > 0 or dist is None or dist <= max_distance:
            filtered.append(c)

    # ---- Optional cross-encoder rerank ----
    reranked = _cohere_rerank(query, filtered, top_n=limit)

    # Drop internal scoring keys before returning (keep distance + rerank
    # for the UI to render match-strength badges).
    for c in reranked:
        c.pop("_rrf", None)
        c.pop("bm25_score", None)
    return reranked
