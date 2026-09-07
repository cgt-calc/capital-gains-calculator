"""l&g ERI transaction parser."""

from __future__ import annotations

from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, override

import dateutil.parser as date_parser
import pdfplumber

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import CurrencyCode, Isin
from cgt_calc.util import round_decimal

if TYPE_CHECKING:
    from pathlib import Path

    from pdfplumber.pdf import PDF

from cgt_calc.parsers.eri.model import ERITransaction

from .model import ERIImporter, ERIImporterOutput

LOGGER = logging.getLogger(__name__)

REPORT_FILE_REGEX = re.compile(r"^legal-and-general-reportable-income.*(\d*)\.pdf$")
ROWNUM_REGEX = re.compile(r"^\d+$")
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

COLUMN_COUNT = 16
ISIN_COLUMN = 4
CURRENCY_COLUMN = 3
ERI_COLUMN = 8
REPORTING_PERIOD_END_COLUMN = 7

LEGAL_AND_GENERAL_ERI_FILENAME = "legal_and_general_eri.csv"


class LegalAndGeneralImporter(ERIImporter):
    """Parser for L&G ETF ERI PDFs.

    Supports the L&G ETF documents released at time of writing,
    but isn't built for their ICAV, SICAV or Liquidity Funds reports.
    """

    def __init__(self) -> None:
        """Create a new L&G Parser instance."""
        super().__init__(name="L&G")

    @staticmethod
    def _parse_row(
        row_number: int, row: list[str | None], file: Path, page_number: int
    ) -> ERITransaction:
        isin_raw = row[ISIN_COLUMN]
        isin = Isin.parse(isin_raw) if isin_raw else None
        if isin is None:
            raise ParsingError(
                file,
                f"Invalid ISIN on page {page_number}, row {row_number}: {isin_raw!r}",
            )

        currency_raw = row[CURRENCY_COLUMN]
        currency = (
            CurrencyCode.parse(currency_raw.replace("JPN", "JPY"))
            if currency_raw
            else None
        )
        if currency is None:
            raise ParsingError(
                file,
                f"Invalid currency on page {page_number}, row {row_number}: "
                f"{row[CURRENCY_COLUMN]!r}",
            )

        try:
            reporting_period_end_raw = row[REPORTING_PERIOD_END_COLUMN]
            reporting_period_end = (
                date_parser.parse(reporting_period_end_raw, dayfirst=True).date()
                if reporting_period_end_raw
                else None
            )
        except date_parser.ParserError as err:
            raise ParsingError(
                file,
                "Could not parse date",
            ) from err
        if reporting_period_end is None:
            raise ParsingError(
                file,
                "Could not parse date (no result)",
            )

        eri_raw = row[ERI_COLUMN]
        if eri_raw is None or not AMOUNT_REGEX.match(eri_raw):
            raise ParsingError(
                file, f"Invalid amount on page {page_number}, row {row_number}: "
            )
        eri = round_decimal(Decimal(eri_raw), 5)

        return ERITransaction(
            isin=isin,
            date=reporting_period_end,
            price=eri,
            currency=currency,
        )

    @staticmethod
    def _parse(pdf: PDF, file: Path) -> list[ERITransaction] | None:
        transactions = []
        for page_number, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if len(tables) != 1:
                raise ParsingError(
                    file,
                    f"Found {len(tables) + 1} tables on page {page_number} (expected 1)",
                )
            table = tables[0]
            for row_number, row in enumerate(table):
                # The first column is a number, but the header rows don't have a number. The following three checks try to guarantee that only ERI rows (not header or unpredicted rows) are included
                is_probably_eri_row = (
                    len(row) == COLUMN_COUNT
                    and isinstance(row[0], str)
                    and ROWNUM_REGEX.match(row[0])
                )
                if not is_probably_eri_row:
                    continue
                transaction = LegalAndGeneralImporter._parse_row(
                    row_number=row_number, row=row, file=file, page_number=page_number
                )
                transactions.append(transaction)
            LOGGER.info("Read %d rows from page %d", len(transactions), page_number)
        if not len(transactions) > 0:
            raise ParsingError(file, "Could not extract any ERI data")
        return transactions

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse an L&G ETF ERI file."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None
        result = ERIImporterOutput(
            transactions=[], output_file_name=LEGAL_AND_GENERAL_ERI_FILENAME
        )

        with pdfplumber.open(file) as pdf:
            if not pdf.pages:
                return None
            parsed_data = LegalAndGeneralImporter._parse(pdf, file)
            if parsed_data is not None:
                result.transactions = parsed_data
        LOGGER.info("Done parsing")
        return result
