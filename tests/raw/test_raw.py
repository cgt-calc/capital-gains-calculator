"""Test raw format support."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
import io
import logging
from pathlib import Path
import re
import subprocess
import sys

import pytest

from cgt_calc.args_validators import STDIN_PATH
from cgt_calc.exceptions import (
    AmountMissingError,
    InvalidTransactionError,
    ParsingError,
)
from cgt_calc.logging import force_utf8_stdio
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.parsers.raw import COLUMNS, RawParser
from tests.general.test_calc import create_calculator, get_report
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


def test_bond_fund_typed_in_lower_case_is_reported_as_interest(tmp_path: Path) -> None:
    """--interest-fund-tickers matches a symbol typed in lower case.

    The option's values are upper case, so `iglt` never matched `IGLT` and
    the bond fund's distribution was reported as a dividend. 100 units at
    £0.25 pay £25.00, all foreign interest and none of it dividends.
    """
    raw_file = tmp_path / "raw.csv"
    _write_csv(
        raw_file,
        [
            COLUMNS,
            ["2023-05-02", "TRANSFER", "", "1", "1100.00", "0.00", "GBP"],
            ["2023-05-02", "BUY", "iglt", "100", "11.00", "0.00", "GBP"],
            ["2023-07-03", "DIVIDEND", "iglt", "100", "0.25", "0.00", "GBP"],
        ],
    )

    cmd = build_cmd(
        "--year",
        "2023",
        "--raw-file",
        str(raw_file),
        "--interest-fund-tickers",
        "IGLT",
        "--output",
        str(tmp_path / "out"),
    )
    result = run_cli(cmd)
    assert re.search(r"\n  Foreign proceeds: +£25\.00\n", result.stdout)
    assert re.search(r"\n  Proceeds: +£0\.00\n", result.stdout)


def test_a_split_day_moved_into_raw_computes_with_the_balance_check_on(
    tmp_path: Path,
) -> None:
    """The route the `cannot be placed either side of it` refusal gives works.

    Left in the Schwab export, the purchase of 20 on the day of the split is
    refused: the export has no times, so nothing says whether the 20 are
    before or after it. Following the advice, the purchase moves out of a
    working copy of the export into RAW, in its real order after the split,
    with a STOCK_SPLIT row for the whole holding and a TRANSFER row depositing
    the purchase's cost, because RAW rows keep a cash balance of their own.

    - 10 bought at $100 on 2 May: $1,000 at 1.2457 dollars to the pound is
      £802.76.
    - The split adds 90, so the 10 become 100.
    - 20 bought after it at $10.50: $210 at 1.2709 dollars to the pound is
      £165.24.
    - The holding is 120 units at £968.00 (802.7615 + 165.2372 = 967.9987).
    - Schwab's balance is $1,210 deposited less $1,000 spent, $210; RAW's is
      the $210 deposited less the $210 spent, $0.
    """
    schwab_file = tmp_path / "schwab.csv"
    _write_csv(
        schwab_file,
        [
            [
                "Date",
                "Action",
                "Symbol",
                "Description",
                "Quantity",
                "Price",
                "Fees & Comm",
                "Amount",
            ],
            ["06/10/2024", "Stock Split", "FOO", "FOO CORP", "90", "", "", ""],
            [
                "05/02/2024",
                "Buy",
                "FOO",
                "FOO CORP",
                "10",
                "$100.00",
                "$0.00",
                "-$1000.00",
            ],
            [
                "05/01/2024",
                "MoneyLink Transfer",
                "",
                "Tfr BANK",
                "",
                "",
                "",
                "$1210.00",
            ],
        ],
    )
    raw_file = tmp_path / "raw.csv"
    _write_csv(
        raw_file,
        [
            COLUMNS,
            ["2024-06-10", "TRANSFER", "", "1", "210", "0", "USD"],
            ["2024-06-10", "STOCK_SPLIT", "FOO", "90", "0", "0", "USD"],
            ["2024-06-10", "BUY", "FOO", "20", "10.50", "0", "USD"],
        ],
    )

    cmd = build_cmd(
        "--year",
        "2024",
        "--schwab-file",
        str(schwab_file),
        "--raw-file",
        str(raw_file),
        "--no-report",
    )
    result = run_cli(cmd)

    assert stderr_alerts(result.stderr) == []
    assert (
        "Final balance\n  Charles Schwab: 210.00 (USD)\n  Unknown: 0.00 (USD)\n"
        in result.stdout
    )
    assert (
        "Portfolio at the end of 2024/2025 tax year\n  FOO: 120.00, £968.00\n"
        in result.stdout
    )


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


def test_read_raw_transactions_with_broker_and_replaces_columns(
    tmp_path: Path,
) -> None:
    """A header may go on with `broker`, then `replaces`, read like the seven.

    A blank broker is the RAW file's own ledger, `Unknown`, as every row was
    before the column; a blank `replaces` adds the row rather than replacing.
    """
    raw_file = tmp_path / "raw.csv"
    _write_csv(
        raw_file,
        [
            [*COLUMNS, " Broker ", "REPLACES"],
            [
                "2024-06-03",
                "DIVIDEND_TAX",
                "FOO",
                "1",
                "-5.00",
                "0",
                "USD",
                "Charles Schwab",
                "2024-06-27",
            ],
            ["2024-06-04", "DIVIDEND", "FOO", "1", "1.00", "0", "USD", "Freetrade", ""],
            ["2024-06-05", "TRANSFER", "", "1", "2.00", "0", "USD", "", ""],
        ],
    )

    transactions = RawParser().load_from_file(raw_file)

    assert [(row.broker, row.replaces) for row in transactions] == [
        ("Charles Schwab", dt.date(2024, 6, 27)),
        ("Freetrade", None),
        ("Unknown", None),
    ]


def test_read_raw_transactions_with_a_broker_column_alone(tmp_path: Path) -> None:
    """`broker` can end the header: `replaces` is optional after it."""
    raw_file = tmp_path / "raw.csv"
    _write_csv(
        raw_file,
        [
            [*COLUMNS, "broker"],
            ["2024-06-04", "DIVIDEND", "FOO", "1", "1.00", "0", "USD", "Freetrade"],
        ],
    )

    transactions = RawParser().load_from_file(raw_file)

    assert [(row.broker, row.replaces) for row in transactions] == [("Freetrade", None)]


_TAX_ROW = ["2024-06-03", "DIVIDEND_TAX", "FOO", "1", "-5.00", "0", "USD"]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(_TAX_ROW, id="seven columns"),
        pytest.param([*_TAX_ROW, "Charles Schwab", "2024-06-27"], id="nine columns"),
    ],
)
def test_a_first_row_of_data_is_not_a_raw_header(
    tmp_path: Path, row: list[str]
) -> None:
    """The header check refuses a row of data, whatever its width.

    It is the one check that tells a RAW header from a first row of data, so
    a file without a header is never taken for one that has it, with or
    without the optional columns.
    """
    with pytest.raises(
        ParsingError, match="Expected column 1 to be 'date' but found '2024-06-03'"
    ):
        RawParser._validate_header(row, tmp_path / "raw.csv")  # noqa: SLF001


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        pytest.param(
            [[*_TAX_ROW, "Charles Schwab"]],
            "row 1: This row has 8 columns, not 7",
            id="no header, eight columns",
        ),
        pytest.param(
            [[*COLUMNS, "account"]],
            "row 1: Expected column 8 to be 'broker' but found 'account'",
            id="eighth column misnamed",
        ),
        pytest.param(
            [COLUMNS[:6]],
            "row 1: This row has 6 columns, not 7",
            id="header of six columns",
        ),
        pytest.param(
            [[*COLUMNS, "broker", "replaces", "note"]],
            "row 1: This row has 10 columns, not 9",
            id="header of ten columns",
        ),
        pytest.param(
            [[*COLUMNS, "replaces", "broker"]],
            "row 1: Expected column 8 to be 'broker' but found 'replaces'",
            id="optional columns out of order",
        ),
        pytest.param(
            [[*COLUMNS, "broker", "replaces"], [*_TAX_ROW, "Charles Schwab"]],
            "row 2: This row has 8 columns, not 9",
            id="row narrower than its header",
        ),
        pytest.param(
            [[*COLUMNS, "broker", "replaces"], [*_TAX_ROW, "", "2024-06-27"]],
            "row 2: A row that replaces a row of a broker export names that "
            "broker in the 'broker' column.",
            id="replaces without a broker",
        ),
        pytest.param(
            [
                [*COLUMNS, "broker", "replaces"],
                [
                    "2024-06-03",
                    "BUY",
                    "FOO",
                    "1",
                    "5.00",
                    "0",
                    "USD",
                    "Charles Schwab",
                    "2024-06-27",
                ],
            ],
            "row 2: A BUY row cannot replace a row of a broker export: only "
            "DIVIDEND_TAX and STOCK_ACTIVITY rows can. Edit a working copy of "
            "the export instead.",
            id="replaces on a purchase",
        ),
        pytest.param(
            [
                [*COLUMNS, "broker", "replaces"],
                [*_TAX_ROW, "Charles Schwab", "2024-02-30"],
            ],
            "row 2: Invalid replaces date: '2024-02-30', expected YYYY-MM-DD, "
            "e.g. '2023-08-15'",
            id="replaces not a date",
        ),
        pytest.param(
            [
                [*COLUMNS, "broker", "replaces"],
                [*_TAX_ROW[:4], "5.00", "0", "USD", "Charles Schwab", ""],
            ],
            "row 2: DIVIDEND_TAX cannot be positive.",
            id="positive tax added to a named broker",
        ),
    ],
)
def test_read_raw_transactions_refuses_a_bad_broker_or_replaces_column(
    tmp_path: Path, rows: list[list[str]], message: str
) -> None:
    """Each optional-column rule refuses the row or header that breaks it.

    Only a header names the optional columns, so a file without one keeps
    to the seven. Naming a broker does not lift the refusal of positive tax:
    only a row replacing an export row with that same amount may be positive.
    """
    raw_file = tmp_path / "raw.csv"
    _write_csv(raw_file, rows)

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert f"While parsing {raw_file}, {message}" in str(exc.value)


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
    ("action", "symbol", "quantity", "price"),
    [
        ("DIVIDEND_TAX", "XYZ", "1", "5.00"),
        ("INTEREST_TAX", "", "1", "5.00"),
        ("DIVIDEND_TAX", "XYZ", "-1", "-5.00"),
    ],
    ids=["dividend tax", "interest tax", "negative quantity and price"],
)
def test_read_raw_transactions_refuses_positive_tax(
    tmp_path: Path, action: str, symbol: str, quantity: str, price: str
) -> None:
    """Refuse tax with a positive amount, a sign typed the wrong way round.

    If accepted, 1 x 5.00 would be reported as 5.00 of negative tax paid and
    added to the cash balance instead of taken from it. The amount is what
    is checked: -1 x -5.00 - 0.00 is 5.00 too, though the price is negative.
    """
    raw_file = tmp_path / "raw_positive_tax.csv"
    rows = [COLUMNS, ["2024-06-15", action, symbol, quantity, price, "0.00", "USD"]]
    _write_csv(raw_file, rows)

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    message = str(exc.value)
    assert "row 2" in message
    assert (
        f"{action} cannot be positive. Enter the tax deducted as a negative "
        "price; if some of it was refunded, enter only the part that was not."
    ) in message


@pytest.mark.parametrize(
    ("action", "symbol", "quantity", "price", "amount"),
    [
        ("DIVIDEND_TAX", "XYZ", "-1", "5.00", "-5.00"),
        ("INTEREST_TAX", "", "1", "0.00", "0"),
    ],
    ids=["negative quantity", "zero"],
)
def test_read_raw_transactions_accepts_tax_that_is_not_positive(
    tmp_path: Path, action: str, symbol: str, quantity: str, price: str, amount: str
) -> None:
    """Accept tax whose amount is not positive, whatever the sign of its price.

    The amount is quantity x price - fees: -1 x 5.00 - 0.00 is -5.00, the
    same deduction as 1 x -5.00. Tax of nothing, 1 x 0.00, is not positive.
    """
    raw_file = tmp_path / "raw_tax.csv"
    rows = [COLUMNS, ["2024-06-15", action, symbol, quantity, price, "0.00", "USD"]]
    _write_csv(raw_file, rows)

    transactions = RawParser().load_from_file(raw_file)

    assert [transaction.amount for transaction in transactions] == [Decimal(amount)]


def test_a_raw_tax_row_with_no_price_stops_as_amount_missing(tmp_path: Path) -> None:
    """A tax row with a blank price has no amount to check the sign of.

    It stops with the error any other row without an amount gives, which the
    guide names, rather than failing inside the sign check.
    """
    raw_file = tmp_path / "raw_tax_no_price.csv"
    rows = [COLUMNS, ["2024-06-15", "INTEREST_TAX", "", "1", "", "0.00", "GBP"]]
    _write_csv(raw_file, rows)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(AmountMissingError, match=r"^Amount missing\.\n"):
        calculator.prepare_history(list(RawParser().load_from_file(raw_file)))


@pytest.mark.parametrize(
    ("rows", "first_row", "conflicting_row"),
    [
        (
            [
                ["2023-05-01", "BUY", "foo", "10", "50", "1", "GBP"],
                ["2023-05-02", "BUY", "FOO", "10", "100", "1", "GBP"],
                ["2023-06-10", "SELL", "FOO", "10", "110", "1", "GBP"],
            ],
            2,
            3,
        ),
        (
            [
                ["2023-04-03", "BUY", "BAR", "5", "20", "1", "GBP"],
                ["2023-05-01", "BUY", "foo", "10", "50", "1", "GBP"],
                ["2023-05-15", "SELL", "BAR", "5", "22", "1", "GBP"],
                ["2023-06-10", "SELL", "FOO", "10", "110", "1", "GBP"],
            ],
            3,
            5,
        ),
    ],
    ids=["observed", "another symbol before and between"],
)
def test_read_raw_transactions_refuses_symbols_differing_only_by_letter_case(
    tmp_path: Path, rows: list[list[str]], first_row: int, conflicting_row: int
) -> None:
    """A symbol typed in another letter case is refused, naming both rows.

    Symbols are matched exactly, so `foo` and `FOO` were two holdings. Before
    this check, the observed file's sale was matched only with the `FOO`
    purchase: a gain of 1,099 - 1,001 = 98. Pooled, the 20 shares cost 501 +
    1,001 = 1,502, so the 10 sold cost 751 and the gain is 1,099 - 751 = 348.
    In the second file the two spellings are not neighbours, and the first
    spelling is not on the first data row (row 2; the header is row 1). The
    parser stops at the first conflict; later conflicting rows are not reported.
    """
    raw_file = tmp_path / "raw_letter_case.csv"
    _write_csv(raw_file, [COLUMNS, *rows])

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert str(exc.value) == (
        f"While parsing {raw_file}: RAW symbols 'foo' (row {first_row}) and 'FOO' "
        f"(row {conflicting_row}) differ only by letter case. "
        "Use one spelling consistently."
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


@pytest.mark.parametrize(
    "row",
    [
        "2024-01-02,BUY, BRK B ,10,2.50,0.10,USD",
        " 2024-01-02 ,BUY,BRK B,10,2.50,0.10,USD",
        "2024-01-02, BUY ,BRK B,10,2.50,0.10,USD",
        '2024-01-02,BUY, "BRK B" ,10,2.50,0.10,USD',
    ],
    ids=["symbol", "date", "action", "symbol in quotes"],
)
def test_read_raw_transactions_ignores_spaces_around_a_value(
    tmp_path: Path, row: str
) -> None:
    """A space typed beside a comma is not part of the value; one inside it is.

    A symbol kept with its space, or with the quotes typed after one, would be
    a holding of its own, apart from the same symbol typed without them.
    """
    raw_file = tmp_path / "raw_spaces.csv"
    raw_file.write_text(
        f"date,action,symbol,quantity,price,fees,currency\n{row}\n", encoding="utf-8"
    )

    (transaction,) = RawParser().load_from_file(raw_file)

    assert transaction.date == dt.date(2024, 1, 2)
    assert transaction.action == ActionType.BUY
    assert transaction.symbol == "BRK B"


def test_read_raw_transactions_reads_a_cell_of_spaces_as_empty(tmp_path: Path) -> None:
    """A cell holding only spaces is an empty cell: no symbol, and no fee."""
    raw_file = tmp_path / "raw_cell_of_spaces.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2024-01-03,TRANSFER, ,1,1000.00, ,GBP\n",
        encoding="utf-8",
    )

    (transaction,) = RawParser().load_from_file(raw_file)

    assert transaction.symbol is None
    assert transaction.fees == Decimal(0)


def test_read_raw_transactions_refuses_a_row_that_runs_over_several_lines(
    tmp_path: Path,
) -> None:
    """A double quote left open takes in the rows below it; they are not dropped.

    The quote opened on row 2 is closed by the one on row 5, so the four rows
    would be read as one purchase of 5 with the rest inside its symbol.
    """
    raw_file = tmp_path / "raw_open_quote.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        '2024-01-02,BUY,"BRK B,10,2.50,0.10,USD\n'
        "2024-01-03,BUY,XYZ,10,2.50,0.10,USD\n"
        "2024-01-04,BUY,XYZ,10,2.50,0.10,USD\n"
        '2024-01-05,BUY,"BRK B,5,3.00,0.10,USD\n',
        encoding="utf-8",
    )

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert exc.value.row_index == 2
    # The whole message, once: the other readers' tests stop at the rows.
    assert exc.value.detail == (
        "This row runs on to row 5, because one of its cells holds a line break. "
        'If a double quote (") is opened on it and not closed, every row down to '
        "there was read as part of that cell: close or remove the double quote. "
        "Otherwise take the line break out of the cell."
    )


def test_read_raw_transactions_names_the_row_of_a_cell_too_long_to_read(
    tmp_path: Path,
) -> None:
    """A double quote left open high up a long file is reported, with its row.

    The reader gives up once the cell the quote opened passes 131,072
    characters, before it hands the row over. That ended in a traceback naming
    neither the file nor the row. The 4,000 rows below the quote come to
    144,000 characters.
    """
    row = "2024-01-03,BUY,XYZ,10,2.50,0.10,USD\n"
    raw_file = tmp_path / "raw_long_open_quote.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2024-01-02,BUY,XYZ,10,2.50,0.10,USD\n"
        '2024-01-03,BUY,"XYZ,10,2.50,0.10,USD\n' + row * 4000,
        encoding="utf-8",
    )

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert exc.value.row_index == 3
    assert exc.value.detail == (
        "A cell that starts on this row is longer than a CSV cell can be "
        '(131,072 characters). A double quote (") is probably opened on it and '
        "not closed: close or remove it."
    )


def test_read_raw_transactions_names_the_row_of_another_reader_error() -> None:
    """Whatever else the CSV reader refuses is reported with its row and its words.

    A carriage return alone inside a line is one such case. It reaches the
    reader when a line that ends in one is read from a stream that leaves it
    as it is, as piped input can be. Here the third line ends in one, so the
    fourth is read as part of it.
    """
    stream = io.StringIO(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2024-01-02,BUY,XYZ,10,2.50,0.10,USD\n"
        "2024-01-03,BUY,XYZ,10,2.50,0.10,USD\r"
        "2024-01-04,BUY,XYZ,10,2.50,0.10,USD\n"
    )

    with pytest.raises(
        ParsingError,
        match=(
            ", row 3: The CSV reader could not read this row: "
            "new-line character seen in unquoted field"
        ),
    ):
        RawParser().load_from_stream(stream, STDIN_PATH)


def test_read_raw_transactions_reports_a_half_filled_row(tmp_path: Path) -> None:
    """A row with any cell filled is not blank: it is reported, at its own line."""
    raw_file = tmp_path / "raw_half_filled.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n\n,BUY,XYZ,10,2.50,0,USD\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert exc.value.row_index == 3


def test_read_raw_transactions_unknown_action_names_its_row(tmp_path: Path) -> None:
    """A row the parser refuses outright is reported at its own line too.

    The row above it is a good one, so an error dropped on the way out would
    leave two transactions and no report at all. The user wrote the row, so
    it is not marked as needing a cgt-calc update, unlike a broker's.
    """
    raw_file = tmp_path / "raw_unknown_action.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2023-02-09,BUY,XYZ,10,2.50,0,USD\n"
        "2023-02-10,NONSENSE,XYZ,10,2.50,0,USD\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="Unknown action: NONSENSE") as exc:
        RawParser().load_from_file(raw_file)

    assert exc.value.row_index == 3
    assert not exc.value.needs_update


@pytest.mark.parametrize(
    "date",
    [
        pytest.param("15/08/2023", id="day first"),
        # Written the right way round, but June has 30 days.
        pytest.param("2023-06-31", id="no such day"),
    ],
)
def test_read_raw_transactions_quotes_an_unreadable_date_with_the_form_it_takes(
    tmp_path: Path, date: str
) -> None:
    """A date that cannot be read is quoted at its own row, with the form RAW takes.

    The header is row 1 and a good row is row 2, so the date is on row 3.
    The user wrote the file, so it is not marked as needing a cgt-calc update.
    """
    raw_file = tmp_path / "raw_bad_date.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2023-08-14,BUY,XYZ,10,2.50,0,USD\n"
        f"{date},BUY,XYZ,10,2.50,0,USD\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError) as exc:
        RawParser().load_from_file(raw_file)

    assert str(exc.value) == (
        f"While parsing {raw_file}, row 3: Invalid date: '{date}', "
        "expected YYYY-MM-DD, e.g. '2023-08-15'"
    )
    assert not exc.value.needs_update


@pytest.mark.parametrize(
    "content", ["", "\n   \n,,,,,,\n"], ids=["no bytes", "only blank rows"]
)
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


# What a refused sale says about the purchases written below it, before the
# advice every refused sale ends with.
_CHECK_THE_ORDER = (
    "A RAW file's rows on one date are read in the order written, as the order "
    "the trades happened, and the holding is checked after each row. Check the "
    "order these trades really happened in. If the purchase came first, write it "
    "above the sale. If the sale came first, keep that order: unless it was a "
    "short sale, of shares not yet held, which cgt-calc does not support, the "
    "shares it sold were already held. "
)


def _usual_advice(raw_file: Path | None) -> str:
    """Return the advice every refused sale ends with, naming where a RAW row goes."""
    where = (
        "a file passed with --raw-file"
        if raw_file is None
        else f"your RAW file ({raw_file})"
    )
    return (
        "Check that the history includes the purchase, vest or transfer that "
        "acquired the shares and any split or rename since, and that no disposal "
        "appears twice. A purchase, vest or split the exports cannot supply can go "
        f"in {where}, using an action listed at "
        "https://cgt-calc.uk/brokers/raw/#actions-to-use; a rename cannot: report "
        "the old and new tickers so the pair can be added."
    )


def _refusal(transactions: list[BrokerTransaction]) -> str:
    """Run a 2024/25 calculation that must be refused, and return why."""
    with pytest.raises(InvalidTransactionError) as error:
        get_report(create_calculator(tax_year=2024, balance_check=False), transactions)
    # The rows beneath the message describe the refused transaction.
    return error.value.message.split("\n")[0]


def _raw_rows(tmp_path: Path, rows: list[list[str]]) -> list[BrokerTransaction]:
    """Write `rows` as a RAW file and read them back as the CLI would."""
    _write_csv(tmp_path / "raw.csv", [COLUMNS, *rows])
    return list(RawParser().load_from_file(tmp_path / "raw.csv"))


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        pytest.param(
            [
                ["2024-05-01", "SELL", "AAA", "10", "5.00", "0", "GBP"],
                ["2024-05-01", "BUY", "AAA", "10", "4.00", "0", "GBP"],
            ],
            "Tried to sell AAA on 2024-05-01, but no AAA is held. The same file "
            "buys 10 AAA lower down the same date (row 3), enough to cover this "
            "sale.",
            id="nothing-held",
        ),
        pytest.param(
            [
                ["2024-05-01", "BUY", "AAA", "5", "4.00", "0", "GBP"],
                ["2024-05-02", "SELL", "AAA", "12", "5.00", "0", "GBP"],
                ["2024-05-02", "BUY", "AAA", "4", "4.00", "0", "GBP"],
                ["2024-05-02", "BUY", "BBB", "100", "1.00", "0", "GBP"],
                ["2024-05-02", "BUY", "AAA", "3", "4.00", "0", "GBP"],
                ["2024-05-03", "BUY", "AAA", "50", "4.00", "0", "GBP"],
            ],
            "Tried to sell 12 AAA on 2024-05-02, but the holding is 5. The same "
            "file buys 7 AAA lower down the same date (rows 4, 6), enough to cover "
            "this sale.",
            id="two-purchases-make-up-the-shortfall",
        ),
    ],
)
def test_a_sale_above_the_purchase_covering_it_names_that_purchase(
    tmp_path: Path, rows: list[list[str]], message: str
) -> None:
    """A RAW sale written above a same-day purchase that covers it says so.

    The first sells 10 with nothing held and 10 bought below. The second
    holds 5 and sells 12, and the 4 and 3 bought below make up exactly the 7
    missing; the BBB purchase between them and the AAA bought the next day
    are not counted.
    """
    raw_rows = _raw_rows(tmp_path, rows)

    assert _refusal(raw_rows) == (
        f"{message} {_CHECK_THE_ORDER}{_usual_advice(tmp_path / 'raw.csv')}"
    )


@pytest.mark.parametrize(
    ("rows", "headline"),
    [
        pytest.param(
            [
                ["2024-05-01", "SELL", "AAA", "10", "5.00", "0", "GBP"],
                ["2024-05-01", "BUY", "AAA", "9", "4.00", "0", "GBP"],
            ],
            "Tried to sell AAA on 2024-05-01, but no AAA is held.",
            id="one-share-short",
        ),
        pytest.param(
            [
                ["2024-05-01", "SELL", "AAA", "-10", "5.00", "0", "GBP"],
                ["2024-05-01", "BUY", "AAA", "10", "4.00", "0", "GBP"],
            ],
            "Tried to sell AAA on 2024-05-01, but no AAA is held.",
            id="negative-count-sold",
        ),
        pytest.param(
            [
                ["2024-05-01", "BUY", "AAA", "5", "4.00", "0", "GBP"],
                ["2024-05-01", "SELL", "AAA", "10", "5.00", "0", "GBP"],
            ],
            "Tried to sell 10 AAA on 2024-05-01, but the holding is 5.",
            id="purchase-above-already-counted",
        ),
        pytest.param(
            [
                ["2024-05-01", "SELL", "AAA", "10", "5.00", "0", "GBP"],
                ["2024-05-01", "SELL", "AAA", "10", "5.00", "0", "GBP"],
            ],
            "Tried to sell AAA on 2024-05-01, but no AAA is held.",
            id="sale-below",
        ),
    ],
)
def test_a_sale_the_purchases_below_it_would_not_cover_keeps_the_usual_advice(
    tmp_path: Path, rows: list[list[str]], headline: str
) -> None:
    """Purchases below a sale are named only where reordering could help.

    9 bought below a sale of 10 leave it one short. A sale of a negative
    count is not a sale any purchase covers. The 5 bought above a sale of 10
    are already in the holding it finds, and nothing is bought below it.
    A second sale below the first buys nothing.
    """
    raw_rows = _raw_rows(tmp_path, rows)

    assert _refusal(raw_rows) == f"{headline} {_usual_advice(tmp_path / 'raw.csv')}"


def _export_row(action: ActionType, quantity: int, index: int) -> BrokerTransaction:
    """Read an AAA trade of 2024-05-01 from a broker export, at GBP 4 a share.

    An export does not say that a day's rows are in the order they happened.
    """
    return BrokerTransaction(
        date=dt.date(2024, 5, 1),
        action=action,
        symbol="AAA",
        description="",
        quantity=Decimal(quantity),
        price=Decimal(4),
        fees=Decimal(0),
        amount=Decimal(-4 * quantity if action is ActionType.BUY else 4 * quantity),
        currency=CurrencyCode("GBP"),
        broker="Export",
        source=TransactionSource(
            parser="Export",
            account="export",
            file=Path("export.csv"),
            row=index + 2,
            index=index,
        ),
    )


def test_a_broker_export_s_row_order_is_not_pointed_out() -> None:
    """Only a RAW file records which of a day's rows came first.

    An export listing a sale of 10 above the purchase of 10 it draws on says
    nothing about which came first, so the usual advice stands.
    """
    rows = [_export_row(ActionType.SELL, 10, 0), _export_row(ActionType.BUY, 10, 1)]

    assert _refusal(rows) == (
        f"Tried to sell AAA on 2024-05-01, but no AAA is held. {_usual_advice(None)}"
    )


def test_a_purchase_from_another_input_is_not_read_as_lower_down(
    tmp_path: Path,
) -> None:
    """Row positions in two files say nothing about each other.

    An export's purchase of 10, its sixth row, is read before the RAW file,
    so the RAW sale of 15 below it finds 10 held. The export's purchase is
    already in that count, and its row number is no position in the RAW file.
    """
    raw_rows = _raw_rows(
        tmp_path, [["2024-05-01", "SELL", "AAA", "15", "5.00", "0", "GBP"]]
    )

    assert _refusal([_export_row(ActionType.BUY, 10, 5), *raw_rows]) == (
        "Tried to sell 15 AAA on 2024-05-01, but the holding is 10. "
        f"{_usual_advice(tmp_path / 'raw.csv')}"
    )
