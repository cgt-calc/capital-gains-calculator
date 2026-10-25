"""Xtrackers ERI transaction parser."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, override

import dateutil.parser as date_parser
import pdfplumber

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import CurrencyCode, Isin
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.util import is_blank_row, round_decimal

from .model import ERIImporter, ERIImporterOutput

if TYPE_CHECKING:
    from pathlib import Path

    from pdfplumber.page import CroppedPage, Page
    from pdfplumber.pdf import PDF

LOGGER = logging.getLogger(__name__)

# Matches both manually renamed reports and the official download names
# from https://etf.dws.com/en-gb/information/etf-documents/reportings/,
# e.g. Reporting-Funds-Xtrackers-II-2024.pdf.
REPORT_FILE_REGEX = re.compile(
    r"^(Xtrackers.*Report.*|Reporting-Funds-Xtrackers.*)\.pdf$"
)

XTRACKERS_ERI_FILENAME = "xtrackers_eri.csv"

AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

# Column indices configuration for Xtrackers tables
ISIN_COLUMN = 0
CURRENCY_COLUMN = 1
ERI_COLUMN = 2
MIN_VALID_YEAR = 2018

# Headings of the columns read from the participants report (from 2025),
# compared with whitespace folded and case ignored. The ERI heading
# continues "IN RESPECT OF THE REPORTING PERIOD", so it is matched by its
# start; the others must match exactly.
PARTICIPANTS_ISIN = "ISIN CODE"
PARTICIPANTS_PERIOD = "REPORTING PERIOD"
PARTICIPANTS_CURRENCY = "CLASS CURRENCY"
PARTICIPANTS_ERI = "EXCESS OF REPORTED INCOME PER UNIT OVER DISTRIBUTIONS"
PARTICIPANTS_HEADINGS = (
    PARTICIPANTS_ISIN,
    PARTICIPANTS_PERIOD,
    PARTICIPANTS_CURRENCY,
    PARTICIPANTS_ERI,
)
ISIN_LIKE_REGEX = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
REPORTING_PERIOD_REGEX = re.compile(r"^(\d{2}\.\d{2}\.\d{4}) - (\d{2}\.\d{2}\.\d{4})$")


def _is_heading(cell: str, heading: str) -> bool:
    """Return whether a participants report header cell is the given heading."""
    cell = cell.upper()
    return cell == heading or (heading == PARTICIPANTS_ERI and cell.startswith(heading))


def _class_period_end(period: str) -> dt.date | None:
    """Return the end of a "DD.MM.YYYY - DD.MM.YYYY" period, or None if invalid."""
    match = REPORTING_PERIOD_REGEX.match(period)
    if match is None:
        return None
    try:
        start, end = (
            dt.datetime.strptime(value, "%d.%m.%Y").date() for value in match.groups()
        )
    except ValueError:
        return None
    return end if start <= end else None


class XtrackersImporter(ERIImporter):
    """Parser for Xtrackers ERI reports.

    Covers the three Xtrackers umbrellas published by DWS: Xtrackers
    (summary format), and Xtrackers II / Xtrackers (IE) Plc (investor
    report format), up to 2024; and all three in the participants report
    format from 2025.
    """

    def __init__(self) -> None:
        """Create a new Xtrackers Parser instance."""
        super().__init__(name="Xtrackers")

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse the input PDF file and return ERI transactions."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None

        result = ERIImporterOutput(
            transactions=[], output_file_name=XTRACKERS_ERI_FILENAME
        )

        with pdfplumber.open(file) as pdf:
            if not pdf.pages:
                return None
            parsers = [
                XtrackersImporter._parse_summary_format,
                XtrackersImporter._parse_investor_report_format,
                XtrackersImporter._parse_participants_report_format,
            ]
            for parser in parsers:
                parsed_data = parser(pdf, file)
                if parsed_data is not None:
                    result.transactions = parsed_data
                    break
            else:
                return None
        LOGGER.info("Done parsing Xtrackers file")
        return result

    @staticmethod
    def _extract_report_end_date(page: Page, prefix: str) -> dt.date | None:
        matches = [
            line["text"]
            for line in page.extract_text_lines()
            if prefix.lower() in line["text"].lower()
        ]
        if not matches:
            return None
        date_str = matches[0][len(prefix) :].strip()
        try:
            date = date_parser.parse(date_str, fuzzy=True, dayfirst=True)
        except date_parser.ParserError:
            return None
        return date.date()

    @staticmethod
    def _extract_header(
        page: Page, file: Path, page_num: int
    ) -> tuple[tuple[float, float, float, float], list[float], dict[int, int]]:
        header_table = page.find_table()
        if header_table is None:
            raise ParsingError(file, f"Could not find table headers on page {page_num}")
        extracted_header = header_table.extract()
        if not extracted_header or not extracted_header[0]:
            raise ParsingError(file, f"Table header is empty on page {page_num}")
        cur_header = [
            (col or "").replace("\n", " ").strip() for col in extracted_header[0]
        ]
        LOGGER.debug("cur_header=%s", cur_header)
        colmap = {}
        colreg = {
            ISIN_COLUMN: re.compile(r"^ISIN.*$", re.IGNORECASE),
            CURRENCY_COLUMN: re.compile(
                r"^(Share class currency|CURRENCY OF THE FOLLOWING AMOUNTS).*$",
                re.IGNORECASE,
            ),
            ERI_COLUMN: re.compile(
                r"^(Excess reported income per share.*|PER UNIT EXCESS REPORTABLE INCOME OVER DISTRIBUTIONS.*)$",
                re.IGNORECASE,
            ),
        }
        for col, regex in colreg.items():
            match_idx = [i for i, h in enumerate(cur_header) if regex.match(h)]
            if len(match_idx) > 1:
                raise ParsingError(
                    file,
                    f"Multiple columns match {regex.pattern!r} on page {page_num}: "
                    f"{match_idx}",
                )
            if not match_idx:
                raise ParsingError(
                    file,
                    f"No column matching {regex.pattern!r} on page {page_num}",
                )
            colmap[col] = match_idx[0]

        columns = [col.bbox[0] for col in header_table.columns]
        columns.append(header_table.rows[0].bbox[2])
        return header_table.rows[0].bbox, columns, colmap

    @staticmethod
    def _extract_data_rows(
        cropped: CroppedPage,
        columns: list[float],
        file: Path,
        page_num: int,
    ) -> list[list[str | None]]:
        table_settings = {
            "vertical_strategy": "explicit",
            "explicit_vertical_lines": columns,
            "horizontal_strategy": "text",
        }
        data_table = cropped.extract_table(table_settings)
        if data_table is None:
            raise ParsingError(file, f"Could not extract data table on page {page_num}")
        return data_table

    @staticmethod
    def _process_data_table(
        page_num: int,
        data_table: list[list[str | None]],
        reporting_period_end: dt.date,
        colmap: dict[int, int],
        file: Path,
    ) -> list[ERITransaction]:
        transactions = []
        for row_num, raw_row in enumerate(data_table, 1):
            if is_blank_row(raw_row):
                continue
            row = {}
            for col, pos in colmap.items():
                row[col] = (raw_row[pos] or "").strip() if pos < len(raw_row) else ""

            isin_raw = row[ISIN_COLUMN]
            isin = Isin.parse(isin_raw)
            if isin is None:
                raise ParsingError(
                    file,
                    f"Invalid ISIN on page {page_num}, row {row_num}: {isin_raw!r}",
                )
            currency = CurrencyCode.parse(row[CURRENCY_COLUMN].replace("JPN", "JPY"))
            if currency is None:
                raise ParsingError(
                    file,
                    f"Invalid currency on page {page_num}, row {row_num}: "
                    f"{row[CURRENCY_COLUMN]!r}",
                )
            if not re.match(AMOUNT_REGEX, row[ERI_COLUMN]):
                raise ParsingError(
                    file,
                    f"Invalid amount on page {page_num}, row {row_num}: "
                    f"{row[ERI_COLUMN]!r}",
                )
            amount = round_decimal(Decimal(row[ERI_COLUMN]), 5)

            transactions.append(
                ERITransaction(
                    isin=isin,
                    date=reporting_period_end,
                    price=amount,
                    currency=currency,
                )
            )
        LOGGER.info("Read %d rows from page %d", len(transactions), page_num)
        return transactions

    @staticmethod
    def _parse_pages(
        pdf: PDF,
        reporting_period_end: dt.date,
        file: Path,
    ) -> list[ERITransaction]:
        transactions = []
        for page_num, page in enumerate(pdf.pages, 1):
            header_bbox, columns, colmap = XtrackersImporter._extract_header(
                page, file, page_num
            )
            (h_left, _h_top, h_right, h_bottom) = header_bbox
            cropped = page.crop((h_left, h_bottom, h_right, page.height))
            data_table = XtrackersImporter._extract_data_rows(
                cropped, columns, file, page_num
            )
            transactions += XtrackersImporter._process_data_table(
                page_num, data_table, reporting_period_end, colmap, file
            )
        return transactions

    @staticmethod
    def _parse_summary_format(pdf: PDF, file: Path) -> list[ERITransaction] | None:
        """Parse the summary format used by the main Xtrackers umbrella."""
        first_page = pdf.pages[0]
        prefix = "Period ended"
        if not first_page.search("Summary of reportable income calculations"):
            return None
        LOGGER.info("Detected Xtrackers summary format")

        reporting_period_end = XtrackersImporter._extract_report_end_date(
            first_page, prefix
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            return []

        return XtrackersImporter._parse_pages(pdf, reporting_period_end, file)

    @staticmethod
    def _parse_investor_report_format(
        pdf: PDF, file: Path
    ) -> list[ERITransaction] | None:
        """Parse the investor report format used by Xtrackers II and (IE) Plc."""
        first_page = pdf.pages[0]
        if not first_page.search("UK reporting fund status report to investors"):
            return None
        if not first_page.search("Xtrackers"):
            return None
        LOGGER.info("Detected Xtrackers investor report format")

        reporting_period_end = XtrackersImporter._extract_report_end_date(
            first_page, "Period of account ended"
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            return []

        return XtrackersImporter._parse_pages(pdf, reporting_period_end, file)

    @staticmethod
    def _parse_participants_report_format(
        pdf: PDF, file: Path
    ) -> list[ERITransaction] | None:
        """Parse the participants report used by all three umbrellas from 2025.

        Each page is one drawn table, so its rows are read as pdfplumber
        finds them. The header is on the first page and repeated on later
        pages only in some reports.
        """
        text = (pdf.pages[0].extract_text() or "").lower()
        if "report to participants" not in text or "xtrackers" not in text:
            return None
        LOGGER.info("Detected Xtrackers participants report format")

        transactions: list[ERITransaction] = []
        colmap: dict[str, int] | None = None
        header_page = header_width = 0
        found_class = False
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.find_tables()
            if len(tables) != 1:
                raise ParsingError(
                    file, f"Expected one table on page {page_num}, found {len(tables)}"
                )
            rows = [
                [" ".join((cell or "").split()) for cell in row]
                for row in tables[0].extract()
            ]
            header_idx = next(
                (
                    idx
                    for idx, row in enumerate(rows)
                    if any(
                        _is_heading(cell, heading)
                        for cell in row
                        for heading in PARTICIPANTS_HEADINGS
                    )
                ),
                None,
            )
            if header_idx is not None:
                if any(
                    ISIN_LIKE_REGEX.match(cell)
                    for row in rows[:header_idx]
                    for cell in row
                ):
                    raise ParsingError(
                        file, f"Share class row before the header on page {page_num}"
                    )
                colmap = XtrackersImporter._map_participants_header(
                    rows[header_idx], file, page_num
                )
                header_page, header_width = page_num, len(rows[header_idx])
                first_row = header_idx + 1
            elif colmap is None:
                raise ParsingError(file, f"No header row on page {page_num}")
            elif rows and len(rows[0]) != header_width:
                raise ParsingError(
                    file,
                    f"Table on page {page_num} has {len(rows[0])} columns; "
                    f"the header on page {header_page} has {header_width}",
                )
            else:
                first_row = 0

            for row_num in range(first_row + 1, len(rows) + 1):
                row = rows[row_num - 1]
                if not row[colmap[PARTICIPANTS_ISIN]]:
                    if any(
                        row[colmap[heading]] for heading in PARTICIPANTS_HEADINGS[1:]
                    ):
                        raise ParsingError(
                            file,
                            f"Value without an ISIN on page {page_num}, row {row_num}",
                        )
                    # Titles, section labels and second-distribution rows.
                    continue
                found_class = True
                transaction = XtrackersImporter._participants_row(
                    row, colmap, file, page_num, row_num
                )
                if transaction is not None:
                    transactions.append(transaction)
        if not found_class:
            raise ParsingError(file, "No share class rows found")
        return transactions

    @staticmethod
    def _map_participants_header(
        header: list[str], file: Path, page_num: int
    ) -> dict[str, int]:
        colmap = {}
        for heading in PARTICIPANTS_HEADINGS:
            matches = [
                idx for idx, cell in enumerate(header) if _is_heading(cell, heading)
            ]
            if not matches:
                raise ParsingError(
                    file, f"No column matching {heading!r} on page {page_num}"
                )
            if len(matches) > 1:
                raise ParsingError(
                    file,
                    f"Multiple columns match {heading!r} on page {page_num}: {matches}",
                )
            colmap[heading] = matches[0]
        return colmap

    @staticmethod
    def _participants_row(
        row: list[str],
        colmap: dict[str, int],
        file: Path,
        page_num: int,
        row_num: int,
    ) -> ERITransaction | None:
        isin_raw = row[colmap[PARTICIPANTS_ISIN]]
        isin = Isin.parse(isin_raw)
        if isin is None:
            raise ParsingError(
                file, f"Invalid ISIN on page {page_num}, row {row_num}: {isin_raw!r}"
            )

        # The date is the share class's own period end: a class that
        # closed during the year has an earlier one than the report.
        period = row[colmap[PARTICIPANTS_PERIOD]]
        end = _class_period_end(period)
        if end is None:
            raise ParsingError(
                file,
                f"Invalid reporting period on page {page_num}, row {row_num}: "
                f"{period!r}",
            )
        if end.year < MIN_VALID_YEAR:
            return None

        currency_raw = row[colmap[PARTICIPANTS_CURRENCY]]
        currency = CurrencyCode.parse(currency_raw.replace("JPN", "JPY"))
        if currency is None:
            raise ParsingError(
                file,
                f"Invalid currency on page {page_num}, row {row_num}: {currency_raw!r}",
            )

        eri = row[colmap[PARTICIPANTS_ERI]]
        if not eri:
            # Missing is not zero: the report gives no figure for this
            # class, so no row is added for it.
            LOGGER.warning(
                "Xtrackers report gives no excess reportable income for %s "
                "on page %d; no row added",
                isin_raw,
                page_num,
            )
            return None
        if not re.match(AMOUNT_REGEX, eri):
            raise ParsingError(
                file, f"Invalid amount on page {page_num}, row {row_num}: {eri!r}"
            )
        return ERITransaction(
            isin=isin,
            date=end,
            price=round_decimal(Decimal(eri), 5),
            currency=currency,
        )
