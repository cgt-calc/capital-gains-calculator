"""JPMorgan ETFs (Ireland) ICAV ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import itertools
import logging
import re
from typing import TYPE_CHECKING, override

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

# JPMorgan publishes the yearly "Report to Investors" at am.jpmorgan.com under
# names such as "jpm-etfs-uk-reportable-income-2021-en.pdf" and
# "jpm-etf-ireland-icav-2025-report-to-investors.pdf".
REPORT_FILE_REGEX = re.compile(r"^jpm.*(reportable-income|report-to-investors).*\.pdf$")
ISIN_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")
NIL = "Nil"
NUMERIC_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")
MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)
WRITTEN_DATE = rf"\d{{1,2}} (?:{MONTHS}) \d{{4}}"
# "PERIOD OF ACCOUNT: 01/01/2021 - 31/12/2021" up to 2023, then
# "Accounting Period 1 January 2025 - 31 December 2025".
ACCOUNT_REGEX = re.compile(
    r"(?:PERIOD OF ACCOUNT:|Accounting Period)\s+"
    rf"(\d{{2}}/\d{{2}}/\d{{4}}|{WRITTEN_DATE})\s+-\s+"
    rf"(\d{{2}}/\d{{2}}/\d{{4}}|{WRITTEN_DATE})"
)
# The 2020 report cuts each period off after the end's day, "1 January 2020 -
# 31", so its end is the accounting period's when the day agrees.
PERIOD_REGEX = re.compile(rf"^({WRITTEN_DATE}) - (?:({WRITTEN_DATE})|(\d{{1,2}}))$")
CLASS_CURRENCY_REGEX = re.compile(r"\bClass ([A-Z]{3})\b")
# The row reports say which currency their figures are in; it is the share
# class's, though the 2025 report also prints the fund's calculation currency.
ERI_HEADING = re.compile(r"^EXCESSOFREPORTABLEINCOMEPERUNITINSHARECLASS(CURRENCY|CCY)$")
# Words of the excess heading start a little left of "Excess".
HEADING_LEFT_MARGIN = 14
# A row gives its reporting period's start and end.
PERIOD_DATES = 2
# The 2020 report's columns are about 120 points wide; a page with one column
# has no neighbour to measure from.
LONE_COLUMN_WIDTH = 120
# Row labels of the 2020 report, which lists the share classes as columns.
LABELS = (
    "Currency of calculation",
    "ISIN/SEDOL",
    "Share class",
    "Reporting period",
    "Excess of reportable income per unit",
)
JPMORGAN_ERI_FILENAME = "jpmorgan_eri.csv"


def _plain(text: str) -> str:
    """Return the text in upper case without spaces, for comparing headings."""
    return re.sub(r"\s+", "", text).upper()


def _date(text: str) -> dt.date:
    """Parse "31/12/2021" or "31 December 2021"."""
    fmt = "%d/%m/%Y" if NUMERIC_DATE.fullmatch(text) else "%d %B %Y"
    return dt.datetime.strptime(text, fmt).date()


def _centre(word: Word) -> float:
    return float(word["x0"] + word["x1"]) / 2


def _text(words: list[Word]) -> str:
    return " ".join(word["text"] for word in words)


def _isins(words: list[Word]) -> list[Word]:
    """Return the words shaped like an ISIN, in the printed case."""
    return [word for word in words if ISIN_SHAPE.fullmatch(word["text"])]


def _isin(word: Word, page_number: int, file: Path) -> Isin:
    """Return the word's ISIN, refusing one whose check digit is wrong."""
    isin = Isin.parse(word["text"])
    if isin is None:
        raise ParsingError(file, f"Invalid ISIN {word['text']!r} on page {page_number}")
    return isin


def _amount(words: list[Word], *, nil: bool) -> Decimal | None:
    """Return the one amount the words hold, or None if they hold another."""
    if len(words) != 1:
        return None
    text = words[0]["text"]
    if nil and text == NIL:
        text = "0"
    elif not AMOUNT_REGEX.match(text):
        return None
    return round_decimal(Decimal(text), 5)


