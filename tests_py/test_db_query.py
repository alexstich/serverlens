"""Unit tests for the read-only query layer.

No database here: every test exercises the whitelist logic and the SQL builder,
which is where a mistake would either leak a denied column or let a filter
through unchecked.
"""
from __future__ import annotations

import pytest

from serverlens.module.db_query import DbQuery, _escape_like, _to_tsv


def table(**overrides):
    tc = {
        "allowed_fields": ["id", "status", "created_at", "note"],
        "denied_fields": ["secret_note"],
        "max_rows": 500,
        "allowed_filters": [],
        "allowed_order_by": [],
    }
    tc.update(overrides)
    return tc


# --------------------------------------------------------------------------
# Filterable / orderable resolution
# --------------------------------------------------------------------------

def test_readable_field_is_filterable_without_config():
    assert "status" in DbQuery._resolve_filterable(table())


def test_allowed_filters_extends_rather_than_restricts():
    tc = table(allowed_filters=["tenant_id"])
    filterable = DbQuery._resolve_filterable(tc)
    assert "tenant_id" in filterable, "a filter-only column must be usable"
    assert "status" in filterable, "a readable column must stay filterable"


def test_denied_field_never_becomes_filterable():
    tc = table(allowed_filters=["secret_note"])
    assert "secret_note" not in DbQuery._resolve_filterable(tc)


def test_denied_field_never_becomes_orderable():
    tc = table(allowed_order_by=["secret_note"])
    assert "secret_note" not in DbQuery._resolve_orderable(tc)


def test_wildcard_table_stays_wildcard():
    assert DbQuery._resolve_filterable(table(allowed_fields=["*"])) == ["*"]


# --------------------------------------------------------------------------
# Filter validation
# --------------------------------------------------------------------------

def test_unknown_column_is_rejected():
    err = DbQuery._validate_filters({"nope": {"eq": 1}}, table())
    assert err and "not allowed" in err


def test_unknown_operator_is_rejected():
    err = DbQuery._validate_filters({"status": {"regex": "x"}}, table())
    assert err and "Invalid filter operator" in err


def test_in_requires_a_list():
    assert DbQuery._validate_filters({"id": {"in": 5}}, table())


def test_in_is_capped_at_50_values():
    err = DbQuery._validate_filters({"id": {"in": list(range(51))}}, table())
    assert err and "50 values" in err


def test_between_needs_exactly_two_values():
    assert DbQuery._validate_filters({"id": {"between": [1]}}, table())
    assert DbQuery._validate_filters({"id": {"between": [1, 2]}}, table()) is None


def test_array_contains_is_rejected_on_mysql():
    err = DbQuery._validate_filters({"note": {"array_contains": "x"}}, table(), "mysql")
    assert err and "PostgreSQL-only" in err


def test_array_contains_is_accepted_on_postgres():
    assert DbQuery._validate_filters({"note": {"array_contains": "x"}}, table()) is None


# --------------------------------------------------------------------------
# WHERE building
# --------------------------------------------------------------------------

@pytest.fixture
def builder():
    return DbQuery.__new__(DbQuery)  # no config needed for _build_where


def test_contains_wraps_value_and_escapes_wildcards(builder):
    params: list = []
    sql = builder._build_where({"note": {"contains": "50%_off"}}, params)
    assert sql == "\"note\" LIKE %s ESCAPE '\\'"
    assert params == ["%50\\%\\_off%"], "a literal % must not become a wildcard"


def test_not_in_builds_negated_list(builder):
    params: list = []
    sql = builder._build_where({"status": {"not_in": ["a", "b"]}}, params)
    assert sql == '"status" NOT IN (%s, %s)'
    assert params == ["a", "b"]


def test_between_builds_range(builder):
    params: list = []
    sql = builder._build_where({"id": {"between": [10, 20]}}, params)
    assert sql == '"id" BETWEEN %s AND %s'
    assert params == [10, 20]


def test_array_contains_uses_any(builder):
    params: list = []
    sql = builder._build_where({"note": {"array_contains": "guid"}}, params)
    assert sql == '%s = ANY("note")'
    assert params == ["guid"]


