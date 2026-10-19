"""Test --skip-unsupported-rows, which leaves out rows a parser does not support."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from cgt_calc.exceptions import ParsingError, UnsupportedActionError
from cgt_calc.model import ActionType
from cgt_calc.parsers.base_parsers import read_supported_row, skipping_unsupported_rows
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.revolut import COLUMNS, RevolutParser
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser
from tests.utils import build_cmd, run_cli, stderr_alerts

FILE = Path("export.csv")
RAW_HEADER = "date,action,symbol,quantity,price,fees,currency\n"


def _unsupported() -> None:
    raise UnsupportedActionError(FILE, "Unknown action: Gift")


def _malformed() -> None:
    raise ParsingError(FILE, "Invalid quantity")


def test_an_unsupported_row_still_stops_the_run_by_default() -> None:
    """Without the flag nothing changes, except that the error names the row."""
    with pytest.raises(UnsupportedActionError, match=r"row 7: Unknown action: Gift"):
        read_supported_row(7, _unsupported)


def test_an_unsupported_row_is_skipped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning names the file, the row and the action, so it can be checked."""
    with (
        skipping_unsupported_rows(enabled=True),
        caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.base_parsers"),
    ):
        assert read_supported_row(7, _unsupported) is None

    assert "export.csv, row 7: Unknown action: Gift" in caplog.text
    assert "--skip-unsupported-rows" in caplog.text


def test_a_malformed_row_stops_the_run_even_when_skipping() -> None:
    """Only an unsupported action is skipped: a row that cannot be read is not."""
    with (
        skipping_unsupported_rows(enabled=True),
        pytest.raises(ParsingError, match="quantity"),
    ):
        read_supported_row(7, _malformed)


def test_skipping_ends_with_the_load() -> None:
    """A later load in the same process is not skipping unless asked again."""
    with skipping_unsupported_rows(enabled=True):
        pass

    with pytest.raises(UnsupportedActionError):
        read_supported_row(7, _unsupported)


def test_a_parser_with_its_own_row_loop_keeps_the_other_rows(tmp_path: Path) -> None:
    """A RAW file is read by its own loop, which goes on past the skipped row."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_text(
        RAW_HEADER
        + "2023-02-09,BUY,XYZ,10,2.50,0,USD\n"
        + "2023-02-10,NONSENSE,XYZ,10,2.50,0,USD\n"
        + "2023-02-11,SELL,XYZ,5,3.00,0,USD\n",
        encoding="utf-8",
    )

    with skipping_unsupported_rows(enabled=True):
        transactions = RawParser().load_from_file(raw_file)

    assert [t.action for t in transactions] == [ActionType.BUY, ActionType.SELL]
    assert [t.source and t.source.row for t in transactions] == [2, 4]


def test_the_shared_csv_loop_keeps_the_other_rows(tmp_path: Path) -> None:
    """Revolut is read by the loop every standard CSV parser shares."""
    path = tmp_path / "revolut.csv"
    path.write_text(
        ",".join(COLUMNS)
        + "\n2021-11-02T12:34:56Z,QCOM,BUY - LIMIT,50,USD 134.50,USD 6725,USD,1.36\n"
        + "2021-11-03T12:34:56Z,,REWARD,,,USD 5,USD,1.36\n",
        encoding="utf-8",
    )

    with skipping_unsupported_rows(enabled=True):
        transactions = RevolutParser().load_from_file(path)

    assert [t.action for t in transactions] == [ActionType.BUY]


def test_schwab_journaled_shares_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row the Schwab page names as unsupported is left out, the rest kept."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))
    content = (
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "03/14/2024,Journaled Shares,FOO,FOO INC,$500,-20,,\n"
        "03/12/2024,Buy,FOO,FOO INC,$480,50,$0,-$24000\n"
    )

    with skipping_unsupported_rows(enabled=True):
        transactions = SchwabParser.read_transactions(
            io.StringIO(content), Path("transactions.csv")
        )

    assert [t.action for t in transactions] == [ActionType.BUY]


def test_the_flag_reaches_the_parsers_from_the_command_line(tmp_path: Path) -> None:
    """The run completes and the skipped row is reported as a warning."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_text(
        RAW_HEADER
        + "2023-04-25,BUY,XYZ,10,5,0,USD\n"
        + "2023-04-26,NONSENSE,XYZ,10,5,0,USD\n",
        encoding="utf-8",
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2023",
            "--raw-file",
            str(raw_file),
            "--no-balance-check",
            "--skip-unsupported-rows",
            "--no-report",
        )
    )

    alerts = stderr_alerts(result.stderr)
    assert any("--skip-unsupported-rows" in alert for alert in alerts)
    assert "row 3: Unknown action: NONSENSE" in result.stderr
