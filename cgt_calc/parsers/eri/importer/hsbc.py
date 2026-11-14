"""HSBC ETFs plc ERI importer."""

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

# The name HSBC's download gives the HSBC ETFs plc report, for example
# `reportable income 2025-gb-en-hetf.pdf` from
# https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/ie00b42tw061/gb/en/reportable%20income%202025.
# HSBC Global Funds ICAV's report comes as `...-hgfi.pdf` and is not read.
REPORT_FILE_REGEX = re.compile(
    r"^reportable income \d{4}-[a-z]{2}-[a-z]{2}-hetf\.pdf$", re.IGNORECASE
)

ISIN_COLUMN = "ISIN"
PERIOD_END_COLUMN = "Reporting period end date"
CURRENCY_COLUMN = "Currency"
ERI_COLUMN = "Excess reported income per share"
COLUMNS = (ISIN_COLUMN, PERIOD_END_COLUMN, CURRENCY_COLUMN, ERI_COLUMN)

AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")
ISIN_LENGTH = 12

HSBC_ERI_FILENAME = "hsbc_eri.csv"


class _Cell(NamedTuple):
    """A column heading, and how far across the page its words reach."""

    text: str
    left: float
    right: float


def _overlaps(word: Word, cell: _Cell) -> bool:
    return bool(word["x0"] < cell.right and word["x1"] > cell.left)


def _isins_in(word: Word) -> set[Isin]:
    """Return every valid ISIN within the word, which may run on into others."""
    text = str(word["text"])
    return {
        isin
        for start in range(len(text) - ISIN_LENGTH + 1)
        if (isin := Isin.parse(text[start : start + ISIN_LENGTH]))
    }


