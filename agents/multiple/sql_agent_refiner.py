from typing import List, Optional, Literal, Callable

from pydantic import BaseModel, ConfigDict

from agents.multiple.loop import LoopSqlAgent, LoopSqlAgentOutput
from agents.multiple.checks_generator import (
    CheckQueryGenerator,
    CheckQueryGeneratorOutput,
)
from agents.multiple.validation.dataframe_validator import (
    DataframeValidatorWithHints,
    ValidationResult,
)
from agents.multiple.outputs import (
    PrimaryResult,
    CheckWithExecution,
    SqlAgentWithChecksOutput,
)
from database.connection import DatabaseConnection
from utils.schema import get_schema_as_str


class RefinementStep(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    step_number: int
    user_question: str
    sql_result: SqlAgentWithChecksOutput
    is_valid: bool
    validation_result: Optional[ValidationResult] = None
    correction_hint: Optional[str] = None


class SqlAgentRefinerOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "not_ok", "unanswerable"]
    final_question: str
    refinement_steps: List[RefinementStep]
    total_steps: int
    is_valid: bool
    final_sql_result: Optional[SqlAgentWithChecksOutput] = None
    final_validation_result: Optional[ValidationResult] = None


class SqlAgentRefiner:
    """
    High-level orchestrator that refines SQL queries based on validation feedback.

    Workflow:
    1. Generate SQL (and internal retries)
    2. Generate check questions + SQL
    3. Validate results
    4. If validation fails, refine question by combining initial question + correction hint
    5. Repeat up to max_refinement_steps
    6. Return final result when validation passes or max steps reached

    Inputs:
    - db_connection: an active DatabaseConnection (connected)
    - model_name: underlying OpenAI model
    - check_limit: maximum number of check queries to generate
    - max_inner_primary_retries: maximum number of refinement attempts for primary query (default: 3)
    - max_refinement_steps: maximum additional attempts (0 disables refinement; None uses 3)
    - recompute_checks: if False, checks are generated only once from initial question and reused (default: True)
    """

    def __init__(
        self,
        db_connection: DatabaseConnection,
        model_name: str = "gpt-4.1-mini",
        check_limit: int = 3,
        max_inner_primary_retries: int = 3,
        max_refinement_steps: Optional[int] = 3,
        recompute_checks: bool = True,
    ) -> None:
        if check_limit < 1:
            raise ValueError("check_limit must be >= 1")
        if max_refinement_steps is not None and max_refinement_steps < 0:
            raise ValueError("max_refinement_steps must be >= 0 or None")

        self.db_connection = db_connection
        self.db_name = self.db_connection.get_database_name()
        self.model_name = model_name
        self.check_limit = check_limit
        self.max_inner_primary_retries = max_inner_primary_retries
        self.max_refinement_steps = (
            max_refinement_steps if max_refinement_steps is not None else 3
        )
        self.recompute_checks = recompute_checks

        # Core agents
        self.loop_agent = LoopSqlAgent(
            db_connection=db_connection,
            model_name=model_name,
            max_retries=self.max_inner_primary_retries,
        )
        self.check_generator = CheckQueryGenerator(
            db_connection=db_connection, model_name=model_name, check_limit=check_limit
        )

        self.validator = DataframeValidatorWithHints(model_name=model_name)
        self.schema = get_schema_as_str(db_name=self.db_name)

    def refine(
        self,
        user_question: str,
        as_dataframe: bool = True,
        step_callback: Optional[Callable[[RefinementStep], None]] = None,
    ) -> SqlAgentRefinerOutput:
        """
        Refine SQL query based on validation feedback.

        Args:
            user_question: Initial user question in natural language
            as_dataframe: Whether to return results as pandas DataFrames

        Returns:
            SqlAgentRefinerOutput with final results and all refinement steps
        """
        initial_question = user_question
        refinement_steps: List[RefinementStep] = []
        current_question = initial_question

        # Reuse checks within this run only, never across separate questions.
        check_results: Optional[CheckQueryGeneratorOutput] = None

        sql_result: Optional[LoopSqlAgentOutput] = None
        validation_result: Optional[ValidationResult] = None

        for step_num in range(self.max_refinement_steps + 1):
            # Step 1: Generate SQL (LoopSqlAgent handles its own internal retries)
            sql_result = self.loop_agent.process_question(current_question)

            # If question is unanswerable, stop immediately
            if sql_result.status == "rejected":
                step = RefinementStep(
                    step_number=step_num,
                    user_question=current_question,
                    sql_result=SqlAgentWithChecksOutput(
                        status=sql_result.status,
                        user_question=current_question,
                        primary=PrimaryResult(
                            generation=sql_result.final_generation
                            or sql_result.initial_generation,
                            execution=sql_result.final_result,
                        ),
                        checks=[],
                    ),
                    validation_result=None,
                    correction_hint=None,
                    is_valid=False,
                )
                refinement_steps.append(step)
                if step_callback:
                    try:
                        step_callback(step)
                    except Exception:
                        pass
                return SqlAgentRefinerOutput(
                    status="unanswerable",
                    final_question=current_question,
                    final_sql_result=None,
                    final_validation_result=None,
                    refinement_steps=refinement_steps,
                    total_steps=step_num + 1,
                    is_valid=False,
                )

            # Step 2: Validate results
            # Generate validation checks (only once if recompute_checks=False)
            if self.recompute_checks or check_results is None:
                check_results = self.check_generator.generate_checks_with_sql(
                    user_question
                )

            # Build proper structures for validator using dataclasses from dataframe_validator
            combined_output_result = self._run_checks_and_build_output_for_validation(
                sql_result=sql_result,
                check_results=check_results,
                as_dataframe=as_dataframe,
                user_question=current_question,
            )

            validation_result = self.validator.validate_sql_agent_output(
                combined_output_result
            )

            # Step 3: Check if validation passed
            is_valid = validation_result.overall_valid is True

            # Step 4: Generate correction hint if validation failed
            correction_hint = None
            if validation_result.overall_valid is False:
                hint_result = self.validator.generate_correction_hint(
                    validation_result=validation_result,
                    user_question=current_question,
                    schema=self.schema,
                )
                correction_hint = hint_result.correction_hint

            # Record this step
            step = RefinementStep(
                step_number=step_num,
                user_question=current_question,
                sql_result=combined_output_result,
                validation_result=validation_result,
                correction_hint=correction_hint,
                is_valid=is_valid,
            )
            refinement_steps.append(step)
            if step_callback:
                try:
                    step_callback(step)
                except Exception:
                    pass

            # If validation passed, return success
            if is_valid:
                return SqlAgentRefinerOutput(
                    status="ok",
                    final_question=current_question,
                    final_sql_result=combined_output_result,
                    final_validation_result=validation_result,
                    refinement_steps=refinement_steps,
                    total_steps=step_num + 1,
                    is_valid=True,
                )

            # If we've reached max steps, stop
            if step_num >= self.max_refinement_steps:
                break

            # If no correction hint, cannot refine further
            if not correction_hint or not correction_hint.strip():
                break

            # Refine question by combining initial question + correction hint
            current_question = f"{initial_question}\n\n{correction_hint}"

        # Max steps reached or no more hints - return NOT ok
        return SqlAgentRefinerOutput(
            status="not_ok",
            final_question=current_question,
            final_sql_result=combined_output_result,
            final_validation_result=validation_result,
            refinement_steps=refinement_steps,
            total_steps=len(refinement_steps),
            is_valid=False,
        )

    def _run_checks_and_build_output_for_validation(
        self,
        sql_result,
        check_results: Optional[CheckQueryGeneratorOutput],
        as_dataframe: bool,
        user_question: str,
    ) -> SqlAgentWithChecksOutput:
        """Adapt LoopSqlAgentOutput + run checks into SqlAgentWithChecksOutput for validator."""
        primary_generation = (
            sql_result.final_generation or sql_result.initial_generation
        )
        primary = PrimaryResult(
            generation=primary_generation,
            execution=sql_result.final_result,
        )

        checks_executions: List[CheckWithExecution] = []
        if check_results and check_results.status == "ok":
            for ch in check_results.checks:
                query = (ch.sql_generation.query or "").strip()
                exec_res = (
                    self.db_connection.execute_read_only_query(
                        query, as_dataframe=as_dataframe
                    )
                    if query
                    else None
                )
                checks_executions.append(
                    CheckWithExecution(
                        query_question=ch.query_question,
                        validation_question=ch.validation_question,
                        sql_generation=ch.sql_generation,
                        execution=exec_res,
                    )
                )

        return SqlAgentWithChecksOutput(
            status=sql_result.status,
            user_question=user_question,
            primary=primary,
            checks=checks_executions,
            check_generation_error=check_results.error if check_results else None,
        )
