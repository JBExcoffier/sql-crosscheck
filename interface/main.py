import streamlit as st

from agents.checker import MAX_CHECKS
from agents.multiple.sql_agent_refiner import SqlAgentRefiner
from agents.multiple.validation.dataframe_validator import ValidationResult
from database.connection import DatabaseConnection


def render_validation(validation: ValidationResult | None):
    if validation is None:
        st.info("Validation not run.")
        return

    overall = validation.overall_valid
    if overall is True:
        st.success("Checks passed — the answer looks consistent with these checks.")
    elif overall is False:
        st.warning("Checks failed — the answer may be inconsistent.")
    elif overall == "unchecked":
        st.warning("Unchecked — no checks were generated for this answer.")
    else:
        st.error("Validation error — the answer could not be fully checked.")
    if validation.error:
        st.error(validation.error)

    for check in validation.check_results:
        status = "Passed" if check.valid is True else "Failed" if check.valid is False else "Error"
        with st.expander(f"Check {check.check_index + 1}: {status} — {check.query_question}"):
            st.write(f"**Validation question:** {check.validation_question}")
            if check.hint:
                st.write(f"**Hint:** {check.hint}")
            if check.error:
                st.error(check.error)


def main():
    st.set_page_config(page_title="Ask Your Data", page_icon="🔍", layout="wide")
    st.title("🔍 Ask Your Data")
    st.markdown("Ask questions in plain language and inspect the SQL, checks, and refinements.")

    with st.sidebar:
        st.header("⚙️ Technical Configuration")
        model_name = st.selectbox(
            "Language Model",
            options=["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4.1"],
            index=1,
            help="Language model to use for SQL generation and refinement",
        )
        check_limit = st.number_input(
            "Check Limit", min_value=1, max_value=MAX_CHECKS, value=1, step=1,
            help="Maximum number of validation check questions to generate",
        )
        max_inner_primary_retries = st.number_input(
            "Max Inner Primary Retries", min_value=1, max_value=5, value=3, step=1,
            help="Maximum retries for repairing SQL execution errors",
        )
        max_refinement_steps = st.number_input(
            "Max Refinement Steps", min_value=0, max_value=5, value=3, step=1,
            help="Maximum additional attempts after the initial answer (0 disables refinement)",
        )
        recompute_checks = st.checkbox(
            "Recompute Checks", value=False,
            help="If unchecked, reuse the first checks within this request",
        )

    user_question = st.text_area(
        "📝 Question your database",
        placeholder="Enter your question (e.g., 'How many employees are there?')",
        height=100,
    )
    if not st.button("🚀 Process Request", type="primary", use_container_width=True):
        return
    if not user_question.strip():
        st.error("Please enter a question")
        return

    steps_placeholder = st.container()
    try:
        with st.spinner("Connecting to database..."):
            db = DatabaseConnection().connect()
    except Exception as exc:
        st.error(f"Failed to connect to database: {exc}")
        return

    try:
        with st.spinner("Initializing SQL Agent Refiner..."):
            refiner = SqlAgentRefiner(
                db_connection=db,
                model_name=model_name,
                check_limit=check_limit,
                max_inner_primary_retries=max_inner_primary_retries,
                max_refinement_steps=max_refinement_steps,
                recompute_checks=recompute_checks,
            )
        with steps_placeholder:
            st.subheader("We're working on your question...")

        def step_callback(step):
            with steps_placeholder:
                with st.expander(
                    f"Step {step.step_number + 1}: {step.user_question[:50]}", expanded=True
                ):
                    st.write(f"**Question:** {step.user_question}")
                    if step.correction_hint:
                        st.write(f"**Correction hint:** {step.correction_hint}")
                    generation = step.sql_result.primary.generation
                    if generation.query:
                        st.code(generation.query, language="sql")
                    if step.sql_result.status == "rejected":
                        st.warning("Question considered unanswerable with the available schema.")
                    render_validation(step.validation_result)

        with st.spinner("Building and checking the answer step by step..."):
            result = refiner.refine(
                user_question, as_dataframe=True, step_callback=step_callback
            )
    except Exception as exc:
        st.error(f"Error during processing: {exc}")
        return
    finally:
        db.close()

    st.header("Run completed")
    st.info(f"Total steps: {result.total_steps}")
    if result.status == "unanswerable":
        st.warning("The question is considered unanswerable with the available database schema.")
        st.info(f"Question: {result.final_question}")
        return

    st.subheader("📝 Refined question")
    st.text_area(
        label="", value=result.final_question, height=100,
        disabled=True, label_visibility="collapsed",
    )
    st.subheader("❔ How the checks went")
    render_validation(result.final_validation_result)

    sql_result = result.final_sql_result
    if sql_result is None:
        st.warning("No SQL result available.")
        return

    st.subheader("🔍 Query details")
    execution = sql_result.primary.execution
    if execution is None:
        st.warning("No execution result available.")
    elif execution.status == "ok":
        st.info("SQL executed successfully.")
        st.write(f"**Rows returned:** {execution.row_count}")
        if execution.dataframe is not None:
            st.dataframe(execution.dataframe, use_container_width=True)
    else:
        st.error(f"Query execution failed: {execution.error}")

    generation = sql_result.primary.generation
    st.subheader("💻 Generated SQL Query")
    if generation.query:
        st.code(generation.query, language="sql")
        st.caption(f"Explanation: {generation.explanations}")
    else:
        st.warning("No SQL query was generated.")
    if generation.error:
        st.error(generation.error)


if __name__ == "__main__":
    main()
