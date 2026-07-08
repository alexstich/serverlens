"""Validation and merging of additive config patches.

A *patch* is a small YAML/JSON document shaped like a subset of config.yaml.
It may only ADD log sources, config sources, database tables/connections and
system service/stack entries. It can never remove or rewrite anything that
already exists, and every addition is checked against :mod:`policy`.

All functions here are pure (no I/O), so the security logic is unit-testable
without a server, a database or root.
"""
from __future__ import annotations

import copy
from typing import Any

from serverlens.admin import policy

_ALLOWED_FILTER_FORMATS = {"plain", "json", "nginx_combined", "postgres"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_patch(patch: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Return a list of human-readable errors. Empty list == patch is safe."""
    errors: list[str] = []

    if not isinstance(patch, dict):
        return ["Patch must be a mapping (YAML/JSON object)"]

    for section in patch:
        if section in policy.FORBIDDEN_SECTIONS:
            errors.append(
                f"Section '{section}' can never be changed through admin "
                f"(edit config.yaml as root if you really must)"
            )
        elif section not in policy.ALLOWED_SECTIONS:
            errors.append(f"Unknown / disallowed section: '{section}'")

    _validate_sources(patch.get("logs"), "logs", current, errors, want_format=True)
    _validate_sources(patch.get("configs"), "configs", current, errors, want_format=False)
    _validate_databases(patch.get("databases"), current, errors)
    _validate_system(patch.get("system"), errors)

    return errors


def _validate_sources(
    section: Any,
    kind: str,
    current: dict[str, Any],
    errors: list[str],
    *,
    want_format: bool,
) -> None:
    if section is None:
        return
    if not isinstance(section, dict) or not isinstance(section.get("sources"), list):
        errors.append(f"{kind}.sources must be a list")
        return

    existing = {
        s.get("name")
        for s in (current.get(kind, {}) or {}).get("sources", []) or []
        if isinstance(s, dict)
    }

    for src in section["sources"]:
        if not isinstance(src, dict):
            errors.append(f"{kind}.sources entries must be mappings")
            continue
        name = src.get("name", "")
        if not policy.is_valid_name(name):
            errors.append(f"{kind}: invalid source name {name!r}")
        elif name in existing:
            errors.append(
                f"{kind}: source '{name}' already exists (admin only adds new sources)"
            )
        path = src.get("path", "")
        path_err = policy.check_path(path)
        if path_err:
            errors.append(f"{kind} '{name}': {path_err}")
        if want_format and src.get("format") and src["format"] not in _ALLOWED_FILTER_FORMATS:
            errors.append(
                f"{kind} '{name}': unknown format {src['format']!r} "
                f"(allowed: {', '.join(sorted(_ALLOWED_FILTER_FORMATS))})"
            )


def _validate_databases(section: Any, current: dict[str, Any], errors: list[str]) -> None:
    if section is None:
        return
    if not isinstance(section, dict) or not isinstance(section.get("connections"), list):
        errors.append("databases.connections must be a list")
        return

    current_conns = {
        c.get("name"): c
        for c in (current.get("databases", {}) or {}).get("connections", []) or []
        if isinstance(c, dict)
    }

    for conn in section["connections"]:
        if not isinstance(conn, dict):
            errors.append("databases.connections entries must be mappings")
            continue
        cname = conn.get("name", "")
        if not policy.is_valid_name(cname):
            errors.append(f"databases: invalid connection name {cname!r}")
            continue

        existing_conn = current_conns.get(cname)
        existing_tables = {}
        if existing_conn is not None:
            existing_tables = {
                t.get("name"): t
                for t in existing_conn.get("tables", []) or []
                if isinstance(t, dict)
            }
            # Existing connection: refuse to silently rewrite connection params.
            for locked in ("host", "port", "database", "user", "password_env", "driver"):
                if locked in conn and conn[locked] != existing_conn.get(locked):
                    errors.append(
                        f"databases '{cname}': cannot change '{locked}' of an "
                        f"existing connection through admin"
                    )
        else:
            # Brand new connection needs the basics.
            for required in ("host", "database", "user"):
                if not conn.get(required):
                    errors.append(f"databases '{cname}': missing '{required}' for new connection")

        for table in conn.get("tables", []) or []:
            _validate_table(cname, table, existing_tables, errors)


def _validate_table(
    cname: str,
    table: Any,
    existing_tables: dict[str, Any],
    errors: list[str],
) -> None:
    if not isinstance(table, dict):
        errors.append(f"databases '{cname}': table entries must be mappings")
        return

    tname = table.get("name", "")
    if not policy.is_valid_name(tname):
        errors.append(f"databases '{cname}': invalid table name {tname!r}")
        return

    allowed = table.get("allowed_fields", []) or []
    denied = set(table.get("denied_fields", []) or [])

    # Fields already denied on an existing table can never be promoted.
    prior_denied: set[str] = set()
    if tname in existing_tables:
        prior_denied = set(existing_tables[tname].get("denied_fields", []) or [])

    if allowed == ["*"] or allowed == "*":
        errors.append(
            f"databases '{cname}.{tname}': wildcard allowed_fields ['*'] is not "
            f"allowed through admin — list explicit columns"
        )
        allowed = []

    for field in allowed:
        if not isinstance(field, str) or not policy.is_valid_name(field):
            errors.append(f"databases '{cname}.{tname}': invalid field name {field!r}")
            continue
        if policy.is_sensitive_field(field):
            errors.append(
                f"databases '{cname}.{tname}': field '{field}' looks sensitive — "
                f"move it to denied_fields, it can't be exposed"
            )
        if field in denied:
            errors.append(
                f"databases '{cname}.{tname}': field '{field}' is in both "
                f"allowed_fields and denied_fields"
            )
        if field in prior_denied:
            errors.append(
                f"databases '{cname}.{tname}': field '{field}' was previously "
                f"denied and cannot be re-allowed through admin"
            )

    allowed_set = set(f for f in allowed if isinstance(f, str))
    for key in ("allowed_filters", "allowed_order_by"):
        for field in table.get(key, []) or []:
            if field not in allowed_set:
                errors.append(
                    f"databases '{cname}.{tname}': {key} references '{field}' "
                    f"which is not in allowed_fields"
                )

    max_rows = table.get("max_rows", 1000)
    if not isinstance(max_rows, int) or max_rows <= 0 or max_rows > policy.MAX_ROWS_CAP:
        errors.append(
            f"databases '{cname}.{tname}': max_rows must be 1..{policy.MAX_ROWS_CAP}"
        )


def _validate_system(section: Any, errors: list[str]) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        errors.append("system must be a mapping")
        return
    for key in ("allowed_services", "allowed_docker_stacks"):
        for entry in section.get(key, []) or []:
            if not policy.is_valid_name(str(entry)):
                errors.append(f"system.{key}: invalid entry {entry!r}")


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_patch(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Return a NEW config dict with the patch applied additively.

    Assumes ``validate_patch`` already returned no errors.
    """
    result = copy.deepcopy(current)

    for kind in ("logs", "configs"):
        if kind in patch:
            dst = result.setdefault(kind, {}).setdefault("sources", [])
            existing = {s.get("name") for s in dst if isinstance(s, dict)}
            for src in patch[kind].get("sources", []):
                if src.get("name") not in existing:
                    dst.append(copy.deepcopy(src))

    if "databases" in patch:
        conns = result.setdefault("databases", {}).setdefault("connections", [])
        by_name = {c.get("name"): c for c in conns if isinstance(c, dict)}
        for conn in patch["databases"].get("connections", []):
            target = by_name.get(conn.get("name"))
            if target is None:
                conns.append(copy.deepcopy(conn))
            else:
                tables = target.setdefault("tables", [])
                by_table = {t.get("name"): t for t in tables if isinstance(t, dict)}
                for table in conn.get("tables", []) or []:
                    dst = by_table.get(table.get("name"))
                    if dst is None:
                        tables.append(copy.deepcopy(table))
                    else:
                        _merge_table_fields(dst, table)

    if "system" in patch:
        sysd = result.setdefault("system", {})
        for key in ("allowed_services", "allowed_docker_stacks"):
            incoming = patch["system"].get(key, []) or []
            if incoming:
                merged = list(sysd.get(key, []) or [])
                for entry in incoming:
                    if entry not in merged:
                        merged.append(entry)
                sysd[key] = merged
        if any(k in patch["system"] for k in ("allowed_services", "allowed_docker_stacks")):
            sysd.setdefault("enabled", True)

    return result


def _extend_unique(dst: list[Any], incoming: list[Any], *, skip: frozenset[Any] | set[Any] = frozenset()) -> None:
    have = set(dst) | set(skip)
    for item in incoming:
        if item not in have:
            dst.append(item)
            have.add(item)


def _merge_table_fields(dst: dict[str, Any], patch_table: dict[str, Any]) -> None:
    """Additively fold new columns into an already-whitelisted table."""
    denied = set(dst.get("denied_fields", []) or [])
    # Never let a field that is already denied slip into allowed_fields.
    _extend_unique(
        dst.setdefault("allowed_fields", []),
        patch_table.get("allowed_fields", []) or [],
        skip=denied,
    )
    _extend_unique(dst.setdefault("denied_fields", []), patch_table.get("denied_fields", []) or [])
    for key in ("allowed_filters", "allowed_order_by"):
        if patch_table.get(key):
            _extend_unique(dst.setdefault(key, []), patch_table[key])


# ---------------------------------------------------------------------------
# Human-readable diff
# ---------------------------------------------------------------------------

def summarize_patch(current: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    """A short bullet list of what the patch would add. For operator review."""
    lines: list[str] = []

    for kind, label in (("logs", "log source"), ("configs", "config source")):
        if kind in patch:
            existing = {
                s.get("name")
                for s in (current.get(kind, {}) or {}).get("sources", []) or []
                if isinstance(s, dict)
            }
            for src in patch[kind].get("sources", []):
                if src.get("name") not in existing:
                    lines.append(f"+ {label}: {src.get('name')} → {src.get('path')}")

    if "databases" in patch:
        cur = {}
        for c in (current.get("databases", {}) or {}).get("connections", []) or []:
            if isinstance(c, dict):
                cur[c.get("name")] = {
                    t.get("name"): set(t.get("allowed_fields", []) or [])
                    for t in c.get("tables", []) or []
                    if isinstance(t, dict)
                }
        for conn in patch["databases"].get("connections", []):
            cname = conn.get("name")
            if cname not in cur:
                lines.append(f"+ database connection: {cname}")
            for table in conn.get("tables", []) or []:
                tname = table.get("name")
                new_fields = table.get("allowed_fields", []) or []
                if tname not in cur.get(cname, {}):
                    lines.append(f"+ table: {cname}.{tname} [{', '.join(new_fields)}]")
                else:
                    added = [f for f in new_fields if f not in cur[cname][tname]]
                    if added:
                        lines.append(f"+ fields on {cname}.{tname}: {', '.join(added)}")

    if "system" in patch:
        for key in ("allowed_services", "allowed_docker_stacks"):
            for entry in patch["system"].get(key, []) or []:
                lines.append(f"+ system.{key}: {entry}")

    return lines or ["(patch adds nothing new)"]
