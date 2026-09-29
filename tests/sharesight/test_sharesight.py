"""Tests for the Sharesight parser."""

from __future__ import annotations

import csv
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import InvalidTransactionError, ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.sharesight import SharesightParser

if TYPE_CHECKING:
    from pathlib import Path
from pathlib import Path

from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_run_with_sharesight_files_no_balance_check(
    request: pytest.FixtureRequest,
) -> None:
    """Runs the tool and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--sharesight-dir",
        "tests/sharesight/data/inputs/",
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests")
        / "sharesight"
        / "data"
        / "test_run_with_sharesight_files_no_balance_check_output.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_real_exports_preserve_physical_row_numbers() -> None:
    """Keep the physical CSV line for trades and income transactions."""
    transactions = SharesightParser.load_from_dir(Path("tests/sharesight/data/inputs"))

    rows = {
        (transaction.date.isoformat(), transaction.symbol, transaction.action): (
            transaction.source.row if transaction.source else None
        )
        for transaction in transactions
    }
    assert rows["2019-08-01", "FX:ETH", ActionType.BUY] == 4
    assert rows["2020-11-10", "FUND1.UKF", ActionType.DIVIDEND] == 7
    assert rows["2020-10-10", "FOO.NASDAQ", ActionType.DIVIDEND_TAX] == 14


def test_parse_income_report_missing_local_column(tmp_path: Path) -> None:
    """Error when Sharesight local dividend header omits required column."""
    file_path = tmp_path / "Taxable Income Report.csv"
    _write_csv(
        file_path,
        [
            ["Test Portfolio"],
            [""],
            ["Local Income"],
            [""],
            ["Dividend Payments"],
            [
                "Code",
                "Name",
                "Date Paid",
                "Net Dividend",
                "Tax Deducted",
                "Tax Credit",
                "Comments",
            ],
            ["ABC", "Example", "01/01/2020", "10", "0", "0", "Note"],
            ["Total"],
        ],
    )

    with pytest.raises(
        ParsingError,
        match="Missing expected columns in Sharesight local dividend header: Gross Dividend",
    ) as excinfo:
        list(SharesightParser().load_from_dir(tmp_path))

    assert excinfo.value.row_index == 6


def test_parse_income_report_missing_foreign_column(tmp_path: Path) -> None:
    """Error when Sharesight foreign dividend header omits required column."""
    file_path = tmp_path / "Taxable Income Report.csv"
    _write_csv(
        file_path,
        [
            ["Test Portfolio"],
            [""],
            ["Foreign Income"],
            [
                "Code",
                "Name",
                "Date Paid",
                "Exchange Rate",
                "Currency",
                "Net Amount",
                "Gross Amount",
                "Comments",
            ],
            ["ABC", "Example", "01/02/2020", "1.23", "USD", "10", "12", "Note"],
            ["Total"],
        ],
    )

    with pytest.raises(
        ParsingError,
        match="Missing expected columns in Sharesight foreign dividend header: Foreign Tax Deducted",
    ) as excinfo:
        list(SharesightParser().load_from_dir(tmp_path))

    assert excinfo.value.row_index == 4


def test_parse_trade_report_missing_column(tmp_path: Path) -> None:
    """Error when trades header omits a required column."""
    file_path = tmp_path / "All Trades Report.csv"
    _write_csv(
        file_path,
        [
            [
                "Market",
                "Code",
                "Name",
                "Type",
                "Date",
                "Quantity",
                "Brokerage *",
                "Currency",
                "Exchange Rate",
                "Value",
                "Comments",
            ],
            [
                "NASDAQ",
                "ABC",
                "Example",
                "Buy",
                "01/01/2020",
                "1",
                "0",
                "USD",
                "1.2",
                "100",
                "Note",
            ],
        ],
    )

    with pytest.raises(
        ParsingError,
        match=r"Missing expected columns in Sharesight trades header: Price \*",
    ) as excinfo:
        list(SharesightParser().load_from_dir(tmp_path))

    assert excinfo.value.row_index == 1


def test_parse_trade_report_invalid_decimal(tmp_path: Path) -> None:
    """Expose row index and column name when decimal parsing fails."""
    file_path = tmp_path / "All Trades Report.csv"
    _write_csv(
        file_path,
        [
            [
                "Market",
                "Code",
                "Name",
                "Type",
                "Date",
                "Quantity",
                "Price *",
                "Brokerage *",
                "Currency",
                "Exchange Rate",
                "Value",
                "",
                "Comments",
            ],
            [
                "NASDAQ",
                "ABC",
                "Example",
                "Buy",
                "01/01/2020",
                "oops",
                "100",
                "0",
                "USD",
                "1.2",
                "1000",
                "",
                "Note",
            ],
        ],
    )

    with pytest.raises(ParsingError, match=r"Invalid decimal.*Quantity") as excinfo:
        list(SharesightParser().load_from_dir(tmp_path))

    assert excinfo.value.row_index == 2


TRADE_HEADER = [
    "Market",
    "Code",
    "Name",
    "Type",
    "Date",
    "Quantity",
    "Price *",
    "Brokerage *",
    "Currency",
    "Exchange Rate",
    "Value",
    "Comments",
]

# Exercise the newer header aliases together. This is deliberately not labelled
# as a verbatim export fixture; keep it independent of the parser mapping so a
# wrong alias there fails the tests.
ALIASED_TRADE_HEADER = [
    "Code",
    "Market Code",
    "Name",
    "Date",
    "Type",
    "Qty",
    "Price",
    "Instrument Currency",
    "Brokerage",
    "Brokerage Currency",
    "Exch. Rate",
    "Value",
    "Comments",
]

LOCAL_DIVIDEND_HEADER = [
    "Code",
    "Name",
    "Date Paid",
    "Net Dividend",
    "Tax Deducted",
    "Tax Credit",
    "Gross Dividend",
    "Comments",
]

FOREIGN_DIVIDEND_HEADER = [
    "Code",
    "Name",
    "Date Paid",
    "Exchange Rate",
    "Currency",
    "Net Amount",
    "Foreign Tax Deducted",
    "Gross Amount",
    "Comments",
]


def test_parse_income_report_with_uppercase_csv_extension(tmp_path: Path) -> None:
    """Parse an income report whose CSV extension is uppercase."""
    _write_csv(
        tmp_path / "Taxable Income Report.CSV",
        [
            ["Taxable Income Report"],
            ["Local Income"],
            ["Dividend Payments"],
            LOCAL_DIVIDEND_HEADER,
            ["ABC", "AB Corp", "01/02/2023", "90", "10", "0", "100", "local divi"],
            ["Total"],
            ["Total Local Income"],
            ["Foreign Income"],
            FOREIGN_DIVIDEND_HEADER,
            ["XYZ", "X Corp", "03/04/2023", "1.2", "USD", "85", "15", "100", "foreign"],
            ["Total"],
        ],
    )

    transactions = SharesightParser().load_from_dir(tmp_path)

    assert [transaction.action for transaction in transactions] == [
        ActionType.DIVIDEND,
        ActionType.DIVIDEND_TAX,
        ActionType.DIVIDEND,
        ActionType.DIVIDEND_TAX,
    ]
    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(-10),
        Decimal(100),
        Decimal(-15),
    ]
    assert [transaction.currency for transaction in transactions] == [
        "GBP",
        "GBP",
        "USD",
        "USD",
    ]


def test_parse_income_report_with_informational_columns(tmp_path: Path) -> None:
    """Ignore newer Taxable Income columns not used by the calculation."""
    _write_csv(
        tmp_path / "Taxable Income Report.csv",
        [
            ["Foreign Income"],
            [*FOREIGN_DIVIDEND_HEADER, "Country", "Income Type"],
            [
                "XYZ",
                "X Corp",
                "03/04/2023",
                "1.2",
                "USD",
                "85",
                "15",
                "100",
                "foreign",
                "United States",
                "Dividend",
            ],
            ["Total"],
        ],
    )

    transactions = SharesightParser().load_from_dir(tmp_path)

    assert [transaction.action for transaction in transactions] == [
        ActionType.DIVIDEND,
        ActionType.DIVIDEND_TAX,
    ]
    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(-15),
    ]


def test_parse_income_report_missing_currency_value(tmp_path: Path) -> None:
    """Raise when a foreign dividend row has no currency."""
    _write_csv(
        tmp_path / "Taxable Income Report.csv",
        [
            ["Foreign Income"],
            FOREIGN_DIVIDEND_HEADER,
            ["XYZ", "X Corp", "03/04/2023", "1.2", "", "85", "15", "100", "foreign"],
        ],
    )

    with pytest.raises(ParsingError, match="Missing currency in column"):
        SharesightParser().load_from_dir(tmp_path)


def test_parse_income_report_section_without_rows(tmp_path: Path) -> None:
    """Produce nothing when the dividend section ends at the file end."""
    _write_csv(
        tmp_path / "Taxable Income Report.csv",
        [
            ["Local Income"],
            ["Dividend Payments"],
        ],
    )

    assert SharesightParser().load_from_dir(tmp_path) == []


def test_unknown_report_is_ignored(tmp_path: Path) -> None:
    """Ignore CSV files that are not Sharesight reports."""
    _write_csv(tmp_path / "Some Other Report.csv", [["Header"], ["Data"]])

    assert SharesightParser().load_from_dir(tmp_path) == []


def test_parse_trade_report(tmp_path: Path) -> None:
    """Parse buys, sells, FX trades and stock activity."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            ["All Trades Report"],
            TRADE_HEADER,
            [
                "NASDAQ",
                "FOO",
                "Foo Inc",
                "Buy",
                "01/02/2023",
                "10",
                "5.5",
                "1",
                "USD",
                "1",
                "44",
                "buy",
            ],
            [
                "NASDAQ",
                "FOO",
                "Foo Inc",
                "Sell",
                "02/02/2023",
                "-5",
                "6",
                "",
                "USD",
                "1",
                "30",
                "sell",
            ],
            [
                "FX",
                "USDGBP",
                "FX trade",
                "Sell",
                "03/02/2023",
                "-100",
                "1.25",
                "0",
                "USD",
                "1.25",
                "80",
                "fx",
            ],
            [
                "NASDAQ",
                "NVDA",
                "Nvidia",
                "Buy",
                "04/02/2023",
                "2",
                "100",
                "0",
                "USD",
                "1",
                "",
                "Stock Activity vest",
            ],
            ["", "", "", "", "", "", "", "", "", "", "", ""],
        ],
    )

    transactions = SharesightParser().load_from_dir(tmp_path)

    assert len(transactions) == 4

    buy, sell, fx, grant = transactions
    assert buy.action == ActionType.BUY
    assert buy.symbol == "NASDAQ:FOO"
    assert buy.quantity == Decimal(10)
    assert buy.amount == Decimal(-56)
    assert buy.fees == Decimal(1)

    assert sell.action == ActionType.SELL
    assert sell.quantity == Decimal(5)
    assert sell.amount == Decimal(30)
    assert sell.fees == Decimal(0)

    # FX price is derived from the GBP value.
    assert fx.currency == "GBP"
    assert fx.price == Decimal("0.8")
    assert fx.quantity == Decimal(100)
    assert fx.amount == Decimal(80)

    assert grant.action == ActionType.STOCK_ACTIVITY
    assert grant.amount is None


