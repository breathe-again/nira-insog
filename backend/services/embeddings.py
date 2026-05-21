"""Description embeddings — Tier-2 semantic learning.

Architecture (May 2026):

  - Model: `sentence-transformers/all-MiniLM-L6-v2`. 384-dim sentence
    embeddings, CPU-friendly, ~80MB weights baked into the Docker image.
    Free, no per-request cost, no data leaves the EC2 box.
  - Storage: pgvector `vector(384)` column on bank_transactions, indexed
    with IVFFlat cosine-similarity. Migration 0004 already created it.
  - Pipeline: every BankTransaction insertion now flows through `embed_text`
    and stores the vector. A backfill helper re-embeds the org's full
    history when called via `/api/learning/backfill-embeddings`.
  - Queries: `find_similar_txns()` returns the N nearest neighbors of a
    given description, scoped to the caller's tenant.

Falls back gracefully if `sentence-transformers` isn't installed (e.g. in
local-dev without the dep) — embedding calls return None, similarity calls
return empty lists, the rest of the worker keeps running.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_DIM = 384

_model = None  # lazy singleton
_pgvector_cache: Optional[bool] = None  # cached DB-side capability probe


def is_enabled() -> bool:
    """True if sentence-transformers is importable. We don't actually load
    the model here — that happens lazily on first use.

    NOTE: This only checks the Python library. Use `is_pgvector_available(db)`
    to additionally verify the DB has the pgvector extension installed —
    both must be true for end-to-end embeddings to work."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def is_pgvector_available(db: Session) -> bool:
    """Check whether the `vector` extension is actually installed on the DB.

    Migration 0004 conditionally creates it (guarded by a DO block), so on
    a Postgres without pgvector packaged the column never exists. Calling
    embed/search code blindly in that scenario raises 'type "vector" does
    not exist'. This probe is the gate.

    Result is cached for the process — extensions don't appear/disappear at
    runtime, so probing once is plenty.
    """
    global _pgvector_cache
    if _pgvector_cache is not None:
        return _pgvector_cache
    try:
        row = db.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector' LIMIT 1")
        ).first()
        _pgvector_cache = row is not None
    except Exception:  # noqa: BLE001 — e.g. SQLite test rig, missing perms
        _pgvector_cache = False
    if not _pgvector_cache:
        logger.warning(
            "pgvector extension not detected on DB — semantic search disabled. "
            "Run `CREATE EXTENSION vector;` (or upgrade to a Postgres image "
            "with pgvector) and restart the API."
        )
    return _pgvector_cache


def fully_enabled(db: Session) -> bool:
    """Both prerequisites in one call: Python lib AND DB extension."""
    return is_enabled() and is_pgvector_available(db)


def _get_model():
    """Lazy-load the sentence-transformers model on first use.

    Loading is ~2-3 seconds on a t3.medium. After that every embedding call
    is <50ms. Model stays resident; ~300MB of RAM.
    """
    global _model
    if _model is not None:
        return _model
    if not is_enabled():
        raise RuntimeError("sentence-transformers not installed")
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model %s (one-time, ~2-3 sec)", DEFAULT_MODEL)
    _model = SentenceTransformer(DEFAULT_MODEL)
    logger.info("Embedding model ready")
    return _model


# ---------------------------------------------------------------------------
# Embed APIs
# ---------------------------------------------------------------------------


def embed_text(text_value: str) -> Optional[list[float]]:
    """Embed a single string. Returns a list[float] of length EMBEDDING_DIM,
    or None if embeddings are disabled."""
    if not is_enabled():
        return None
    if not text_value or not text_value.strip():
        return [0.0] * EMBEDDING_DIM
    model = _get_model()
    vec = model.encode(text_value, normalize_embeddings=True)
    return vec.tolist()  # type: ignore[no-any-return]


def embed_batch(texts: list[str]) -> Optional[list[list[float]]]:
    """Embed many strings in one batch — ~10× faster than one-at-a-time when
    re-embedding a whole org's history. Returns None if disabled."""
    if not is_enabled():
        return None
    if not texts:
        return []
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=64)
    return [v.tolist() for v in vecs]


# ---------------------------------------------------------------------------
# Storage helpers — write/read on the bank_transactions.description_embedding
# column added by migration 0004.
# ---------------------------------------------------------------------------


def set_txn_embedding(db: Session, *, txn_id: uuid.UUID, vector: list[float]) -> None:
    """Persist an embedding for a single bank transaction via raw SQL.

    Why raw SQL: SQLAlchemy doesn't natively know about pgvector's type
    until we declare a Column with the right adapter. To keep ORM models
    simple (and to avoid breaking the SQLite test rig), we treat the
    vector column as opaque from Python's perspective and use literal
    SQL casts.

    No-op if pgvector isn't installed — caller doesn't need to check.
    """
    if not vector:
        return
    if not is_pgvector_available(db):
        return
    # Format as Postgres array literal '[0.1,0.2,...]' — pgvector accepts this
    # and casts via the column's vector type.
    arr = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
    db.execute(
        text(
            "UPDATE bank_transactions "
            "SET description_embedding = (:vec)::vector "
            "WHERE id = :id"
        ),
        {"vec": arr, "id": str(txn_id)},
    )


