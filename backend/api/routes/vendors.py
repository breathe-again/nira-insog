"""Vendor endpoints — list with rollup stats, dismiss-merge, anomaly history."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.deps import current_org_id
from api.schemas import VendorListOut, VendorOut, VendorStatsOut
from common.db import get_db
from common.models import BankTransaction, Receipt, Vendor

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


@router.get("", response_model=VendorListOut, summary="List vendors with spend stats")
def list_vendors(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None, description="Substring match on name (case-insensitive)."),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> VendorListOut:
    base = select(Vendor).where(Vendor.org_id == org_id)
    if search:
        base = base.where(func.lower(Vendor.name).contains(search.lower()))

    total = db.scalar(select(func.count()).select_from(base.subquery()))

    stmt = base.order_by(Vendor.name.asc()).limit(limit).offset(offset)
    vendors = list(db.scalars(stmt).all())

    if not vendors:
        return VendorListOut(items=[], total=int(total or 0))

    vendor_ids = [v.id for v in vendors]

    # Rollup: per-vendor txn count + sum + mean (bank debits only, for now).
    txn_stats_rows = db.execute(
        select(
            BankTransaction.matched_vendor_id,
            func.count(BankTransaction.id),
            func.coalesce(func.sum(BankTransaction.amount), 0),
            func.coalesce(func.avg(BankTransaction.amount), 0),
        )
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.matched_vendor_id.in_(vendor_ids),
        )
        .group_by(BankTransaction.matched_vendor_id)
    ).all()
    txn_stats: dict[uuid.UUID, tuple[int, Decimal, Decimal]] = {
        row[0]: (int(row[1]), Decimal(row[2]), Decimal(row[3])) for row in txn_stats_rows
    }

    # Rollup: per-vendor receipt count + sum.
    receipt_stats_rows = db.execute(
        select(
            Receipt.vendor_id,
            func.count(Receipt.id),
            func.coalesce(func.sum(Receipt.amount), 0),
        )
        .where(
            Receipt.org_id == org_id,
            Receipt.vendor_id.in_(vendor_ids),
        )
        .group_by(Receipt.vendor_id)
    ).all()
    receipt_stats: dict[uuid.UUID, tuple[int, Decimal]] = {
        row[0]: (int(row[1]), Decimal(row[2])) for row in receipt_stats_rows
    }

    items: list[VendorOut] = []
    for v in vendors:
        txn_count, txn_sum, txn_mean = txn_stats.get(v.id, (0, Decimal("0"), Decimal("0")))
        rcpt_count, rcpt_sum = receipt_stats.get(v.id, (0, Decimal("0")))
        items.append(
            VendorOut(
                id=v.id,
                name=v.name,
                aliases=v.aliases or [],
                gstin=v.gstin,
                default_expense_category=v.default_expense_category,
                created_at=v.created_at,
                stats=VendorStatsOut(
                    txn_count=txn_count,
                    txn_total=txn_sum,
                    txn_mean=txn_mean,
                    receipt_count=rcpt_count,
                    receipt_total=rcpt_sum,
                ),
            )
        )

    return VendorListOut(items=items, total=int(total or 0))


@router.get(
    "/{vendor_id}/transactions",
    summary="Recent bank transactions and receipts for a vendor",
)
def vendor_history(
    vendor_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> dict:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != org_id:
        raise HTTPException(status_code=404, detail="vendor not found")

    txns = list(
        db.scalars(
            select(BankTransaction)
            .where(
                BankTransaction.org_id == org_id,
                BankTransaction.matched_vendor_id == vendor_id,
            )
            .order_by(desc(BankTransaction.txn_date))
            .limit(limit)
        ).all()
    )
    receipts = list(
        db.scalars(
            select(Receipt)
            .where(Receipt.org_id == org_id, Receipt.vendor_id == vendor_id)
            .order_by(desc(Receipt.date))
            .limit(limit)
        ).all()
    )

    return {
        "vendor": {"id": str(vendor.id), "name": vendor.name},
        "bank_transactions": [
            {
                "id": str(t.id),
                "txn_date": t.txn_date.isoformat(),
                "description": t.description,
                "amount": str(t.amount),
                "direction": t.direction,
            }
            for t in txns
        ],
        "receipts": [
            {
                "id": str(r.id),
                "date": r.date.isoformat(),
                "amount": str(r.amount),
                "category": r.category,
                "payment_mode": r.payment_mode,
            }
            for r in receipts
        ],
    }
