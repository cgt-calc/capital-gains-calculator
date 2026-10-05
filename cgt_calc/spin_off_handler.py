"""Handle stock spin-offs."""

from __future__ import annotations

import csv
import logging
import sys
from typing import TYPE_CHECKING, Final

from .exceptions import InteractiveInputRequiredError, ParsingError
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

        with self.spin_offs_file.open(encoding="utf-8-sig") as fin:
            csv_reader = csv.DictReader(fin)
            for line in csv_reader:
                if sorted(SPIN_OFFS_HEADER) != sorted(line.keys()):
                    raise ParsingError(
                        self.spin_offs_file,
                        f"invalid columns {line.keys()}, "
                        f"they should be {SPIN_OFFS_HEADER}",
                    )
                cache[line["dst"]] = line["src"]
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
