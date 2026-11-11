from typing import List, Literal, Optional

from pydantic import BaseModel

from agents.checker import (
    MAX_CHECKS,
    CheckQuestionsAgent,
    OutputChecks,
    OutputSingleCheck,
)
from agents.straightforward import StraightforwardWithSchemaSqlAgent
from agents.multiple.outputs import CheckWithSql, OutputSQL


class CheckQueryGeneratorOutput(BaseModel):
    user_question: str
    status: Literal["ok", "empty", "error"]
    checks: List[CheckWithSql]
    error: Optional[str] = None


class CheckQueryGenerator:
    """
    Orchestrates generation of validation check questions and their corresponding SQL queries.

    - Uses CheckQuestionsAgent to produce natural-language check questions based on the user's question and schema
    - Uses StraightforwardWithSchemaSqlAgent to generate SQL for each check question
    - Limits the number of checks to `check_limit` (>=1)
    - Handles the case where no check questions are needed/returned
    """

    def __init__(
        self,
        db_connection,
        model_name: str = "gpt-4.1-mini",
        check_limit: int = 3,
    ) -> None:
        if not 1 <= check_limit <= MAX_CHECKS:
            raise ValueError(f"check_limit must be between 1 and {MAX_CHECKS}")

        self.db_connection = db_connection
        self.check_limit = check_limit
        self.check_agent = CheckQuestionsAgent(
            model_name=model_name, db_connection=self.db_connection, check_limit=check_limit
        )
        self.sql_agent = StraightforwardWithSchemaSqlAgent(
            model_name=model_name, db_connection=self.db_connection
        )

    def generate_checks_with_sql(self, user_question: str) -> CheckQueryGeneratorOutput:
        """
        Generate up to `check_limit` check questions and corresponding SQL queries.

        Returns CheckQueryGeneratorOutput with:
        - user_question: Original question
        - status: 'ok' if checks generated, 'empty' if none, 'error' on generation failure
        - checks: List of CheckWithSql objects
        """
        checks_result: OutputChecks = self.check_agent.generate_checks(user_question)
        if checks_result.error:
            return CheckQueryGeneratorOutput(
                user_question=user_question,
                status="error",
                checks=[],
                error=checks_result.error,
            )
        checks_list: List[OutputSingleCheck] = checks_result.checks or []

        if not checks_list:
            return CheckQueryGeneratorOutput(
                user_question=user_question,
                status="empty",
                checks=[],
            )

        limited_checks = checks_list[: self.check_limit]
        output_checks: List[CheckWithSql] = []

        for check in limited_checks:
            query_question = (check.query_question or "").strip()
            validation_question = (check.validation_question or "").strip()
            if not query_question or not validation_question:
                continue

            try:
                sql_gen: OutputSQL = self.sql_agent.generate_sql(query_question)
                if sql_gen.error or not sql_gen.answerable or not sql_gen.query.strip():
                    raise ValueError(sql_gen.error or "No answerable check SQL generated")
            except Exception as exc:
                return CheckQueryGeneratorOutput(
                    user_question=user_question,
                    status="error",
                    checks=output_checks,
                    error=f"Check SQL generation failed for '{query_question}': {exc}",
                )

            output_checks.append(
                CheckWithSql(
                    query_question=query_question,
                    validation_question=validation_question,
                    sql_generation=sql_gen,
                )
            )

        return CheckQueryGeneratorOutput(
            user_question=user_question,
            status="ok" if output_checks else "empty",
            checks=output_checks,
        )
