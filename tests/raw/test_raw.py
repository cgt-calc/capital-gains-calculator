"""Test raw format support."""

from __future__ import annotations

import csv
from decimal import Decimal
import io
import logging
from pathlib import Path
import subprocess
import sys

import pytest

from cgt_calc.args_validators import STDIN_PATH
from cgt_calc.exceptions import ParsingError
from cgt_calc.logging import force_utf8_stdio
from cgt_calc.model import ActionType
from cgt_calc.parsers.raw import COLUMNS, RawParser
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write CSV rows to disk."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_run_with_raw_files_no_balance_check(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2022",
        "--raw-file",
        "tests/raw/data/test_data.csv",
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = Path("tests") / "raw" / "data" / "expected_output.txt"
    assert_stdout_matches(result, cmd, expected_file)


def test_run_with_raw_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2022",
        "--raw-file",
        "tests/raw/data/test_data_2.csv",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = Path("tests") / "raw" / "data" / "expected_output_2.txt"
    assert_stdout_matches(result, cmd, expected_file)


def test_run_with_raw_files_stdin(request: pytest.FixtureRequest) -> None:
    """Runs the script with stdin and verifies it works identically."""
    csv_file = Path("tests") / "raw" / "data" / "test_data.csv"
    csv_content = csv_file.read_text(encoding="utf-8")

    cmd = build_cmd(
        "--year",
        "2022",
        "--raw-file",
        "-",
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd, stdin=csv_content)
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = Path("tests") / "raw" / "data" / "expected_output_stdin.txt"
    assert_stdout_matches(result, cmd, expected_file, piped_from=csv_file)


