"""First pass: planning what a day's share reorganisations do."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from fractions import Fraction
import logging
from typing import TYPE_CHECKING, Literal

from .exceptions import (
    CalculationError,
    InvalidTransactionError,
    QuantityMissingError,
    QuantityNotPositiveError,
    SymbolMissingError,
)
from .model import ActionType, Position
from .parsers.raw import RawTransaction
from .stock_splits import (
    SplitMode,
    SplitTransformation,
    StockSplitEvent,
    StockSplitTransaction,
    UnresolvedRatio,
    quantity_sign,
    scale_quantity,
)
from .util import indent_entry, strip_zeros

if TYPE_CHECKING:
    import datetime

    from .calculator_state import CalculatorState
    from .model import BrokerTransaction, TransactionSource

LOGGER = logging.getLogger(__name__)


def _get_symbol_or_fail(transaction: BrokerTransaction) -> str:
    """Return the transaction symbol or raise an error if missing."""
    symbol = transaction.symbol
    if symbol is None:
        raise SymbolMissingError(transaction)
    return symbol


def _get_quantity_or_fail(transaction: BrokerTransaction) -> Decimal:
    """Return the transaction quantity or raise an error if missing."""
    quantity = transaction.quantity
    if quantity is None:
        raise QuantityMissingError(transaction)
    return quantity


def _stated_quantity(transaction: BrokerTransaction) -> Decimal:
    """Return a trade row's stated count, refusing one that is not positive.

    A day is planned before any of its rows has been processed, so a row
    stating nothing, or a count of zero or less, has to be refused where the
    plan reads it. Left to the row's own processing it would first shape the
    plan, and whichever valid row is then measured against that plan is the
    one the calculator blames.

    Only rows that move a share count are read this way. A reorganisation
    row states its own delta, which a consolidation states as a negative
    number.
    """
    quantity = _get_quantity_or_fail(transaction)
    if quantity <= 0:
        raise QuantityNotPositiveError(transaction)
    return quantity


def source_account(transaction: BrokerTransaction) -> str:
    """Return the declared account boundary this transaction was loaded under.

    Every parser records one, so a transaction without provenance is
    hand-built. Those fall back to the broker name, which groups them, and
    the safe reading of that is a boundary shared by everything from that
    broker rather than a boundary of its own.
    """
    if transaction.source is not None and transaction.source.account is not None:
        return transaction.source.account
    return f"{transaction.broker} (source unrecorded)"


def _relative_to_split(
    instants: tuple[datetime.datetime, ...],
    split_source: TransactionSource | None,
    other_source: TransactionSource | None,
) -> Literal["before", "after"] | None:
    """Say whether a row happened before or after a reorganisation.

    An exported instant settles it wherever both rows carry one. Failing
    that, the only other evidence is a source that documents its same-date
    rows as being in the order they happened, which a hand-written RAW
    history does and a broker export does not. Nothing else will do: several
    parsers reorder their rows for the calculation's sake, and the registry's
    stable ordering and its action priorities for gifts, spouse transfers and
    vests are calculation plumbing rather than evidence of corporate-action
    chronology. Returns None when it cannot be told.

    What a lone instant proves is one-sided. An export that states the whole
    holding on both sides of the event brackets the restatement in the
    broker's own books, and its own counts are what the ratio comes from, so
    a row outside that bracket is placed against a figure the broker stated.
    An export that posts the event as one row states only when it booked the
    entry: everything after it is in the new units, because the holding had
    already been restated when the row was written, but a row before it may
    have traded in either unit system - the market restates at the opening of
    the ex-date, and nothing says the broker's book-keeping ran to the same
    clock. Guessing there would not merely misplace that row: a single row
    states a net change, so `before_quantity` is whatever was placed ahead of
    it and the corporate ratio is derived from that. One row on the wrong
    side rewrites the ratio for the entire holding.
    """
    if instants and other_source is not None and other_source.timestamp is not None:
        if other_source.timestamp > max(instants):
            return "after"
        if len(instants) > 1 and other_source.timestamp < min(instants):
            return "before"
        # At or between the two halves of the reorganisation, or ahead of a
        # lone booking instant: no unit system can be proved.
        return None
    if (
        split_source is not None
        and other_source is not None
        and split_source.rows_in_time_order
        and other_source.rows_in_time_order
        and split_source.account is not None
        and split_source.index is not None
        and other_source.index is not None
        and (split_source.parser, split_source.account, split_source.file)
        == (other_source.parser, other_source.account, other_source.file)
    ):
        return "before" if other_source.index < split_source.index else "after"
    return None


def plan_stock_splits(
    state: CalculatorState,
    date_index: datetime.date,
    day_transactions: list[BrokerTransaction],
) -> None:
    """Decide what each of the day's reorganisations does to a holding.

    Runs before any of the day's rows are processed, so the portfolio
    still holds the day-opening pool and every source row and its ordering
    are still to hand. Both passes replay what is recorded here.
    """
    splits: dict[str, list[BrokerTransaction]] = defaultdict(list)
    for transaction in day_transactions:
        if transaction.action is ActionType.STOCK_SPLIT:
            splits[_get_symbol_or_fail(transaction)].append(transaction)
    for symbol, rows in sorted(splits.items()):
        for row in rows:
            _check_reorganisation_row(row)
        row = _sole_reorganisation(symbol, date_index, rows)
        _plan_stock_split(state, date_index, symbol, row, day_transactions)


def _check_reorganisation_row(transaction: BrokerTransaction) -> None:
    """Refuse a reorganisation row that also states money.

    A reorganisation is neither an acquisition nor a disposal, so nothing
    is paid for it and nothing is received (TCGA 1992 s127, CG51805).
    Anything in the price, fees or amount column is therefore a figure the
    calculation has no use for and would drop without saying so, and a row
    that states one is a row about something other than the reorganisation.
    """
    quantity = transaction.quantity
    for column, value in (
        ("price", transaction.price),
        ("fees", transaction.fees),
        ("amount", transaction.amount),
    ):
        if (
            column == "amount"
            and value is not None
            and not value.is_finite()
            and quantity is not None
            and not quantity.is_finite()
        ):
            # A RAW row derives its amount from the quantity, so a
            # non-finite quantity makes a non-finite amount the user never
            # wrote. The quantity check downstream names the real defect.
            continue
        if value:
            raise InvalidTransactionError(
                transaction,
                f"The {column} column of a share reorganisation states "
                f"{strip_zeros(value)}. A reorganisation buys nothing and "
                "sells nothing (TCGA 1992 s127), so there is nothing for "
                "that figure to be, and cgt-calc will not drop it without "
                "saying so. Leave every money column at 0 or blank. If "
                "money really did change hands (cash in lieu of a "
                "fractional entitlement, say), it is a part disposal under "
                "TCGA 1992 s128 or a small distribution under s122(2), and "
                "cgt-calc can represent neither: leave money off this row "
                "and work the payment and the holding's later figures out "
                "separately",
            )
    if transaction.foreign_fees:
        listed = ", ".join(
            f"{currency} {strip_zeros(value)}"
            for currency, value in sorted(transaction.foreign_fees.items())
            if value
        )
        if listed:
            raise InvalidTransactionError(
                transaction,
                "The fees column of a share reorganisation states "
                f"{listed}. A reorganisation buys nothing and sells "
                "nothing (TCGA 1992 s127), so leave all fee columns at 0",
            )


def _conflicting_reorganisation_times(
    rows: list[BrokerTransaction],
) -> list[str]:
    """Return times proving one source reported more than one event.

    The source is the declared account, not the file. One account is
    routinely exported as several date-range files, and two of its events
    landing in different files is a fact about how the history was
    downloaded, not evidence that two accounts each reported one event.
    Keying on the file would put every such pair in a bucket of its own,
    find no conflict, and let one RAW row stand for both.

    ``_relative_to_split`` deliberately does keep the file in its own key:
    it compares ``source.index``, which ``stamp_source`` restarts at zero
    for every file, so an index from one file says nothing about a row in
    another. Nothing here reads an index.
    """
    times_by_source: dict[
        tuple[str | None, str | None], list[datetime.datetime | None]
    ] = defaultdict(list)
    for row in rows:
        source = row.source
        source_key = (
            (source.parser, source.account) if source is not None else (None, None)
        )
        times_by_source[source_key].append(
            source.timestamp if source is not None else None
        )
    conflicting_times = [
        times
        for times in times_by_source.values()
        if len(times) > 1 and (None in times or len(set(times)) > 1)
    ]
    return sorted(
        "unknown" if timestamp is None else str(timestamp)
        for times in conflicting_times
        for timestamp in times
    )


def _sole_reorganisation(
    symbol: str,
    date_index: datetime.date,
    rows: list[BrokerTransaction],
) -> BrokerTransaction:
    """Return the one row the day's reorganisation of ``symbol`` is taken from.

    Two brokers reporting the same event is not two events, and nothing in
    their exports tells the two apart, so several broker rows are refused.
    A hand-written RAW row is different: it states the change to the whole
    pooled holding by definition, which is exactly what a broker's own row
    cannot state and exactly what the calculator asks for when it refuses
    one. So a single RAW row settles the day and the broker rows for the
    same event are dropped, the way a RAW row classifying a gift drops the
    broker's own row for it.
    """
    if len(rows) == 1:
        return rows[0]
    stated_in_full = [row for row in rows if isinstance(row, RawTransaction)]
    if len(stated_in_full) != 1:
        raise CalculationError(_two_splits_message(symbol, date_index, rows))
    raw_row = stated_in_full[0]
    broker_rows = [row for row in rows if row is not raw_row]
    broker_changes = [_get_quantity_or_fail(row) for row in broker_rows]
    raw_change = _get_quantity_or_fail(raw_row)
    if event_times := _conflicting_reorganisation_times(broker_rows):
        listed = "\n".join(indent_entry(row) for row in rows)
        raise CalculationError(
            f"Cannot use the RAW STOCK_SPLIT row for {symbol} on "
            f"{date_index}: the same source reports reorganisations at "
            f"different or unknown times ({', '.join(event_times)}):\n"
            f"{listed}\nThese rows are not proved to report the same "
            "reorganisation. Leave the day's STOCK_SPLIT rows out and work "
            "this day out by hand (consider professional advice)."
        )
    if not raw_change.is_finite():
        return raw_row
    if any(not change.is_finite() for change in broker_changes):
        listed = "\n".join(indent_entry(row) for row in rows)
        raise CalculationError(
            f"Cannot use the RAW STOCK_SPLIT row for {symbol} on "
            f"{date_index}: a broker row states a change that cannot be "
            f"part of its change of {strip_zeros(raw_change)} units:\n"
            f"{listed}\nCorrect the broker or RAW row, or work this day "
            "out by hand (consider professional advice)."
        )
    broker_change = sum(broker_changes, Decimal(0))
    if (
        not raw_change
        or any(change * raw_change <= 0 for change in broker_changes)
        or abs(broker_change) > abs(raw_change)
    ):
        listed = "\n".join(indent_entry(row) for row in rows)
        raise CalculationError(
            f"Cannot use the RAW STOCK_SPLIT row for {symbol} on "
            f"{date_index}: the broker rows change their holdings by "
            f"{strip_zeros(broker_change)} units in total, which cannot "
            "be part of its change of "
            f"{strip_zeros(raw_change)} units:\n"
            f"{listed}\nThese rows are not proved to report the same "
            "reorganisation. Correct the RAW row or work this day out by "
            "hand (consider professional advice)."
        )
    LOGGER.info(
        "Using the RAW STOCK_SPLIT row for %s on %s and ignoring %d broker "
        "row(s) for the same event: a RAW row states the change to the "
        "whole holding.",
        symbol,
        date_index,
        len(rows) - 1,
    )
    return raw_row


def _plan_stock_split(
    state: CalculatorState,
    date_index: datetime.date,
    symbol: str,
    row: BrokerTransaction,
    day_transactions: list[BrokerTransaction],
) -> None:
    """Work out and record one reorganisation."""
    instants = _split_instants(row)
    before_rows: list[BrokerTransaction] = []
    after_rows: list[BrokerTransaction] = []
    for other in day_transactions:
        if other is row or other.symbol != symbol or quantity_sign(other.action) == 0:
            continue
        placement = _relative_to_split(instants, row.source, other.source)
        if placement is None:
            raise CalculationError(
                _split_chronology_message(symbol, date_index, row, other, instants)
            )
        (before_rows if placement == "before" else after_rows).append(other)

    day_open_quantity = state.run.portfolio[symbol].quantity
    holding_at_event = day_open_quantity + sum(
        (
            quantity_sign(other.action) * _stated_quantity(other)
            for other in before_rows
        ),
        Decimal(0),
    )
    if holding_at_event <= 0:
        raise CalculationError(
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"the holding is {strip_zeros(holding_at_event)} units at that "
            "point, so there is nothing to restate. The history is missing "
            f"the {symbol} shares it acts on."
        )

    event = _stock_split_event(
        state, row, symbol, date_index, holding_at_event, instants, before_rows
    )
    ratio = event.ratio
    if isinstance(ratio, UnresolvedRatio):
        if before_rows:
            raise CalculationError(_unresolved_same_day_message(event, before_rows[0]))
        if holding_at_event != event.before_quantity:
            raise CalculationError(_unresolved_scaling_message(event))
        mode = SplitMode.DIRECT_SUBSTITUTION
        exact_ratio = None
        scaled_day_open_quantity = event.after_quantity
    else:
        exact_ratio = ratio
        scaled_day_open_quantity = scale_quantity(day_open_quantity, ratio)
        mode = (
            SplitMode.BROKER_EXACT
            if holding_at_event == event.before_quantity
            else SplitMode.RATIO_ONLY
        )
        # Restate the day's earlier rows into the units the day ends in,
        # exactly once and without touching their cost, proceeds or fees.
        # A buy of 10 shares for £100 followed by a 2-for-1 split that day
        # becomes an acquisition of 20 shares for £100, which is the same
        # pool by another route and needs no special case anywhere else.
        for other in before_rows:
            other.calculation_quantity = scale_quantity(
                _get_quantity_or_fail(other), exact_ratio
            )

    # What the event leaves of the holding it acts on. The day-opening
    # pool is deliberately not the test: a holding first acquired earlier
    # the same day opens at nothing and is still restated normally.
    resulting_holding = (
        event.after_quantity
        if exact_ratio is None
        else scale_quantity(holding_at_event, exact_ratio)
    )
    if resulting_holding <= 0:
        raise CalculationError(
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"it leaves {strip_zeros(resulting_holding)} units of a holding "
            f"of {strip_zeros(holding_at_event)}."
        )

    post_split_delta = signed_quantity(after_rows)
    normalised_day_delta = post_split_delta + signed_quantity(before_rows)
    if mode is SplitMode.BROKER_EXACT:
        expected_day_close = event.after_quantity + post_split_delta
        reconciliation_delta = expected_day_close - (
            scaled_day_open_quantity + normalised_day_delta
        )
    else:
        # One broker's rounded count must not adjust a pool built from
        # more than one source, and a substitution has nothing to round.
        reconciliation_delta = Decimal(0)
        expected_day_close = scaled_day_open_quantity + normalised_day_delta
    if expected_day_close < 0:
        raise CalculationError(
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"the day's activity would leave {strip_zeros(expected_day_close)} "
            "units."
        )

    state.history.splits[date_index][symbol] = SplitTransformation(
        event=event,
        mode=mode,
        ratio=exact_ratio,
        day_open_quantity=day_open_quantity,
        scaled_day_open_quantity=scaled_day_open_quantity,
        reconciliation_delta=reconciliation_delta,
        expected_day_close=expected_day_close,
    )
    # The reorganisation belongs at the start of its day: the rows before
    # it are now stated in the units it leaves behind, so the pool has to
    # be in those units before any of them is processed. The pooled cost
    # is untouched, because a reorganisation only changes how many units
    # the same money bought (TCGA 1992 s127).
    #
    # The reconciliation goes on at the same moment, which is not where
    # the recorded replay puts it. That order -- increases, reconcile,
    # decreases -- is what the second pass follows, and the second pass is
    # what works out the tax. All this pool does is refuse a history that
    # sells shares it does not have, and for that the day's closing count
    # is the honest figure to check against: a day whose reconciliation is
    # positive can otherwise drain to nothing partway through and refuse
    # its own last disposal. A negative reconciliation can leave the count
    # briefly below zero here, ahead of the day's acquisitions, which
    # nothing reads.
    position = state.run.portfolio[symbol]
    state.run.portfolio[symbol] = Position(
        scaled_day_open_quantity + reconciliation_delta, position.amount
    )
    # Everything the day can give up, in the order the replay puts it:
    # the restated opening, then every increase, then the reconciliation.
    # Decreases draw on this rather than on the running count.
    state.history.split_day_capacity[symbol, date_index] = (
        scaled_day_open_quantity
        + signed_quantity(
            [
                other
                for other in (*before_rows, *after_rows)
                if quantity_sign(other.action) > 0
            ]
        )
        + reconciliation_delta
    )


def signed_quantity(transactions: list[BrokerTransaction]) -> Decimal:
    """Net units these rows add to a holding, in their pooled counts."""
    total = Decimal(0)
    for transaction in transactions:
        stated = _stated_quantity(transaction)
        # ``BrokerTransaction.pool_quantity``, now that the stated count is
        # known to be there: a same-day reorganisation restates a row into the
        # units the day ends in, and that is the count the pool moves by.
        restated = transaction.calculation_quantity
        total += quantity_sign(transaction.action) * (
            stated if restated is None else restated
        )
    return total


def _split_instants(row: BrokerTransaction) -> tuple[datetime.datetime, ...]:
    """Instants the export stamped on the rows behind a reorganisation."""
    if isinstance(row, StockSplitTransaction):
        return row.event.instants
    if row.source is not None and row.source.timestamp is not None:
        return (row.source.timestamp,)
    return ()


def _stock_split_event(
    state: CalculatorState,
    row: BrokerTransaction,
    symbol: str,
    date_index: datetime.date,
    holding_at_event: Decimal,
    instants: tuple[datetime.datetime, ...],
    before_rows: list[BrokerTransaction],
) -> StockSplitEvent:
    """Return the reorganisation this row states.

    A paired export states both counts itself. A single row states only a
    net change, which is a change to the whole pooled holding in a RAW
    history by definition, and a change to one account's holding when a
    broker wrote it. The second may only be read as the first once the
    whole holding is known to have come from that one account: adding one
    account's delta to a pool built from several derives a ratio that was
    never the corporate one.
    """
    if isinstance(row, StockSplitTransaction):
        return row.event
    delta = _get_quantity_or_fail(row)
    if not delta.is_finite():
        raise CalculationError(
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"it states a change of {delta} units, which is not a number "
            "of shares."
        )
    after_quantity = holding_at_event + delta
    if after_quantity <= 0:
        raise CalculationError(
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"a change of {strip_zeros(delta)} units leaves "
            f"{strip_zeros(after_quantity)} of a holding of "
            f"{strip_zeros(holding_at_event)}."
        )
    if not isinstance(row, RawTransaction):
        contributors = state.history.holding_sources.get(symbol, set()) | {
            source_account(other)
            for other in before_rows
            if quantity_sign(other.action) > 0
        }
        account = source_account(row)
        if contributors - {account}:
            raise CalculationError(
                _broker_split_delta_message(
                    symbol, date_index, account, sorted(contributors - {account})
                )
            )
    return StockSplitEvent(
        date=date_index,
        broker=row.broker,
        symbol_before=symbol,
        symbol_after=symbol,
        isin_before=row.isin,
        isin_after=row.isin,
        before_quantity=holding_at_event,
        after_quantity=after_quantity,
        ratio=Fraction(after_quantity) / Fraction(holding_at_event),
        instants=instants,
    )


def _two_splits_message(
    symbol: str,
    date_index: datetime.date,
    rows: list[BrokerTransaction],
) -> str:
    """Explain why two reorganisations of one holding cannot be combined."""
    listed = "\n".join(indent_entry(row) for row in rows)
    if any(isinstance(row, RawTransaction) for row in rows):
        # A single RAW row settles a day the brokers also reported, so
        # more than one of them is the thing to fix rather than to add.
        remedy = (
            "Leave all but one of the RAW STOCK_SPLIT rows out, so that "
            "one row states the change to the whole holding, or work this "
            "day out by hand (consider professional advice)"
        )
    elif event_times := _conflicting_reorganisation_times(rows):
        remedy = (
            "The same source reports different or unknown times "
            f"({', '.join(event_times)}), so a single RAW STOCK_SPLIT row "
            "cannot settle these as one event. Leave these STOCK_SPLIT "
            "rows out and work this day out by hand (consider professional "
            "advice)"
        )
    else:
        remedy = (
            "Add a single RAW STOCK_SPLIT row stating the change to the "
            "whole holding, which settles the day and is used in place of "
            "the broker rows, or work this day out by hand (consider "
            "professional advice)"
        )
    return (
        f"{symbol} has more than one reorganisation on {date_index}:\n"
        f"{listed}\n"
        "The Section 104 holding is global, so a corporate ratio applies "
        "to it once. Two brokers reporting the same event is not the same "
        "as two events, and this tool cannot tell them apart, so it will "
        f"not guess. {remedy}."
    )


def _split_chronology_message(
    symbol: str,
    date_index: datetime.date,
    row: BrokerTransaction,
    other: BrokerTransaction,
    instants: tuple[datetime.datetime, ...],
) -> str:
    """Explain why a same-day row cannot be placed around a reorganisation.

    Times the export does state are not always enough, so the reason has
    to say which of the two situations this is. Telling someone whose
    export already states its times to supply times would send them
    looking for a file that does not exist.
    """
    preamble = (
        f"Cannot apply the reorganisation of {symbol} on {date_index}: "
        f"this row is on the same day and cannot be placed either side of "
        f"it, so there is no telling whether its count is stated in the "
        f"units before or after:\n{indent_entry(other)}\n"
        f"The reorganisation is:\n{indent_entry(row)}\n"
    )
    stated = other.source is not None and other.source.timestamp is not None
    if len(instants) == 1 and stated:
        return preamble + (
            "Both rows state their times, but the reorganisation is a "
            "single row, and the instant on it is when the broker booked "
            "the entry rather than when the units changed: a share trades "
            "in the new units from the opening of the ex-date, which need "
            "not be when the broker wrote the row. Anything the export "
            "booked after that row is in the new units; this row was not. "
            "Work the day out by hand (consider professional advice)."
        )
    return preamble + (
        "The two come from different inputs, or from an export that gives "
        "neither times nor a documented order, or the row falls between "
        "the two halves of the reorganisation. Run again with the day's "
        f"{symbol} rows in one input that states times, or work the day "
        "out by hand (consider professional advice)."
    )


def _unresolved_same_day_message(
    event: StockSplitEvent, other: BrokerTransaction
) -> str:
    """Explain why an unrecoverable ratio cannot restate an earlier row."""
    assert isinstance(event.ratio, UnresolvedRatio)
    return (
        f"Cannot apply the reorganisation of {event.describe()}: "
        f"{event.symbol} also changed hands earlier that day:\n{indent_entry(other)}\n"
        "That row's count is in the units before the reorganisation, so it "
        "has to be restated into the units after it, and the ratio for "
        f"that cannot be recovered from the export: {event.ratio.describe()}. "
        "Run again with the day's other rows left out, or work the day out "
        "by hand (consider professional advice)."
    )


def _unresolved_scaling_message(event: StockSplitEvent) -> str:
    """Explain why an unrecoverable ratio cannot scale the pool."""
    assert isinstance(event.ratio, UnresolvedRatio)
    return (
        f"Cannot apply the reorganisation of {event.describe()}: the "
        f"holding is not the {strip_zeros(event.before_quantity)} units "
        "the export states, so it has to be scaled by the corporate ratio "
        f"rather than replaced, and that ratio cannot be recovered from "
        f"the export: {event.ratio.describe()}. This happens when the same "
        "security is held through more than one broker. Work the "
        "reorganisation out by hand (consider professional advice)."
    )


def _broker_split_delta_message(
    symbol: str,
    date_index: datetime.date,
    account: str,
    others: list[str],
) -> str:
    """Explain why one account's split delta is not a global one."""
    listed = ", ".join(others)
    return (
        f"Cannot apply the reorganisation of {symbol} on {date_index}: it "
        f"is a single row from {account} stating how many units that "
        "account gained or lost, and this holding also has units from "
        f"{listed}. Adding one account's change to a holding built from "
        "several derives a ratio that was never the corporate one. Supply "
        "the reorganisation as a RAW STOCK_SPLIT row stating the change to "
        "the whole holding, or as a broker export that states the counts "
        "either side of the event."
    )
