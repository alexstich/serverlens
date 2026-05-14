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
_VALID_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "in", "like", "is_null")


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
                    "filters": {"type": "object", "description": "Filter conditions: {field: {op: value}}. Ops: eq, neq, gt, gte, lt, lte, in, like, is_null"},
                    "order_by": {"type": "array", "items": {"type": "string"}, "description": "Order by fields. Prefix with - for DESC."},
                    "limit": {"type": "integer", "description": "Max rows to return"},
                    "offset": {"type": "integer", "description": "Offset for pagination", "default": 0},
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
            "allowed_filters": tc["allowed_filters"],
            "allowed_order_by": tc["allowed_order_by"],
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

        err = self._validate_query_params(fields, filters, order_by, tc)
        if err:
            return self.error(err)

        limit = min(limit, tc["max_rows"])
        offset = max(0, offset)

        try:
            conn = self._get_conn(db_name)
            driver = self._connections[db_name]["driver"]
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

        err = self._validate_filters(filters, tc)
        if err:
            return self.error(err)

        try:
            conn = self._get_conn(db_name)
            driver = self._connections[db_name]["driver"]
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

    def _validate_query_params(
        self,
        fields: list[str],
        filters: dict[str, Any],
        order_by: list[str],
        tc: dict[str, Any],
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

        err = self._validate_filters(filters, tc)
        if err:
            return err

        allowed_ob = tc["allowed_order_by"]
        for ob in order_by:
            field = ob.lstrip("-")
            if allowed_ob and field not in allowed_ob:
                return f"Order by field not allowed: {field}. Allowed: [{', '.join(allowed_ob)}]"

        return None

    @staticmethod
    def _validate_filters(filters: dict[str, Any], tc: dict[str, Any]) -> str | None:
        allowed_filters = tc["allowed_filters"]

        for field, conditions in filters.items():
            if allowed_filters and field not in allowed_filters:
                return f"Filter on field not allowed: {field}. Allowed: [{', '.join(allowed_filters)}]"

            if not isinstance(conditions, dict):
                return f"Invalid filter format for field: {field}"

            for op, value in conditions.items():
                if op not in _VALID_OPS:
                    return f"Invalid filter operator: {op}"
                if op == "in" and isinstance(value, list) and len(value) > 50:
                    return "IN operator limited to 50 values"
                if op not in ("in", "is_null") and not isinstance(value, (str, int, float, bool)):
                    return "Filter values must be scalar"

        return None

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
                elif op == "in":
                    if not isinstance(value, list) or not value:
                        continue
                    placeholders = ", ".join(["%s"] * len(value))
                    conditions.append(f"{qf} IN ({placeholders})")
                    params.extend(value)
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
        return "Database query failed"


def _quote_ident(name: str, driver: str = "postgresql") -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    if driver == "mysql":
        return f'`{name}`'
    return f'"{name}"'
