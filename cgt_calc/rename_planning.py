"""First pass: reading a day's ticker renames as one holding.

A ticker change leaves the same shares under another name. Same-date broker
rows do not consistently establish where the rename falls relative to trades,
so ordinary purchases and sales are checked against the whole holding.

The provisional pool still moves at the RENAME row. Day-open planning computes
capacity across the connected tickers; day-close reconciliation gathers any
remaining positions under the closing ticker. Acquisition and disposal logs
keep the ticker from the source row for the matching pass.

Other holding actions keep their existing processing because they may depend
on the pool's position or cost when processed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from .const import RENAME_DESCRIPTION_PREFIX
from .exceptions import CalculationError, InvalidTransactionError, SymbolMissingError
from .model import ActionType, Position
from .stock_split_planning import signed_quantity
from .stock_splits import quantity_sign
from .transaction_log import has_key

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    import datetime

    from .calculator_state import CalculatorState, PreparedHistory
    from .model import BrokerTransaction


RENAME_DAY_UNSUPPORTED_ACTIONS: Final = frozenset(
    {
        # Restate or apportion the pool itself, so where the pool sits when
        # the row is read decides what they do.
        ActionType.STOCK_SPLIT,
        ActionType.FEE,
        ActionType.EXCESS_REPORTED_INCOME,
        # Take a cost out of the pool, or put one in, at a figure worked out
        # from the holding as the row finds it.
        ActionType.STOCK_ACTIVITY,
        ActionType.REINVEST_SHARES,
        ActionType.TRANSFER_FROM_SPOUSE,
        ActionType.TRANSFER_TO_SPOUSE,
        ActionType.GIFT,
        ActionType.GIFT_UNCONNECTED,
        ActionType.UNCLASSIFIED_GIFT,
        ActionType.CASH_MERGER,
        ActionType.FULL_REDEMPTION,
    }
)
"""Actions that keep their component on the existing row-position path."""


UNNAMED_HOLDING_ACTIONS: Final = frozenset({ActionType.EXCESS_REPORTED_INCOME})
"""Rows that change a pool without naming it, so no component can exclude them.

An excess reported income row names no ticker at all, only an ISIN, which is
resolved to the holdings it adds cost to when that row is read. It cannot be
matched to a rename component by anything on its own row, so a day carrying
one is left to the row-position pool in its entirety.

A spin-off used to be read this way too, because the holding it draws from is
chosen when the row is read rather than named on it. But the row does name the
holding it creates, which is the only one whose count it changes, and a
spin-off leaves the source's count alone. So a component can take a spin-off
into account like any other row that adds units, and a spin-off elsewhere on
the day no longer stops an unrelated rename being read as one holding.
"""


@dataclass(frozen=True)
class RenameComponent:
    """One holding the day's renames spell under more than one ticker.

    `names` is every ticker the day's renames connect, `closing_name` the one
    they all end under, and `capacity` what the whole holding can give up that
    day: what its names opened with, plus what the day's purchases add to any
    of them.
    """

    names: tuple[str, ...]
    closing_name: str
    capacity: Decimal


def end_name(renames: Mapping[str, str], symbol: str) -> str:
    """Return the name a day's renames leave this holding under.

    A rename changes what a holding is called and nothing else, so two rows
    spelling it differently are rows of one holding. The name it ends the day
    under is what they agree on, and is the same answer whichever of the
    day's spellings is asked about, so it is what tells one holding from
    another: comparing the spellings themselves makes one holding look like
    two, and each half is then worked out as if the other were a different
    security.

    Chains and cycles are refused before any of the day's rows is read, so a
    single hop reaches the end.
    """
    return renames.get(symbol, symbol)


def connected_names(renames: Mapping[str, str], symbol: str) -> set[str]:
    """Return every ticker a day's renames say is this holding.

    Walked in both directions: a holding is reached from the name it is
    renamed to as well as from the ones renamed into it.
    """
    reached = {symbol}
    frontier = [symbol]
    while frontier:
        name = frontier.pop()
        for old_name, new_name in renames.items():
            other = (
                new_name if name == old_name else old_name if name == new_name else None
            )
            if other is not None and other not in reached:
                reached.add(other)
                frontier.append(other)
    return reached


def rename_pair(transaction: BrokerTransaction) -> tuple[str, str]:
    """Return the old and new names one RENAME row states."""
    if transaction.symbol is None:
        raise SymbolMissingError(transaction)
    old_symbol = transaction.description[len(RENAME_DESCRIPTION_PREFIX) :]
    if (
        not transaction.description.startswith(RENAME_DESCRIPTION_PREFIX)
        or not old_symbol
    ):
        raise InvalidTransactionError(
            transaction,
            "Rename transaction does not identify the old symbol",
        )
    return old_symbol, transaction.symbol


def plan_renames(
    state: CalculatorState,
    date_index: datetime.date,
    day_transactions: list[BrokerTransaction],
) -> None:
    """Validate the day's renames and compute capacity for eligible components."""
    pairs = [
        rename_pair(transaction)
        for transaction in day_transactions
        if transaction.action is ActionType.RENAME
    ]
    if not pairs:
        return
    # Validate every component before choosing a fallback. Excluded actions
    # must not bypass chain or conflict checks.
    components = [
        (names, _one_hop_renames(names, pairs, date_index))
        for names in _components(pairs)
    ]
    for _, renames in components:
        _refuse_rename_chain(renames, date_index)
    # Which names the day makes one holding, recorded before any of its rows
    # is read. The pool still moves where the RENAME row sits, but a row that
    # names either spelling can be resolved to the holding whatever order the
    # export lists them in, which the row-position record cannot do for a
    # rename it has not reached yet.
    for _, renames in components:
        state.history.rename_list[date_index].update(renames)
    # These rows cannot be assigned to components by ticker alone.
    if any(
        transaction.action in UNNAMED_HOLDING_ACTIONS
        for transaction in day_transactions
    ):
        return
    planned: dict[str, RenameComponent] = {}
    for names, renames in components:
        rows = [
            transaction
            for transaction in day_transactions
            if transaction.symbol in names
        ]
        if any(
            transaction.action in RENAME_DAY_UNSUPPORTED_ACTIONS for transaction in rows
        ):
            continue
        component = RenameComponent(
            names=tuple(sorted(names)),
            closing_name=_closing_name(renames),
            capacity=sum(
                (state.run.portfolio.get(name, Position()).quantity for name in names),
                signed_quantity([row for row in rows if quantity_sign(row.action) > 0]),
            ),
        )
        for name in names:
            planned[name] = component
    if planned:
        state.history.rename_components[date_index] = planned


