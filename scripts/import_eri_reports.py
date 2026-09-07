"""Module that is able to import ERI data into the tool resources."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cgt_calc import resources
from cgt_calc.const import DEFAULT_ISIN_TRANSLATION_FILE
from cgt_calc.exceptions import (
    CgtError,
    InvalidTransactionError,
    IsinMissingError,
    PriceMissingError,
)
from cgt_calc.parsers.eri.importer.blackrock import BlackrockImporter
from cgt_calc.parsers.eri.importer.invesco import InvescoImporter
from cgt_calc.parsers.eri.importer.vanguard import VanguardImporter
from cgt_calc.parsers.eri.importer.xtrackers import XtrackersImporter
from cgt_calc.parsers.eri.raw import COLUMNS, RAW_DATE_FORMAT, ERIRawParser
from cgt_calc.util import approx_equal, display_str

if TYPE_CHECKING:
    import datetime

    from cgt_calc.parsers.eri.importer.model import ERIImporter
    from cgt_calc.parsers.eri.model import ERITransaction

ERI_IMPORTERS: list[ERIImporter] = [
    BlackrockImporter(),
    InvescoImporter(),
    VanguardImporter(),
    XtrackersImporter(),
]

logging.basicConfig(level=logging.INFO)


def validate_and_remove_duplicates(
    transactions: list[ERITransaction],
) -> list[ERITransaction]:
    """Validate and remove duplicate transactions.

    Sort the final output by date for writing
    """
    transaction_index: dict[
        tuple[str, datetime.date], tuple[ERITransaction, Decimal]
    ] = {}
    result = []
    for transaction in transactions:
        price = transaction.price
        if price is None:
            raise PriceMissingError(transaction)
        if not price.is_finite() or price < 0:
            raise InvalidTransactionError(
                transaction, "ERI price must be finite and non-negative"
            )
        isin = transaction.isin
        if isin is None:
            raise IsinMissingError(transaction)
        key = (isin, transaction.date)
        if key in transaction_index:
            current_transaction, current_price = transaction_index[key]
            if approx_equal(current_price, price, Decimal("0.0001")):
                continue
            # With the currency: two rows for one fund on one day can state
            # their prices in different currencies, and "1.25 against 0.98"
            # is a different conflict from "1.25 USD against 0.98 GBP".
            raise InvalidTransactionError(
                transaction,
                "Duplicate same day ERI with a different price: an earlier "
                f"row on this date states {display_str(current_price)} "
                f"{current_transaction.currency}",
            )
        result.append(transaction)
        transaction_index[key] = (transaction, price)

    result.sort(key=lambda t: t.date)
    return result


def eri_import_from_file(path: Path) -> None:
    """Import the specified path into the tool resources."""

    if not path.is_file():
        raise CgtError(f"Specified path is not a file: {path}")

    print(f"Processing ERI file: {path}")
    for importer in ERI_IMPORTERS:
        data = importer.parse(path)
        if data is None:
            continue

        if not data.transactions:
            print(
                f"WARNING: ERI importer {importer.name} found no transactions in {path}"
            )
            return
        print(
            f"ERI file {path} successfully parsed with {importer.name}, "
            f"transactions found: {len(data.transactions)}"
        )
        output_path = Path(resources.__file__).parent / "eri" / data.output_file_name
        transactions: list[ERITransaction] = []
        if output_path.exists():
            transactions += cast(
                "list[ERITransaction]", ERIRawParser.load_from_file(output_path)
            )
        transactions += data.transactions
        transactions = validate_and_remove_duplicates(transactions)

        with output_path.open("w", encoding="utf8") as fout:
            data_rows = [
                [
                    transaction.isin,
                    transaction.date.strftime(RAW_DATE_FORMAT),
                    transaction.currency,
                    transaction.price,
                ]
                for transaction in transactions
            ]
            writer = csv.writer(fout)
            writer.writerows([COLUMNS, *data_rows])
        return

    print(f"WARNING: No ERI importer found for {path}")


def eri_import_from_path(path_str: str) -> str:
    """Import the specified path (file or directory) into the tool resources.

    Returns the path of the ISIN translation file to use for writing into resources
    """
    print(f"ERI data import mode activated from {path_str}")
    path = Path(path_str)
    if path.is_dir():
        for subpath in path.rglob("*"):
            if subpath.is_file() and not subpath.name.startswith("."):
                eri_import_from_file(subpath)
    else:
        eri_import_from_file(path)
    return str(
        (Path(resources.__file__).parent / DEFAULT_ISIN_TRANSLATION_FILE).resolve()
    )


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Import ERI Reports in CGT tool")
    parser.add_argument(
        "eri_reports",
        type=str,
        help=(
            "Input file or folder to import ERI reports into "
            "the project resources folder using the existing ERI parsers available."
        ),
    )

    args = parser.parse_args()
    try:
        eri_import_from_path(args.eri_reports)
    except CgtError as err:
        parser.error(str(err))
    print("Import complete! Run cgt_calc to use the imported data in your reports!")


if __name__ == "__main__":
    main()
