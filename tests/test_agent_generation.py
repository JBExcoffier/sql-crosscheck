from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.models.test import TestModel

from agents.multiple.checks_generator import CheckQueryGenerator
from agents.queryer import OutputRefiner, SqlQueryRefiner
from database.connection import DatabaseConnection, QueryExecutionResult


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    db = DatabaseConnection()
    db.db_name = "adventureworks"
    db.connection = MagicMock()
    db.connection.closed = False
    db.connection.get_dsn_parameters.return_value = {"dbname": db.db_name}
    return db


def test_check_pipeline_generates_typed_sql_with_pydantic_ai(database):
    generator = CheckQueryGenerator(database)
    check_model = TestModel(custom_output_args={
        "checks": [{
            "query_question": " Total employees? ",
            "validation_question": " Counts match? ",
        }],
    })
    sql_model = TestModel(custom_output_args={
        "answerable": True,
        "query": "SELECT COUNT(*) FROM humanresources.employee",
        "explanations": "Count employees",
    })

    with generator.check_agent.agent.override(model=check_model), generator.sql_agent.agent.override(model=sql_model):
        result = generator.generate_checks_with_sql("Employees per department?")

    assert result.status == "ok"
    assert result.error is None
    assert len(result.checks) == 1
    assert result.checks[0].query_question == "Total employees?"
    assert result.checks[0].validation_question == "Counts match?"
    assert result.checks[0].sql_generation.query == "SELECT COUNT(*) FROM humanresources.employee"
    database.connection.cursor.assert_not_called()


def test_refiner_returns_shared_output_model_with_pydantic_ai(database):
    refiner = SqlQueryRefiner(database)
    model = TestModel(call_tools=[], custom_output_args={
        "answerable": True, "query": "SELECT 1", "explanations": "Fixed query",
    })

    with refiner.agent.override(model=model), patch.object(
        database, "execute_read_only_query", return_value=QueryExecutionResult(status="ok")
    ):
        result = refiner.refine("Question", "SELECT broken", "Invalid column")

    assert isinstance(result, OutputRefiner)
    assert result.final_query == "SELECT 1"
    assert result.status == "ok"
    assert result.retries_used == 1
