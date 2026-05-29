"""13-week rolling cash forecast.

This is Nira's headline finance-team-facing differentiator. Comparable
tools (Trovata, Drivetrain, Cube) lead with this feature. The goal: a
CFO opens the page in the morning and sees exactly when their cash dips,
which payments cause it, and what they can do about it.

Design philosophy:

  1. **Compose, don't ML.**
     For mid-market data sizes (50k-500k transactions/year), classical
     decomposition + recurring-pattern attribution beats ML in both
     accuracy and explainability. Every line on the chart has a NAMED
     driver — "your line dips on Jul 1 because of salary + GST payment",
     not "the model predicts a dip".

  2. **Three scenarios, not five.**
     CFOs ignore five-line forecasts. Pessimistic / Likely / Optimistic
     is the universal language. We achieve scenarios by varying the
     CONFIDENCE multipliers on each driver — high-confidence drivers
     (rent, salary, recurring SaaS) barely move between scenarios;
     low-confidence ones (open AR from a slow-paying customer) swing
     widely.

  3. **Drivers are first-class data.**
     Every projected inflow/outflow becomes a `ForecastDriver` row so
     the "Why this forecast?" panel can show it. This is the trust
     mechanism — a CFO who can audit the forecast trusts the forecast.

  4. **Canonical-ledger native, bank-CSV fallback.**
     Starting cash comes from the canonical ledger when available
     (sum of cash + bank accounts), else from `bank_transactions`.
     Historical movements come from bank_transactions for now (the
     full Day-Book→canonical port is C1b, future work).

  5. **Idempotent + cheap to regenerate.**
     A forecast run is a snapshot; we keep history. Calling generate()
     twice in the same minute produces two rows — the latest one wins.
     No locks, no contention.

What's INTENTIONALLY not here yet:

  - LLM-based "narrative explanation" of the forecast. Easy to add
    once the data is in place.
  - Multi-currency. Single-INR assumed for now; expansion uses the
    canonical fx_rate_to_inr columns.
  - "What-if" scenario editor (let the user simulate "what if customer
    X pays 30 days late"). Requires a forecast adjustments table.

API surface (services-layer):

    generate_forecast(db, org_id, ...) -> CashForecastRun
    get_latest_forecast(db, org_id, entity_id=None) -> Optional[ForecastSummary]
    get_drivers(db, run_id) -> list[ForecastDriver]
"""
from __future__ import annotations

import logging
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.models import (
    BankTransaction,
    CashForecastPoint,
    CashForecastRun,
    ForecastDriver,
    Invoice,
    RecurringPattern,
    Vendor,
)
from services.canonical import dashboard_kpis

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Tunables
# -----------------------------------------------------------------------------

HORIZON_DAYS_DEFAULT = 91          # 13 weeks
LOOKBACK_DAYS_HISTORY = 180        # for residual seasonality
RECEIVABLE_LATENESS_PESSIMISTIC = 21    # pessimistic: AR pays 21d late
RECEIVABLE_LATENESS_OPTIMISTIC = -7     # optimistic: AR pays 7d early
PAYABLE_STRETCH_PESSIMISTIC = 0         # pessimistic: pay on time
PAYABLE_STRETCH_OPTIMISTIC = 14         # optimistic: stretch payables 14d
# Recurring patterns: scenarios swing by ±15% of amount × (1 - confidence)
RECURRING_SCENARIO_AMOUNT_SPREAD = Decimal("0.15")


