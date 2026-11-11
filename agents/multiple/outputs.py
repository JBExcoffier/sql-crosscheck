from typing import Optional, List, Literal

from pydantic import BaseModel, ConfigDict

from database.connection import QueryExecutionResult


class OutputSQL(BaseModel):
    explanations: str
    query: str
    answerable: bool
    error: Optional[str] = None


class CheckWithSql(BaseModel):
    query_question: str
    validation_question: str
    sql_generation: OutputSQL


class PrimaryResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    generation: OutputSQL
    execution: Optional[QueryExecutionResult]


class CheckWithExecution(CheckWithSql):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    execution: Optional[QueryExecutionResult]


class SqlAgentWithChecksOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "error", "rejected"]
    user_question: str
    primary: PrimaryResult
    checks: List[CheckWithExecution]
    check_generation_error: Optional[str] = None
