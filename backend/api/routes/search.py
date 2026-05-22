"""Semantic search over a tenant's transactions + invoices + receipts.

  GET /api/search?q=<query>&limit=<n>

Searches three data sources in one pass and merges them:
  1. bank_transactions  — semantic match via pgvector embeddings
  2. invoices           — literal substring match on (invoice_number,
                          vendor.name, vendor.aliases) since invoice rows
                          don't have embeddings yet
  3. receipts           — literal substring match on (notes, vendor.name)

This means "AWS" finds every spend signal — operational invoices, bank
debits, and standalone receipts — not just bank descriptions.

Tenant strictly enforced — every query is scoped to `current_org_id`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from common.models import Invoice, Receipt, Vendor
from services.embeddings import fully_enabled, search_txns_by_query

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
    # Extra fields for invoice/receipt hits.
    document_id: Optional[str] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None


class SearchOut(BaseModel):
    query: str
    enabled: bool
    count: int
    hits: list[SearchHitOut]


# Cosine distance threshold above which we consider a "match" too weak to
# surface. The MiniLM L6 v2 model returns distances roughly:
#   0.00 - 0.20 → near-identical phrasing
#   0.20 - 0.50 → strong semantic match
#   0.50 - 0.90 → loose / topical match
#   0.90 - 1.20 → marginal; usually noise
#   > 1.20      → unrelated
# Empirically with this org's data, "AWS" against 257 txns returns nearest
# rows at distance ~1.1-1.4 — i.e. nothing actually about AWS. We use 1.0 as
# the cutoff so genuinely-unrelated queries return an empty list (which the
# UI explains with "No transactions match X") instead of dumping noise.
_DEFAULT_MAX_DISTANCE = 1.0


@router.get("", response_model=SearchOut, summary="Semantic transaction search")
def search(
    q: str = Query(..., min_length=1, max_length=200, description="Natural-language search query"),
    limit: int = Query(default=20, ge=1, le=100),
    max_distance: float = Query(
        default=_DEFAULT_MAX_DISTANCE,
        ge=0.0,
        le=2.0,
        description="Cosine distance cutoff. Lower = stricter. Default 1.0.",
    ),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> SearchOut:
    """Search across bank transactions (semantic) + invoices + receipts
    (literal substring). Merges results into one ranked list — bank-txn
    matches come back with a cosine distance, invoice/receipt matches use
    a synthetic distance so the UI can rank them alongside."""
    enabled = fully_enabled(db)
    hits: list[SearchHitOut] = []

    # --- Bank transactions: semantic search via pgvector ---
    if enabled:
        raw_hits = search_txns_by_query(db, org_id=org_id, query=q, limit=limit * 2)
        for h in raw_hits:
            if h.get("distance") is not None and h["distance"] > max_distance:
                continue
            hits.append(SearchHitOut(**h, source="bank_txn"))

    # --- Invoices: literal substring match on number + vendor name/aliases.
    # We use ILIKE for case-insensitive matching. Each match gets a synthetic
    # distance derived from where the substring lands (early in the field =
    # stronger match) so it sorts alongside semantic hits.
    qpat = f"%{q.strip()}%"
    inv_rows = db.execute(
        select(Invoice, Vendor)
        .outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
        .where(
            Invoice.org_id == org_id,
            or_(
                Invoice.invoice_number.ilike(qpat),
                Vendor.name.ilike(qpat),
            ),
        )
        .order_by(Invoice.issue_date.desc())
        .limit(limit)
    ).all()
    for inv, vnd in inv_rows:
        # Synthetic distance: 0.10 if the query lands in the vendor name
        # exactly, 0.30 if elsewhere — keeps invoices near the top.
        vname = (vnd.name if vnd else "") or ""
        synth = 0.10 if q.lower() in vname.lower() else 0.30
        hits.append(
            SearchHitOut(
                id=str(inv.id),
                source="invoice",
                txn_date=inv.issue_date.isoformat() if inv.issue_date else None,
                amount=str(inv.total) if inv.total is not None else None,
                direction="debit" if inv.type == "purchase" else "credit",
                description=f"Invoice {inv.invoice_number}"
                + (f" — {vname}" if vname else ""),
                matched_vendor_id=str(vnd.id) if vnd else None,
                category=None,
                distance=synth,
                document_id=str(inv.document_id) if inv.document_id else None,
                vendor_name=vname or None,
                invoice_number=inv.invoice_number,
            )
        )

    # --- Receipts: literal substring on notes + vendor name ---
    rcpt_rows = db.execute(
        select(Receipt, Vendor)
        .outerjoin(Vendor, Vendor.id == Receipt.vendor_id)
        .where(
            Receipt.org_id == org_id,
            or_(
                Receipt.notes.ilike(qpat),
                Vendor.name.ilike(qpat),
            ),
        )
        .order_by(Receipt.date.desc())
        .limit(limit)
    ).all()
    for rcpt, vnd in rcpt_rows:
        vname = (vnd.name if vnd else "") or ""
        synth = 0.15 if q.lower() in vname.lower() else 0.35
        hits.append(
            SearchHitOut(
                id=str(rcpt.id),
                source="receipt",
                txn_date=rcpt.date.isoformat() if rcpt.date else None,
                amount=str(rcpt.amount) if rcpt.amount is not None else None,
                direction="debit",
                description=(rcpt.notes or vname or "Receipt")[:200],
                matched_vendor_id=str(vnd.id) if vnd else None,
                category=rcpt.category,
                distance=synth,
                document_id=str(rcpt.document_id) if rcpt.document_id else None,
                vendor_name=vname or None,
            )
        )

    # Sort merged hits by distance ascending. Dedupe by (source, id) just in
    # case (shouldn't happen but defensive).
    seen: set[tuple] = set()
    merged: list[SearchHitOut] = []
    for h in sorted(hits, key=lambda x: (x.distance if x.distance is not None else 2.0)):
        key = (h.source, h.id)
        if key in seen:
            continue
        seen.add(key)
        merged.append(h)
        if len(merged) >= limit:
            break

    return SearchOut(
        query=q,
        enabled=enabled,
        count=len(merged),
        hits=merged,
    )
