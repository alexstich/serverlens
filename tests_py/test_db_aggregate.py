from __future__ import annotations

import pytest

from serverlens.config import Config
from serverlens.module.db_query import DbQuery


def _make_db() -> DbQuery:
    config = Config({
        "server": {"host": "127.0.0.1", "transport": "stdio"},
        "databases": {
            "connections": [{
                "name": "test_db",
                "host": "localhost",
                "port": 5432,
                "database": "test",
                "user": "test",
                "password_env": "",
                "tables": [{
                    "name": "users",
                    "allowed_fields": ["id", "email", "gis_guid", "created_at", "is_active"],
                    "denied_fields": ["password_hash", "reset_token"],
                    "max_rows": 100,
                    "allowed_filters": ["id", "email", "is_active", "created_at"],
                    "allowed_order_by": ["id", "created_at"],
                }],
            }],
        },
    })
    return DbQuery(config)


@pytest.fixture
def db() -> DbQuery:
    return _make_db()


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_unknown_database_error(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "unknown", "table": "users", "group_by": ["email"],
    })
    assert result["isError"]


def test_unknown_table_error(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "nonexistent", "group_by": ["email"],
    })
    assert result["isError"]


def test_empty_group_by_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": [],
    })
    assert result["isError"]
    assert "group_by" in result["content"][0]["text"]


def test_disallowed_group_by_field_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email", "secret_column"],
    })
    assert result["isError"]
    assert "not allowed" in result["content"][0]["text"]


def test_denied_group_by_field_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["password_hash"],
    })
    assert result["isError"]


def test_malformed_group_by_identifier_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users",
        "group_by": ['email"; DROP TABLE users; --'],
    })
    assert result["isError"]
    assert "Invalid group_by field" in result["content"][0]["text"]


def test_unsupported_aggregate_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email"], "aggregate": "sum",
    })
    assert result["isError"]
    assert "Invalid aggregate function" in result["content"][0]["text"]


def test_invalid_order_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email"],
        "order": "email; DROP TABLE users",
    })
    assert result["isError"]
    assert "Invalid order" in result["content"][0]["text"]


@pytest.mark.parametrize("having", [0, -5, "abc", True])
def test_invalid_having_min_count_rejected(db, having):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email"],
        "having_min_count": having,
    })
    assert result["isError"]
    assert "having_min_count" in result["content"][0]["text"]


def test_disallowed_filter_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email"],
        "filters": {"secret_field": {"eq": "x"}},
    })
    assert result["isError"]
    assert "not allowed" in result["content"][0]["text"]


def test_invalid_filter_operator_rejected(db):
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email"],
        "filters": {"id": {"INVALID_OP": 1}},
    })
    assert result["isError"]
    assert "Invalid filter operator" in result["content"][0]["text"]


def test_valid_params_pass_validation(db):
    # Validation passes; the call then fails on the (unconfigured) DB
    # connection, not on parameter checks.
    result = db.handle_tool_call("db_aggregate", {
        "database": "test_db", "table": "users", "group_by": ["email"],
        "having_min_count": 2, "order": "count_desc",
    })
    assert result["isError"]
    assert "authentication failed" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

def test_sql_generation_postgres(db):
    params: list = []
    sql = db._build_aggregate_sql(
        "users", ["gis_guid"], {"is_active": {"eq": True}},
        2, "count_desc", 50, 100, "postgresql", params,
    )

    assert sql == (
        'SELECT "gis_guid", COUNT(*) AS count FROM "users"'
        ' WHERE "is_active" = %s'
        ' GROUP BY "gis_guid"'
        " HAVING COUNT(*) >= %s"
        " ORDER BY COUNT(*) DESC"
        " LIMIT %s"
    )
    assert params == [True, 2, 50]


def test_sql_generation_multiple_group_by_asc_no_having(db):
    params: list = []
    sql = db._build_aggregate_sql(
        "users", ["email", "is_active"], {},
        None, "count_asc", 10, 100, "postgresql", params,
    )

    assert sql == (
        'SELECT "email", "is_active", COUNT(*) AS count FROM "users"'
        ' GROUP BY "email", "is_active"'
        " ORDER BY COUNT(*) ASC"
        " LIMIT %s"
    )
    assert params == [10]


def test_sql_generation_mysql_quoting(db):
    params: list = []
    sql = db._build_aggregate_sql(
        "users", ["email"], {}, None, "count_desc", 10, 100, "mysql", params,
    )

    assert "SELECT `email`, COUNT(*) AS count FROM `users`" in sql
    assert "GROUP BY `email`" in sql


def test_sql_generation_caps_limit_at_max_rows(db):
    params: list = []
    db._build_aggregate_sql(
        "users", ["email"], {}, None, "count_desc", 5000, 100, "postgresql", params,
    )

    assert params[-1] == 100
