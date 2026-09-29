"""Tests for share prices."""

from __future__ import annotations

import datetime
from decimal import Decimal
import re
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.cli import calculate_cgt
from cgt_calc.exceptions import (
    BundledPriceCurrencyError,
    ParsingError,
    SharePriceMissingError,
)
from cgt_calc.share_prices import SharePrices

if TYPE_CHECKING:
    from pathlib import Path


def test_load_custom_file(tmp_path: Path) -> None:
    """Load prices from a custom file."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,10.5\n')

    prices = SharePrices(prices_file=prices_file)

    assert prices.get(datetime.date(2021, 3, 8), "FOO", "USD") == Decimal("10.5")


def test_get_missing_price(tmp_path: Path) -> None:
    """Raise when no price is stored for the date and symbol."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text("date,symbol,price\n")

    prices = SharePrices(prices_file=prices_file)

    with pytest.raises(
        SharePriceMissingError,
        match=re.escape(
            "No share price for FOO on 2021-03-08: add it to a file passed with "
            "--prices-file"
        ),
    ):
        prices.get(datetime.date(2021, 3, 8), "FOO", "USD")


def test_invalid_row(tmp_path: Path) -> None:
    """Raise with row context on invalid rows."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO\n')

    with pytest.raises(ParsingError) as excinfo:
        SharePrices(prices_file=prices_file)

    assert excinfo.value.row_index == 2


def test_invalid_date(tmp_path: Path) -> None:
    """Report file and row context for unparsable dates."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text("date,symbol,price\n2021-03-08,FOO,10.5\n")

    with pytest.raises(ParsingError, match="Invalid date format") as excinfo:
        SharePrices(prices_file=prices_file)

    message = str(excinfo.value)
    assert excinfo.value.row_index == 2
    assert "2021-03-08" in message
    assert "share_prices.csv" in message


def test_invalid_price(tmp_path: Path) -> None:
    """Report file and row context for unparsable prices."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,ten\n')

    with pytest.raises(ParsingError, match="Invalid decimal price") as excinfo:
        SharePrices(prices_file=prices_file)

    message = str(excinfo.value)
    assert excinfo.value.row_index == 2
    assert "ten" in message
    assert "share_prices.csv" in message


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity"])
def test_invalid_numeric_price(tmp_path: Path, price: str) -> None:
    """Negative and non-finite prices are reported as invalid file data."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(f'date,symbol,price\n"Mar 08, 2021",FOO,{price}\n')

    with pytest.raises(ParsingError, match="finite and non-negative") as excinfo:
        SharePrices(prices_file=prices_file)

    assert excinfo.value.row_index == 2


@pytest.mark.parametrize(
    ("currency", "given", "expected"),
    [
        pytest.param("USD", None, "  GOOG: 1.00, £1,061.68\n", id="bundled-usd"),
        pytest.param("GBP", "1000", "  GOOG: 1.00, £1,000.00\n", id="file-gbp"),
        pytest.param("GBP", None, None, id="bundled-gbp-refused"),
    ],
)
def test_a_vest_without_a_price_is_priced_in_its_own_currency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    currency: str,
    given: str | None,
    expected: str | None,
) -> None:
    """A price from your file is in the vest's currency; a bundled one is USD.

    The bundled GOOG price on 2021-03-25 is $1,491.764, £1,061.68 at that
    month's rate. Read for a GBP vest it would be taken as £1,491.76.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        f"2021-03-25,STOCK_ACTIVITY,GOOG,1,,0,{currency}\n"
    )
    args = [
        "--year",
        "2020",
        "--raw-file",
        str(raw),
        "--exchange-rates-file",
        "tests/exchange_rates_data.csv",
        "--isin-translation-file",
        "",
        "--spin-offs-file",
        "",
        "--no-report",
    ]
    if given is not None:
        prices = tmp_path / "prices.csv"
        prices.write_text(f'date,symbol,price\n"Mar 25, 2021",GOOG,{given}\n')
        args += ["--prices-file", str(prices)]

    if expected is None:
        with pytest.raises(
            BundledPriceCurrencyError,
            match=re.escape(
                "is in USD, but the transaction is in GBP. Enter the vest's price in "
                "the row, or give it in the transaction's currency in a file passed "
                "with --prices-file."
            ),
        ):
            calculate_cgt(create_parser().parse_args(args))
        return
    calculate_cgt(create_parser().parse_args(args))
    assert expected in capsys.readouterr().out
