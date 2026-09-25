"""Utils for tests."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction


def build_cmd(*args: str, keep_tex: bool = False) -> list[str]:
    """Return CLI command for cgt_calc with optional pdflatex disabled.

    Pass keep_tex when the test reads the generated .tex. Rendering a PDF puts
    the LaTeX source in a temporary file and leaves no .tex next to the report,
    so a test asserting on the source has to skip pdflatex even where
    ENABLE_PDFLATEX asks for it.
    """
    cmd = [sys.executable, "-m", "cgt_calc.main", *args]
    if keep_tex or not os.getenv("ENABLE_PDFLATEX"):
        cmd.append("--no-pdflatex")
    cmd += ["--exchange-rates-file", "tests/exchange_rates_data.csv"]
    return cmd


def report_path(request: pytest.FixtureRequest) -> str:
    """Return an --output path named after the test asking for it.

    Reports land in one directory and CI keeps them as an artifact, so a
    downloaded file has to say which test produced it. Hyphens because the
    workflow collects them with an `out/test-*` glob.
    """
    return f"out/{request.node.name.replace('_', '-')}/"


def tax_fields(transaction: BrokerTransaction) -> tuple[object, ...]:
    """Return the fields that decide tax, for comparing two readings of one history."""
    return (
        transaction.date,
        transaction.action,
        transaction.symbol,
        transaction.description,
        transaction.quantity,
        transaction.price,
        transaction.fees,
        transaction.amount,
        transaction.currency,
        transaction.broker,
    )


def stderr_alerts(stderr: str) -> list[str]:
    """Return warning/error lines from stderr, ignoring info chatter."""
    return [
        line
        for line in stderr.splitlines()
        if line.startswith(("WARNING", "ERROR", "CRITICAL"))
    ]


def run_cli(
    cmd: list[str], *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run cgt-calc, failing the test with its output if it exits non-zero."""
    result = subprocess.run(
        cmd, input=stdin, capture_output=True, encoding="utf-8", check=False
    )
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def assert_stdout_matches(
    result: subprocess.CompletedProcess[str],
    cmd: list[str],
    expected_file: Path,
    *,
    piped_from: Path | None = None,
) -> None:
    """Compare stdout with the expected output byte for byte.

    On a mismatch, print the command that regenerates the file, reading
    ``piped_from`` on stdin when the run did.
    """
    cmd_str = " ".join([param or "''" for param in cmd])
    if piped_from is not None:
        cmd_str = f"cat {piped_from} | {cmd_str}"
    assert result.stdout == expected_file.read_text(encoding="utf-8"), (
        "Run generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
