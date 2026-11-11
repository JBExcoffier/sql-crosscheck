import logging
from typing import List, Literal, Optional, Union
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from agents.multiple.validation.sandbox import ValidationSandbox
from agents.multiple.outputs import CheckWithExecution, SqlAgentWithChecksOutput

import pandas as pd

logger = logging.getLogger(__name__)
ValidationOutcome = Union[bool, Literal["unchecked", "error"]]


@dataclass
class SingleCheckResult:
    check_index: int
    query_question: str
    validation_question: str
    valid: Union[bool, Literal["error"]]
    hint: str
    error: Optional[str]
    executed_code: str
    stdout: str
    stderr: str


@dataclass
class ValidationResult:
    overall_valid: ValidationOutcome
    check_results: List[SingleCheckResult]
    error: Optional[str] = None


@dataclass
class CorrectionHintResult:
    overall_valid: ValidationOutcome
    correction_hint: str
    detailed_feedback: str
    check_results: List[SingleCheckResult]


@dataclass
class HintResult:
    short_hint: str
    detailed_feedback: str


class DataframeValidator:
    """
    Validates SQL query results by comparing main query dataframe with check query dataframes.

    Uses AI to generate validation code from natural language validation questions,
    then executes the code in a sandboxed environment.
    """

    def __init__(
        self,
        model_name: str = "gpt-4.1-mini",
        timeout_seconds: int = 30,
        validation_retries: int = 1,
    ):
        self.model = OpenAIChatModel(model_name=model_name)
        self.sandbox = ValidationSandbox(timeout_seconds=timeout_seconds)

        self.validation_retries = validation_retries
        self.max_total_attempts = self.validation_retries + 1

        # Agent to generate validation code (static system prompt; dynamic per run)
        class CodeOutput(BaseModel):
            code: str

        @dataclass
        class CodeDeps:
            main_cols: List[str]
            check_cols: List[str]

        self._CodeDeps = CodeDeps
        self.code_agent = Agent[CodeDeps, CodeOutput](
            model=self.model,
            deps_type=CodeDeps,
            output_type=CodeOutput,
            system_prompt=(
                """# OBJECTIVE 
You are a data analysis that needs to answer a query in a boolean fashion.

To do so, you have access to two dataframes : 
- main_df: DataFrame from the main SQL query result
- check_df: DataFrame from a check SQL query result 


# METHOD
1. Manipulate the dataframes as needed to perform the validation
2. You have access to only pandas, numpy, and basic Python. These packages are already imported for you. Do NOT import them again.
3. Return a dict with "valid" (bool) and "hint" (str) keys
4. Store the result in a variable called 'result'

Example:
```python
# Check if totals match
main_total = main_df['count'].sum() if 'count' in main_df.columns else 0
check_total = check_df['count'].sum() if 'count' in check_df.columns else 0
valid = abs(main_total - check_total) < 0.001
hint = f"Main total: {{main_total}}, Check total: {{check_total}}" if not valid else "Totals match"
result = {{"valid": valid, "hint": hint}}
"""
            ),
        )

        # Agent to generate correction hints (static system prompt; dynamic per run)
        class HintOutput(BaseModel):
            short_hint: str
            detailed_feedback: str

        @dataclass
        class HintDeps:
            schema: str
            user_question: str
            context_text: str

        self._HintDeps = HintDeps
        self.hint_agent = Agent[HintDeps, HintOutput](
            model=self.model,
            deps_type=HintDeps,
            output_type=HintOutput,
            system_prompt=(
                """You are a SQL expert helping to correct a query.

# OBJECTIVE
Generate a SHORT (preferably one sentence, maximum two sentences) comment/hint that can be added to the original question to help produce correct results.

Focus on:
- The specific issues found in validations
- Concise, actionable guidance
- NOT technical SQL details, but readable guidance

# OUTPUT FORMAT
Respond ONLY in JSON with this structure:
{{
  "short_hint": "<one sentence to add to the question>",
  "detailed_feedback": "<more detailed explanation of what's wrong>"
}}

Do not include any other text."""
            ),
        )

    def _generate_validation_code(
        self,
        validation_question: str,
        main_df: pd.DataFrame,
        check_df: pd.DataFrame,
        error_context: str = "",
    ) -> str:
        """Generate Python code for dataframe validation based on natural language question."""

        run_prompt = f"""# OBJECTIVE 
# INPUT
You need to manipulate and transform these two dataframes to be able to verify and answer the validation question :
- validation_question: "{validation_question}"

Here are some details about the dataframes :
- main_df column names : "{main_df.columns.to_list()}"
- check_df column names : "{check_df.columns.to_list()}"

Previous attempt error (if any): "{error_context}"

Generate Python validation code and output : 
- Use only pandas/numpy/built-ins. No markdown fences.
- Store the result in a variable called 'result' with keys valid (bool) and hint (str). 
"""

        result = self.code_agent.run_sync(
            run_prompt,
            deps=self._CodeDeps(
                main_cols=list(main_df.columns.to_list()),
                check_cols=list(check_df.columns.to_list()),
            ),
        )

        code = result.output.code.strip()
        # Clean up the code (remove markdown if present)
        if code.startswith("```python"):
            code = code[9:]
        if code.endswith("```"):
            code = code[:-3]
        return code.strip()

    def validate_sql_agent_output(
        self, sql_agent_output: SqlAgentWithChecksOutput
    ) -> ValidationResult:
        """
        Args:
            sql_agent_output: Result

        Returns:
            ValidationResult with overall_valid and check_results
        """

        # Main query dataframe
        main_execution = sql_agent_output.primary.execution
        if main_execution is None:
            return ValidationResult(
                overall_valid="error",
                check_results=[],
                error="Main query did not return an execution result",
            )
        if main_execution.status != "ok":
            return ValidationResult(
                overall_valid="error",
                check_results=[],
                error="Main query execution failed",
            )

        main_df = main_execution.dataframe

        if main_df is None:
            return ValidationResult(
                overall_valid="error",
                check_results=[],
                error="Main query did not return a dataframe",
            )

        if sql_agent_output.check_generation_error:
            return ValidationResult(
                overall_valid="error",
                check_results=[],
                error=f"Check generation failed: {sql_agent_output.check_generation_error}",
            )

        if len(main_df) > self.sandbox.max_rows:
            return ValidationResult(
                overall_valid="error",
                check_results=[],
                error=f"Main dataframe exceeds validation limit of {self.sandbox.max_rows} rows",
            )

        if not sql_agent_output.checks:
            return ValidationResult(overall_valid="unchecked", check_results=[])

        # Process each check
        check_results: List[SingleCheckResult] = []
        checks = sql_agent_output.checks

        for i, check in enumerate(checks):
            check_result = self._validate_single_check(check, main_df, i)
            check_results.append(check_result)

        # Determine overall validity
        valid_statuses = [r.valid for r in check_results]
        if any(v == "error" for v in valid_statuses):
            overall_valid = "error"
        elif all(v is True for v in valid_statuses):
            overall_valid = True
        else:
            overall_valid = False

        return ValidationResult(
            overall_valid=overall_valid, check_results=check_results
        )

    def _validate_single_check(
        self, check: CheckWithExecution, main_df: pd.DataFrame, check_index: int
    ) -> SingleCheckResult:
        """Validate a single check against the main dataframe."""

        query_question = check.query_question
        validation_question = check.validation_question
        check_execution = check.execution

        # Check if check query executed successfully
        if check_execution is None or check_execution.status != "ok":
            err_msg = check_execution.error if check_execution is not None else None
            return SingleCheckResult(
                check_index=check_index,
                query_question=query_question,
                validation_question=validation_question,
                valid="error",
                hint="",
                error=f"Check query execution failed: {err_msg if err_msg else 'Unknown error'}",
                executed_code="",
                stdout="",
                stderr="",
            )

        check_df = check_execution.dataframe

        if check_df is None:
            return SingleCheckResult(
                check_index=check_index,
                query_question=query_question,
                validation_question=validation_question,
                valid="error",
                hint="",
                error="Check query did not return a dataframe",
                executed_code="",
                stdout="",
                stderr="",
            )

        if len(check_df) > self.sandbox.max_rows:
            return SingleCheckResult(
                check_index=check_index,
                query_question=query_question,
                validation_question=validation_question,
                valid="error",
                hint="",
                error=f"Check dataframe exceeds validation limit of {self.sandbox.max_rows} rows",
                executed_code="",
                stdout="",
                stderr="",
            )

        last_error = ""
        generated_code = ""
        stdout, stderr = "", ""
        for attempt in range(self.max_total_attempts):
            try:
                generated_code = self._generate_validation_code(
                    validation_question=validation_question,
                    main_df=main_df,
                    check_df=check_df,
                    error_context=last_error,
                )
                logger.info(
                    f"Generated validation code for check {check_index}, attempt {attempt}:\n{generated_code}"
                )
            except Exception as e:
                last_error = f"Code generation failed: {str(e)}"
                logger.error(
                    f"Failed to generate validation code for check {check_index} (attempt {attempt}): {e}"
                )
                continue

            try:
                sandbox_result = self.sandbox.execute_validation(
                    main_df=main_df,
                    check_df=check_df,
                    validation_question=validation_question,
                    generated_code=generated_code,
                )
                stdout, stderr = sandbox_result.stdout, sandbox_result.stderr

                logger.info(
                    f"Sandbox execution result for check {check_index} (attempt {attempt}): {sandbox_result.status}"
                )
                logger.info(f"Generated code:\n{sandbox_result.executed_code}")
                logger.info(f"Stdout: {sandbox_result.stdout}")
                logger.info(f"Stderr: {sandbox_result.stderr}")

                if sandbox_result.status == "success":
                    return SingleCheckResult(
                        check_index=check_index,
                        query_question=query_question,
                        validation_question=validation_question,
                        valid=sandbox_result.valid,
                        hint=sandbox_result.hint,
                        error=sandbox_result.error,
                        executed_code=sandbox_result.executed_code,
                        stdout=sandbox_result.stdout,
                        stderr=sandbox_result.stderr,
                    )

                last_error = (
                    sandbox_result.error or f"Validation attempt {attempt} failed"
                )
            except Exception as e:
                last_error = f"Sandbox execution failed: {str(e)}"
                logger.error(f"Sandbox execution failed for check {check_index}: {e}")

        # All attempts failed
        return SingleCheckResult(
            check_index=check_index,
            query_question=query_question,
            validation_question=validation_question,
            valid="error",
            hint="",
            error=last_error,
            executed_code=generated_code,
            stdout=stdout,
            stderr=stderr,
        )