def test_parse_trade_report_with_minimal_header(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Parse a trade without unused or conditionally required columns."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            ["Market", "Code", "Type", "Date", "Quantity", "Price *", "Currency"],
            ["LSE", "ABC", "Buy", "01/02/2023", "2", "10", "GBP"],
        ],
    )

    [transaction] = SharesightParser().load_from_dir(tmp_path)

    assert transaction.symbol == "LSE:ABC"
    assert transaction.description == ""
    assert transaction.fees == Decimal(0)
    assert transaction.amount == Decimal(-20)
    assert "Stock Activity markers cannot be detected" in caplog.text


def test_parse_trade_report_with_renamed_columns(tmp_path: Path) -> None:
    """Parse the report generation that renamed three legacy headings."""
    renamed_header = [
        "Price" if column == "Price *" else column for column in TRADE_HEADER
    ]
    renamed_header[renamed_header.index("Brokerage *")] = "Brokerage"
    renamed_header[renamed_header.index("Currency")] = "Brokerage Currency"
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            renamed_header,
            [
                "LSE",
                "ABC",
                "Example",
                "Buy",
                "01/02/2023",
                "2",
                "10",
                "1",
                "GBP",
                "1",
                "20",
                "",
            ],
        ],
    )

    [transaction] = SharesightParser().load_from_dir(tmp_path)

    assert transaction.symbol == "LSE:ABC"
    assert transaction.currency == "GBP"
    assert transaction.quantity == Decimal(2)
    assert transaction.price == Decimal(10)
    assert transaction.fees == Decimal(1)
    assert transaction.amount == Decimal(-21)


