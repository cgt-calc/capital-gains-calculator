"""Share prices on given dates, for vests without a price and for spin-offs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Final, override

from .const import SHARE_PRICES_RESOURCE
from .dates import is_date
from .exceptions import (
    BundledPriceCurrencyError,
    ParsingError,
    SharePriceMissingError,
    UnexpectedColumnCountError,
    reading_as,
)
from .resources import RESOURCES_PACKAGE
from .ticker_renames import current_ticker

SHARE_PRICES_COLUMNS_NUM: Final = 3


@dataclass
class SharePricesEntry:
    """Entry from a share prices file."""

    date: datetime.date
    symbol: str
    price: Decimal

    def __init__(self, row: list[str], file: Path):
        """Create entry from CSV row."""
        if len(row) != SHARE_PRICES_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, SHARE_PRICES_COLUMNS_NUM, file)
        # date,symbol,price
        self.date = self._parse_date(row[0], file)
        self.symbol = row[1]
        try:
            self.price = Decimal(row[2])
        except InvalidOperation as err:
            raise ParsingError(file, f"Invalid decimal price: {row[2]!r}") from err
        if not self.price.is_finite() or self.price < 0:
            raise ParsingError(
                file, f"Share price must be finite and non-negative: {row[2]!r}"
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


class SharePrices:
    """Share prices from a file the user passed, or the bundled ones."""

    def __init__(self, prices_file: Path | None = None) -> None:
        """Load data from an optional prices file or package resources."""
        self.prices_file = prices_file
        self.prices = self._read_prices()

    def get(self, date: datetime.date, symbol: str, currency: str) -> Decimal:
        """Get the price of a share on a date, for a transaction in `currency`.

        A price from a file the user passed is in the transaction's currency.
        The bundled prices are all USD, so one is refused for a transaction in
        any other currency rather than read as that currency.
        """
        assert is_date(date)
        if date not in self.prices or symbol not in self.prices[date]:
            raise SharePriceMissingError(symbol, date)
        if self.prices_file is None and currency != "USD":
            raise BundledPriceCurrencyError(symbol, date, currency)
        return self.prices[date][symbol]

    def closing_prices(self) -> dict[str, dict[datetime.date, Decimal]]:
        """Return the prices of a file the user passed, by symbol then date.

        Empty when no file was passed. These price spin-offs as well as
        vests. The bundled prices do not: they are USD prices for a few
        symbols and dates, and a spin-off priced from them would change runs
        that work today.
        """
        prices: dict[str, dict[datetime.date, Decimal]] = {}
        if self.prices_file is None:
            return prices
        for date, by_symbol in self.prices.items():
            for symbol, price in by_symbol.items():
                prices.setdefault(symbol, {})[date] = price
        return prices

    def _read_prices(self) -> dict[datetime.date, dict[str, Decimal]]:
        """Read share prices from CSV file."""
        prices: dict[datetime.date, dict[str, Decimal]] = {}
        if self.prices_file is None:
            with (
                resources.files(RESOURCES_PACKAGE)
                .joinpath(SHARE_PRICES_RESOURCE)
                .open(encoding="utf-8") as csv_file
            ):
                lines = list(csv.reader(csv_file))
        else:
            with (
                reading_as("utf-8", self.prices_file),
                self.prices_file.open(encoding="utf-8") as csv_file,
            ):
                lines = list(csv.reader(csv_file))
        lines = lines[1:]
        file = self.prices_file or Path("resources") / SHARE_PRICES_RESOURCE
        # The ticker each price was written under, to name an earlier one.
        stated: dict[tuple[datetime.date, str], str] = {}
        for index, row in enumerate(lines, start=2):
            try:
                entry = SharePricesEntry(row, file)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            by_symbol = prices.setdefault(entry.date, {})
            # Transactions are read under their current ticker, so a price
            # filed under the old one has to be too, or it is never found.
            symbol = current_ticker(entry.symbol, entry.date)
            known = by_symbol.get(symbol)
            if known is not None and known != entry.price:
                earlier = {stated[entry.date, symbol], entry.symbol} - {symbol}
                why = (
                    f" {earlier.pop()} is an earlier ticker of {symbol}, so both "
                    "rows price the same security."
                    if earlier
                    else ""
                )
                raise ParsingError(
                    file,
                    f"Two rows price {symbol} on {entry.date}, at {known} and "
                    f"{entry.price}.{why} Keep the right price and remove the "
                    "other row.",
                    row_index=index,
                )
            by_symbol[symbol] = entry.price
            stated[entry.date, symbol] = entry.symbol
        return prices
