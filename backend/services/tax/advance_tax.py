"""Advance tax estimator for the current financial year.

Under section 208 of the Income Tax Act, businesses and professionals with
estimated tax liability ≥ ₹10,000 must pay advance tax in four installments:

  Installment | Due date | Cumulative % of liability
  ------------+----------+--------------------------
  Q1          | Jun 15   | 15%
  Q2          | Sep 15   | 45%
  Q3          | Dec 15   | 75%
  Q4          | Mar 15   | 100%

This module:
  1. Estimates the org's FY net profit from YTD bank flows + invoice signal.
  2. Annualizes by extrapolating run-rate to end of FY.
  3. Applies corporate / individual / professional tax rates.
  4. Computes the 4 installments + flags overdue ones.

Rates are simplified — real-world tax calculation involves surcharge,
cess, MAT, presumptive schemes, depreciation, etc. We surface the estimate
as "rough number to discuss with your CA", not a filing-ready value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.models import BankTransaction, Invoice


EntityType = Literal["company", "individual", "professional", "llp"]


# Simplified slab tables. Conservative — uses headline rates.
# Source: Income Tax Act FY 2025-26 / AY 2026-27 (post-Budget 2024).
# Company: 25% (turnover ≤ ₹400 Cr) — most SMBs fit here.
# Individual: progressive (new regime 2024 slabs).
# LLP: 30% flat.
# Professional (44ADA presumptive ≥ 50% income): treated as Individual.

_INSTALLMENTS = [
    ("Q1", 6, 15, 0.15),   # 15% by Jun 15
    ("Q2", 9, 15, 0.45),   # cumulative 45% by Sep 15
    ("Q3", 12, 15, 0.75),  # 75% by Dec 15
    ("Q4", 3, 15, 1.00),   # 100% by Mar 15
]


@dataclass
class TaxInstallmentOut:
    """One quarterly advance-tax installment."""

    label: str                # Q1 / Q2 / Q3 / Q4
    due_date: date
    cumulative_pct: float     # 0.15, 0.45, 0.75, 1.00
    cumulative_amount: Decimal  # in ₹
    this_installment: Decimal   # delta over previous installment
    status: Literal["upcoming", "due_soon", "overdue", "complete"]
    days_until_due: int


@dataclass
class AdvanceTaxEstimateOut:
    """The full estimate payload."""

    fy_label: str              # e.g. "2025-26"
    fy_start: date
    fy_end: date
    days_elapsed: int          # days into the FY
    days_remaining: int

    revenue_ytd: Decimal       # credits classified as receipts/sales/income
    expense_ytd: Decimal       # debits classified as ops spend (excl. tax + investments)
    net_profit_ytd: Decimal
    projected_annual_profit: Decimal   # YTD run-rate × FY length

    entity_type: EntityType
    estimated_tax_rate: float   # effective rate applied
    estimated_annual_tax: Decimal

    installments: list[TaxInstallmentOut]
    next_due: Optional[TaxInstallmentOut]
    total_overdue: Decimal


def _fy_window(today: date) -> tuple[date, date, str]:
    """Indian financial year that contains `today`.

    Apr 1 → Mar 31. So 2025-04-01 .. 2026-03-31 is FY 2025-26."""
    if today.month >= 4:
        fy_start = date(today.year, 4, 1)
        fy_end = date(today.year + 1, 3, 31)
        label = f"{today.year}-{(today.year + 1) % 100:02d}"
    else:
        fy_start = date(today.year - 1, 4, 1)
        fy_end = date(today.year, 3, 31)
        label = f"{today.year - 1}-{today.year % 100:02d}"
    return fy_start, fy_end, label


def _slab_tax_company(income: Decimal) -> Decimal:
    """Domestic company w/ turnover ≤ ₹400 Cr: flat 25%.
    Plus 4% health & education cess. We fold the cess into the rate (26%)
    for simplicity since cess always applies."""
    if income <= 0:
        return Decimal("0")
    return income * Decimal("0.26")


def _slab_tax_llp(income: Decimal) -> Decimal:
    """LLP / partnership firm: flat 30% + 4% cess = 31.2%."""
    if income <= 0:
        return Decimal("0")
    return income * Decimal("0.312")


def _slab_tax_individual(income: Decimal) -> Decimal:
    """New regime slabs for FY 2025-26 (Section 115BAC).
    Source: Budget 2024."""
    if income <= 0:
        return Decimal("0")
    slabs = [
        (Decimal("300000"), Decimal("0.00")),
        (Decimal("700000"), Decimal("0.05")),
        (Decimal("1000000"), Decimal("0.10")),
        (Decimal("1200000"), Decimal("0.15")),
        (Decimal("1500000"), Decimal("0.20")),
        (Decimal("99999999999"), Decimal("0.30")),
    ]
    tax = Decimal("0")
    prev_limit = Decimal("0")
    remaining = income
    for limit, rate in slabs:
        slab_amt = min(remaining, limit - prev_limit)
        if slab_amt <= 0:
            break
        tax += slab_amt * rate
        remaining -= slab_amt
        prev_limit = limit
        if remaining <= 0:
            break
    # 4% health & education cess on tax.
    tax = tax * Decimal("1.04")
    return tax


def _apply_slabs(income: Decimal, entity_type: EntityType) -> tuple[Decimal, float]:
    """Returns (tax_amount, effective_rate)."""
    if entity_type == "company":
        tax = _slab_tax_company(income)
    elif entity_type == "llp":
        tax = _slab_tax_llp(income)
    else:  # individual / professional
        tax = _slab_tax_individual(income)
    rate = float(tax / income) if income > 0 else 0.0
    return tax, rate


def _installment_status(due: date, today: date) -> tuple[str, int]:
    """Return ('upcoming'|'due_soon'|'overdue', days_until_due)."""
    days = (due - today).days
    if days < 0:
        return "overdue", days
    if days <= 14:
        return "due_soon", days
    return "upcoming", days


def estimate_advance_tax(
    db: Session,
    *,
    org_id: uuid.UUID,
    today: date,
    entity_type: EntityType = "company",
) -> AdvanceTaxEstimateOut:
    """Compute the org's quarterly advance-tax installments for the FY that
    contains `today`. Pure read; no DB writes."""
    fy_start, fy_end, fy_label = _fy_window(today)
    days_elapsed = max(1, (today - fy_start).days + 1)
    fy_length = (fy_end - fy_start).days + 1
    days_remaining = max(0, fy_length - days_elapsed)

    # ---- Revenue YTD: credits to bank + sales-invoice totals ----
    cred_ytd = db.scalar(
        select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "credit",
            BankTransaction.txn_date >= fy_start,
            BankTransaction.txn_date <= today,
        )
    ) or 0
    sales_inv_ytd = db.scalar(
        select(func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.org_id == org_id,
            Invoice.type == "sales",
            Invoice.issue_date >= fy_start,
            Invoice.issue_date <= today,
        )
    ) or 0
    # Take the larger signal — bank credits include redemptions / transfers
    # that aren't revenue, while sales invoices are pure revenue but only
    # those uploaded.
    revenue_ytd = Decimal(max(float(cred_ytd) * 0.7, float(sales_inv_ytd)))

    # ---- Expense YTD: debits minus investment / transfer flows ----
    deb_ytd = db.scalar(
        select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= fy_start,
            BankTransaction.txn_date <= today,
        )
    ) or 0
    # Crude haircut: ~30% of debits in our data are person-to-person
    # transfers / investments which aren't operating expense. Take 70%.
    expense_ytd = Decimal(float(deb_ytd) * 0.70)

    net_profit_ytd = revenue_ytd - expense_ytd

    # ---- Project annual ----
    run_rate_per_day = net_profit_ytd / Decimal(days_elapsed)
    projected_annual = run_rate_per_day * Decimal(fy_length)
    if projected_annual < 0:
        projected_annual = Decimal("0")

    # ---- Tax liability ----
    annual_tax, eff_rate = _apply_slabs(projected_annual, entity_type)

    # ---- Quarterly installments ----
    installments: list[TaxInstallmentOut] = []
    prev_cum = Decimal("0")
    for label, m, d, pct in _INSTALLMENTS:
        # Each installment due date is in the FY's calendar year.
        # Q4 due date is Mar 15 of the *next* calendar year.
        year = fy_end.year if m <= 3 else fy_start.year
        due = date(year, m, d)
        cum_amount = (annual_tax * Decimal(pct)).quantize(Decimal("0.01"))
        this_installment = cum_amount - prev_cum
        prev_cum = cum_amount
        status_str, days_until = _installment_status(due, today)
        installments.append(
            TaxInstallmentOut(
                label=label,
                due_date=due,
                cumulative_pct=pct,
                cumulative_amount=cum_amount,
                this_installment=this_installment,
                status=status_str,  # type: ignore[arg-type]
                days_until_due=days_until,
            )
        )

    # Pick the next upcoming installment (smallest non-overdue days).
    upcoming = [i for i in installments if i.status != "overdue"]
    next_due = upcoming[0] if upcoming else None

    total_overdue = sum(
        (i.this_installment for i in installments if i.status == "overdue"),
        start=Decimal("0"),
    )

    return AdvanceTaxEstimateOut(
        fy_label=fy_label,
        fy_start=fy_start,
        fy_end=fy_end,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        revenue_ytd=revenue_ytd,
        expense_ytd=expense_ytd,
        net_profit_ytd=net_profit_ytd,
        projected_annual_profit=projected_annual,
        entity_type=entity_type,
        estimated_tax_rate=eff_rate,
        estimated_annual_tax=annual_tax,
        installments=installments,
        next_due=next_due,
        total_overdue=total_overdue,
    )
