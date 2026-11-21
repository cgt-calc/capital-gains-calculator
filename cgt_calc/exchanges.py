"""Exchanges of one asset for another, read as the disposals and acquisition.

Giving up one asset for another is a disposal of the first at the market value
of the consideration and an acquisition of the second at that same value
(TCGA 1992 s17, s38). A fee paid in a third asset is a disposal of that asset
too, and its value is an allowable cost of the exchange. An EXCHANGE row is
replaced by those legs before the history is planned, so the planning and
matching code, which reads one holding per row, never sees one.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import TYPE_CHECKING

from .model import ActionType

if TYPE_CHECKING:
    from .model import BrokerTransaction


def _leg(
    row: BrokerTransaction,
    action: ActionType,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    fees: Decimal,
    amount: Decimal,
    description: str,
) -> BrokerTransaction:
    """Return one leg of `row`: a row of the same class, date, broker and source.

    Built from `row` rather than as a plain BrokerTransaction, so a leg of a
    RAW row is checked as a RAW row, and an error about it names the line of
    the EXCHANGE row it came from.
    """
    leg = copy.copy(row)
    leg.action = action
    leg.symbol = symbol
    leg.quantity = quantity
    leg.price = price
    leg.fees = fees
    leg.amount = amount
    leg.description = description
    leg.isin = None
    leg.market = None
    leg.to_symbol = None
    leg.to_quantity = None
    leg.fee_symbol = None
    leg.fee_quantity = None
    leg.foreign_fees = dict(row.foreign_fees) if action is ActionType.SELL else {}
    leg.capital_adjustments = []
    # An exchange moves no money in or out of the account.
    leg.affects_cash_balance = False
    return leg


def exchange_legs(row: BrokerTransaction) -> list[BrokerTransaction]:
    """Return the disposal, the acquisition and any fee disposal of `row`."""
    assert row.action is ActionType.EXCHANGE
    assert row.symbol is not None
    assert row.quantity is not None
    assert row.price is not None
    assert row.to_symbol is not None
    assert row.to_quantity is not None
    value = row.quantity * row.price
    fees = row.fees
    what = f"Exchange of {row.symbol} for {row.to_symbol}"
    legs = [
        _leg(
            row,
            ActionType.SELL,
            row.symbol,
            row.quantity,
            row.price,
            fees,
            value - fees,
            what,
        ),
        # Received at the value given up. Before the fee disposal below, so a
        # fee taken from what was received is matched with it the same day.
        _leg(
            row,
            ActionType.BUY,
            row.to_symbol,
            row.to_quantity,
            value / row.to_quantity,
            Decimal(0),
            -value,
            what,
        ),
    ]
    if row.fee_symbol is not None:
        assert row.fee_quantity is not None
        legs.append(
            _leg(
                row,
                ActionType.SELL,
                row.fee_symbol,
                row.fee_quantity,
                fees / row.fee_quantity,
                Decimal(0),
                fees,
                f"Fee for {what[0].lower()}{what[1:]}",
            )
        )
    return legs


def expand_exchanges(transactions: list[BrokerTransaction]) -> list[BrokerTransaction]:
    """Replace each EXCHANGE row, where it stands, with its legs."""
    if not any(row.action is ActionType.EXCHANGE for row in transactions):
        return transactions
    expanded: list[BrokerTransaction] = []
    for row in transactions:
        if row.action is ActionType.EXCHANGE:
            expanded += exchange_legs(row)
        else:
            expanded.append(row)
    return expanded
