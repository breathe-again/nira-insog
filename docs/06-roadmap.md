# 06 — Roadmap

This is the build plan. Phase 1 is laid out week-by-week so it can be tracked. Phase 2+ is sketched at the theme level — we will detail it once Phase 1 lands.

## Phase 1 — Accounting Insight Engine (12 weeks)

Goal: a working product that ingests bank statements, invoices, and receipts; produces live + predictive insights; and has 3–5 pilot customers actively using it.

### Week 0 — Pre-build

- Final architecture review (this doc set).
- Set up GitHub org, monorepo, CI skeleton.
- Provision dev AWS account and base infrastructure (VPC, RDS, S3, ECS cluster).
- Hire / confirm team (see `07-team-and-resources.md`).

### Weeks 1–2 — Foundations

- Database schema + Alembic migrations for all Phase 1 entities.
- Auth: email/password + Google SSO + roles + org tenancy.
- Skeleton FastAPI app with `/health`, `/auth/*`, `/orgs/*` endpoints.
- React + Vite + Tailwind app with login, signup, empty dashboard shell.
- Basic CI: lint, type-check, unit tests, deploy to dev.

**Exit criteria:** A user can sign up, create an org, invite teammates, and land on an empty dashboard.

### Weeks 3–4 — Ingestion + Storage

- Inbox UI: drag-drop upload, file list, per-document status.
- Backend upload endpoint with file safety + storage in S3.
- Document entity with status state machine.
- Celery + Redis wired up; placeholder extraction worker that just transitions status.
- WebSocket channel for live status updates in the inbox UI.
- Email-to-inbox: each org gets a unique inbound email address.

**Exit criteria:** Users can upload any file; it appears in their inbox; status flows through the state machine; raw file is safely stored.

### Weeks 5–6 — Extraction service

- OCR pipeline: Tesseract baseline + Textract fallback for low-confidence cases.
- LLM extractor with Pydantic-typed schemas per document type (bank statement, invoice, receipt).
- Per-field confidence scoring.
- CSV parser for bank statements (HDFC, ICICI, SBI, Axis, Kotak templates).
- Inbox detail view: original file on the left, extracted fields on the right (editable).
- Feedback events captured on every edit.

**Exit criteria:** 90% field-level extraction accuracy on a benchmark set of 100 mixed documents.

### Weeks 7–8 — Understanding service

- Document classifier (bank statement / invoice / receipt / unknown).
- Vendor + Client entity resolution with fuzzy matching.
- Expense categorization (rule + ML hybrid).
- Bank-transaction-to-invoice matching engine.
- Duplicate detection.
- Anomaly detection (statistical, with thresholds explainable in the UI).

**Exit criteria:** A new uploaded purchase invoice automatically links to the vendor, categorizes the expense, and matches to the paying bank transaction if one already exists.

### Weeks 9–10 — Insights service (current + dashboard)

- Aggregation jobs: cash position, in/out trends, receivable aging, top vendors/clients, expense by category, GST summary.
- Insight feed (anomalies, vendor alerts) generated and surfaced as cards.
- Dashboard UI: Altogether view with cards, charts, drill-downs.
- Date-range filter, real-time WebSocket refresh.
- Drill-down from any number to the underlying documents.

**Exit criteria:** Founder logs into the dashboard and sees an accurate, real-time financial picture; can click any number to see the source.

### Weeks 11–12 — Predictive insights + polish + pilot launch

- Cash flow forecast (Prophet model, weekly retrain).
- Receivable collection probability scoring.
- Quarterly GST liability projection.
- Weekly digest email (founder + accountant).
- Bulk re-processing, error recovery flows.
- Audit log surface in UI.
- Performance pass (P95 targets in `04-functional-requirements.md`).
- Security pass (DPDP compliance review, basic pen-test).
- Onboard 3–5 pilot customers and run with them.

**Exit criteria:** Pilot customers reach the "I check this every morning" usage pattern. NPS conversation with each pilot.

### Phase 1 milestone summary

| Milestone | Week | Deliverable |
|---|---|---|
| M1: Foundations | 2 | Auth + empty app deployed |
| M2: Ingestion live | 4 | Upload → stored, real-time inbox |
| M3: Extraction live | 6 | 90% extraction accuracy on benchmark |
| M4: Understanding live | 8 | Auto-linking, categorization, anomalies |
| M5: Dashboard live | 10 | Founder sees real-time picture |
| M6: Predictive + pilot | 12 | Forecasts + 3–5 pilots using daily |

---

## Phase 2 — Expansion (months 4–9 post-launch)

Themes (not week-by-week yet — will detail nearer the time):

**Native mobile app** — most receipts get clicked on a phone. iOS + Android with a receipt-capture-first experience.

**Direct bank integration** — Account Aggregator framework (India) or Plaid/Setu for live bank feeds. Removes the upload-statement step entirely.

**AI chat ("ask my finances anything")** — natural-language query over the warehouse. "How much did we spend on marketing last quarter?" → answered with chart + sources.

**Sales document type** — quotes, purchase orders, sales contracts. Unlocks revenue forecasting and pipeline insight.

**Tally / Zoho / QuickBooks two-way sync** — many customers have an existing accounting tool. We pull from it AND push insights back.

**Multi-entity consolidation** — for customers with multiple legal entities.

**Multi-currency** — for export businesses and SaaS companies billing globally.

## Phase 3 — Platform play (months 9–18 post-launch)

Themes:

**HR documents** — offer letters, payroll registers, attendance data. Workforce cost insights.

**Operations documents** — delivery challans, GRNs, work orders. Supply chain insights, vendor SLA tracking.

**Legal documents** — contracts. Obligation extraction, renewal alerts.

**Public API + marketplace** — let third parties build on top of the extracted, structured data warehouse.

**Enterprise tier** — SAML SSO, SOC 2, on-prem option for regulated customers.

## What we will measure

Across all phases the same north-star and supporting metrics:

**North star:** number of business decisions per month driven by a Nira Insig insight (self-reported by users + correlated with in-app dismiss/action data).

**Supporting metrics:**
- Time from document upload to extracted + indexed (target: <60s P95).
- Field-level extraction accuracy (target: >95% steady state).
- Insight surfacing accuracy (true-positive anomalies vs false alarms).
- Weekly active org-users.
- % of insights dismissed (signal of noise).
- Customer-stated time saved per week (interview metric).
