"""Synthetic documentation fixtures for schema and cache tests."""

import copy

from practicegraph.analysis.docs import parse_docs_artifact

SAMPLE_DOCS_DOCUMENT = {
    "schema": "practicegraph.docs/1",
    "docs_version": "docs-test-2026-09-05",
    "sections": [
        {
            "title": "Example section 1",
            "links": [
                {
                    "title": "Example reference 1",
                    "url": "https://example.com/docs/0/0",
                    "why": "Example reference 1",
                },
                {
                    "title": "Example reference 2",
                    "url": "https://example.com/docs/0/1",
                    "why": "Example reference 2",
                },
                {
                    "title": "Example reference 3",
                    "url": "https://example.com/docs/0/2",
                    "why": "Example reference 3",
                },
            ],
        },
        {
            "title": "Example section 2",
            "links": [
                {
                    "title": "Example reference 1",
                    "url": "https://example.com/docs/1/0",
                    "why": "Example reference 1",
                },
                {
                    "title": "Example reference 2",
                    "url": "https://example.com/docs/1/1",
                    "why": "Example reference 2",
                },
            ],
        },
        {
            "title": "Example section 3",
            "links": [
                {
                    "title": "Example reference 1",
                    "url": "https://example.com/docs/2/0",
                    "why": "Example reference 1",
                },
                {
                    "title": "Example reference 2",
                    "url": "https://example.com/docs/2/1",
                    "why": "Example reference 2",
                },
            ],
        },
        {
            "title": "Example section 4",
            "links": [
                {
                    "title": "Example reference 1",
                    "url": "https://example.com/docs/3/0",
                    "why": "Example reference 1",
                },
                {
                    "title": "Example reference 2",
                    "url": "https://example.com/docs/3/1",
                    "why": "Example reference 2",
                },
            ],
        },
    ],
}
SAMPLE_DOCS = parse_docs_artifact(SAMPLE_DOCS_DOCUMENT)
assert SAMPLE_DOCS is not None
_productivity = copy.deepcopy(SAMPLE_DOCS_DOCUMENT)
_productivity["docs_version"] = "docs-productivity-test-2026-09-05"
SAMPLE_DOCS_PRODUCTIVITY = parse_docs_artifact(_productivity)
assert SAMPLE_DOCS_PRODUCTIVITY is not None
