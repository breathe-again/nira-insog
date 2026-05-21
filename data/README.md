# data/

Sample data and JSON schemas for the project.

- `samples/` — anonymized sample documents (bank statements, invoices, receipts) used for local development and demos. **Never commit real customer data here.**
- `schemas/` — canonical JSON schemas for every extracted entity. The backend's Pydantic schemas and the frontend's generated TS types both derive from these.
