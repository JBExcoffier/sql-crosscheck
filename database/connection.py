import os
import psycopg2
import pandas as pd
import re
from typing import Any, Optional, Sequence, Tuple, List, Literal
from dataclasses import dataclass
from uuid import uuid4
from pydantic import ConfigDict


@dataclass
class QueryExecutionResult:
    """Result of a database query execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "rejected", "error"]
    columns: Optional[List[str]] = None
    rows: Optional[List[Tuple[Any, ...]]] = None
    row_count: Optional[int] = None
    dataframe: Optional[pd.DataFrame] = None
    error_type: Optional[Literal["validation_error", "sql_error", "system_error"]] = (
        None
    )
    error: Optional[str] = None
    pgcode: Optional[str] = None


class DatabaseConnection:
    """Manages PostgreSQL database connections with proper resource management."""

    def __init__(self, statement_timeout_ms: int = 30000):
        """Initialize the connection manager with a per-statement timeout."""
        self.connection = None
        if statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms must be positive")
        self.statement_timeout_ms = statement_timeout_ms
        self.db_name = None
        self.db_user = None
        self.db_host = None
        self.db_port = None

    def connect(self) -> "DatabaseConnection":
        """Establish connection to PostgreSQL database using environment variables."""
        self.db_user = os.getenv("POSTGRES_DB_USER")
        self.db_password = os.getenv("POSTGRES_DB_PASSWORD")
        self.db_name = os.getenv("POSTGRES_DB_NAME")
        self.db_host = os.getenv("POSTGRES_DB_HOST", "localhost")
        self.db_port = os.getenv("POSTGRES_DB_PORT", "5432")

        if not all([self.db_user, self.db_password, self.db_name]):
            raise ValueError(
                "Missing required environment variables: POSTGRES_DB_USER, POSTGRES_DB_PASSWORD, POSTGRES_DB_NAME"
            )

        self.connection = psycopg2.connect(
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            user=self.db_user,
            password=self.db_password,
        )

        return self

    def get_database_name(self) -> str:
        """Get the database name from the connection."""
        if not self.connection:
            raise RuntimeError("No active database connection. Call connect() first.")

        try:
            db_name = self.connection.get_dsn_parameters().get("dbname")
            if not db_name:
                raise ValueError("Database name not found in connection parameters")
            return db_name
        except Exception as e:
            raise ValueError(f"Could not retrieve database name from connection: {e}")

    def get_connection(self):
        """Get the underlying psycopg2 connection object."""
        if not self.connection:
            raise RuntimeError("No active database connection. Call connect() first.")
        return self.connection

    def is_connected(self) -> bool:
        """Check if the connection is active and valid."""
        return self.connection is not None and not self.connection.closed

    def close(self):
        """Close the database connection properly."""
        if self.connection and not self.connection.closed:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        """Context manager entry point."""
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit point."""
        self.close()

    def __del__(self):
        """Destructor to ensure connection is closed."""
        self.close()

    def _is_read_only_query(self, query: str) -> Tuple[bool, str]:
        """Filter SQL tokens; PostgreSQL's read-only transaction enforces writes."""
        pattern = r"""[eE]'(?:\\.|''|[^'])*'|'(?:''|[^'])*'|"(?:""|[^"])*"|(?P<tag>\$(?:[A-Za-z_]\w*)?\$).*?(?P=tag)|--[^\r\n]*|/\*.*?\*/|[A-Za-z_][A-Za-z_0-9$]*|;|[^\s]"""
        tokens = []
        for match in re.finditer(pattern, query, re.DOTALL):
            token = match.group()
            if token.startswith(("--", "/*")):
                continue
            if token.startswith(("'", '"', "$", "E'", "e'")):
                token = "_quoted_value_"
            tokens.append(token.lower())

        statements = [part.strip() for part in " ".join(tokens).split(";") if part.strip()]
        if not statements:
            return False, "Empty SQL query"
        if len(statements) > 1:
            return False, "Multiple statements detected; only a single read-only statement is allowed"

        words = statements[0].split()
        forbidden = {
            "insert", "update", "delete", "merge", "create", "alter", "drop",
            "truncate", "comment", "grant", "revoke", "vacuum", "analyze",
            "cluster", "copy", "call", "do", "refresh", "set", "reset", "explain",
        }
        if forbidden.intersection(words):
            return False, "Forbidden keyword found; only SELECT or CTEs are allowed"
        if words[0] not in {"select", "with"}:
            return False, "Query must start with SELECT or WITH (CTEs)"
        if words[0] == "with" and "select" not in words:
            return False, "CTE detected but no SELECT found"
        return True, ""

    def execute_read_only_query(
        self,
        query: str,
        params: Optional[Sequence[Any]] = None,
        max_rows: Optional[int] = None,
        as_dataframe: bool = True,
    ) -> QueryExecutionResult:
        """Execute a read-only SQL query and return structured results.

        Parameters
        - query: SQL string. Must be a single SELECT or WITH (CTE) statement.
        - params: Optional sequence for parametrized query placeholders.
        - max_rows: Result limit (defaults to 1000); larger results are rejected.

        Returns QueryExecutionResult with:
        - On success: status='ok', columns, rows, row_count, dataframe (if requested)
        - On validation failure: status='rejected', error_type='validation_error', error
        - On SQL error: status='error', error_type='sql_error', error, pgcode
        - On non-SQL error: status='error', error_type='system_error', error
        """
        if not self.is_connected():
            return QueryExecutionResult(
                status="error",
                error_type="system_error",
                error="No active database connection. Call connect() first.",
            )

        is_read_only, reason = self._is_read_only_query(query)
        if not is_read_only:
            return QueryExecutionResult(
                status="rejected",
                error_type="validation_error",
                error=reason,
            )

        limit = 1000 if max_rows is None else max_rows
        if limit < 0:
            return QueryExecutionResult(
                status="rejected", error_type="validation_error",
                error="max_rows must be >= 0",
            )

        try:
            # Finish each transaction so settings and failed queries cannot leak
            # into the next execution on this connection.
            with self.connection:
                with self.connection.cursor() as settings:
                    settings.execute("SET LOCAL TRANSACTION READ ONLY;")
                    settings.execute("SET LOCAL statement_timeout = %s", (self.statement_timeout_ms,))
                # A server-side cursor avoids buffering the complete result in libpq.
                with self.connection.cursor(name=f"sql_agent_{uuid4().hex}") as cursor:
                    cursor.execute(query, params if params is not None else None)
                    rows = cursor.fetchmany(limit + 1)
                    columns = [desc[0] for desc in cursor.description]
                    if len(rows) > limit:
                        return QueryExecutionResult(
                            status="rejected", error_type="validation_error",
                            error=f"Query exceeds row limit of {limit}; result was not returned",
                        )
                    return QueryExecutionResult(
                        status="ok", columns=columns, rows=rows, row_count=len(rows),
                        dataframe=pd.DataFrame(rows, columns=columns) if as_dataframe else None,
                    )
        except psycopg2.Error as exc:
            return QueryExecutionResult(
                status="error", error_type="sql_error", error=str(exc),
                pgcode=getattr(exc, "pgcode", None),
            )
        except Exception as exc:
            return QueryExecutionResult(
                status="error", error_type="system_error", error=str(exc),
            )
