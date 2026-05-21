"""Description embeddings — Tier-2 learning foundation.

Provider choice (May 2026):
  - Anthropic doesn't expose embeddings yet.
  - OpenAI text-embedding-3-small: 1536 dims, $0.02/1M tokens, fast.
  - Cohere embed-multilingual-v3.0: 1024 dims, decent on Hindi/Hinglish.
  - sentence-transformers all-MiniLM-L6-v2: 384 dims, runs locally on CPU,
    ~80MB model weight, no API cost.

For an SMB tool on a single t3.medium with the option to scale later, the
right default is sentence-transformers — zero per-request cost, predictable
latency, no PII leaves the box. Migration 0004 sets up a 384-dim vector
column to match this model.

This module is a SKELETON for now. Full wiring (embed on ingest, similarity
search endpoint, auto-tag from neighbors) lands in the next session.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIM = 384

_model = None  # lazy singleton


def is_enabled() -> bool:
    """Embeddings are enabled when sentence-transformers is installed AND
    the model can be loaded. Skips silently in dev if the wheel isn't there."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model():
    """Lazy-load the sentence-transformers model on first use."""
    global _model
    if _model is not None:
        return _model
    if not is_enabled():
        raise RuntimeError("sentence-transformers not installed")
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model %s (one-time, ~80MB)", DEFAULT_MODEL)
    _model = SentenceTransformer(DEFAULT_MODEL)
    return _model


def embed_text(text: str) -> Optional[list[float]]:
    """Embed a single string. Returns a list[float] of length EMBEDDING_DIM
    or None if embeddings are disabled."""
    if not is_enabled():
        return None
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIM
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()  # type: ignore[no-any-return]


def embed_batch(texts: list[str]) -> Optional[list[list[float]]]:
    """Embed many strings in one batch. None if disabled.

    Use this when re-embedding a whole org's history — much faster than
    one-at-a-time."""
    if not is_enabled():
        return None
    if not texts:
        return []
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=64)
    return [v.tolist() for v in vecs]


# ---------------------------------------------------------------------------
# TODO (next session) — full Tier-2 wiring:
#
# 1. In worker/tasks.py, after BankTransaction.add, also write its
#    description_embedding using embed_batch([t.description for t in inserted]).
#
# 2. New endpoint: GET /api/transactions/{id}/similar
#    Uses pgvector's <=> operator:
#       SELECT id, description, category, matched_vendor_id
#       FROM bank_transactions
#       WHERE org_id = :org
#       ORDER BY description_embedding <=> :query_vec
#       LIMIT 5
#
# 3. Auto-tag from neighbors: when a new txn has matched_vendor_id IS NULL,
#    find its 5 nearest neighbors. If 4+ share a vendor, auto-assign that
#    vendor (and mark auto_tagged_by='embedding').
#
# 4. New endpoint: GET /api/inbox/search?q=...
#    Frontend search bar that does semantic search across all txns + invoices
#    + receipts in the tenant.
# ---------------------------------------------------------------------------
