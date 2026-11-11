# Codebase overview

Experimental SQL assistant turning natural-language questions into PostgreSQL queries, executes them, and checks whether the results answer the question with sub-questions. 
- Pydantic AI agents handle generation and validation.
- Streamlit exposes the workflow and its intermediate results.

## Workflow

1. Generate SQL using the database schema and assess whether the question is answerable.
2. Execute the query and attempt bounded repairs if execution fails.
3. **Generate supporting questions** and execute their SQL queries.
4. Generate Python checks comparing the resulting DataFrames.
5. If checks fail, use a correction hint to refine the question within a configured attempt limit.

Validation distinguishes passed, failed, unchecked, and error outcomes. 
Results include generated SQL, execution details, checks, and refinement history.

## Structure

| Path | Responsibility |
| --- | --- |
| `interface/main.py` | Streamlit interface for asking questions, configuring limits, and inspecting results. |
| `agents/straightforward.py` | Schema-aware SQL generation. |
| `agents/queryer.py` | SQL repair after execution errors, with Python-enforced attempt limits. |
| `agents/checker.py` | Generation of supporting check questions. |
| `agents/multiple/` | Workflow orchestration, check-query generation, refinement, and shared result models. |
| `agents/multiple/validation/` | DataFrame validation, correction hints, and generated-check execution in a separate process with a timeout. |
| `database/connection.py` | PostgreSQL access, read-only query filtering, statement timeouts, and bounded result fetching. |
| `utils/schema.py` | Loading database schemas for agent prompts. |
| `documentation/auto_doc/` | Database introspection code that generates schema YAML. |
| `documentation/databases/` | Schema data consumed by the agents. |
| `tests/` | Database integration tests and agent, repair, validation, refinement, and interface tests. |
