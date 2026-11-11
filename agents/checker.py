from typing import List, Optional

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from utils.schema import get_schema_as_str
from database.connection import DatabaseConnection

MAX_CHECKS = 5


class OutputSingleCheck(BaseModel):
    query_question: str
    validation_question: str


class ChecksOutput(BaseModel):
    checks: List[OutputSingleCheck]


class OutputChecks(BaseModel):
    checks: List[OutputSingleCheck]
    error: Optional[str] = None


class CheckDeps(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    schema_text: Optional[str] = None
    db_connection: Optional[DatabaseConnection] = None


class CheckQuestionsAgent:
    """
    Generates auxiliary natural-language check questions (not SQL) that help validate
    whether an answer/query to a user's question is likely correct.

    Inputs:
    - Database name (used to load schema YAML)
    - User question

    Output:
    - A typed response containing a list of natural-language check questions. The list
      may be empty if no additional checks are deemed necessary.
    """

    def __init__(
        self,
        db_connection: DatabaseConnection,
        model_name: str = "gpt-4.1-mini",
        check_limit: int = 3,
    ) -> None:
        if not 1 <= check_limit <= MAX_CHECKS:
            raise ValueError(f"check_limit must be between 1 and {MAX_CHECKS}")
        self.check_limit = check_limit
        self.db_connection = db_connection
        self.db_name = self.db_connection.get_database_name()
        self.schema_text = get_schema_as_str(db_name=self.db_name)
        self.model_name = model_name
        self.model = OpenAIChatModel(model_name=model_name)
        self.agent = Agent[CheckDeps, ChecksOutput](
            model=self.model,
            deps_type=CheckDeps,
            output_type=ChecksOutput,
            system_prompt=(
                """
            # OBJECTIVE
            You are a Data Analysis expert using SQL.
            You have as input infos about a database (<SCHEMA>) and an input question (<USER_QUESTION>).

            You objective is to generate additional NATURAL-LANGUAGE CHECK QUESTIONS that can then be transformed into SQL queries 
            that can help validate a proposed query answer to the input question.

            You must answer for each check question a query question (query_question) and a way of validating a proposed query (validation_question).
            
            
            # EXAMPLES
            For-example if input question is "yearly evolution of total number of users in France", check questions could be the following :
            { 
                "checks": 
                    [
                        {"query_question":"total number of users in France", "validation_question":"number of France users for each year must be always inferior or equal to total number of France users"}
                    ] 
            }


            # REQUIREMENTS
            - Check questions must be simple and if possible only looking at aggregations and NOT individual records.
            - Do NOT output SQL. Only natural-language questions.
            - Do NOT return DUPLICATED check question. They all must be different.
            - Limit the output to the requested maximum in <CHECK_LIMIT>.
            - If no checks are needed, return an empty list.
            - Order the check question by DECREASING important (the first check question is the most important).


            # OUTPUT
            Respond ONLY in strict JSON with this exact structure:
            { "checks": [{"query_question":"<question 1>", "validation_question":"<validation 1>"}, 
                        {"query_question":"<question 2>", "validation_question":"<validation 2>"}, 
                        ...] }
            """
            ),
        )

    def _build_prompt(self, user_question: str) -> str:
        parts: List[str] = []
        parts.append(f"You have the following database name: '{self.db_name}'.")
        parts.append(f"\n<CHECK_LIMIT>\n{self.check_limit}\n</CHECK_LIMIT>\n")
        if self.schema_text:
            parts.append(
                f"\n<SCHEMA>\nThe database schema is as follows :\n\n{self.schema_text}\n\n</SCHEMA>\n"
            )
        parts.append(
            f"\n<USER_QUESTION>\nThe question to answer is :\n{user_question}\n</USER_QUESTION>"
        )
        return "".join(parts)

    def generate_checks(self, user_question: str) -> OutputChecks:
        """
        Generate a list of natural-language check questions based on the user question
        and the database schema.
        """
        prompt = self._build_prompt(user_question)
        deps = CheckDeps(schema_text=self.schema_text, db_connection=self.db_connection)

        try:
            result = self.agent.run_sync(prompt, deps=deps)
            normalized = [
                OutputSingleCheck(
                    query_question=item.query_question.strip(),
                    validation_question=item.validation_question.strip(),
                )
                for item in (result.output.checks or [])
                if item.query_question.strip() and item.validation_question.strip()
            ]
            return OutputChecks(checks=normalized[:self.check_limit])
        except Exception as e:
            return OutputChecks(checks=[], error=str(e))
