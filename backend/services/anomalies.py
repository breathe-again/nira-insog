"""Per-vendor anomaly detection → Insight rows.

The rule, for v0:

    For each transaction/receipt amount A against vendor V:
      look at V's prior history H (debits only, last 12 months).
      If |H| >= MIN_HISTORY and A > mean(H) + Z_THRESHOLD * stddev(H):
        emit an Insight describing the spike.

The thresholds are conservative on purpose — we'd rather miss the 11th
"slightly high" payment than spam the inbox.

We deduplicate insights by (type, supporting_data.txn_id|receipt_id) so
re-running the pipeline doesn't double-fire.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, stdev
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import BankTransaction, Insight, Receipt, Vendor, VendorMute

logger = logging.getLogger(__name__)


# Tunables — exposed at module level so tests can monkey-patch them cleanly.
MIN_HISTORY = 5           # need at least 5 prior payments to a vendor
Z_THRESHOLD = 2.0          # >2σ from mean ⇒ flag
LOOKBACK_DAYS = 365        # only consider history within the last year
ABSOLUTE_FLOOR = Decimal("500")  # ignore noise below ₹500
SEVERITY_URGENT_Z = 4.0    # >4σ ⇒ urgent, else attention


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StatVerdict:
    """Pure-math output of the threshold rule. No I/O needed to compute this."""

    flagged: bool
    mean: float
    stddev: float
    z_score: float
    sample_size: int
    severity: Optional[str] = None  # set only when flagged


def evaluate_amount(
    amount: Decimal,
    history: list[Decimal],
    *,
    min_history: int = MIN_HISTORY,
    z_threshold: float = Z_THRESHOLD,
    severity_urgent_z: float = SEVERITY_URGENT_Z,
    absolute_floor: Decimal = ABSOLUTE_FLOOR,
) -> StatVerdict:
    """Decide whether `amount` is anomalous given prior `history`.

    Pure function — does not touch the database. Exposed so it can be unit
    tested in isolation and reused outside the worker.
    """
    n = len(history)
    if n < min_history or amount <= absolute_floor:
        return StatVerdict(False, mean=0.0, stddev=0.0, z_score=0.0, sample_size=n)

    h_floats = [float(x) for x in history]
    mu = mean(h_floats)
    sigma = stdev(h_floats) if n >= 2 else 0.0

    if sigma == 0.0:
        # No variance in history — only flag if amount differs by >50%.
        if mu == 0.0 or abs(float(amount) - mu) / max(mu, 1.0) < 0.5:
            return StatVerdict(False, mean=mu, stddev=sigma, z_score=0.0, sample_size=n)
        z = 99.0
    else:
        z = (float(amount) - mu) / sigma

    if z <= z_threshold:
        return StatVerdict(False, mean=mu, stddev=sigma, z_score=z, sample_size=n)

    severity = "urgent" if z >= severity_urgent_z else "attention"
    return StatVerdict(
        True,
        mean=mu,
        stddev=sigma,
        z_score=z,
        sample_size=n,
        severity=severity,
    )


@dataclass(slots=True)
class AnomalyResult:
    """One detected anomaly — what the Insight row was built from."""

    vendor_id: uuid.UUID
    vendor_name: str
    amount: Decimal
    mean: Decimal
    stddev: Decimal
    z_score: float
    sample_size: int
    severity: str
    insight_id: Optional[uuid.UUID] = None  # set after persistence


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def check_bank_transaction(
    db: Session,
    org_id: uuid.UUID,
    txn: BankTransaction,
) -> Optional[AnomalyResult]:
    """Run the anomaly check for a single bank transaction. Returns the result
    if an insight was created, else None.
    """
    if txn.direction != "debit":
        return None  # only flag outflows
    if txn.matched_vendor_id is None:
        return None
    if txn.amount <= ABSOLUTE_FLOOR:
        return None

    vendor = db.get(Vendor, txn.matched_vendor_id)
    if vendor is None:
        return None

    history = _history_for_vendor(
        db,
        org_id=org_id,
        vendor_id=vendor.id,
        before=txn.txn_date,
        exclude_txn_id=txn.id,
    )

    return _maybe_emit(
        db,
        org_id=org_id,
        vendor=vendor,
        amount=txn.amount,
        history=history,
        observed_on=txn.txn_date,
        source_entity="bank_transaction",
        source_id=txn.id,
    )


def check_receipt(
    db: Session,
    org_id: uuid.UUID,
    receipt: Receipt,
) -> Optional[AnomalyResult]:
    """Run the anomaly check for a standalone receipt."""
    if receipt.vendor_id is None:
        return None
    if receipt.amount <= ABSOLUTE_FLOOR:
        return None

    vendor = db.get(Vendor, receipt.vendor_id)
    if vendor is None:
        return None

    history = _history_for_vendor(
        db,
        org_id=org_id,
        vendor_id=vendor.id,
        before=receipt.date,
        exclude_receipt_id=receipt.id,
    )

    return _maybe_emit(
        db,
        org_id=org_id,
        vendor=vendor,
        amount=receipt.amount,
        history=history,
        observed_on=receipt.date,
        source_entity="receipt",
        source_id=receipt.id,
    )


def check_many_transactions(
    db: Session,
    org_id: uuid.UUID,
    txns: list[BankTransaction],
) -> list[AnomalyResult]:
    """Convenience: run check_bank_transaction over a batch."""
    out: list[AnomalyResult] = []
    for txn in txns:
        result = check_bank_transaction(db, org_id, txn)
        if result is not None:
            out.append(result)
    return out


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _history_for_vendor(
    db: Session,
    *,
    org_id: uuid.UUID,
    vendor_id: uuid.UUID,
    before: date,
    exclude_txn_id: Optional[uuid.UUID] = None,
    exclude_receipt_id: Optional[uuid.UUID] = None,
) -> list[Decimal]:
    """Return prior amounts (debits + receipts) for a vendor in the lookback window."""
    cutoff = before - timedelta(days=LOOKBACK_DAYS)

    txn_stmt = select(BankTransaction.amount).where(
        BankTransaction.org_id == org_id,
        BankTransaction.matched_vendor_id == vendor_id,
        BankTransaction.direction == "debit",
        BankTransaction.txn_date >= cutoff,
        BankTransaction.txn_date < before,
    )
    if exclude_txn_id is not None:
        txn_stmt = txn_stmt.where(BankTransaction.id != exclude_txn_id)

    receipt_stmt = select(Receipt.amount).where(
        Receipt.org_id == org_id,
        Receipt.vendor_id == vendor_id,
        Receipt.date >= cutoff,
        Receipt.date < before,
    )
    if exclude_receipt_id is not None:
        receipt_stmt = receipt_stmt.where(Receipt.id != exclude_receipt_id)

    amounts = list(db.scalars(txn_stmt).all()) + list(db.scalars(receipt_stmt).all())
    return [Decimal(a) for a in amounts]


def _maybe_emit(
    db: Session,
    *,
    org_id: uuid.UUID,
    vendor: Vendor,
    amount: Decimal,
    history: list[Decimal],
    observed_on: date,
    source_entity: str,
    source_id: uuid.UUID,
) -> Optional[AnomalyResult]:
    """Compute stats, decide whether to flag, and (if so) persist an Insight."""
    # Respect user mutes — once a founder says "this vendor's spikes are
    # normal", we stop flagging them.
    from datetime import datetime, timezone  # local import to keep top thin

    mute = db.execute(
        select(VendorMute).where(
            VendorMute.org_id == org_id,
            VendorMute.vendor_id == vendor.id,
            VendorMute.rule == "anomaly",
        )
    ).scalar_one_or_none()
    if mute is not None:
        if mute.expires_at is None or mute.expires_at > datetime.now(timezone.utc):
            return None

    # Tier-1 learning: tenant-adaptive z threshold.
    # Businesses with naturally lumpy cash flow get a higher bar before we
    # cry wolf. We measure that via the coefficient of variation across the
    # whole org's debit history.
    z_thresh = _adaptive_z_threshold(db, org_id=org_id)

    verdict = evaluate_amount(amount, history, z_threshold=z_thresh)
    if not verdict.flagged or verdict.severity is None:
        return None
    mu = verdict.mean
    sigma = verdict.stddev
    z = verdict.z_score
    n = verdict.sample_size
    severity = verdict.severity

    # Dedupe: do we already have an insight for this exact source row?
    existing = db.execute(
        select(Insight)
        .where(Insight.org_id == org_id)
        .where(Insight.type == "vendor_amount_anomaly")
    ).scalars()
    for ex in existing:
        sd = ex.supporting_data or {}
        if sd.get("source_entity") == source_entity and sd.get("source_id") == str(source_id):
            return None  # already flagged

    pct_above = ((float(amount) - mu) / mu * 100.0) if mu > 0 else 0.0
    multiple = (float(amount) / mu) if mu > 0 else 0.0

    insight = Insight(
        org_id=org_id,
        type="vendor_amount_anomaly",
        severity=severity,
        title=f"Unusual payment to {vendor.name}",
        body=_humanize_anomaly_body(
            amount=amount,
            mu=mu,
            multiple=multiple,
            pct_above=pct_above,
            vendor_name=vendor.name,
            observed_on=observed_on,
        ),
        supporting_data={
            "source_entity": source_entity,
            "source_id": str(source_id),
            "vendor_id": str(vendor.id),
            "vendor_name": vendor.name,
            "amount": str(amount),
            "mean": f"{mu:.2f}",
            "stddev": f"{sigma:.2f}",
            "z_score": round(z, 2),
            "sample_size": n,
            "observed_on": observed_on.isoformat(),
            # Stash the technical version too for the "Why this insight?"
            # detail pane (Phase C). Frontend can show on hover/click.
            "technical": (
                f"+{pct_above:.0f}% vs typical (n={n}, σ=₹{sigma:,.2f}, z={z:.1f})"
            ),
        },
    )
    db.add(insight)
    db.flush()

    return AnomalyResult(
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        amount=amount,
        mean=Decimal(f"{mu:.2f}"),
        stddev=Decimal(f"{sigma:.2f}"),
        z_score=z,
        sample_size=n,
        severity=severity,
        insight_id=insight.id,
    )


# ---------------------------------------------------------------------------
# Human-facing formatting
# ---------------------------------------------------------------------------


def _format_inr(amount: float) -> str:
    """Format an amount in Indian-style: ₹1.05 Cr, ₹46.0 L, ₹85,000, ₹450.

    Crore  >= 1,00,00,000  (1e7)
    Lakh   >= 1,00,000     (1e5)
    Below 1 Lakh: comma-grouped rupees with no paise.
    """
    a = abs(float(amount))
    sign = "-" if amount < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.1f} L"
    # Indian comma grouping for < 1 Lakh: e.g. 85,000  not  85,000
    # (The Indian system would write 12,34,567 but at < 1L the western form
    # is identical, so plain {:,.0f} suffices.)
    return f"{sign}₹{a:,.0f}"


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _format_date_human(d: date) -> str:
    """Format a date as 'Apr 22, 2025' — short, unambiguous, no ISO ugliness."""
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _adaptive_z_threshold(db: Session, *, org_id: uuid.UUID) -> float:
    """Compute a tenant-adaptive z-threshold.

    The rationale: businesses with naturally lumpy cash flow (e.g. consulting
    firms that get a single big invoice per quarter) have high coefficient of
    variation across their debits. Using a fixed z>2 floods them with false
    positives. We bump the threshold for lumpy tenants and lower it slightly
    for tenants with very regular cadence.

    Returns a float between 1.8 and 4.0.
    """
    # 12-month sample of all debits.
    cutoff = date.today() - timedelta(days=365)
    amounts = list(db.scalars(
        select(BankTransaction.amount).where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= cutoff,
        )
    ).all())

    if len(amounts) < 30:
        return Z_THRESHOLD  # not enough data — use the global default

    floats = [float(a) for a in amounts]
    mu = mean(floats)
    sigma = stdev(floats) if len(floats) >= 2 else 0.0
    if mu <= 0:
        return Z_THRESHOLD
    cv = sigma / mu  # coefficient of variation

    # Map CV [0, ∞) → threshold [1.8, 4.0]:
    #   cv ≤ 0.5 (regular cadence)  → 1.8 (tighter — catch small anomalies)
    #   cv ≈ 1.0 (typical SMB)      → 2.0 (the default)
    #   cv ≈ 2.0 (lumpy business)   → 3.0
    #   cv ≥ 3.0 (very lumpy)       → 4.0 (almost nothing fires)
    if cv <= 0.5:
        return 1.8
    if cv <= 1.0:
        return 2.0
    if cv <= 2.0:
        return 2.0 + (cv - 1.0)  # linearly 2.0 → 3.0
    return min(4.0, 3.0 + (cv - 2.0) * 0.5)


def _humanize_anomaly_body(
    *,
    amount: Decimal,
    mu: float,
    multiple: float,
    pct_above: float,
    vendor_name: str,
    observed_on: date,
) -> str:
    """Build a plain-English description of an anomaly insight.

    Examples produced:
      - "₹1.00 Cr to SGB on Apr 22, 2025 — about 5× your usual payment
         (typically around ₹21.0 L)."
      - "₹85,000 to Cafe Coffee Day on May 12, 2026 — roughly 3× your usual
         spend at this vendor (typically around ₹28,000)."
    """
    amount_str = _format_inr(float(amount))
    typical_str = _format_inr(mu)
    date_str = _format_date_human(observed_on)

    # Choose a human comparison: prefer multiples for big spikes (>= 2×),
    # else use the percentage.
    if multiple >= 10:
        comparison = f"about {multiple:.0f}× your usual payment"
    elif multiple >= 2:
        comparison = f"about {multiple:.1f}× your usual payment"
    else:
        comparison = f"about {pct_above:.0f}% above your usual payment"

    return (
        f"{amount_str} to {vendor_name} on {date_str} — "
        f"{comparison} (typically around {typical_str})."
    )
