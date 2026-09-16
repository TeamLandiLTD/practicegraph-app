"""Validate and inventory approved website catalogs, with a private release archive.

No research, approval, Git commit, network request, or deployment happens here.
Existing curator commands prepare one channel; this is the final release gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from practicegraph.catalog_crypto import MAX_ENVELOPE_BYTES, open_catalog
from practicegraph.content import (
    CHANNELS,
    MAX_CONTENT_BYTES,
    canonical_bytes,
    edition_date,
    edition_version,
)

MANIFEST = "catalog-manifest.json"
TERMS = "CONTENT_LICENSE.txt"
SCHEMA = "practicegraph.content-manifest/1"
_NON_CONTENT_JSON = {MANIFEST, "vercel.json"}



def content_limit(channel: str) -> int:
    """The token-price matrix is the one channel allowed past the shared bound."""
    from practicegraph.analysis.token_prices import MAX_FEED_BYTES

    return MAX_FEED_BYTES if channel == "token-prices" else MAX_CONTENT_BYTES

def validate_draft(
    channel: str, draft: Path, previous: Path | None = None, *, today: date | None = None,
) -> dict[str, Any]:
    """Read-only review of one edition using the exact production contract."""
    if channel not in CHANNELS:
        raise ValueError("unknown content channel")

    def load(path: Path) -> tuple[dict[str, Any], bytes]:
        with path.open("rb") as stream:
            raw = stream.read(MAX_ENVELOPE_BYTES + 1)
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise ValueError(f"{channel}: artifact is too large")
        document = open_catalog(json.loads(raw), channel)
        if len(canonical_bytes(document)) > content_limit(channel):
            raise ValueError(f"{channel}: artifact is too large")
        edition_version(channel, document)
        assert isinstance(document, dict)
        return document, raw

    document, raw = load(draft)
    version = edition_version(channel, document)
    reviewed = edition_date(channel, document)
    today = today or datetime.now(UTC).date()
    if reviewed and date.fromisoformat(reviewed) > today:
        raise ValueError(f"{channel}: edition date is in the future")
    if previous is not None:
        prior, _ = load(previous)
        if document != prior and version == edition_version(channel, prior):
            raise ValueError(f"{channel}: changed content requires a new version")
    if channel == "harness-playbooks":
        from tools.harness_playbooks import validate

        validate(draft, previous, today=today)
    if channel == "token-prices" and previous is not None:
        from practicegraph.analysis.token_prices import check_transition

        check_transition(document, prior)
    if channel == "training":
        prior_entries = {entry["id"]: entry for entry in prior["entries"]} if previous else {}
        entries = {entry["id"]: entry for entry in document["entries"]}
        if set(prior_entries) - set(entries):
            raise ValueError("training: retain removed entries with retired status")
        for identifier, entry in entries.items():
            old = prior_entries.get(identifier)
            if old and (entry["revision"] < old["revision"] or (
                entry != old and entry["revision"] <= old["revision"]
            )):
                raise ValueError("training: changed entries require a higher revision")
    age = (today - date.fromisoformat(reviewed)).days if reviewed else None
    return {
        "channel": channel, "version": version, "edition_date": reviewed,
        "review_due": age > CHANNELS[channel].review_after_days if age is not None else None,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "counts": {key: len(value) for key, value in document.items()
                   if isinstance(value, (list, dict))},
    }


def inventory(site: Path, *, normalize: bool = False) -> dict[str, object]:
    """A closed list prevents accidentally serving drafts or arbitrary JSON."""
    if not site.is_dir() or not (site / TERMS).is_file():
        raise ValueError("site directory and CONTENT_LICENSE.txt are required")
    allowed = {f"{channel}.json" for channel in CHANNELS} | _NON_CONTENT_JSON
    for path in site.rglob("*.json"):
        relative = path.relative_to(site)
        if any(part in (".git", ".vercel") for part in relative.parts):
            continue
        if path.parent != site or path.name not in allowed:
            raise ValueError(f"unapproved public JSON path: {relative.as_posix()}")
    for name in ("drafts", "candidates", "rejected", "editorial", "research"):
        if (site / name).exists():
            raise ValueError(f"private editorial directory in public site: {name}")
    # A locally valid edition can still be omitted from a static deployment.
    deployment_list = site / ".vercelignore"
    deployed = set(deployment_list.read_text(encoding="utf-8").splitlines()) if (
        deployment_list.is_file()
    ) else None
    if deployed is not None and "/*" in deployed:
        for required in (TERMS, MANIFEST):
            if f"!{required}" not in deployed:
                raise ValueError(f"{required}: missing from the deployment allowlist")
    entries: dict[str, object] = {}
    for channel, contract in CHANNELS.items():
        path = site / f"{channel}.json"
        if not path.exists():
            continue
        if deployed is not None and "/*" in deployed and f"!{path.name}" not in deployed:
            raise ValueError(f"{channel}: catalog is missing from the deployment allowlist")
        if path.is_symlink() or path.resolve().parent != site.resolve():
            raise ValueError(f"{channel}: catalog must be a regular file inside the site")
        if path.stat().st_size > MAX_ENVELOPE_BYTES:
            raise ValueError(f"{channel}: artifact is too large")
        raw = path.read_bytes()
        if normalize:
            raw = raw.replace(b"\r\n", b"\n")
        document = open_catalog(json.loads(raw.decode("utf-8")), channel)
        if len(canonical_bytes(document)) > content_limit(channel):
            raise ValueError(f"{channel}: artifact is too large")
        version = edition_version(channel, document)
        entries[channel] = {
            "path": path.name, "version": version,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "edition_date": edition_date(channel, document),
            "review_after_days": contract.review_after_days,
        }
    if not entries:
        raise ValueError("site contains no validated catalogs")
    return {
        "schema": SCHEMA,
        "publisher": "TeamLandi Ltd",
        "content_license": "TeamLandi-Editorial-1.0",
        "content_license_path": TERMS,
        "content_license_sha256": hashlib.sha256((site / TERMS).read_bytes()).hexdigest(),
        "channels": entries,
    }


def _write(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing a symlink destination: {path.name}")
    descriptor, staging = tempfile.mkstemp(dir=path.parent, prefix=".content-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


def prepare(site: Path, archive: Path) -> dict[str, object]:
    """Validate everything before writing; preserve versioned copies privately.

    The site commit must include the manifest and changed channel files together.
    Version reuse with different bytes is refused, including after a rollback.
    """
    site, archive = site.resolve(), archive.resolve()
    app = Path(__file__).resolve().parents[1]
    for public_root in (app, site):
        if archive == public_root or public_root in archive.parents:
            raise ValueError("release archive must be outside the app and website")
    result = inventory(site, normalize=True)
    channels = result["channels"]
    assert isinstance(channels, dict)
    pending: list[tuple[Path, bytes]] = []
    previous_path = site / MANIFEST
    previous = (json.loads(previous_path.read_text(encoding="utf-8"))
                if previous_path.exists() else {})
    prior_channels = previous.get("channels", {}) if isinstance(previous, dict) else {}
    for channel, entry in channels.items():
        prior = prior_channels.get(channel, {}) if isinstance(prior_channels, dict) else {}
        if (isinstance(prior, dict) and prior.get("version") == entry["version"]
                and prior.get("sha256") != entry["sha256"]):
            raise ValueError(f"{channel}: changed content requires a new version")
        destination = archive / channel / (entry["version"] + ".json")
        if archive not in destination.resolve().parents:
            raise ValueError("archive destination escaped the selected directory")
        raw = (site / entry["path"]).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"{channel}: artifact changed during release preparation")
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError(f"{channel}: this version is already archived with different content")
        pending.append((destination, raw))
    for destination, raw in pending:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            _write(destination, raw)
    # Stable LF bytes match both Windows checkouts and the hosted Git checkout.
    for channel, entry in channels.items():
        target = site / entry["path"]
        raw = (archive / channel / (entry["version"] + ".json")).read_bytes()
        if target.read_bytes() != raw:
            _write(target, raw)
    _write(site / MANIFEST, canonical_bytes(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "prepare", "check"))
    parser.add_argument("--site", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--channel", choices=tuple(CHANNELS))
    parser.add_argument("--draft", type=Path)
    parser.add_argument("--against", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            if args.channel is None or args.draft is None:
                raise ValueError("validate requires --channel and --draft")
            print(json.dumps(validate_draft(args.channel, args.draft, args.against), indent=2))
            return 0
        if args.site is None:
            raise ValueError("prepare and check require --site")
        if args.command == "prepare":
            if args.archive is None:
                raise ValueError("prepare requires an explicit private --archive directory")
            result = prepare(args.site, args.archive)
        else:
            result = inventory(args.site)
            actual = json.loads((args.site / MANIFEST).read_text(encoding="utf-8"))
            if actual != result:
                raise ValueError("manifest does not match the site; run prepare before delivery")
        channels = result["channels"]
        assert isinstance(channels, dict)
        print(f"Validated {len(channels)} channels; {MANIFEST} matches the validated files.")
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(f"Content release refused: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
