"""Book RAW rows under the broker they name, and let one replace an export row.

A RAW row is booked under its own `Unknown` broker unless it names another
input's broker, and then it joins that broker's cash and dividend tax. A
row that also says which export row it replaces takes that row's place, so
a correction is supplied beside a downloaded export instead of edited into
it. Replacement is limited to dividend tax and vests: their export rows
carry nothing the RAW columns cannot state, and replacing one cannot change
how much money moved.
"""

from __future__ import annotations

from collections import defaultdict
import copy
import dataclasses
import logging
from typing import TYPE_CHECKING, Final

from .exceptions import ParsingError
from .model import ActionType, BrokerTransaction
from .parsers.raw import UNKNOWN_BROKER, RawTransaction
from .parsers.schwab_equity_award_json import (
    SPLITS as SCHWAB_AWARD_SPLITS,
    SchwabAwardTransaction,
    split_multiplier,
)
from .stock_split_planning import source_account
from .util import indent_entry

if TYPE_CHECKING:
    from collections.abc import Hashable
    import datetime as dt

LOGGER = logging.getLogger(__name__)

DOCS: Final = "https://cgt-calc.uk/brokers/raw/#correct-a-row-in-a-broker-export"
# How many of the broker's nearby rows a refusal lists when none matches.
NEARBY_ROWS: Final = 3


def _name_key(name: str) -> str:
    """Compare broker names as people type them: `Trading 212` is `Trading212`."""
    return "".join(name.split()).casefold()


def _refuse(row: RawTransaction, message: str) -> ParsingError:
    """Point a refusal at the RAW row it is about."""
    assert row.source is not None
    assert row.source.file is not None
    return ParsingError(row.source.file, message, row_index=row.source.row)


def _identity(row: BrokerTransaction, date: dt.date) -> tuple[Hashable, ...]:
    """Return what a replacing row and the export row it replaces share.

    A vest keeps its count and may restate its value; a tax row has no
    count worth the name, so its amount identifies it and stays the same.
    """
    measure: tuple[Hashable, ...] = (
        (row.quantity,)
        if row.action is ActionType.STOCK_ACTIVITY
        else (row.amount, row.currency)
    )
    return (row.broker, row.action, row.symbol, date, *measure)


def _what_it_inherits(row: BrokerTransaction) -> tuple[Hashable, ...]:
    """Return what makes two export rows interchangeable as a target.

    The replacing row takes the target's ISIN and market, which say what the
    security is. A tax row is booked by broker, symbol, date and currency
    alone, so its account is not compared. The cost of a vest is its price
    and fees in its own currency, and its account is the one a later
    single-row split checks.
    """
    security = (row.isin, row.market)
    if row.action is ActionType.STOCK_ACTIVITY:
        return (*security, row.price, row.fees, row.currency, source_account(row))
    return security


def _restated_split(target: BrokerTransaction, row: RawTransaction) -> str | None:
    """Explain why a vest restated for a later split cannot be replaced.

    The Equity Awards export states a vest in units after the later splits
    in its parser's table, while a RAW row is read in the units in force on
    its own date, and the pool's exemption from a known split finds the vest
    by its class. Either date before such a split is refused.
    """
    if not isinstance(target, SchwabAwardTransaction) or target.symbol is None:
        return None
    symbol = target.symbol
    earlier = min(target.date, row.date)
    if split_multiplier(symbol, earlier) == 1:
        return None
    effective = next(
        date for date, _ in SCHWAB_AWARD_SPLITS[symbol].splits if earlier < date
    )
    return (
        f"Cannot replace the {symbol} vest of {row.quantity} dated "
        f"{target.date} in the Schwab Equity Awards export: that export states "
        f"it in units after the {symbol} split of {effective}, while a RAW row "
        "is read in the units in force on its own date. Edit the vest's date "
        "and price in a working copy of the Equity Awards export instead, "
        f"keeping its quantity. See {DOCS}"
    )


def _no_match(row: RawTransaction, exported: list[BrokerTransaction]) -> str:
    assert row.replaces is not None
    action = row.action.name
    measure = (
        f"quantity {row.quantity}"
        if row.action is ActionType.STOCK_ACTIVITY
        else f"amount {row.amount} {row.currency}"
    )
    message = (
        f"Cannot find the {row.broker} row this {action} row replaces: no "
        f"{row.broker} {action} row for {row.symbol} dated {row.replaces} has "
        f"{measure}."
    )
    same_kind = [
        other
        for other in exported
        if other.broker == row.broker
        and other.action is row.action
        and other.symbol == row.symbol
    ]
    if same_kind:
        replaces = row.replaces
        nearest = sorted(same_kind, key=lambda other: abs(other.date - replaces))
        listed = "\n".join(indent_entry(other) for other in nearest[:NEARBY_ROWS])
        message += (
            f" The nearest, with the dates cgt-calc reads, which "
            f"--dump-transactions writes:\n{listed}\n"
        )
    else:
        message += f" {row.broker} has no {action} rows for {row.symbol}. "
    return message + f"See {DOCS}"


