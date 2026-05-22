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
    (re.compile(r"\b(hdfc\s*(flexi|asset|elss|cap|fund)|axis\s*(fund|elss)|sbi\s*(fund|elss)|icici\s*(fund|elss)|nippon\s*(fund)|kotak\s*(fund)|parag\s*parikh|ppfas|mirae|quant\s*(fund|mutual)|uti\s*(fund|mutual)|tata\s*(fund|mutual)|edelweiss\s*(fund|mutual)|dsp\s*(fund|mutual)|aditya\s*birla\s*(fund|mutual)|motilal\s*oswal)\b", re.IGNORECASE), "Investments", "#0d9488"),
    # ISIN code (INF followed by 9 alphanumerics) — guaranteed mutual-fund
    # marker that survives any prefix/suffix in the description.
    (re.compile(r"\bINF[0-9A-Z]{9}\b", re.IGNORECASE), "Investments", "#0d9488"),
    # NSE/BSE Clearing transfers — bank statements often glue them as one
    # token like "NSEClearingNewMutualFund", which the \b-anchored "nse"
    # rule above misses. Match without word-boundary on the right.
    (re.compile(r"(nse|bse)\s*clearing|clearing\s*new\s*mutual|flexi\s*cap|elss|nav\s*\d|isin\b|folio\s*(no|number)|scheme\s*name|asset\s*management|amc\b", re.IGNORECASE), "Investments", "#0d9488"),
    # Stamp duty in a fund/ISIN/scheme context is part of an MF buy.
    (re.compile(r"stamp\s*duty.*(fund|scheme|inf[0-9a-z]{9}|cap)", re.IGNORECASE), "Investments", "#0d9488"),
    # Insurance — life, health, term.
    (re.compile(r"\b(insurance|policy|premium|lic|hdfc\s*life|term\s*plan|mediclaim|health\s*plan|max\s*life|tata\s*aig)\b", re.IGNORECASE), "Insurance", "#16a34a"),
    # People & ops
    (re.compile(r"\bsalary|payroll|wages?\b", re.IGNORECASE), "Payroll", "#6366f1"),
    # Person-to-person bank transfers — common founder pattern (paying staff,
    # contractors, family members, reimbursements). Without this rule these
    # land in "Other" and drown out genuine spend categories on the donut.
    # Matches:
    #   "TRFR TO:NAME"            (HDFC/ICICI pattern)
    #   "NEFT:IN.../BANKNAME/NAME"
    #   "IMPS-... -NAME-..."
    #   "UPI-NAME-..."
    (re.compile(r"\b(trfr\s*to|neft.*[a-z]+\s*bank|imps[-/\s]|upi[-/\s])\b", re.IGNORECASE), "Personal transfers", "#9333ea"),
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


# ---------------------------------------------------------------------------
# Mutual-fund triplet dedupe
#
# Bank/MF statements for a single fund buy often emit THREE rows:
#   • "Gross Purchase - <Scheme> - <ISIN>"  ← gross outflow
#   • "Stamp Duty   - <Scheme> - <ISIN>"   ← govt stamp on the buy
#   • "Net Purchase - <Scheme> - <ISIN>"   ← what actually hit the bank
# Only Net Purchase reflects the real cash movement; summing all three
# triple-counts the spend (₹100 Cr looks like ₹200 Cr+ on the dashboard).
# We dedupe at query time so the underlying rows remain auditable but the
# totals reflect reality. Convention: prefer Net, then Gross, then Stamp.
# ---------------------------------------------------------------------------

_ISIN_RE = re.compile(r"\bINF[0-9A-Z]{9}\b", re.IGNORECASE)


def _extract_isin(desc: str) -> Optional[str]:
    """Return the 12-char ISIN (INF + 9 alphanumerics) if present in desc."""
    m = _ISIN_RE.search(desc or "")
    return m.group(0).upper() if m else None


def _mf_row_role(desc: str) -> Optional[str]:
    """Identify which leg of an MF triplet a row represents.
    Returns 'net' | 'gross' | 'stamp' | None."""
    s = (desc or "").lower()
    if "net purchase" in s:
        return "net"
    if "gross purchase" in s:
        return "gross"
    if "stamp duty" in s and ("fund" in s or "scheme" in s or _ISIN_RE.search(s or "")):
        return "stamp"
    return None


