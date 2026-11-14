"""ERI raw data CSV parser."""

from __future__ import annotations

import csv
from importlib import resources
import io
from typing import TYPE_CHECKING, Final, TextIO, override

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction

from cgt_calc.const import ERI_RESOURCE_FOLDER
from cgt_calc.csv_columns import CsvColumns
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import CurrencyCode, Isin, TransactionSource
from cgt_calc.parsers.base_parsers import BaseSingleFileParser
from cgt_calc.resources import RESOURCES_PACKAGE
from cgt_calc.util import is_blank_row, parse_date, parse_decimal

from .model import ERITransaction

COLUMNS: Final[list[str]] = [
    "ISIN",
    "Fund Reporting Period End Date",
    "Currency",
    "Excess of reporting income over distribution",
]
ERI_COLUMNS: Final = CsvColumns(tuple(COLUMNS))

RAW_DATE_FORMAT = "%d/%m/%Y"


class ERIRaw(ERITransaction):
    """Represents a single raw ERI transaction."""

    def __init__(
        self, columns: Mapping[str, int], row_raw: list[str], file: Path
    ) -> None:
        """Create transaction from CSV row, read by its header's `columns`."""
        if len(row_raw) != len(COLUMNS):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), file)

        row = {column: row_raw[index] for column, index in columns.items()}

        isin_raw = row["ISIN"]
        isin = Isin.parse(isin_raw)
        if isin is None:
            raise ParsingError(file, f"Invalid ISIN value '{isin_raw}' in ERI data")
        column = "Fund Reporting Period End Date"
        try:
            date = parse_date(
                row[column],
                column,
                RAW_DATE_FORMAT,
                expected="DD/MM/YYYY",
                example="30/06/2024",
            )
        except ValueError as err:
            # The loader adds the row only to a ParsingError.
            raise ParsingError(file, str(err)) from err
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
    argument_help = (
        "custom Excess Reported Income (ERI) data in CSV format; PATH can be '-' "
        "to read the file from standard input (only one '-' per run)"
    )

    @classmethod
    @override
    def recognise(cls, text: str, file_path: Path) -> list[str]:
        """Name the file when its first row passes the reader's header check."""
        first_row = next(csv.reader(io.StringIO(text)), [])
        return ["ERI data"] if ERI_COLUMNS.matches(first_row) else []

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

        if all(is_blank_row(row) for row in lines):
            raise ParsingError(file_path, "ERI data file is empty")

        columns = ERI_COLUMNS.positions(lines[0], file_path)
        transactions: list[ERIRaw] = []
        for index, row in enumerate(lines[1:], start=2):
            # Skip harmless blank rows left by editors or tooling.
            if is_blank_row(row):
                continue
            try:
                transaction = ERIRaw(columns, row, file_path)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            transaction.source = TransactionSource(row=index)
            transactions.append(transaction)
        return transactions