def test_values_are_never_inlined_into_sql(builder):
    params: list = []
    sql = builder._build_where({"status": {"eq": "'; DROP TABLE users --"}}, params)
    assert "DROP TABLE" not in sql
    assert params == ["'; DROP TABLE users --"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def test_escape_like_handles_backslash_first():
    assert _escape_like("a\\b%c") == "a\\\\b\\%c"


def test_tsv_keeps_one_line_per_row():
    rows = [{"a": 1, "b": "x\ty"}, {"a": None, "b": "z"}]
    out = _to_tsv(rows, ["a", "b"], "# head")
    assert out.splitlines() == ["# head", "a\tb", "1\tx y", "\tz"]


# --------------------------------------------------------------------------
# Multi-aggregate extensions to db_aggregate
# --------------------------------------------------------------------------

@pytest.fixture
def agg():
    """A DbQuery instance usable for validation / SQL building only."""
    return DbQuery.__new__(DbQuery)


def test_group_by_may_be_empty_when_aggregates_given(agg):
    err = agg._validate_aggregate_params(
        [], "count", {}, None, "count_desc", table(), [{"fn": "max", "field": "id"}], [],
    )
    assert err is None


def test_group_by_still_required_without_aggregates(agg):
    err = agg._validate_aggregate_params([], "count", {}, None, "count_desc", table())
    assert err and "non-empty" in err


def test_aggregate_needs_a_field_unless_it_is_count(agg):
    err = agg._validate_aggregate_params(
        ["status"], "count", {}, None, "count_desc", table(), [{"fn": "avg"}], [],
    )
    assert err and "requires a field" in err


def test_aggregate_cannot_touch_a_denied_field(agg):
    err = agg._validate_aggregate_params(
        ["status"], "count", {}, None, "count_desc", table(),
        [{"fn": "max", "field": "secret_note"}], [],
    )
    assert err and "not allowed" in err


def test_alias_may_not_collide_with_a_group_field(agg):
    err = agg._validate_aggregate_params(
        ["status"], "count", {}, None, "count_desc", table(),
        [{"fn": "count", "alias": "status"}], [],
    )
    assert err and "Duplicate output name" in err


def test_order_by_must_name_a_group_or_alias(agg):
    err = agg._validate_aggregate_params(
        ["status"], "count", {}, None, "count_desc", table(),
        [{"fn": "count"}], ["-nonsense"],
    )
    assert err and "Order by must reference" in err


def test_order_by_accepts_an_aggregate_alias(agg):
    err = agg._validate_aggregate_params(
        ["status"], "count", {}, None, "count_desc", table(),
        [{"fn": "count", "alias": "total"}], ["-total"],
    )
    assert err is None


def test_multi_aggregate_sql_selects_every_alias(agg):
    params: list = []
    select_extra, aliases = agg._build_aggregate_selects(
        [{"fn": "count"}, {"fn": "min", "field": "created_at", "alias": "oldest"}],
        table(), "postgresql",
    )
    sql = agg._build_aggregate_sql(
        "t", ["status"], {}, None, "count_desc", 10, 500, "postgresql", params,
        select_extra, ["-count"],
    )
    assert aliases == ["count", "oldest"]
    assert 'SELECT "status", COUNT(*) AS "count", MIN("created_at") AS "oldest"' in sql
    assert 'GROUP BY "status"' in sql
    assert 'ORDER BY "count" DESC' in sql


def test_avg_is_cast_on_postgres_but_not_mysql(agg):
    pg, _ = agg._build_aggregate_selects([{"fn": "avg", "field": "id"}], table(), "postgresql")
    my, _ = agg._build_aggregate_selects([{"fn": "avg", "field": "id"}], table(), "mysql")
    assert 'AVG("id"::numeric)' in pg[0]
    assert "AVG(`id`)" in my[0]


def test_group_less_aggregate_omits_group_by(agg):
    params: list = []
    select_extra, _ = agg._build_aggregate_selects([{"fn": "count"}], table(), "postgresql")
    sql = agg._build_aggregate_sql(
        "t", [], {}, None, "count_desc", 10, 500, "postgresql", params, select_extra, [],
    )
    assert "GROUP BY" not in sql
    assert sql.startswith('SELECT COUNT(*) AS "count" FROM "t"')