def test_parse_trade_report_with_newer_aliases(tmp_path: Path) -> None:
    """Parse the newer aliases and ignore an unrelated report column."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            [*ALIASED_TRADE_HEADER, "Unused report column"],
            [
                "ABC",
                "NASDAQ",
                "Example",
                "01/02/2023",
                "Sell",
                "-2",
                "10",
                "USD",
                "1",
                "USD",
                "1.2",
                "16.67",
                "",
                "not used",
            ],
        ],
    )

    [transaction] = SharesightParser().load_from_dir(tmp_path)

    assert transaction.symbol == "NASDAQ:ABC"
    assert transaction.currency == "USD"
    assert transaction.quantity == Decimal(2)
    assert transaction.price == Decimal(10)
    assert transaction.fees == Decimal(1)
    assert transaction.amount == Decimal(19)


def test_parse_trade_report_with_foreign_brokerage(tmp_path: Path) -> None:
    """Reject brokerage whose currency differs from the instrument currency."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            ALIASED_TRADE_HEADER,
            [
                "ABC",
                "NASDAQ",
                "Example",
                "01/02/2023",
                "Buy",
                "2",
                "10",
                "USD",
                "1",
                "GBP",
                "1.2",
                "16.67",
                "",
            ],
        ],
    )

    with pytest.raises(
        ParsingError,
        match="Brokerage Currency 'GBP' differs from transaction currency 'USD'",
    ) as excinfo:
        SharesightParser().load_from_dir(tmp_path)

    assert excinfo.value.row_index == 2


