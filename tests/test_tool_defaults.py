"""The pinned session defaults: read locally, fail closed, judged upstream."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from practicegraph.analysis.model_intelligence import (
    build_model_recommendations,
    parse_model_artifact,
)
from practicegraph.analysis.tool_defaults import ToolDefault, read_tool_defaults


def _homes(tmp_path: Path) -> dict[str, str]:
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    codex.mkdir()
    claude.mkdir()
    return {
        "PRACTICEGRAPH_CODEX_HOME": str(codex),
        "PRACTICEGRAPH_CLAUDE_HOME": str(claude),
    }


def test_reads_the_codex_pin_with_profile_override(tmp_path: Path) -> None:
    env = _homes(tmp_path)
    (Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n'
        'model_reasoning_effort = "xhigh"\n'
        'profile = "daily"\n'
        '[profiles.daily]\n'
        'model = "gpt-5.6-terra"\n'
        'model_reasoning_effort = "medium"\n',
        encoding="utf-8",
    )
    claude, codex = read_tool_defaults(env)
    # The active profile wins, exactly as the CLI resolves it.
    assert codex == ToolDefault("codex", "gpt-5.6-terra", "medium")
    assert claude == ToolDefault("claude_code", None, None)


def test_reads_the_claude_pin_and_fails_closed(tmp_path: Path) -> None:
    env = _homes(tmp_path)
    # effortLevel is Claude Code's pinned reasoning dial — the real
    # settings.json on the owner's machine carries exactly this pair.
    (Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "settings.json").write_text(
        '{"model": "claude-opus-5", "effortLevel": "high"}', encoding="utf-8"
    )
    (Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "config.toml").write_text(
        "not toml at all [", encoding="utf-8"
    )
    claude, codex = read_tool_defaults(env)
    assert claude == ToolDefault("claude_code", "claude-opus-5", "high")
    # Malformed config is an absent pin, never a crash.
    assert codex == ToolDefault("codex", None, None)


def _artifact() -> object:
    return parse_model_artifact({
        "schema": "practicegraph.model-intelligence/1",
        "artifact_version": "models-test",
        "published_on": "2026-08-01",
        "source": {
            "name": "Artificial Analysis",
            "results_url": "https://artificialanalysis.ai/agents/coding-agents",
            "methodology_url": (
                "https://artificialanalysis.ai/methodology/coding-agents-benchmarking"
            ),
            "attribution": "Coding-agent benchmark data: Artificial Analysis",
        },
        "benchmark": {
            "name": "Artificial Analysis Coding Agent Index",
            "version": "2026-08",
            "token_unit": "average_total_tokens_per_task",
        },
        "variants": [
            {"id": "opus-high", "tool": "claude_code", "model": "Claude Opus 5",
             "effort": "high", "index_tenths": 905,
             "total_tokens_per_task": 9_500_000},
            {"id": "sonnet-medium", "tool": "claude_code", "model": "Claude Sonnet 5",
             "effort": "medium", "index_tenths": 830,
             "total_tokens_per_task": 3_200_000},
            {"id": "haiku-low", "tool": "claude_code", "model": "Claude Haiku 4.5",
             "effort": "low", "index_tenths": 700,
             "total_tokens_per_task": 900_000},
            {"id": "sol-high", "tool": "codex", "model": "GPT-5.6 Sol",
             "effort": "high", "index_tenths": 898,
             "total_tokens_per_task": 8_800_000},
            {"id": "terra-medium", "tool": "codex", "model": "GPT-5.6 Terra",
             "effort": "medium", "index_tenths": 820,
             "total_tokens_per_task": 2_900_000},
        ],
    })


def test_the_artifact_judges_the_pin() -> None:
    artifact = _artifact()
    assert artifact is not None
    usage = {"codex": {"gpt-5.6-sol": 40}, "claude_code": {"claude-opus-5": 60}}
    defaults = {
        "codex": ("gpt-5.6-sol", "xhigh"),
        "claude_code": (None, None),
    }
    recs = build_model_recommendations(
        artifact, usage, date(2026, 8, 21), defaults
    )
    by_tool = {rec.tool: rec for rec in recs}
    # The Codex pin is the strongest cast, said by the artifact.
    assert by_tool["codex"].default_model == "gpt-5.6-sol"
    assert by_tool["codex"].default_role == "strongest"
    assert by_tool["codex"].default_matched is True
    # No Claude pin: nothing judged, nothing guessed.
    assert by_tool["claude_code"].default_model is None
    assert by_tool["claude_code"].default_role is None
    assert by_tool["claude_code"].default_matched is False
