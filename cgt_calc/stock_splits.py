"""Share reorganisations: stock splits and share consolidations.

The old holding is not disposed of and the new one is not acquired: the two
are the same asset, held continuously, with the same acquisition dates and
the same allowable cost (TCGA 1992 ss126-127, HMRC CG51805). Only the unit
count changes, so the pooled cost is never multiplied, divided or rounded
here and everything in this module is about the share count.

Two shapes of input are modelled. Most parsers state a reorganisation as one
row carrying the net change to a share count, which the calculator reads
directly. `StockSplitTransaction`, `recover_ratio` and `UnresolvedRatio` are
for the other shape, an export that states the complete holding either side of
the event and leaves the ratio to be recovered from two rounded counts, which
is how Trading 212 exports one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import math
from typing import TYPE_CHECKING, Final, Literal

from .exceptions import QuantityMissingError, QuantityNotPositiveError
from .model import ActionType, BrokerTransaction
from .util import normalize_amount, strip_zeros

if TYPE_CHECKING:
    import datetime

    from .model import CurrencyCode, Isin, TransactionSource

# Exported quantities print to ten decimal places but are granular to 1e-8,
# so the quotient of two of them is not the corporate ratio. This is the
# uncertainty each printed quantity is read with.
QUANTITY_EPSILON: Final = Decimal("1E-8")

# The largest numerator and denominator a recovered ratio may use. A ratio
# outside this needs an explicit input rather than a guess.
RATIO_SEARCH_BOUND: Final = 1000

# Candidates an ambiguous recovery keeps, to name in the error.
RATIO_SAMPLE_COUNT: Final = 2


# Everything else leaves the count alone, so it has no unit system to be in
# and no part in a reorganisation.
QUANTITY_INCREASING_ACTIONS: Final = frozenset(
    {
        ActionType.BUY,
        ActionType.REINVEST_SHARES,
        ActionType.STOCK_ACTIVITY,
        ActionType.SPIN_OFF,
        ActionType.TRANSFER_FROM_SPOUSE,
    }
)
QUANTITY_DECREASING_ACTIONS: Final = frozenset(
    {
        ActionType.SELL,
        ActionType.CASH_MERGER,
        ActionType.FULL_REDEMPTION,
        ActionType.TRANSFER_TO_SPOUSE,
        ActionType.UNCLASSIFIED_GIFT,
        ActionType.GIFT,
        ActionType.GIFT_UNCONNECTED,
    }
)


def quantity_sign(action: ActionType) -> int:
    """Return which way an action moves the share count, or 0 for neither."""
    if action in QUANTITY_INCREASING_ACTIONS:
        return 1
    if action in QUANTITY_DECREASING_ACTIONS:
        return -1
    return 0


def get_quantity_or_fail(transaction: BrokerTransaction) -> Decimal:
    """Return the transaction quantity or raise an error if missing."""
    quantity = transaction.quantity
    if quantity is None:
        raise QuantityMissingError(transaction)
    return quantity


def stated_quantity(transaction: BrokerTransaction) -> Decimal:
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
    quantity = get_quantity_or_fail(transaction)
    if quantity <= 0:
        raise QuantityNotPositiveError(transaction)
    return quantity


def signed_quantity(transactions: list[BrokerTransaction]) -> Decimal:
    """Net units these rows add to a holding, in their pooled counts."""
    total = Decimal(0)
    for transaction in transactions:
        stated = stated_quantity(transaction)
        # ``BrokerTransaction.pool_quantity``, now that the stated count is
        # known to be there: a same-day reorganisation restates a row into the
        # units the day ends in, and that is the count the pool moves by.
        restated = transaction.calculation_quantity
        total += quantity_sign(transaction.action) * (
            stated if restated is None else restated
        )
    return total


def scale_quantity(quantity: Decimal, ratio: Fraction) -> Decimal:
    """Convert a count in the old units into the new ones.

    Multiplying first keeps the integer factor exact, so the single division
    at the end is the only place a repeating decimal can appear, rounded once
    at the ten decimal places the rest of the calculator uses.
    """
    return normalize_amount(quantity * ratio.numerator / ratio.denominator)


def unscale_quantity(quantity: Decimal, ratio: Fraction) -> Decimal:
    """Convert a count in the new units back into the old ones."""
    return normalize_amount(quantity * ratio.denominator / ratio.numerator)


@dataclass(frozen=True)
class UnresolvedRatio:
    """No single corporate ratio follows from the exported quantities.

    Carried through parsing rather than raised there: a reorganisation of a
    holding the broker states in full needs no ratio, so refusing here would
    reject a whole export for a number nothing asked for. The calculator
    refuses at each point the ratio is consumed instead.
    """

    reason: Literal["none", "multiple"]
    # Zero candidates, or the first two in canonical order, for diagnostics.
    samples: tuple[Fraction, ...]
    # The quotient of the two rounded quantities, which is not the ratio.
    observed: Fraction | None
    bound: int

    def describe(self) -> str:
        """Explain, for an error message, why there is no ratio."""
        if self.reason == "none":
            detail = (
                "no ratio with a numerator and denominator of "
                f"{self.bound} or less maps one to the other"
            )
        else:
            candidates = ", ".join(str(sample) for sample in self.samples)
            detail = f"at least two ratios fit them ({candidates}, and possibly more)"
        observed = f", whose quotient is {self.observed}" if self.observed else ""
        return f"{detail}{observed}"


def recover_ratio(
    before: Decimal,
    after: Decimal,
    *,
    epsilon: Decimal = QUANTITY_EPSILON,
    bound: int = RATIO_SEARCH_BOUND,
) -> Fraction | UnresolvedRatio:
    """Recover the corporate ratio from two rounded share counts.

    Their quotient is not the ratio and no arithmetic precision makes it one:
    a 1-for-9 consolidation of a whole share exports as 0.1111111100, and
    dividing that by 1 is an exact division that still is not 1/9. What the
    counts establish is an interval. Every reduced fraction within ``bound``
    is tested for whether some true ``before`` inside its uncertainty
    interval maps to some true ``after`` inside its own. The test is
    symmetric, so it needs no direction-dependent tolerance: reconstructing a
    small ``before`` from a large ``after`` would amplify the rounding by the
    ratio itself and reject a correct candidate.

    Exactly one admissible ratio is the answer. None or several is not a
    guess to be papered over with the observed quotient.
    """
    before_exact = Fraction(before)
    after_exact = Fraction(after)
    epsilon_exact = Fraction(epsilon)
    observed = after_exact / before_exact if before_exact else None

    # For a fixed denominator the admissible numerators are one contiguous
    # range, so a thousand interval calculations settle the whole search.
    low = max(after_exact - epsilon_exact, Fraction(0)) / (before_exact + epsilon_exact)
    high_divisor = max(before_exact - epsilon_exact, Fraction(0))
    high = (after_exact + epsilon_exact) / high_divisor if high_divisor else None

    found: list[Fraction] = []
    for denominator in range(1, bound + 1):
        first = max(math.ceil(low * denominator), 1)
        last = bound if high is None else min(math.floor(high * denominator), bound)
        for numerator in range(first, last + 1):
            candidate = Fraction(numerator, denominator)
            # A fraction that reduces was already reached at its own, smaller
            # denominator, so this keeps every candidate distinct without
            # holding the whole set: a small enough pair admits 608,383 of
            # them within the bound.
            if candidate.denominator != denominator:
                continue
            found.append(candidate)
            if len(found) == RATIO_SAMPLE_COUNT:
                return UnresolvedRatio(
                    reason="multiple",
                    samples=tuple(found),
                    observed=observed,
                    bound=bound,
                )
    if len(found) == 1:
        return found[0]
    return UnresolvedRatio(reason="none", samples=(), observed=observed, bound=bound)


@dataclass(frozen=True)
class StockSplitEvent:
    """One share reorganisation, as the input stated it."""

    date: datetime.date
    broker: str
    symbol_before: str
    symbol_after: str
    isin_before: Isin | None
    isin_after: Isin | None
    # The complete holding at this broker before and after the event.
    before_quantity: Decimal
    after_quantity: Decimal
    ratio: Fraction | UnresolvedRatio
    # The instants the export stamped on the rows behind this event: none for
    # a history that states dates only, one for a single row, two for a pair.
    instants: tuple[datetime.datetime, ...] = ()

    def __post_init__(self) -> None:
        """Refuse an event that also changes the identifier.

        Combining two securities needs authoritative relationship data, which
        no supported input carries, so a reorganisation that renames as it
        restates is out of scope rather than guessed at from adjacency.
        """
        if self.symbol_before != self.symbol_after:
            raise ValueError(
                f"A stock split cannot change the ticker: {self.symbol_before} "
                f"to {self.symbol_after}"
            )
        if (
            self.isin_before is not None
            and self.isin_after is not None
            and self.isin_before != self.isin_after
        ):
            raise ValueError(
                f"A stock split cannot change the ISIN: {self.isin_before} "
                f"to {self.isin_after}"
            )

    @property
    def symbol(self) -> str:
        """The ticker, which the event cannot change."""
        return self.symbol_before

    @property
    def isin(self) -> Isin | None:
        """The identifier, which the event cannot change."""
        return self.isin_before or self.isin_after

    @property
    def exact_ratio(self) -> Fraction | None:
        """The ratio, or None when it could not be recovered."""
        return self.ratio if isinstance(self.ratio, Fraction) else None

    def describe(self) -> str:
        """Name the event for an error message."""
        return (
            f"{self.symbol} on {self.date} "
            f"({strip_zeros(self.before_quantity)} -> "
            f"{strip_zeros(self.after_quantity)} units, "
            f"ratio {self.ratio if self.exact_ratio else 'unresolved'})"
        )


class SplitMode(Enum):
    """How one reorganisation is applied to the Section 104 holding."""

    # The holding matched the broker's own before count exactly, so the whole
    # holding is at this broker and its after count can be honoured to the
    # unit. The pool is scaled by the exact ratio and a reconciliation delta
    # absorbs the difference from the broker's rounding.
    BROKER_EXACT = 1

    # The ratio could not be recovered, but the holding matched the broker's
    # before count and nothing earlier that day needs restating, so the count
    # is replaced outright with no division and no rounding.
    DIRECT_SUBSTITUTION = 2

    # The holding did not match, so it holds shares this event does not
    # account for. The global pool is scaled by the exact ratio, without the
    # broker-specific correction: one broker's rounded count must not adjust
    # a pool built from several.
    RATIO_ONLY = 3


@dataclass(frozen=True)
class SplitTransformation:
    """What both calculation passes replay for one security on one day.

    Decided once and never re-evaluated: substituting the broker's count in
    one pass while scaling by the ratio in the other diverges, and leaves a
    disposal of the whole holding short of zero.
    """

    event: StockSplitEvent
    mode: SplitMode
    # None only for a direct substitution, which needs no ratio.
    ratio: Fraction | None
    # The pool before any of the day's activity, and what the reorganisation
    # makes of it.
    day_open_quantity: Decimal
    scaled_day_open_quantity: Decimal
    # Absorbs the broker's rounding of the after count, and the difference
    # between normalising the opening pool and the day's earlier rows
    # separately rather than as one sum. Quantity only: it is part of the
    # reorganisation, not an acquisition, a disposal or a cash movement.
    reconciliation_delta: Decimal
    expected_day_close: Decimal

    @property
    def ratio_description(self) -> str:
        """The ratio as the report and the log state it."""
        if self.ratio is not None:
            return str(self.ratio)
        return (
            f"unresolved — broker {strip_zeros(self.event.before_quantity)} -> "
            f"{strip_zeros(self.event.after_quantity)} used directly"
        )


@dataclass(frozen=True)
class StockSplitDetail:
    """What one reorganisation did to the holding, for the report.

    Kept apart from ``CalculationEntry.quantity``, which would otherwise have
    to hold counts in two different unit systems at once.
    """

    ratio_description: str
    broker_before_quantity: Decimal
    broker_after_quantity: Decimal
    day_open_quantity: Decimal
    scaled_day_open_quantity: Decimal
    quantity_before_reconciliation: Decimal
    quantity_after_reconciliation: Decimal
    reconciliation_delta: Decimal


class StockSplitTransaction(BrokerTransaction):
    """A share reorganisation stated as two complete share counts.

    The quantity is the net change at this broker, so a consolidation states
    a negative one. Nothing reads it as an acquisition: it is here so the
    event travels with the history, and the calculator applies ``event``.
    """

    def __init__(
        self,
        event: StockSplitEvent,
        description: str,
        currency: CurrencyCode,
        source: TransactionSource | None = None,
    ) -> None:
        """Create the transaction that carries one reorganisation."""
        self.event = event
        super().__init__(
            date=event.date,
            action=ActionType.STOCK_SPLIT,
            symbol=event.symbol,
            description=description,
            quantity=event.after_quantity - event.before_quantity,
            price=Decimal(0),
            fees=Decimal(0),
            amount=Decimal(0),
            currency=currency,
            broker=event.broker,
            isin=event.isin,
            source=source,
        )
