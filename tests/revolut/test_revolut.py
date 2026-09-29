"""Test Revolut support."""

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
import logging
from pathlib import Path
import re
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.revolut import COLUMNS, RevolutColumn, RevolutParser
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

BASE_ROW_VALUES = {
    RevolutColumn.DATE: "2021-11-02T12:34:56.789012Z",
    RevolutColumn.TICKER: "QCOM",
    RevolutColumn.ACTION: "BUY - LIMIT",
    RevolutColumn.QUANTITY: "50",
    RevolutColumn.PRICE_PER_SHARE: "USD 134.50",
    RevolutColumn.TOTAL_AMOUNT: "USD 6725",
    RevolutColumn.CURRENCY: "USD",
    RevolutColumn.FX_RATE: "1.3646",
}


def _default_row(overrides: dict[RevolutColumn, str] | None = None) -> list[str]:
    """Return default row data with optional overrides."""
    values = BASE_ROW_VALUES.copy()
    if overrides:
        values.update(overrides)
    return [values[column] for column in RevolutColumn]


def _write_csv(
    tmp_path: Path,
    rows: list[list[str]] | None = None,
    *,
    header: list[str] | None = COLUMNS,
) -> Path:
    """Write CSV file with the given rows, and a header unless one is refused."""
    target = tmp_path / "revolut.csv"
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if header is not None:
            writer.writerow(header)
        if rows:
            writer.writerows(rows)
    return target


@pytest.mark.parametrize("tax_year", ["2021", "2025"])
def test_run_with_revolut_file(request: pytest.FixtureRequest, tax_year: str) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        tax_year,
        "--revolut-file",
        "tests/revolut/data/transactions.csv",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests") / "revolut" / "data" / f"expected_output_{tax_year}.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_read_revolut_transactions_empty_file(tmp_path: Path) -> None:
    """Ensure parser raises when CSV is empty."""
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("")

    with pytest.raises(ParsingError, match="Revolut CSV file is empty"):
        RevolutParser().load_from_file(empty_file)


def test_read_revolut_transactions_missing_column(tmp_path: Path) -> None:
    """A header that drops a column triggers ParsingError naming it."""
    path = _write_csv(tmp_path, header=COLUMNS[:-1])

    with pytest.raises(ParsingError, match=re.escape("Missing: {'FX Rate'}")):
        RevolutParser().load_from_file(path)


def test_read_revolut_transactions_unexpected_column(tmp_path: Path) -> None:
    """A renamed column triggers ParsingError naming both spellings."""
    header = [*COLUMNS[:-1], "Exchange Rate"]
    path = _write_csv(tmp_path, header=header)

    with pytest.raises(
        ParsingError,
        match=re.escape("Missing: {'FX Rate'}, Extra: {'Exchange Rate'}"),
    ):
        RevolutParser().load_from_file(path)


def test_read_revolut_transactions_reordered_columns(tmp_path: Path) -> None:
    """Columns are read by name, so the export may state them in any order."""
    header = list(reversed(COLUMNS))
    path = _write_csv(tmp_path, [list(reversed(_default_row()))], header=header)

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "QCOM"
    assert transaction.amount == Decimal(-6725)


def test_read_revolut_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Invalid decimal values surface as ParsingError with row context."""
    overrides = {RevolutColumn.QUANTITY: "not-a-number"}
    path = _write_csv(tmp_path, [_default_row(overrides)])

    with pytest.raises(
        ParsingError,
        match=", row 2: Invalid decimal in column 'Quantity'",
    ):
        RevolutParser().load_from_file(path)


def test_read_revolut_transactions_missing_header(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An export without a header row is read, under warning."""
    path = _write_csv(tmp_path, [_default_row()], header=None)
    caplog.set_level(logging.WARNING, logger="cgt_calc.parsers.revolut")

    transactions = RevolutParser().load_from_file(path)

    assert len(transactions) == 1
    assert "missing header row" in caplog.text


