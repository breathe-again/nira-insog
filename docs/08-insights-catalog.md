# 08 — Insights Catalog

This is the menu of insights the system produces. Each insight is described with a short definition, what data it reads, when it fires, and a real example of how it would surface to the user.

Insights fall into three buckets:

1. **Current insights** — descriptive. "Here's what is true right now."
2. **Future insights** — predictive. "Here is what is likely to happen."
3. **Action insights** — prescriptive. "Here is what you should consider doing."

For Phase 1 we deliver bucket 1 + bucket 2. Bucket 3 starts in Phase 2 once we have enough usage data to make confident recommendations.

---

## Current insights (descriptive)

### Cash position
**Definition:** Total cash across all connected bank accounts, right now.
**Reads from:** `BankTransaction` (latest running balance per account).
**Refresh:** Every time a new statement is uploaded; otherwise on schedule.
**Surface as:** Headline number on the dashboard.
**Example:**
> Total cash: **₹42,30,000**
> Across 3 accounts. Up ₹4.1L from last week.

### Cash flow trend
**Definition:** Money in vs money out over a rolling window.
**Reads from:** `BankTransaction` (aggregated by direction and date).
**Refresh:** Continuous.
**Surface as:** Stacked bar chart (7d / 30d / 90d / 12m views).
**Example:** "May: ₹62L in, ₹48L out. Net positive ₹14L — best month this quarter."

### Receivables aging
**Definition:** Outstanding invoices grouped by how long they are overdue.
**Reads from:** `Invoice` where status ≠ paid.
**Refresh:** Daily.
**Surface as:** Aging buckets table + amount per bucket.
**Example:**
> Outstanding receivables: **₹18,40,000**
> 0–30 days: ₹6.2L · 31–60 days: ₹4.8L · 61–90 days: ₹3.1L · 90+ days: ₹4.3L
> 4 invoices in the 90+ bucket — needs attention.

### Top vendors by spend
**Definition:** Ranked list of vendors by total amount paid this period.
**Reads from:** `Invoice` (purchase) + `BankTransaction` linked to vendors.
**Surface as:** Top 5 list with trend arrow (vs previous period).
**Example:** "1. ABC Traders — ₹3.4L (↑18% vs April). 2. XYZ Office Supplies — ₹2.1L (flat). ..."

### Top clients by revenue
**Definition:** Ranked list of clients by amount received this period.
**Reads from:** `Invoice` (sales) + matching `BankTransaction` credits.
**Surface as:** Top 5 list with trend.
**Example:** "1. Acme Corp — ₹14.2L (↓ 22% vs April). 2. Globex — ₹8.5L (↑ 40%). ..."

### Expense by category
**Definition:** Total spend in each category for the period.
**Reads from:** `Receipt` + `Invoice` (purchase) + `BankTransaction` (categorized debits).
**Surface as:** Pie chart + detail table.
**Example:** "Payroll: 41% · Rent: 12% · Raw material: 18% · Marketing: 7% · Travel: 5% · Other: 17%."

### GST liability summary
**Definition:** Output GST collected vs input GST credit available, current quarter to date.
**Reads from:** `Invoice` (both directions, tax field).
**Surface as:** Two numbers + net.
**Example:**
> **Output GST collected: ₹6.4L · Input GST credit: ₹2.1L · Net payable: ₹4.3L**
> Quarter ends in 27 days. Filing window 1–20 of next month.

### Anomaly feed
**Definition:** A real-time list of items that look unusual.
**Reads from:** All transaction streams; rules + statistical models.
**Surface as:** Ordered cards in a "Needs attention" feed.
**Sub-types:**
- **Amount anomaly:** "Vendor ABC Traders invoiced ₹4.2L — 38% above their 6-month average."
- **Duplicate document:** "Invoice #INV-104 appears uploaded twice (today and on May 18)."
- **Missing payment:** "Sales invoice to Globex (₹3.1L) is 12 days past due — no matching credit in the bank."
- **Unusual category:** "Receipt for ₹85,000 categorized as 'food & beverage' — outside normal range for that category."

