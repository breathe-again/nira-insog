# backend/

Python (FastAPI) backend for Nira Insig.

Modules:

- `api/` — FastAPI app, routes, auth, request/response models.
- `ingestion/` — receive uploads (web, email, future bank API), store raw files, dispatch jobs.
- `extraction/` — OCR + LLM extraction workers.
- `understanding/` — classification, entity resolution, linking, anomaly detection.
- `insights/` — aggregation jobs and predictive model serving.
- `common/` — shared models (SQLAlchemy), schemas (Pydantic), utilities, queue helpers.

Each module exposes a clear public interface; internal details stay private. We run this as a modular monolith — separate services later only if a module genuinely needs independent scaling.
