# 04 — Functional Requirements

This document lists what the system must do, organized by capability area. Each requirement has an ID so it can be referenced in build tickets and tests.

Legend: **MUST** = required for Phase 1 launch. **SHOULD** = strongly preferred but optional. **LATER** = Phase 2+.

---

## 1. Document ingestion

| ID | Requirement | Priority |
|---|---|---|
| ING-1 | User can drag-and-drop one or many files into the inbox. | MUST |
| ING-2 | Supported file types: PDF, JPG, PNG, CSV, XLSX. | MUST |
| ING-3 | Max single file size: 25 MB. | MUST |
| ING-4 | System detects file type automatically; user does not pre-tag. | MUST |
| ING-5 | Email-to-inbox: each org gets a unique `<org-id>@in.nira-insig.com` address. | SHOULD |
| ING-6 | WhatsApp-to-inbox via WhatsApp Business API. | LATER |
| ING-7 | Direct bank API integration (Plaid / Setu / Account Aggregator). | LATER |
| ING-8 | System rejects files that fail virus scan. | MUST |

## 2. Extraction

| ID | Requirement | Priority |
|---|---|---|
| EXT-1 | Bank statement (PDF) extraction must parse line items into structured rows. | MUST |
| EXT-2 | Bank statement (CSV) extraction must handle major Indian bank formats (HDFC, ICICI, SBI, Axis, Kotak). | MUST |
| EXT-3 | Invoice extraction must capture: invoice number, date, counterparty, line items, subtotal, tax, total. | MUST |
| EXT-4 | Receipt extraction must capture: vendor, date, amount, tax, payment mode. | MUST |
| EXT-5 | Extraction confidence must be returned per field; low-confidence fields are flagged for human review. | MUST |
| EXT-6 | Time from upload to extracted < 60 seconds for 95% of documents under 10 pages. | MUST |
| EXT-7 | Multi-language receipts (Hindi, Tamil, Bengali) supported. | LATER |

## 3. Understanding

| ID | Requirement | Priority |
|---|---|---|
| UND-1 | System classifies every document into one of: bank statement, sales invoice, purchase invoice, receipt, unknown. | MUST |
| UND-2 | System recognizes recurring vendors and clients by name (with fuzzy matching for variations). | MUST |
| UND-3 | System auto-categorizes expenses based on vendor + description (with override). | MUST |
| UND-4 | System matches bank transactions to outstanding invoices by amount and date proximity. | MUST |
| UND-5 | System flags duplicate uploads (same invoice number + same counterparty + same amount). | MUST |
| UND-6 | System detects anomalies: amounts > 2 standard deviations from a vendor's mean, or > 50% above 6-month average. | MUST |
| UND-7 | User can override any auto-categorization; override is captured as a feedback event. | MUST |

## 4. Storage and search

| ID | Requirement | Priority |
|---|---|---|
| STO-1 | Every original uploaded file is retained in object storage for at least 7 years. | MUST |
| STO-2 | All structured data is queryable via the API. | MUST |
| STO-3 | Full-text search across vendors, clients, line items, descriptions. | MUST |
| STO-4 | Documents are isolated per organization at the query layer. | MUST |
| STO-5 | Soft-delete only — nothing is permanently removed without a 30-day grace period. | MUST |

## 5. Insights — current (descriptive)

| ID | Requirement | Priority |
|---|---|---|
| INS-1 | Real-time cash position across all bank accounts. | MUST |
| INS-2 | Cash in/out trend chart (7d, 30d, 90d, 12m views). | MUST |
| INS-3 | Receivables outstanding by aging bucket (0–30, 31–60, 61–90, 90+ days). | MUST |
| INS-4 | Top 5 vendors by spend (this month, this quarter). | MUST |
| INS-5 | Top 5 clients by revenue (this month, this quarter). | MUST |
| INS-6 | Expense breakdown by category (pie + table). | MUST |
| INS-7 | Anomaly feed: surfaced as a list of insight cards. | MUST |
| INS-8 | GST liability summary (output tax vs input tax credit). | MUST |
| INS-9 | Drill-down: clicking any number opens the underlying documents. | MUST |

