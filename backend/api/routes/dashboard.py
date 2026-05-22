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
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.deps import current_org_id
from api.schemas import (
    AgingBucketOut,
    CashFlowCategoryPointOut,
    CashFlowMetaOut,
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
    # Investments & savings — large flows that aren't really "expenses" but
    # founder probably wants to see them broken out.
    (re.compile(r"\b(sgb|sovereign\s*gold|mutual\s*fund|nse|bse|equity|equities|bond|debenture|sip|swp|stp|mf\s*purchase|fund\s*purchase|silver\s*bond)\b", re.IGNORECASE), "Investments", "#0d9488"),
    (re.compile(r"\b(hdfc\s*(flexi|asset|elss|cap|fund)|axis\s*(fund|elss)|sbi\s*(fund|elss)|icici\s*(fund|elss)|nippon\s*(fund)|kotak\s*(fund))\b", re.IGNORECASE), "Investments", "#0d9488"),
    # Insurance — life, health, term.
    (re.compile(r"\b(insurance|policy|premium|lic|hdfc\s*life|term\s*plan|mediclaim|health\s*plan|max\s*life|tata\s*aig)\b", re.IGNORECASE), "Insurance", "#16a34a"),
    # People & ops
    (re.compile(r"\bsalary|payroll|wages?\b", re.IGNORECASE), "Payroll", "#6366f1"),
    (re.compile(r"\brent\b", re.IGNORECASE), "Rent", "#8b5cf6"),
    # Day-to-day
    (re.compile(r"\b(swiggy|zomato|food|cafe|coffee|restaurant|bundl)\b", re.IGNORECASE), "Food", "#10b981"),
    (re.compile(r"\b(uber|ola|cab|rapido|metro|train|flight|travel|irctc|indigo|vistara|spicejet|airfare)\b", re.IGNORECASE), "Travel", "#06b6d4"),
    # Software & cloud
    (re.compile(r"\b(aws|azure|gcp|cloud|github|saas|software|netflix|spotify|prime|hotstar|openai|anthropic|claude|chatgpt)\b", re.IGNORECASE), "Software", "#0ea5e9"),
    # Tax (more specific so it doesn't swallow other things)
    (re.compile(r"\b(gst\b|gstn|tds\b|advance\s*tax|income\s*tax\s*dep|i\.?t\.?\s*dep|26q|24q|tax\s*payment|challan)\b", re.IGNORECASE), "Tax", "#f43f5e"),
    # Marketing
    (re.compile(r"\b(marketing|ads?\b|advert|facebook|google\s*ads|linkedin|meta\s*ads)\b", re.IGNORECASE), "Marketing", "#f59e0b"),
    # Lending
    (re.compile(r"\b(loan|emi|mpokket|cred\b|interest\s*paid|nbfc|nach\s*debit|repayment)\b", re.IGNORECASE), "Finance", "#a855f7"),
    # Utilities
    (re.compile(r"\b(airtel|jio|vi\b|vodafone|bsnl|electricity|cesc|tata\s*power|water\s*bill|gas|utility\s*bill|broadband|wifi|internet)\b", re.IGNORECASE), "Utilities", "#14b8a6"),
    # Shopping & e-commerce
    (re.compile(r"\b(amazon|flipkart|myntra|shopping|reliance|dmart|big\s*basket|blinkit|zepto)\b", re.IGNORECASE), "Shopping", "#ec4899"),
    # Bank fees + ATM / cash movements — surface these as their own category
    # so the "Other" bucket isn't dominated by bank noise.
    (re.compile(r"\b(bank\s*charge|sms\s*charge|annual\s*fee|maintenance\s*fee|service\s*charge|chq\s*return|cheque\s*return|cash\s*deposit|atm\s*withdrawal|debit\s*card\s*fee)\b", re.IGNORECASE), "Bank fees", "#64748b"),
    # Refunds & reimbursements — usually money flowing back, but for the
    # debit side of the chart we still want them surfaced.
    (re.compile(r"\b(reimbursement|reimburse|refund\b|expense\s*claim|cashback)\b", re.IGNORECASE), "Reimbursement", "#fb7185"),
]
_CATEGORY_OTHER = ("Other", "#94a3b8")