def test_parse_fx_trade_with_gbp_brokerage(tmp_path: Path) -> None:
    """Accept an FX row whose brokerage is in GBP, the currency FX rows use."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            ALIASED_TRADE_HEADER,
            [
                "USDGBP",
                "FX",
                "FX trade",
                "03/02/2023",
                "Sell",
                "-100",
                "1.25",
                "USD",
                "1",
                "GBP",
                "1.25",
                "80",
                "fx",
            ],
        ],
    )

    [transaction] = SharesightParser().load_from_dir(tmp_path)

    assert transaction.symbol == "FX:USDGBP"
    assert transaction.currency == "GBP"
    assert transaction.quantity == Decimal(100)
    assert transaction.price == Decimal("0.8")
    assert transaction.fees == Decimal(1)
    assert transaction.amount == Decimal(79)


def test_parse_trade_report_unknown_action(tmp_path: Path) -> None:
    """Raise on unknown trade types with row context."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            TRADE_HEADER,
            [
                "NASDAQ",
                "FOO",
                "Foo Inc",
                "Split",
                "01/02/2023",
                "10",
                "5.5",
                "1",
                "USD",
                "1",
                "44",
                "",
            ],
        ],
    )

    with pytest.raises(ParsingError, match="Unknown action: Split") as excinfo:
        SharesightParser().load_from_dir(tmp_path)

    assert excinfo.value.row_index == 2


def test_parse_trade_report_fx_without_value(tmp_path: Path) -> None:
    """Raise on FX trades without a GBP value."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            TRADE_HEADER,
            [
                "FX",
                "USDGBP",
                "FX trade",
                "Sell",
                "03/02/2023",
                "-100",
                "1.25",
                "0",
                "USD",
                "1.25",
                "",
                "fx",
            ],
        ],
    )

    with pytest.raises(ParsingError, match="Missing Value in FX transaction"):
        SharesightParser().load_from_dir(tmp_path)


def test_parse_trade_report_fx_without_value_column(tmp_path: Path) -> None:
    """Require the conditionally used Value column for an FX trade."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            ["Market", "Code", "Type", "Date", "Quantity", "Price *", "Currency"],
            ["FX", "USDGBP", "Sell", "03/02/2023", "-100", "1.25", "USD"],
        ],
    )

    with pytest.raises(ParsingError, match="Missing Value in FX transaction"):
        SharesightParser().load_from_dir(tmp_path)


def test_parse_trade_report_invalid_stock_activity(tmp_path: Path) -> None:
    """Raise when stock activity is not a buy."""
    _write_csv(
        tmp_path / "All Trades Report.csv",
        [
            TRADE_HEADER,
            [
                "NASDAQ",
                "NVDA",
                "Nvidia",
                "Sell",
                "04/02/2023",
                "-2",
                "100",
                "0",
                "USD",
                "1",
                "",
                "Stock Activity vest",
            ],
        ],
    )

    with pytest.raises(InvalidTransactionError, match="must have Type=Buy"):
        SharesightParser().load_from_dir(tmp_path)