@pytest.mark.parametrize(
    ("action", "quantity", "price", "total", "expected_amount", "expected_price"),
    [
        # The whole cash movement is the cost of the shares: the 35 cents by
        # which the total exceeds the quoted price is that price rounded to
        # the cent, not a charge to be taken back off the amount.
        (
            "BUY - LIMIT",
            "88",
            "USD 112.50",
            "USD 9900.35",
            Decimal("-9900.35"),
            Decimal("9900.35") / 88,
        ),
        # And the whole cash movement is what a disposal raised.
        (
            "SELL - LIMIT",
            "38",
            "USD 132.20",
            "USD 5023.49",
            Decimal("5023.49"),
            Decimal("5023.49") / 38,
        ),
    ],
)
def test_read_revolut_trade_takes_the_total_at_face_value(
    tmp_path: Path,
    action: str,
    quantity: str,
    price: str,
    total: str,
    expected_amount: Decimal,
    expected_price: Decimal,
) -> None:
    """Trades carry no fee, and the price is re-derived from the exact figures."""
    overrides = {
        RevolutColumn.ACTION: action,
        RevolutColumn.QUANTITY: quantity,
        RevolutColumn.PRICE_PER_SHARE: price,
        RevolutColumn.TOTAL_AMOUNT: total,
    }
    path = _write_csv(tmp_path, [_default_row(overrides)])

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.fees == Decimal(0)
    assert transaction.amount == expected_amount
    assert transaction.price == expected_price
    assert transaction.currency == "USD"
    assert transaction.broker == "Revolut"


def test_read_revolut_trade_reads_thousands_separators(tmp_path: Path) -> None:
    """Amounts grouped for readability are still amounts."""
    overrides = {
        RevolutColumn.PRICE_PER_SHARE: "USD 1,749.45",
        RevolutColumn.QUANTITY: "2",
        RevolutColumn.TOTAL_AMOUNT: "USD 3,498.90",
    }
    path = _write_csv(tmp_path, [_default_row(overrides)])

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.amount == Decimal("-3498.90")
    assert transaction.price == Decimal("1749.45")


@pytest.mark.parametrize(
    ("action", "total", "expected_action", "expected_amount"),
    [
        ("CASH TOP-UP", "USD 1000", ActionType.TRANSFER, Decimal(1000)),
        ("CASH WITHDRAWAL", "USD -2663.31", ActionType.TRANSFER, Decimal("-2663.31")),
        ("CUSTODY FEE", "USD -0.01", ActionType.ADJUSTMENT, Decimal("-0.01")),
        ("DIVIDEND", "USD 1.30", ActionType.DIVIDEND, Decimal("1.30")),
        (
            "DIVIDEND TAX (CORRECTION)",
            "USD -4.48",
            ActionType.DIVIDEND_TAX,
            Decimal("-4.48"),
        ),
    ],
)
def test_read_revolut_cash_rows(
    tmp_path: Path,
    action: str,
    total: str,
    expected_action: ActionType,
    expected_amount: Decimal,
) -> None:
    """Rows with no quantity move their total, in the direction it is signed."""
    overrides = {
        RevolutColumn.ACTION: action,
        RevolutColumn.QUANTITY: "",
        RevolutColumn.PRICE_PER_SHARE: "",
        RevolutColumn.TOTAL_AMOUNT: total,
    }
    path = _write_csv(tmp_path, [_default_row(overrides)])

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.action is expected_action
    assert transaction.amount == expected_amount
    assert transaction.fees == Decimal(0)


def test_read_revolut_stock_split(tmp_path: Path) -> None:
    """A split states the shares it added and no price for them."""
    overrides = {
        RevolutColumn.TICKER: "NVDA",
        RevolutColumn.ACTION: "STOCK SPLIT",
        RevolutColumn.QUANTITY: "5.72912688",
        RevolutColumn.PRICE_PER_SHARE: "",
        RevolutColumn.TOTAL_AMOUNT: "USD 0",
    }
    path = _write_csv(tmp_path, [_default_row(overrides)])

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.action is ActionType.STOCK_SPLIT
    assert transaction.quantity == Decimal("5.72912688")
    assert transaction.price == Decimal(0)
    assert transaction.amount == Decimal(0)