# When the user has tagged a vendor with `default_expense_category`, we use
# that string as a category name AND pick a stable color for it (so re-running
# the dashboard produces the same color for the same category name).
_DYNAMIC_PALETTE = [
    "#6366f1", "#8b5cf6", "#10b981", "#06b6d4", "#0ea5e9",
    "#f43f5e", "#f59e0b", "#a855f7", "#14b8a6", "#ec4899",
    "#0d9488", "#16a34a", "#fb7185", "#84cc16", "#eab308",
]


def _color_for_dynamic(category_name: str) -> str:
    """Stable hash-based color picker for user-defined category names so they
    keep the same color across page loads."""
    idx = abs(hash(category_name.lower())) % len(_DYNAMIC_PALETTE)
    return _DYNAMIC_PALETTE[idx]


def _categorize(
    description: str,
    vendor_name: Optional[str],
    vendor_default_category: Optional[str] = None,
) -> tuple[str, str]:
    """Classify a transaction into (category_name, color).

    Priority order (most-specific to least):
      1. The vendor's user-set `default_expense_category` — when a founder /
         CA has tagged a vendor with a category, that overrides every regex.
         This is what makes categories DYNAMIC: as you tag vendors via the
         feedback loop, the chart picks them up automatically.
      2. The static regex rules in `_CATEGORY_RULES`.
      3. "Other" fallback.
    """
    if vendor_default_category and vendor_default_category.strip():
        name = vendor_default_category.strip()
        return name, _color_for_dynamic(name)

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
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> DashboardSummaryOut:
    """Build the dashboard summary.

    Date filter (Phase C):
      - `from_date` and `to_date` (ISO YYYY-MM-DD) restrict the window used
        by cash flow, expense breakdown, top vendors/clients, MTD net flow,
        and recurring detection.
      - When omitted, defaults to "last 30 days" anchored on the latest
        bank-txn date — preserves the pre-filter behavior.

    Receivables aging, total receivables, total payables, compliance, and
    insights are NOT date-filtered: they're a snapshot of the org's current
    obligations.
    """
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

    # ---- Date filter ---------------------------------------------------
    # If the caller specified from_date + to_date, every "windowed" metric
    # (cash flow chart, MTD net flow, expense breakdown, top vendors/clients)
    # uses that range. Otherwise default to the last 30 days from `today`.
    if from_date is not None and to_date is not None:
        if from_date > to_date:
            # Tolerate reversed input.
            from_date, to_date = to_date, from_date
        window_start = from_date
        window_end = to_date
        # "This month" semantics for MTD KPI shift to "this range" totals.
        month_start = from_date
        # Comparison period = same length immediately before the window.
        span_days = max(1, (window_end - window_start).days + 1)
        prev_window_end = window_start - timedelta(days=1)
        prev_window_start = prev_window_end - timedelta(days=span_days - 1)
        # Drive the chart x-axis from the explicit range.
        thirty_days_ago = window_start
        sixty_days_ago = prev_window_start
        # Force the latest-data anchor to the window end so dashboards
        # tooltipped on the right dates.
        today = window_end
    else:
        month_start = today.replace(day=1)
        thirty_days_ago = today - timedelta(days=30)
        sixty_days_ago = today - timedelta(days=60)
        window_start = thirty_days_ago
        window_end = today
        prev_window_start = sixty_days_ago
        prev_window_end = thirty_days_ago

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

    # ------------ KPI: Net flow over the selected window ------------
    # "MTD" is the legacy label — it's really "net flow over whatever window
    # the user selected." When no date filter is set, window_start = month_start
    # and the behavior matches the original MTD semantics.
    mtd_credits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "credit",
                BankTransaction.txn_date >= window_start,
                BankTransaction.txn_date <= window_end,
            )
        )
        or 0
    )
    mtd_debits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "debit",
                BankTransaction.txn_date >= window_start,
                BankTransaction.txn_date <= window_end,
            )
        )
        or 0
    )
    net_flow_now = mtd_credits - mtd_debits

    # Comparison: equal-length window immediately before the current one.
    prev_credits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "credit",
                BankTransaction.txn_date >= prev_window_start,
                BankTransaction.txn_date <= prev_window_end,
            )
        )
        or 0
    )
    prev_debits = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.org_id == org_id,
                BankTransaction.direction == "debit",
                BankTransaction.txn_date >= prev_window_start,
                BankTransaction.txn_date <= prev_window_end,
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
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
        .group_by(BankTransaction.txn_date, BankTransaction.direction)
    ).all()
    by_day: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"in": Decimal("0"), "out": Decimal("0")}
    )
    for d, direction, amt in daily_rows:
        key = "in" if direction == "credit" else "out"
        by_day[d][key] += Decimal(amt or 0)

    # Chart length = the actual window the user selected (1 day to ~1 year).
    # Cap at 366 to keep response size bounded if someone picks 5 years.
    span_days = min(366, max(1, (window_end - window_start).days + 1))
    cash_flow: list[CashFlowPointOut] = []
    for i in range(span_days):
        d = window_start + timedelta(days=i)
        cell = by_day.get(d, {"in": Decimal("0"), "out": Decimal("0")})
        cash_flow.append(
            CashFlowPointOut(
                date=d.strftime("%b %-d"),
                in_amount=cell["in"],
                out_amount=cell["out"],
                net=cell["in"] - cell["out"],
            )
        )

    # ------------ Cash flow by category (advanced chart mode) ------------
    # Daily breakdown of debits grouped by the same category buckets the
    # donut uses. Lets the frontend stack-area "where my money went" by day.
    # We pull vendor.default_expense_category so the categorizer can prefer
    # user-tagged categories over the regex rules.
    cat_rows = db.execute(
        select(
            BankTransaction.txn_date,
            BankTransaction.description,
            BankTransaction.amount,
            Vendor.name,
            Vendor.default_expense_category,
        )
        .outerjoin(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
    ).all()

    # day -> {category_name: amount}
    by_day_cat: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    # Track which categories actually appeared so the legend only shows real ones.
    seen_cats: dict[str, str] = {}  # name → color
    for d, desc_text, amt, vname, vcat in cat_rows:
        cat_name, cat_color = _categorize(desc_text, vname, vcat)
        by_day_cat[d][cat_name] += Decimal(amt or 0)
        seen_cats.setdefault(cat_name, cat_color)

    cash_flow_by_category: list[CashFlowCategoryPointOut] = []
    for i in range(span_days):
        d = window_start + timedelta(days=i)
        cats = by_day_cat.get(d, {})
        cash_flow_by_category.append(
            CashFlowCategoryPointOut(
                date=d.strftime("%b %-d"),
                # Convert defaultdict → plain dict so Pydantic serializes cleanly.
                categories={k: v for k, v in cats.items()},
            )
        )

    # ------------ Cash flow meta — anomaly markers + category palette ------
    # Set of "MMM d" dates that have a vendor_amount_anomaly insight in the
    # selected window. Frontend draws red dots on these days.
    anomaly_dates: list[str] = []
    anomaly_rows = db.execute(
        select(Insight.supporting_data).where(
            Insight.org_id == org_id,
            Insight.type == "vendor_amount_anomaly",
            Insight.dismissed_at.is_(None),
        )
    ).all()
    for (sd,) in anomaly_rows:
        if not isinstance(sd, dict):
            continue
        iso_str = sd.get("observed_on")
        if not iso_str:
            continue
        try:
            y, m, day = (int(p) for p in str(iso_str).split("-")[:3])
            occ_d = date(y, m, day)
        except (ValueError, TypeError):
            continue
        if window_start <= occ_d <= window_end:
            label = occ_d.strftime("%b %-d")
            if label not in anomaly_dates:
                anomaly_dates.append(label)

    cash_flow_meta = CashFlowMetaOut(
        anomaly_dates=anomaly_dates,
        category_palette=sorted(seen_cats.items(), key=lambda kv: kv[0]),
    )

    # ------------ Expense breakdown (selected window, by category) ------------
    expense_rows = db.execute(
        select(
            BankTransaction.description,
            BankTransaction.amount,
            Vendor.name,
            Vendor.default_expense_category,
        )
        .outerjoin(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
    ).all()
    cat_totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for desc_text, amt, vname, vcat in expense_rows:
        cat = _categorize(desc_text, vname, vcat)
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

    # ------------ Top vendors (selected window, by debit spend) ------------
    vendor_rows = db.execute(
        select(
            Vendor.name,
            func.coalesce(func.sum(BankTransaction.amount), 0).label("amt"),
        )
        .join(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
        .group_by(Vendor.name)
        .order_by(desc("amt"))
        .limit(5)
    ).all()
    # Comparison: the equal-length window immediately before this one.
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
            BankTransaction.txn_date >= prev_window_start,
            BankTransaction.txn_date <= prev_window_end,
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

    # ------------ Top clients (selected window, by credit inflow) ------------
    client_rows = db.execute(
        select(
            Client.name,
            func.coalesce(func.sum(BankTransaction.amount), 0).label("amt"),
        )
        .join(Client, Client.id == BankTransaction.matched_client_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "credit",
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
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
            BankTransaction.txn_date >= prev_window_start,
            BankTransaction.txn_date <= prev_window_end,
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

    # ------------ Seasonal cash forecast (next 30 days) ------------
    # Tier-1 learning: replaces the naive linear from-last-14-days with a
    # day-of-month seasonal model that uses 6 months of history. Falls back
    # to running-average daily net when there isn't enough history for a
    # given day-of-month (e.g. day 31 in a Feb-only history).
    from services.forecasting import seasonal_forecast

    seasonal_points = seasonal_forecast(db, org_id=org_id, starting_from=today)
    forecast: list[ForecastPointOut] = []
    running_cash = cash_now
    for p in seasonal_points:
        running_cash = running_cash + p.forecast
        # Bands widen the further out we project — compounding uncertainty.
        days_out = (p.date - today).days
        widening = Decimal(days_out) * Decimal("1500")
        lower = running_cash + p.lower_band - p.forecast - widening
        upper = running_cash + p.upper_band - p.forecast + widening
        forecast.append(
            ForecastPointOut(
                date=p.date.strftime("%b %-d"),
                forecast=running_cash,
                lower_band=lower,
                upper_band=upper,
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

    # ------------ Recurring outflows (Tier-1 learning) ------------
    from common.models import RecurringPattern
    from api.schemas import RecurringOutflowOut

    rec_rows = list(
        db.scalars(
            select(RecurringPattern)
            .where(RecurringPattern.org_id == org_id)
            .order_by(desc(RecurringPattern.median_amount))
            .limit(10)
        )
    )
    recurring_outflows: list[RecurringOutflowOut] = []
    for r in rec_rows:
        status = "on_track"
        days_until_due: int | None = None
        if r.cadence == "monthly" and r.expected_day_of_month is not None:
            # Same logic as services/recurring._expected_next_date.
            target_day = max(1, min(28, r.expected_day_of_month))
            year, month = r.last_seen_on.year, r.last_seen_on.month + 1
            if month > 12:
                year, month = year + 1, 1
            try:
                next_due = date(year, month, target_day)
            except ValueError:
                next_due = date(year, month, 28)
            delta = (next_due - today).days
            days_until_due = delta
            if delta < -5:
                status = "overdue"
            elif delta < 0:
                status = "due_soon"
            elif delta <= 3:
                status = "due_soon"
        recurring_outflows.append(
            RecurringOutflowOut(
                label=r.label,
                median_amount=r.median_amount,
                expected_day_of_month=r.expected_day_of_month,
                observed_count=r.observed_count,
                last_seen_on=r.last_seen_on.isoformat(),
                status=status,
                days_until_due=days_until_due,
            )
        )

    return DashboardSummaryOut(
        cash_position=kpis["cash_position"],
        receivables=kpis["receivables"],
        payables=kpis["payables"],
        net_flow_mtd=kpis["net_flow_mtd"],
        cash_flow=cash_flow,
        cash_flow_by_category=cash_flow_by_category,
        cash_flow_meta=cash_flow_meta,
        expense_breakdown=expense_breakdown,
        receivables_aging=receivables_aging,
        forecast=forecast,
        top_vendors=top_vendors,
        top_clients=top_clients,
        insights=insights,
        compliance=compliance,
        recurring_outflows=recurring_outflows,
        has_any_data=bank_txn_count > 0,
        bank_txn_count=bank_txn_count,
    )


# ---------------------------------------------------------------------------
# Category drill-down — "what's in this slice?"
# ---------------------------------------------------------------------------


class CategoryDetailRowOut(BaseModel):
    """One contributor inside a category slice."""

    vendor_name: Optional[str] = None
    description_sample: str
    txn_count: int
    total: Decimal


class CategoryDetailOut(BaseModel):
    category: str
    color: str
    total: Decimal
    txn_count: int
    contributors: list[CategoryDetailRowOut]


@router.get(
    "/category-detail",
    response_model=CategoryDetailOut,
    summary="Drill-down: what's inside a category slice",
)
def category_detail(
    category: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> CategoryDetailOut:
    """Return the top vendors / descriptions that contributed to the given
    category slice. Used by the donut's click-to-explore behavior.

    Date range mirrors the summary endpoint — defaults to the latest-anchored
    30-day window when not specified."""
    real_today = _today_utc()
    latest_txn_date = db.scalar(
        select(func.max(BankTransaction.txn_date)).where(BankTransaction.org_id == org_id)
    )
    if latest_txn_date is not None and (real_today - latest_txn_date).days > 30:
        today = latest_txn_date
    else:
        today = real_today
    if from_date and to_date:
        window_start, window_end = (from_date, to_date) if from_date <= to_date else (to_date, from_date)
    else:
        window_start = today - timedelta(days=30)
        window_end = today

    rows = db.execute(
        select(
            BankTransaction.description,
            BankTransaction.amount,
            Vendor.name,
            Vendor.default_expense_category,
        )
        .outerjoin(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
    ).all()

    target = category.strip()
    color = ""
    # Group by (vendor_name OR description-prefix) so the user sees what's
    # actually inside the slice, not 200 individual transactions.
    grouped: dict[str, dict] = {}
    total = Decimal("0")
    txn_count = 0
    for desc_text, amt, vname, vcat in rows:
        cat_name, cat_color = _categorize(desc_text, vname, vcat)
        if cat_name != target:
            continue
        if not color:
            color = cat_color
        amt_dec = Decimal(amt or 0)
        total += amt_dec
        txn_count += 1
        key = (vname or _short_desc(desc_text)).strip()
        if not key:
            key = "(unlabeled)"
        bucket = grouped.setdefault(
            key, {"vendor_name": vname, "description_sample": desc_text or "", "txn_count": 0, "total": Decimal("0")}
        )
        bucket["txn_count"] += 1
        bucket["total"] += amt_dec
        # Keep the longest description sample we've seen — usually more
        # informative than the bank's truncated forms.
        if len(desc_text or "") > len(bucket["description_sample"]):
            bucket["description_sample"] = desc_text or ""

    contributors = sorted(grouped.values(), key=lambda b: b["total"], reverse=True)[:limit]
    return CategoryDetailOut(
        category=target,
        color=color or "#94a3b8",
        total=total,
        txn_count=txn_count,
        contributors=[
            CategoryDetailRowOut(
                vendor_name=b["vendor_name"],
                description_sample=(b["description_sample"] or "")[:200],
                txn_count=b["txn_count"],
                total=b["total"],
            )
            for b in contributors
        ],
    )


def _short_desc(s: Optional[str]) -> str:
    if not s:
        return ""
    # Take the first 4 tokens — for things like 'INF/NEFT/.../from .../<dest>'
    # the destination tail is usually further in. Fall back to the first
    # alphabetic token.
    parts = re.split(r"[/\s\-:|]+", s)
    for p in parts:
        if p and any(c.isalpha() for c in p):
            return p[:40]
    return s[:40]
