"""Recurring transaction detector.

After every bank-statement ingest we sweep the last 6 months of debits, group
them by (vendor or description prefix), and look for groups where:

  - the group has ≥ 3 observations,
  - the gaps between observations cluster around 28-31 days (monthly cadence),
  - the amounts cluster around a median with low variance (CV ≤ 0.2).

Groups that pass become `RecurringPattern` rows.

Once we have patterns:

  - Each matching new txn is marked `is_recurring=True` so the anomaly
    detector can ignore them (recurring spend is by definition normal).
  - If a pattern's `last_seen_on` is more than 5 days past its expected
    next occurrence, we emit a `recurring_payment_missed` Insight.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.models import BankTransaction, Insight, RecurringPattern, Vendor

logger = logging.getLogger(__name__)

# Tunables — exposed module-level so tests can monkey-patch.
MIN_OBSERVATIONS = 3                  # need 3+ same-amount payments to call it recurring
LOOKBACK_DAYS = 180                   # 6 months of history
MAX_CV = 0.20                         # coefficient of variation must be ≤ 20%
MIN_AMOUNT = Decimal("500")           # ignore noise
GAP_MEAN_LOW = 25                     # accept monthly cadence 25-33 days
GAP_MEAN_HIGH = 33
LATE_BY_DAYS = 5                      # flag as missed if 5+ days past expected


@dataclass(slots=True)
class DetectedPattern:
    """In-memory representation of a recurring pattern detected from history."""

    label: str
    vendor_id: Optional[uuid.UUID]
    cadence: str
    expected_day_of_month: Optional[int]
    median_amount: Decimal
    observations: list[tuple[date, Decimal]]  # (date, amount) sorted asc

    @property
    def first_seen(self) -> date:
        return self.observations[0][0]

    @property
    def last_seen(self) -> date:
        return self.observations[-1][0]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_patterns(
    db: Session, *, org_id: uuid.UUID, as_of: Optional[date] = None
) -> list[DetectedPattern]:
    """Scan the last 6 months of debit txns for this org and return detected
    recurring patterns. Does NOT write to the database."""
    if as_of is None:
        as_of = date.today()
    cutoff = as_of - timedelta(days=LOOKBACK_DAYS)

    rows = db.execute(
        select(
            BankTransaction.matched_vendor_id,
            BankTransaction.description,
            BankTransaction.txn_date,
            BankTransaction.amount,
        )
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= cutoff,
            BankTransaction.amount >= MIN_AMOUNT,
        )
        .order_by(BankTransaction.txn_date.asc())
    ).all()

    # Group by (vendor_id, prefix) — vendor wins, prefix is the fallback.
    groups: dict[tuple[Optional[uuid.UUID], str], list[tuple[date, Decimal]]] = {}
    for vendor_id, desc, txn_date, amount in rows:
        if vendor_id is not None:
            key = (vendor_id, "")  # vendor-keyed
        else:
            prefix = _description_prefix(desc)
            if not prefix:
                continue
            key = (None, prefix)
        groups.setdefault(key, []).append((txn_date, amount))

    detected: list[DetectedPattern] = []
    for (vendor_id, prefix), obs in groups.items():
        if len(obs) < MIN_OBSERVATIONS:
            continue

        # Sorted by date ascending (rows query already orders by date).
        amounts = [float(a) for _, a in obs]
        median = statistics.median(amounts)
        if median <= 0:
            continue
        stdev = statistics.stdev(amounts) if len(amounts) >= 2 else 0.0
        cv = stdev / median if median > 0 else float("inf")
        if cv > MAX_CV:
            continue

        # Check cadence: gaps in days between consecutive observations.
        gaps = [(obs[i][0] - obs[i - 1][0]).days for i in range(1, len(obs))]
        if not gaps:
            continue
        gap_mean = statistics.mean(gaps)
        if not (GAP_MEAN_LOW <= gap_mean <= GAP_MEAN_HIGH):
            continue  # not monthly cadence

        # Day-of-month: use the mode of observation days.
        try:
            expected_day = statistics.mode([d.day for d, _ in obs])
        except statistics.StatisticsError:
            expected_day = obs[-1][0].day  # tie — use most recent

        label = _label_for_group(db, vendor_id, prefix)
        detected.append(
            DetectedPattern(
                label=label,
                vendor_id=vendor_id,
                cadence="monthly",
                expected_day_of_month=expected_day,
                median_amount=Decimal(f"{median:.2f}"),
                observations=obs,
            )
        )

    return detected


def upsert_patterns(
    db: Session, *, org_id: uuid.UUID, as_of: Optional[date] = None
) -> list[RecurringPattern]:
    """Detect patterns and upsert RecurringPattern rows.

    Called by the worker after each bank-statement ingest. Idempotent.
    Returns the list of upserted rows (whether new or updated)."""
    detected = detect_patterns(db, org_id=org_id, as_of=as_of)
    upserted: list[RecurringPattern] = []

    for pat in detected:
        existing = _find_existing(db, org_id=org_id, label=pat.label, vendor_id=pat.vendor_id)
        if existing is None:
            row = RecurringPattern(
                org_id=org_id,
                vendor_id=pat.vendor_id,
                label=pat.label,
                cadence=pat.cadence,
                expected_day_of_month=pat.expected_day_of_month,
                median_amount=pat.median_amount,
                observed_count=len(pat.observations),
                first_seen_on=pat.first_seen,
                last_seen_on=pat.last_seen,
            )
            db.add(row)
        else:
            existing.median_amount = pat.median_amount
            existing.expected_day_of_month = pat.expected_day_of_month
            existing.observed_count = len(pat.observations)
            existing.last_seen_on = pat.last_seen
            row = existing
        upserted.append(row)
    db.flush()
    return upserted


def tag_recurring_transactions(
    db: Session, *, org_id: uuid.UUID, txns: list[BankTransaction]
) -> int:
    """Mark the given txns as recurring if they match any known pattern.

    Match rule: same vendor (or prefix) AND amount within tolerance of the
    pattern's median. Returns count of txns tagged."""
    patterns = list(db.scalars(
        select(RecurringPattern).where(RecurringPattern.org_id == org_id)
    ).all())
    if not patterns:
        return 0

    tagged = 0
    for txn in txns:
        if txn.direction != "debit":
            continue
        for pat in patterns:
            if _txn_matches_pattern(txn, pat):
                if not txn.is_recurring:
                    txn.is_recurring = True
                    if txn.auto_tagged_by is None:
                        txn.auto_tagged_by = "recurring"
                    tagged += 1
                break
    return tagged


