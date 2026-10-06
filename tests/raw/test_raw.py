"""Test raw format support."""

from __future__ import annotations

import csv
from decimal import Decimal
import io
import logging
from pathlib import Path
import re
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


MARKED_CSV = (
    b"\xef\xbb\xbf"
    b"date,action,symbol,quantity,price,fees,currency\n"
    b"2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD\n"
)


@pytest.mark.parametrize(
    ("csv_bytes", "symbol"),
    [
        (
            (
                "date,action,symbol,quantity,price,fees,currency\n"
                "2023-02-09,DIVIDEND,CAFÉ,4200,0.80,0.0,USD\n"
            ).encode(),
            "CAFÉ",
        ),
        (MARKED_CSV, "OPRA"),
    ],
    ids=["symbol outside ASCII", "byte-order mark"],
)
def test_stdin_decodes_utf8_under_a_legacy_locale(
    monkeypatch: pytest.MonkeyPatch, csv_bytes: bytes, symbol: str
) -> None:
    """A UTF-8 export piped in keeps its characters on a legacy code page.

    Windows decodes redirected stdin with the ANSI code page, so a symbol
    outside ASCII would otherwise arrive as mojibake, and a leading
    byte-order mark as stray characters in front of the first column name.
    """
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(csv_bytes), encoding="cp1252")
    )
    force_utf8_stdio()

    transactions = RawParser.load_from_file(STDIN_PATH, show_parsing_msg=False)

    assert [txn.symbol for txn in transactions] == [symbol]


def test_a_byte_order_mark_is_not_read_as_data(tmp_path: Path) -> None:
    """A file saved as Excel's "CSV UTF-8" starts with a mark, not a column."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_bytes(MARKED_CSV)

    transactions = RawParser.load_from_file(raw_file, show_parsing_msg=False)

    assert [txn.symbol for txn in transactions] == ["OPRA"]


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


def test_read_raw_transactions_negative_fees(tmp_path: Path) -> None:
    """Refuse negative fees, a sign typed the wrong way round."""
    raw_file = tmp_path / "raw_negative_fees.csv"
    rows = [
        COLUMNS,
        ["2024-01-04", "BUY", "XYZ", "100", "10.00", "-50.00", "GBP"],
    ]
    _write_csv(raw_file, rows)

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    message = str(exc.value)
    assert "row 2" in message
    assert "Fees cannot be negative. Enter the fees you paid as a positive number." in (
        message
    )


@pytest.mark.parametrize(
    "blank", ["", "   ", ",,,,,,"], ids=["empty line", "only spaces", "only commas"]
)
def test_read_raw_transactions_skips_a_blank_row(tmp_path: Path, blank: str) -> None:
    """A row with nothing in it is ignored, and the rows after it keep their lines."""
    raw_file = tmp_path / "raw_blank_row.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2024-01-02,BUY,XYZ,10,2.50,0,USD\n"
        f"{blank}\n"
        "2024-01-03,BUY,XYZ,5,3.00,0,USD\n",
        encoding="utf-8",
    )

    transactions = RawParser().load_from_file(raw_file)

    assert [t.source.row for t in transactions if t.source] == [2, 4]


def test_read_raw_transactions_reports_a_half_filled_row(tmp_path: Path) -> None:
    """A row with any cell filled is not blank: it is reported, at its own line."""
    raw_file = tmp_path / "raw_half_filled.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n\n,BUY,XYZ,10,2.50,0,USD\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="does not match format") as exc:
        RawParser().load_from_file(raw_file)

    assert exc.value.row_index == 3


@pytest.mark.parametrize("content", ["", "\n\n"], ids=["no bytes", "only blank lines"])
def test_read_raw_transactions_empty_file(tmp_path: Path, content: str) -> None:
    """Error when RAW CSV is empty."""

    raw_file = tmp_path / "empty.csv"
    raw_file.write_text(content, encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert "CSV file is empty" in str(exc.value)


@pytest.mark.parametrize(
    ("symbol", "date", "expected"),
    [
        pytest.param("FB", "2012-05-18", "META", id="listing-day"),
        pytest.param("FB", "2023-09-01", "META", id="kept-after-the-change"),
        pytest.param("FB", "2025-06-25", "META", id="last-day-unused"),
        pytest.param("FB", "2025-06-26", "FB", id="reused"),
        pytest.param("FB", "2002-06-03", "FB", id="used-before"),
        pytest.param("BLL", "2021-06-01", "BALL", id="no-dates-stated"),
    ],
)
def test_a_renamed_ticker_without_an_isin_is_renamed_by_its_date(
    tmp_path: Path, symbol: str, date: str, expected: str
) -> None:
    """A row without an ISIN is renamed only while nobody else had the ticker.

    FB was Meta's from its 2012 listing, kept by some histories after Meta's
    2022 rename, and a ProShares ETF's from 26 June 2025; FBR Asset
    Investment used it in 2002-2003. Ball's rename states neither date,
    since no other security is known to have used BLL on an exchange a
    supported broker offers, so it applies on any.
    """
    raw_file = tmp_path / "raw.csv"
    _write_csv(raw_file, [COLUMNS, [date, "BUY", symbol, "1", "10.00", "0.00", "USD"]])

    (transaction,) = RawParser().load_from_file(raw_file)

    assert transaction.symbol == expected


@pytest.mark.parametrize(
    ("dates", "message"),
    [
        pytest.param(
            ("2021-06-01", "2025-06-26"),
            "Rows under FB fall on both sides of 2025-06-26, when another security "
            "started trading as FB, so cgt-calc cannot tell which rows belong to "
            "Meta Platforms. Write META on every Meta Platforms row.",
            id="reused",
        ),
        pytest.param(
            ("2002-06-03", "2021-06-01"),
            "Rows under FB fall on both sides of 2012-05-18, when Meta Platforms "
            "started trading as FB, so cgt-calc cannot tell which rows belong to "
            "Meta Platforms. Write META on every Meta Platforms row.",
            id="used-before",
        ),
    ],
)
def test_rows_on_both_sides_of_a_ticker_changing_hands_are_refused(
    tmp_path: Path, dates: tuple[str, str], message: str
) -> None:
    """Without an ISIN, nothing says which of the rows are which security's.

    FB rows from 2021 and 2025 may be Meta and the ProShares ETF, or Meta
    throughout.
    """
    raw_file = tmp_path / "raw.csv"
    _write_csv(
        raw_file,
        [
            COLUMNS,
            *([date, "BUY", "FB", "1", "10.00", "0.00", "USD"] for date in dates),
        ],
    )

    with pytest.raises(ParsingError, match=re.escape(message)):
        RawParser().load_from_file(raw_file)


def test_rows_that_are_all_another_security_s_are_left_alone(tmp_path: Path) -> None:
    """FB rows from 2002 and July 2025 are none of them Meta's.

    They fall on both sides of both days FB changed hands, but no row would
    be renamed, so there is nothing to tell apart.
    """
    raw_file = tmp_path / "raw.csv"
    _write_csv(
        raw_file,
        [
            COLUMNS,
            ["2002-06-03", "BUY", "FB", "1", "10.00", "0.00", "USD"],
            ["2025-07-01", "BUY", "FB", "1", "10.00", "0.00", "USD"],
        ],
    )

    transactions = RawParser().load_from_file(raw_file)

    assert [transaction.symbol for transaction in transactions] == ["FB", "FB"]


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
