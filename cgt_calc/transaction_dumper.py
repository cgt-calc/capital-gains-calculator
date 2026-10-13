"""Export parsed broker transactions as CSV for inspection."""

from __future__ import annotations

import csv
from dataclasses import fields, is_dataclass
import datetime as dt
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .model import BrokerTransaction

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

__all__ = ["DUMP_FIELDS", "dump_transactions"]

# The columns come from BrokerTransaction itself, so a field added to the
# model is exported rather than quietly missing from a file users are told
# holds every parsed value. Two deliberate departures from its declared order:
#  * `calculation_quantity` is left out, because the calculator sets it while
#    planning share reorganisations, long after this snapshot is taken;
#  * `source` is moved last, because its file and row provenance is what
#    differs when two exports describe the same transactions.
# A test pins the resulting header, so a new field still has to be looked at,
# and _jsonable refuses a type it does not know rather than guessing.
_EXCLUDED_FIELDS: Final = frozenset({"calculation_quantity"})
_TRAILING_FIELDS: Final = ("source",)

DUMP_FIELDS: Final = (
    tuple(
        field.name
        for field in fields(BrokerTransaction)
        if field.name not in _EXCLUDED_FIELDS and field.name not in _TRAILING_FIELDS
    )
    + _TRAILING_FIELDS
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
    if isinstance(value, dt.date):
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
        return {_json_key(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    raise TypeError(f"Cannot export {type(value).__name__} to CSV")


def _json_key(key: object) -> str:
    """Return a mapping key, which JSON can only hold as text.

    Only a key that is already text is accepted. str() on anything else would
    turn it into a Python repr, and for an arbitrary object that includes its
    memory address, in a file whose whole point is that two exports of one
    history compare cleanly.

    Converting other types instead would not be safe either: the conversion is
    not one-to-one, so OptionType.CALL and the string "CALL" would both become
    "CALL" and one of the two entries would quietly vanish. Distinct text keys
    cannot collide, because a mapping already holds only one of any two equal
    strings. A mapping keyed by something else needs a decision about how to
    name its entries, so it is refused until someone makes it.
    """
    if not isinstance(key, str):
        raise TypeError(f"Cannot export {type(key).__name__} as a CSV field name")
    return key


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
