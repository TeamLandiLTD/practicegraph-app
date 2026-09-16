"""Validate, preview, and explicitly publish the curated Advisor artifact.

The Advisor artifact (docs/ADVISOR_PLAN.md Phase 2) carries market rows
with individual source citations plus human-written verdict cards. Same
curation discipline as models/news: a human drafts, this tool validates
against the closed production parser, previews what would render, and only
`publish --site` writes the file the public pull serves. Attribution is
enforced by the schema itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from practicegraph.analysis.advisor_market import (
    ATTRIBUTION,
    LEGACY_SCHEMA,
    SCHEMA,
    SOURCE_NAME,
    SOURCE_URL,
    VERDICT_TIERS,
    AdvisorArtifact,
    advisor_artifact_to_dict,
    market_stale,
    metric_current,
    model_metrics,
    parse_advisor_artifact,
)
from practicegraph.content import MAX_CONTENT_BYTES


def _read_json(path: Path) -> object:
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONTENT_BYTES + 1)
    if len(raw) > MAX_CONTENT_BYTES:
        raise ValueError("oversized artifact")
    return json.loads(raw)


def _derived_version(document: dict[str, object]) -> str:
    content = {key: value for key, value in document.items() if key != "artifact_version"}
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:10]
    as_of = content.get("as_of", "unknown")
    return f"advisor-{as_of}-{digest}"


def _prepare(raw: object) -> tuple[AdvisorArtifact | None, list[str]]:
    if not isinstance(raw, dict):
        return None, ["artifact: expected a JSON object"]
    document = dict(raw)
    if "artifact_version" not in document:
        document["artifact_version"] = _derived_version(document)
    artifact = parse_advisor_artifact(document)
    if artifact is not None:
        return artifact, []
    return None, _validation_errors(document)


def _validation_errors(document: dict[str, object]) -> list[str]:
    """Human-oriented hints for the curator; the parser stays the authority."""
    errors: list[str] = []
    expected_root = {
        "schema", "artifact_version", "as_of", "expires", "source", "models", "verdicts",
    }
    if document.get("schema") == SCHEMA:
        expected_root.remove("source")
    if set(document) != expected_root:
        errors.append("artifact: root fields must match the closed schema")
    if document.get("schema") not in (LEGACY_SCHEMA, SCHEMA):
        errors.append(f"schema: expected {SCHEMA} (legacy {LEGACY_SCHEMA} is also readable)")
    if document.get("schema") == LEGACY_SCHEMA and document.get("source") != {
        "name": SOURCE_NAME,
        "url": SOURCE_URL,
        "attribution": ATTRIBUTION,
    }:
        errors.append("source: must use the fixed Artificial Analysis attribution block")
    if document.get("schema") == SCHEMA:
        errors.append(
            "metrics: each supplied value needs source_name, public HTTPS source_url,"
            " observed_on <= as_of, and a measurement basis; omit unknowns or use null."
            " Each model needs at least one verified metric. Verdict evidence must cite"
            " present, current metrics for its included families."
        )
    models = document.get("models")
    if not isinstance(models, list) or not models:
        errors.append("models: expected a non-empty array of family market rows")
    verdicts = document.get("verdicts")
    if not isinstance(verdicts, list):
        errors.append("verdicts: expected an array (may be empty)")
    else:
        for index, row in enumerate(verdicts):
            if isinstance(row, dict) and row.get("tier") not in VERDICT_TIERS:
                errors.append(
                    f"verdicts[{index}].tier: expected one of {', '.join(VERDICT_TIERS)}"
                )
    errors.append(
        "artifact: failed closed production validation — check copy caps, the"
        " lexicon scan, closed families/tools, dates (expires >= as_of), and"
        " that every verdict family has a market row"
    )
    return errors


def _load_valid(path: Path) -> tuple[AdvisorArtifact | None, list[str]]:
    try:
        raw = _read_json(path)
    except OSError as error:
        return None, [f"draft: could not read file ({error.__class__.__name__})"]
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None, ["draft: invalid UTF-8 JSON"]
    return _prepare(raw)


def _preview(artifact: AdvisorArtifact) -> dict[str, object]:
    document = advisor_artifact_to_dict(artifact)
    return {
        "schema": artifact.schema,
        "artifact_version": artifact.artifact_version,
        "as_of": artifact.as_of.isoformat(),
        "expires": artifact.expires.isoformat(),
        "stale_on_publish": market_stale(artifact, date.today()),
        "models": {
            model.family: {
                "model_id": model.model_id or model.aa_slug,
                "metrics": {
                    name: value.isoformat() if isinstance(value, date) else value
                    for name, value in model_metrics(model).items()
                },
                "unknown_metrics": [name for name, value in model_metrics(model).items()
                                    if value is None],
                "unavailable_today": [name for name in model_metrics(model)
                                      if not metric_current(model, name, date.today())],
            }
            for model in artifact.models
        },
        "verdicts": {
            verdict.verdict_id: {
                "tier": verdict.tier,
                "families": list(verdict.families),
                "tools": list(verdict.tools),
                "expires": verdict.expires.isoformat(),
                "evidence": [{"family": family, "metric": metric}
                             for family, metric in verdict.evidence],
            }
            for verdict in artifact.verdicts
        },
        "document": document,
    }


def _publish(artifact: AdvisorArtifact, site: Path) -> Path:
    if not site.is_dir():
        raise OSError("site path must be an existing directory")
    target = site / "advisor.json"
    handle, staging_name = tempfile.mkstemp(prefix=".advisor-", suffix=".tmp", dir=site)
    staging = Path(staging_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(advisor_artifact_to_dict(artifact), stream, indent=2, sort_keys=True)
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
