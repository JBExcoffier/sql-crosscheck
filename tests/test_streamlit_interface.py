from unittest.mock import patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from agents.multiple.outputs import OutputSQL, PrimaryResult, SqlAgentWithChecksOutput
from agents.multiple.sql_agent_refiner import RefinementStep, SqlAgentRefinerOutput
from agents.multiple.validation.dataframe_validator import SingleCheckResult, ValidationResult
from database.connection import QueryExecutionResult


def make_result(outcome=True):
    validation = ValidationResult(
        overall_valid=outcome,
        check_results=[] if outcome == "unchecked" else [SingleCheckResult(
            check_index=0, query_question="Total employees?",
            validation_question="Do the totals match?", valid=outcome,
            hint="Check department counts" if outcome is False else "",
            error="Validation timed out" if outcome == "error" else None,
            executed_code="", stdout="", stderr="",
        )],
    )
    sql_result = SqlAgentWithChecksOutput(
        status="ok", user_question="Employees per department?",
        primary=PrimaryResult(
            generation=OutputSQL(
                answerable=True, explanations="Count employees", query="SELECT 1 AS count"
            ),
            execution=QueryExecutionResult(
                status="ok", row_count=1, dataframe=pd.DataFrame({"count": [1]})
            ),
        ),
        checks=[],
    )
    step = RefinementStep(
        step_number=0, user_question=sql_result.user_question,
        sql_result=sql_result, is_valid=outcome is True,
        validation_result=validation,
        correction_hint="Use the department breakdown" if outcome is False else None,
    )
    return SqlAgentRefinerOutput(
        status="ok" if outcome is True else "not_ok",
        final_question=sql_result.user_question, refinement_steps=[step],
        total_steps=1, is_valid=outcome is True,
        final_sql_result=sql_result, final_validation_result=validation,
    )


def run_app(result, failure=None):
    def refine(question, as_dataframe, step_callback):
        if failure == "processing":
            raise RuntimeError("Processing unavailable")
        for step in result.refinement_steps:
            step_callback(step)
        return result

    with patch("database.connection.DatabaseConnection") as database, patch(
        "agents.multiple.sql_agent_refiner.SqlAgentRefiner"
    ) as refiner:
        if failure == "initialization":
            refiner.side_effect = RuntimeError("Initialization unavailable")
        refiner.return_value.refine.side_effect = refine
        app = AppTest.from_file("interface/main.py").run()
        app.text_area[0].set_value("Employees per department?")
        app.button[0].click().run()
        database.return_value.connect.return_value.close.assert_called_once()
    assert not app.exception
    return app


@pytest.mark.parametrize("outcome,element,message", [
    (True, "success", "Checks passed"),
    (False, "warning", "Checks failed"),
    ("unchecked", "warning", "Unchecked"),
    ("error", "error", "Validation error"),
])
def test_validation_outcomes_display_in_progress_and_final_result(outcome, element, message):
    app = run_app(make_result(outcome))
    assert sum(message in item.value for item in app.get(element)) == 2
    assert len(app.dataframe) == 1
    assert any("SELECT 1 AS count" in item.value for item in app.code)
    if outcome is not True:
        assert not app.success


def test_refinement_keeps_steps_checks_and_correction_hints_visible():
    failed = make_result(False)
    result = make_result(True)
    result.refinement_steps[0].step_number = 1
    result.refinement_steps.insert(0, failed.refinement_steps[0])
    result.total_steps = 2
    app = run_app(result)
    labels = [item.label for item in app.expander]
    assert any(label.startswith("Step 1:") for label in labels)
    assert any(label.startswith("Step 2:") for label in labels)
    assert any("Total employees?" in label for label in labels)
    assert any("Use the department breakdown" in item.value for item in app.markdown)
    assert any("Do the totals match?" in item.value for item in app.markdown)
    assert any("Check department counts" in item.value for item in app.markdown)


def test_unanswerable_question_does_not_claim_checks_passed():
    result = make_result("unchecked")
    result.status = "unanswerable"
    result.final_sql_result = None
    result.final_validation_result = None
    result.refinement_steps[0].sql_result.status = "rejected"
    result.refinement_steps[0].validation_result = None
    app = run_app(result)
    assert any("unanswerable" in item.value for item in app.warning)
    assert not app.success
    assert not app.dataframe


def test_missing_execution_and_generation_error_are_shown():
    result = make_result("error")
    result.final_sql_result.primary.execution = None
    result.final_sql_result.primary.generation.error = "SQL generation failed"
    app = run_app(result)
    assert any("No execution result" in item.value for item in app.warning)
    assert any("SQL generation failed" in item.value for item in app.error)
    assert not app.success


def test_missing_final_sql_and_validation_do_not_crash():
    result = make_result("error")
    result.final_sql_result = None
    result.final_validation_result = None
    app = run_app(result)
    assert any("Validation not run" in item.value for item in app.info)
    assert any("No SQL result" in item.value for item in app.warning)


@pytest.mark.parametrize("stage", ["initialization", "processing"])
def test_request_errors_are_displayed_and_connection_is_closed(stage):
    app = run_app(make_result(), failure=stage)
    assert any("unavailable" in item.value for item in app.error)
    assert not app.success
