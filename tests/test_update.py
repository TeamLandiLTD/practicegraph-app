"""The update channel: it decides which binary a machine will trust.

That makes it the highest-consequence code in the product, so these tests are
mostly about what it REFUSES. The threat that shapes the design is local, not
remote: the 2026-07-19 review found ``C:\\ProgramData\\PracticeGraph``
world-readable with a locally-writable config, while the agent runs as
LocalSystem. An update channel that trusted its configured URL would hand any
local user a privilege escalation. So the manifest is signed, the verifying key
is compiled in, and the asset host is pinned in the binary too.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from practicegraph.analysis.update import (
    ALLOWED_ASSET_PREFIXES,
    MANIFEST_VERSION,
    canonical_body,
    parse_update_manifest,
    update_public_key,
    version_tuple,
)

# A throwaway keypair, generated here so the suite never needs the real one.
SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
PUBLIC = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
)


def _sign(seed: bytes, message: bytes) -> bytes:
    """The release signer's algorithm, inlined so the test proves the CLIENT
    accepts what the tool produces without importing a packaging script."""
    from practicegraph import _ed25519

    digest = hashlib.sha512(seed).digest()
    scalar = (int.from_bytes(digest[:32], "little") & ((1 << 254) - 8)) | (1 << 254)
    prefix = digest[32:]

    def compress(point: tuple[int, int]) -> bytes:
        x, y = point
        return (y | ((x & 1) << 255)).to_bytes(32, "little")

    public = compress(_ed25519._scalar_mult(_ed25519._B, scalar))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little")
    r %= _ed25519._L
    big_r = compress(_ed25519._scalar_mult(_ed25519._B, r))
    k = int.from_bytes(
        hashlib.sha512(big_r + public + message).digest(), "little"
    ) % _ed25519._L
    return big_r + ((r + k * scalar) % _ed25519._L).to_bytes(32, "little")


BASE = f"{ALLOWED_ASSET_PREFIXES[0]}v0.2.0/"


def asset(**over: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "url": f"{BASE}PracticeGraph-0.2.0.msi",
        "sha256": "a" * 64,
        "bytes": 12_000_000,
    }
    entry.update(over)
    return entry


def manifest(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "update_version": MANIFEST_VERSION,
        "version": "0.2.0",
        "published": "2026-07-26",
        "notes_url": "https://github.com/TeamLandiLTD/practicegraph/releases/tag/v0.2.0",
        "assets": {"win32": asset()},
    }
    body.update(over)
    body["signature"] = base64.b64encode(
        _sign(SEED, canonical_body(body))
    ).decode("ascii")
    return body


def parse(doc: dict[str, object], current: str = "0.1.10", platform: str = "win32"):
    return parse_update_manifest(doc, PUBLIC, current, platform=platform)


def test_a_correctly_signed_newer_release_is_offered() -> None:
    offer = parse(manifest())
    assert offer is not None
    assert offer.version == "0.2.0"
    assert offer.url.endswith("PracticeGraph-0.2.0.msi")
    assert offer.sha256 == "a" * 64


def test_a_tampered_field_is_refused_even_though_the_url_is_ours() -> None:
    """The whole point of signing. An attacker who can rewrite the served
    document — or the world-readable cache file — changes one field and the
    signature stops matching."""
    for field, value in (
        ("version", "9.9.9"),
        ("published", "2026-07-27"),
        ("assets", {"win32": asset(sha256="b" * 64)}),
        ("notes_url", "https://github.com/TeamLandiLTD/practicegraph/releases"),
    ):
        forged = manifest()
        forged[field] = value  # signature now covers the OLD value
        assert parse(forged) is None, field


def test_a_signature_from_another_key_is_refused() -> None:
    other_seed = bytes.fromhex("11" * 32)
    forged = manifest()
    forged["signature"] = base64.b64encode(
        _sign(other_seed, canonical_body(forged))
    ).decode("ascii")
    assert parse(forged) is None


def test_the_asset_host_is_pinned_in_the_binary() -> None:
    """Even a VALIDLY signed manifest cannot send a download elsewhere. If the
    signing key ever leaks, the blast radius is still limited to files hosted
    on our own releases."""
    for hostile in (
        "https://evil.example/PracticeGraph-0.2.0.msi",
        "https://github.com/someoneelse/practicegraph/releases/download/v1/x.msi",
        "https://raw.githubusercontent.com/TeamLandiLTD/practicegraph/x.msi",
        "http://github.com/TeamLandiLTD/practicegraph/releases/download/v1/x.msi",
    ):
        assert parse(manifest(assets={"win32": asset(url=hostile)})) is None, hostile
    # ...and it must actually be an installer.
    assert parse(manifest(assets={"win32": asset(url=f"{BASE}setup.exe")})) is None


def test_a_signed_older_release_never_walks_a_machine_backwards() -> None:
    """A correctly signed OLD manifest is a replay, not an update. Serving one
    is the cheapest downgrade attack there is: re-publish last month's document
    and every client offers to install a version whose bugs you know."""
    assert parse(manifest(version="0.1.9")) is None
    assert parse(manifest(version="0.1.10")) is None
    assert parse(manifest(version="0.1.11")) is not None


def test_version_comparison_is_numeric_not_lexicographic() -> None:
    """0.1.10 is NEWER than 0.1.9. A string compare says the opposite, and
    would have stranded every install at 0.1.9 forever."""
    assert version_tuple("0.1.10") > version_tuple("0.1.9")  # type: ignore[operator]
    assert parse(manifest(version="0.1.10"), current="0.1.9") is not None
    for junk in ("1.0", "v1.0.0", "1.0.0-rc1", "", "99999.0.0", None, 1):
        assert version_tuple(junk) is None, junk


def test_an_unknown_or_missing_field_rejects_the_whole_document() -> None:
    extra = manifest()
    extra["auto_install"] = True
    assert parse(extra) is None
    for missing in ("version", "assets", "signature", "published", "notes_url"):
        short = manifest()
        del short[missing]
        assert parse(short) is None, missing


def test_implausible_sizes_and_shapes_are_refused() -> None:
    for size in (0, 1, 999_999, 500_000_001, "12000000", True):
        assert parse(manifest(assets={"win32": asset(bytes=size)})) is None
    for sha in ("A" * 64, "a" * 63, "z" * 64, ""):
        assert parse(manifest(assets={"win32": asset(sha256=sha)})) is None
    for published in ("26-07-2026", "2026-7-26", "tomorrow", ""):
        assert parse(manifest(published=published)) is None


def test_a_build_with_no_key_trusts_nothing() -> None:
    """Fails closed. An unsigned build must not be steerable by anything it
    downloads, so the absence of a key is a hard stop rather than a bypass."""
    assert update_public_key() is None  # no key compiled into the test build


def test_the_release_tool_and_the_client_agree() -> None:
    """End to end against the real signer: what packaging/sign_release.py
    produces is exactly what the client accepts. These two drifting apart
    would mean shipping a release nobody can install."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "sign_release", root / "packaging" / "sign_release.py"
    )
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    # The tool derives the same public key the client will verify against.
    assert tool._public_key(SEED) == PUBLIC

    msi = root / "tests" / "goldens" / ".update-fixture.msi"
    msi.write_bytes(b"MSI\x00" + b"x" * 2_000_000)
    key = msi.with_suffix(".key")
    key.write_text(SEED.hex(), encoding="utf-8")
    try:
        from datetime import date

        built = tool.build([msi], "0.2.0", key, date(2026, 7, 26))
        win = built["assets"]["win32"]
        assert win["sha256"] == hashlib.sha256(msi.read_bytes()).hexdigest()
        offer = parse(built)
        assert offer is not None
        assert offer.size_bytes == msi.stat().st_size
        # And it round-trips through JSON exactly as it will be served.
        served = json.loads(json.dumps(built))
        assert parse(served) == offer
    finally:
        msi.unlink(missing_ok=True)
        key.unlink(missing_ok=True)


