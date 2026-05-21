# data/schemas/

Canonical JSON schemas for every extracted entity.

Single source of truth for:

- The expected output of the LLM extractor.
- Pydantic schemas on the backend (generated from these).
- TypeScript types on the frontend (generated from these).

Planned files:

- `document.schema.json`
- `bank-transaction.schema.json`
- `invoice.schema.json`
- `receipt.schema.json`
- `vendor.schema.json`
- `client.schema.json`
- `insight.schema.json`

Schemas are versioned. Breaking changes get a new major version; the migration plan is documented in the PR description.
