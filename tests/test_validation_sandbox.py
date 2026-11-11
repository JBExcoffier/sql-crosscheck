import multiprocessing
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from agents.multiple.validation.sandbox import ValidationSandbox


def validate(code, main_rows=1, check_rows=1, timeout=10):
    return ValidationSandbox(timeout).execute_validation(
        pd.DataFrame({"value": [1] * main_rows}),
        pd.DataFrame({"value": [1] * check_rows}),
        "Totals match?", code,
    )


@pytest.mark.parametrize("code,expected", [
    ("result = {'valid': True}", True),
    ("result = {'valid': False}", False),
    ("import pandas as pd\nresult = {'valid': main_df['value'].sum() == check_df['value'].sum()}", True),
    ("result = {'valid': 'False'}", None),
    ("result = {'valid': 1}", None),
    ("result = {}", None),
    ("result = True", None),
    ("print('No result')", None),
])
def test_validation_requires_a_boolean_result(code, expected):
    result = validate(code)
    assert result.valid is expected
    assert result.status == ("error" if expected is None else "success")


@pytest.mark.parametrize("main_rows,check_rows", [(1001, 1), (1, 1001)])
def test_oversized_inputs_are_not_silently_truncated(main_rows, check_rows):
    result = validate("result = {'valid': True}", main_rows, check_rows)
    assert result.status == "error"
    assert result.valid is None
    assert "1000 rows" in result.error


def test_exact_row_limit_is_allowed():
    result = validate("result = {'valid': len(main_df) == 1000 and len(check_df) == 1000}", 1000, 1000)
    assert result.status == "success"
    assert result.valid is True


@pytest.mark.parametrize("worker_thread", [False, True])
def test_infinite_validation_times_out_and_cleans_up(worker_thread):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    children = {child.pid for child in multiprocessing.active_children()}
    code = "while True:\n    pass"
    if worker_thread:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(validate, code, timeout=2).result(timeout=10)
    else:
        result = validate(code, timeout=2)
    assert result.status == "timeout"
    assert result.valid is None
    assert "exceeded" in result.error
    assert {child.pid for child in multiprocessing.active_children()} == children
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


def test_validation_captures_output_and_isolates_dataframes_in_worker_thread():
    main_df = pd.DataFrame({"value": [1]})
    code = "print('Checking'); main_df['value'] = 999; result = {'valid': True}"
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            ValidationSandbox().execute_validation, main_df, main_df, "Check", code
        ).result(timeout=10)
    assert result.status == "success"
    assert result.stdout == "Checking\n"
    assert main_df['value'].tolist() == [1]