# ---------------------------------------------------------------------------
# Missed-payment insights
# ---------------------------------------------------------------------------


def emit_missed_payment_insights(
    db: Session, *, org_id: uuid.UUID, as_of: Optional[date] = None
) -> int:
    """For each recurring pattern, check whether it's overdue. Emit an
    Insight if `last_seen_on` is more than LATE_BY_DAYS past the expected
    next date. Returns count of insights emitted."""
    if as_of is None:
        as_of = date.today()
    patterns = list(db.scalars(
        select(RecurringPattern).where(RecurringPattern.org_id == org_id)
    ).all())

    emitted = 0
    for pat in patterns:
        expected_next = _expected_next_date(pat, as_of=as_of)
        if expected_next is None:
            continue
        days_late = (as_of - expected_next).days
        if days_late < LATE_BY_DAYS:
            continue

        # Dedupe — already flagged for this pattern + cycle?
        existing = db.execute(
            select(Insight).where(
                Insight.org_id == org_id,
                Insight.type == "recurring_payment_missed",
            )
        ).scalars()
        already = False
        for ex in existing:
            sd = ex.supporting_data or {}
            if sd.get("pattern_id") == str(pat.id) and sd.get(
                "expected_on"
            ) == expected_next.isoformat():
                already = True
                break
        if already:
            continue

        amount_str = _format_inr_short(pat.median_amount)
        title = f"Missed payment: {pat.label}"
        # Plain-English: avoid jargon, suggest a next step.
        if days_late < 10:
            urgency = f"a few days behind ({days_late} days, to be exact)"
        elif days_late < 35:
            urgency = f"about {days_late} days overdue now"
        else:
            urgency = f"more than a month overdue ({days_late} days)"
        body = (
            f"Your {amount_str} payment to {pat.label} normally goes out around "
            f"the {_ordinal(pat.expected_day_of_month or 1)} of every month, "
            f"but this cycle's payment hasn't shown up yet. It's {urgency}. "
            f"Either the payment was paused, the bank statement isn't uploaded yet, "
            f"or this needs your attention."
        )
        db.add(
            Insight(
                org_id=org_id,
                type="recurring_payment_missed",
                severity="attention" if days_late < 15 else "urgent",
                title=title,
                body=body,
                supporting_data={
                    "pattern_id": str(pat.id),
                    "vendor_id": str(pat.vendor_id) if pat.vendor_id else None,
                    "label": pat.label,
                    "expected_on": expected_next.isoformat(),
                    "days_late": days_late,
                    "median_amount": str(pat.median_amount),
                    "technical": (
                        f"expected day {pat.expected_day_of_month}, "
                        f"last seen {pat.last_seen_on.isoformat()}, "
                        f"observed {pat.observed_count} times"
                    ),
                },
            )
        )
        emitted += 1
    db.flush()
    return emitted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _description_prefix(description: str) -> str:
    """Best-effort label from a bank-txn description.

    Strips UPI/NEFT/IMPS prefixes and a leading transfer token, keeps the
    first 'meaningful' segment. e.g. 'NEFT/TATA POWER/...' → 'TATA POWER'.
    """
    if not description:
        return ""
    s = description.strip().upper()
    # Common transaction-channel prefixes to drop.
    for prefix in ("NEFT/", "RTGS/", "IMPS/", "UPI/", "TRFR TO:", "TRFR FROM:", "POS "):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    # Take the first segment up to "/" or "-".
    for sep in ("/", "-", " - "):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    return s[:80]