class DataframeValidatorWithHints(DataframeValidator):
    """
    Extends DataframeValidator to generate actionable hints for query correction
    when validation fails (overall_valid is False).

    Generates a human-readable, concise comment that can be added to the initial
    user question to produce correct results.
    """

    def generate_correction_hint(
        self, validation_result: ValidationResult, user_question: str, schema: str
    ) -> CorrectionHintResult:
        """
        Generate a concise correction hint based on failed validations.

        Args:
            validation_result: Output from validate_sql_agent_output()
            user_question: Original user question
            schema: Database schema text

        Returns:
            CorrectionHintResult with correction_hint and detailed_feedback
        """

        # Only failed checks can provide feedback for refining the question.
        if validation_result.overall_valid is not False:
            return CorrectionHintResult(
                overall_valid=validation_result.overall_valid,
                correction_hint="",
                detailed_feedback="",
                check_results=validation_result.check_results,
            )

        # If no check results or all errors, return error state
        check_results = validation_result.check_results
        if not check_results or all(r.valid == "error" for r in check_results):
            return CorrectionHintResult(
                overall_valid="error",
                correction_hint="",
                detailed_feedback="Could not generate validation hints - all checks failed",
                check_results=check_results,
            )

        # Filter failed validations (hint is False, not error)
        failed_checks = [r for r in check_results if r.valid is False and r.hint]

        if not failed_checks:
            return CorrectionHintResult(
                overall_valid=False,
                correction_hint="",
                detailed_feedback="",
                check_results=check_results,
            )

        # Generate correction hint using LLM
        hint = self._generate_hint_from_validations(
            user_question=user_question, schema=schema, failed_checks=failed_checks
        )

        return CorrectionHintResult(
            overall_valid=False,
            correction_hint=hint.short_hint,
            detailed_feedback=hint.detailed_feedback,
            check_results=check_results,
        )

    def _generate_hint_from_validations(
        self, user_question: str, schema: str, failed_checks: list
    ) -> HintResult:
        """Use LLM to generate actionable hints from failed validations."""

        # Prepare context of failed validations
        validation_context = []
        for check in failed_checks:
            validation_context.append(
                f"- Check: {check.query_question}\n"
                f"  Validation: {check.validation_question}\n"
                f"  Issue: {check.hint}"
            )
        context_text = "\n".join(validation_context)

        run_prompt = f"""Here is the validation question, context and validation failure details :

# CONTEXT
Database schema:
{schema}

# ORIGINAL QUESTION
{user_question}

# VALIDATION FAILURES
{context_text}

Generate a concise hint and a detailed feedback based on validation failures."""

        try:
            result = self.hint_agent.run_sync(
                run_prompt,
                deps=self._HintDeps(
                    schema=schema,
                    user_question=user_question,
                    context_text=context_text,
                ),
            )

            return HintResult(
                short_hint=result.output.short_hint,
                detailed_feedback=result.output.detailed_feedback,
            )

        except Exception as e:
            logger.error(f"Failed to generate correction hint: {e}")
            # Fallback: construct hint from validation hints
            hints = [c.hint for c in failed_checks if c.hint]
            fallback_hint = " | ".join(hints) if hints else ""
            return HintResult(short_hint=fallback_hint, detailed_feedback=fallback_hint)
