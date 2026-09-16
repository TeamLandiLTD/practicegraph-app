"""Vendored Ed25519 signature verification (RFC 8032), verify-only.

Pure Python, stdlib-only (``hashlib.sha512``). The offline license check
(``analysis/license.py``) verifies a vendor-signed token with a pinned public
key; doing it without a native crypto dependency keeps the embeddable runtime
lean and gives one codepath for both build flavors. Signing happens
server-side and is not shipped here.

Correctness is pinned by ``tests/test_ed25519.py``, which cross-checks this
verifier against a reference Ed25519 implementation (real keypairs, real
signatures, tamper rejection). Verification is a couple of scalar
multiplications — performance is irrelevant for a once-per-day check.

The twisted Edwards curve is ``-x^2 + y^2 = 1 + d*x^2*y^2`` over GF(2^255-19).
Affine point arithmetic is used for clarity; malformed input never raises,
it returns False.
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int | None:
    """The x with the given low bit for a curve point at ``y``, or None when
    ``y`` is not a valid coordinate (the point is off the curve)."""
    if y >= _P:
        return None
    xx = ((y * y - 1) * _inv(_D * y * y + 1)) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _SQRT_M1) % _P
    if (x * x - xx) % _P != 0:
        return None  # not a quadratic residue -> invalid encoding
    if x == 0 and sign == 1:
        return None  # -0 is not a valid encoding
    if (x & 1) != sign:
        x = _P - x
    return x


def _add(p: tuple[int, int], q: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p
    x2, y2 = q
    x1x2 = x1 * x2 % _P
    y1y2 = y1 * y2 % _P
    dxy = _D * x1x2 % _P * y1y2 % _P
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + dxy) % _P
    y3 = (y1y2 + x1x2) * _inv(1 - dxy) % _P
    return (x3, y3)


def _scalar_mult(p: tuple[int, int], e: int) -> tuple[int, int]:
    q = (0, 1)  # identity
    while e > 0:
        if e & 1:
            q = _add(q, p)
        p = _add(p, p)
        e >>= 1
    return q


def _base_point() -> tuple[int, int]:
    by = (4 * _inv(5)) % _P
    bx = _recover_x(by, 0)
    if bx is None:  # unreachable: the base point is valid by construction
        raise RuntimeError("ed25519 base point construction failed")
    return (bx, by)


_B = _base_point()


def _decompress(data: bytes) -> tuple[int, int] | None:
    if len(data) != 32:
        return None
    n = int.from_bytes(data, "little")
    sign = (n >> 255) & 1
    y = n & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True iff ``signature`` is a valid Ed25519 signature of ``message``
    under ``public_key`` (both raw bytes: 32-byte key, 64-byte signature).
    Never raises; any malformed input returns False."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = (
        int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
        )
        % _L
    )
    # The group equation [s]B == R + [k]A.
    return _scalar_mult(_B, s) == _add(r, _scalar_mult(a, k))
