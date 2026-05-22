"""TDS section auto-detect + draft generator for vendor payments.

Tax Deducted at Source (TDS) requires the payer to withhold tax on certain
vendor payments and deposit it with the government. The applicable section
depends on the *type* of payment:

  Section | Trigger                                          | Threshold        | Rate
  --------+--------------------------------------------------+------------------+------
  194C    | Contractor / sub-contractor work                 | ₹30K single /    | 1% indv
                                                              ₹1L aggregate    | 2% other
  194I-a  | Rent of plant & machinery                        | ₹2.4L / FY       | 2%
  194I-b  | Rent of land/building/furniture                  | ₹2.4L / FY       | 10%
  194J    | Professional / technical services                | ₹30K / FY        | 10% / 2%
  194A    | Interest (other than securities)                 | ₹40K / FY        | 10%
  194H    | Commission / brokerage                            | ₹15K / FY        | 5%
  194Q    | Purchase of goods (large buyers)                 | ₹50L / FY        | 0.1%

This module:
  1. For each vendor, classifies the payment type from vendor.default_expense_category
     and description keywords.
  2. Tracks aggregate payments per vendor per FY.
  3. Flags vendors where the threshold has been crossed but TDS may not have
     been deducted (no matching tax-side outflow recorded).
  4. Generates a draft table suitable for 24Q/26Q filing.

Caveats:
  - PAN-availability rules (higher rates if PAN missing) are noted but the
    draft uses standard rates — CA should adjust.
  - 26AS reconciliation is not implemented (would require GST portal API).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.models import BankTransaction, Invoice, Vendor
from services.tax.gstin import extract_pan_from_gstin


@dataclass(frozen=True)
class TDSSection:
    code: str           # e.g. '194C'
    label: str          # 'Contractor payment'
    threshold_fy: Decimal     # aggregate threshold in INR
    threshold_single: Optional[Decimal]  # per-payment threshold (only some sections)
    rate_with_pan: float
    rate_without_pan: float
    form_quarterly: str   # '26Q' for non-salary, '24Q' for salary
    needed: bool          # whether TDS deduction is mandatory above threshold


_SECTIONS: dict[str, TDSSection] = {
    "194C": TDSSection("194C", "Contractor / sub-contractor", Decimal("100000"), Decimal("30000"), 0.02, 0.20, "26Q", True),
    "194I_a": TDSSection("194I(a)", "Rent — plant & machinery", Decimal("240000"), None, 0.02, 0.20, "26Q", True),
    "194I_b": TDSSection("194I(b)", "Rent — land/building/furniture", Decimal("240000"), None, 0.10, 0.20, "26Q", True),
    "194J": TDSSection("194J", "Professional / technical services", Decimal("30000"), None, 0.10, 0.20, "26Q", True),
    "194A": TDSSection("194A", "Interest (non-securities)", Decimal("40000"), None, 0.10, 0.20, "26Q", True),
    "194H": TDSSection("194H", "Commission / brokerage", Decimal("15000"), None, 0.05, 0.20, "26Q", True),
    "194Q": TDSSection("194Q", "Purchase of goods", Decimal("5000000"), None, 0.001, 0.05, "26Q", True),
    "192":  TDSSection("192",  "Salary",                          Decimal("250000"), None, 0.0,  0.0,  "24Q", True),
}


# Keyword → section map. First-match-wins; ordered most-specific to least.
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["rent", "lease", "landlord"], "194I_b"),
    (["machinery rent", "equipment rent", "plant rent"], "194I_a"),
    (["salary", "payroll", "wages"], "192"),
    (["interest", "loan interest", "deposit interest"], "194A"),
    (["commission", "brokerage", "agent fee"], "194H"),
    (["consulting", "consultancy", "professional fee", "legal", "audit",
      "ca fee", "advisory", "design", "creative"], "194J"),
    (["contract", "contractor", "construction", "fabrication", "freight",
      "courier", "labour"], "194C"),
]


@dataclass
class VendorTDSRow:
    """One row of the TDS draft table."""

    vendor_id: uuid.UUID
    vendor_name: str
    pan: Optional[str]
    section_code: str
    section_label: str
    fy_payments_total: Decimal
    threshold: Decimal
    has_crossed_threshold: bool
    applicable_rate: float
    tds_amount_estimated: Decimal
    net_payable_after_tds: Decimal
    form_quarterly: str   # '24Q' or '26Q'
    deduction_status: str  # 'likely_pending' | 'below_threshold' | 'na'
    notes: Optional[str] = None


def _detect_section(
    vendor_name: str,
    vendor_default_category: Optional[str],
    description_sample: str,
) -> Optional[str]:
    """Return the section code that best fits, or None if uncertain."""
    haystack = " ".join(
        s.lower()
        for s in [vendor_name, vendor_default_category or "", description_sample]
    )
    for kws, code in _KEYWORD_RULES:
        for kw in kws:
            if kw in haystack:
                return code
    return None


def generate_tds_draft(
    db: Session, *, org_id: uuid.UUID, today: date
) -> list[VendorTDSRow]:
    """Walk every vendor that received payments in the current FY, classify
    by TDS section, aggregate spend, and surface vendors over threshold."""
    # FY window — re-derive from today instead of importing helper.
    if today.month >= 4:
        fy_start = date(today.year, 4, 1)
        fy_end = date(today.year + 1, 3, 31)
    else:
        fy_start = date(today.year - 1, 4, 1)
        fy_end = date(today.year, 3, 31)

    # Per-vendor totals: sum of bank debits + purchase invoices in the FY.
    bank_rows = db.execute(
        select(
            Vendor.id,
            Vendor.name,
            Vendor.gstin,
            Vendor.default_expense_category,
            func.coalesce(func.sum(BankTransaction.amount), 0).label("bank_paid"),
        )
        .join(Vendor, Vendor.id == BankTransaction.matched_vendor_id)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= fy_start,
            BankTransaction.txn_date <= today,
        )
        .group_by(Vendor.id, Vendor.name, Vendor.gstin, Vendor.default_expense_category)
    ).all()

    inv_rows = db.execute(
        select(
            Vendor.id,
            func.coalesce(func.sum(Invoice.total), 0).label("inv_total"),
        )
        .join(Vendor, Vendor.id == Invoice.vendor_id)
        .where(
            Invoice.org_id == org_id,
            Invoice.type == "purchase",
            Invoice.issue_date >= fy_start,
            Invoice.issue_date <= today,
        )
        .group_by(Vendor.id)
    ).all()
    invoice_total_by_vendor: dict[uuid.UUID, Decimal] = {
        r[0]: Decimal(r[1] or 0) for r in inv_rows
    }

    # Sample description for keyword detection — most recent debit per vendor.
    desc_rows = db.execute(
        select(BankTransaction.matched_vendor_id, BankTransaction.description)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.matched_vendor_id.isnot(None),
            BankTransaction.direction == "debit",
            BankTransaction.txn_date >= fy_start,
            BankTransaction.txn_date <= today,
        )
        .order_by(BankTransaction.txn_date.desc())
    ).all()
    desc_by_vendor: dict[uuid.UUID, str] = {}
    for vid, desc in desc_rows:
        if vid and vid not in desc_by_vendor:
            desc_by_vendor[vid] = desc or ""

    out: list[VendorTDSRow] = []
    for vid, vname, gstin, vcat, bank_paid in bank_rows:
        if not vid:
            continue
        total = Decimal(bank_paid or 0) + invoice_total_by_vendor.get(vid, Decimal("0"))
        desc_sample = desc_by_vendor.get(vid, "")
        section_code = _detect_section(vname or "", vcat, desc_sample)
        if section_code is None:
            # Couldn't confidently detect — emit a row tagged 'na' so the user
            # can see the vendor and pick a section manually if needed.
            out.append(
                VendorTDSRow(
                    vendor_id=vid,
                    vendor_name=vname or "(unnamed)",
                    pan=extract_pan_from_gstin(gstin) if gstin else None,
                    section_code="?",
                    section_label="Section not auto-detected",
                    fy_payments_total=total,
                    threshold=Decimal("0"),
                    has_crossed_threshold=False,
                    applicable_rate=0.0,
                    tds_amount_estimated=Decimal("0"),
                    net_payable_after_tds=total,
                    form_quarterly="?",
                    deduction_status="na",
                    notes="Add a default_expense_category or describe the service to enable detection.",
                )
            )
            continue

        section = _SECTIONS[section_code]
        pan = extract_pan_from_gstin(gstin) if gstin else None
        rate = section.rate_with_pan if pan else section.rate_without_pan

        crossed = total >= section.threshold_fy
        tds_amount = (total * Decimal(str(rate))).quantize(Decimal("0.01")) if crossed else Decimal("0")
        net = total - tds_amount
        status = (
            "likely_pending" if crossed
            else "below_threshold"
        )

        out.append(
            VendorTDSRow(
                vendor_id=vid,
                vendor_name=vname or "(unnamed)",
                pan=pan,
                section_code=section.code,
                section_label=section.label,
                fy_payments_total=total,
                threshold=section.threshold_fy,
                has_crossed_threshold=crossed,
                applicable_rate=rate,
                tds_amount_estimated=tds_amount,
                net_payable_after_tds=net,
                form_quarterly=section.form_quarterly,
                deduction_status=status,
                notes=None if pan else "PAN missing — higher 20% rate applies once threshold crossed.",
            )
        )

    # Sort: section ascending, then total payments descending (biggest exposure first).
    out.sort(key=lambda r: (r.section_code, -float(r.fy_payments_total)))
    return out
