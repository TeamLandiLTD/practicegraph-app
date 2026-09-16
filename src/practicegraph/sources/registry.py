"""Adapter registry and collection entry point.

Collection is fail-open per file and per source (NFR-REL-1): an unreadable
file increments the source's malformed counter and never aborts the scan.
"""

from __future__ import annotations

from practicegraph.events import TurnEvent
from practicegraph.sources import ParseHealth, SourceAdapter, SourceHealthRow
from practicegraph.sources.claude_code import ADAPTER as CLAUDE_CODE_ADAPTER
from practicegraph.sources.codex import ADAPTER as CODEX_ADAPTER

ADAPTERS: tuple[SourceAdapter, ...] = (CLAUDE_CODE_ADAPTER, CODEX_ADAPTER)


def collect_events(env: dict[str, str]) -> tuple[list[TurnEvent], list[SourceHealthRow]]:
    """Discover and parse every supported source. Always returns one health row
    per registered source, even when no logs were found (honest zeros)."""
    all_events: list[TurnEvent] = []
    health_rows: list[SourceHealthRow] = []
    for adapter in ADAPTERS:
        health = ParseHealth()
        try:
            result = adapter.parse_many(adapter.discover(env))
            all_events.extend(result.events)
            health.merge(result.health)
        except Exception:
            # A whole source lane failing degrades that lane only (NFR-REL-1).
            health.malformed += 1
        health_rows.append(
            SourceHealthRow(
                source_id=adapter.source_id,
                capability=adapter.capability,
                parser_version=adapter.parser_version,
                health=health,
            )
        )
    return all_events, health_rows
