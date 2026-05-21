# 02 — System Architecture

This document describes the technical structure of Nira Insig: the components, how they talk to each other, and how data flows from a raw uploaded document to a finished insight on the dashboard.

## Architecture at a glance

```
                ┌────────────────────────────────────────────────────────┐
                │                       USERS                            │
                │   Founder/CEO   ·   Accountant   ·   Ops user          │
                └──────────────────────────┬─────────────────────────────┘
                                           │  (web dashboard, inbox)
                                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          FRONTEND LAYER                               │
│   Dashboard UI   ·   Inbox UI   ·   Insight digest views              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │  REST / WebSocket
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          API LAYER                                    │
│   Auth   ·   Org/User mgmt   ·   Document API   ·   Insight API      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────────────────────┐
        ▼                  ▼                                  ▼
┌──────────────┐   ┌────────────────┐               ┌──────────────────┐
│  INGESTION   │   │  EXTRACTION    │               │   INSIGHTS       │
│  Service     │──▶│  Service       │──┐         ┌─▶│   Service        │
│  (inbox)     │   │  (OCR + LLM)   │  │         │  │  (analytics + ML)│
└──────────────┘   └────────────────┘  ▼         │  └──────────────────┘
                                  ┌────────────────┐
                                  │ UNDERSTANDING  │
                                  │ Service        │
                                  │ (classify,     │
                                  │  link, dedupe) │
                                  └────────┬───────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │     LEARNING BUCKET      │
                              │  (structured data store) │
                              │  Postgres + object store │
                              └──────────┬───────────────┘
                                         │
                                         ▼
                              ┌──────────────────────────┐
                              │   FEEDBACK LOOP          │
                              │   (user corrections      │
                              │    → retraining queue)   │
                              └──────────────────────────┘
```

## Component-by-component

### 1. Frontend layer
Two main surfaces:
- **Inbox UI** — where users drop documents and watch them flow through extraction. Shows per-document status (uploaded → extracted → understood → indexed).
- **Dashboard UI** — the consolidated "altogether" view. Cards for cash, receivables, payables, anomalies, forecasts. Drill-down into any number opens the underlying documents.

Web-first (React). Mobile-responsive for v1; native app in Phase 2.