def resolve_raw_brokers(
    transactions: list[BrokerTransaction],
) -> list[BrokerTransaction]:
    """Book RAW rows under the brokers they name and apply their replacements.

    Runs once every input is loaded, after the ISIN alias rewrite, so the
    names and symbols compared are the ones the calculation uses. The rows
    passed in are not changed: a row booked or placed differently is a copy.
    """
    named = [
        row
        for row in transactions
        if isinstance(row, RawTransaction) and row.broker != UNKNOWN_BROKER
    ]
    if not named:
        return transactions
    exported = [row for row in transactions if not isinstance(row, RawTransaction)]
    # A RAW row cannot vouch for its own broker: only another input can.
    canonical = {_name_key(row.broker): row.broker for row in exported}
    booked: dict[int, RawTransaction] = {}
    for row in named:
        name = canonical.get(_name_key(row.broker))
        if name is None:
            known = ", ".join(sorted(set(canonical.values())))
            message = (
                f"The broker '{row.broker}' is not the broker of any other "
                "input in this run"
            )
            if known:
                message += (
                    ". Leave the broker blank to keep the row in the RAW file's "
                    f"own ledger, or use one of: {known}."
                )
            else:
                message += (
                    ", and no other input names one. Leave the broker blank to "
                    "keep the row in the RAW file's own ledger."
                )
            raise _refuse(row, f"{message} See {DOCS}")
        copied = copy.copy(row)
        copied.broker = name
        booked[id(row)] = copied

    # Replacing rows with the same identity share the export rows that have
    # it: identical targets are interchangeable, so they are counted.
    groups: defaultdict[tuple[Hashable, ...], list[RawTransaction]] = defaultdict(list)
    for row in booked.values():
        if row.replaces is not None:
            groups[_identity(row, row.replaces)].append(row)
    replaced: set[int] = set()
    for key, rows in groups.items():
        targets = [other for other in exported if _identity(other, other.date) == key]
        if len({_what_it_inherits(target) for target in targets}) > 1:
            listed = "\n".join(indent_entry(target) for target in targets)
            raise _refuse(
                rows[0],
                f"Several {rows[0].broker} rows match this replacement and they "
                "differ, so cgt-calc cannot tell which one it replaces:\n"
                f"{listed}\nEdit the row in a working copy of the export "
                f"instead. See {DOCS}",
            )
        if not targets:
            raise _refuse(rows[0], _no_match(rows[0], exported))
        if len(rows) > len(targets):
            extra = rows[len(targets)]
            raise _refuse(
                extra,
                f"Cannot find a {extra.broker} row left for this "
                f"{extra.action.name} row to replace: rows above it already "
                f"replace every {extra.broker} {extra.action.name} row for "
                f"{extra.symbol} dated {extra.replaces} like it. Remove the "
                f"repeated row. See {DOCS}",
            )
        for row, target in zip(rows, targets, strict=False):
            if (restated := _restated_split(target, row)) is not None:
                raise _refuse(row, restated)
            if (
                target.capital_adjustments
                or not target.affects_cash_balance
                or target.option_contract is not None
                or target.written_option_tax is not None
                or target.foreign_fees
                or target.ambiguous_quantity is not None
            ):
                # No parser writes these on a vest or a tax row.
                raise AssertionError(f"RAW cannot state what {target} carries")
            replaced.add(id(target))
            # Every parser records where its rows came from.
            assert row.source is not None
            assert target.source is not None
            # The shares are in the export's account, so a later split from
            # it still counts them; the ISIN and market name the security.
            row.source = dataclasses.replace(row.source, account=target.source.account)
            row.isin = target.isin
            row.market = target.market
            place = (
                f"row {target.source.row} of {target.source.file}"
                if target.source.row is not None
                else str(target.source.file)
            )
            LOGGER.info(
                "Using RAW row %s (%s %s on %s) in place of the %s row dated %s (%s)",
                row.source.row,
                row.action.name,
                row.symbol,
                row.date,
                row.broker,
                target.date,
                place,
            )
    return [booked.get(id(row), row) for row in transactions if id(row) not in replaced]