@pytest.mark.parametrize("raw", [None, [], "x", 0, {"version": "0.2.0"}])
def test_garbage_is_never_an_update(raw: object) -> None:
    assert parse_update_manifest(raw, PUBLIC, "0.1.10", platform="win32") is None


def test_one_signed_document_serves_every_platform() -> None:
    """A manifest per platform means two signing operations per release and two
    chances to publish a mismatched pair. One document, keyed by sys.platform,
    signed once - each client picks its own entry."""
    both = manifest(assets={
        "win32": asset(),
        "darwin": asset(url=f"{BASE}PracticeGraph-0.2.0.dmg",
                        sha256="c" * 64, bytes=30_000_000),
    })
    windows = parse(both, platform="win32")
    mac = parse(both, platform="darwin")
    assert windows is not None and windows.url.endswith(".msi")
    assert mac is not None and mac.url.endswith(".dmg")
    assert mac.size_bytes == 30_000_000
    # Same document, same signature, two different answers.
    assert windows.version == mac.version == "0.2.0"


def test_a_platform_is_never_handed_another_platform_s_installer() -> None:
    """The extension is checked against the platform that will RUN it, so a
    signed-but-wrong manifest cannot tell a Mac to install an .msi. Without
    this, one mistake in the release script is an unusable update for a whole
    platform - and the manifest verifies, so nothing looks wrong."""
    crossed = manifest(assets={"darwin": asset()})  # an .msi under darwin
    assert parse(crossed, platform="darwin") is None
    swapped = manifest(assets={
        "win32": asset(url=f"{BASE}PracticeGraph-0.2.0.dmg"),
    })
    assert parse(swapped, platform="win32") is None


def test_no_build_for_this_platform_is_an_ordinary_outcome() -> None:
    """A Windows-only hotfix offers a Mac nothing. That is not a failure and
    must not read like one - the Mac simply stays where it is."""
    win_only = manifest(assets={"win32": asset()})
    assert parse(win_only, platform="darwin") is None
    assert parse(win_only, platform="win32") is not None


def test_a_malformed_entry_for_ANOTHER_platform_still_sinks_the_document() -> None:
    """Deliberately strict. A manifest that is broken for a Mac is not one we
    will act on from Windows either: one bad entry means the release was
    published wrong, and acting on half of it is how the other half of your
    users get a broken update."""
    half_bad = manifest(assets={
        "win32": asset(),
        "darwin": asset(url="https://evil.example/x.dmg", sha256="c" * 64),
    })
    assert parse(half_bad, platform="win32") is None


def test_an_unknown_platform_key_rejects_the_document() -> None:
    """The platform table is closed, like every other vocabulary here."""
    assert parse(manifest(assets={"linux": asset()})) is None
    assert parse(manifest(assets={})) is None


def test_the_release_repo_is_the_public_app_repo() -> None:
    """Internal builds come from this repo; releases are published from the
    public app repo, and the client only trusts assets hosted there."""
    from practicegraph.analysis.update import ALLOWED_ASSET_PREFIXES, RELEASE_REPO
    from practicegraph.config import DEFAULT_UPDATE_SOURCE_URL

    assert RELEASE_REPO == "TeamLandiLTD/practicegraph-app"
    assert (f"https://github.com/{RELEASE_REPO}/releases/download/",) == ALLOWED_ASSET_PREFIXES
    assert (
        f"https://github.com/{RELEASE_REPO}/releases/latest/download/update.json"
    ) == DEFAULT_UPDATE_SOURCE_URL