# -----------------------------------------------------------------------------
# In-memory data classes
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class DriverDraft:
    """A projected cash event before it's persisted as a ForecastDriver."""

    kind: str
    label: str
    expected_date: date
    expected_amount_inr: Decimal
    direction: str  # 'inflow' | 'outflow'
    confidence: Decimal
    source_kind: str
    source_recurring_id: Optional[uuid.UUID] = None
    source_invoice_id: Optional[uuid.UUID] = None
    vendor_id: Optional[uuid.UUID] = None
    client_id: Optional[uuid.UUID] = None
    supporting_data: Optional[dict] = None

    def date_for_scenario(self, scenario: str) -> date:
        """Shift the expected date by scenario lateness/earliness rules.

        Only affects AR / AP — recurring patterns and scheduled events
        stay on their expected date across scenarios (rent is rent).

        Important: only shift NOT-YET-OVERDUE invoices. Once an invoice
        is overdue, its expected_date has already been pushed forward by
        the collection-window heuristic in _collect_invoice_drivers; we
        don't double-shift. Otherwise overdue receivables in the
        optimistic scenario would cluster at day 0 (creating a fake
        ₹1.6 Cr spike).

        The is_overdue check uses supporting_data.days_overdue which the
        invoice collector populates.
        """
        days_overdue = 0
        if self.supporting_data:
            days_overdue = int(self.supporting_data.get("days_overdue", 0) or 0)

        if self.kind == "open_receivable":
            if days_overdue > 0:
                # Already-late receivables: don't shift further in any
                # scenario — the heuristic already encoded the uncertainty.
                return self.expected_date
            if scenario == "pessimistic":
                return self.expected_date + timedelta(days=RECEIVABLE_LATENESS_PESSIMISTIC)
            if scenario == "optimistic":
                return self.expected_date + timedelta(days=RECEIVABLE_LATENESS_OPTIMISTIC)
        elif self.kind == "open_payable":
            if days_overdue > 0:
                # Overdue payables: assume we'll pay them in the
                # collection window already set; don't push them further.
                return self.expected_date
            if scenario == "pessimistic":
                return self.expected_date  # pay on time (worse for cash)
            if scenario == "optimistic":
                return self.expected_date + timedelta(days=PAYABLE_STRETCH_OPTIMISTIC)
        return self.expected_date

    def amount_for_scenario(self, scenario: str) -> Decimal:
        """Shift amount by confidence-weighted spread for recurring drivers.

        High-confidence recurring (rent, salary) barely moves. Low-confidence
        recurring (variable AWS) swings wider.
        """
        if self.kind in ("recurring_inflow", "recurring_outflow"):
            # Spread amount by (1 - confidence) * spread%
            spread = RECURRING_SCENARIO_AMOUNT_SPREAD * (Decimal("1") - self.confidence)
            delta = self.expected_amount_inr * spread
            if scenario == "pessimistic":
                # Pessimistic: inflows ↓, outflows ↑
                if self.direction == "inflow":
                    return self.expected_amount_inr - delta
                return self.expected_amount_inr + delta
            if scenario == "optimistic":
                if self.direction == "inflow":
                    return self.expected_amount_inr + delta
                return self.expected_amount_inr - delta
        return self.expected_amount_inr


@dataclass(slots=True)
class ForecastSummary:
    """Lightweight view of a forecast run for API consumers."""

    run_id: uuid.UUID
    as_of_date: date
    horizon_days: int
    starting_cash_inr: Decimal
    ending_cash_likely_inr: Decimal
    ending_cash_pessimistic_inr: Decimal
    ending_cash_optimistic_inr: Decimal
    runway_zero_date: Optional[date]
    drivers_count: int
    inflows_total_inr: Decimal
    outflows_total_inr: Decimal
    created_at: datetime
    points: list[dict] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Driver discovery
# -----------------------------------------------------------------------------


def _collect_recurring_drivers(
    db: Session,
    org_id: uuid.UUID,
    today: date,
    horizon_end: date,
) -> list[DriverDraft]:
    """Project each active RecurringPattern across the horizon.

    Uses pattern.expected_day_of_month + cadence to enumerate occurrences.
    Confidence = clamp(observed_count / 6, 0.4, 0.95) so a pattern seen
    6+ times gets near-perfect confidence; newer patterns swing wider.
    """
    rows = list(
        db.execute(
            select(RecurringPattern, Vendor.name.label("vendor_name"))
            .outerjoin(Vendor, Vendor.id == RecurringPattern.vendor_id)
            .where(RecurringPattern.org_id == org_id)
        )
    )

    drafts: list[DriverDraft] = []
    for row in rows:
        pattern: RecurringPattern = row[0]
        vendor_name: Optional[str] = row[1]

        if pattern.cadence != "monthly":
            continue
        if pattern.expected_day_of_month is None:
            continue

        amount = Decimal(pattern.median_amount or 0)
        if amount <= 0:
            continue

        confidence = max(
            Decimal("0.4"),
            min(Decimal("0.95"), Decimal(pattern.observed_count or 0) / Decimal(6)),
        )

        # Direction: most recurring patterns are outflows (rent, salary,
        # SaaS). If the pattern has a vendor, treat as outflow; absence
        # of vendor (or 'client'-linked patterns later) → inflow.
        direction = "outflow" if pattern.vendor_id is not None else "inflow"

        # Enumerate occurrences in the horizon
        cursor = today.replace(day=1)
        while cursor <= horizon_end:
            try:
                occurrence = cursor.replace(day=pattern.expected_day_of_month)
            except ValueError:
                # Day-of-month doesn't exist in this month (e.g. 31 in Feb)
                cursor = _add_month(cursor)
                continue
            if today <= occurrence <= horizon_end:
                drafts.append(
                    DriverDraft(
                        kind=("recurring_outflow" if direction == "outflow" else "recurring_inflow"),
                        label=f"{pattern.label or vendor_name or 'Recurring'}",
                        expected_date=occurrence,
                        expected_amount_inr=amount,
                        direction=direction,
                        confidence=confidence,
                        source_kind="recurring_pattern",
                        source_recurring_id=pattern.id,
                        vendor_id=pattern.vendor_id,
                        supporting_data={
                            "observed_count": pattern.observed_count,
                            "cadence": pattern.cadence,
                            "first_seen_on": pattern.first_seen_on.isoformat(),
                            "last_seen_on": pattern.last_seen_on.isoformat(),
                        },
                    )
                )
            cursor = _add_month(cursor)

    return drafts


