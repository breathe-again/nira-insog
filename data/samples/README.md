# data/samples/

Anonymized sample documents for local development, demos, and the extraction benchmark.

**Files in this folder:**

| File | What it is | Try this |
|---|---|---|
| `sample_bank_statement.csv` | A small fake bank statement (April + early May 2026) | Drop it in the Inbox — should auto-classify as `bank_statement` |
| `sample_receipt.txt` | A plain-text receipt for a coffee shop visit | Drop it in the Inbox — should classify as `receipt` |

Rules:

1. **No real customer data, ever.** Use synthetic data or fully anonymized examples only.
2. Group new samples by document type: `bank-statements/`, `sales-invoices/`, `purchase-invoices/`, `receipts/`.
3. For each sample, include the expected extraction output as a sibling `.expected.json` file. The eval harness in `ml-models/extraction/` reads these.

This folder is committed to the repo so all developers have a consistent test bed.
