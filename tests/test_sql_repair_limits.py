from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agents.multiple.outputs import OutputSQL
from agents.queryer import SqlQueryRefiner
from database.connection import DatabaseConnection, QueryExecutionResult


@pytest.fixture
def refiner(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    db = MagicMock(spec=DatabaseConnection)
    db.is_connected.return_value = True
    db.get_database_name.return_value = "adventureworks"
    with patch("agents.queryer.get_schema_as_str", return_value="schema"):
        instance = SqlQueryRefiner(db, max_retries=2)
    instance.agent.run_sync = MagicMock(return_value=SimpleNamespace(output=OutputSQL(
        answerable=True, query="SELECT 1", explanations="Repair"
    )))
    return instance


def test_failed_repairs_stop_at_python_limit(refiner):
    refiner.db_connection.execute_read_only_query.return_value = QueryExecutionResult(
        status="error", error="Actual SQL error"
    )
    result = refiner.refine("Question", "SELECT broken", "Initial error")
    assert result.status == "error"
    assert result.retries_used == 2
    assert len(result.steps) == 2
    assert refiner.db_connection.execute_read_only_query.call_count == 2
    assert refiner.agent.run_sync.call_count == 2
    assert "Actual SQL error" in refiner.agent.run_sync.call_args.args[0]
    assert result.final_error == "Actual SQL error"
    assert result.steps[-1].execution["status"] == "error"


def test_success_stops_repairs_early(refiner):
    refiner.db_connection.execute_read_only_query.return_value = QueryExecutionResult(
        status="ok", columns=["value"], rows=[(1,)], row_count=1
    )
    result = refiner.refine("Question", "SELECT broken", "Initial error")
    assert result.status == "ok"
    assert result.retries_used == 1
    assert result.steps[0].execution["rows"] == [(1,)]
    refiner.db_connection.execute_read_only_query.assert_called_once_with("SELECT 1")


def test_zero_retries_does_not_call_model_or_database(refiner):
    refiner.max_retries = 0
    result = refiner.refine("Question", "SELECT broken", "Initial error")
    assert result.retries_used == 0
    assert result.final_error == "Initial error"
    refiner.agent.run_sync.assert_not_called()
    refiner.db_connection.execute_read_only_query.assert_not_called()


def test_generation_failure_does_not_count_as_sql_execution(refiner):
    refiner.agent.run_sync.side_effect = RuntimeError("Model unavailable")
    result = refiner.refine("Question", "SELECT broken", "Initial error")
    assert result.status == "error"
    assert result.retries_used == 0
    assert "Model unavailable" in result.final_error
    refiner.db_connection.execute_read_only_query.assert_not_called()


def test_unanswerable_repair_does_not_execute_sql(refiner):
    refiner.agent.run_sync.return_value.output.answerable = False
    result = refiner.refine("Question", "SELECT broken", "Initial error")
    assert result.status == "error"
    assert result.retries_used == 0
    refiner.db_connection.execute_read_only_query.assert_not_called()
