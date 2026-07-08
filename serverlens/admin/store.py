"""Filesystem side of the admin tool: paths, pending proposals, safe writes.

All locations are overridable via environment variables so the whole flow can
be exercised in a temp directory by the test suite (and by you, without root).
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any

import yaml


def config_path() -> str:
    return os.environ.get("SL_CONFIG", "/etc/serverlens/config.yaml")


def admin_dir() -> str:
    return os.environ.get("SL_ADMIN_DIR", "/etc/serverlens")


def pending_dir() -> str:
    return os.path.join(admin_dir(), "pending")


def totp_secret_path() -> str:
    return os.environ.get("SL_ADMIN_TOTP", os.path.join(admin_dir(), "admin.totp"))


def audit_path() -> str:
    return os.environ.get("SL_ADMIN_AUDIT", "/var/log/serverlens/admin-audit.log")


# ---------------------------------------------------------------------------

def load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"Not a YAML mapping: {path}")
    return data


def load_config() -> dict[str, Any]:
    return load_yaml(config_path())


def write_config_atomic(data: dict[str, Any]) -> str:
    """Write config.yaml atomically, preserving owner/mode of the original.

    Returns the path of the timestamped backup that was created.
    """
    path = config_path()
    backup = f"{path}.bak.{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(path, backup)

    st = os.stat(path)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# ServerLens Configuration\n")
        f.write("# Managed by serverlens-admin — additive edits are audited.\n\n")
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except (PermissionError, OSError):
        pass
    os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, path)
    return backup


# ---------------------------------------------------------------------------
# Pending proposals
# ---------------------------------------------------------------------------

def new_proposal_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def save_proposal(pid: str, record: dict[str, Any]) -> str:
    os.makedirs(pending_dir(), exist_ok=True)
    path = os.path.join(pending_dir(), f"{pid}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(record, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.chmod(path, 0o640)
    return path


def load_proposal(pid: str) -> dict[str, Any]:
    return load_yaml(os.path.join(pending_dir(), f"{pid}.yaml"))


def list_proposals() -> list[str]:
    d = pending_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".yaml"))


def discard_proposal(pid: str) -> None:
    path = os.path.join(pending_dir(), f"{pid}.yaml")
    if os.path.isfile(path):
        os.remove(path)


# ---------------------------------------------------------------------------

def audit(action: str, detail: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{os.getenv('SUDO_USER', os.getenv('USER', '?'))}\t{action}\t{detail}\n"
    try:
        os.makedirs(os.path.dirname(audit_path()), exist_ok=True)
        with open(audit_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