@pytest.mark.parametrize(
    "action",
    [
        "TRANSFER FROM REVOLUT BANK UAB TO REVOLUT SECURITIES EUROPE UAB",
        "TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB",
    ],
)
def test_read_revolut_skips_internal_transfers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, action: str
) -> None:
    """Moving a holding between Revolut entities is not a transaction."""
    overrides = {
        RevolutColumn.ACTION: action,
        RevolutColumn.PRICE_PER_SHARE: "",
        RevolutColumn.TOTAL_AMOUNT: "USD 0",
    }
    path = _write_csv(tmp_path, [_default_row(), _default_row(overrides)])
    caplog.set_level(logging.DEBUG, logger="cgt_calc.parsers.revolut")

    transactions = RevolutParser().load_from_file(path)

    assert len(transactions) == 1
    assert transactions[0].action is ActionType.BUY
    assert f"Skipping {action}" in caplog.text


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # The tax year boundary always falls inside BST, so the last hour
        # of 5 April in UTC already belongs to the next tax year.
        ("2025-04-05T22:59:59.000000Z", date(2025, 4, 5)),
        ("2025-04-05T23:00:00.000000Z", date(2025, 4, 6)),
        ("2025-04-05T23:30:00.000000", date(2025, 4, 6)),
        # The clocks go back on 25 October 2026, so the last hour of the
        # 26th is already GMT.
        ("2026-10-26T23:30:00.000000Z", date(2026, 10, 26)),
        # Outside summer time UK dates and UTC dates agree.
        ("2026-01-15T23:30:00.000000Z", date(2026, 1, 15)),
        ("2026-11-01T23:30:00.000000Z", date(2026, 11, 1)),
    ],
)
def test_read_revolut_transactions_uses_uk_dates(
    tmp_path: Path, timestamp: str, expected: date
) -> None:
    """Take the tax date from the UK calendar, not the UTC one."""
    path = _write_csv(tmp_path, [_default_row({RevolutColumn.DATE: timestamp})])

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.date == expected


def test_read_revolut_transactions_invalid_timestamp(tmp_path: Path) -> None:
    """A timestamp that is not a date and time surfaces with its row."""
    path = _write_csv(tmp_path, [_default_row({RevolutColumn.DATE: "02/11/2021"})])

    with pytest.raises(ParsingError, match=", row 2: Invalid timestamp: '02/11/2021'"):
        RevolutParser().load_from_file(path)


def test_read_revolut_transactions_unknown_action(tmp_path: Path) -> None:
    """An action the parser has never seen is refused with its row."""
    overrides = {RevolutColumn.ACTION: "SPIN-OFF"}
    path = _write_csv(tmp_path, [_default_row(overrides)])

    with pytest.raises(ParsingError, match=", row 2: Unknown action: SPIN-OFF"):
        RevolutParser().load_from_file(path)


def test_read_revolut_transactions_keep_the_exported_instant(tmp_path: Path) -> None:
    """The export times every row, and the time is what orders a busy day."""
    path = _write_csv(
        tmp_path, [_default_row({RevolutColumn.DATE: "2021-11-02T12:34:56.789012Z"})]
    )

    (transaction,) = RevolutParser().load_from_file(path)

    assert transaction.source is not None
    assert transaction.source.timestamp == datetime(
        2021, 11, 2, 12, 34, 56, 789012, tzinfo=UTC
    )
    # And the line the row came from is still recorded next to it.
    assert transaction.source.row == 2


def test_read_revolut_transactions_order_across_the_autumn_clock_change(
    tmp_path: Path,
) -> None:
    """Instants are kept in UTC, where the clocks going back cannot reorder them.

    Two aware datetimes sharing one tzinfo are compared by their wall clocks,
    ignoring `fold`. Stated in London the earlier of these reads as 01:30 BST
    and the later as 01:15 GMT, so keeping them in UK time would put the
    45 minutes between them the wrong way round.
    """
    path = _write_csv(
        tmp_path,
        [
            _default_row({RevolutColumn.DATE: "2026-10-25T00:30:00.000000Z"}),
            _default_row({RevolutColumn.DATE: "2026-10-25T01:15:00.000000Z"}),
        ],
    )

    earlier, later = RevolutParser().load_from_file(path)

    assert earlier.source is not None
    assert later.source is not None
    assert earlier.source.timestamp is not None
    assert later.source.timestamp is not None
    assert earlier.source.timestamp < later.source.timestamp
    # And both fell on the same UK day, which is what the tax year counts.
    assert earlier.date == later.date == date(2026, 10, 25)


