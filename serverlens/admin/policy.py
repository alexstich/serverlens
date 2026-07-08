"""Hard security policy for config mutations.

These rules are the last line of defence. Even a fully-trusted operator running
``serverlens-admin`` cannot cross them without editing config.yaml by hand as
root — which is exactly the escape hatch we want (auditable, deliberate, local).
"""
from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

# Sections the admin CLI is allowed to add to. Everything else is off-limits.
ALLOWED_SECTIONS: frozenset[str] = frozenset(
    {"logs", "configs", "databases", "system"}
)

# Sections that must NEVER be touched through the admin channel. Rotating
# tokens, changing audit/rate-limiting or the bind host stays a manual,
# root-only operation on purpose.
FORBIDDEN_SECTIONS: frozenset[str] = frozenset(
    {"auth", "audit", "rate_limiting", "server"}
)

# ---------------------------------------------------------------------------
# Sensitive fields
# ---------------------------------------------------------------------------

# A column whose name looks like a secret/PII may never land in allowed_fields.
# It can only ever be listed under denied_fields. Safe-by-default: we would
# rather over-deny a benign column (operator moves it back by hand) than leak a
# credential because a migration added a plausibly-named column.
_SENSITIVE_SEGMENTS: frozenset[str] = frozenset({
    "password", "passwd", "pass", "pwd",
    "secret", "token", "key", "apikey", "privkey",
    "hash", "salt", "seed", "mnemonic",
    "credential", "credentials", "auth",
    "ssn", "cvv", "cvc", "pan", "pin", "otp", "mfa", "totp",
})

_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "password", "passwd", "secret", "api_key", "apikey",
    "private_key", "access_key", "secret_key", "encrypted",
    "card_number", "cardnumber", "card_no", "_hash", "hash_",
    "reset_token", "refresh_token", "access_token",
)

_SEGMENT_RE = re.compile(r"[^a-z0-9]+")
# Split camelCase / PascalCase so "AccessToken" → "access token".
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def is_sensitive_field(name: str) -> bool:
    """True if a column name looks like a secret or sensitive identifier."""
    name = name.strip()
    if not name:
        return True  # empty / weird → treat as unsafe
    lower = _CAMEL_RE.sub("_", name).lower()
    for seg in _SEGMENT_RE.split(lower):
        if seg in _SENSITIVE_SEGMENTS:
            return True
    return any(sub in lower for sub in _SENSITIVE_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# A new log/config source may only point inside these roots.
ALLOWED_PATH_ROOTS: tuple[str, ...] = (
    "/var/log", "/etc", "/var/www", "/srv", "/opt", "/home",
)

# ...but never at any of these, whatever the root. Protects ServerLens' own
# secrets, host credentials and private keys.
_DENY_PREFIXES: tuple[str, ...] = (
    "/etc/serverlens",
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
    "/etc/ssh",
    "/root",
    "/proc", "/sys", "/dev",
)

_DENY_SUBSTRINGS: tuple[str, ...] = ("/.ssh/",)

_DENY_SUFFIXES: tuple[str, ...] = (
    ".key", ".pem", ".p12", ".pfx",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
)

MAX_ROWS_CAP = 100_000

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]*$")


def is_valid_name(name: str) -> bool:
    """Whitelist-safe identifier for source / table / connection names."""
    return bool(name) and bool(_IDENT_RE.match(name))


def check_path(path: str) -> str | None:
    """Return an error string if the path is not allowed, else ``None``."""
    if not path or not path.startswith("/"):
        return f"Path must be absolute: {path!r}"

    norm = os.path.normpath(path)
    # normpath collapses '..'; a mismatch on the leading segments means the
    # original tried to escape upward.
    if ".." in norm.split(os.sep):
        return f"Path traversal not allowed: {path!r}"

    lower = norm.lower()
    for pref in _DENY_PREFIXES:
        if norm == pref or norm.startswith(pref + "/"):
            return f"Path is in a protected location: {norm}"
    if any(sub in lower for sub in _DENY_SUBSTRINGS):
        return f"Path points at an SSH directory: {norm}"
    if any(lower.endswith(suf) for suf in _DENY_SUFFIXES):
        return f"Path looks like a private key / credential file: {norm}"

    if not any(norm == r or norm.startswith(r + "/") for r in ALLOWED_PATH_ROOTS):
        allowed = ", ".join(ALLOWED_PATH_ROOTS)
        return f"Path outside allowed roots [{allowed}]: {norm}"

    return None
