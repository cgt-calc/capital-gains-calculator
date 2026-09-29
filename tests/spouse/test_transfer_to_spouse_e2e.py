"""End-to-end tests for a transfer to a spouse, from both sides.

The pair is the point: what leaves the transferor's pool has to arrive as the
recipient's cost, or the shares are taxed twice or not at all. Both runs write
a PDF to ``out/`` so CI keeps it as an artifact to look at.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

if TYPE_CHECKING:
    import subprocess

    import pytest

DATA = Path("tests") / "spouse" / "data"

# 40 of the transferor's 100 shares, bought at £10, go to their spouse.
BASE_COST_PASSED_ON = "£400.00"


def _run(name: str, request: pytest.FixtureRequest) -> subprocess.CompletedProcess[str]:
    cmd = build_cmd(
        "--year",
        "2024",
        "--raw-file",
        str(DATA / f"{name}.csv"),
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    assert_stdout_matches(result, cmd, DATA / f"expected_output_{name}.txt")
    return result


def test_transferor_report(request: pytest.FixtureRequest) -> None:
    """The transferor owes nothing on the transfer and reports the base cost."""
    result = _run("sender", request)

    assert "Transferred to spouse" in result.stdout
    assert BASE_COST_PASSED_ON in result.stdout
    # The row the recipient pastes into their own RAW file.
    assert "2024-06-15,TRANSFER_FROM_SPOUSE,FOO,40,10,0.00,GBP" in result.stdout
    # Only the later sale of 20 shares is taxable.
    assert "Disposals:                1" in result.stdout
    assert "Total gain:         £100.00" in result.stdout


def test_recipient_report(request: pytest.FixtureRequest) -> None:
    """The recipient's cost is what the transferor passed on, not market value."""
    result = _run("recipient", request)

    # £800 proceeds against the £400 that came across, so £400 of gain.
    assert f"Allowable costs:    {BASE_COST_PASSED_ON}" in result.stdout
    assert "Total gain:         £400.00" in result.stdout
    # Nothing was transferred away, so that section is absent.
    assert "Transferred to spouse" not in result.stdout
