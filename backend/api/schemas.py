"""Pydantic schemas for the public API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---------- Auth ----------


class SignupIn(BaseModel):
    """First-user signup. Creates an Org + a founder User in one shot."""

    org_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)

    @field_validator("org_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("org name cannot be blank")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class AuthMeOut(BaseModel):
    """Response from /api/auth/me — also returned after signup/login."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    org_id: uuid.UUID
    email: EmailStr
    role: str
    org_name: str
    org_plan: str


class TokensOut(BaseModel):
    """Returned by signup/login/refresh. Also written to httpOnly cookies."""

    access_token: str
    access_token_expires_at: datetime
    # We do NOT return the refresh token in the JSON body for browser clients
    # (it goes in the httpOnly cookie). API clients can opt in by passing
    # ?include_refresh=1 — handy for CLIs.
    refresh_token: Optional[str] = None
    user: AuthMeOut


# ---------- Feedback / edit ----------


class DocumentPatchIn(BaseModel):
    """Fields a user can correct on a Document."""

    document_type: Optional[str] = Field(default=None, max_length=40)
    vendor_id: Optional[uuid.UUID] = None
    category: Optional[str] = Field(default=None, max_length=100)


class VendorPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    default_expense_category: Optional[str] = Field(default=None, max_length=100)
    gstin: Optional[str] = Field(default=None, max_length=20)
    add_alias: Optional[str] = Field(default=None, max_length=255)


class VendorMergeIn(BaseModel):
    """POST body for /api/vendors/{id}/merge — merge `loser_id` into `id`."""

    loser_id: uuid.UUID


class InsightPatchIn(BaseModel):
    severity: Optional[str] = Field(default=None, pattern="^(info|attention|urgent)$")
    mute_vendor: bool = False  # if True, silences anomaly for the linked vendor


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


class RecurringOutflowOut(BaseModel):
    """One detected recurring spend pattern (rent, salary, AWS, etc.)."""

    label: str
    median_amount: Decimal
    expected_day_of_month: int | None = None
    observed_count: int
    last_seen_on: str  # ISO date — frontend formats
    status: str = "on_track"  # "on_track" | "due_soon" | "overdue"
    days_until_due: int | None = None


class CashFlowCategoryPointOut(BaseModel):
    """One day's stacked-by-category breakdown for the advanced chart mode.

    `date` matches the same "MMM d" format the simple cash_flow uses, so the
    two arrays can be cross-referenced by index. `categories` maps category
    name → outflow amount that day (debits only)."""

    date: str
    categories: dict[str, Decimal] = Field(default_factory=dict)


class CashFlowMetaOut(BaseModel):
    """Auxiliary annotations for the cash flow chart — anomaly day markers
    and the palette of category colours (so the frontend doesn't have to
    duplicate the bucketing logic)."""

    # Set of dates (same "MMM d" format) where any non-info insight fired.
    anomaly_dates: list[str] = Field(default_factory=list)
    # Ordered list of (category_name, hex_color) for the stacked chart legend.
    category_palette: list[tuple[str, str]] = Field(default_factory=list)


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
    cash_flow_by_category: list[CashFlowCategoryPointOut] = Field(default_factory=list)
    cash_flow_meta: CashFlowMetaOut = Field(default_factory=CashFlowMetaOut)
    expense_breakdown: list[CategorySliceOut]
    receivables_aging: list[AgingBucketOut]
    forecast: list[ForecastPointOut]

    # Lists
    top_vendors: list[CounterpartyRowOut]
    top_clients: list[CounterpartyRowOut]
    insights: list[InsightOut]
    compliance: list[ComplianceRowOut]

    # Tier-1 learning: detected recurring monthly spend.
    recurring_outflows: list[RecurringOutflowOut] = Field(default_factory=list)

    # Honesty signal — tells the frontend whether enough data exists.
    has_any_data: bool = False
    bank_txn_count: int = 0


# ---------- Generic ----------


class ErrorOut(BaseModel):
    detail: str
