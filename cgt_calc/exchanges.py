"""Exchanges, spends and fees paid in an asset, read as disposals and acquisitions.

Giving up one asset for another is a disposal of the first at the market value
of the consideration and an acquisition of the second at that same value
(TCGA 1992 s17, s38). A fee relates to the whole exchange, so its value is
split equally between the disposal and the acquisition, a basis HMRC accepts
(CRYPTO22150). A fee paid in a third asset is a disposal of that asset too.
Giving an asset for goods or services, a fee included, is a disposal of it
(CRYPTO22100) at its market value, as nothing else values what was received.
So a fee paid in an asset is a sale of that asset for the fee's value, and
the fee is a cost of the transaction it was paid for (CRYPTO22150), as a fee
paid in cash would be. That includes the fee for moving an asset between your
own wallets, though the move itself is not a disposal. Rows of SPEND give an
asset for anything else, and no cash is received for it.
An EXCHANGE or SPEND row, or a BUY, SELL or TRANSFER whose fee was paid in an
asset, is replaced by those legs before the history is planned, so the
planning and matching code, which reads one holding per row, never sees one.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import TYPE_CHECKING

from .model import ActionType

if TYPE_CHECKING:
    from .model import BrokerTransaction

# The actions read only as the legs they are replaced by.
_EXPANDED = frozenset({ActionType.EXCHANGE, ActionType.SPEND})
# The actions read as themselves, followed by the sale of an asset their fee
# was paid in, when they name one.
_FEE_ROWS = frozenset({ActionType.BUY, ActionType.SELL, ActionType.TRANSFER})


def _has_asset_fee(row: BrokerTransaction) -> bool:
    """Say whether `row` is one of `_FEE_ROWS` whose fee was paid in an asset."""
    return row.action in _FEE_ROWS and row.fee_symbol is not None


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
    the EXCHANGE row it came from. The row's ISIN and market describe its own
    `symbol`, so only a leg of that asset keeps them.
    """
    leg = copy.copy(row)
    leg.action = action
    leg.symbol = symbol
    leg.quantity = quantity
    leg.price = price
    leg.fees = fees
    leg.amount = amount
    leg.description = description
    if symbol != row.symbol:
        leg.isin = None
        leg.market = None
    leg.to_symbol = None
    leg.to_quantity = None
    leg.fee_symbol = None
    leg.fee_quantity = None
    leg.foreign_fees = {}
    leg.capital_adjustments = []
    return leg


def exchange_legs(row: BrokerTransaction) -> list[BrokerTransaction]:
    """Return the disposal, the acquisition and any fee disposal of `row`.

    The legs' amounts move the balance by the cash the exchange moved: the
    sale and the purchase together by minus the fee, which a fee paid in an
    asset brings back as the proceeds of its disposal.
    """
    assert row.action is ActionType.EXCHANGE
    assert row.symbol is not None
    assert row.quantity is not None
    assert row.price is not None
    assert row.to_symbol is not None
    assert row.to_quantity is not None
    # The fee is split between the legs by its value in `currency`, which a
    # fee in another currency would not have.
    assert not row.foreign_fees
    value = row.quantity * row.price
    fees = row.fees
    half_fee = fees / 2
    what = f"Exchange of {row.symbol} for {row.to_symbol}"
    legs = [
        _leg(
            row,
            ActionType.SELL,
            row.symbol,
            row.quantity,
            row.price,
            half_fee,
            value - half_fee,
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
            half_fee,
            -value - half_fee,
            what,
        ),
    ]
    if row.fee_symbol is not None:
        legs.append(_fee_sale(row, f"{what[0].lower()}{what[1:]}"))
    return legs


def _fee_sale(
    row: BrokerTransaction, what: str, *, moves_cash: bool = True
) -> BrokerTransaction:
    """Return the disposal of the asset the fee of `row` was paid in, at its value.

    Its amount is the fee's value exactly, the cash a row that took the fee as
    cash gets back. Without `moves_cash`, for a row that moves none, nothing
    is received in cash and the balance is left alone.
    """
    assert row.fee_symbol is not None
    assert row.fee_quantity is not None
    leg = _leg(
        row,
        ActionType.SELL,
        row.fee_symbol,
        row.fee_quantity,
        row.fees / row.fee_quantity,
        Decimal(0),
        row.fees,
        f"Fee for {what}",
    )
    leg.affects_cash_balance = moves_cash
    return leg


def spend_legs(row: BrokerTransaction) -> list[BrokerTransaction]:
    """Return the disposal of what `row` spent, and of any fee asset.

    Neither moves cash: the asset was given for goods or services, not money.
    The fee is a cost of the disposal, so its value comes off the proceeds.
    """
    assert row.action is ActionType.SPEND
    assert row.symbol is not None
    assert row.quantity is not None
    assert row.price is not None
    assert not row.foreign_fees
    # A fee is only ever paid in an asset: nothing on the row moves cash.
    assert row.fee_symbol is not None or not row.fees
    spent = _leg(
        row,
        ActionType.SELL,
        row.symbol,
        row.quantity,
        row.price,
        row.fees,
        row.quantity * row.price - row.fees,
        f"Spent {row.symbol}",
    )
    spent.affects_cash_balance = False
    if row.fee_symbol is None:
        return [spent]
    return [spent, _fee_sale(row, f"spending {row.symbol}", moves_cash=False)]


def fee_legs(row: BrokerTransaction) -> list[BrokerTransaction]:
    """Return a BUY, SELL or TRANSFER `row` and the disposal of its fee asset.

    The row keeps its fee, as a cost of a purchase or off the proceeds of a
    sale, and its amount, which took the fee as cash. The fee sale returns
    that cash, so the balance moves by the trade or transfer alone.
    """
    assert row.action in _FEE_ROWS
    assert not row.foreign_fees
    kept = copy.copy(row)
    kept.fee_symbol = None
    kept.fee_quantity = None
    what = row.action.name.lower()
    if row.symbol:
        what += f" of {row.symbol}"
    return [kept, _fee_sale(row, what)]


def expand_to_legs(transactions: list[BrokerTransaction]) -> list[BrokerTransaction]:
    """Replace each row read as legs, where it stands, with its legs."""
    if not any(row.action in _EXPANDED or _has_asset_fee(row) for row in transactions):
        return transactions
    expanded: list[BrokerTransaction] = []
    for row in transactions:
        if row.action is ActionType.EXCHANGE:
            expanded += exchange_legs(row)
        elif row.action is ActionType.SPEND:
            expanded += spend_legs(row)
        elif _has_asset_fee(row):
            expanded += fee_legs(row)
        else:
            expanded.append(row)
    return expanded