def _collect_invoice_drivers(
    db: Session,
    org_id: uuid.UUID,
    today: date,
    horizon_end: date,
) -> list[DriverDraft]:
    """Project unpaid sales (receivables) + purchase (payables) invoices.

    For receivables: use due_date if present and ≥ today, else issue_date + 30d.
    For payables: use due_date if present, else issue_date + 14d.

    Confidence — varies by how far past due:
      0.85 if due ≥ today
      0.65 if 1-30 days past due (likely to still pay)
      0.45 if 31-60 days past due
      0.25 if 60+ days past due (treat as bad debt-ish)
    """
    rows = list(
        db.execute(
            select(Invoice).where(
                Invoice.org_id == org_id,
                Invoice.status != "paid",
            )
        ).scalars()
    )

    drafts: list[DriverDraft] = []
    for inv in rows:
        amount = Decimal(inv.total or 0)
        if amount <= 0:
            continue

        # Resolve expected date
        if inv.due_date is not None:
            base_date = inv.due_date
        elif inv.type == "sales":
            base_date = inv.issue_date + timedelta(days=30)
        else:
            base_date = inv.issue_date + timedelta(days=14)

        # Late receivables: project them to TODAY (treat as overdue
        # collection, not skip), so the cash forecast still shows them
        # as expected. Even-more-overdue ones get lower confidence.
        days_overdue = (today - base_date).days if base_date < today else 0
        if days_overdue > 0:
            base_date = today + timedelta(days=min(days_overdue, 21))
        if base_date > horizon_end:
            continue

        if days_overdue == 0:
            confidence = Decimal("0.85")
        elif days_overdue <= 30:
            confidence = Decimal("0.65")
        elif days_overdue <= 60:
            confidence = Decimal("0.45")
        else:
            confidence = Decimal("0.25")

        if inv.type == "sales":
            drafts.append(
                DriverDraft(
                    kind="open_receivable",
                    label=f"Invoice {inv.invoice_number}",
                    expected_date=base_date,
                    expected_amount_inr=amount,
                    direction="inflow",
                    confidence=confidence,
                    source_kind="invoice",
                    source_invoice_id=inv.id,
                    client_id=inv.client_id,
                    supporting_data={
                        "invoice_number": inv.invoice_number,
                        "issue_date": inv.issue_date.isoformat(),
                        "due_date": inv.due_date.isoformat() if inv.due_date else None,
                        "days_overdue": days_overdue,
                    },
                )
            )
        else:
            drafts.append(
                DriverDraft(
                    kind="open_payable",
                    label=f"Bill {inv.invoice_number}",
                    expected_date=base_date,
                    expected_amount_inr=amount,
                    direction="outflow",
                    confidence=confidence,
                    source_kind="invoice",
                    source_invoice_id=inv.id,
                    vendor_id=inv.vendor_id,
                    supporting_data={
                        "invoice_number": inv.invoice_number,
                        "issue_date": inv.issue_date.isoformat(),
                        "due_date": inv.due_date.isoformat() if inv.due_date else None,
                        "days_overdue": days_overdue,
                    },
                )
            )
    return drafts


