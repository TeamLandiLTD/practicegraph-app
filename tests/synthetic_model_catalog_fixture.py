"""Synthetic schema fixtures; no editorial recommendations are distributed here."""

import copy

from practicegraph.analysis.model_catalog import parse_model_catalog

SAMPLE_CATALOG_DOCUMENT = {
    "schema": "practicegraph.model-catalog/1",
    "catalog_version": "catalog-test-2026-09-05",
    "closing": "Synthetic catalog for parser tests; this is not model guidance.",
    "tools": [
        {
            "tool": "claude_code",
            "models": [
                {
                    "model": "Claude Opus",
                    "pin": "opus",
                    "role": "strongest",
                    "when": "Synthetic model row for parser and pin tests.",
                    "price_note": "Example price label.",
                },
                {
                    "model": "Claude Sonnet",
                    "pin": "sonnet",
                    "role": "everyday",
                    "when": "Synthetic model row for parser and pin tests.",
                    "price_note": "Example price label.",
                },
                {
                    "model": "Claude Haiku",
                    "pin": "haiku",
                    "role": "fast",
                    "when": "Synthetic model row for parser and pin tests.",
                    "price_note": "Example price label.",
                },
            ],
            "efforts": [
                {
                    "level": "low",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "good",
                    "recommended": False,
                },
                {
                    "level": "medium",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "good",
                    "recommended": False,
                },
                {
                    "level": "high",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "neutral",
                    "recommended": True,
                },
            ],
            "practices": [
                {"title": "Example practice", "body": "Synthetic explanatory text for a test."}
            ],
            "practices_source": "Synthetic test source.",
            "switch": ["Example configuration step for a test."],
        },
        {
            "tool": "codex",
            "models": [
                {
                    "model": "gpt-5.6-sol",
                    "pin": "gpt-5.6-sol",
                    "role": "strongest",
                    "when": "Synthetic model row for parser and pin tests.",
                    "price_note": "Example price label.",
                },
                {
                    "model": "gpt-5.6-terra",
                    "pin": "gpt-5.6-terra",
                    "role": "everyday",
                    "when": "Synthetic model row for parser and pin tests.",
                    "price_note": "Example price label.",
                },
                {
                    "model": "gpt-5.6-luna",
                    "pin": "gpt-5.6-luna",
                    "role": "fast",
                    "when": "Synthetic model row for parser and pin tests.",
                    "price_note": "Example price label.",
                },
            ],
            "efforts": [
                {
                    "level": "low",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "good",
                    "recommended": False,
                },
                {
                    "level": "medium",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "good",
                    "recommended": True,
                },
                {
                    "level": "high",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "neutral",
                    "recommended": False,
                },
                {
                    "level": "xhigh",
                    "when": "Synthetic effort row for parser tests.",
                    "tone": "costly",
                    "recommended": False,
                },
            ],
            "practices": [
                {"title": "Example practice", "body": "Synthetic explanatory text for a test."}
            ],
            "practices_source": "Synthetic test source.",
            "switch": ["Example configuration step for a test."],
        },
    ],
}
SAMPLE_CATALOG = parse_model_catalog(SAMPLE_CATALOG_DOCUMENT)
assert SAMPLE_CATALOG is not None
_productivity = copy.deepcopy(SAMPLE_CATALOG_DOCUMENT)
_productivity["catalog_version"] = "catalog-productivity-test-2026-09-05"
for tool in _productivity["tools"]:
    tool["models"][1]["when"] = "Synthetic example for documents."
    tool["practices"][0]["title"] = "Scope the benchmarks in this test"
SAMPLE_CATALOG_PRODUCTIVITY = parse_model_catalog(_productivity)
assert SAMPLE_CATALOG_PRODUCTIVITY is not None
