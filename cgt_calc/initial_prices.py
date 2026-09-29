"""Initial stock prices."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Final, override

from .const import INITIAL_PRICES_RESOURCE
from .dates import is_date
from .exceptions import (
    InitialPriceCurrencyError,
    InitialPriceMissingError,
    ParsingError,
    UnexpectedColumnCountError,
)
from .resources import RESOURCES_PACKAGE

INITIAL_PRICES_COLUMNS_NUM: Final = 3


@dataclass
class InitialPricesEntry:
    """Entry from initial stock prices file."""

    date: datetime.date
    symbol: str
    price: Decimal

    def __init__(self, row: list[str], file: Path):
        """Create entry from CSV row."""
        if len(row) != INITIAL_PRICES_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, INITIAL_PRICES_COLUMNS_NUM, file)
        # date,symbol,price
        self.date = self._parse_date(row[0], file)
        self.symbol = row[1]
        try:
            self.price = Decimal(row[2])
        except InvalidOperation as err:
            raise ParsingError(file, f"Invalid decimal price: {row[2]!r}") from err
        if not self.price.is_finite() or self.price < 0:
            raise ParsingError(
                file, f"Initial price must be finite and non-negative: {row[2]!r}"
            )

    @staticmethod
    def _parse_date(date_str: str, file: Path) -> datetime.date:
        """Parse date from string."""
        try:
            return datetime.datetime.strptime(date_str, "%b %d, %Y").date()
        except ValueError as err:
            raise ParsingError(
                file,
                f"Invalid date format: {date_str!r}, expected e.g. 'Mar 08, 2021'",
            ) from err

    @override
    def __str__(self) -> str:
        """Return string representation."""
        return f"date: {self.date}, symbol: {self.symbol}, price: {self.price}"


class InitialPrices:
    """Class to store initial stock prices."""

    def __init__(self, initial_prices_file: Path | None = None) -> None:
        """Load data from an optional initial prices file or package resources."""
        self.initial_prices_file = initial_prices_file
        self.initial_prices = self._read_initial_prices()

    def get(self, date: datetime.date, symbol: str, currency: str) -> Decimal:
        """Get the price of a share on a date, for a transaction in `currency`.

        A price from a file the user passed is in the transaction's currency.
        The bundled prices are all USD, so one is refused for a transaction in
        any other currency rather than read as that currency.
        """
        assert is_date(date)
        if date not in self.initial_prices or symbol not in self.initial_prices[date]:
            raise InitialPriceMissingError(symbol, date)
        if self.initial_prices_file is None and currency != "USD":
            raise InitialPriceCurrencyError(symbol, date, currency)
        return self.initial_prices[date][symbol]

    def closing_prices(self) -> dict[str, dict[datetime.date, Decimal]]:
        """Return the prices of a file the user passed, by symbol then date.

        Empty when no file was passed. These price spin-offs as well as
        vests. The bundled prices do not: they are USD prices for a few
        symbols and dates, and a spin-off priced from them would change runs
        that work today.
        """
        prices: dict[str, dict[datetime.date, Decimal]] = {}
        if self.initial_prices_file is None:
            return prices
        for date, by_symbol in self.initial_prices.items():
            for symbol, price in by_symbol.items():
                prices.setdefault(symbol, {})[date] = price
        return prices

    def _read_initial_prices(self) -> dict[datetime.date, dict[str, Decimal]]:
        """Read initial stock prices from CSV file."""
        initial_prices: dict[datetime.date, dict[str, Decimal]] = {}
        if self.initial_prices_file is None:
            with (
                resources.files(RESOURCES_PACKAGE)
                .joinpath(INITIAL_PRICES_RESOURCE)
                .open(encoding="utf-8") as csv_file
            ):
                lines = list(csv.reader(csv_file))
        else:
            with self.initial_prices_file.open(encoding="utf-8") as csv_file:
                lines = list(csv.reader(csv_file))
        lines = lines[1:]
        for index, row in enumerate(lines, start=2):
            try:
                entry = InitialPricesEntry(
                    row,
                    self.initial_prices_file
                    or Path("resources") / INITIAL_PRICES_RESOURCE,
                )
            except ParsingError as err:
                err.add_row_context(index)
                raise
            date_index = entry.date
            if date_index not in initial_prices:
                initial_prices[date_index] = {}
            initial_prices[date_index][entry.symbol] = entry.price
        return initial_prices
