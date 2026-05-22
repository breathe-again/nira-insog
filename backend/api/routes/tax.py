"""Tax intelligence endpoints.

  GET /api/tax/gstin-health        — every vendor + client with GSTIN status
  GET /api/tax/advance-tax          — quarterly installment estimate
  GET /api/tax/tds-draft            — vendor-by-vendor TDS draft

All three are read-only and tenant-scoped.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from common.models import Client, Vendor
from services.tax.advance_tax import (
    AdvanceTaxEstimateOut,
    TaxInstallmentOut,
    estimate_advance_tax,
)
from services.tax.gstin import validate_gstin
from services.tax.tds import VendorTDSRow, generate_tds_draft

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tax", tags=["tax"])


# ---------------------------------------------------------------------------
# GSTIN health
# ---------------------------------------------------------------------------


class CounterpartyGSTINOut(BaseModel):
    id: uuid.UUID
    role: Literal["vendor", "client"]
    name: str
    gstin_raw: Optional[str] = None
    is_valid: bool
    reason: Optional[str] = None
    state_code: Optional[str] = None
    state_name: Optional[str] = None
    pan: Optional[str] = None


class GSTINHealthOut(BaseModel):
    counterparties: list[CounterpartyGSTINOut]
    total: int
    valid: int
    invalid: int
    missing: int
    compliance_pct: float  # valid / (valid + invalid + missing)


@router.get(
    "/gstin-health",
    response_model=GSTINHealthOut,
    summary="GSTIN validity across every vendor + client",
)
def gstin_health(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> GSTINHealthOut:
    vendors = list(
        db.scalars(select(Vendor).where(Vendor.org_id == org_id))
    )
    clients = list(
        db.scalars(select(Client).where(Client.org_id == org_id))
    )

    rows: list[CounterpartyGSTINOut] = []
    valid = 0
    invalid = 0
    missing = 0

    for v in vendors:
        r = validate_gstin(v.gstin)
        rows.append(
            CounterpartyGSTINOut(
                id=v.id,
                role="vendor",
                name=v.name,
                gstin_raw=v.gstin,
                is_valid=r.is_valid,
                reason=r.reason,
                state_code=r.state_code,
                state_name=r.state_name,
                pan=r.pan,
            )
        )
        if r.is_valid:
            valid += 1
        elif r.reason == "missing":
            missing += 1
        else:
            invalid += 1

    for c in clients:
        r = validate_gstin(c.gstin)
        rows.append(
            CounterpartyGSTINOut(
                id=c.id,
                role="client",
                name=c.name,
                gstin_raw=c.gstin,
                is_valid=r.is_valid,
                reason=r.reason,
                state_code=r.state_code,
                state_name=r.state_name,
                pan=r.pan,
            )
        )
        if r.is_valid:
            valid += 1
        elif r.reason == "missing":
            missing += 1
        else:
            invalid += 1

    total = len(rows)
    # Sort: invalid first (need attention), then missing, then valid.
    sort_key = {"invalid": 0, "missing": 1, "valid": 2}
    rows.sort(
        key=lambda r: (
            sort_key["valid"] if r.is_valid else (
                sort_key["missing"] if r.reason == "missing" else sort_key["invalid"]
            ),
            r.name.lower(),
        )
    )
    compliance = (valid / total * 100) if total > 0 else 0.0
    return GSTINHealthOut(
        counterparties=rows,
        total=total,
        valid=valid,
        invalid=invalid,
        missing=missing,
        compliance_pct=round(compliance, 1),
    )


# ---------------------------------------------------------------------------
# Advance tax
# ---------------------------------------------------------------------------


class _TaxInstallmentSchema(BaseModel):
    label: str
    due_date: date
    cumulative_pct: float
    cumulative_amount: Decimal
    this_installment: Decimal
    status: str
    days_until_due: int


class _AdvanceTaxSchema(BaseModel):
    fy_label: str
    fy_start: date
    fy_end: date
    days_elapsed: int
    days_remaining: int
    revenue_ytd: Decimal
    expense_ytd: Decimal
    net_profit_ytd: Decimal
    projected_annual_profit: Decimal
    entity_type: str
    estimated_tax_rate: float
    estimated_annual_tax: Decimal
    installments: list[_TaxInstallmentSchema]
    next_due: Optional[_TaxInstallmentSchema] = None
    total_overdue: Decimal


def _installment_to_schema(i: TaxInstallmentOut) -> _TaxInstallmentSchema:
    return _TaxInstallmentSchema(
        label=i.label,
        due_date=i.due_date,
        cumulative_pct=i.cumulative_pct,
        cumulative_amount=i.cumulative_amount,
        this_installment=i.this_installment,
        status=i.status,
        days_until_due=i.days_until_due,
    )


@router.get(
    "/advance-tax",
    response_model=_AdvanceTaxSchema,
    summary="Quarterly advance-tax estimate based on YTD run-rate",
)
def advance_tax(
    entity_type: Literal["company", "individual", "professional", "llp"] = Query(
        default="company"
    ),
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> _AdvanceTaxSchema:
    today = datetime.now(timezone.utc).date()
    est = estimate_advance_tax(
        db, org_id=org_id, today=today, entity_type=entity_type
    )
    return _AdvanceTaxSchema(
        fy_label=est.fy_label,
        fy_start=est.fy_start,
        fy_end=est.fy_end,
        days_elapsed=est.days_elapsed,
        days_remaining=est.days_remaining,
        revenue_ytd=est.revenue_ytd,
        expense_ytd=est.expense_ytd,
        net_profit_ytd=est.net_profit_ytd,
        projected_annual_profit=est.projected_annual_profit,
        entity_type=est.entity_type,
        estimated_tax_rate=est.estimated_tax_rate,
        estimated_annual_tax=est.estimated_annual_tax,
        installments=[_installment_to_schema(i) for i in est.installments],
        next_due=_installment_to_schema(est.next_due) if est.next_due else None,
        total_overdue=est.total_overdue,
    )


# ---------------------------------------------------------------------------
# TDS draft
# ---------------------------------------------------------------------------


class _VendorTDSRowSchema(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str
    pan: Optional[str]
    section_code: str
    section_label: str
    fy_payments_total: Decimal
    threshold: Decimal
    has_crossed_threshold: bool
    applicable_rate: float
    tds_amount_estimated: Decimal
    net_payable_after_tds: Decimal
    form_quarterly: str
    deduction_status: str
    notes: Optional[str] = None


class TDSDraftOut(BaseModel):
    fy_label: str
    rows: list[_VendorTDSRowSchema]
    total_vendors: int
    vendors_crossed_threshold: int
    total_tds_estimated: Decimal


@router.get(
    "/tds-draft",
    response_model=TDSDraftOut,
    summary="TDS section detection + draft 24Q/26Q entries",
)
def tds_draft(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> TDSDraftOut:
    today = datetime.now(timezone.utc).date()
    rows = generate_tds_draft(db, org_id=org_id, today=today)

    schemas = [
        _VendorTDSRowSchema(
            vendor_id=r.vendor_id,
            vendor_name=r.vendor_name,
            pan=r.pan,
            section_code=r.section_code,
            section_label=r.section_label,
            fy_payments_total=r.fy_payments_total,
            threshold=r.threshold,
            has_crossed_threshold=r.has_crossed_threshold,
            applicable_rate=r.applicable_rate,
            tds_amount_estimated=r.tds_amount_estimated,
            net_payable_after_tds=r.net_payable_after_tds,
            form_quarterly=r.form_quarterly,
            deduction_status=r.deduction_status,
            notes=r.notes,
        )
        for r in rows
    ]
    crossed = sum(1 for r in rows if r.has_crossed_threshold)
    total_tds = sum((r.tds_amount_estimated for r in rows), start=Decimal("0"))

    fy_label = ""
    if today.month >= 4:
        fy_label = f"{today.year}-{(today.year + 1) % 100:02d}"
    else:
        fy_label = f"{today.year - 1}-{today.year % 100:02d}"

    return TDSDraftOut(
        fy_label=fy_label,
        rows=schemas,
        total_vendors=len(rows),
        vendors_crossed_threshold=crossed,
        total_tds_estimated=total_tds,
    )
