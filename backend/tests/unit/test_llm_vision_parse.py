"""Unit tests for the LLM-vision extractor's response-parsing helpers.

We don't make real API calls here — just verify that the helpers correctly
unwrap whatever shape Claude returns and reject malformed output.
"""

from __future__ import annotations

import pytest

from services.extractors.llm_vision import ExtractorError, _parse_json


def test_parses_plain_json():
    payload = _parse_json('{"document_type": "receipt", "amount": 100}')
    assert payload["document_type"] == "receipt"
    assert payload["amount"] == 100


def test_unwraps_markdown_json_fence():
    raw = "Sure! Here is the JSON:\n```json\n{\"a\": 1}\n```\nLet me know if you need more."
    payload = _parse_json(raw)
    assert payload == {"a": 1}


def test_unwraps_unlabeled_fence():
    raw = "```\n{\"a\": 1}\n```"
    payload = _parse_json(raw)
    assert payload == {"a": 1}


def test_extracts_json_from_messy_prose():
    """When the model prepends prose, fall back to first-{ to last-}."""
    raw = 'Here you go: {"document_type": "receipt", "amount": 100}'
    payload = _parse_json(raw)
    assert payload["document_type"] == "receipt"


def test_empty_response_raises():
    with pytest.raises(ExtractorError):
        _parse_json("")


def test_non_json_raises():
    with pytest.raises(ExtractorError):
        _parse_json("I cannot read this document.")


def test_non_object_raises():
    """Top-level must be an object, not an array or scalar."""
    with pytest.raises(ExtractorError):
        _parse_json('[1, 2, 3]')


def test_nested_braces_preserved():
    raw = '```json\n{"vendor": {"name": "Acme", "gstin": "29X"}}\n```'
    payload = _parse_json(raw)
    assert payload["vendor"]["name"] == "Acme"
    assert payload["vendor"]["gstin"] == "29X"
