from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from agents.multiple.outputs import OutputSQL
from database.connection import DatabaseConnection
from utils.schema import get_schema_as_str


class StepRefinement(BaseModel):
    attempt: int
    proposed_query: str
    llm_explanation: str
    execution: Dict[str, Any]


class OutputRefiner(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: str
    final_query: str
    final_error: str
    retries_used: int
    steps: List[StepRefinement]


class SqlQueryRefiner:
    """Generate and execute at most max_retries repaired SQL candidates."""

    def __init__(
        self,
        db_connection: DatabaseConnection,
        model_name: str = "gpt-4.1-mini",
        max_retries: int = 3,
    ) -> None:
        self.db_connection = db_connection
        self.max_retries = max_retries if max_retries is not None else 3
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.model = OpenAIChatModel(model_name=model_name)
        self.agent = Agent(
            model=self.model,
            output_type=OutputSQL,
            system_prompt=(
                "Repair the PostgreSQL query using the schema, user question, and database error. "
                "Return one read-only SELECT or CTE query, with a short explanation. "
                "If no repair is possible, set answerable=false and query to an empty string. "
                "Python will execute the query and provide any error on the next attempt."
            ),
        )

    def refine(
        self,
        user_question: str,
        initial_query: str,
        initial_error_message: str,
    ) -> OutputRefiner:
        """Count actual SQL executions and build attempt details from database results."""
        if not self.db_connection.is_connected():
            return OutputRefiner(
                status="error", final_query="",
                final_error="No active database connection. Call connect() first.",
                retries_used=0, steps=[],
            )

        db_name = self.db_connection.get_database_name()
        schema = get_schema_as_str(db_name=db_name)
        current_query = initial_query
        last_error = initial_error_message
        steps = []
        for attempt in range(1, self.max_retries + 1):
            prompt = (
                f"Database: {db_name}\n<SCHEMA>\n{schema}\n</SCHEMA>\n"
                f"<USER_QUESTION>\n{user_question}\n</USER_QUESTION>\n"
                f"<QUERY_TO_FIX>\n{current_query}\n</QUERY_TO_FIX>\n"
                f"<ERROR>\n{last_error}\n</ERROR>"
            )
            try:
                candidate = self.agent.run_sync(prompt).output
            except Exception as exc:
                last_error = f"SQL repair generation failed: {exc}"
                break
            if not candidate.answerable or not candidate.query.strip():
                last_error = candidate.error or candidate.explanations or "No repaired SQL generated"
                break

            current_query = candidate.query.strip()
            execution = self.db_connection.execute_read_only_query(current_query)
            steps.append(StepRefinement(
                attempt=attempt, proposed_query=current_query,
                llm_explanation=candidate.explanations,
                execution={name: getattr(execution, name) for name in (
                    "status", "error_type", "error", "pgcode", "columns", "row_count", "rows"
                )},
            ))
            if execution.status == "ok":
                return OutputRefiner(
                    status="ok", final_query=current_query, final_error="",
                    retries_used=len(steps), steps=steps,
                )
            last_error = execution.error or "SQL execution failed"

        return OutputRefiner(
            status="error", final_query="", final_error=last_error,
            retries_used=len(steps), steps=steps,
        )