### 2. API layer
A single backend API gateway that the frontend talks to. Responsibilities:
- Authentication and multi-tenant authorization (each org's data isolated).
- Receiving uploads and dispatching them to the Ingestion service.
- Serving extracted documents and insights to the frontend.
- Webhook endpoints for future integrations (Gmail, WhatsApp, banks).

### 3. Ingestion service
The front door for documents. Handles:
- File uploads (drag-drop, email-to-inbox, WhatsApp-to-inbox).
- File type detection (PDF, image, CSV, Excel).
- Virus/file safety checks.
- Storing the raw file in object storage and creating a `Document` record.
- Queuing the document for extraction.

### 4. Extraction service
The "reader" of the system. Takes a raw file and outputs structured fields.

Two engines work together:
- **OCR engine** — converts image/PDF to text. Tesseract for cost-sensitive cases; cloud OCR (AWS Textract, Google Document AI) for accuracy-critical cases.
- **LLM extractor** — takes OCR text + prompt and returns structured JSON (vendor, date, amount, line items, etc.). We use a schema-guided prompt so the output is reliable.

Output: a `RawExtraction` JSON payload tied to the `Document` record.

### 5. Understanding service
This is what separates Nira Insig from a basic OCR app. It takes raw extractions and adds meaning:

- **Document classification** — is this a sales invoice, purchase invoice, bank statement, or receipt? (A small ML classifier handles this.)
- **Entity resolution** — "ABC Traders Pvt Ltd" and "A.B.C. Traders" are the same vendor; merge them.
- **Document linking** — match payments in the bank statement to outstanding invoices; match receipts to expense categories.
- **Duplicate detection** — flag if the same invoice has been uploaded twice.
- **Anomaly detection** — flag values that are far outside this company's normal range.

Output: structured, linked records written to the Learning Bucket.

### 6. Learning bucket (the data store)
The single source of truth.

- **Postgres** — for structured data (organizations, users, documents, transactions, invoices, vendors, clients, insights).
- **Object storage (S3 / equivalent)** — for raw uploaded files.
- **Vector store (pgvector or similar)** — for semantic search and similarity (used by the understanding layer).

### 7. Insights service
Reads from the Learning Bucket and produces the two kinds of insight:

- **Descriptive insights** — SQL + rule-based aggregations. Fast, deterministic. Drives most dashboard cards.
- **Predictive insights** — ML models trained on historical transaction patterns. Cash flow forecast (time-series), receivable collection probability (classification), expense anomaly scores.

Insights are recomputed on a schedule (every 15 min) and also reactively when a new document is indexed.

### 8. Feedback loop
Every time a user corrects something ("this wasn't a travel expense, it was client entertainment") the correction is captured as a labeled training example. Periodically these are batched and used to fine-tune the extraction and classification models — making the system smarter over time, and specifically smarter at *this customer's* patterns.

## Data flow — end to end

Here is what happens when a user uploads one receipt PDF:

1. **Upload** — User drops `receipt-2026-05-19.pdf` into the inbox. The API receives it, stores the raw file in S3, creates a `Document` record with status `received`, and pushes a job onto the extraction queue.

2. **Extraction** — A worker picks up the job. OCR runs on the PDF, producing raw text. The text is sent to the LLM with a schema prompt. Output: `{ vendor: "Cafe Coffee Day", date: "2026-05-18", amount: 480, currency: "INR", tax: 22, payment_mode: "UPI" }`. Document status becomes `extracted`.

3. **Understanding** — Another worker picks up the extracted JSON. It classifies the document as `receipt`, looks up the vendor in the org's known-vendor list (creates a new one if unseen), categorizes the spend as `food & beverage`, and checks for duplicates. Document status becomes `understood`.

4. **Storage** — The structured `Receipt` record is written to Postgres, linked to the `Document` and the `Vendor`. Status becomes `indexed`.

5. **Insight recompute** — The Insights service notices a new receipt has landed. It updates the relevant aggregates: this month's F&B spend, today's outflow, the cash position. If the amount or vendor is anomalous, it generates an `Insight` record of type `anomaly_alert`.

6. **Dashboard refresh** — The frontend, listening on a WebSocket channel, receives the update and refreshes the relevant cards. The founder, if their dashboard is open, sees the new number appear within seconds.

7. **Feedback (if any)** — If the accountant later opens the receipt and changes the category to `client meetings`, that correction is logged. The next training cycle will use it to improve the understanding service.

## Deployment topology

For Phase 1, a simple cloud deployment is enough:

- **App services (API, extraction workers, understanding workers, insight workers)** run as containerized services on a managed platform (AWS ECS / GCP Cloud Run / Render).
- **Postgres** — managed (AWS RDS / GCP Cloud SQL / Neon).
- **Object storage** — S3 / GCS.
- **Queue** — Redis (managed) or AWS SQS.
- **Frontend** — Vercel or Netlify.

For Phase 2+, when the user base grows, we'll move to Kubernetes and introduce read replicas, but premature scaling is not needed for v1.

## Security and tenancy

- Every record carries an `org_id`. All queries are scoped by `org_id` at the API layer.
- Encryption at rest (database and object storage).
- Encryption in transit (TLS everywhere).
- Audit log of every document access and every insight surfaced.
- Role-based access: founder sees everything; accountant sees documents + edits; ops user sees only their own uploads.

## What this architecture is NOT

To keep the scope honest, here is what we are deliberately not building in v1:

- We are not building a full bookkeeping/ledger engine. The system links to existing accounting tools rather than replacing them.
- We are not building a chat-with-your-finances feature yet. (Tempting, but Phase 2.)
- We are not training extraction models from scratch. We use proven OCR + LLM extractors and fine-tune at the edges.

That discipline is what will let us ship in 12 weeks.
