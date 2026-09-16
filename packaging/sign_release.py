"""Mint and sign the release manifest that clients verify before offering an
update.

Run this after building the MSI, then attach BOTH files to the GitHub release:

    uv run python packaging/sign_release.py \\
        --msi dist/PracticeGraph-0.1.11.msi \\
        --version 0.1.11 \\
        --key <path to the offline private key>

  # first time only, on an offline machine:
    uv run python packaging/sign_release.py --new-key release-signing.key

**The private key never lives in this repository.** It is the same class of
secret as the licensing key: generated once, kept offline, and used only to
sign. Anyone holding it can tell every installed client which binary to trust.
The matching public key is compiled into the client
(`analysis/update.py: UPDATE_PUBLIC_KEY_HEX`), which is what makes a repointed
update URL useless to an attacker.

The signature covers the version, the asset URL, its SHA-256 and its size — so
a manifest cannot be edited to point at a different file, and a client can
check what it downloaded against what was signed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from practicegraph import _ed25519
from practicegraph.analysis.update import (
    ALLOWED_ASSET_PREFIXES,
    MANIFEST_VERSION,
    PLATFORM_INSTALLERS,
    RELEASE_REPO,
    canonical_body,
    version_tuple,
)

RELEASE_BASE = ALLOWED_ASSET_PREFIXES[0]
NOTES_BASE = f"https://github.com/{RELEASE_REPO}/releases/tag/"


# --- signing -----------------------------------------------------------------
# The SHIPPED client is verify-only on purpose: `practicegraph._ed25519` has no
# `sign`, so a compromised install cannot mint a manifest that other installs
# would trust. The signing half lives here, in a tool that only ever runs on
# the release machine, and borrows the curve arithmetic from the same module so
# the two halves can never disagree about the curve.


def _clamp(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8          # clear the low 3 bits, clear bit 255
    scalar |= 1 << 254                # set bit 254
    return scalar, digest[32:]


def _compress(point: tuple[int, int]) -> bytes:
    x, y = point
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _public_key(seed: bytes) -> bytes:
    scalar, _prefix = _clamp(seed)
    return _compress(_ed25519._scalar_mult(_ed25519._B, scalar))


def _sign(seed: bytes, message: bytes) -> bytes:
    if len(seed) != 32:
        raise SystemExit("signing key must be 32 bytes of hex")
    scalar, prefix = _clamp(seed)
    public = _compress(_ed25519._scalar_mult(_ed25519._B, scalar))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little")
    r %= _ed25519._L
    big_r = _compress(_ed25519._scalar_mult(_ed25519._B, r))
    k = int.from_bytes(
        hashlib.sha512(big_r + public + message).digest(), "little"
    ) % _ed25519._L
    s_val = (r + k * scalar) % _ed25519._L
    return big_r + s_val.to_bytes(32, "little")



def new_key(path: Path) -> None:
    """Generate an Ed25519 signing key. Offline machine, once, then guard it."""
    if path.exists():
        raise SystemExit(f"refusing to overwrite an existing key: {path}")
    seed = secrets.token_bytes(32)
    public = _public_key(seed)
    path.write_text(seed.hex(), encoding="utf-8")
    print(f"private key written to {path} - keep this offline, back it up once")
    print()
    print("Compile this into the client (analysis/update.py):")
    print(f'    UPDATE_PUBLIC_KEY_HEX = "{public.hex()}"')


def _platform_for(installer: Path) -> str:
    """Which platform an installer is for, from its extension. Refuses anything
    the client would not accept, so a mistake surfaces here rather than as a
    silently ignored asset on an already-published release."""
    suffix = installer.suffix.lower()
    for platform, allowed in PLATFORM_INSTALLERS.items():
        if suffix in allowed:
            return platform
    known = sorted({s for a in PLATFORM_INSTALLERS.values() for s in a})
    raise SystemExit(
        f"{installer.name}: {suffix} is not an installer any client accepts "
        f"(expected one of {known})"
    )


def build(
    installers: list[Path], version: str, key_path: Path, today: date
) -> dict[str, object]:
    if version_tuple(version) is None:
        raise SystemExit(f"version must be MAJOR.MINOR.PATCH, got {version!r}")
    if not installers:
        raise SystemExit("need at least one --installer")

    assets: dict[str, dict[str, object]] = {}
    for installer in installers:
        if not installer.is_file():
            raise SystemExit(f"no such installer: {installer}")
        platform = _platform_for(installer)
        if platform in assets:
            raise SystemExit(f"two installers for {platform}; pass one each")
        payload = installer.read_bytes()
        assets[platform] = {
            "url": f"{RELEASE_BASE}v{version}/{installer.name}",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }

    manifest: dict[str, object] = {
        "update_version": MANIFEST_VERSION,
        "version": version,
        "published": today.isoformat(),
        "notes_url": f"{NOTES_BASE}v{version}",
        "assets": assets,
    }
    seed = bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
    manifest["signature"] = base64.b64encode(
        _sign(seed, canonical_body(manifest))
    ).decode("ascii")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-key", type=Path)
    parser.add_argument(
        "--installer", type=Path, action="append", default=[],
        help="an installer to publish; repeat once per platform (.msi/.dmg/.pkg)",
    )
    parser.add_argument("--version")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--out", type=Path, default=Path("dist/update.json"))
    parser.add_argument("--today", default=date.today().isoformat())
    args = parser.parse_args()

    if args.new_key is not None:
        new_key(args.new_key)
        return
    if not (args.installer and args.version and args.key):
        raise SystemExit("need --installer (repeatable), --version and --key")

    manifest = build(
        args.installer, args.version, args.key, date.fromisoformat(args.today)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    assets = manifest["assets"]
    assert isinstance(assets, dict)
    print(f"wrote {args.out}  (version {manifest['version']})")
    for platform in sorted(assets):
        name = str(assets[platform]["url"]).rsplit("/", 1)[-1]
        print(f"  {platform:8} {name}  sha256 {assets[platform]['sha256'][:16]}...")
    missing = sorted(set(PLATFORM_INSTALLERS) - set(assets))
    if missing:
        print(f"  no build for {', '.join(missing)} - those clients are offered "
              "nothing, which is an ordinary outcome")
    print()
    print(f"Attach every installer AND {args.out.name} to the release, tagged "
          f"v{manifest['version']}.")


if __name__ == "__main__":
    main()
