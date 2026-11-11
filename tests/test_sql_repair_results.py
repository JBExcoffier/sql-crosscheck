from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from agents.multiple.loop import LoopSqlAgent
from agents.multiple.outputs import OutputSQL, PrimaryResult
from agents.queryer import OutputRefiner, StepRefinement
from database.connection import DatabaseConnection, QueryExecutionResult


@pytest.fixture
def agent():
    db = MagicMock(spec=DatabaseConnection)
    db.get_database_name.return_value = "adventureworks"
    with patch("agents.multiple.loop.StraightforwardWithSchemaSqlAgent"), patch(
        "agents.multiple.loop.SqlQueryRefiner"
    ):
        instance = LoopSqlAgent(db)
    instance.base_agent.generate_sql.return_value = OutputSQL(
        explanations="Count employees", answerable=True, query="SELECT broken"
    )
    return instance


def repair_output(status="ok", with_steps=True):
    return OutputRefiner(
        status=status,
        final_query="SELECT COUNT(*) AS count FROM humanresources.employee"
        if status == "ok"
        else "",
        final_error="" if status == "ok" else "Repair failed",
        retries_used=1,
        steps=[
            StepRefinement(
                attempt=1,
                proposed_query="SELECT something_else",
                llm_explanation="Repaired query",
                execution={"status": "ok", "rows": [[999]], "row_count": 1},
            )
        ]
        if with_steps
        else [],
    )


@pytest.mark.parametrize("with_steps", [True, False])
def test_repaired_query_returns_actual_database_result(agent, with_steps):
    initial = QueryExecutionResult(status="error", error="Invalid column")
    actual = QueryExecutionResult(
        status="ok",
        columns=["count"],
        rows=[(290,)],
        row_count=1,
        dataframe=pd.DataFrame({"count": [290]}),
    )
    repair = repair_output(with_steps=with_steps)
    agent.refiner.refine.return_value = repair
    agent.db_connection.execute_read_only_query.side_effect = [initial, actual]

    result = agent.process_question("How many employees?")

    assert result.status == "ok"
    assert result.final_generation.query == repair.final_query
    assert result.final_result is actual
    assert result.initial_execution is initial
    assert result.total_attempts == 3
    assert agent.db_connection.execute_read_only_query.call_args_list == [
        call("SELECT broken"),
        call(repair.final_query),
    ]
    primary = PrimaryResult(
        generation=result.final_generation, execution=result.final_result
    )
    pd.testing.assert_frame_equal(primary.execution.dataframe, actual.dataframe)


@pytest.mark.parametrize("status", ["error", "rejected"])
def test_reported_repair_success_does_not_override_execution_failure(agent, status):
    actual = QueryExecutionResult(status=status, error="Query failed")
    agent.refiner.refine.return_value = repair_output()
    agent.db_connection.execute_read_only_query.side_effect = [
        QueryExecutionResult(status="error", error="Initial error"),
        actual,
    ]

    result = agent.process_question("How many employees?")

    assert result.status == "error"
    assert result.final_result is actual
    assert result.final_result.error == "Query failed"


def test_failed_repair_preserves_actual_initial_execution(agent):
    initial = QueryExecutionResult(status="error", error="Initial error")
    agent.db_connection.execute_read_only_query.return_value = initial
    agent.refiner.refine.return_value = repair_output(status="error")

    result = agent.process_question("How many employees?")

    assert result.status == "error"
    assert result.final_result is initial
    assert result.final_generation is None
    agent.db_connection.execute_read_only_query.assert_called_once_with("SELECT broken")


def test_successful_initial_query_does_not_need_repair(agent):
    actual = QueryExecutionResult(status="ok", dataframe=pd.DataFrame({"count": [290]}))
    agent.db_connection.execute_read_only_query.return_value = actual

    result = agent.process_question("How many employees?")

    assert result.status == "ok"
    assert result.final_result is actual
    assert result.total_attempts == 1
    agent.refiner.refine.assert_not_called()
    agent.db_connection.execute_read_only_query.assert_called_once_with("SELECT broken")
