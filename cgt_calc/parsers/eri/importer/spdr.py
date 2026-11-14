"""SSGA SPDR ETFs Europe I and II plc ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, NamedTuple, override

import pdfplumber

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import CurrencyCode, Isin
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.util import round_decimal

from .model import ERIImporter, ERIImporterOutput

if TYPE_CHECKING:
    from pathlib import Path

    # A word as pdfplumber extracts it: its text and position.
    from pdfplumber._typing import T_obj as Word
    from pdfplumber.page import Page

LOGGER = logging.getLogger(__name__)

# SSGA publishes the reports for both companies under many names, all with
# "spdr" in them, at
# https://ssga.com/uk/en_gb/intermediary/resources/documents/announcements.
# The shareholder notices published beside them hold no figures.
REPORT_FILE_REGEX = re.compile(r"^(?!.*notice).*spdr.*\.pdf$", re.IGNORECASE)

# Headings, compared in upper case without spaces: the reports up to 2019
# print their text without spaces between words.
TABLE_HEADING = "UMBRELLA"
CURRENCY_COLUMN = "CURRENCYOF"
ERI_COLUMN = "PERUNITEXCESS"
# The headings of the table reach no further below the line with
# "UMBRELLA FUND NAME" than this many points.
HEADING_REACH = 25

TITLE_REGEX = re.compile(r"REPORTINGPERIODEND(?:ED)?(\d{1,2}[A-Z]+\d{4})(\(REVISED\))?")
# SSGA reissued its reports for the periods ending 31 March 2020 to 2023 in
# June 2024, restating the excess of accumulating share classes.
REVISED_YEARS = range(2020, 2024)
TERMINATED_REGEX = re.compile(r"(.+)TERMINATED(\d{1,2}[A-Z]+\d{4})\.?")
# Printed in capitals; a sentence run together without spaces must not yield
# an ISIN once upper-cased.
ISIN_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
ISIN_LENGTH = 12
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

SPDR_ERI_FILENAME = "spdr_eri.csv"


class _Cell(NamedTuple):
    """A column heading, and how far across the page its words reach."""

    text: str
    left: float
    right: float


def _plain(text: str) -> str:
    """Return text in upper case without spaces, as headings are compared."""
    return re.sub(r"\s+", "", text).upper()


def _date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%d%B%Y").date()


def _overlaps(word: Word, cell: _Cell) -> bool:
    return bool(word["x0"] < cell.right and word["x1"] > cell.left)


def _isins_in(word: Word) -> set[Isin]:
    """Return every valid ISIN within the word, which may run on into others."""
    text = str(word["text"])
    return {
        isin
        for start in range(len(text) - ISIN_LENGTH + 1)
        if ISIN_SHAPE.fullmatch(text[start : start + ISIN_LENGTH])
        and (isin := Isin.parse(text[start : start + ISIN_LENGTH]))
    }


def _is_data(line: list[Word]) -> bool:
    return any(_isins_in(word) or AMOUNT_REGEX.match(word["text"]) for word in line)


class SpdrImporter(ERIImporter):
    """Parser for the SSGA SPDR ETFs Europe I and II plc UK reporting reports.

    Each report has HMRC's template table, printed with or without spaces
    between words. The period end is in the table's title, not in its rows.
    """

    def __init__(self) -> None:
        """Create a new SPDR parser instance."""
        super().__init__(name="SPDR")

    @staticmethod
    def _lines(page: Page) -> list[list[Word]]:
        """Return the page's words, as lines from top to bottom.

        The tables have no ruled lines, so cells are read from the words'
        positions.
        """
        lines: dict[float, list[Word]] = {}
        for word in page.extract_words(use_text_flow=True):
            lines.setdefault(round(word["top"]), []).append(word)
        return [
            sorted(words, key=lambda word: word["x0"])
            for _, words in sorted(lines.items())
        ]

    @staticmethod
    def _header_cells(lines: list[list[Word]], anchor: int) -> tuple[list[_Cell], int]:
        """Group the table's headings into one cell per column.

        Headings stack over several lines above and below the line with
        "UMBRELLA FUND NAME". Above it, they stand right of that heading; the
        notes over the table start at the page's left margin, so the headings
        end there. Below it they end at the first row. Words on different lines
        that overlap across the page head one column. Return the cells and the
        index of the first line after the headings.
        """
        line = lines[anchor]
        right_of = line[0]["x1"]
        words = list(line)
        for above in reversed(lines[:anchor]):
            if any(word["x0"] <= right_of for word in above):
                break
            words += above
        end = anchor + 1
        while (
            end < len(lines)
            and lines[end][0]["top"] <= line[0]["top"] + HEADING_REACH
            and not _is_data(lines[end])
        ):
            words += lines[end]
            end += 1
        groups: list[list[Word]] = []
        for word in sorted(words, key=lambda word: word["x0"]):
            if groups and word["x0"] < max(other["x1"] for other in groups[-1]):
                groups[-1].append(word)
            else:
                groups.append([word])
        cells = [
            _Cell(
                text=_plain(
                    "".join(
                        word["text"]
                        for word in sorted(
                            group, key=lambda word: (round(word["top"]), word["x0"])
                        )
                    )
                ),
                left=min(word["x0"] for word in group),
                right=max(word["x1"] for word in group),
            )
            for group in groups
        ]
        return cells, end

    @staticmethod
    def _column(
        cells: list[_Cell], heading: str, file: Path, page_number: int
    ) -> _Cell:
        matches = [cell for cell in cells if cell.text.startswith(heading)]
        if len(matches) != 1:
            raise ParsingError(
                file,
                f"Expected one column headed {heading!r} on page {page_number}, "
                f"found {len(matches)}",
            )
        return matches[0]

    @staticmethod
    def _period_end(
        page: Page, previous: dt.date | None, file: Path, page_number: int
    ) -> dt.date:
        """Return the period end in the table's title.

        A table continued on the next page has no title of its own.
        """
        match = TITLE_REGEX.search(_plain(page.extract_text() or ""))
        if match is None:
            if previous is None:
                raise ParsingError(
                    file,
                    f"No reporting period end above the table on page {page_number}",
                )
            return previous
        end = _date(match.group(1))
        if previous is not None and end != previous:
            raise ParsingError(
                file,
                f"The table on page {page_number} is for the period ending {end}, "
                f"the one before it for {previous}",
            )
        if end.year in REVISED_YEARS and match.group(2) is None:
            raise ParsingError(
                file,
                f"This is SSGA's original report for the period ending {end}. SSGA "
                "reissued its reports for the periods ending 31 March 2020 to 2023; "
                "import the revised report instead",
            )
        return end

    @staticmethod
    def _read_page(
        page: Page, page_number: int, file: Path, previous: dt.date | None
    ) -> tuple[list[ERITransaction], dt.date | None]:
        lines = SpdrImporter._lines(page)
        anchor = next(
            (
                index
                for index, line in enumerate(lines)
                if any(_plain(word["text"]).startswith(TABLE_HEADING) for word in line)
            ),
            None,
        )
        # The letter, notes and disclaimer pages have no table.
        if anchor is None:
            return [], previous
        end = SpdrImporter._period_end(page, previous, file, page_number)
        cells, first_row = SpdrImporter._header_cells(lines, anchor)
        currency_cell = SpdrImporter._column(cells, CURRENCY_COLUMN, file, page_number)
        eri_cell = SpdrImporter._column(cells, ERI_COLUMN, file, page_number)

        # A fund closed during the period is footnoted, for example
        # "SPDR Barclays Sterling Aggregate Bond UCITS ETF terminated 8 December
        # 2014.", and its period ended then.
        terminated: dict[str, dt.date] = {}
        for line in lines:
            match = TERMINATED_REGEX.fullmatch(
                _plain("".join(word["text"] for word in line))
            )
            if match:
                terminated[match.group(1)] = _date(match.group(2))
        matched: set[str] = set()

        transactions = []
        for line in lines[first_row:]:
            text = " ".join(word["text"] for word in line)
            found = {isin for word in line for isin in _isins_in(word)}
            amounts = [word for word in line if _overlaps(word, eri_cell)]
            if not found:
                # A fund name continued on another line, a distribution or a note.
                if any(AMOUNT_REGEX.match(word["text"]) for word in amounts):
                    raise ParsingError(
                        file, f"An amount on page {page_number} has no ISIN: {text!r}"
                    )
                continue
            if len(found) != 1:
                raise ParsingError(
                    file, f"Expected one ISIN on page {page_number}, found {text!r}"
                )
            isin = found.pop()
            currencies = [word for word in line if _overlaps(word, currency_cell)]
            currency = (
                CurrencyCode.parse(currencies[0]["text"])
                if len(currencies) == 1
                else None
            )
            if currency is None:
                raise ParsingError(
                    file,
                    f"Expected one currency for {isin} on page {page_number}, found "
                    f"{' '.join(word['text'] for word in currencies)!r}",
                )
            if len(amounts) != 1 or not AMOUNT_REGEX.match(amounts[0]["text"]):
                raise ParsingError(
                    file,
                    f"Expected one amount for {isin} on page {page_number}, found "
                    f"{' '.join(word['text'] for word in amounts)!r}",
                )
            day = end
            plain = _plain(text)
            for name, terminated_on in terminated.items():
                if name in plain:
                    day = terminated_on
                    matched.add(name)
            transactions.append(
                ERITransaction(
                    date=day,
                    isin=isin,
                    price=round_decimal(Decimal(amounts[0]["text"]), 5),
                    currency=currency,
                )
            )
        unmatched = sorted(set(terminated) - matched)
        if unmatched:
            raise ParsingError(
                file,
                f"Page {page_number} says {', '.join(unmatched)} terminated, but no "
                "row of the table names it",
            )
        LOGGER.info("Read %d rows from page %d", len(transactions), page_number)
        return transactions, end

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse an SSGA SPDR ETFs Europe I or II plc ERI report."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None
        transactions: list[ERITransaction] = []
        end: dt.date | None = None
        with pdfplumber.open(file) as pdf:
            if not any(page.chars for page in pdf.pages):
                raise ParsingError(
                    file,
                    "The file has no text to read; it may be a scanned copy of a "
                    "report SSGA also publishes as text",
                )
            for page_number, page in enumerate(pdf.pages, 1):
                rows, end = SpdrImporter._read_page(page, page_number, file, end)
                transactions += rows
        if not transactions:
            raise ParsingError(file, "Could not extract any ERI data")
        LOGGER.info("Done parsing SPDR file")
        return ERIImporterOutput(
            transactions=transactions, output_file_name=SPDR_ERI_FILENAME
        )
