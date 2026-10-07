"""Handle stock spin-offs."""

from __future__ import annotations

import csv
import logging
import sys
from typing import TYPE_CHECKING, Final

from .exceptions import (
    InteractiveInputRequiredError,
    ParsingError,
    UnexpectedColumnCountError,
    UnexpectedHeaderError,
    reading_as,
)
from .util import open_with_parents

if TYPE_CHECKING:
    import datetime
    from pathlib import Path

    from .model import Position

CHOICES_TO_SHOW: Final = 10
SPIN_OFFS_HEADER: Final = ["dst", "src"]
LOGGER = logging.getLogger(__name__)


class SpinOffHandler:
    """Handles spin-offs."""

    def __init__(
        self,
        spin_offs_file: Path | None = None,
    ):
        """Load data from spin_offs_file."""
        self.spin_offs_file = spin_offs_file
        self.cache = self._read_spin_offs_file()

    def _read_spin_offs_file(self) -> dict[str, str]:
        cache: dict[str, str] = {}
        if self.spin_offs_file is None or not self.spin_offs_file.is_file():
            return cache

        with (
            reading_as("utf-8-sig", self.spin_offs_file),
            self.spin_offs_file.open(encoding="utf-8-sig") as fin,
        ):
            csv_reader = csv.DictReader(fin)
            # The row each new ticker was first read from, to name it in the error.
            first_rows: dict[str, int] = {}
            header = csv_reader.fieldnames
            # Checked before any row: a file of one mapping and no header has
            # no row left to check, and would be read as empty.
            if header is not None and sorted(header) != sorted(SPIN_OFFS_HEADER):
                raise UnexpectedHeaderError(
                    header, SPIN_OFFS_HEADER, self.spin_offs_file
                )
            for line in csv_reader:
                # A value beyond the last column is filed under the key None.
                extra = line.pop(None, None)
                if extra is not None:
                    raise UnexpectedColumnCountError(
                        [*line.values(), *extra],
                        len(SPIN_OFFS_HEADER),
                        self.spin_offs_file,
                        row_index=csv_reader.line_num,
                    )
                # Skip harmless blank rows left by editors or tooling.
                if not any((value or "").strip() for value in line.values()):
                    continue
                # A short row leaves the missing column as None. Spaces typed
                # beside the comma are not part of a ticker.
                dst = (line["dst"] or "").strip()
                src = (line["src"] or "").strip()
                if not dst or not src:
                    raise ParsingError(
                        self.spin_offs_file,
                        "this row needs both tickers: the new one, then the one "
                        "it was spun off from.",
                        row_index=csv_reader.line_num,
                    )
                # Keeping the later row would drop the earlier one's source
                # without a word. The same row twice says nothing new.
                earlier = cache.get(dst)
                if earlier is not None and earlier != src:
                    raise ParsingError(
                        self.spin_offs_file,
                        f"{dst} is also on row {first_rows[dst]}, spun off from "
                        f"{earlier} there. Keep the row that is right.",
                        row_index=csv_reader.line_num,
                    )
                cache[dst] = src
                first_rows.setdefault(dst, csv_reader.line_num)
            return cache

    def _write_spin_off_file(self) -> None:
        if self.spin_offs_file is None:
            return
        with open_with_parents(self.spin_offs_file) as fout:
            data_rows = [[dst, src] for dst, src in self.cache.items()]
            writer = csv.writer(fout)
            writer.writerows([SPIN_OFFS_HEADER, *data_rows])

    def get_spin_off_source(
        self, symbol: str, date: datetime.date, portfolio: dict[str, Position]
    ) -> str:
        """Given a spin-off ticker gets the spin-off source."""
        if symbol in self.cache:
            return self.cache[symbol]

        if not sys.stdin.isatty():
            raise InteractiveInputRequiredError(symbol, date, self.spin_offs_file)

        while True:
            # This would ideally be fetched from some stock DB but yfinance does not
            # provide any info on SpinOffs
            try:
                ticker = input(
                    "For a spin-off, please enter the original ticker from which the "
                    f"new stock (symbol: {symbol}) was spun off on {date}: "
                )
            except EOFError as err:
                raise InteractiveInputRequiredError(
                    symbol, date, self.spin_offs_file
                ) from err
            if ticker in portfolio:
                break
            LOGGER.error(
                "Invalid ticker: %s, couldn't find it in the portfolio!", ticker
            )
            if len(portfolio) > CHOICES_TO_SHOW:
                LOGGER.info(
                    "Available choices (showing %d): %s",
                    CHOICES_TO_SHOW,
                    sorted(portfolio)[:CHOICES_TO_SHOW],
                )
            else:
                LOGGER.info("Available choices: %s", sorted(portfolio))
        self.cache[symbol] = ticker
        self._write_spin_off_file()
        return ticker
