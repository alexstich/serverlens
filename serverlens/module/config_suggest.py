"""ConfigSuggest — read-only module that PROPOSES whitelist patches.

Use case: a migration added tables/columns and the whitelist has not caught up.
This module introspects the live schema (through the same read-only DB user the
rest of ServerLens uses) and emits a ready-to-review patch for the
``serverlens-admin propose`` / ``apply`` flow.

It is strictly advisory — it never writes config and never touches row data.
Any column whose name looks sensitive is auto-sorted into ``denied_fields`` so
the default suggestion cannot leak a credential. The operator still reviews the
patch and confirms with a second factor before it takes effect.
"""
from __future__ import annotations

import json
from typing import Any

import yaml

from serverlens.admin import policy
from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult
from serverlens.module.db_query import DbQuery

# Columns to seed allowed_filters / allowed_order_by with when present.
_COMMON_FILTERABLE = ("id", "created_at", "updated_at", "status", "type")


class ConfigSuggest(ModuleInterface):
    def __init__(self, config: Config) -> None:
        # Reuse DbQuery for connection params + read-only enforcement — single
        # source of truth for how ServerLens talks to a database.
        self._db = DbQuery(config)

    def get_tools(self) -> list[Tool]:
        return [
            Tool(
                "config_suggest",
                "Introspect a database's live schema and propose an additive "
                "config patch (new tables / new columns) for serverlens-admin. "
                "Sensitive-looking columns are auto-placed in denied_fields. "
                "Advisory only — applying the patch needs a second factor.",
                {
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database connection name"},
                        "schema": {"type": "string", "description": "Schema to scan (default: public / the DB name)"},
                        "include_existing": {
                            "type": "boolean",
                            "description": "Also propose columns newly appeared on already-whitelisted tables",
                            "default": True,
                        },
                    },
                    "required": ["database"],
                },
            ),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name != "config_suggest":
            return self.error(f"Unknown tool: {name}")
        return self._suggest(arguments)

    # ------------------------------------------------------------------

    def _suggest(self, args: dict[str, Any]) -> ToolResult:
        db_name = self._db._resolve_db_name(args.get("database", ""))
        if db_name is None:
            available = ", ".join(self._db._connections)
            return self.error(
                f"Unknown database connection: '{args.get('database', '')}'. Available: [{available}]"
            )

        conn_info = self._db._connections[db_name]
        driver = conn_info["driver"]
        include_existing = bool(args.get("include_existing", True))
        schema = args.get("schema") or ("public" if driver != "mysql" else conn_info["database"])

        try:
            live = self._introspect(db_name, driver, schema)
        except Exception as e:
            return self.error(self._db._format_db_error(e))

        if not live:
            return self.ok(f"No tables found in schema '{schema}' of '{db_name}'.")

        whitelisted = conn_info["tables"]
        proposed_tables: list[dict[str, Any]] = []
        notes: list[str] = []

        for table_name, columns in sorted(live.items()):
            existing = whitelisted.get(table_name)
            if existing is None:
                entry, tnotes = self._propose_new_table(table_name, columns)
                proposed_tables.append(entry)
                notes.extend(tnotes)
            elif include_existing:
                entry, tnotes = self._propose_new_columns(table_name, columns, existing)
                if entry is not None:
                    proposed_tables.append(entry)
                    notes.extend(tnotes)

        if not proposed_tables:
            return self.ok(
                f"Whitelist for '{db_name}' is already up to date with schema '{schema}'. "
                f"Nothing to propose."
            )

        patch = {"databases": {"connections": [{"name": db_name, "tables": proposed_tables}]}}
        patch_yaml = yaml.safe_dump(
            patch, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

        result = {
            "database": db_name,
            "schema": schema,
            "tables_proposed": len(proposed_tables),
            "notes": notes,
            "how_to_apply": (
                "Save 'patch_yaml' to a file on the server, then: "
                "sudo serverlens-admin propose --patch <file>  →  review  →  "
                "sudo serverlens-admin apply --id <id> --otp <code>"
            ),
            "patch_yaml": patch_yaml,
        }
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    # ------------------------------------------------------------------

    @staticmethod
    def _propose_new_table(
        table_name: str, columns: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        allowed, denied = _split_columns(columns)
        notes: list[str] = []
        if denied:
            notes.append(
                f"{table_name}: auto-denied sensitive columns → {', '.join(denied)}"
            )
        if not allowed:
            notes.append(
                f"{table_name}: every column looked sensitive — review denied_fields by hand"
            )
        entry: dict[str, Any] = {
            "name": table_name,
            "allowed_fields": allowed,
            "denied_fields": denied,
            "max_rows": 1000,
            "allowed_filters": [c for c in _COMMON_FILTERABLE if c in allowed],
            "allowed_order_by": [c for c in ("id", "created_at", "updated_at") if c in allowed],
        }
        return entry, notes

    @staticmethod
    def _propose_new_columns(
        table_name: str, columns: list[str], existing: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        known = set(existing["allowed_fields"]) | set(existing["denied_fields"])
        if existing["allowed_fields"] == ["*"]:
            return None, []  # wildcard table already sees everything
        new_cols = [c for c in columns if c not in known]
        if not new_cols:
            return None, []
        allowed, denied = _split_columns(new_cols)
        notes = [f"{table_name}: {len(new_cols)} new column(s) since last whitelist"]
        if denied:
            notes.append(f"{table_name}: auto-denied → {', '.join(denied)}")
        entry: dict[str, Any] = {"name": table_name}
        if allowed:
            entry["allowed_fields"] = allowed
        if denied:
            entry["denied_fields"] = denied
        return entry, notes

    def _introspect(self, db_name: str, driver: str, schema: str) -> dict[str, list[str]]:
        conn = self._db._get_conn(db_name)
        if driver == "mysql":
            sql = (
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s ORDER BY table_name, ordinal_position"
            )
        else:
            sql = (
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s ORDER BY table_name, ordinal_position"
            )
        tables: dict[str, list[str]] = {}
        with conn.cursor() as cur:
            cur.execute(sql, [schema])
            for row in cur.fetchall():
                tname, cname = row[0], row[1]
                tables.setdefault(tname, []).append(cname)
        return tables


def _split_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    allowed, denied = [], []
    for col in columns:
        (denied if policy.is_sensitive_field(col) else allowed).append(col)
    return allowed, denied