def _label_for_group(
    db: Session, vendor_id: Optional[uuid.UUID], prefix: str
) -> str:
    if vendor_id is not None:
        vendor = db.get(Vendor, vendor_id)
        if vendor is not None:
            return vendor.name
    return prefix or "Unknown recurring"


def _find_existing(
    db: Session,
    *,
    org_id: uuid.UUID,
    label: str,
    vendor_id: Optional[uuid.UUID],
) -> Optional[RecurringPattern]:
    stmt = select(RecurringPattern).where(
        RecurringPattern.org_id == org_id, RecurringPattern.label == label
    )
    if vendor_id is not None:
        stmt = stmt.where(RecurringPattern.vendor_id == vendor_id)
    return db.execute(stmt).scalar_one_or_none()


def _txn_matches_pattern(txn: BankTransaction, pat: RecurringPattern) -> bool:
    # Amount band
    median = float(pat.median_amount)
    tol = float(pat.amount_tolerance_pct or Decimal("0.10"))
    if median <= 0:
        return False
    if not (median * (1 - tol) <= float(txn.amount) <= median * (1 + tol)):
        return False
    # Vendor match takes precedence
    if pat.vendor_id is not None:
        return txn.matched_vendor_id == pat.vendor_id
    # Otherwise description prefix
    return _description_prefix(txn.description) == pat.label.upper()


def _expected_next_date(pat: RecurringPattern, *, as_of: date) -> Optional[date]:
    if pat.cadence != "monthly" or pat.expected_day_of_month is None:
        return None
    # First, the expected occurrence in the SAME month as last_seen_on, then
    # roll forward month-by-month until we land at-or-after last_seen_on.
    target_day = max(1, min(28, pat.expected_day_of_month))  # cap at 28 to avoid Feb issues
    year, month = pat.last_seen_on.year, pat.last_seen_on.month + 1
    if month > 12:
        year, month = year + 1, 1
    try:
        return date(year, month, target_day)
    except ValueError:
        return date(year, month, 28)


def rehumanize_missed_payment_insights(
    db: Session, *, org_id: uuid.UUID, as_of: Optional[date] = None
) -> int:
    """Walk every existing `recurring_payment_missed` insight for the org
    and rewrite its `body` text in the latest conversational format.

    Idempotent. Used by /api/learning/retrain so old jargon-y bodies get
    replaced in one click."""
    if as_of is None:
        as_of = date.today()
    rows = list(db.scalars(
        select(Insight).where(
            Insight.org_id == org_id,
            Insight.type == "recurring_payment_missed",
        )
    ).all())
    rewritten = 0
    for ins in rows:
        sd = ins.supporting_data or {}
        try:
            label = str(sd.get("label") or "this vendor")
            median = Decimal(str(sd.get("median_amount", "0")))
            expected_iso = sd.get("expected_on")
            if not expected_iso:
                continue
            y, m, d = (int(p) for p in str(expected_iso).split("-")[:3])
            expected_on = date(y, m, d)
        except (TypeError, ValueError, KeyError):
            continue
        days_late = (as_of - expected_on).days
        if days_late < 0:
            continue
        expected_day = expected_on.day
        amount_str = _format_inr_short(median)
        if days_late < 10:
            urgency = f"a few days behind ({days_late} days, to be exact)"
        elif days_late < 35:
            urgency = f"about {days_late} days overdue now"
        else:
            urgency = f"more than a month overdue ({days_late} days)"
        new_body = (
            f"Your {amount_str} payment to {label} normally goes out around "
            f"the {_ordinal(expected_day)} of every month, but this cycle's "
            f"payment hasn't shown up yet. It's {urgency}. Either the payment "
            f"was paused, the bank statement isn't uploaded yet, or this "
            f"needs your attention."
        )
        if new_body != ins.body:
            ins.body = new_body
            rewritten += 1
    db.flush()
    return rewritten


def _ordinal(n: int) -> str:
    """1 → '1st', 2 → '2nd', 3 → '3rd', 11 → '11th', etc."""
    if 10 <= (n % 100) <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_inr_short(amount: Decimal) -> str:
    a = float(amount)
    if a >= 1e7:
        return f"₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"₹{a / 1e5:.1f} L"
    return f"₹{a:,.0f}"
