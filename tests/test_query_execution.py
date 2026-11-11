import pytest

from database.connection import DatabaseConnection


@pytest.mark.parametrize("query", [
    "SELECT * FROM purchasing.vendor",
    "SELECT updated_at, dropdown FROM items",
    "SELECT 'update; -- drop table' AS label",
    'SELECT "delete", "a;--b" FROM "update"',
    "SELECT $$delete; /* comment */$$",
    "SELECT $text$update; -- comment$text$",
    r"SELECT E'can\'t; delete'",
    "-- delete\nSELECT 1; /* update */",
    "WITH total AS (SELECT 1) SELECT * FROM total;",
])
def test_valid_select_is_not_rejected_by_keyword_substrings(query):
    assert DatabaseConnection()._is_read_only_query(query) == (True, "")


@pytest.mark.parametrize("query", ["", " ", ";", ";;;", "-- only a comment", "/* comment */;"])
def test_empty_statement_is_rejected_without_crashing(query):
    allowed, error = DatabaseConnection()._is_read_only_query(query)
    assert allowed is False
    assert error == "Empty SQL query"


@pytest.mark.parametrize("query", [
    "SELECT 1; SELECT 2", "SELECT 1; DELETE FROM items",
    "DELETE FROM items", "SELECTED 1",
    "WITH deleted AS (DELETE FROM items RETURNING *) SELECT * FROM deleted",
])
def test_writes_and_multiple_statements_remain_rejected(query):
    assert DatabaseConnection()._is_read_only_query(query)[0] is False


@pytest.mark.parametrize("query,max_rows,expected", [
    ("SELECT generate_series(1, 3) AS value", 3, "ok"),
    ("SELECT generate_series(1, 4) AS value", 3, "rejected"),
    ("SELECT generate_series(1, 1001) AS value", None, "rejected"),
    ("SELECT 1 WHERE FALSE", 0, "ok"),
    ("SELECT 1", 0, "rejected"),
])
def test_database_fetching_reports_oversized_results(query, max_rows, expected):
    with DatabaseConnection() as db:
        result = db.execute_read_only_query(query, max_rows=max_rows)
    assert result.status == expected
    if expected == "rejected":
        assert "row limit" in result.error
        assert result.rows is None
        assert result.dataframe is None
    else:
        assert result.row_count <= max_rows
        assert len(result.dataframe) == result.row_count


def test_statement_timeout_rolls_back_and_allows_next_query():
    with DatabaseConnection(statement_timeout_ms=50) as db:
        timed_out = db.execute_read_only_query("SELECT pg_sleep(0.2)")
        assert timed_out.status == "error"
        assert timed_out.pgcode == "57014"
        assert "statement timeout" in timed_out.error
        following = db.execute_read_only_query("SELECT 1 AS value")
        assert following.status == "ok"
        assert following.rows == [(1,)]


def test_query_error_rolls_back_and_read_only_mode_is_enforced():
    with DatabaseConnection() as db:
        failed = db.execute_read_only_query("SELECT nonexistent_fix_test_column")
        assert failed.status == "error"
        assert failed.pgcode == "42703"
        following = db.execute_read_only_query("SELECT current_setting('transaction_read_only') AS value")
        assert following.status == "ok"
        assert following.rows == [("on",)]


@pytest.mark.parametrize("timeout", [0, -1])
def test_statement_timeout_cannot_be_disabled(timeout):
    with pytest.raises(ValueError, match="must be positive"):
        DatabaseConnection(statement_timeout_ms=timeout)
