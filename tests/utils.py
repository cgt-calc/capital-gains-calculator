"""Utils for tests."""

from __future__ import annotations

import re
import subprocess
import sys
from typing import TYPE_CHECKING

import pdfplumber
import pytest

from cgt_calc.render_pdf import render_pdf

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction, CapitalGainsReport

# Where the refusals that ask for two securities in separate runs send you.
SEPARATE_RUNS = (
    "https://cgt-calc.uk/extra-data-and-options/#two-securities-in-separate-runs"
)
# The header of the Trading 212 exports these tests write.
T212_HEADER = (
    "Action,Time (UTC),ISIN,Ticker,Name,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,"
    "Currency (Total),ID"
)


def build_cmd(*args: str) -> list[str]:
    """Return the CLI command for cgt_calc with the shared exchange rate cache."""
    return [
        sys.executable,
        "-m",
        "cgt_calc.main",
        *args,
        "--exchange-rates-file",
        "tests/exchange_rates_data.csv",
    ]


def pdf_text(path: Path) -> str:
    """Return a PDF report's text as a reader sees it, page after page."""
    with pdfplumber.open(path) as pdf:
        return "".join(f"{page.extract_text()}\n" for page in pdf.pages)


def report_text(report: CapitalGainsReport, directory: Path) -> str:
    """Render the report to a PDF in `directory` and return its text, one line.

    Line breaks only shape the page, so each run of whitespace becomes one
    space and a phrase can be found wherever the line wraps.
    """
    render_pdf(report, directory / "report.pdf")
    return " ".join(pdf_text(directory / "report.pdf").split())


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


def final_portfolio(stdout: str) -> dict[str, str]:
    """Return the final portfolio printed by a run as ticker to quantity."""
    block = stdout.split("Final portfolio\n", 1)[1].split("\n\n", 1)[0]
    return dict(line.strip().split(": ") for line in block.splitlines())


def summary_figure(stdout: str, label: str) -> str:
    """Return a pound figure of the printed tax summary, such as `Gain`."""
    match = re.search(rf"^  {label}:\s+£([\d,.]+)$", stdout, re.MULTILINE)
    assert match is not None, stdout
    return match.group(1)


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
