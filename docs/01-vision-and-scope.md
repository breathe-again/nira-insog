# 01 — Vision and Scope

## The problem we are solving

In most small and mid-sized businesses, financial information is **scattered, slow, and stale**.

A typical week looks like this: invoices arrive over email and WhatsApp, receipts get clicked on phones and forgotten, bank statements are downloaded once a month, and an accountant manually keys everything into Tally or Excel. By the time the books are clean, two or three weeks have passed. The founder is making decisions on numbers that no longer reflect reality.

The consequences are real:

- Cash crunches happen "suddenly" because no one was tracking the trajectory.
- Overdue receivables sit uncollected because nobody is watching the aging.
- Duplicate payments slip through. Vendor over-charging goes unnoticed.
- GST and audit prep becomes a yearly fire drill instead of a continuous flow.

The root cause is not that the data does not exist — it does. The cause is that **nobody has time to read every document, link them together, and turn them into a clear picture in real time.**

## The solution

Nira Insig is an AI engine that does exactly that, automatically.

It accepts documents from any source (email, upload, WhatsApp, bank feed). It reads them using OCR + AI. It links related documents together — invoice ↔ payment, vendor bill ↔ bank debit, receipt ↔ expense category. It stores everything in a structured form. And it surfaces insights to the founder in a single dashboard, refreshed continuously.

The result: the founder sees the financial truth of the business **today**, not three weeks ago, and gets a forward-looking view of what is likely to happen next.

## The two kinds of insight we deliver

**Current insights** — descriptive answers about *what is happening right now*:
- "Cash balance across all accounts: ₹42 lakh."
- "Top 3 expense categories this month: rent, payroll, raw material."
- "Overdue receivables: ₹12 lakh from 4 clients."
- "Unusual: Vendor X billed 38% more than their 6-month average."

**Future insights** — predictive answers about *what is likely to happen*:
- "Based on receivable patterns, cash will dip below ₹15 lakh by July 18."
- "Quarterly GST liability projected at ₹8.4 lakh."
- "Client Y is showing late-payment behavior typical of accounts that churn in 90 days."

This second category is what makes the product strategically valuable. Recording what happened is a commodity. Predicting what is about to happen is a moat.

## Why this matters as a business

The accounting software market is dominated by tools that **record** transactions (Tally, Zoho Books, QuickBooks). They are essential, but they are passive — the user has to ask the right question to get a useful answer.

Nira Insig is positioned **one layer above**: an intelligence layer that reads what the recording tools (and the raw documents) contain, and proactively surfaces what matters. We are not competing with Tally; we sit on top of it.

The same engine extends naturally beyond accounting: sales contracts, HR documents, operational paperwork all follow the same pattern — ingest, extract, understand, insight. Accounting is the wedge; the platform is the prize.

## Phase 1 scope (what we are committing to first)

To keep the first release focused and shippable, Phase 1 covers:

**Document types in scope**
1. Bank transaction statements (CSV + PDF formats from major Indian banks).
2. Sales invoices issued by the company.
3. Purchase invoices / vendor bills received by the company.
4. Receipts and expense slips (photos, scans, PDFs).

**Insight categories in scope**
1. Cash flow — current balance, in/out trend, 30/60/90 day projection.
2. Receivables — outstanding, aging buckets, collection priority.
3. Payables & expenses — top vendors, category spend, anomalies.
4. Compliance readiness — GST-ready summary, audit trail.

**Users in scope**
- Founder / CEO (primary insight consumer).
- In-house accountant or finance lead (verifies extractions, corrects mistakes).
- One ops user (uploads receipts from the field).

**Out of scope for Phase 1**
- Multi-currency handling beyond INR (English only, INR only for v1).
- Direct bank API integration (we start with CSV/PDF upload; live bank feeds in Phase 2).
- Sales pipeline, HR, or operational document types.
- Mobile native app (responsive web only for v1; native app in Phase 2).

## What success looks like

By the end of Phase 1, a founder can:
1. Drop any of the four document types into one inbox.
2. See it extracted and categorized within 60 seconds.
3. Open one dashboard and see live cash, receivables, payables, and the next 60-day forecast.
4. Receive a weekly insight digest highlighting what changed and what needs attention.

If we hit those four marks, Phase 1 is a success.
