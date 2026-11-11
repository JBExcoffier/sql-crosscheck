from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from agents.multiple.checks_generator import CheckQueryGeneratorOutput
from agents.multiple.loop import LoopSqlAgentOutput
from agents.multiple.outputs import OutputSQL
from agents.multiple.sql_agent_refiner import SqlAgentRefiner
from agents.multiple.validation.dataframe_validator import (
    DataframeValidatorWithHints,
    ValidationResult,
)
from database.connection import DatabaseConnection, QueryExecutionResult


@pytest.fixture
def refiner():
    db = MagicMock(spec=DatabaseConnection)
    db.get_database_name.return_value = "adventureworks"
    with patch("agents.multiple.sql_agent_refiner.LoopSqlAgent"), patch(
        "agents.multiple.sql_agent_refiner.CheckQueryGenerator"
    ), patch("agents.multiple.sql_agent_refiner.DataframeValidatorWithHints"), patch(
        "agents.multiple.sql_agent_refiner.get_schema_as_str", return_value="schema"
    ):
        instance = SqlAgentRefiner(db, recompute_checks=False)

    instance.loop_agent.process_question.return_value = LoopSqlAgentOutput(
        status="ok",
        user_question="Question",
        initial_generation=OutputSQL(
            explanations="Count", answerable=True, query="SELECT 1"
        ),
        initial_execution=None,
        final_result=QueryExecutionResult(
            status="ok", dataframe=pd.DataFrame({"count": [1]})
        ),
        refinement=None,
        total_attempts=1,
    )
    instance.check_generator.generate_checks_with_sql.return_value = (
        CheckQueryGeneratorOutput(user_question="Question", status="empty", checks=[])
    )
    instance.validator.validate_sql_agent_output.return_value = ValidationResult(
        overall_valid=False, check_results=[]
    )
    instance.validator.generate_correction_hint.return_value.correction_hint = "Try again"
    return instance


@pytest.mark.parametrize("max_steps", [0, 1, 2])
def test_refinement_limit_counts_additional_attempts(refiner, max_steps):
    refiner.max_refinement_steps = max_steps
    callback = MagicMock()

    result = refiner.refine("Original question", step_callback=callback)

    assert result.status == "not_ok"
    assert result.total_steps == max_steps + 1
    assert refiner.loop_agent.process_question.call_args_list == [
        call("Original question"),
        *[call("Original question\n\nTry again")] * max_steps,
    ]
    assert callback.call_count == max_steps + 1


@pytest.mark.parametrize("recompute_checks,expected_calls", [(False, 2), (True, 4)])
def test_checks_are_scoped_to_each_run(refiner, recompute_checks, expected_calls):
    refiner.recompute_checks = recompute_checks
    refiner.max_refinement_steps = 1

    refiner.refine("First question")
    refiner.refine("Second question")

    calls_per_question = expected_calls // 2
    assert refiner.check_generator.generate_checks_with_sql.call_args_list == (
        [call("First question")] * calls_per_question
        + [call("Second question")] * calls_per_question
    )


def test_missing_execution_returns_validation_error(refiner):
    primary_result = refiner.loop_agent.process_question.return_value
    primary_result.status = "error"
    primary_result.final_result = None
    # These methods need no model calls when the main execution is absent.
    refiner.validator = object.__new__(DataframeValidatorWithHints)

    result = refiner.refine("Question that failed to generate SQL")

    assert result.status == "not_ok"
    assert result.is_valid is False
    assert result.total_steps == 1
    assert result.final_sql_result.primary.execution is None
    assert result.final_validation_result.overall_valid == "error"
    assert "execution result" in result.final_validation_result.error


def test_unanswerable_question_does_not_generate_checks(refiner):
    refiner.loop_agent.process_question.return_value.status = "rejected"

    result = refiner.refine("Unanswerable question")

    assert result.status == "unanswerable"
    assert result.total_steps == 1
    refiner.check_generator.generate_checks_with_sql.assert_not_called()
    refiner.validator.validate_sql_agent_output.assert_not_called()


def test_streamlit_passes_zero_refinement_steps(refiner):
    refiner.max_refinement_steps = 0
    result = refiner.refine("Question")
    with patch("database.connection.DatabaseConnection"), patch(
        "agents.multiple.sql_agent_refiner.SqlAgentRefiner"
    ) as constructor:
        constructor.return_value.refine.return_value = result
        app = AppTest.from_file("interface/main.py").run()
        app.number_input[2].set_value(0)
        app.text_area[0].set_value("Question")
        app.button[0].click().run()

    assert not app.exception
    assert constructor.call_args.kwargs["max_refinement_steps"] == 0
