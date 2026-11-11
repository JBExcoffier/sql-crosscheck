from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from utils.schema import get_schema_as_str
from agents.multiple.outputs import OutputSQL
from database.connection import DatabaseConnection


class SqlAgentDeps(BaseModel):
    """Dependencies injected into the Pydantic AI agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    schema_text: Optional[str] = None
    db_connection: Optional[DatabaseConnection] = None


class StraightforwardWithSchemaSqlAgent:
    """Generate SQL using Pydantic AI and the database schema loaded from YAML."""

    def __init__(
        self,
        model_name: str = "gpt-4.1-mini",
        db_connection: Optional[DatabaseConnection] = None,
    ):
        self.schema_text = get_schema_as_str(db_name=db_connection.get_database_name())
        self.db_connection = db_connection

        # Pydantic AI model and agent
        self.model = OpenAIChatModel(model_name=model_name)

        self.agent = Agent[SqlAgentDeps, OutputSQL](
            model=self.model,
            deps_type=SqlAgentDeps,
            output_type=OutputSQL,
            system_prompt=(
                """ 
            You are an expert SQL assistant. 

            Your objective is to generate a single and valid SQL query, without any comments, for the given <QUESTION>.

            If the question is answerable, set the "answerable" key to true. Otherwise, set it to false and set the "query" key to an empty string.

            Respond ONLY in valid JSON with the following structure : 
            {
            "explanations": "<short explanation of the query>", 
            "answerable": true or false,
            "query": "<the valid SQL query>"
            }

            "\nDo not include any other text. Do not include any comments in the query.
            """
            ),
        )

    def _build_prompt(self, question: str) -> str:
        base = ""
        db_name = getattr(self.db_connection, "db_name", None)
        if db_name:
            base += f"You have the following database name : '{db_name}'.\n"
        if self.schema_text:
            base += f"\n<SCHEMA>\nThe database schema is as follows :\n\n{self.schema_text}\n\n</SCHEMA>\n"
        base += "\n\n<QUESTION>\nThe question to answer is : :\n"
        base += f"{question}\n\n"
        base += "</QUESTION>"

        return base

    def generate_sql(self, question: str) -> OutputSQL:
        """Generate a typed SQL response for the question."""
        deps = SqlAgentDeps(
            schema_text=self.schema_text,
            db_connection=self.db_connection,
        )
        prompt = self._build_prompt(question)
        result = self.agent.run_sync(prompt, deps=deps)
        return OutputSQL(
            explanations=result.output.explanations,
            query=result.output.query,
            answerable=result.output.answerable,
        )
