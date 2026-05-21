"""Dashboard summary endpoint — one call, everything the home page needs.

Aggregates over BankTransaction, Invoice, Receipt, Vendor, Client, Insight to
produce the same shape the demo data uses, so the frontend can swap the data
source without touching its rendering code.

Heuristics used (deliberately simple — refinements come later):

- **Cash position** = latest running_balance across bank_transactions. Falls
  back to sum(credit) − sum(debit) if no balance is recorded.
- **Receivables** = sum of sales invoice totals where status != 'paid'.
- **Payables**   = sum of purchase invoice totals where status != 'paid'.
- **Net flow MTD** = month-to-date credits − debits.
- **Cash flow chart** = per-day inflow/outflow for the last 30 days.
- **Expense breakdown** = bucket bank_transactions (debits) into a small set
  of categories by description-keyword heuristic. UPI/Swiggy → Food, RENT →
  Rent, SALARY → Payroll, etc.
- **Receivables aging** = invoices binned by days past due_date.
- **Top vendors / clients** = sum of debits / credits this month, grouped by
  matched counterparty.
- **Forecast** = naive linear projection from the last 14 days' mean daily
  net flow, with a widening ±15% confidence band. Real Prophet later.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.deps import current_org_id
from api.schemas import (
    AgingBucketOut,
    CashFlowPointOut,
    CategorySliceOut,
    ComplianceRowOut,
    CounterpartyRowOut,
    DashboardSummaryOut,
    ForecastPointOut,
    InsightOut,
    KpiOut,
)
from common.db import get_db
from common.models import (
    BankTransaction,
    Client,
    Insight,
    Invoice,
    Receipt,
    Vendor,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Category bucketing — keyword → (display name, color)
# ---------------------------------------------------------------------------

_CATEGORY_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bsalary|payroll|wages?\b", re.IGNORECASE), "Payroll", "#6366f1"),
    (re.compile(r"\brent\b", re.IGNORECASE), "Rent", "#8b5cf6"),
    (re.compile(r"\b(swiggy|zomato|food|cafe|coffee|restaurant)\b", re.IGNORECASE), "Food", "#10b981"),
    (re.compile(r"\b(uber|ola|cab|rapido|metro|train|flight|travel)\b", re.IGNORECASE), "Travel", "#06b6d4"),
    (re.compile(r"\b(aws|azure|gcp|cloud|github|saas|software|netflix|spotify|prime|hotstar)\b", re.IGNORECASE), "Software", "#0ea5e9"),
    (re.compile(r"\b(gst|tds|tax)\b", re.IGNORECASE), "Tax", "#f43f5e"),
    (re.compile(r"\b(marketing|ads?|advert|facebook|google\s*ads)\b", re.IGNORECASE), "Marketing", "#f59e0b"),
    (re.compile(r"\b(loan|emi|mpokket|cred|interest|nbfc|nach)\b", re.IGNORECASE), "Finance", "#a855f7"),
    (re.compile(r"\b(airtel|jio|vi|vodafone|bsnl|electricity|water|gas|utility|bill)\b", re.IGNORECASE), "Utilities", "#14b8a6"),
    (re.compile(r"\b(amazon|flipkart|myntra|shopping)\b", re.IGNORECASE), "Shopping", "#ec4899"),
]
_CATEGORY_OTHER = ("Other", "#94a3b8")


def _categorize(description: str, vendor_name: Optional[str]) -> tuple[str, str]:
    blob = f"{description or ''} {vendor_name or ''}".strip()
    for pat, name, color in _CATEGORY_RULES:
        if pat.search(blob):
            return name, color
    return _CATEGORY_OTHER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signed_delta_pct(curr: Decimal, prev: Decimal) -> float:
    if prev == 0:
        return 0.0 if curr == 0 else 100.0
    return float((curr - prev) / prev * 100)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=DashboardSummaryOut, summary="Dashboard rollup")
def dashboard_summary(
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> DashboardSummaryOut:
    real_today = _today_utc()

    bank_txn_count = int(
        db.scalar(
            select(func.count())
            .select_from(BankTransaction)
            .where(BankTransaction.org_id == org_id)
        )
        or 0
    )

    # If the user's most recent transaction is older than ~30 days, anchor the
    # rolling windows on that latest date instead of "today". Otherwise the
    # last-30-days chart and MTD KPI would be empty for any historical upload,
    # making the dashboard useless until they upload fresh data daily.
    latest_txn_date = db.scalar(
        select(func.max(BankTransaction.txn_date)).where(
            BankTransaction.org_id == org_id
        )
    )
    if latest_txn_date is not None and (real_today - latest_txn_date).days > 30:
        today = latest_txn_date
    else:
        today = real_today

    month_start = today.replace(day=1)
    thirty_days_ago = today - timedelta(days=30)
    sixty_days_ago = today - timedelta(days=60)

    # ------------ KPI: Cash position ------------
    latest_balance_row = db.execute(
        select(BankTransaction.running_balance)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.running_balance.isnot(None),
        )
        .order_by(desc(BankTransaction.txn_date), desc(BankTransaction.created_at))
        .limit(1)
    ).first()
    cash_now = Decimal(latest_balance_row[0]) if latest_balance_row else Decimal("0")

    # Prior-period balance: balance as of 30 days ago.
    prev_balance_row = db.execute(
        select(BankTransaction.running_balance)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.running_balance.isnot(None),
            BankTransaction.txn_date < thirty_days_ago,
        )
        .order_by(desc(BankTransaction.txn_date), desc(BankTransaction.created_at))
        .limit(1)
    ).first()
    cash_prev = Decimal(prev_balance_row[0]) if prev_balance_row else cash_now

    # If we never recorded balance, fall back to net cumulative.
    if cash_now == 0 and bank_txn_count:
        net_all = db.execute(
            select(
                func.coalesce(
                    func.sum(
                        BankTransaction.amount * (-1 if False else 1)  # placeholder
                    ),
                    0,
                )
            )
        ).scalar()  # not used; will use explicit credit/debit sums:
        credits = db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0))
            .where(BankTransaction.org_id == org_id, BankTransaction.direction == "credit")
        ) or 0
        debits = db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0))
            .where(BankTransaction.org_id == org_id, BankTransaction.direction == "debit")
        ) or 0
        cash_now = Decimal(credits) - Decimal(debits)
        _ = net_all

    # ------------ KPI: Receivables (unpaid sales invoices) ------------
    receivables_now = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                Invoice.org_id == org_id,
                Invoice.type == "sales",
                Invoice.status != "paid",
            )
        )
        or 0
    )
    # Prior period: invoices issued before 30 days ago, still unpaid then.
    receivables_prev = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                Invoice.org_id == org_id,
                Invoice.type == "sales",
                Invoice.status != "paid",
                Invoice.issue_date < thirty_days_ago,
            )
        )
        or 0
    )

    # ------------ KPI: Payables (unpaid purchase invoices) ------------
    payables_now = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                Invoice.org_id == org_id,
                Invoice.type == "purchase",
                Invoice.status != "paid",
            )
        )
        or 0
    )
    payables_prev = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                Invoice.org_id == org_id,
                Invoice.type == "purchase",
                Invoice.status != "paid",
                Invoice.issue_date < thirty_days_ago,
            )
        )
        or 0
    )

    # ------------ KPI: Net flow (month-to-date) ------------
    mtd_credits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "credit",
                BankTransaction.txn_date >= month_start,
            )
        )
        or 0
    )
    mtd_debits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "debit",
                BankTransaction.txn_date >= month_start,
            )
        )
        or 0
    )
    net_flow_now = mtd_credits - mtd_debits

    # Prior month for delta: previous month-to-same-day.
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_month_cutoff = prev_month_start + timedelta(days=today.day - 1)
    prev_credits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "credit",
                BankTransaction.txn_date >= prev_month_start,
                BankTransaction.txn_date <= prev_month_cutoff,
            )
        )
        or 0
    )
    prev_debits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "debit",
                BankTransaction.txn_date >= prev_month_start,
                BankTransaction.txn_date <= prev_month_cutoff,
            )
        )
        or 0
    )
    net_flow_prev = prev_credits - prev_debits

    kpis = {
        "cash_position": KpiOut(
            value=cash_now,
            prev_value=cash_prev,
            delta_pct=_signed_delta_pct(cash_now, cash_prev),
        ),
        "receivables": KpiOut(
            value=receivables_now,
            prev_value=receivables_prev,
            delta_pct=_signed_delta_pct(receivables_now, receivables_prev),
        ),
        "payables": KpiOut(
            value=payables_now,
            prev_value=payables_prev,
            delta_pct=_signed_delta_pct(payables_now, payables_prev),
        ),
        "net_flow_mtd": KpiOut(
            value=net_flow_now,
            prev_value=net_flow_prev,
            delta_pct=_signed_delta_pct(net_flow_now, net_flow_prev),
        ),
    }

    # ------------ Cash flow chart (last 30 days) ------------
    daily_rows = db.execute(
        select(
            BankTransaction.txn_date,
            BankTransaction.direction,
            func.sum(BankTransaction.amount).label("amt"),
        )
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.txn_date >= thirty_days_ago,
        )
        .group_by(BankTransaction.txn_date, BankTransaction.direction)
    ).all()
    by_day: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"in": Decimal("0"), "out": Decimal("0")}
    )
    for d, direction, amt in daily_rows:
        key = "in" if direction == "credit" else "out"
        by_day[d][key] += Decimal(amt or 0)

    cash_flow: list[CashFlowPointOut] = []
    for i in range(30):
        d = thirty_days_ago + timedelta(days=i)
        cell = by_day.get(d, {"in": Decimal("0"), "out": Decimal("0")})
        cash_flow.append(
            CashFlowPointOut(
                date=d.strftime("%b %-d"),
                in_amount=cell["in"],
                out_amount=cell["out"],
                net=cell["in"] - cell["out"],
            )
        )

    # ------------ Expense breakdown (this month, by category) ------------
    expense_rows = db.execute(
        select(
            BankTransaction.description,
            BankTransaction.amount,
            Vendor.name,
        )
        .outerjoin(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= month_start,
        )
    ).all()
    cat_totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for desc_text, amt, vname in expense_rows:
        cat = _categorize(desc_text, vname)
        cat_totals[cat] += Decimal(amt or 0)
    expense_breakdown = [
        CategorySliceOut(name=name, value=v, color=color)
        for (name, color), v in sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)
        if v > 0
    ]

    # ------------ Receivables aging ------------
    aging_buckets = {
        "0–30": Decimal("0"),
        "31–60": Decimal("0"),
        "61–90": Decimal("0"),
        "90+": Decimal("0"),
    }
    open_invoices = list(
        db.scalars(
            select(Invoice).where(
                Invoice.org_id == org_id,
                Invoice.type == "sales",
                Invoice.status != "paid",
                Invoice.due_date.isnot(None),
            )
        )
    )
    for inv in open_invoices:
        if inv.due_date is None:
            continue
        days = (today - inv.due_date).days
        if days <= 30:
            aging_buckets["0–30"] += Decimal(inv.total)
        elif days <= 60:
            aging_buckets["31–60"] += Decimal(inv.total)
        elif days <= 90:
            aging_buckets["61–90"] += Decimal(inv.total)
        else:
            aging_buckets["90+"] += Decimal(inv.total)
    receivables_aging = [
        AgingBucketOut(bucket=b, amount=a) for b, a in aging_buckets.items()
    ]

    # ------------ Top vendors (this month, by debit spend) ------------
    vendor_rows = db.execute(
        select(
            Vendor.name,
            func.coalesce(func.sum(BankTransaction.amount), 0).label("amt"),
        )
        .join(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= month_start,
        )
        .group_by(Vendor.name)
        .order_by(desc("amt"))
        .limit(5)
    ).all()
    # Same query for the previous month, for delta.
    prev_month_end = month_start - timedelta(days=1)
    prev_month_first = prev_month_end.replace(day=1)
    vendor_prev_map: dict[str, Decimal] = {}
    for name, amt in db.execute(
        select(
            Vendor.name,
            func.coalesce(func.sum(BankTransaction.amount), 0),
        )
        .join(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= prev_month_first,
            BankTransaction.txn_date <= prev_month_end,
        )
        .group_by(Vendor.name)
    ).all():
        vendor_prev_map[name] = Decimal(amt)

    top_vendors = [
        CounterpartyRowOut(
            name=name,
            amount=Decimal(amt),
            delta_pct=_signed_delta_pct(Decimal(amt), vendor_prev_map.get(name, Decimal("0"))),
        )
        for name, amt in vendor_rows
    ]

    # ------------ Top clients (this month, by credit inflow) ------------
    client_rows = db.execute(
        select(
            Client.name,
            func.coalesce(func.sum(BankTransaction.amount), 0).label("amt"),
        )
        .join(Client, Client.id == BankTransaction.matched_client_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "credit",
            BankTransaction.txn_date >= month_start,
        )
        .group_by(Client.name)
        .order_by(desc("amt"))
        .limit(5)
    ).all()
    client_prev_map: dict[str, Decimal] = {}
    for name, amt in db.execute(
        select(Client.name, func.coalesce(func.sum(BankTransaction.amount), 0))
        .join(Client, Client.id == BankTransaction.matched_client_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "credit",
            BankTransaction.txn_date >= prev_month_first,
            BankTransaction.txn_date <= prev_month_end,
        )
        .group_by(Client.name)
    ).all():
        client_prev_map[name] = Decimal(amt)

    top_clients = [
        CounterpartyRowOut(
            name=name,
            amount=Decimal(amt),
            delta_pct=_signed_delta_pct(Decimal(amt), client_prev_map.get(name, Decimal("0"))),
        )
        for name, amt in client_rows
    ]

    # ------------ Insights (live, non-dismissed, latest 4) ------------
    insight_rows = list(
        db.scalars(
            select(Insight)
            .where(Insight.org_id == org_id, Insight.dismissed_at.is_(None))
            .order_by(desc(Insight.created_at))
            .limit(4)
        )
    )
    insights = [InsightOut.model_validate(r) for r in insight_rows]

    # ------------ Naive cash forecast (next 30 days) ------------
    # mean daily net flow over the last 14 days
    last14_start = today - timedelta(days=14)
    last14_credits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "credit",
                BankTransaction.txn_date >= last14_start,
            )
        )
        or 0
    )
    last14_debits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "debit",
                BankTransaction.txn_date >= last14_start,
            )
        )
        or 0
    )
    daily_net = (last14_credits - last14_debits) / Decimal("14")

    forecast: list[ForecastPointOut] = []
    base = cash_now
    for i in range(30):
        d = today + timedelta(days=i)
        projected = base + daily_net * Decimal(i)
        band = projected * Decimal("0.15") + Decimal(i) * Decimal("2000")
        forecast.append(
            ForecastPointOut(
                date=d.strftime("%b %-d"),
                forecast=projected,
                lower_band=projected - band,
                upper_band=projected + band,
            )
        )

    # ------------ Compliance checks ------------
    # Sales invoices with a counterparty GSTIN.
    sales_total = int(
        db.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.org_id == org_id, Invoice.type == "sales")
        )
        or 0
    )
    sales_with_gstin = int(
        db.scalar(
            select(func.count())
            .select_from(Invoice.__table__.join(
                Client.__table__, Invoice.client_id == Client.id, isouter=True
            ))
            .where(
                Invoice.org_id == org_id,
                Invoice.type == "sales",
                Client.gstin.isnot(None),
            )
        )
        or 0
    )
    receipts_total = int(
        db.scalar(
            select(func.count())
            .select_from(Receipt)
            .where(Receipt.org_id == org_id)
        )
        or 0
    )
    receipts_missing_vendor = int(
        db.scalar(
            select(func.count())
            .select_from(Receipt)
            .where(Receipt.org_id == org_id, Receipt.vendor_id.is_(None))
        )
        or 0
    )
    latest_stmt_date = db.scalar(
        select(func.max(BankTransaction.txn_date)).where(BankTransaction.org_id == org_id)
    )

    compliance: list[ComplianceRowOut] = []
    if sales_total:
        pct = int(sales_with_gstin / sales_total * 100)
        compliance.append(
            ComplianceRowOut(
                status="ok" if pct >= 95 else "warn",
                label=f"{pct}% of sales invoices have GSTIN",
            )
        )
    if receipts_total:
        compliance.append(
            ComplianceRowOut(
                status="warn" if receipts_missing_vendor else "ok",
                label=(
                    f"{receipts_missing_vendor} receipt(s) missing vendor"
                    if receipts_missing_vendor
                    else "All receipts have a vendor"
                ),
            )
        )
    if latest_stmt_date is not None:
        compliance.append(
            ComplianceRowOut(
                status="ok",
                label=f"Bank statements complete through {latest_stmt_date.strftime('%b %-d')}",
            )
        )
    if not compliance:
        compliance.append(
            ComplianceRowOut(status="warn", label="No documents yet — upload to get checks.")
        )

    return DashboardSummaryOut(
        cash_position=kpis["cash_position"],
        receivables=kpis["receivables"],
        payables=kpis["payables"],
        net_flow_mtd=kpis["net_flow_mtd"],
        cash_flow=cash_flow,
        expense_breakdown=expense_breakdown,
        receivables_aging=receivables_aging,
        forecast=forecast,
        top_vendors=top_vendors,
        top_clients=top_clients,
        insights=insights,
        compliance=compliance,
        has_any_data=bank_txn_count > 0,
        bank_txn_count=bank_txn_count,
    )
