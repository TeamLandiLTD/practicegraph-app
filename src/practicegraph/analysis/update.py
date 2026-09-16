"""Is there a newer build, and can we prove it is ours?

An update channel is, by construction, a remote-code-execution channel: it
tells a machine which binary to trust. This one is deliberately the smallest
thing that can be useful, and it verifies more than it needs to.

**Where the binaries live.** GitHub Releases. Versioned, CDN-backed, free, and
nothing to operate. The manifest sits at the release's stable
``/releases/latest/download/update.json`` URL, so the client needs no API token
and no rate-limited API call.

**Why a signature on top of HTTPS.** The 2026-07-19 security review found a
CRITICAL on this product: ``C:\\ProgramData\\PracticeGraph`` is world-readable
and its config is writable by a local user, while the agent runs as
LocalSystem. If the update URL were merely config-driven over TLS, any local
user could repoint it and hand LocalSystem a binary of their choosing — a
straight privilege escalation, delivered by our own updater. So the manifest is
signed with the same offline Ed25519 key the licensing path uses, and the
verifying key is compiled into the build. Repointing the URL then buys an
attacker nothing: the manifest fails verification and the check reports
nothing. TLS protects the transport; the signature protects the *decision*.

**Fails closed, everywhere.** No key configured, bad signature, unknown field,
malformed version, a "newer" version that is actually older — every one of
those returns None and the surface stays silent. An update prompt that can be
forged is worse than no update prompt.

**It never installs anything.** The check produces a sentence and a link. The
person downloads and runs the installer themselves. Auto-installation on
Windows means elevation, service restart and rollback, and it is not something
to bolt onto a channel whose publisher certificate is still self-signed.

Determinism (INV-6): pure parsing and integer comparison; `current` is always
passed in. Local-only (NFR-PRV-6) — nothing here rides an emit.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sys
from dataclasses import dataclass

from practicegraph import _ed25519

# Set at release-signing time, like LICENSE_PUBLIC_KEY_HEX. Empty means this
# build trusts no manifest at all, which is the correct default: an unsigned
# build must not be steerable by anything it downloads.
UPDATE_PUBLIC_KEY_HEX = ""

# The closed manifest schema. Anything else present, absent, or mistyped and
# the whole document is rejected — there is no partial acceptance.
#
# ONE document covers every platform: `assets` is keyed by `sys.platform`, the
# whole thing is signed once, and each client picks its own entry. The
# alternative - a manifest per platform - means two signing operations per
# release and two chances to publish a mismatched pair.
_SIGNED_KEYS = ("assets", "notes_url", "published", "version")
_ALLOWED_KEYS = frozenset((*_SIGNED_KEYS, "signature", "update_version"))
_ASSET_KEYS = frozenset(("bytes", "sha256", "url"))

MANIFEST_VERSION = "2"
MAX_MANIFEST_BYTES = 4096
# A release asset that is not roughly an MSI is not one we will point a person
# at. Bounds are generous; they exist to make a nonsense value visible.
MIN_ASSET_BYTES = 1_000_000
MAX_ASSET_BYTES = 500_000_000

_VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The public distribution repo: releases and public artifacts, no source. One
# constant, because the host is pinned in three places that must never drift
# apart (asset prefix, manifest URL, release notes).
RELEASE_REPO = "TeamLandiLTD/practicegraph-app"

# The release host is pinned in the BINARY, not in config — see the module
# docstring. A manifest may only point at assets on our own releases, so a
# signed-but-hostile manifest still cannot redirect a download elsewhere.
ALLOWED_ASSET_PREFIXES: tuple[str, ...] = (
    f"https://github.com/{RELEASE_REPO}/releases/download/",
)

# What an installer for each platform is allowed to be. A manifest cannot hand
# a Mac an .msi or a PC a .dmg, and it cannot hand either one a .exe or a
# script: the extension is checked against the platform that will run it.
PLATFORM_INSTALLERS: dict[str, tuple[str, ...]] = {
    "win32": (".msi",),
    "darwin": (".dmg", ".pkg"),
}


def current_platform() -> str:
    """The `sys.platform` key this build looks for in a manifest."""
    return sys.platform

UPDATE_COPY: dict[str, str] = {
    "label": "Update available",
    "line": "Version {version} is out (you are on {current}), published {published}.",
    "action": "Download the installer",
    "note": (
        "Downloaded and installed by you - this app never installs anything on "
        "its own. The manifest is signature-checked before this line appears."
    ),
}


@dataclass(frozen=True, slots=True)
class UpdateOffer:
    """A verified, strictly-newer release. Never constructed otherwise."""

    version: str
    published: str
    url: str
    sha256: str
    size_bytes: int
    notes_url: str


def update_public_key() -> bytes | None:
    """The pinned verification key, or None when this build has none."""
    if not UPDATE_PUBLIC_KEY_HEX:
        return None
    try:
        key = bytes.fromhex(UPDATE_PUBLIC_KEY_HEX)
    except ValueError:
        return None
    return key if len(key) == 32 else None


def version_tuple(value: object) -> tuple[int, int, int] | None:
    """Strict MAJOR.MINOR.PATCH, or None. Deliberately not a general semver
    parser: this compares OUR versions, and anything we did not publish should
    fail rather than be guessed at."""
    if not isinstance(value, str):
        return None
    matched = _VERSION_RE.match(value)
    if matched is None:
        return None
    return tuple(int(part) for part in matched.groups())  # type: ignore[return-value]


def canonical_body(fields: dict[str, object]) -> bytes:
    """The exact bytes the signature covers: every signed field, sorted keys,
    tight separators. The release signer produces these identically."""
    body = {key: fields[key] for key in _SIGNED_KEYS}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_update_manifest(
    raw: object, public_key: bytes, current: str, platform: str | None = None
) -> UpdateOffer | None:
    """Return the offer only when the manifest is well-formed, correctly
    signed, carries an installer for THIS platform on a pinned release host,
    and is strictly newer than `current`. Any deviation returns None."""
    if not isinstance(raw, dict):
        return None
    if set(raw) != _ALLOWED_KEYS:
        return None
    if raw.get("update_version") != MANIFEST_VERSION:
        return None

    offered = version_tuple(raw.get("version"))
    running = version_tuple(current)
    if offered is None or running is None:
        return None

    published = raw.get("published")
    if not isinstance(published, str) or _DATE_RE.match(published) is None:
        return None
    notes_url = raw.get("notes_url")
    if not isinstance(notes_url, str) or not notes_url.startswith("https://"):
        return None

    # Every asset in the document is validated, not just this platform's. A
    # manifest that is malformed for a Mac is not a manifest we will act on
    # from Windows either: one bad entry means the release was published
    # wrong, and acting on half of it is how you ship a broken update to the
    # other half of your users.
    assets = raw.get("assets")
    if not isinstance(assets, dict) or not assets:
        return None
    for key, asset in assets.items():
        if key not in PLATFORM_INSTALLERS:
            return None
        if not isinstance(asset, dict) or set(asset) != _ASSET_KEYS:
            return None
        sha256 = asset.get("sha256")
        if not isinstance(sha256, str) or _SHA256_RE.match(sha256) is None:
            return None
        size = asset.get("bytes")
        if type(size) is not int or not MIN_ASSET_BYTES <= size <= MAX_ASSET_BYTES:
            return None
        url = asset.get("url")
        if not isinstance(url, str) or not url.startswith(ALLOWED_ASSET_PREFIXES):
            return None
        if not url.endswith(PLATFORM_INSTALLERS[key]):
            return None

    signature_b64 = raw.get("signature")
    if not isinstance(signature_b64, str) or not 0 < len(signature_b64) <= 128:
        return None
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(signature) != 64:
        return None
    if not _ed25519.verify(public_key, canonical_body(raw), signature):
        return None

    # Signature verified — only now does anything else matter. A correctly
    # signed OLDER manifest is not an update; accepting one would let a
    # stale-but-valid document walk a machine backwards.
    if offered <= running:
        return None

    # No build for this platform in this release is an ordinary outcome, not a
    # failure: a Windows-only hotfix simply offers a Mac nothing.
    mine = assets.get(platform or current_platform())
    if mine is None:
        return None

    return UpdateOffer(
        version=str(raw["version"]),
        published=published,
        url=str(mine["url"]),
        sha256=str(mine["sha256"]),
        size_bytes=int(mine["bytes"]),
        notes_url=notes_url,
    )
