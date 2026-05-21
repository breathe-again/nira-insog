# 03 — Data Model

This document defines the shape of the data the system stores. Every other layer (extraction, understanding, insights) reads or writes one of these entities.

The data model is intentionally simple in Phase 1. We optimize for clarity over cleverness; complexity gets added when a feature demands it.

## Core entities (Phase 1)

### Organization
Every customer is an Organization. Multi-tenant isolation is enforced at this level.

| Field | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| name | string | legal name |
| gstin | string | optional, India-specific |
| created_at | timestamp | |
| plan | enum | `trial`, `paid` |

### User
A person who logs in.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | FK Organization |
| email | string | unique |
| role | enum | `founder`, `accountant`, `ops`, `viewer` |
| created_at | timestamp | |

### Document
The atomic unit of input — one file uploaded.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| uploaded_by | UUID | FK User |
| source | enum | `upload`, `email`, `whatsapp`, `bank_api` |
| file_url | string | object storage URL |
| file_type | enum | `pdf`, `image`, `csv`, `xlsx` |
| document_type | enum | `bank_statement`, `sales_invoice`, `purchase_invoice`, `receipt`, `unknown` |
| status | enum | `received`, `extracting`, `extracted`, `understood`, `indexed`, `error` |
| raw_extraction_json | jsonb | output of extraction service |
| created_at | timestamp | |
| processed_at | timestamp | nullable |

### Vendor
A counterparty the business pays.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| name | string | canonical name |
| aliases | string[] | alternate names seen in documents |
| gstin | string | optional |
| default_expense_category | string | learned over time |
| created_at | timestamp | |

### Client
A counterparty the business is paid by. (Same shape as Vendor.)

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| name | string | |
| aliases | string[] | |
| gstin | string | |
| created_at | timestamp | |

### BankTransaction
One line item from a bank statement.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| document_id | UUID | FK Document (the statement it came from) |
| account_id | UUID | FK BankAccount |
| txn_date | date | |
| description | string | raw description from bank |
| amount | decimal | always positive |
| direction | enum | `credit`, `debit` |
| running_balance | decimal | nullable |
| matched_invoice_id | UUID | nullable, FK Invoice (set by understanding layer) |
| matched_vendor_id | UUID | nullable |
| matched_client_id | UUID | nullable |
| category | string | e.g. `payroll`, `rent`, `vendor_payment` |
| created_at | timestamp | |

### BankAccount
| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| bank_name | string | |
| account_number_last4 | string | mask the rest |
| currency | string | `INR` for v1 |
| current_balance | decimal | last known |

### Invoice
Covers both sales (issued) and purchase (received) invoices.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| document_id | UUID | FK Document |
| type | enum | `sales`, `purchase` |
| invoice_number | string | as written on the invoice |
| counterparty_id | UUID | FK Vendor (if purchase) or Client (if sales) |
| issue_date | date | |
| due_date | date | nullable |
| subtotal | decimal | |
| tax | decimal | |
| total | decimal | |
| currency | string | |
| status | enum | `draft`, `issued`, `partially_paid`, `paid`, `overdue` |
| line_items | jsonb | array of `{description, qty, rate, amount}` |
| created_at | timestamp | |

### Receipt
A standalone expense receipt (a receipt that supports an invoice should be linked to it, but most receipts are standalone like cab fares, meals, supplies).

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| document_id | UUID | FK Document |
| vendor_id | UUID | nullable |
| date | date | |
| amount | decimal | |
| tax | decimal | nullable |
| category | string | learned |
| payment_mode | enum | `cash`, `card`, `upi`, `bank_transfer`, `unknown` |
| notes | string | |
| created_at | timestamp | |

### Insight
A derived statement about the business.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| type | enum | `cash_position`, `receivable_aging`, `expense_anomaly`, `cash_forecast`, `vendor_alert`, `compliance_summary`, ... |
| severity | enum | `info`, `attention`, `urgent` |
| title | string | one-line summary |
| body | string | full statement |
| supporting_data | jsonb | numbers and pointers to source records |
| created_at | timestamp | |
| dismissed_by | UUID | nullable |
| dismissed_at | timestamp | nullable |

### FeedbackEvent
Captures every correction the user makes — feeds the learning loop.

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| org_id | UUID | |
| user_id | UUID | who made the correction |
| entity_type | string | e.g. `Receipt`, `Invoice` |
| entity_id | UUID | which record |
| field | string | which field was corrected |
| old_value | jsonb | |
| new_value | jsonb | |
| created_at | timestamp | |

## Relationships at a glance

```
Organization 1 ── ∞ User
Organization 1 ── ∞ Document
Organization 1 ── ∞ Vendor
Organization 1 ── ∞ Client
Organization 1 ── ∞ BankAccount

Document 1 ── 0..1 BankTransaction (if it is a statement, it has many)
Document 1 ── 0..1 Invoice
Document 1 ── 0..1 Receipt

BankTransaction ∞ ── 0..1 Invoice (matched payment)
Invoice ∞ ── 1 Vendor or Client (counterparty)
Receipt ∞ ── 0..1 Vendor

Insight ∞ ── 1 Organization
FeedbackEvent ∞ ── 1 User
```

## Indexing strategy (Phase 1)

- `BankTransaction(org_id, txn_date)` — most queries are time-bounded.
- `Invoice(org_id, status, due_date)` — receivable aging queries.
- `Receipt(org_id, date, category)` — expense reports.
- `Vendor(org_id, name)` and a trigram index on `name` + `aliases` for fuzzy lookup.
- `Insight(org_id, created_at, dismissed_at)` — dashboard feed.

## What we are NOT modeling in Phase 1

To stay disciplined:

- No journal entries / double-entry bookkeeping (we are an insight layer, not an accounting system).
- No multi-currency conversion (INR only).
- No inventory or stock data.
- No payroll-employee-level data.
- No fixed-asset depreciation.

These are all valid Phase 2+ additions, but not now.
