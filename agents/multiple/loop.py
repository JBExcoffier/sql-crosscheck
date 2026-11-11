from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict

from agents.straightforward import StraightforwardWithSchemaSqlAgent
from agents.queryer import SqlQueryRefiner, OutputRefiner
from database.connection import DatabaseConnection, QueryExecutionResult
from agents.multiple.outputs import OutputSQL


class LoopSqlAgentOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "error", "rejected"]
    final_result: Optional[QueryExecutionResult]
    final_generation: Optional[OutputSQL] = None
    initial_generation: OutputSQL
    initial_execution: Optional[QueryExecutionResult]
    refinement: Optional[OutputRefiner]
    total_attempts: int
    user_question: str


class LoopSqlAgent:
    """
    SQL agent that combines initial query generation with automatic error correction.

    Workflow:
    1. Generate initial SQL using StraightforwardWithSchemaSqlAgent
    2. Execute query via DatabaseConnection
    3. If error occurs, use SqlQueryRefiner to fix it up to max_retries
    4. Return comprehensive results including all attempts and final outcome
    """

    def __init__(
        self,
        db_connection: DatabaseConnection,
        model_name: str = "gpt-4.1-mini",
        max_retries: int = 3,
    ):
        """
        Initialize the loop agent.

        Args:
            db_connection: Active database connection for query execution
            model_name: OpenAI model to use for both generation and refinement
            max_retries: Maximum number of refinement attempts (default: 3)
        """
        self.db_connection = db_connection
        self.db_name = self.db_connection.get_database_name()
        self.max_retries = max_retries

        # Initialize the base agent for initial query generation
        self.base_agent = StraightforwardWithSchemaSqlAgent(
            model_name=model_name, db_connection=db_connection
        )

        # Initialize the refiner for error correction
        self.refiner = SqlQueryRefiner(
            db_connection=db_connection, model_name=model_name, max_retries=max_retries
        )

    def process_question(self, question: str) -> LoopSqlAgentOutput:
        """
        Process a natural language question through the complete loop workflow.

        Returns comprehensive results including:
        - Initial query generation
        - Execution results
        - Refinement attempts (if needed)
        - Final outcome
        """

        # Step 1: Generate initial SQL query
        try:
            initial_result = self.base_agent.generate_sql(question)
        except Exception as e:
            return LoopSqlAgentOutput(
                status="error",
                final_result=None,
                initial_generation=OutputSQL(
                    explanations="",
                    answerable=False,
                    query="",
                    error=str(e),
                ),
                initial_execution=None,
                refinement=None,
                total_attempts=1,
                user_question=question,
            )

        # Check if question was deemed unanswerable
        if not initial_result.answerable:
            return LoopSqlAgentOutput(
                status="rejected",
                final_result=None,
                initial_generation=OutputSQL(
                    explanations=initial_result.explanations,
                    answerable=initial_result.answerable,
                    query=initial_result.query,
                    error=None,
                ),
                initial_execution=None,
                refinement=None,
                total_attempts=1,
                user_question=question,
            )

        initial_query = initial_result.query.strip()
        if not initial_query:
            return LoopSqlAgentOutput(
                status="error",
                final_result=None,
                initial_generation=OutputSQL(
                    explanations=initial_result.explanations,
                    answerable=initial_result.answerable,
                    query=initial_result.query,
                    error=None,
                ),
                initial_execution=None,
                refinement=None,
                total_attempts=1,
                user_question=question,
            )

        # Step 2: Execute initial query
        initial_execution = self.db_connection.execute_read_only_query(initial_query)

        # Step 3: Check if refinement is needed
        if initial_execution.status == "ok":
            # Success on first try
            return LoopSqlAgentOutput(
                status="ok",
                final_result=initial_execution,
                final_generation=OutputSQL(
                    explanations=initial_result.explanations,
                    answerable=initial_result.answerable,
                    query=initial_query,
                    error=None,
                ),
                initial_generation=OutputSQL(
                    explanations=initial_result.explanations,
                    answerable=initial_result.answerable,
                    query=initial_result.query,
                    error=None,
                ),
                initial_execution=initial_execution,
                refinement=None,
                total_attempts=1,
                user_question=question,
            )

        # Step 4: Use refiner to fix the query with Pydantic AI refiner
        refinement_result = self.refiner.refine(
            user_question=question,
            initial_query=initial_query,
            initial_error_message=initial_execution.error,
        )

        # Step 5: Compile final results
        total_attempts = 1 + refinement_result.retries_used

        if refinement_result.status == "ok":
            final_query = refinement_result.final_query
            # Execute the returned SQL ourselves: model-reported rows and statuses
            # are not database results, and do not provide a usable dataframe.
            final_execution = self.db_connection.execute_read_only_query(final_query)
            total_attempts += 1

            # Derive final_generation explanation from last refinement step when available
            final_explanation = initial_result.explanations
            if (
                refinement_result
                and refinement_result.steps
                and refinement_result.steps[-1].llm_explanation
            ):
                final_explanation = refinement_result.steps[-1].llm_explanation

            return LoopSqlAgentOutput(
                status="ok" if final_execution.status == "ok" else "error",
                final_result=final_execution,
                final_generation=OutputSQL(
                    explanations=final_explanation,
                    answerable=True,
                    query=final_query,
                    error=None,
                ),
                initial_generation=OutputSQL(
                    explanations=initial_result.explanations,
                    answerable=initial_result.answerable,
                    query=initial_result.query,
                    error=None,
                ),
                initial_execution=initial_execution,
                refinement=refinement_result,
                total_attempts=total_attempts,
                user_question=question,
            )
        else:
            # Refinement failed
            final_execution = initial_execution
            return LoopSqlAgentOutput(
                status=(
                    refinement_result.status
                    if refinement_result.status != "ok"
                    else "error"
                ),
                final_result=final_execution,
                final_generation=None,
                initial_generation=OutputSQL(
                    explanations=initial_result.explanations,
                    answerable=initial_result.answerable,
                    query=initial_result.query,
                    error=None,
                ),
                initial_execution=initial_execution,
                refinement=refinement_result,
                total_attempts=total_attempts,
                user_question=question,
            )
