from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from agents.checker import OutputChecks, OutputSingleCheck
from agents.multiple.checks_generator import CheckQueryGenerator
from agents.multiple.loop import LoopSqlAgentOutput
from agents.multiple.outputs import (
    CheckWithExecution, OutputSQL, PrimaryResult, SqlAgentWithChecksOutput,
)
from agents.multiple.sql_agent_refiner import SqlAgentRefiner
from agents.multiple.validation.dataframe_validator import (
    DataframeValidatorWithHints, SingleCheckResult, ValidationResult,
)
from agents.multiple.validation.sandbox import ValidationSandbox
from database.connection import DatabaseConnection, QueryExecutionResult


def sql_output(main_rows=1, check_rows=1):
    generation = OutputSQL(explanations="Count", answerable=True, query="SELECT 1")
    return SqlAgentWithChecksOutput(
        status="ok", user_question="Count?",
        primary=PrimaryResult(
            generation=generation,
            execution=QueryExecutionResult(
                status="ok", dataframe=pd.DataFrame({"count": [1] * main_rows})
            ),
        ),
        checks=[CheckWithExecution(
            query_question="Total?", validation_question="Totals match?",
            sql_generation=generation,
            execution=QueryExecutionResult(
                status="ok", dataframe=pd.DataFrame({"count": [1] * check_rows})
            ),
        )],
    )


@pytest.fixture
def validator():
    instance = object.__new__(DataframeValidatorWithHints)
    instance.sandbox = ValidationSandbox()
    instance.max_total_attempts = 1
    instance._generate_validation_code = MagicMock(return_value=(
        "result = {'valid': main_df['count'].sum() == check_df['count'].sum()}"
    ))
    return instance


def test_no_checks_is_unchecked(validator):
    output = sql_output()
    output.checks = []
    result = validator.validate_sql_agent_output(output)
    assert result.overall_valid == "unchecked"
    validator._generate_validation_code.assert_not_called()
    assert validator.generate_correction_hint(result, "Question", "schema").overall_valid == "unchecked"


@pytest.mark.parametrize("main_rows,check_rows", [(1001, 1), (1, 1001)])
def test_oversized_dataframes_are_rejected_before_code_generation(validator, main_rows, check_rows):
    result = validator.validate_sql_agent_output(sql_output(main_rows, check_rows))
    assert result.overall_valid == "error"
    error = result.error or result.check_results[0].error
    assert "1000 rows" in error
    validator._generate_validation_code.assert_not_called()


@pytest.mark.parametrize("check_rows,expected", [(1, True), (2, False)])
def test_generated_validation_produces_real_pass_or_failure(validator, check_rows, expected):
    result = validator.validate_sql_agent_output(sql_output(check_rows=check_rows))
    assert result.overall_valid is expected


@pytest.mark.parametrize("statuses,expected", [
    ([True, True], True), ([True, False], False),
    ([True, "error"], "error"), ([False, "error"], "error"),
])
def test_overall_outcome_preserves_errors(validator, statuses, expected):
    output = sql_output()
    output.checks *= len(statuses)
    validator._validate_single_check = MagicMock(side_effect=[
        SingleCheckResult(i, "Check", "Rule", valid, "", None, "", "", "")
        for i, valid in enumerate(statuses)
    ])
    result = validator.validate_sql_agent_output(output)
    assert result.overall_valid == expected
    assert [check.valid for check in result.check_results] == statuses


def test_generated_code_error_keeps_diagnostics(validator):
    validator._generate_validation_code.return_value = "print('debug'); result = 1 / 0"
    result = validator.validate_sql_agent_output(sql_output())
    assert result.overall_valid == "error"
    assert "ZeroDivisionError" in result.check_results[0].error
    assert result.check_results[0].stdout == "debug\n"


@pytest.fixture
def generator():
    instance = object.__new__(CheckQueryGenerator)
    instance.check_limit = 3
    instance.use_loop_agent = False
    instance.check_agent = MagicMock()
    instance.sql_agent = MagicMock()
    instance.check_agent.generate_checks.return_value = OutputChecks(checks=[
        OutputSingleCheck(query_question="Total?", validation_question="Totals match?")
    ])
    return instance


def test_natural_language_check_generation_error_is_preserved(generator):
    generator.check_agent.generate_checks.return_value = OutputChecks(checks=[], error="Model unavailable")
    result = generator.generate_checks_with_sql("Count?")
    assert result.status == "error"
    assert result.error == "Model unavailable"
    generator.sql_agent.generate_sql.assert_not_called()


def test_legitimate_empty_checks_are_not_an_error(generator):
    generator.check_agent.generate_checks.return_value = OutputChecks(checks=[])
    result = generator.generate_checks_with_sql("Count?")
    assert result.status == "empty"
    assert result.error is None


@pytest.mark.parametrize("failure", ["exception", "unanswerable", "empty", "error"])
def test_check_sql_generation_failure_is_preserved(generator, failure):
    generator.sql_agent.generate_sql.return_value = OutputSQL(
        answerable=failure != "unanswerable", explanations="",
        query="" if failure == "empty" else "SELECT 1",
        error="Generation error" if failure == "error" else None,
    )
    if failure == "exception":
        generator.sql_agent.generate_sql.side_effect = RuntimeError("Model unavailable")
    result = generator.generate_checks_with_sql("Count?")
    assert result.status == "error"
    assert "Total?" in result.error


@pytest.mark.parametrize("generation_error,expected", [(None, "unchecked"), ("Model unavailable", "error")])
def test_unchecked_and_generation_errors_survive_refinement(validator, generator, generation_error, expected):
    generator.check_agent.generate_checks.return_value = OutputChecks(checks=[], error=generation_error)
    db = MagicMock(spec=DatabaseConnection)
    db.get_database_name.return_value = "adventureworks"
    with patch("agents.multiple.sql_agent_refiner.LoopSqlAgent"), patch(
        "agents.multiple.sql_agent_refiner.CheckQueryGenerator", return_value=generator
    ), patch(
        "agents.multiple.sql_agent_refiner.DataframeValidatorWithHints", return_value=validator
    ), patch("agents.multiple.sql_agent_refiner.get_schema_as_str", return_value="schema"):
        refiner = SqlAgentRefiner(db)
    output = sql_output()
    refiner.loop_agent.process_question.return_value = LoopSqlAgentOutput(
        status="ok", user_question="Count?", initial_generation=output.primary.generation,
        final_result=output.primary.execution, initial_execution=output.primary.execution,
        refinement=None, total_attempts=1,
    )
    result = refiner.refine("Count?")
    assert result.is_valid is False
    assert result.total_steps == 1
    assert result.final_validation_result.overall_valid == expected
    assert result.refinement_steps[0].correction_hint is None
    if generation_error:
        assert generation_error in result.final_validation_result.error
    validator._generate_validation_code.assert_not_called()
    db.execute_read_only_query.assert_not_called()


def test_error_does_not_generate_a_correction_hint(validator):
    validator._generate_hint_from_validations = MagicMock()
    result = validator.generate_correction_hint(
        ValidationResult(overall_valid="error", check_results=[]), "Question", "schema"
    )
    assert result.overall_valid == "error"
    assert result.correction_hint == ""
    validator._generate_hint_from_validations.assert_not_called()