def _collect_tax_calendar_drivers(
    org_id: uuid.UUID,
    today: date,
    horizon_end: date,
) -> list[DriverDraft]:
    """Indian tax calendar — advance tax + GST + TDS standard due dates.

    These are conservative single-line entries; we don't try to estimate
    AMOUNTS here (that's the tax module's job). We emit a placeholder
    driver labelled "Tax deadline — <kind>" with amount ₹0 so the
    forecast UI can show them as anchor points the CFO must plan for.

    Future: pull projected amounts from services/tax/advance_tax.py
    and services/tax/tds.py.
    """
    # Indian FY = Apr-Mar. Advance tax instalments: Jun 15, Sep 15,
    # Dec 15, Mar 15. GST monthly return: 20th. TDS payment: 7th.
    drafts: list[DriverDraft] = []
    current = today
    while current <= horizon_end:
        # GSTR-3B payment — 20th of next month (rough heuristic)
        try:
            gst_due = current.replace(day=20)
        except ValueError:
            gst_due = current
        if today < gst_due <= horizon_end:
            drafts.append(
                DriverDraft(
                    kind="scheduled_tax",
                    label="GST payment (GSTR-3B)",
                    expected_date=gst_due,
                    expected_amount_inr=Decimal("0"),
                    direction="outflow",
                    confidence=Decimal("0.5"),
                    source_kind="tax_calendar",
                    supporting_data={"tax": "gst_3b", "monthly_due_day": 20},
                )
            )
        # TDS — 7th of next month
        try:
            tds_due = _add_month(current.replace(day=1)).replace(day=7)
        except ValueError:
            tds_due = _add_month(current)
        if today < tds_due <= horizon_end:
            drafts.append(
                DriverDraft(
                    kind="scheduled_tax",
                    label="TDS payment",
                    expected_date=tds_due,
                    expected_amount_inr=Decimal("0"),
                    direction="outflow",
                    confidence=Decimal("0.5"),
                    source_kind="tax_calendar",
                    supporting_data={"tax": "tds", "monthly_due_day": 7},
                )
            )
        # Advance tax instalments (15-Jun / 15-Sep / 15-Dec / 15-Mar)
        for month in (6, 9, 12, 3):
            y = current.year
            try:
                inst = date(y, month, 15)
            except ValueError:
                continue
            if today < inst <= horizon_end:
                drafts.append(
                    DriverDraft(
                        kind="scheduled_tax",
                        label=f"Advance tax instalment ({inst.strftime('%b')})",
                        expected_date=inst,
                        expected_amount_inr=Decimal("0"),
                        direction="outflow",
                        confidence=Decimal("0.4"),
                        source_kind="tax_calendar",
                        supporting_data={"tax": "advance_tax", "due_date": inst.isoformat()},
                    )
                )
        current = _add_month(current.replace(day=1))
    return drafts


# -----------------------------------------------------------------------------
# Driver → daily-bucket aggregation
# -----------------------------------------------------------------------------


def _bucket_drivers_by_day(
    drafts: list[DriverDraft],
    today: date,
    horizon_days: int,
    scenario: str,
) -> dict[date, tuple[Decimal, Decimal]]:
    """Returns {date: (inflow_total, outflow_total)} for a given scenario.

    Drivers shift their date/amount according to scenario rules; days
    outside the horizon are clipped.

    AR/AP drivers are SMOOTHED — instead of landing all on the expected
    date (which produces step-function chart shapes), they distribute
    across a 5-day triangular window centered on the expected date.
    This better reflects reality: customers don't all pay on day-30
    exactly, payroll batches don't fire on a single Tuesday for everyone.

    Recurring patterns (salary, rent, fixed SaaS) and tax deadlines
    STAY on their expected date — those genuinely are single-day events.
    """
    horizon_end = today + timedelta(days=horizon_days)
    buckets: dict[date, tuple[Decimal, Decimal]] = {}
    for draft in drafts:
        center = draft.date_for_scenario(scenario)
        amt = draft.amount_for_scenario(scenario)

        # Decide whether to smooth this driver across days
        should_smooth = draft.kind in ("open_receivable", "open_payable")

        if should_smooth:
            # Triangular kernel: weights [1, 2, 3, 2, 1] / 9 over 5 days
            # centered on `center`. Splits the amount across days that fall
            # within the horizon.
            weights = [(center + timedelta(days=offset), w)
                       for offset, w in zip((-2, -1, 0, 1, 2), (1, 2, 3, 2, 1))]
            # Drop days outside horizon and renormalise
            in_horizon = [(d, w) for d, w in weights if today <= d <= horizon_end]
            if not in_horizon:
                continue
            total_weight = sum(w for _, w in in_horizon)
            for d, w in in_horizon:
                share = amt * Decimal(w) / Decimal(total_weight)
                cur_in, cur_out = buckets.get(d, (Decimal("0"), Decimal("0")))
                if draft.direction == "inflow":
                    buckets[d] = (cur_in + share, cur_out)
                else:
                    buckets[d] = (cur_in, cur_out + share)
        else:
            # Single-day driver (salary, rent, tax deadline)
            if not (today <= center <= horizon_end):
                continue
            cur_in, cur_out = buckets.get(center, (Decimal("0"), Decimal("0")))
            if draft.direction == "inflow":
                buckets[center] = (cur_in + amt, cur_out)
            else:
                buckets[center] = (cur_in, cur_out + amt)
    return buckets


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------


