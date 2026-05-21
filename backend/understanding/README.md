# backend/understanding/

Turns raw extractions into linked, classified, deduplicated business facts.

Responsibilities:

- Document classification (bank statement / invoice / receipt / unknown).
- Vendor + Client entity resolution (fuzzy matching, alias merging).
- Expense categorization (rule + ML hybrid; learns from feedback).
- Bank-transaction-to-invoice matching.
- Duplicate detection.
- Statistical + rule-based anomaly detection.

Output: writes structured records (Invoice, Receipt, BankTransaction, Vendor, Client) into Postgres.