def _dedupe_mf_breakdown(
    rows: list,
    *,
    date_idx: int = 0,
    desc_idx: int = 1,
) -> list:
    """Drop redundant Gross + Stamp Duty rows when a Net Purchase exists for
    the same (txn_date, ISIN). Each input row is a tuple (or row-proxy) where
    ``rows[i][date_idx]`` is the txn date and ``rows[i][desc_idx]`` is the
    description. Other columns pass through unchanged.

    Returns a new list containing only the surviving rows. Order is preserved.
    """
    if not rows:
        return list(rows)

    # Group row indices by (date, isin), but only for rows that look like
    # part of an MF triplet (have both an ISIN and a recognized role).
    groups: dict[tuple, list[int]] = defaultdict(list)
    roles_per_row: dict[int, str] = {}
    for i, row in enumerate(rows):
        try:
            dt = row[date_idx]
            desc = row[desc_idx]
        except (IndexError, TypeError):
            continue
        isin = _extract_isin(desc or "")
        role = _mf_row_role(desc or "")
        if isin and role:
            groups[(dt, isin)].append(i)
            roles_per_row[i] = role

    drop: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        role_to_idx = {roles_per_row[i]: i for i in idxs}
        if "net" in role_to_idx:
            keep = role_to_idx["net"]
        elif "gross" in role_to_idx:
            keep = role_to_idx["gross"]
        else:
            continue
        for i in idxs:
            if i != keep:
                drop.add(i)

    return [r for i, r in enumerate(rows) if i not in drop]


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


_DELTA_PCT_CAP = 999.0  # display ceiling — anything bigger is "uninformative"
_DELTA_PCT_MIN_BASE = Decimal("1000")  # if |prev| < this, the % is meaningless


