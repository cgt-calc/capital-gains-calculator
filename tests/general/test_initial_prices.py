"""Tests for initial stock prices."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import InitialPriceMissingError, ParsingError
from cgt_calc.initial_prices import InitialPrices

if TYPE_CHECKING:
    from pathlib import Path


def test_load_custom_file(tmp_path: Path) -> None:
    """Load prices from a custom file."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,10.5\n')

    prices = InitialPrices(initial_prices_file=prices_file)

    assert prices.get(datetime.date(2021, 3, 8), "FOO") == Decimal("10.5")


def test_get_missing_price(tmp_path: Path) -> None:
    """Raise when no price is stored for the date and symbol."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text("date,symbol,price\n")

    prices = InitialPrices(initial_prices_file=prices_file)

    with pytest.raises(InitialPriceMissingError):
        prices.get(datetime.date(2021, 3, 8), "FOO")


def test_invalid_row(tmp_path: Path) -> None:
    """Raise with row context on invalid rows."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO\n')

    with pytest.raises(ParsingError) as excinfo:
        InitialPrices(initial_prices_file=prices_file)

    assert excinfo.value.row_index == 2


def test_invalid_date(tmp_path: Path) -> None:
    """Report file and row context for unparsable dates."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text("date,symbol,price\n2021-03-08,FOO,10.5\n")

    with pytest.raises(ParsingError, match="Invalid date format") as excinfo:
        InitialPrices(initial_prices_file=prices_file)

    message = str(excinfo.value)
    assert excinfo.value.row_index == 2
    assert "2021-03-08" in message
    assert "initial_prices.csv" in message


def test_invalid_price(tmp_path: Path) -> None:
    """Report file and row context for unparsable prices."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,ten\n')

    with pytest.raises(ParsingError, match="Invalid decimal price") as excinfo:
        InitialPrices(initial_prices_file=prices_file)

    message = str(excinfo.value)
    assert excinfo.value.row_index == 2
    assert "ten" in message
    assert "initial_prices.csv" in message


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity"])
def test_invalid_numeric_price(tmp_path: Path, price: str) -> None:
    """Negative and non-finite prices are reported as invalid file data."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text(f'date,symbol,price\n"Mar 08, 2021",FOO,{price}\n')

    with pytest.raises(ParsingError, match="finite and non-negative") as excinfo:
        InitialPrices(initial_prices_file=prices_file)

    assert excinfo.value.row_index == 2