def test_stdin_decodes_utf8_under_a_legacy_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A UTF-8 export piped in keeps its characters on a legacy code page.

    Windows decodes redirected stdin with the ANSI code page, so a symbol
    outside ASCII would otherwise arrive as mojibake.
    """
    csv_bytes = (
        "date,action,symbol,quantity,price,fees,currency\n"
        "2023-02-09,DIVIDEND,CAFÉ,4200,0.80,0.0,USD\n"
    ).encode()
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(csv_bytes), encoding="cp1252")
    )
    force_utf8_stdio()

    transactions = RawParser.load_from_file(STDIN_PATH, show_parsing_msg=False)

    assert [txn.symbol for txn in transactions] == ["CAFÉ"]


def test_run_with_nonexistent_file() -> None:
    """Runs the script with a nonexistent file and verifies it fails with an error."""
    missing_file = "tests/raw/data/does_not_exist.csv"
    cmd = build_cmd(
        "--year",
        "2022",
        "--raw-file",
        missing_file,
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode != 0
    assert "path does not exist" in result.stderr
    assert missing_file in result.stderr


@pytest.mark.parametrize(
    ("fees", "expected_fees"),
    [("0.10", Decimal("0.10")), ("", Decimal(0))],
    ids=["stated", "blank"],
)
def test_read_raw_transactions_with_header(
    tmp_path: Path, fees: str, expected_fees: Decimal
) -> None:
    """Parse a RAW file including a header row; a blank fee is no fee."""

    raw_file = tmp_path / "raw_with_header.csv"
    rows = [
        COLUMNS,
        [
            "2024-01-02",
            "BUY",
            "XYZ",
            "10",
            "2.50",
            fees,
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.BUY
    assert transaction.symbol == "XYZ"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal("2.50")
    assert transaction.amount == -(Decimal("25.00") + expected_fees)
    assert transaction.fees == expected_fees


def test_read_raw_transactions_keep_their_row_and_order(tmp_path: Path) -> None:
    """RAW rows keep the line they came from and the order they were read in.

    A RAW file has dates and no times, so the order of its rows is the only
    statement it makes about what happened first on a day. It is preserved
    here rather than re-derived later from the merged, re-sorted stream.
    """

    raw_file = tmp_path / "raw_ordered.csv"
    rows = [
        COLUMNS,
        ["2024-01-02", "BUY", "XYZ", "10", "2.50", "0.00", "USD"],
        ["2024-01-02", "BUY", "XYZ", "5", "3.00", "0.00", "USD"],
    ]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    sources = [transaction.source for transaction in transactions]
    assert all(source is not None for source in sources)
    assert [source.row for source in sources if source] == [2, 3]
    assert [source.index for source in sources if source] == [0, 1]
    assert [source.file for source in sources if source] == [raw_file, raw_file]
    assert [source.parser for source in sources if source] == ["RAW format"] * 2
    # A RAW export states no times, so nothing may later read one from it.
    assert [source.timestamp for source in sources if source] == [None, None]
    # The order above is a statement about what happened first, because the
    # format asks the user to write a day's rows in the order they happened.
    assert [source.rows_in_time_order for source in sources if source] == [True] * 2


def test_read_raw_transactions_transfer_to_spouse(tmp_path: Path) -> None:
    """Parse a RAW TRANSFER_TO_SPOUSE row (no gain/no loss share transfer)."""

    raw_file = tmp_path / "raw_transfer_to_spouse.csv"
    rows = [
        COLUMNS,
        [
            "2024-03-16",
            "TRANSFER_TO_SPOUSE",
            "XYZ",
            "21.5",
            "0.00",
            "0.00",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER_TO_SPOUSE
    assert transaction.symbol == "XYZ"
    assert transaction.quantity == Decimal("21.5")


def test_read_raw_transactions_without_header(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Parse RAW rows without a header."""

    raw_file = tmp_path / "raw_without_header.csv"
    rows = [
        [
            "2024-01-03",
            "SELL",
            "XYZ",
            "5",
            "3.00",
            "0.00",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.raw"):
        transactions = RawParser().load_from_file(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.SELL
    assert transaction.amount == Decimal("15.00")
    assert "missing header row" in caplog.text.lower()


def test_read_raw_transactions_invalid_header(tmp_path: Path) -> None:
    """Fail fast when header columns do not match expected schema."""

    raw_file = tmp_path / "raw_bad_header.csv"
    bad_header = ["date", "action", "ticker", *COLUMNS[3:]]
    _write_csv(raw_file, [bad_header])

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert "Expected column 3" in str(exc.value)


def test_read_raw_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when decimal conversion fails."""

    raw_file = tmp_path / "raw_bad_decimal.csv"
    rows = [
        COLUMNS,
        [
            "2024-01-04",
            "DIVIDEND",
            "XYZ",
            "bad",
            "0.10",
            "",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in column 'quantity'" in message


def test_read_raw_transactions_empty_file(tmp_path: Path) -> None:
    """Error when RAW CSV is empty."""

    raw_file = tmp_path / "empty.csv"
    raw_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert "CSV file is empty" in str(exc.value)


def test_read_raw_transactions_applies_ticker_renames(tmp_path: Path) -> None:
    """Rename known tickers according to configuration."""

    raw_file = tmp_path / "raw_ticker_rename.csv"
    rows = [
        COLUMNS,
        [
            "2024-01-05",
            "BUY",
            "FB",
            "1",
            "10.00",
            "0.00",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    assert len(transactions) == 1
    assert transactions[0].symbol == "META"


def test_read_raw_transactions_transfer_from_spouse(tmp_path: Path) -> None:
    """Parse a RAW TRANSFER_FROM_SPOUSE row: shares arriving at a stated cost."""

    raw_file = tmp_path / "raw_transfer_from_spouse.csv"
    rows = [
        COLUMNS,
        ["2024-03-16", "TRANSFER_FROM_SPOUSE", "XYZ", "21.5", "95.60", "0.00", "GBP"],
    ]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER_FROM_SPOUSE
    assert transaction.quantity == Decimal("21.5")
    assert transaction.price == Decimal("95.60")


def test_read_raw_transactions_gift(tmp_path: Path) -> None:
    """Parse a RAW GIFT row: shares given away, priced at market value."""

    raw_file = tmp_path / "raw_gift.csv"
    rows = [
        COLUMNS,
        ["2024-03-16", "GIFT", "XYZ", "21.5", "480.00", "0.00", "USD"],
    ]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.GIFT
    assert transaction.quantity == Decimal("21.5")
    assert transaction.price == Decimal("480.00")
