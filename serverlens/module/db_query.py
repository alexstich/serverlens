from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PG = True
except ImportError:
    _HAS_PG = False

try:
    import pymysql
    import pymysql.cursors
    _HAS_MYSQL = True
except ImportError:
    _HAS_MYSQL = False

from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_VALID_OPS = (
    "eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
    "like", "contains", "between", "is_null", "array_contains",
)
_VALID_AGG_FNS = ("count", "min", "max", "avg", "sum")

# Read-only queries must never hold a connection hostage: a filter on an
# unindexed column of a large table gets cut off instead of blocking the
# whole MCP server.
_STATEMENT_TIMEOUT_MS = 30_000


class DbQuery(ModuleInterface):
    def __init__(self, config: Config) -> None:
        self._connections: dict[str, dict[str, Any]] = {}
        self._conn_cache: dict[str, Any] = {}

        for conn in config.get_database_connections():
            password_env = conn.get("password_env", "")
            password = os.environ.get(password_env, "") if password_env else ""

            tables: dict[str, dict[str, Any]] = {}
            for table in conn.get("tables", []):
                tables[table["name"]] = {
                    "allowed_fields": table.get("allowed_fields", ["*"]),
                    "denied_fields": table.get("denied_fields", []),
                    "max_rows": int(table.get("max_rows", 1000)),
                    "allowed_filters": table.get("allowed_filters", []),
                    "allowed_order_by": table.get("allowed_order_by", []),
                }

            driver = conn.get("driver", "postgresql")
            default_port = 3306 if driver == "mysql" else 5432

            self._connections[conn["name"]] = {
                "driver": driver,
                "host": conn.get("host", "localhost"),
                "port": int(conn.get("port", default_port)),
                "database": conn.get("database", ""),
                "user": conn.get("user", ""),
                "password": password,
                "tables": tables,
            }

    def get_tools(self) -> list[Tool]:
        return [
            Tool("db_list", "List databases, tables, and available fields", {
                "type": "object", "properties": {},
            }),
            Tool("db_describe", "Describe table structure (allowed fields)", {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database connection name"},
                    "table": {"type": "string", "description": "Table name"},
                },
                "required": ["database", "table"],
            }),
            Tool("db_query", "Query records with structured filters (no raw SQL)", {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database connection name"},
                    "table": {"type": "string", "description": "Table name"},
                    "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to select"},
                    "filters": {"type": "object", "description": "Filter conditions: {field: {op: value}}. Ops: eq, neq, gt, gte, lt, lte, in, not_in, like, contains, between, is_null, array_contains"},
                    "order_by": {"type": "array", "items": {"type": "string"}, "description": "Order by fields. Prefix with - for DESC."},
                    "limit": {"type": "integer", "description": "Max rows to return"},
                    "offset": {"type": "integer", "description": "Offset for pagination", "default": 0},
                    "format": {"type": "string", "enum": ["json", "tsv"], "description": "Output format. tsv is ~4x more compact for wide result sets.", "default": "json"},
                },
                "required": ["database", "table"],
            }),
            Tool("db_count", "Count records matching filters", {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database connection name"},
                    "table": {"type": "string", "description": "Table name"},
                    "filters": {"type": "object", "description": "Filter conditions"},
                },
                "required": ["database", "table"],
            }),
            Tool("db_aggregate", "Aggregate records grouped by fields (no raw SQL). Find duplicates: group_by + having_min_count=2", {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database connection name"},
                    "table": {"type": "string", "description": "Table name"},
                    "group_by": {"type": "array", "items": {"type": "string"}, "description": "Fields to group by (must be in allowed_fields). May be empty when 'aggregates' is given — then the whole table is one group."},
                    "aggregate": {"type": "string", "description": 'Single aggregate function; ignored when "aggregates" is given', "enum": ["count"], "default": "count"},
                    "aggregates": {
                        "type": "array",
                        "description": "Several aggregates at once: [{fn: count|min|max|avg|sum, field?, alias?}]. count without a field means COUNT(*).",
                        "items": {"type": "object"},
                    },
                    "filters": {"type": "object", "description": "Filter conditions: {field: {op: value}}. Same operators as db_query"},
                    "having_min_count": {"type": "integer", "description": "Only return groups with count >= X (optional)"},
                    "order": {"type": "string", "description": "Sort by group count", "enum": ["count_desc", "count_asc"], "default": "count_desc"},
                    "order_by": {"type": "array", "items": {"type": "string"}, "description": "Sort by group field or aggregate alias, prefix with - for DESC. Takes precedence over 'order'."},
                    "limit": {"type": "integer", "description": "Max groups to return (capped at table max_rows)"},
                    "format": {"type": "string", "enum": ["json", "tsv"], "description": "tsv is ~4x more compact", "default": "json"},
                },
                "required": ["database", "table"],
            }),
            Tool("db_stats", "Get basic statistics for a numeric field (COUNT, MIN, MAX, AVG)", {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database connection name"},
                    "table": {"type": "string", "description": "Table name"},
                    "field": {"type": "string", "description": "Field name"},
                },
                "required": ["database", "table", "field"],
            }),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        dispatch = {
            "db_list": self._list_databases,
            "db_describe": self._describe,
            "db_query": self._query,
            "db_aggregate": self._aggregate,
            "db_count": self._count,
            "db_stats": self._stats,
        }
        handler = dispatch.get(name)
        if handler is None:
            return self.error(f"Unknown tool: {name}")
        return handler(arguments)

    # ------------------------------------------------------------------

    def _list_databases(self, _args: dict[str, Any]) -> ToolResult:
        result = []
        for name, conn in self._connections.items():
            tables = [
                {"name": t, "allowed_fields": c["allowed_fields"], "max_rows": c["max_rows"]}
                for t, c in conn["tables"].items()
            ]

            status = "untested"
            error_msg = None
            try:
                db = self._get_conn(name)
                with db.cursor() as cur:
                    cur.execute("SELECT 1")
                status = "ok"
            except Exception as e:
                status = "error"
                error_msg = self._format_db_error(e)

            entry: dict[str, Any] = {
                "database": name,
                "driver": conn["driver"],
                "connection_status": status,
                "has_password": bool(conn["password"]),
                "tables": tables,
            }
            if error_msg:
                entry["connection_error"] = error_msg
            result.append(entry)

        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _describe(self, args: dict[str, Any]) -> ToolResult:
        db_name = self._resolve_db_name(args.get("database", ""))
        if db_name is None:
            return self._db_not_found_error(args.get("database", ""))
        table_name = args.get("table", "")
        tc = self._connections[db_name]["tables"].get(table_name)
        if tc is None:
            return self._table_not_found_error(db_name, table_name)

        info = {
            "database": db_name,
            "table": table_name,
            "allowed_fields": tc["allowed_fields"],
            "denied_fields": tc["denied_fields"],
            "max_rows": tc["max_rows"],
            # Effective sets, not the raw config keys: a readable column is
            # filterable and orderable by default, and config only extends that.
            "filterable_fields": self._resolve_filterable(tc),
            "orderable_fields": self._resolve_orderable(tc),
            "allowed_filters_config": tc["allowed_filters"],
            "allowed_order_by_config": tc["allowed_order_by"],
        }
        return self.ok(json.dumps(info, indent=2, ensure_ascii=False))

    def _query(self, args: dict[str, Any]) -> ToolResult:
        db_name = self._resolve_db_name(args.get("database", ""))
        if db_name is None:
            return self._db_not_found_error(args.get("database", ""))

        table_name = args.get("table", "")
        fields = args.get("fields")
        filters: dict[str, Any] = args.get("filters", {}) or {}
        order_by: list[str] = args.get("order_by", []) or []
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))

        tc = self._connections[db_name]["tables"].get(table_name)
        if tc is None:
            return self._table_not_found_error(db_name, table_name)

        allowed = self._resolve_allowed_fields(tc)
        if fields is None:
            fields = allowed

        driver = self._connections[db_name]["driver"]
        err = self._validate_query_params(fields, filters, order_by, tc, driver)
        if err:
            return self.error(err)

        limit = min(limit, tc["max_rows"])
        offset = max(0, offset)

        try:
            conn = self._get_conn(db_name)
            quoted_fields = [_quote_ident(f, driver) for f in fields]
            quoted_table = _quote_ident(table_name, driver)

            sql = f"SELECT {', '.join(quoted_fields)} FROM {quoted_table}"
            params: list[Any] = []

            where = self._build_where(filters, params, driver)
            if where:
                sql += f" WHERE {where}"

            if order_by:
                parts = []
                for ob in order_by:
                    if ob.startswith("-"):
                        parts.append(f"{_quote_ident(ob[1:], driver)} DESC")
                    else:
                        parts.append(f"{_quote_ident(ob, driver)} ASC")
                sql += " ORDER BY " + ", ".join(parts)

            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            if driver == "mysql":
                with conn.cursor(pymysql.cursors.DictCursor) as cur:
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]
            else:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]

            for row in rows:
                for k, v in row.items():
                    if not isinstance(v, (str, int, float, bool, type(None))):
                        row[k] = str(v)

            if _wants_tsv(args):
                header = f"# database={db_name} table={table_name} rows={len(rows)} offset={offset} limit={limit}"
                return self.ok(_to_tsv(rows, fields, header))

            result = {
                "database": db_name,
                "table": table_name,
                "rows_returned": len(rows),
                "offset": offset,
                "limit": limit,
                "data": rows,
            }
            return self.ok(json.dumps(result, indent=2, ensure_ascii=False, default=str))

        except Exception as e:
            print(f"[ServerLens] DB error: {e}", file=sys.stderr)
            return self.error(self._format_db_error(e))

    def _count(self, args: dict[str, Any]) -> ToolResult:
        db_name = self._resolve_db_name(args.get("database", ""))
        if db_name is None:
            return self._db_not_found_error(args.get("database", ""))
        table_name = args.get("table", "")
        filters: dict[str, Any] = args.get("filters", {}) or {}

        tc = self._connections[db_name]["tables"].get(table_name)
        if tc is None:
            return self._table_not_found_error(db_name, table_name)

        driver = self._connections[db_name]["driver"]
        err = self._validate_filters(filters, tc, driver)
        if err:
            return self.error(err)

        try:
            conn = self._get_conn(db_name)
            params: list[Any] = []
            sql = f"SELECT COUNT(*) as count FROM {_quote_ident(table_name, driver)}"
            where = self._build_where(filters, params, driver)
            if where:
                sql += f" WHERE {where}"

            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()

            result = {
                "database": db_name,
                "table": table_name,
                "count": row[0] if row else 0,
            }
            return self.ok(json.dumps(result, indent=2))
        except Exception as e:
            print(f"[ServerLens] DB error: {e}", file=sys.stderr)
            return self.error(self._format_db_error(e))

    def _aggregate(self, args: dict[str, Any]) -> ToolResult:
        db_name = self._resolve_db_name(args.get("database", ""))
        if db_name is None:
            return self._db_not_found_error(args.get("database", ""))

        table_name = args.get("table", "")
        group_by = args.get("group_by", []) or []
        aggregate_fn = args.get("aggregate", "count")
        aggregates = args.get("aggregates") or []
        filters: dict[str, Any] = args.get("filters", {}) or {}
        having_min_count = args.get("having_min_count")
        order = args.get("order", "count_desc")
        order_by: list[str] = args.get("order_by", []) or []
        limit = int(args.get("limit", 100))

        tc = self._connections[db_name]["tables"].get(table_name)
        if tc is None:
            return self._table_not_found_error(db_name, table_name)

        driver = self._connections[db_name]["driver"]
        err = self._validate_aggregate_params(
            group_by, aggregate_fn, filters, having_min_count, order, tc,
            aggregates, order_by, driver,
        )
        if err:
            return self.error(err)

        if having_min_count is not None:
            having_min_count = int(float(having_min_count))

        select_extra, aliases = self._build_aggregate_selects(aggregates, tc, driver)

        try:
            conn = self._get_conn(db_name)
            params: list[Any] = []

            sql = self._build_aggregate_sql(
                table_name, group_by, filters, having_min_count,
                order, limit, tc["max_rows"], driver, params,
                select_extra, order_by,
            )

            if driver == "mysql":
                with conn.cursor(pymysql.cursors.DictCursor) as cur:
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]
            else:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]

            for row in rows:
                for k, v in row.items():
                    if not isinstance(v, (str, int, float, bool, type(None))):
                        row[k] = str(v)

            if _wants_tsv(args):
                columns = list(group_by) + (aliases or ["count"])
                header = f"# database={db_name} table={table_name} groups={len(rows)}"
                return self.ok(_to_tsv(rows, columns, header))

            result = {
                "database": db_name,
                "table": table_name,
                "group_by": group_by,
                "aggregate": aggregates if aggregates else aggregate_fn,
                "groups_returned": len(rows),
                "data": rows,
            }
            return self.ok(json.dumps(result, indent=2, ensure_ascii=False, default=str))

        except Exception as e:
            print(f"[ServerLens] DB error: {e}", file=sys.stderr)
            return self.error(self._format_db_error(e))

    def _build_aggregate_selects(
        self,
        aggregates: list[dict[str, Any]],
        tc: dict[str, Any],
        driver: str,
    ) -> tuple[list[str], list[str]]:
        """Turn the aggregates spec into SELECT expressions plus their aliases.

        Assumes _validate_aggregate_params already accepted the spec.
        """
        select_extra: list[str] = []
        aliases: list[str] = []

        for agg in aggregates:
            fn = str(agg.get("fn", "count")).lower()
            field = agg.get("field")
            if field is None:
                expr, default_alias = "COUNT(*)", "count"
            else:
                qf = _quote_ident(str(field), driver)
                if fn == "avg" and driver != "mysql":
                    # Postgres refuses AVG on integer-like columns without a cast.
                    expr = f"AVG({qf}::numeric)"
                else:
                    expr = f"{fn.upper()}({qf})"
                default_alias = f"{fn}_{field}"

            alias = str(agg.get("alias") or default_alias)
            aliases.append(alias)
            select_extra.append(f"{expr} AS {_quote_ident(alias, driver)}")

        return select_extra, aliases

    def _stats(self, args: dict[str, Any]) -> ToolResult:
        db_name = self._resolve_db_name(args.get("database", ""))
        if db_name is None:
            return self._db_not_found_error(args.get("database", ""))
        table_name = args.get("table", "")
        field = args.get("field", "")

        tc = self._connections[db_name]["tables"].get(table_name)
        if tc is None:
            return self._table_not_found_error(db_name, table_name)

        allowed = self._resolve_allowed_fields(tc)
        if field not in allowed and allowed != ["*"]:
            return self.error(f"Field not allowed: {field}")

        try:
            conn = self._get_conn(db_name)
            driver = self._connections[db_name]["driver"]
            qt = _quote_ident(table_name, driver)
            qf = _quote_ident(field, driver)

            if driver == "mysql":
                sql = (
                    f"SELECT COUNT({qf}) as count, MIN({qf}) as min, "
                    f"MAX({qf}) as max, AVG({qf}) as avg FROM {qt}"
                )
            else:
                sql = (
                    f"SELECT COUNT({qf}) as count, MIN({qf}) as min, "
                    f"MAX({qf}) as max, AVG({qf}::numeric) as avg FROM {qt}"
                )

            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()

            result = {
                "database": db_name,
                "table": table_name,
                "field": field,
                "count": row[0] if row else 0,
                "min": str(row[1]) if row and row[1] is not None else None,
                "max": str(row[2]) if row and row[2] is not None else None,
                "avg": round(float(row[3]), 4) if row and row[3] is not None else None,
            }
            return self.ok(json.dumps(result, indent=2))
        except Exception as e:
            print(f"[ServerLens] DB error: {e}", file=sys.stderr)
            return self.error(self._format_db_error(e))

    # ------------------------------------------------------------------

    def _resolve_db_name(self, name: str) -> str | None:
        if name and name in self._connections:
            return name
        if len(self._connections) == 1:
            return next(iter(self._connections))
        return None

    def _db_not_found_error(self, requested: str) -> ToolResult:
        available = ", ".join(self._connections)
        return self.error(f"Unknown database connection: '{requested}'. Available: [{available}]")

    def _table_not_found_error(self, db_name: str, table: str) -> ToolResult:
        cnt = len(self._connections[db_name]["tables"])
        return self.error(f"Unknown table: '{table}' in database '{db_name}'. Total tables: {cnt}. Use db_list to see all.")

    @staticmethod
    def _resolve_allowed_fields(tc: dict[str, Any]) -> list[str]:
        allowed = tc["allowed_fields"]
        if allowed == ["*"] or allowed == "*":
            return ["*"]
        denied = tc["denied_fields"]
        return [f for f in allowed if f not in denied]

    @classmethod
    def _resolve_filterable(cls, tc: dict[str, Any]) -> list[str]:
        """Fields a filter may reference.

        A readable column is a filterable column: if the model may already SELECT
        the value, filtering by it discloses nothing new, and forcing operators to
        maintain a second parallel whitelist only means diagnostics stall on a
        config edit. ``allowed_filters`` therefore EXTENDS the set — it is for
        columns you want filterable but not visible — and ``denied_fields`` still
        wins over everything.
        """
        allowed = cls._resolve_allowed_fields(tc)
        if allowed == ["*"]:
            return ["*"]
        denied = tc["denied_fields"]
        extra = [f for f in tc["allowed_filters"] if f not in allowed and f not in denied]
        return allowed + extra

    @classmethod
    def _resolve_orderable(cls, tc: dict[str, Any]) -> list[str]:
        """Fields ORDER BY may reference — same reasoning as _resolve_filterable."""
        allowed = cls._resolve_allowed_fields(tc)
        if allowed == ["*"]:
            return ["*"]
        denied = tc["denied_fields"]
        extra = [f for f in tc["allowed_order_by"] if f not in allowed and f not in denied]
        return allowed + extra

    def _validate_query_params(
        self,
        fields: list[str],
        filters: dict[str, Any],
        order_by: list[str],
        tc: dict[str, Any],
        driver: str = "postgresql",
    ) -> str | None:
        allowed = self._resolve_allowed_fields(tc)
        is_wildcard = allowed == ["*"]

        if not is_wildcard:
            for f in fields:
                if f not in allowed:
                    return f"Field not allowed: {f}"

        for f in fields:
            if f in tc["denied_fields"]:
                return f"Access denied to field: {f}"

        err = self._validate_filters(filters, tc, driver)
        if err:
            return err

        orderable = self._resolve_orderable(tc)
        if orderable != ["*"]:
            for ob in order_by:
                field = ob.lstrip("-")
                if field not in orderable:
                    return f"Order by field not allowed: {field}. Allowed: [{', '.join(orderable)}]"

        return None

    @classmethod
    def _validate_filters(
        cls,
        filters: dict[str, Any],
        tc: dict[str, Any],
        driver: str = "postgresql",
    ) -> str | None:
        filterable = cls._resolve_filterable(tc)

        for field, conditions in filters.items():
            if filterable != ["*"] and field not in filterable:
                return f"Filter on field not allowed: {field}. Allowed: [{', '.join(filterable)}]"

            if not isinstance(conditions, dict):
                return f"Invalid filter format for field: {field}"

            for op, value in conditions.items():
                if op not in _VALID_OPS:
                    return f"Invalid filter operator: {op}. Valid: [{', '.join(_VALID_OPS)}]"
                if op in ("in", "not_in"):
                    if not isinstance(value, list):
                        return f"Operator '{op}' expects a list of values"
                    if len(value) > 50:
                        return f"Operator '{op}' is limited to 50 values"
                elif op == "between":
                    if not isinstance(value, list) or len(value) != 2:
                        return "Operator 'between' expects a list of exactly two values"
                elif op == "array_contains":
                    if driver == "mysql":
                        return "Operator 'array_contains' is PostgreSQL-only"
                    if not isinstance(value, (str, int, float, bool)):
                        return "Filter values must be scalar"
                elif op != "is_null" and not isinstance(value, (str, int, float, bool)):
                    return "Filter values must be scalar"

        return None

    def _validate_aggregate_params(
        self,
        group_by: Any,
        aggregate_fn: Any,
        filters: dict[str, Any],
        having_min_count: Any,
        order: Any,
        tc: dict[str, Any],
        aggregates: Any = None,
        order_by: Any = None,
        driver: str = "postgresql",
    ) -> str | None:
        aggregates = aggregates or []
        order_by = order_by or []

        if not isinstance(aggregates, list):
            return "aggregates must be an array of {fn, field?, alias?} objects"

        # Without an explicit aggregates spec the tool computes COUNT(*) per
        # group, and a group-less COUNT(*) is just db_count — so keep requiring
        # group_by in that case.
        if not isinstance(group_by, list) or (not group_by and not aggregates):
            return "group_by must be a non-empty array of fields"

        allowed = self._resolve_allowed_fields(tc)
        is_wildcard = allowed == ["*"]
        denied = tc["denied_fields"]

        for field in group_by:
            if not isinstance(field, str) or not _IDENT_RE.match(field):
                return f"Invalid group_by field: {field}"
            if not is_wildcard and field not in allowed:
                return f"Group by field not allowed: {field}"
            if field in denied:
                return f"Access denied to field: {field}"

        if not aggregates and aggregate_fn != "count":
            return f"Invalid aggregate function: {aggregate_fn}. Supported: count"

        aliases = list(group_by)
        for agg in aggregates:
            if not isinstance(agg, dict):
                return "Each aggregate must be an object: {fn, field?, alias?}"
            fn = str(agg.get("fn", "")).lower()
            if fn not in _VALID_AGG_FNS:
                return f"Invalid aggregate function: {agg.get('fn')}. Valid: [{', '.join(_VALID_AGG_FNS)}]"

            field = agg.get("field")
            if field is None:
                if fn != "count":
                    return f"Aggregate '{fn}' requires a field"
                default_alias = "count"
            else:
                if not isinstance(field, str) or not _IDENT_RE.match(field):
                    return f"Invalid aggregate field: {field}"
                if not is_wildcard and field not in allowed:
                    return f"Aggregate field not allowed: {field}"
                if field in denied:
                    return f"Access denied to field: {field}"
                default_alias = f"{fn}_{field}"

            alias = str(agg.get("alias") or default_alias)
            if not _IDENT_RE.match(alias):
                return f"Invalid aggregate alias: {alias}"
            if alias in aliases:
                return f"Duplicate output name: {alias}"
            aliases.append(alias)

        if not aggregates:
            aliases.append("count")

        if not isinstance(order_by, list):
            return "order_by must be an array"
        for ob in order_by:
            if not isinstance(ob, str) or ob.lstrip("-") not in aliases:
                return (
                    f"Order by must reference a group field or aggregate alias: {ob}. "
                    f"Available: [{', '.join(aliases)}]"
                )

        if having_min_count is not None and not _is_positive_count(having_min_count):
            return "having_min_count must be a positive integer"

        if not order_by and order not in ("count_desc", "count_asc"):
            return f"Invalid order: {order}. Allowed: [count_desc, count_asc]"

        return self._validate_filters(filters, tc, driver)

    def _build_aggregate_sql(
        self,
        table_name: str,
        group_by: list[str],
        filters: dict[str, Any],
        having_min_count: int | None,
        order: str,
        limit: int,
        max_rows: int,
        driver: str,
        params: list[Any],
        select_extra: list[str] | None = None,
        order_by: list[str] | None = None,
    ) -> str:
        quoted_table = _quote_ident(table_name, driver)
        group_by_clause = ", ".join(_quote_ident(f, driver) for f in group_by)

        select_parts = [group_by_clause] if group_by_clause else []
        select_parts.extend(select_extra or ["COUNT(*) AS count"])
        sql = f"SELECT {', '.join(select_parts)} FROM {quoted_table}"

        where = self._build_where(filters, params, driver)
        if where:
            sql += f" WHERE {where}"

        if group_by_clause:
            sql += f" GROUP BY {group_by_clause}"

        if having_min_count is not None:
            sql += " HAVING COUNT(*) >= %s"
            params.append(having_min_count)

        if order_by:
            parts = [
                f"{_quote_ident(ob.lstrip('-'), driver)} {'DESC' if ob.startswith('-') else 'ASC'}"
                for ob in order_by
            ]
            sql += " ORDER BY " + ", ".join(parts)
        elif not select_extra:
            direction = "ASC" if order == "count_asc" else "DESC"
            sql += f" ORDER BY COUNT(*) {direction}"

        sql += " LIMIT %s"
        params.append(min(max(limit, 1), max_rows))

        return sql

    def _build_where(self, filters: dict[str, Any], params: list[Any], driver: str = "postgresql") -> str:
        conditions: list[str] = []

        op_map = {
            "eq": "=", "neq": "!=", "gt": ">", "gte": ">=",
            "lt": "<", "lte": "<=", "like": "LIKE",
        }

        for field, ops in filters.items():
            qf = _quote_ident(field, driver)
            for op, value in ops.items():
                if op in op_map:
                    conditions.append(f"{qf} {op_map[op]} %s")
                    params.append(value)
                elif op in ("in", "not_in"):
                    if not isinstance(value, list) or not value:
                        continue
                    placeholders = ", ".join(["%s"] * len(value))
                    keyword = "IN" if op == "in" else "NOT IN"
                    conditions.append(f"{qf} {keyword} ({placeholders})")
                    params.extend(value)
                elif op == "contains":
                    conditions.append(f"{qf} LIKE %s ESCAPE '\\'")
                    params.append(f"%{_escape_like(str(value))}%")
                elif op == "between":
                    conditions.append(f"{qf} BETWEEN %s AND %s")
                    params.extend([value[0], value[1]])
                elif op == "array_contains":
                    # PostgreSQL array column: value = ANY(col). Rejected for
                    # MySQL during validation.
                    conditions.append(f"%s = ANY({qf})")
                    params.append(value)
                elif op == "is_null":
                    conditions.append(f"{qf} IS NULL" if value else f"{qf} IS NOT NULL")

        return " AND ".join(conditions)

    def _get_conn(self, db_name: str) -> Any:
        if db_name in self._conn_cache:
            cached = self._conn_cache[db_name]
            try:
                driver = self._connections[db_name]["driver"]
                if driver == "mysql":
                    cached.ping(reconnect=False)
                else:
                    cached.isolation_level
                return cached
            except Exception:
                self._conn_cache.pop(db_name, None)

        info = self._connections[db_name]
        if not info["password"]:
            raise RuntimeError(
                "No password configured (check env file and password_env setting)"
            )

        if info["driver"] == "mysql":
            if not _HAS_MYSQL:
                raise RuntimeError("PyMySQL not installed. Run: pip install pymysql")
            conn = pymysql.connect(
                host=info["host"],
                port=info["port"],
                database=info["database"],
                user=info["user"],
                password=info["password"],
                charset="utf8mb4",
                autocommit=True,
            )
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {_STATEMENT_TIMEOUT_MS}")
        else:
            if not _HAS_PG:
                raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
            conn = psycopg2.connect(
                host=info["host"],
                port=info["port"],
                dbname=info["database"],
                user=info["user"],
                password=info["password"],
            )
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")

        self._conn_cache[db_name] = conn
        return conn

    @staticmethod
    def _format_db_error(e: Exception) -> str:
        msg = str(e)
        if "password authentication failed" in msg or "No password configured" in msg or "Access denied" in msg:
            return "Database authentication failed (check password in env file)"
        if "Connection refused" in msg or "could not connect" in msg or "Can't connect" in msg:
            return "Database connection refused (check host/port)"
        if "Unknown column" in msg:
            m = re.search(r"Unknown column '([^']+)'", msg)
            if m:
                return f"Column does not exist: {m.group(1)} (check allowed_fields in config)"
            return "Column does not exist (check allowed_fields in config)"
        if "column" in msg and "does not exist" in msg:
            m = re.search(r'column "([^"]+)" does not exist', msg)
            if m:
                return f"Column does not exist: {m.group(1)} (check allowed_fields in config)"
            return "Column does not exist (check allowed_fields in config)"
        if "does not exist" in msg or "doesn't exist" in msg:
            return "Database or table does not exist"
        if "permission denied" in msg or "command denied" in msg:
            return "Database permission denied (check GRANT SELECT)"
        if "statement timeout" in msg or "maximum statement execution time" in msg:
            return (
                f"Query exceeded the {_STATEMENT_TIMEOUT_MS // 1000}s read-only timeout — "
                f"narrow the filters or group over fewer rows"
            )
        if "must appear in the GROUP BY" in msg or "not in GROUP BY" in msg:
            return "Aggregate query is invalid: every selected field must be grouped or aggregated"
        return "Database query failed"


def _is_positive_count(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) >= 1
    except (TypeError, ValueError):
        return False


def _escape_like(value: str) -> str:
    """Neutralise LIKE wildcards so 'contains' means a literal substring."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _wants_tsv(args: dict[str, Any]) -> bool:
    return str(args.get("format", "json")).lower() == "tsv"


def _to_tsv(rows: list[dict[str, Any]], columns: list[str], header: str) -> str:
    """Compact output: one header line, one line per row, tabs between values.

    Roughly four times smaller than indented JSON on wide result sets, which is
    what decides whether a result fits in the caller's context at all.
    """
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")

    if rows and columns == ["*"]:
        columns = list(rows[0].keys())

    lines = [header, "\t".join(columns)]
    lines.extend("\t".join(cell(row.get(c)) for c in columns) for row in rows)
    return "\n".join(lines)


def _quote_ident(name: str, driver: str = "postgresql") -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    if driver == "mysql":
        return f'`{name}`'
    return f'"{name}"'
