"""L&G ERI transaction parser."""

from __future__ import annotations

from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, override

import dateutil.parser as date_parser
import pdfplumber

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import CurrencyCode, Isin
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.util import round_decimal

from .model import ERIImporter, ERIImporterOutput

if TYPE_CHECKING:
    from pathlib import Path

    from pdfplumber.pdf import PDF

LOGGER = logging.getLogger(__name__)

# Matches both manually renamed reports and the official download names from
# https://am.landg.com/en-uk/institutional/fundcentre/uk-reporting-funds/,
# e.g. legal-general-ucits-etf-plc-ukrfs-report-to-investors-2025.pdf and the
# differently named 2023 report, lg-etf-30.06.23---report-to-investors.pdf.
# Only the UCITS ETF names are listed: the ICAV, SICAV and Liquidity Funds
# reports published alongside them use a different layout.
REPORT_FILE_REGEX = re.compile(
    r"^(legal-and-general-reportable-income.*"
    r"|legal-general-ucits-etf-plc.*"
    r"|lg-etf-.*report-to-investors.*)\.pdf$",
    re.IGNORECASE,
)

ROWNUM_REGEX = re.compile(r"^\d+$")
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

# The columns to read, by the header each one is published under. L&G splits
# its header over two rows: the reporting period start and end dates sit in a
# second row under a shared "Reporting Period" heading.
ISIN_COLUMN = "ISIN"
CURRENCY_COLUMN = "currency"
REPORTING_PERIOD_END_COLUMN = "reporting period end"
ERI_COLUMN = "excess reportable income"

COLUMN_HEADER_REGEX = {
    ISIN_COLUMN: re.compile(r"^ISIN\b", re.IGNORECASE),
    CURRENCY_COLUMN: re.compile(r"^Currency of calculation$", re.IGNORECASE),
    REPORTING_PERIOD_END_COLUMN: re.compile(r"^Reporting to$", re.IGNORECASE),
    ERI_COLUMN: re.compile(r"^Excess of reportable income per unit$", re.IGNORECASE),
}

LEGAL_AND_GENERAL_ERI_FILENAME = "legal_and_general_eri.csv"


class LegalAndGeneralImporter(ERIImporter):
    """Parser for L&G ETF ERI PDFs.

    Supports the L&G UCITS ETF plc documents released at time of writing,
    but isn't built for their ICAV, SICAV or Liquidity Funds reports.
    """

    def __init__(self) -> None:
        """Create a new L&G Parser instance."""
        super().__init__(name="L&G")

    @staticmethod
    def _extract_colmap(
        table: list[list[str | None]], file: Path, page_number: int
    ) -> dict[str, int]:
        """Map each column to its index, using the header rows of the table.

        The indices are read from the report rather than hardcoded, so a
        layout change fails here instead of silently reading a neighbouring
        column: the excess reportable income column is followed by the
        distribution columns, which hold amounts of the same shape.
        """
        colmap: dict[str, int] = {}
        for row in table:
            # Data rows are numbered in the first column, header rows are not.
            if row and isinstance(row[0], str) and ROWNUM_REGEX.match(row[0]):
                break
            for index, cell in enumerate(row):
                header = " ".join((cell or "").split())
                for column, regex in COLUMN_HEADER_REGEX.items():
                    if regex.match(header):
                        if column in colmap and colmap[column] != index:
                            raise ParsingError(
                                file,
                                f"Multiple columns match {regex.pattern!r} on page "
                                f"{page_number}: {colmap[column]} and {index}",
                            )
                        colmap[column] = index
        missing = sorted(set(COLUMN_HEADER_REGEX) - set(colmap))
        if missing:
            raise ParsingError(
                file,
                f"No column matching {', '.join(missing)} on page {page_number}",
            )
        return colmap

    @staticmethod
    def _parse_row(
        row_number: int,
        row: list[str | None],
        colmap: dict[str, int],
        file: Path,
        page_number: int,
    ) -> ERITransaction:
        def cell(column: str) -> str:
            index = colmap[column]
            return (row[index] or "").strip() if index < len(row) else ""

        isin_raw = cell(ISIN_COLUMN)
        isin = Isin.parse(isin_raw)
        if isin is None:
            raise ParsingError(
                file,
                f"Invalid ISIN on page {page_number}, row {row_number}: {isin_raw!r}",
            )

        # JPN is a known data bug in the reports and is read as JPY, as it is
        # for the Xtrackers reports.
        currency_raw = cell(CURRENCY_COLUMN)
        currency = CurrencyCode.parse(currency_raw.replace("JPN", "JPY"))
        if currency is None:
            raise ParsingError(
                file,
                f"Invalid currency on page {page_number}, row {row_number}: "
                f"{currency_raw!r}",
            )

        reporting_period_end_raw = cell(REPORTING_PERIOD_END_COLUMN)
        try:
            reporting_period_end = date_parser.parse(
                reporting_period_end_raw, dayfirst=True
            ).date()
        except (date_parser.ParserError, OverflowError) as err:
            raise ParsingError(
                file,
                f"Invalid reporting period end on page {page_number}, row "
                f"{row_number}: {reporting_period_end_raw!r}",
            ) from err

        eri_raw = cell(ERI_COLUMN)
        if not AMOUNT_REGEX.match(eri_raw):
            raise ParsingError(
                file,
                f"Invalid amount on page {page_number}, row {row_number}: {eri_raw!r}",
            )
        eri = round_decimal(Decimal(eri_raw), 5)

        return ERITransaction(
            isin=isin,
            date=reporting_period_end,
            price=eri,
            currency=currency,
        )

    @staticmethod
    def _parse(pdf: PDF, file: Path) -> list[ERITransaction]:
        transactions = []
        for page_number, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            if len(tables) != 1:
                raise ParsingError(
                    file,
                    f"Found {len(tables)} tables on page {page_number} (expected 1)",
                )
            table = tables[0]
            colmap = LegalAndGeneralImporter._extract_colmap(table, file, page_number)
            page_transactions = []
            for row_number, row in enumerate(table, 1):
                # Only the data rows are numbered in the first column, so this
                # skips the header rows above them.
                if not (row and isinstance(row[0], str) and ROWNUM_REGEX.match(row[0])):
                    continue
                page_transactions.append(
                    LegalAndGeneralImporter._parse_row(
                        row_number=row_number,
                        row=row,
                        colmap=colmap,
                        file=file,
                        page_number=page_number,
                    )
                )
            LOGGER.info(
                "Read %d rows from page %d", len(page_transactions), page_number
            )
            transactions += page_transactions
        if not transactions:
            raise ParsingError(file, "Could not extract any ERI data")
        return transactions

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse an L&G ETF ERI file."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None

        with pdfplumber.open(file) as pdf:
            if not pdf.pages:
                return None
            transactions = LegalAndGeneralImporter._parse(pdf, file)
        LOGGER.info("Done parsing L&G file")
        return ERIImporterOutput(
            transactions=transactions,
            output_file_name=LEGAL_AND_GENERAL_ERI_FILENAME,
        )
