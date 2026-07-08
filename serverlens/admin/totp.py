"""RFC 6238 TOTP — the second factor for ``serverlens-admin apply``.

Implemented on the standard library only (hmac/hashlib/struct/base64) so the
admin tool needs no extra dependency. The shared secret lives in a root-only
file on the server; the 6-digit code comes from the operator's authenticator
app and never passes through the MCP/LLM channel.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

_DIGITS = 6
_PERIOD = 30
_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def generate_secret(length: int = 20) -> str:
    """Return a fresh base32 secret (default 160 bits, no padding)."""
    raw = secrets.token_bytes(length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _code_at(secret_b32: str, counter: int) -> str:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    try:
        key = base64.b32decode(padded, casefold=True)
    except (ValueError, Exception):
        return ""
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** _DIGITS)).zfill(_DIGITS)


def verify(secret_b32: str, code: str, *, at: float | None = None, window: int = 1) -> bool:
    """Constant-time-ish verification, tolerating ±``window`` time steps."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != _DIGITS:
        return False
    now = time.time() if at is None else at
    counter = int(now // _PERIOD)
    for drift in range(-window, window + 1):
        if hmac.compare_digest(_code_at(secret_b32, counter + drift), code):
            return True
    return False


def provisioning_uri(secret_b32: str, account: str, issuer: str = "ServerLens") -> str:
    """otpauth:// URI to feed an authenticator app (e.g. as a QR)."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={_DIGITS}&period={_PERIOD}"
    )
