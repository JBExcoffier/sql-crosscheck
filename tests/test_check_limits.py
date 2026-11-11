from unittest.mock import MagicMock

import pytest
from pydantic_ai.models.test import TestModel

from agents.checker import CheckQuestionsAgent
from agents.multiple.checks_generator import CheckQueryGenerator
from database.connection import DatabaseConnection


@pytest.mark.parametrize("limit", [1, 3, 5])
def test_requested_limit_reaches_checker_prompt_and_sql_generation(monkeypatch, limit):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    db = MagicMock(spec=DatabaseConnection)
    db.get_database_name.return_value = "adventureworks"
    generator = CheckQueryGenerator(db, check_limit=limit)
    checks = TestModel(custom_output_args={"checks": [
        {"query_question": f"Check {index}?", "validation_question": "Totals match?"}
        for index in range(5)
    ]})
    sql = TestModel(custom_output_args={
        "answerable": True, "query": "SELECT 1", "explanations": "Check count",
    })
    prompt = generator.check_agent._build_prompt("Question")
    assert f"<CHECK_LIMIT>\n{limit}\n</CHECK_LIMIT>" in prompt
    with generator.check_agent.agent.override(model=checks), generator.sql_agent.agent.override(model=sql):
        result = generator.generate_checks_with_sql("Question")
    assert result.status == "ok"
    assert len(result.checks) == limit


@pytest.mark.parametrize("limit", [0, 6])
@pytest.mark.parametrize("agent_type", [CheckQuestionsAgent, CheckQueryGenerator])
def test_invalid_check_limits_are_rejected(agent_type, limit):
    with pytest.raises(ValueError, match="between 1 and 5"):
        agent_type(MagicMock(spec=DatabaseConnection), check_limit=limit)