## 6. Insights — future (predictive)

| ID | Requirement | Priority |
|---|---|---|
| PRD-1 | 30/60/90 day cash flow forecast based on historical patterns and known commitments. | MUST |
| PRD-2 | Receivable collection probability per overdue invoice. | SHOULD |
| PRD-3 | Projected quarterly GST liability. | MUST |
| PRD-4 | Vendor cost trend forecast (is vendor X getting more expensive?). | SHOULD |
| PRD-5 | Client risk score (is client Y showing churn-like late payment behavior?). | SHOULD |
| PRD-6 | Working-capital optimization suggestions. | LATER |

## 7. Dashboard

| ID | Requirement | Priority |
|---|---|---|
| DSH-1 | One unified dashboard ("Altogether view") shows cash, receivables, payables, insights, forecast. | MUST |
| DSH-2 | Date range filter (this month / quarter / year / custom). | MUST |
| DSH-3 | Real-time updates via WebSocket when new data arrives. | MUST |
| DSH-4 | Mobile-responsive layout. | MUST |
| DSH-5 | Native mobile app. | LATER |
| DSH-6 | Export any view to PDF or Excel. | SHOULD |

## 8. Inbox UI

| ID | Requirement | Priority |
|---|---|---|
| IBX-1 | List view of all uploaded documents with status. | MUST |
| IBX-2 | Filter by status, type, date, uploader. | MUST |
| IBX-3 | Per-document detail view: original file + extracted fields side by side. | MUST |
| IBX-4 | Edit extracted fields; save fires a feedback event. | MUST |
| IBX-5 | Bulk re-process for documents in error state. | MUST |

## 9. Notifications

| ID | Requirement | Priority |
|---|---|---|
| NOT-1 | Weekly insight digest emailed to founder every Monday. | MUST |
| NOT-2 | Urgent anomaly alerts (severity = urgent) emailed immediately. | MUST |
| NOT-3 | In-app notification feed. | MUST |
| NOT-4 | WhatsApp / SMS alerts. | LATER |

## 10. Auth, users, and permissions

| ID | Requirement | Priority |
|---|---|---|
| AUT-1 | Email + password login. | MUST |
| AUT-2 | Google SSO. | MUST |
| AUT-3 | Multi-user per organization with roles: founder, accountant, ops, viewer. | MUST |
| AUT-4 | Role-based access (founder sees all; ops sees own uploads; etc.). | MUST |
| AUT-5 | Audit log of every document view and every insight viewed. | MUST |
| AUT-6 | 2FA. | SHOULD |
| AUT-7 | SSO (SAML/OIDC) for enterprise. | LATER |

## 11. Feedback loop

| ID | Requirement | Priority |
|---|---|---|
| FBK-1 | Every user correction is stored as a `FeedbackEvent`. | MUST |
| FBK-2 | Feedback events are exportable for retraining. | MUST |
| FBK-3 | A weekly job retrains the classification + categorization models on accumulated feedback. | SHOULD |
| FBK-4 | Closed-loop measurement: % of fields needing correction trend (should drop over time). | MUST |

## 12. Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| NFR-1 | Page load (dashboard, P95) < 2 seconds. | MUST |
| NFR-2 | API response (P95) < 500 ms for read endpoints. | MUST |
| NFR-3 | Document processing pipeline can handle 500 documents/hour per org. | MUST |
| NFR-4 | 99.5% uptime in Phase 1 (best-effort, not contractual SLA yet). | MUST |
| NFR-5 | All data encrypted at rest and in transit. | MUST |
| NFR-6 | Customer data export (full account) on request, within 7 days. | MUST |
| NFR-7 | Compliance: India Digital Personal Data Protection Act (DPDP). | MUST |
| NFR-8 | SOC 2 readiness. | LATER |

---

## Out of scope for Phase 1

Calling these out explicitly so they do not silently creep in:

- Full ledger / double-entry bookkeeping.
- Tax filing (we produce GST summaries; the user still files via their CA).
- Inventory management.
- Payroll processing.
- Multi-entity consolidation (one org = one entity in v1).
- AI chat ("ask my finances anything") — Phase 2 feature, very high priority then.
