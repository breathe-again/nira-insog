"""Document extractors — turn raw PDFs / images into structured JSON.

Currently one implementation:

- `llm_vision` — sends the file to Anthropic's vision API and asks for a
  JSON payload matching `services/parsers/extracted_json.py`'s expected shape.

If `ANTHROPIC_API_KEY` is unset, extractors are disabled and the worker falls
back to the stub payload. This keeps the stack runnable without a key.
"""
