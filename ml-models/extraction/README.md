# ml-models/extraction/

Prompt templates, schema definitions, and benchmark evaluations for the LLM-driven extraction layer.

Files (planned):

- `schemas/` — Pydantic schemas defining the JSON output expected per document type.
- `prompts/` — prompt templates per document type and per LLM provider.
- `benchmark/` — labeled benchmark set of 100+ documents (test set frozen).
- `evaluate.py` — runs current prompts against the benchmark, reports per-field accuracy.

We treat prompt + schema as code — every change runs the eval harness in CI.