### Compliance readiness
**Definition:** A health-check across the data needed for tax filings.
**Reads from:** All entities; checks completeness.
**Surface as:** Checklist with green/amber/red items.
**Example:**
- ✅ 100% of sales invoices have GSTIN
- ✅ 98% of purchase invoices match HSN codes
- ⚠ 7 receipts have no vendor identified — review needed
- ✅ Bank statements complete through May 18

---

## Future insights (predictive)

### Cash flow forecast (30 / 60 / 90 day)
**Definition:** Projected cash position at future dates based on historical patterns plus known commitments (outstanding payables, expected receivables).
**Model:** Prophet for seasonal/trend baseline + explicit overlay of dated future commitments.
**Surface as:** Line chart with confidence band; numeric projection at +30, +60, +90 days.
**Example:**
> Projected cash on **July 18: ₹14.8L** (range: ₹11–19L).
> Below the ₹15L threshold you flagged. Top contributors: ₹8L payroll + ₹6L vendor payments due.

### Receivable collection probability
**Definition:** For each overdue invoice, the probability it gets collected in the next 30 days.
**Model:** Gradient boosting on features: client payment history, invoice age, communication recency, amount, category.
**Surface as:** A column on the receivables table; sorted by "least likely to collect."
**Example:**
> Acme Corp · INV-2026-031 · ₹3.4L · 47 days overdue · **Collection likelihood: 28%**
> Last paid invoice (INV-2026-019) took 61 days. Trend: slowing.

### Projected quarterly GST liability
**Definition:** Extrapolation of current QTD GST liability to end-of-quarter.
**Model:** Run-rate + seasonality adjustment.
**Surface as:** A single number with confidence range, refreshed daily.
**Example:** "Projected Q1 (Apr–Jun) net GST payable: **₹9.8L ± ₹0.6L**. Set aside accordingly."

### Vendor cost trend
**Definition:** Is the average cost from a vendor rising faster than expected?
**Model:** Linear trend with significance test per vendor.
**Surface as:** Insight card on the vendor's detail page; aggregated into "rising vendors" list.
**Example:**
> ABC Traders' average monthly billing has risen **23% over 6 months**, vs your overall vendor cost trend of 4%. Consider renegotiation or sourcing alternates.

### Client risk score
**Definition:** A 0–100 score per client estimating churn or payment-default risk.
**Model:** Logistic regression on: recent payment delay trend, drop in invoice frequency, average days-to-pay.
**Surface as:** Score chip on client detail; aggregated into "at-risk clients" list.
**Example:** "Globex risk score: **72 (high).** Last 3 invoices paid 18, 24, 31 days late respectively. Invoice frequency down 40% vs Q4."

---

## Action insights (Phase 2)

For each piece of bucket-3 insight, the system goes beyond observation and proposes a concrete next step. Examples we have in the backlog for Phase 2:

- "Collect ₹3.4L from Acme by emailing them this reminder draft → [Send]."
- "Move ₹15L from current account to a 30-day fixed deposit; you will not need it before then. Estimated extra interest: ₹12,500."
- "Renegotiate with ABC Traders. Their pricing is now 18% above market based on [comparable data]."
- "File GST early this quarter — your input credit is fully matched and locking it in protects against vendor amendments."

These require more user trust + product maturity, so we earn them in Phase 1 before shipping them in Phase 2.

---

## How insights are delivered to the user

Each insight is delivered through one or more channels, based on severity:

| Severity | Dashboard | In-app feed | Email | Urgent push |
|---|---|---|---|---|
| Info | ✅ | ✅ | weekly digest | — |
| Attention | ✅ | ✅ | weekly digest | — |
| Urgent | ✅ (top of feed) | ✅ | immediate email | (Phase 2: SMS / WhatsApp) |

Every insight is dismissible, snoozable, and traceable (the user can always see *why* the system flagged it, with links to the source documents).
