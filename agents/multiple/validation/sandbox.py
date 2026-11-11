import multiprocessing
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import Literal, Optional

import numpy as np
import pandas as pd


@dataclass
class ValidationSandboxResult:
    status: Literal["success", "timeout", "error"]
    valid: Optional[bool]
    hint: str
    error: Optional[str]
    executed_code: str
    stdout: str
    stderr: str


def _run_validation(connection, main_df, check_df, validation_question, generated_code):
    """Execute in a child process so timeout and output capture work in any thread."""
    safe_globals = {
        "pandas": pd,
        "pd": pd,
        "numpy": np,
        "np": np,
        "main_df": main_df,
        "check_df": check_df,
        "validation_question": validation_question,
        "__builtins__": {
            "len": len,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "sorted": sorted,
            "reversed": reversed,
            "any": any,
            "all": all,
            "isinstance": isinstance,
            "type": type,
            "print": print,
        },
    }
    stdout_capture, stderr_capture = StringIO(), StringIO()
    status, valid, hint, error = "error", None, "", None
    try:
        # pandas/numpy are preloaded; generated import statements are unnecessary.
        cleaned_code = "\n".join(
            line for line in generated_code.splitlines()
            if not line.lstrip().startswith(("import ", "from "))
        )
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            exec(cleaned_code, safe_globals)
        result = safe_globals.get("result")
        if not isinstance(result, dict) or not isinstance(
            result.get("valid"), (bool, np.bool_)
        ):
            raise ValueError('Validation must define result with a boolean "valid" key')
        status, valid, hint = "success", bool(result["valid"]), str(result.get("hint", ""))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    try:
        connection.send(ValidationSandboxResult(
            status=status,
            valid=valid,
            hint=hint,
            error=error,
            executed_code=generated_code,
            stdout=stdout_capture.getvalue(),
            stderr=stderr_capture.getvalue(),
        ))
    finally:
        connection.close()


class ValidationSandbox:
    """Run validation in a terminable process with restricted builtins.

    Rejects inputs above 1,000 rows instead of validating a truncated sample.
    This is an execution helper, not a security boundary for untrusted code.
    """

    def __init__(self, timeout_seconds: float = 30):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_rows = 1000

    def execute_validation(
        self,
        main_df: pd.DataFrame,
        check_df: pd.DataFrame,
        validation_question: str,
        generated_code: str,
    ) -> ValidationSandboxResult:
        def failure(status, error):
            return ValidationSandboxResult(
                status=status, valid=None, hint="", error=error,
                executed_code=generated_code, stdout="", stderr="",
            )

        if len(main_df) > self.max_rows or len(check_df) > self.max_rows:
            return failure("error", f"Dataframe exceeds validation limit of {self.max_rows} rows")

        # Spawn avoids inheriting Streamlit/API threads and their global state.
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_run_validation,
            args=(sender, main_df, check_df, validation_question, generated_code),
            daemon=True,
        )
        try:
            process.start()
            sender.close()
            if not receiver.poll(self.timeout_seconds):
                return failure("timeout", f"Validation code exceeded {self.timeout_seconds} seconds")
            return receiver.recv()
        except Exception as exc:
            return failure("error", f"Validation process failed: {type(exc).__name__}: {exc}")
        finally:
            sender.close()
            receiver.close()
            if process.pid is not None:
                process.join(timeout=0.1)
                if process.is_alive():
                    process.terminate()
                    process.join()
                process.close()