TOP_UP = {
    RevolutColumn.DATE: "2021-01-04T10:00:00.000000Z",
    RevolutColumn.TICKER: "",
    RevolutColumn.ACTION: "CASH TOP-UP",
    RevolutColumn.QUANTITY: "",
    RevolutColumn.PRICE_PER_SHARE: "",
    RevolutColumn.TOTAL_AMOUNT: "USD 20000",
}
OPENING_BUY = {
    RevolutColumn.DATE: "2021-01-04T14:39:38.000000Z",
    RevolutColumn.TICKER: "NVDA",
    RevolutColumn.ACTION: "BUY - MARKET",
    RevolutColumn.QUANTITY: "10",
    RevolutColumn.PRICE_PER_SHARE: "USD 100",
    RevolutColumn.TOTAL_AMOUNT: "USD 1000",
}
# NVIDIA's four-for-one split: ten shares became forty on 20 July 2021.
SPLIT = {
    RevolutColumn.DATE: "2021-07-20T10:34:52.000000Z",
    RevolutColumn.TICKER: "NVDA",
    RevolutColumn.ACTION: "STOCK SPLIT",
    RevolutColumn.QUANTITY: "30",
    RevolutColumn.PRICE_PER_SHARE: "",
    RevolutColumn.TOTAL_AMOUNT: "USD 0",
}


def test_run_with_a_trade_after_a_same_day_split(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """A sale booked after the split is counted in the units it left behind.

    The holding had already been restated when Revolut wrote the row, so the
    eight shares sold are eight of the forty. Only the exported times say so,
    and a run that discarded them would refuse the day.
    """
    path = _write_csv(
        tmp_path,
        [
            _default_row(TOP_UP),
            _default_row(OPENING_BUY),
            _default_row(SPLIT),
            _default_row(
                {
                    RevolutColumn.DATE: "2021-07-20T14:00:00.000000Z",
                    RevolutColumn.TICKER: "NVDA",
                    RevolutColumn.ACTION: "SELL - MARKET",
                    RevolutColumn.QUANTITY: "8",
                    RevolutColumn.PRICE_PER_SHARE: "USD 25",
                    RevolutColumn.TOTAL_AMOUNT: "USD 200",
                }
            ),
        ],
    )
    cmd = build_cmd(
        "--year",
        "2021",
        "--revolut-file",
        str(path),
        "--output",
        report_path(request),
    )

    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    # Ten shares became forty, and eight of those forty were sold.
    assert "NVDA: 32.00" in result.stdout


def test_run_with_a_trade_before_a_same_day_split_is_refused(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """Revolut's split row says when it was booked, not when units changed.

    A US share trades split-adjusted from the opening of the ex-date, which
    need not be when Revolut posted the adjustment, so a purchase stamped
    ahead of that row may have been made in either unit system. Nothing in
    the export settles it, and guessing would rewrite the ratio derived for
    the whole holding, so the day is refused.
    """
    path = _write_csv(
        tmp_path,
        [
            _default_row(TOP_UP),
            _default_row(OPENING_BUY),
            _default_row(
                {
                    RevolutColumn.DATE: "2021-07-20T09:00:00.000000Z",
                    RevolutColumn.TICKER: "NVDA",
                    RevolutColumn.ACTION: "BUY - MARKET",
                    RevolutColumn.QUANTITY: "2",
                    RevolutColumn.PRICE_PER_SHARE: "USD 500",
                    RevolutColumn.TOTAL_AMOUNT: "USD 1000",
                }
            ),
            _default_row(SPLIT),
        ],
    )
    cmd = build_cmd(
        "--year",
        "2021",
        "--revolut-file",
        str(path),
        "--output",
        report_path(request),
    )

    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)

    assert result.returncode != 0
    assert "cannot be placed either side" in result.stderr
    assert "the instant on it is when the broker booked the entry" in result.stderr
    # The export does state its times, so it must not be told to supply them.
    assert "in one input that states times" not in result.stderr
