"""Parsers that turn raw inputs into typed Python records.

Two flavors:

- `bank_csv` — reads a bank-statement CSV and yields BankTransactionDraft objects.
- `extracted_json` — takes the JSON payload from the LLM extractor and
  produces InvoiceDraft / ReceiptDraft objects.

Parsers do NOT touch the database. They return drafts; the caller (Celery task
or test) persists them. This keeps parsing pure and easy to unit-test.
"""
