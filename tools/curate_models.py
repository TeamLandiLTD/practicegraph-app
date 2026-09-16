"""Validate, preview, and explicitly publish curated coding-model guidance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from practicegraph.analysis.model_intelligence import (
    ATTRIBUTION,
    BENCHMARK_NAME,
    EFFORTS,
    METHODOLOGY_URL,
    RESULTS_URL,
    SCHEMA,
    SUPPORTED_TOOLS,
    TOKEN_UNIT,
    ModelArtifact,
    build_model_recommendations,
    model_artifact_to_dict,
    pareto_frontier,
    parse_model_artifact,
)

_VARIANT_KEYS = {
    "id", "tool", "model", "effort", "index_tenths", "total_tokens_per_task",
}
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _derived_version(document: dict[str, object]) -> str:
    content = {key: value for key, value in document.items() if key != "artifact_version"}
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:10]
    benchmark = content.get("benchmark")
    benchmark_version = benchmark.get("version") if isinstance(benchmark, dict) else "unknown"
    published_on = content.get("published_on", "unknown")
    return f"models-aa-coding-{benchmark_version}-{published_on}-{digest}"


def _prepare(raw: object) -> tuple[ModelArtifact | None, list[str]]:
    if not isinstance(raw, dict):
        return None, ["artifact: expected a JSON object"]
    document = dict(raw)
    if "artifact_version" not in document:
        document["artifact_version"] = _derived_version(document)
    artifact = parse_model_artifact(document)
    if artifact is not None:
        return artifact, []
    return None, _validation_errors(document)


def _validation_errors(document: dict[str, object]) -> list[str]:
    errors: list[str] = []
    expected_root = {
        "schema", "artifact_version", "published_on", "source", "benchmark", "variants",
    }
    if set(document) != expected_root:
        errors.append("artifact: root fields must match the closed schema")
    if document.get("schema") != SCHEMA:
        errors.append(f"schema: must equal {SCHEMA}")
    source = document.get("source")
    expected_source = {
        "name": "Artificial Analysis",
        "results_url": RESULTS_URL,
        "methodology_url": METHODOLOGY_URL,
        "attribution": ATTRIBUTION,
    }
    if source != expected_source:
        errors.append("source: must use the fixed Artificial Analysis attribution and URLs")
    benchmark = document.get("benchmark")
    if not isinstance(benchmark, dict):
        errors.append("benchmark: expected an object")
    else:
        if benchmark.get("name") != BENCHMARK_NAME:
            errors.append(f"benchmark.name: must equal {BENCHMARK_NAME}")
        if benchmark.get("token_unit") != TOKEN_UNIT:
            errors.append(f"benchmark.token_unit: must equal {TOKEN_UNIT}")
    variants = document.get("variants")
    if not isinstance(variants, list):
        errors.append("variants: expected an array")
    else:
        if not 2 <= len(variants) <= 24:
            errors.append("variants: expected 2 through 24 rows")
        ids: set[str] = set()
        tools: set[str] = set()
        for index, row in enumerate(variants):
            prefix = f"variants[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{prefix}: expected an object")
                continue
            if set(row) != _VARIANT_KEYS:
                errors.append(f"{prefix}: fields must match the closed variant schema")
            row_id = row.get("id")
            if not isinstance(row_id, str) or _SLUG.fullmatch(row_id) is None:
                errors.append(f"{prefix}.id: expected a lowercase slug")
            elif row_id in ids:
                errors.append(f"{prefix}.id: duplicate id {row_id}")
            else:
                ids.add(row_id)
            tool = row.get("tool")
            if tool not in SUPPORTED_TOOLS:
                errors.append(f"{prefix}.tool: expected codex or claude_code")
            else:
                tools.add(str(tool))
            if row.get("effort") not in EFFORTS:
                errors.append(f"{prefix}.effort: unsupported effort")
            score = row.get("index_tenths")
            if type(score) is not int or not 0 <= score <= 1000:
                errors.append(f"{prefix}.index_tenths: expected integer 0 through 1000")
            tokens = row.get("total_tokens_per_task")
            if type(tokens) is not int or not 1 <= tokens <= 100_000_000:
                errors.append(
                    f"{prefix}.total_tokens_per_task: expected integer 1 through 100000000"
                )
        if tools != SUPPORTED_TOOLS:
            errors.append("variants: at least one codex and one claude_code row are required")
    return errors or ["artifact: failed closed production validation"]


def _load_valid(path: Path) -> tuple[ModelArtifact | None, list[str]]:
    try:
        raw = _read_json(path)
    except OSError as error:
        return None, [f"draft: could not read file ({error.__class__.__name__})"]
    except (ValueError, UnicodeDecodeError):
        return None, ["draft: invalid UTF-8 JSON"]
    return _prepare(raw)


def _preview(artifact: ModelArtifact) -> dict[str, object]:
    usage = {tool: {"curator-preview": 1} for tool in SUPPORTED_TOOLS}
    recommendations = {
        item.tool: {
            "balanced": item.variant_id,
            **{alternative.kind: alternative.variant_id for alternative in item.alternatives},
        }
        for item in build_model_recommendations(artifact, usage, artifact.published_on)
    }
    return {
        "artifact_version": artifact.artifact_version,
        "published_on": artifact.published_on.isoformat(),
        "frontiers": {
            tool: [row.id for row in pareto_frontier(artifact.variants, tool)]
            for tool in sorted(SUPPORTED_TOOLS)
        },
        "recommendations": recommendations,
    }


def _publish(artifact: ModelArtifact, site: Path) -> Path:
    if not site.is_dir():
        raise OSError("site path must be an existing directory")
    target = site / "models.json"
    handle, staging_name = tempfile.mkstemp(prefix=".models-", suffix=".tmp", dir=site)
    staging = Path(staging_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(model_artifact_to_dict(artifact), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "preview"):
        command = commands.add_parser(name)
        command.add_argument("draft", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("draft", type=Path)
    publish.add_argument("--site", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifact, errors = _load_valid(args.draft)
    if artifact is None:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if args.command == "validate":
        print(json.dumps({"artifact_version": artifact.artifact_version, "valid": True}))
        return 0
    if args.command == "preview":
        print(json.dumps(_preview(artifact), indent=2, sort_keys=True))
        return 0
    try:
        target = _publish(artifact, args.site)
    except OSError as error:
        print(f"publish: {error}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