def set_txn_embeddings_batch(
    db: Session, *, items: list[tuple[uuid.UUID, list[float]]]
) -> int:
    """Bulk-update embeddings for many txns in one statement. Returns count."""
    n = 0
    for txn_id, vec in items:
        if not vec:
            continue
        set_txn_embedding(db, txn_id=txn_id, vector=vec)
        n += 1
    return n


# ---------------------------------------------------------------------------
# Similarity search — uses pgvector's <=> operator (cosine distance).
# ---------------------------------------------------------------------------


def find_similar_txns(
    db: Session,
    *,
    org_id: uuid.UUID,
    query_vector: list[float],
    limit: int = 5,
    exclude_id: Optional[uuid.UUID] = None,
) -> list[dict]:
    """Return the N nearest bank_transactions to the query vector, scoped
    strictly to org_id. Output rows include the cosine distance so the
    caller can decide whether the match is good enough.

    Cosine distance ranges from 0 (identical) to 2 (opposite). For
    sentence-transformers normalized embeddings, distance < 0.3 is a
    "good" match, < 0.15 is "almost certainly same vendor".
    """
    if not query_vector:
        return []
    if not is_pgvector_available(db):
        return []
    arr = "[" + ",".join(f"{v:.7f}" for v in query_vector) + "]"
    params: dict = {"org": str(org_id), "vec": arr, "lim": limit}
    extra_where = ""
    if exclude_id is not None:
        extra_where = "AND id != :exclude "
        params["exclude"] = str(exclude_id)

    rows = db.execute(
        text(
            f"""
            SELECT id, txn_date, amount, direction, description,
                   matched_vendor_id, category,
                   description_embedding <=> (:vec)::vector AS distance
            FROM bank_transactions
            WHERE org_id = :org
              AND description_embedding IS NOT NULL
              {extra_where}
            ORDER BY description_embedding <=> (:vec)::vector
            LIMIT :lim
            """
        ),
        params,
    ).all()
    return [
        {
            "id": str(r[0]),
            "txn_date": r[1].isoformat() if r[1] else None,
            "amount": str(r[2]) if r[2] is not None else None,
            "direction": r[3],
            "description": r[4],
            "matched_vendor_id": str(r[5]) if r[5] else None,
            "category": r[6],
            "distance": float(r[7]) if r[7] is not None else None,
        }
        for r in rows
    ]


def search_txns_by_query(
    db: Session,
    *,
    org_id: uuid.UUID,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """User-facing semantic search. Embeds the query string and returns
    matching bank txns."""
    if not query.strip() or not is_enabled():
        return []
    vec = embed_text(query)
    if vec is None:
        return []
    return find_similar_txns(db, org_id=org_id, query_vector=vec, limit=limit)


# ---------------------------------------------------------------------------
# Backfill — embed every existing bank txn for an org.
# ---------------------------------------------------------------------------


def backfill_org_embeddings(
    db: Session, *, org_id: uuid.UUID, batch_size: int = 64
) -> dict:
    """One-shot: embed every bank_transaction in the org whose
    description_embedding is still NULL. Returns counts.

    Idempotent. Safe to re-run; only NULL embeddings get filled.
    """
    if not is_enabled():
        return {
            "embedded": 0,
            "total": 0,
            "skipped_reason": "sentence-transformers not installed",
        }
    if not is_pgvector_available(db):
        return {
            "embedded": 0,
            "total": 0,
            "skipped_reason": "pgvector extension not installed on the database",
        }

    rows = db.execute(
        text(
            """
            SELECT id, description
            FROM bank_transactions
            WHERE org_id = :org AND description_embedding IS NULL
            """
        ),
        {"org": str(org_id)},
    ).all()
    total = len(rows)
    if total == 0:
        return {"embedded": 0, "total": 0}

    logger.info("Backfilling %d embeddings for org %s", total, org_id)
    embedded = 0
    # Chunk into batches so we don't blow up RAM on huge orgs.
    for i in range(0, total, batch_size):
        chunk = rows[i : i + batch_size]
        texts = [r[1] or "" for r in chunk]
        vectors = embed_batch(texts)
        if vectors is None:
            break
        for (txn_id, _desc), vec in zip(chunk, vectors):
            set_txn_embedding(db, txn_id=txn_id, vector=vec)
            embedded += 1
        db.flush()
    db.commit()
    logger.info("Backfill complete: %d embeddings written", embedded)
    return {"embedded": embedded, "total": total}


def coverage_stats(db: Session, *, org_id: uuid.UUID) -> dict:
    """How much of the org's data has been embedded? Used by the Learning
    page to show progress.

    If pgvector isn't installed on this DB, the `description_embedding`
    column doesn't exist; we still need to return the txn total so the
    UI can render the coverage tile without 500ing."""
    pgvec = is_pgvector_available(db)
    if pgvec:
        row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(description_embedding) AS embedded
                FROM bank_transactions
                WHERE org_id = :org
                """
            ),
            {"org": str(org_id)},
        ).first()
        total = int(row[0] or 0)
        embedded = int(row[1] or 0)
    else:
        # Embedding column may not exist — just count rows.
        total = int(
            db.execute(
                text("SELECT COUNT(*) FROM bank_transactions WHERE org_id = :org"),
                {"org": str(org_id)},
            ).scalar()
            or 0
        )
        embedded = 0
    return {
        "total": total,
        "embedded": embedded,
        "coverage_pct": (100.0 * embedded / total) if total else 0.0,
    }
