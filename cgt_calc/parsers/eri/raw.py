"""ERI raw data CSV parser."""

from __future__ import annotations

import csv
import datetime
from importlib import resources
from typing import TYPE_CHECKING, Final, TextIO, override

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction

from cgt_calc.const import ERI_RESOURCE_FOLDER
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import CurrencyCode, Isin, TransactionSource
from cgt_calc.parsers.base_parsers import BaseSingleFileParser
from cgt_calc.resources import RESOURCES_PACKAGE
from cgt_calc.util import parse_decimal

from .model import ERITransaction

COLUMNS: Final[list[str]] = [
    "ISIN",
    "Fund Reporting Period End Date",
    "Currency",
    "Excess of reporting income over distribution",
]

RAW_DATE_FORMAT = "%d/%m/%Y"


class ERIRaw(ERITransaction):
    """Represents a single raw ERI transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: Path) -> None:
        """Create transaction from CSV row."""
        if len(row_raw) != len(COLUMNS):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), file)

        row = dict(zip(header, row_raw, strict=True))

        isin_raw = row["ISIN"]
        isin = Isin.parse(isin_raw)
        if isin is None:
            raise ParsingError(file, f"Invalid ISIN value '{isin_raw}' in ERI data")
        date_str = row["Fund Reporting Period End Date"]
        try:
            date = datetime.datetime.strptime(date_str, RAW_DATE_FORMAT).date()
        except ValueError as err:
            raise ParsingError(file, f"Invalid date '{date_str}' in ERI data") from err
        currency_raw = row["Currency"]
        currency = CurrencyCode.parse(currency_raw)
        if currency is None:
            raise ParsingError(
                file, f"Invalid currency value '{currency_raw}' in ERI data"
            )
        price_str = row["Excess of reporting income over distribution"]
        try:
            price = parse_decimal(price_str, "ERI data")
        except ValueError as err:
            raise ParsingError(
                file, f"Invalid decimal '{price_str}' in ERI data"
            ) from err

        super().__init__(
            date,
            isin,
            price,
            currency,
        )


class ERIRawParser(BaseSingleFileParser[ERIRaw]):
    """Parser for ERI (Excess Reported Income) CSV data files."""

    arg_name = "eri-raw"
    pretty_name = "Historical Excess Reported Income data"
    format_name = "CSV"
    argument_help = "custom Excess Reported Income (ERI) data in CSV format"

    @staticmethod
    def _validate_header(header: list[str], file: Path) -> None:
        """Check that the header is the four columns, in any order."""
        unknown_columns = sorted(set(header) - set(COLUMNS))
        if unknown_columns:
            raise ParsingError(
                file,
                f"Unknown columns: {', '.join(unknown_columns)}",
                row_index=1,
            )
        # Each row is matched to the header cell by cell and then read by
        # column name, so each of the four has to be there exactly once.
        missing_columns = [column for column in COLUMNS if column not in header]
        if missing_columns:
            raise ParsingError(
                file,
                f"Missing columns: {', '.join(missing_columns)}",
                row_index=1,
            )
        repeated_columns = [column for column in COLUMNS if header.count(column) > 1]
        if repeated_columns:
            raise ParsingError(
                file,
                f"Repeated columns: {', '.join(repeated_columns)}",
                row_index=1,
            )

    @classmethod
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load ERI data from arguments and pre-packaged data."""
        transactions: list[BrokerTransaction] = []
        for entry in (
            resources.files(RESOURCES_PACKAGE).joinpath(ERI_RESOURCE_FOLDER).iterdir()
        ):
            if entry.is_file() and entry.name.endswith(".csv"):
                with resources.as_file(entry) as path:
                    transactions += cls.load_from_file(path, show_parsing_msg=False)
        transactions += super().load_from_args(args)
        return transactions

    @classmethod
    @override
    def read_transactions(cls, file: TextIO, file_path: Path) -> list[ERIRaw]:
        """Read ERI raw transactions from file."""

        lines = list(csv.reader(file))

        if not any(cell.strip() for row in lines for cell in row):
            raise ParsingError(file_path, "ERI data file is empty")

        header = lines[0]

        cls._validate_header(header, file_path)

        transactions: list[ERIRaw] = []
        for index, row in enumerate(lines[1:], start=2):
            # Skip harmless blank rows left by editors or tooling.
            if not any(cell.strip() for cell in row):
                continue
            try:
                transaction = ERIRaw(header, row, file_path)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            transaction.source = TransactionSource(row=index)
            transactions.append(transaction)
        return transactions