def rename_day_units(
    history: PreparedHistory, date_index: datetime.date, symbol: str
) -> Decimal | None:
    """Return capacity minus today's disposals under every connected ticker.

    Return None for an unplanned or empty holding so the existing checks
    report sales of shares that were never held.
    """
    component = history.rename_components.get(date_index, {}).get(symbol)
    if component is None or component.capacity == 0:
        return None
    taken = sum(
        (
            history.disposal_list[date_index][name].quantity
            for name in component.names
            if has_key(history.disposal_list, date_index, name)
        ),
        Decimal(0),
    )
    return component.capacity - taken


def reconcile_rename_day(state: CalculatorState, date_index: datetime.date) -> None:
    """Move positions left under retired tickers into the closing ticker.

    This only reconciles provisional bookkeeping; it records no second rename
    and does not change the acquisition or disposal logs.
    """
    for name, component in state.history.rename_components.get(date_index, {}).items():
        if name == component.closing_name:
            continue
        position = state.run.portfolio.pop(name, None)
        if position is not None:
            state.run.portfolio[component.closing_name] += position
        # The units keep the accounts that put them there; only the name
        # changes. Which holdings have emptied is settled where every earlier
        # day has closed, in ``_open_transaction_day``.
        moved = state.history.holding_sources.pop(name, set())
        if moved:
            state.history.holding_sources[component.closing_name] |= moved


def two_renames_message(
    old_symbol: str,
    date_index: datetime.date,
    first: str,
    second: str,
) -> str:
    """Explain why one ticker cannot be renamed to two names in a day."""
    return (
        f"{old_symbol} is renamed to both {first} and {second} on "
        f"{date_index}. The holding goes to one name or the other, and a "
        "date carries no order to choose by, so which one cannot be "
        "established and the pool would be replayed under a name it never "
        "reached. Leave the row that does not belong out, or work this "
        "day out by hand (consider professional advice)."
    )


def rename_chain_message(
    old_symbol: str,
    middle: str,
    new_symbol: str,
    date_index: datetime.date,
) -> str:
    """Explain why a holding renamed twice in a day cannot be worked out."""
    return (
        f"Cannot apply the renames of {old_symbol} on {date_index}: it is "
        f"renamed to {middle}, and {middle} is renamed to {new_symbol}, the "
        "same day. The renames are applied in the order the input lists them, "
        "so where the holding ends up depends on which of the two rows comes "
        "first, and a date carries no order to choose by. Put the renames on "
        "the days they happened, or work this day out by hand (consider "
        "professional advice)."
    )


def _components(pairs: list[tuple[str, str]]) -> Iterator[set[str]]:
    """Group the day's renames into the holdings they connect.

    A rename says two tickers are one security, so two holdings the day
    renames onto the same name belong together, even though no single row
    names them both. The graph is walked in both directions for that reason.
    """
    neighbours: dict[str, set[str]] = defaultdict(set)
    for old_name, new_name in pairs:
        neighbours[old_name].add(new_name)
        neighbours[new_name].add(old_name)
    seen: set[str] = set()
    for name in neighbours:
        if name in seen:
            continue
        group = {name}
        frontier = [name]
        while frontier:
            for other in neighbours[frontier.pop()]:
                if other not in group:
                    group.add(other)
                    frontier.append(other)
        seen |= group
        yield group


def _one_hop_renames(
    names: set[str],
    pairs: list[tuple[str, str]],
    date_index: datetime.date,
) -> dict[str, str]:
    """Return one component's old to new mappings, refusing a split holding."""
    renames: dict[str, str] = {}
    for old_name, new_name in pairs:
        if old_name not in names:
            continue
        existing = renames.get(old_name)
        if existing is not None and existing != new_name:
            raise CalculationError(
                two_renames_message(old_name, date_index, existing, new_name)
            )
        renames[old_name] = new_name
    return renames


def _refuse_rename_chain(renames: dict[str, str], date_index: datetime.date) -> None:
    """Refuse chains and cycles, whose result would depend on rename row order.

    Sort names so either row order produces the same diagnostic.
    """
    for name in sorted({*renames, *renames.values()}):
        onward = renames.get(name)
        if onward is not None and onward in renames:
            raise CalculationError(
                rename_chain_message(name, onward, renames[onward], date_index)
            )


def _closing_name(renames: dict[str, str]) -> str:
    """Return the one name this holding's renames leave it under.

    One holding, one destination: a second one could only join this component
    through a name that is both renamed and renamed to, which is the chain
    ``_refuse_rename_chain`` has already ruled out.
    """
    (closing_name,) = set(renames.values())
    return closing_name
