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

    verdict = evaluate_amount(amount, history)
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

    insight = Insight(
        org_id=org_id,
        type="vendor_amount_anomaly",
        severity=severity,
        title=f"Unusual payment to {vendor.name}",
        body=(
            f"₹{amount:,.2f} on {observed_on.isoformat()} is "
            f"{pct_above:+.0f}% vs the typical ₹{mu:,.2f} "
            f"(n={n}, σ=₹{sigma:,.2f}, z={z:.1f})."
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