def _signed_delta_pct(curr: Decimal, prev: Decimal) -> float:
    """Signed percentage delta from prev → curr, with two safety rails:

    1. If the comparison base is tiny (|prev| < ₹1,000) the percentage is
       essentially meaningless — e.g. ₹50 → ₹1 Cr would show 2,000,000% which
       drowns out any real signal. We return 0.0 in that case; callers/UI
       can choose to render "—" instead of a number.
    2. Even with a reasonable base, the result is clamped to ±999% so a
       genuine 5,000% change just shows as ">999% up" without inflating the
       sparkline.
    """
    abs_prev = abs(prev)
    if abs_prev == 0 or abs_prev < _DELTA_PCT_MIN_BASE:
        # Both zero → no change. One side near zero → not comparable.
        if curr == 0 and prev == 0:
            return 0.0
        return 0.0
    raw = float((curr - prev) / prev * 100)
    if raw > _DELTA_PCT_CAP:
        return _DELTA_PCT_CAP
    if raw < -_DELTA_PCT_CAP:
        return -_DELTA_PCT_CAP
    return raw


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
    # Pull raw txns (not aggregated) so we can dedupe MF triplets before
    # summing — otherwise the daily debits get triple-counted on fund-buy days.
    raw_rows = db.execute(
        select(
            BankTransaction.txn_date,
            BankTransaction.description,
            BankTransaction.direction,
            BankTransaction.amount,
        )
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
    ).all()
    raw_rows = _dedupe_mf_breakdown(raw_rows, date_idx=0, desc_idx=1)
    by_day: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"in": Decimal("0"), "out": Decimal("0")}
    )
    for d, _desc, direction, amt in raw_rows:
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
    # Dedupe MF triplets so a single ₹100 Cr buy doesn't appear as ₹300 Cr
    # spread across the stacked area.
    cat_rows = _dedupe_mf_breakdown(cat_rows, date_idx=0, desc_idx=1)

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
    # Combines TWO sources:
    #   1. Bank-transaction debits (cash that actually went out).
    #   2. Purchase invoices (money billed to us — usually pays via a bank
    #      txn later but for tenants who haven't uploaded statements yet,
    #      the invoices ARE the only signal of what they're spending on).
    #
    # We don't dedupe here because we don't yet have invoice→bank linking
    # (Session 3 invoice reconciliation). Instead we tag each contribution
    # with its source so the drill-down can show "invoiced ₹X / paid ₹Y".
    cat_totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))

    bank_rows = db.execute(
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
    bank_rows = _dedupe_mf_breakdown(bank_rows, date_idx=0, desc_idx=1)
    for _d, desc_text, amt, vname, vcat in bank_rows:
        cat = _categorize(desc_text, vname, vcat)
        cat_totals[cat] += Decimal(amt or 0)

    # Purchase invoices in the same window — keyed by issue_date to match
    # the chart's time window.
    invoice_rows = db.execute(
        select(
            Invoice.invoice_number,
            Invoice.total,
            Vendor.name,
            Vendor.default_expense_category,
        )
        .outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
        .where(
            Invoice.org_id == org_id,
            Invoice.type == "purchase",
            Invoice.issue_date >= window_start,
            Invoice.issue_date <= window_end,
        )
    ).all()
    for inv_num, total, vname, vcat in invoice_rows:
        # Use the invoice number as the "description" — it gives the
        # categorizer a chance to read AWS / Google / etc. out of it.
        # The vendor name is the bigger signal anyway.
        cat = _categorize(inv_num or "", vname, vcat)
        cat_totals[cat] += Decimal(total or 0)

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

    target = category.strip()
    color = ""
    grouped: dict[str, dict] = {}
    total = Decimal("0")
    txn_count = 0

    # Source 1 — bank-transaction debits
    bank_rows = db.execute(
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
    bank_rows = _dedupe_mf_breakdown(bank_rows, date_idx=0, desc_idx=1)
    for _d, desc_text, amt, vname, vcat in bank_rows:
        cat_name, cat_color = _categorize(desc_text, vname, vcat)
        if cat_name != target:
            continue
        if not color:
            color = cat_color
        amt_dec = Decimal(amt or 0)
        total += amt_dec
        txn_count += 1
        key = (vname or _short_desc(desc_text)).strip() or "(unlabeled)"
        bucket = grouped.setdefault(
            key,
            {
                "vendor_name": vname,
                "description_sample": desc_text or "",
                "txn_count": 0,
                "total": Decimal("0"),
            },
        )
        bucket["txn_count"] += 1
        bucket["total"] += amt_dec
        if len(desc_text or "") > len(bucket["description_sample"]):
            bucket["description_sample"] = desc_text or ""

    # Source 2 — purchase invoices (the new addition that surfaces mutual
    # funds + AWS + Google + CESC etc. for tenants whose bank statement
    # doesn't cover everything).
    inv_rows = db.execute(
        select(
            Invoice.invoice_number,
            Invoice.total,
            Vendor.name,
            Vendor.default_expense_category,
        )
        .outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
        .where(
            Invoice.org_id == org_id,
            Invoice.type == "purchase",
            Invoice.issue_date >= window_start,
            Invoice.issue_date <= window_end,
        )
    ).all()
    for inv_num, inv_total, vname, vcat in inv_rows:
        cat_name, cat_color = _categorize(inv_num or "", vname, vcat)
        if cat_name != target:
            continue
        if not color:
            color = cat_color
        amt_dec = Decimal(inv_total or 0)
        total += amt_dec
        txn_count += 1
        key = (vname or _short_desc(inv_num or "")).strip() or "(unlabeled)"
        bucket = grouped.setdefault(
            key,
            {
                "vendor_name": vname,
                "description_sample": f"Invoice {inv_num or ''}",
                "txn_count": 0,
                "total": Decimal("0"),
            },
        )
        bucket["txn_count"] += 1
        bucket["total"] += amt_dec

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


# ---------------------------------------------------------------------------
# Investment activity widget
# ---------------------------------------------------------------------------


_AMC_HINTS = [
    "parag parikh", "ppfas",
    "hdfc", "axis", "sbi", "icici", "nippon", "kotak", "mirae",
    "quant", "uti", "tata", "edelweiss", "dsp", "aditya birla", "motilal oswal",
]


_BANK_REF_INLINE_RE = re.compile(r"\b[A-Za-z]{3,5}\d{10,}\b")


def _scheme_label(desc: str, vendor_name: Optional[str]) -> str:
    """Best-effort scheme label for an investment txn. Prefers an AMC + scheme
    snippet from the description, falls back to vendor name, then a short desc.

    Examples:
      "Net Purchase - Parag Parikh Flexi Cap Fund - INF879O01019 - NAV ..."
        → "Parag Parikh Flexi Cap Fund"
      "RTGS/.../NSEClearingNewMutualFund/UN..." → "NSE Clearing"
    """
    s = (desc or "").strip()
    low = s.lower()

    # Try to find an AMC name and grab the run of words that follow.
    for amc in _AMC_HINTS:
        i = low.find(amc)
        if i == -1:
            continue
        # Take ~60 chars from the AMC onward, trim at hyphen/pipe/comma so we
        # don't drag in NAV / Units / ISIN tails.
        chunk = s[i : i + 80]
        for sep in [" - ", "-", "|", ","]:
            cut = chunk.find(sep)
            if cut > 0:
                chunk = chunk[:cut]
                break
        chunk = chunk.strip()
        if chunk:
            # Title-case nicely (preserve all-caps acronyms like SBI, UTI).
            words = []
            for w in chunk.split():
                words.append(w if w.isupper() and len(w) <= 4 else w.capitalize())
            return " ".join(words)

    if "nseclearing" in low or "nse clearing" in low:
        return "NSE Clearing"
    if "bseclearing" in low or "bse clearing" in low:
        return "BSE Clearing"
    if "mutual fund redemption" in low or "mutual_fund_redemption" in low:
        return "Mutual Fund Redemption"
    if "ppfas" in low:
        return "Parag Parikh (PPFAS)"

    # Fall back to vendor name only if it doesn't look like a bank RTGS ref.
    if vendor_name and not _BANK_REF_INLINE_RE.fullmatch(vendor_name.strip()):
        return vendor_name

    # Strip "Net Purchase / Gross Purchase / Stamp Duty -" prefix if present.
    for prefix in ("net purchase", "gross purchase", "stamp duty"):
        if low.startswith(prefix):
            rest = s[len(prefix):].lstrip(" -:")[:60]
            if rest and not _BANK_REF_INLINE_RE.fullmatch(rest.strip()):
                return rest

    # Final fallback: short description, but skip if it's just a bank ref.
    short = s[:60]
    if _BANK_REF_INLINE_RE.fullmatch(short.strip()):
        return "Other investment"
    return short


class InvestmentSchemeOut(BaseModel):
    scheme: str
    invested: Decimal
    redeemed: Decimal
    net: Decimal
    txn_count: int


class InvestmentActivityOut(BaseModel):
    window_start: date
    window_end: date
    invested_total: Decimal
    redeemed_total: Decimal
    net_invested: Decimal
    txn_count_in: int   # debits (money into investments)
    txn_count_out: int  # credits (redemptions out of investments)
    by_scheme: list[InvestmentSchemeOut]


@router.get(
    "/investment-activity",
    response_model=InvestmentActivityOut,
    summary="Net invested vs redeemed for the selected window",
)
def investment_activity(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> InvestmentActivityOut:
    """Money flowing INTO investments (debits classified as Investments) vs
    money flowing OUT (credits — redemptions, SWP, dividend reinvestments
    going back to bank). Net = invested − redeemed.

    Dedupes MF triplets (Gross/Stamp/Net) before summing so the headline
    figures reflect real cash movement, not statement breakdown rows."""
    real_today = _today_utc()
    latest_txn_date = db.scalar(
        select(func.max(BankTransaction.txn_date)).where(BankTransaction.org_id == org_id)
    )
    if latest_txn_date is not None and (real_today - latest_txn_date).days > 30:
        today = latest_txn_date
    else:
        today = real_today
    if from_date and to_date:
        window_start, window_end = (
            (from_date, to_date) if from_date <= to_date else (to_date, from_date)
        )
    else:
        window_start = today - timedelta(days=30)
        window_end = today

    rows = db.execute(
        select(
            BankTransaction.txn_date,
            BankTransaction.description,
            BankTransaction.direction,
            BankTransaction.amount,
            Vendor.name,
            Vendor.default_expense_category,
        )
        .outerjoin(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.txn_date >= window_start,
            BankTransaction.txn_date <= window_end,
        )
    ).all()
    rows = _dedupe_mf_breakdown(rows, date_idx=0, desc_idx=1)

    invested = Decimal("0")
    redeemed = Decimal("0")
    cnt_in = 0
    cnt_out = 0
    # scheme → {invested, redeemed, count}
    by_scheme: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"invested": Decimal("0"), "redeemed": Decimal("0"), "txn_count": 0}
    )

    for _d, desc, direction, amt, vname, vcat in rows:
        cat, _color = _categorize(desc, vname, vcat)
        if cat != "Investments":
            continue
        amt_dec = Decimal(amt or 0)
        scheme = _scheme_label(desc or "", vname)
        bucket = by_scheme[scheme]
        bucket["txn_count"] += 1  # type: ignore[operator]
        if direction == "debit":
            invested += amt_dec
            cnt_in += 1
            bucket["invested"] += amt_dec  # type: ignore[operator]
        else:
            redeemed += amt_dec
            cnt_out += 1
            bucket["redeemed"] += amt_dec  # type: ignore[operator]

    scheme_list = sorted(
        (
            InvestmentSchemeOut(
                scheme=name,
                invested=b["invested"],  # type: ignore[arg-type]
                redeemed=b["redeemed"],  # type: ignore[arg-type]
                net=b["invested"] - b["redeemed"],  # type: ignore[operator]
                txn_count=b["txn_count"],  # type: ignore[arg-type]
            )
            for name, b in by_scheme.items()
        ),
        key=lambda s: (s.invested + s.redeemed),
        reverse=True,
    )[:12]

    return InvestmentActivityOut(
        window_start=window_start,
        window_end=window_end,
        invested_total=invested,
        redeemed_total=redeemed,
        net_invested=invested - redeemed,
        txn_count_in=cnt_in,
        txn_count_out=cnt_out,
        by_scheme=scheme_list,
    )
