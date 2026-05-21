# backend/extraction/

Reads raw files and produces structured field-level extractions.

Components:

- OCR adapters (Tesseract baseline, AWS Textract fallback).
- LLM extractor (Claude via Anthropic API) with Pydantic-typed schemas per document type.
- Per-field confidence scoring.
- CSV/XLSX parsers for bank statements (HDFC, ICICI, SBI, Axis, Kotak).

Output: writes `raw_extraction_json` on the `Document` record and transitions status to `extracted`.