class HsbcImporter(ERIImporter):
    """Parser for the HSBC ETFs plc UK reporting fund status reports.

    Each report has a table of excess reported income per share, then tables of
    the distributions paid. Only the first is read.
    """

    def __init__(self) -> None:
        """Create a new HSBC parser instance."""
        super().__init__(name="HSBC")

    @staticmethod
    def _lines(page: Page) -> list[list[Word]]:
        """Return the page's words, as lines from top to bottom.

        The tables have no ruled lines, so cells are read from the words'
        positions. Words are formed in the order the PDF draws them, not left
        to right: a fund name too long for its column runs into the ISIN, and
        only the drawing order keeps their letters apart.
        """
        lines: dict[float, list[Word]] = {}
        for word in page.extract_words(use_text_flow=True):
            lines.setdefault(round(word["top"]), []).append(word)
        return [
            sorted(words, key=lambda word: word["x0"])
            for _, words in sorted(lines.items())
        ]

    @staticmethod
    def _header_cells(
        lines: list[list[Word]], heading_line: int, file: Path, page_number: int
    ) -> list[_Cell]:
        """Group the headings above the table into one cell per column.

        A heading spans up to four lines, right of "Sub fund"; the title above
        the table starts at the page's left margin, so the heading ends there.
        Words on different lines that overlap across the page head one column.
        """
        line = lines[heading_line]
        if [word["text"] for word in line[:2]] != ["Sub", "fund"]:
            raise ParsingError(file, f"No 'Sub fund' heading on page {page_number}")
        right_of = line[1]["x1"]
        words = [word for word in line if word["x0"] > right_of]
        for above in reversed(lines[:heading_line]):
            if any(word["x0"] <= right_of for word in above):
                break
            words += above
        groups: list[list[Word]] = []
        for word in sorted(words, key=lambda word: word["x0"]):
            if groups and word["x0"] < max(other["x1"] for other in groups[-1]):
                groups[-1].append(word)
            else:
                groups.append([word])
        return [
            _Cell(
                text=" ".join(
                    word["text"]
                    for word in sorted(
                        group, key=lambda word: (round(word["top"]), word["x0"])
                    )
                ),
                left=min(word["x0"] for word in group),
                right=max(word["x1"] for word in group),
            )
            for group in groups
        ]

    @staticmethod
    def _read_isin(words: list[Word], file: Path, page_number: int) -> Isin:
        """Return the one ISIN among the words printed across the ISIN column.

        The end of a long fund name can share the column with the ISIN, as a
        separate word (`IE00BKY59G90 ETF`) or joined to it (`ETFIE000XFORJ80`).
        """
        found = {isin for word in words for isin in _isins_in(word)}
        if len(found) != 1:
            text = " ".join(word["text"] for word in words)
            raise ParsingError(
                file, f"Expected one ISIN on page {page_number}, found {text!r}"
            )
        return found.pop()

    @staticmethod
    def _read_page(page: Page, page_number: int, file: Path) -> list[ERITransaction]:
        lines = HsbcImporter._lines(page)
        heading_line = next(
            (
                index
                for index, line in enumerate(lines)
                if any(word["text"] == ISIN_COLUMN for word in line)
            ),
            None,
        )
        # The cover page, notes and disclaimer have no table. A page with ISINs
        # but no heading is a table that would otherwise be dropped unread.
        if heading_line is None:
            if any(_isins_in(word) for line in lines for word in line):
                raise ParsingError(
                    file, f"Page {page_number} has ISINs but no {ISIN_COLUMN!r} heading"
                )
            return []
        cells = HsbcImporter._header_cells(lines, heading_line, file, page_number)
        colmap: dict[str, _Cell] = {}
        for column in COLUMNS:
            matches = [cell for cell in cells if cell.text.lower() == column.lower()]
            if len(matches) == 1:
                colmap[column] = matches[0]
            elif matches:
                raise ParsingError(
                    file, f"Several columns headed {column!r} on page {page_number}"
                )
        if ERI_COLUMN not in colmap:
            if any(cell.text.lower().startswith("distribution") for cell in cells):
                return []
            raise ParsingError(
                file,
                f"No column headed {ERI_COLUMN!r} on page {page_number}, and it is "
                "not a table of distributions",
            )
        missing = [column for column in COLUMNS if column not in colmap]
        if missing:
            raise ParsingError(
                file,
                f"No column headed {', '.join(map(repr, missing))} on page "
                f"{page_number}",
            )

        transactions = []
        for line in lines[heading_line + 1 :]:
            cells_words = {
                column: [word for word in line if _overlaps(word, cell)]
                for column, cell in colmap.items()
            }
            if not cells_words[ISIN_COLUMN]:
                # A fund name continued on a second line has nothing else.
                if cells_words[ERI_COLUMN]:
                    raise ParsingError(
                        file,
                        f"An amount on page {page_number} has no ISIN: "
                        f"{' '.join(word['text'] for word in line)!r}",
                    )
                continue
            transactions.append(HsbcImporter._read_row(cells_words, page_number, file))
        LOGGER.info("Read %d rows from page %d", len(transactions), page_number)
        return transactions

    @staticmethod
    def _read_row(
        cells_words: dict[str, list[Word]], page_number: int, file: Path
    ) -> ERITransaction:
        isin = HsbcImporter._read_isin(cells_words[ISIN_COLUMN], file, page_number)

        def cell(column: str) -> str:
            words = cells_words[column]
            if len(words) != 1:
                raise ParsingError(
                    file,
                    f"Expected one value under {column!r} for {isin} on page "
                    f"{page_number}, found "
                    f"{' '.join(word['text'] for word in words)!r}",
                )
            return str(words[0]["text"])

        period_end_raw = cell(PERIOD_END_COLUMN)
        try:
            period_end = dt.datetime.strptime(period_end_raw, "%d/%m/%Y").date()
        except ValueError:
            raise ParsingError(
                file,
                f"Invalid reporting period end for {isin} on page {page_number}: "
                f"{period_end_raw!r}",
            ) from None

        currency_raw = cell(CURRENCY_COLUMN)
        currency = CurrencyCode.parse(currency_raw)
        if currency is None:
            raise ParsingError(
                file,
                f"Invalid currency for {isin} on page {page_number}: {currency_raw!r}",
            )

        eri_raw = cell(ERI_COLUMN)
        if not AMOUNT_REGEX.match(eri_raw):
            raise ParsingError(
                file,
                f"Invalid amount for {isin} on page {page_number}: {eri_raw!r}",
            )

        return ERITransaction(
            date=period_end,
            isin=isin,
            price=round_decimal(Decimal(eri_raw), 5),
            currency=currency,
        )

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse an HSBC ETFs plc ERI report."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None
        with pdfplumber.open(file) as pdf:
            transactions = [
                transaction
                for page_number, page in enumerate(pdf.pages, 1)
                for transaction in HsbcImporter._read_page(page, page_number, file)
            ]
        if not transactions:
            raise ParsingError(file, "Could not extract any ERI data")
        LOGGER.info("Done parsing HSBC file")
        return ERIImporterOutput(
            transactions=transactions, output_file_name=HSBC_ERI_FILENAME
        )
