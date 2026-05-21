"""Pydantic schemas for the public API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- Organization & User ----------


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    plan: str
    created_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    email: EmailStr
    role: str
    created_at: datetime


# ---------- Documents ----------


class DocumentOut(BaseModel):
    """A Document row as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    source: str
    original_filename: str
    file_type: str
    document_type: str
    status: str
    file_size_bytes: int
    error_message: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None


class DocumentDetailOut(DocumentOut):
    """Document plus its raw extraction payload (for the detail view)."""

    raw_extraction_json: Optional[dict] = None


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int = Field(description="Total documents matching filters (not just this page).")


# ---------- Vendors ----------


class VendorStatsOut(BaseModel):
    """Rollup stats for one vendor, computed at list time."""

    txn_count: int = 0
    txn_total: Decimal = Decimal("0")
    txn_mean: Decimal = Decimal("0")
    receipt_count: int = 0
    receipt_total: Decimal = Decimal("0")


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    aliases: list[str] = Field(default_factory=list)
    gstin: Optional[str] = None
    default_expense_category: Optional[str] = None
    created_at: datetime
    stats: VendorStatsOut = Field(default_factory=VendorStatsOut)


class VendorListOut(BaseModel):
    items: list[VendorOut]
    total: int


# ---------- Insights ----------


class InsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    type: str
    severity: str
    title: str
    body: str
    supporting_data: Optional[dict[str, Any]] = None
    created_at: datetime
    dismissed_at: Optional[datetime] = None


class InsightListOut(BaseModel):
    items: list[InsightOut]
    total: int


# ---------- Dashboard ----------


class KpiOut(BaseModel):
    """One KPI tile — current value with prior-period comparison."""

    value: Decimal = Decimal("0")
    prev_value: Decimal = Decimal("0")
    delta_pct: float = 0.0  # signed percentage change


class CashFlowPointOut(BaseModel):
    date: str  # "MMM d" — already formatted server-side
    in_amount: Decimal = Field(default=Decimal("0"), alias="in")
    out_amount: Decimal = Field(default=Decimal("0"), alias="out")
    net: Decimal = Decimal("0")

    model_config = ConfigDict(populate_by_name=True)


class CategorySliceOut(BaseModel):
    name: str
    value: Decimal
    color: str


class AgingBucketOut(BaseModel):
    bucket: str  # "0–30" | "31–60" | "61–90" | "90+"
    amount: Decimal


class CounterpartyRowOut(BaseModel):
    name: str
    amount: Decimal
    delta_pct: float = 0.0


class ForecastPointOut(BaseModel):
    date: str
    forecast: Decimal
    lower_band: Decimal
    upper_band: Decimal


class ComplianceRowOut(BaseModel):
    status: str  # "ok" | "warn" | "fail"
    label: str


class DashboardSummaryOut(BaseModel):
    """Everything the Dashboard needs in one call.

    Computed at request time from BankTransaction, Invoice, Receipt, Vendor,
    Client, and Insight rows.
    """

    # KPIs
    cash_position: KpiOut
    receivables: KpiOut
    payables: KpiOut
    net_flow_mtd: KpiOut

    # Charts
    cash_flow: list[CashFlowPointOut]
    expense_breakdown: list[CategorySliceOut]
    receivables_aging: list[AgingBucketOut]
    forecast: list[ForecastPointOut]

    # Lists
    top_vendors: list[CounterpartyRowOut]
    top_clients: list[CounterpartyRowOut]
    insights: list[InsightOut]
    compliance: list[ComplianceRowOut]

    # Honesty signal — tells the frontend whether enough data exists.
    has_any_data: bool = False
    bank_txn_count: int = 0


# ---------- Generic ----------


class ErrorOut(BaseModel):
    detail: str