def generate_forecast(
    db: Session,
    *,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
    horizon_days: int = HORIZON_DAYS_DEFAULT,
    trigger: str = "manual",
    generated_by: Optional[uuid.UUID] = None,
) -> CashForecastRun:
    """Generate a fresh forecast run + points + drivers, persist, return."""

    today = date.today()
    horizon_end = today + timedelta(days=horizon_days)

    # 1. Starting cash position. Canonical-first; bank fallback.
    starting_cash = dashboard_kpis.get_cash_position(
        db, org_id=org_id, entity_id=entity_id, as_of=today
    )

    # 2. Discover drivers
    drafts: list[DriverDraft] = []
    drafts.extend(_collect_recurring_drivers(db, org_id, today, horizon_end))
    drafts.extend(_collect_invoice_drivers(db, org_id, today, horizon_end))
    drafts.extend(_collect_tax_calendar_drivers(org_id, today, horizon_end))

    # 3. Roll forward day-by-day for each scenario
    daily_pessimistic = _bucket_drivers_by_day(drafts, today, horizon_days, "pessimistic")
    daily_likely = _bucket_drivers_by_day(drafts, today, horizon_days, "likely")
    daily_optimistic = _bucket_drivers_by_day(drafts, today, horizon_days, "optimistic")

    # 4. Persist the run row first so we have run_id for children.
    run = CashForecastRun(
        org_id=org_id,
        entity_id=entity_id,
        as_of_date=today,
        horizon_days=horizon_days,
        starting_cash_inr=starting_cash,
        source_systems_json={"used_canonical_ledger": dashboard_kpis.has_canonical_data(
            db, org_id, categories=["cash", "bank"], entity_id=entity_id
        )},
        config_json={
            "horizon_days": horizon_days,
            "lookback_days": LOOKBACK_DAYS_HISTORY,
            "receivable_lateness_pessimistic": RECEIVABLE_LATENESS_PESSIMISTIC,
            "payable_stretch_optimistic": PAYABLE_STRETCH_OPTIMISTIC,
        },
        status="ok",
        trigger=trigger,
        generated_by=generated_by,
    )
    db.add(run)
    db.flush()

    # 5. Persist drivers
    inflows_total = Decimal("0")
    outflows_total = Decimal("0")
    for draft in drafts:
        if draft.direction == "inflow":
            inflows_total += draft.expected_amount_inr
        else:
            outflows_total += draft.expected_amount_inr
        db.add(
            ForecastDriver(
                run_id=run.id,
                org_id=org_id,
                kind=draft.kind,
                label=draft.label,
                vendor_id=draft.vendor_id,
                client_id=draft.client_id,
                expected_date=draft.expected_date,
                expected_amount_inr=draft.expected_amount_inr,
                direction=draft.direction,
                confidence=draft.confidence,
                source_kind=draft.source_kind,
                source_recurring_id=draft.source_recurring_id,
                source_invoice_id=draft.source_invoice_id,
                supporting_data=draft.supporting_data,
            )
        )

    # 6. Compute daily cumulative cash + persist points
    cash_p = starting_cash
    cash_l = starting_cash
    cash_o = starting_cash
    runway_zero_date: Optional[date] = None
    for offset in range(horizon_days + 1):
        d = today + timedelta(days=offset)
        in_p, out_p = daily_pessimistic.get(d, (Decimal("0"), Decimal("0")))
        in_l, out_l = daily_likely.get(d, (Decimal("0"), Decimal("0")))
        in_o, out_o = daily_optimistic.get(d, (Decimal("0"), Decimal("0")))
        cash_p = cash_p + in_p - out_p
        cash_l = cash_l + in_l - out_l
        cash_o = cash_o + in_o - out_o
        if runway_zero_date is None and cash_l < Decimal("0"):
            runway_zero_date = d
        db.add(
            CashForecastPoint(
                run_id=run.id,
                org_id=org_id,
                point_date=d,
                days_from_now=offset,
                cash_pessimistic_inr=cash_p,
                cash_likely_inr=cash_l,
                cash_optimistic_inr=cash_o,
                inflow_likely_inr=in_l,
                outflow_likely_inr=out_l,
            )
        )

    # 7. Update run summary
    run.drivers_count = len(drafts)
    run.inflows_total_inr = inflows_total
    run.outflows_total_inr = outflows_total
    run.ending_cash_likely_inr = cash_l
    run.ending_cash_pessimistic_inr = cash_p
    run.ending_cash_optimistic_inr = cash_o
    run.runway_zero_date = runway_zero_date

    db.commit()
    db.refresh(run)
    return run