class JpmorganImporter(ERIImporter):
    """Import ERI data from JPMorgan ETFs (Ireland) ICAV's Report to Investors."""

    def __init__(self) -> None:
        """Create a new instance."""
        super().__init__("JPMorgan Importer")

    @staticmethod
    def _lines(page: Page) -> list[list[Word]]:
        """Return the page's lines, top to bottom, each sorted left to right.

        Words keep the order they were drawn in, so a fund name that runs into
        the ISIN beside it stays a separate word.
        """
        lines: dict[int, list[Word]] = {}
        for word in page.extract_words(use_text_flow=True):
            lines.setdefault(round(word["top"]), []).append(word)
        return [
            sorted(line, key=lambda word: word["x0"])
            for _, line in sorted(lines.items())
        ]

    @staticmethod
    def _account(pages: list[Page], file: Path) -> tuple[dt.date, dt.date]:
        """Return the report's accounting period, printed above the table."""
        for page in pages:
            match = ACCOUNT_REGEX.search(page.extract_text() or "")
            if match:
                return _date(match.group(1)), _date(match.group(2))
        raise ParsingError(file, "The report gives no accounting period")

    @staticmethod
    def _period_end(
        start: dt.date,
        end: dt.date,
        account: tuple[dt.date, dt.date],
        where: str,
        file: Path,
    ) -> dt.date:
        """Check that a share class's period lies in the accounting period."""
        if not account[0] <= start <= end <= account[1]:
            raise ParsingError(
                file,
                f"The reporting period {start} to {end} {where} is not within the "
                f"accounting period {account[0]} to {account[1]}",
            )
        return end

    @staticmethod
    def _read_rows(
        lines: list[list[Word]],
        page_number: int,
        account: tuple[dt.date, dt.date],
        file: Path,
    ) -> list[ERITransaction]:
        """Read a page with one row per share class (the reports from 2021)."""
        first_row = next(index for index, line in enumerate(lines) if _isins(line))
        header = [word for line in lines[:first_row] for word in line]
        excess = [word for word in header if word["text"] == "Excess"]
        if len(excess) != 1:
            raise ParsingError(
                file,
                f"Expected one column headed 'Excess' on page {page_number}, "
                f"found {len(excess)}",
            )
        left = excess[0]["x0"] - HEADING_LEFT_MARGIN
        # The distribution columns follow the excess; without them the excess
        # column runs to the edge of the page.
        right = min(
            (
                word["x0"]
                for word in header
                if word["text"].startswith("Distribution")
                and word["x0"] > excess[0]["x0"]
            ),
            default=float("inf"),
        )
        heading = sorted(
            (
                word
                for word in header
                if word["top"] >= excess[0]["top"] - 1
                and left <= word["x0"]
                and word["x1"] <= right + 1
            ),
            key=lambda word: (round(word["top"]), word["x0"]),
        )
        if not ERI_HEADING.match(_plain(_text(heading))):
            raise ParsingError(
                file,
                f"The excess heading on page {page_number} does not say its "
                f"figures are in the share class currency: {_text(heading)!r}",
            )

        transactions = []
        for line in lines[first_row:]:
            found = _isins(line)
            if not found:
                continue
            text = _text(line)
            if len(found) != 1:
                raise ParsingError(
                    file, f"Expected one ISIN on page {page_number}, found {text!r}"
                )
            isin = _isin(found[0], page_number, file)
            after = line[line.index(found[0]) + 1 :]
            # The 2025 report names the currency in the share class column,
            # "Class EUR Hedged (acc)"; the earlier ones give it a column.
            if after and after[0]["text"] == "Class":
                after = after[1:]
            currency = CurrencyCode.parse(after[0]["text"]) if after else None
            if currency is None:
                raise ParsingError(
                    file,
                    f"Expected the share class currency after {isin} on page "
                    f"{page_number}, found {text!r}",
                )
            dates = [
                word["text"] for word in line if NUMERIC_DATE.fullmatch(word["text"])
            ]
            if len(dates) != PERIOD_DATES:
                raise ParsingError(
                    file,
                    f"Expected the reporting period's start and end for {isin} on "
                    f"page {page_number}, found {text!r}",
                )
            end = JpmorganImporter._period_end(
                _date(dates[0]),
                _date(dates[1]),
                account,
                f"of {isin} on page {page_number}",
                file,
            )
            amount = _amount(
                [word for word in line if left <= _centre(word) < right], nil=False
            )
            if amount is None:
                raise ParsingError(
                    file,
                    f"Expected one amount under 'Excess' for {isin} on page "
                    f"{page_number}, found {text!r}",
                )
            transactions.append(
                ERITransaction(date=end, isin=isin, price=amount, currency=currency)
            )
        return transactions

    @staticmethod
    def _read_columns(
        lines: list[list[Word]],
        page_number: int,
        account: tuple[dt.date, dt.date],
        file: Path,
    ) -> list[ERITransaction]:
        """Read a page with one column per share class (the 2020 report).

        Each row label starts at the left margin, and a row runs down to the
        next label. A column spans halfway to its neighbours' ISINs.
        """
        margin = next(
            line[0]["x0"] for line in lines if _text(line).startswith("ISIN/SEDOL")
        )
        rows: dict[str, list[Word]] = {}
        label: str | None = None
        for line in lines:
            if abs(line[0]["x0"] - margin) < 1:
                text = _text(line)
                label = next((name for name in LABELS if text.startswith(name)), None)
            if label is not None:
                rows.setdefault(label, []).extend(line)
        for name in LABELS:
            if name not in rows:
                raise ParsingError(
                    file, f"No row labelled {name!r} on page {page_number}"
                )

        isins = _isins(rows["ISIN/SEDOL"])
        centres = [_centre(word) for word in isins]
        pairs = list(itertools.pairwise(centres))
        half = min((b - a for a, b in pairs), default=LONE_COLUMN_WIDTH) / 2
        edges = [
            centres[0] - half,
            *((a + b) / 2 for a, b in pairs),
            centres[-1] + half,
        ]

        def cell(name: str, index: int) -> list[Word]:
            return sorted(
                (
                    word
                    for word in rows[name]
                    if edges[index] <= _centre(word) < edges[index + 1]
                ),
                key=lambda word: (round(word["top"]), word["x0"]),
            )

        transactions = []
        for index, word in enumerate(isins):
            isin = _isin(word, page_number, file)
            share_class = _text(cell("Share class", index))
            match = CLASS_CURRENCY_REGEX.search(share_class)
            currency = CurrencyCode.parse(match.group(1)) if match else None
            calculation = _text(cell("Currency of calculation", index))
            calculated_in = CurrencyCode.parse(calculation)
            if currency is None or calculated_in is None:
                raise ParsingError(
                    file,
                    f"Expected a share class currency and a currency of calculation "
                    f"for {isin} on page {page_number}, found {share_class!r} and "
                    f"{calculation!r}",
                )
            period = _text(cell("Reporting period", index))
            dates = PERIOD_REGEX.match(period)
            if dates is None or (
                dates.group(3) is not None and int(dates.group(3)) != account[1].day
            ):
                raise ParsingError(
                    file,
                    f"Expected the reporting period of {isin} on page {page_number}, "
                    f"ending with the accounting period on {account[1]}, found "
                    f"{period!r}",
                )
            end = JpmorganImporter._period_end(
                _date(dates.group(1)),
                _date(dates.group(2)) if dates.group(2) else account[1],
                account,
                f"of {isin} on page {page_number}",
                file,
            )
            words = cell("Excess of reportable income per unit", index)
            amount = _amount(words, nil=True)
            if amount is None:
                raise ParsingError(
                    file,
                    f"Expected one amount or 'Nil' for {isin} on page {page_number}, "
                    f"found {_text(words)!r}",
                )
            # This layout does not say which currency its figures are in. Where
            # the share class and calculation currencies differ, only a nil
            # figure is the same in both.
            if amount and currency != calculated_in:
                LOGGER.warning(
                    "Skipping %s on page %d: the report does not say whether its "
                    "excess of %s is in the share class currency, %s, or the "
                    "currency of calculation, %s",
                    isin,
                    page_number,
                    _text(words),
                    currency,
                    calculated_in,
                )
                continue
            transactions.append(
                ERITransaction(date=end, isin=isin, price=amount, currency=currency)
            )
        return transactions

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse a JPMorgan ETFs (Ireland) ICAV Report to Investors."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None
        transactions: list[ERITransaction] = []
        with pdfplumber.open(file) as pdf:
            account = JpmorganImporter._account(pdf.pages, file)
            for page_number, page in enumerate(pdf.pages, 1):
                lines = JpmorganImporter._lines(page)
                if not any(_isins(line) for line in lines):
                    continue
                if any(_text(line).startswith("ISIN/SEDOL") for line in lines):
                    rows = JpmorganImporter._read_columns(
                        lines, page_number, account, file
                    )
                else:
                    rows = JpmorganImporter._read_rows(
                        lines, page_number, account, file
                    )
                LOGGER.info("Read %d rows from page %d", len(rows), page_number)
                transactions += rows
        if not transactions:
            raise ParsingError(file, "Could not extract any ERI data")
        LOGGER.info("Done parsing JPMorgan file")
        return ERIImporterOutput(
            transactions=transactions, output_file_name=JPMORGAN_ERI_FILENAME
        )
