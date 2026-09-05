"""Export parsed broker transactions as CSV for inspection."""

from __future__ import annotations

import csv
from dataclasses import fields, is_dataclass
import datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from .model import BrokerTransaction

__all__ = ["DUMP_FIELDS", "dump_transactions"]

# Every BrokerTransaction field as it stands once parsing is done, in the
# order the dataclass declares them, except:
#  * `source` is last, because its file and row provenance is what differs
#    when two exports describe the same transactions;
#  * `calculation_quantity` is left out, because the calculator sets it while
#    planning share reorganisations, long after this snapshot is taken.
DUMP_FIELDS: Final = (
    "date",
    "action",
    "symbol",
    "description",
    "quantity",
    "price",
    "fees",
    "amount",
    "currency",
    "broker",
    "isin",
    "foreign_fees",
    "ambiguous_quantity",
    "option_contract",
    "written_option_tax",
    "capital_adjustments",
    "affects_cash_balance",
    "source",
)


def _jsonable(value: object) -> object:
    """Convert one value to JSON types, deliberately without a str() catch-all.

    An unknown type raises instead of being rendered through Python's repr,
    so a new model field forces a decision about how to export it.
    """
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str | Decimal | Path):
        return str(value)
    if isinstance(value, datetime.date):
        # datetime.datetime is a date, and isoformat covers both.
        return value.isoformat()
    if isinstance(value, Enum):
        # The name is stable; the value is an implementation detail.
        return value.name
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    raise TypeError(f"Cannot export a {type(value).__name__} to CSV")


def _csv_field(value: object) -> str:
    """Render one transaction field as CSV text.

    Scalars are written plainly and exactly; anything with structure becomes
    compact JSON in the one field. None is written as an empty field, which an
    empty string also produces: this is a diagnostic export, not a backup.

    Everything but text goes through json.dumps, which already writes a
    boolean as true/false and a number bare, as the schema asks.
    """
    if value is None:
        return ""
    rendered = _jsonable(value)
    if isinstance(rendered, str):
        return rendered
    return json.dumps(rendered, sort_keys=True, separators=(",", ":"))


def dump_transactions(
    transactions: Sequence[BrokerTransaction], stream: TextIO
) -> None:
    """Write parsed transactions to `stream` as CSV, header first.

    Rows keep the order the broker registry returned, which is the order the
    calculation reads them in and not necessarily the order they happened.

    The stream belongs to the caller: it is neither closed nor reconfigured
    here, and it has to be opened with newline="" so that the writer's own
    record terminator is what reaches the file.
    """
    writer = csv.writer(stream)
    writer.writerow(DUMP_FIELDS)
    for transaction in transactions:
        writer.writerow(
            [_csv_field(getattr(transaction, name)) for name in DUMP_FIELDS]
        )