def get_latest_forecast(
    db: Session,
    *,
    org_id: uuid.UUID,
    entity_id: Optional[uuid.UUID] = None,
) -> Optional[ForecastSummary]:
    """Return the most recent forecast for an org. None if never run."""
    q = select(CashForecastRun).where(
        CashForecastRun.org_id == org_id, CashForecastRun.status == "ok"
    )
    if entity_id is not None:
        q = q.where(CashForecastRun.entity_id == entity_id)
    run = db.execute(q.order_by(CashForecastRun.created_at.desc()).limit(1)).scalar_one_or_none()
    if run is None:
        return None

    points = list(
        db.execute(
            select(CashForecastPoint)
            .where(CashForecastPoint.run_id == run.id)
            .order_by(CashForecastPoint.point_date)
        ).scalars()
    )

    return ForecastSummary(
        run_id=run.id,
        as_of_date=run.as_of_date,
        horizon_days=run.horizon_days,
        starting_cash_inr=run.starting_cash_inr,
        ending_cash_likely_inr=run.ending_cash_likely_inr,
        ending_cash_pessimistic_inr=run.ending_cash_pessimistic_inr,
        ending_cash_optimistic_inr=run.ending_cash_optimistic_inr,
        runway_zero_date=run.runway_zero_date,
        drivers_count=run.drivers_count,
        inflows_total_inr=run.inflows_total_inr,
        outflows_total_inr=run.outflows_total_inr,
        created_at=run.created_at,
        points=[
            {
                "date": p.point_date.isoformat(),
                "days_from_now": p.days_from_now,
                "pessimistic": str(p.cash_pessimistic_inr),
                "likely": str(p.cash_likely_inr),
                "optimistic": str(p.cash_optimistic_inr),
                "inflow": str(p.inflow_likely_inr),
                "outflow": str(p.outflow_likely_inr),
                "actual": str(p.actual_cash_inr) if p.actual_cash_inr is not None else None,
            }
            for p in points
        ],
    )


def get_drivers(
    db: Session, run_id: uuid.UUID, org_id: uuid.UUID
) -> list[dict]:
    """Return drivers for a run, org-scoped (security)."""
    rows = list(
        db.execute(
            select(ForecastDriver).where(
                ForecastDriver.run_id == run_id, ForecastDriver.org_id == org_id
            ).order_by(ForecastDriver.expected_date)
        ).scalars()
    )
    return [
        {
            "id": str(d.id),
            "kind": d.kind,
            "label": d.label,
            "direction": d.direction,
            "expected_date": d.expected_date.isoformat() if d.expected_date else None,
            "expected_amount_inr": str(d.expected_amount_inr),
            "confidence": str(d.confidence),
            "source_kind": d.source_kind,
            "vendor_id": str(d.vendor_id) if d.vendor_id else None,
            "client_id": str(d.client_id) if d.client_id else None,
            "supporting_data": d.supporting_data,
        }
        for d in rows
    ]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _add_month(d: date) -> date:
    """Add one calendar month to a date, day-safe."""
    if d.month == 12:
        return date(d.year + 1, 1, d.day)
    try:
        return date(d.year, d.month + 1, d.day)
    except ValueError:
        # Day-of-month doesn't exist next month (e.g. 31-Jan → 28/29-Feb)
        if d.month == 1:
            # to Feb — use last day of Feb
            last = 29 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 28
        else:
            last = (date(d.year, d.month + 2, 1) - timedelta(days=1)).day
        return date(d.year, d.month + 1, last)
