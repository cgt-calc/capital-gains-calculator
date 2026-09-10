"""Share reorganisations: stock splits and share consolidations.

A reorganisation is not a disposal and not an acquisition. The old holding
and the new one are the same asset, held continuously, at the same allowable
cost (TCGA 1992 ss126-127, HMRC CG51805). Only the number of units changes,
so every test here checks both: that the count follows the event, and that
the pooled cost does not move.
"""

from __future__ import annotations

from dataclasses import replace
import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.exceptions import (
    CalculationError,
    InvalidTransactionError,
    QuantityNotPositiveError,
)
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    Isin,
    Position,
    RuleType,
    TransactionSource,
)
from cgt_calc.parsers.raw import RawTransaction
from cgt_calc.stock_splits import (
    RATIO_SEARCH_BOUND,
    SplitMode,
    StockSplitEvent,
    StockSplitTransaction,
    UnresolvedRatio,
    recover_ratio,
    scale_quantity,
    unscale_quantity,
)
from cgt_calc.util import round_decimal, strip_zeros

from .test_calc import create_calculator, get_report

if TYPE_CHECKING:
    from cgt_calc.main import CapitalGainsCalculator

GBP = CurrencyCode("GBP")

POOL_DAY = datetime.date(2023, 5, 2)
RENAME_DAY = datetime.date(2023, 5, 8)
EVENT_DAY = datetime.date(2023, 5, 15)
LATER_DAY = datetime.date(2023, 6, 10)


def trade(
    date: datetime.date,
    action: ActionType,
    symbol: str,
    quantity: str,
    price: str,
    *,
    source: TransactionSource | None = None,
) -> BrokerTransaction:
    """Build a trade stated to the last exported decimal place.

    The shared test helpers round quantities to six places, which is coarser
    than a broker states a fractional holding, and this whole feature is
    about the last two of the ten places they do state.
    """
    units = Decimal(quantity)
    unit_price = Decimal(price)
    value = units * unit_price
    return BrokerTransaction(
        date=date,
        action=action,
        symbol=symbol,
        description=f"{action.name} {symbol}",
        quantity=units,
        price=unit_price,
        fees=Decimal(0),
        amount=-value if action is ActionType.BUY else value,
        currency=GBP,
        broker="Testing",
        source=source,
    )


def legacy_split(
    date: datetime.date,
    symbol: str,
    delta: str,
    *,
    source: TransactionSource | None = None,
) -> BrokerTransaction:
    """Build a single row stating the net change a reorganisation made."""
    return BrokerTransaction(
        date=date,
        action=ActionType.STOCK_SPLIT,
        symbol=symbol,
        description=f"Split of {symbol}",
        quantity=Decimal(delta),
        price=Decimal(0),
        fees=Decimal(0),
        amount=Decimal(0),
        currency=GBP,
        broker="Testing",
        source=source,
    )


def paired_split(
    date: datetime.date,
    symbol: str,
    before: str,
    after: str,
    ratio: Fraction | UnresolvedRatio,
    *,
    instants: tuple[datetime.datetime, ...] = (),
    source: TransactionSource | None = None,
) -> StockSplitTransaction:
    """Build a reorganisation an export stated as two complete share counts."""
    return StockSplitTransaction(
        event=StockSplitEvent(
            date=date,
            broker="Testing",
            symbol_before=symbol,
            symbol_after=symbol,
            isin_before=None,
            isin_after=None,
            before_quantity=Decimal(before),
            after_quantity=Decimal(after),
            ratio=ratio,
            instants=instants,
        ),
        description=f"Reorganisation of {symbol}",
        currency=GBP,
        source=source,
    )


def elsewhere(index: int, *, account: str = "Other account") -> TransactionSource:
    """Return provenance for a row configured as a separate input."""
    return TransactionSource(
        parser="Testing",
        account=account,
        file=Path("other.csv"),
        row=index + 2,
        index=index,
    )


def run(transactions: list[BrokerTransaction]) -> CapitalGainsCalculator:
    """Calculate a history and hand back the calculator that did it."""
    calculator = create_calculator(tax_year=2023, balance_check=False)
    get_report(calculator, transactions)
    return calculator


# ===== the helpers the whole feature is built on =====


def test_scaling_rounds_once_at_the_end() -> None:
    """A repeating ratio is rounded once, at ten places, not step by step."""
    assert scale_quantity(Decimal(1), Fraction(1, 3)) == Decimal("0.3333333333")
    assert scale_quantity(Decimal(1), Fraction(3, 7)) == Decimal("0.4285714286")
    # Multiplying by the numerator first keeps the integer factor exact, so
    # only the final division can repeat.
    assert scale_quantity(Decimal(7), Fraction(3, 7)) == Decimal(3)
    assert unscale_quantity(Decimal(3), Fraction(3, 7)) == Decimal(7)


@pytest.mark.parametrize(
    ("before", "after", "ratio"),
    [
        ("0.0953545300", "0.0041458400", Fraction(1, 23)),
        ("1.0000000000", "0.1111111100", Fraction(1, 9)),
        ("0.2102512700", "1.2615076200", Fraction(6)),
        ("0.0638781600", "0.6387816000", Fraction(10)),
        ("4.2874920800", "3.8111040700", Fraction(8, 9)),
        ("0.0041458400", "0.0002763800", Fraction(1, 15)),
        ("2.3903907000", "2.2947750700", Fraction(24, 25)),
        # A second 1-for-15 consolidation of a much smaller holding, which
        # still singles one ratio out.
        ("0.0002763800", "0.0000184300", Fraction(1, 15)),
        ("0.0001000000", "0.0000500000", Fraction(1, 2)),
    ],
)
def test_a_rounded_pair_recovers_its_corporate_ratio(
    before: str, after: str, ratio: Fraction
) -> None:
    """Every reorganisation seen in a real export recovers exactly one ratio."""
    assert recover_ratio(Decimal(before), Decimal(after)) == ratio


def test_the_quotient_of_the_exported_counts_is_not_the_ratio() -> None:
    """The counts are rounded, so dividing them does not give the ratio.

    This is not a precision problem that more decimal places would fix: the
    division below is exact and still is not 1/9.
    """
    assert Decimal("0.1111111100") / Decimal("1.0000000000") != Fraction(1, 9)
    assert recover_ratio(Decimal("1.0000000000"), Decimal("0.1111111100")) == Fraction(
        1, 9
    )


def test_a_holding_too_small_to_single_out_a_ratio_is_unresolved() -> None:
    """A count granular to 1e-8 can leave many ratios admissible.

    A ten-thousandth of a share consolidated 1-for-15 is stated as
    0.0000066700, and the rounding is then a ten-thousandth of the holding
    itself, so dozens of ratios fit. Two of them are kept for the error, and
    the search stops there rather than building the whole set.
    """
    unresolved = recover_ratio(Decimal("0.0001000000"), Decimal("0.0000066700"))
    assert isinstance(unresolved, UnresolvedRatio)
    assert unresolved.reason == "multiple"
    assert len(unresolved.samples) == 2
    assert unresolved.bound == RATIO_SEARCH_BOUND
    assert "at least two ratios fit them" in unresolved.describe()


def test_a_pair_no_supported_ratio_maps_is_unresolved() -> None:
    """A ratio outside the supported bound is refused rather than guessed."""
    unresolved = recover_ratio(Decimal("5000.0000000000"), Decimal("1.0000000000"))
    assert isinstance(unresolved, UnresolvedRatio)
    assert unresolved.reason == "none"
    assert unresolved.samples == ()
    assert "1000 or less" in unresolved.describe()


def test_a_before_count_inside_its_own_uncertainty_does_not_divide_by_zero() -> None:
    """A count no larger than the rounding leaves the interval unbounded."""
    unresolved = recover_ratio(Decimal("0.0000000100"), Decimal("0.0000001000"))
    assert isinstance(unresolved, UnresolvedRatio)
    assert len(unresolved.samples) == 2


# ===== the reorganisation itself =====


def test_a_forward_split_multiplies_the_units_and_keeps_the_cost() -> None:
    """Twice the units, the same money, so half the cost per unit."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            legacy_split(EVENT_DAY, "FOO", "10"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_a_consolidation_reduces_the_units_and_keeps_the_cost() -> None:
    """A negative delta is a consolidation, not a purchase of minus shares.

    Every consolidation used to fail: the shares were added as a zero-cost
    acquisition, which refuses a non-positive quantity.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "100", "7"),
            legacy_split(EVENT_DAY, "FOO", "-99"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(1)
    assert calculator.portfolio["FOO"].amount == Decimal(700)


def _malformed_purchase(date: datetime.date, symbol: str) -> BrokerTransaction:
    """Build a purchase stating a negative count."""
    return BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol=symbol,
        description=f"malformed buy {symbol}",
        quantity=Decimal(-50),
        price=Decimal(20),
        fees=Decimal(0),
        amount=Decimal(-1000),
        currency=GBP,
        broker="Testing",
    )


def test_a_malformed_purchase_after_a_reorganisation_blames_its_own_row() -> None:
    """The row with the impossible count is named, not the reorganisation.

    A consolidation's own delta is allowed to be negative, which
    ``test_a_consolidation_reduces_the_units_and_keeps_the_cost`` covers. A
    purchase's is not, and the day's plan reads it before any row is
    processed.
    """
    with pytest.raises(QuantityNotPositiveError) as excinfo:
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "100", "5"),
                legacy_split(EVENT_DAY, "FOO", "100"),
                _malformed_purchase(EVENT_DAY, "FOO"),
                trade(EVENT_DAY, ActionType.SELL, "FOO", "200", "10"),
            ]
        )

    assert "malformed buy FOO" in str(excinfo.value)


def test_a_malformed_purchase_before_a_reorganisation_blames_its_own_row() -> None:
    """The same row, read where the plan works out what the event acts on."""
    with pytest.raises(QuantityNotPositiveError) as excinfo:
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "40", "5"),
                trade(EVENT_DAY, ActionType.SELL, "FOO", "10", "10"),
                _malformed_purchase(EVENT_DAY, "FOO"),
                legacy_split(EVENT_DAY, "FOO", "100"),
            ]
        )

    assert "malformed buy FOO" in str(excinfo.value)


def test_a_later_part_disposal_takes_its_share_of_the_unchanged_cost() -> None:
    """100 shares costing £700, consolidated 100-for-1, then 0.4 sold."""
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "100", "7"),
            legacy_split(EVENT_DAY, "FOO", "-99"),
            trade(LATER_DAY, ActionType.SELL, "FOO", "0.4", "1000"),
        ],
    )
    (entry,) = report.calculation_log[LATER_DAY]["sell$FOO"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == Decimal(280)
    assert calculator.portfolio["FOO"].amount == Decimal(420)


def test_a_reorganisation_is_neither_a_gain_nor_a_cash_movement() -> None:
    """Nothing is disposed of, nothing is bought and no money moves."""
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            legacy_split(EVENT_DAY, "FOO", "10"),
        ],
    )
    assert report.total_gain() == Decimal(0)
    assert report.disposal_count == 0
    (entry,) = report.calculation_log[EVENT_DAY]["split$FOO"]
    assert entry.rule_type is RuleType.STOCK_SPLIT
    assert entry.amount == Decimal(0)
    assert entry.gain == Decimal(0)
    assert entry.new_pool_cost == Decimal(100)


def test_a_reorganisation_is_not_an_acquisition_to_be_matched_against() -> None:
    """Restated units cost nothing, so no disposal may be identified with them.

    Pooled with the day's real purchases they would take a share of that
    purchase's cost, and hand the disposal a nil allowable cost of their own
    (TCGA 1992 s127, CG51805).
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            legacy_split(EVENT_DAY, "FOO", "10"),
            trade(EVENT_DAY, ActionType.SELL, "FOO", "5", "8"),
        ],
    )
    entries = report.calculation_log[EVENT_DAY]["sell$FOO"]
    assert [entry.rule_type for entry in entries] == [RuleType.SECTION_104]
    # Half of a £100 pool spread over 20 units, not free shares at nil cost.
    assert entries[0].allowable_cost == Decimal(25)
    assert "buy$FOO" not in report.calculation_log[EVENT_DAY]


def test_selling_the_whole_holding_after_a_reorganisation_leaves_nothing() -> None:
    """The pool ends at exactly zero units and exactly zero cost."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "100", "7"),
            legacy_split(EVENT_DAY, "FOO", "-99"),
            trade(LATER_DAY, ActionType.SELL, "FOO", "1", "1000"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(0)
    assert calculator.portfolio["FOO"].amount == Decimal(0)


def test_a_reorganisation_of_an_empty_holding_is_refused() -> None:
    """There is nothing to restate, and dividing by it is not the answer."""
    with pytest.raises(CalculationError, match="there is nothing to restate"):
        run([legacy_split(EVENT_DAY, "FOO", "10")])


def test_a_delta_that_wipes_out_the_holding_is_refused() -> None:
    """A reorganisation leaves a holding; it does not end one."""
    with pytest.raises(CalculationError, match="leaves 0 of a holding of 10"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                legacy_split(EVENT_DAY, "FOO", "-10"),
            ]
        )


def test_two_reorganisations_of_one_holding_on_one_day_are_refused() -> None:
    """One corporate ratio applies to the global holding once.

    Two brokers reporting the same event is not two events, and the input
    does not say which this is.
    """
    with pytest.raises(CalculationError, match="more than one reorganisation"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                legacy_split(EVENT_DAY, "FOO", "10"),
                legacy_split(EVENT_DAY, "FOO", "10"),
            ]
        )


# ===== where a single row's delta may be read as a global one =====


def test_a_raw_delta_is_a_change_to_the_whole_holding() -> None:
    """A RAW row states the change to the taxpayer's entire pooled holding."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "6", "10", source=elsewhere(0)),
            trade(POOL_DAY, ActionType.BUY, "FOO", "4", "10"),
            RawTransaction(
                [str(EVENT_DAY), "STOCK_SPLIT", "FOO", "10", "0.00", "0.00", "GBP"],
                Path("raw.csv"),
            ),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_a_brokers_own_delta_is_refused_when_the_pool_has_other_sources() -> None:
    """One account's change is not the whole holding's change.

    Adding it to a pool built from several derives a ratio that was never the
    corporate one, so the holding would be restated by the wrong factor.
    """
    with pytest.raises(CalculationError, match="also has units from"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "6", "10", source=elsewhere(0)),
                trade(POOL_DAY, ActionType.BUY, "FOO", "4", "10"),
                legacy_split(EVENT_DAY, "FOO", "10"),
            ]
        )


def test_a_brokers_own_delta_is_allowed_when_it_holds_everything() -> None:
    """Everything came from one account, so its change is the whole change."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "6", "10"),
            trade(POOL_DAY, ActionType.BUY, "FOO", "4", "10"),
            legacy_split(EVENT_DAY, "FOO", "10"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(20)


# ===== same-day rows and the units they are stated in =====


def test_a_purchase_before_a_same_day_reorganisation_is_restated() -> None:
    """The count is restated; the money it cost is not.

    A buy of 10 for £100 followed by a 2-for-1 split that day is an
    acquisition of 20 for £100, which is the same pool by another route.
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            trade(EVENT_DAY, ActionType.BUY, "FOO", "10", "10"),
            legacy_split(EVENT_DAY, "FOO", "20"),
        ],
    )
    (entry,) = report.calculation_log[EVENT_DAY]["buy$FOO"]
    assert entry.quantity == Decimal(20)
    assert entry.allowable_cost == Decimal(100)
    assert calculator.portfolio["FOO"].quantity == Decimal(40)
    assert calculator.portfolio["FOO"].amount == Decimal(200)


def test_a_purchase_after_a_same_day_reorganisation_is_left_alone() -> None:
    """It is already in the units the day ends in."""
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            legacy_split(EVENT_DAY, "FOO", "10"),
            trade(EVENT_DAY, ActionType.BUY, "FOO", "10", "5"),
        ],
    )
    (entry,) = report.calculation_log[EVENT_DAY]["buy$FOO"]
    assert entry.quantity == Decimal(10)
    assert calculator.portfolio["FOO"].quantity == Decimal(30)
    assert calculator.portfolio["FOO"].amount == Decimal(150)


def test_a_gift_before_a_same_day_reorganisation_is_restated() -> None:
    """The rule is about counts leaving as well as counts arriving.

    The market value the row states is for the units it states, so only the
    count leaving the pool follows the reorganisation.
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    given_away = BrokerTransaction(
        date=EVENT_DAY,
        action=ActionType.GIFT_UNCONNECTED,
        symbol="FOO",
        description="Gift of FOO",
        quantity=Decimal(4),
        price=Decimal(20),
        fees=Decimal(0),
        amount=None,
        currency=GBP,
        broker="Testing",
    )
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            given_away,
            legacy_split(EVENT_DAY, "FOO", "6"),
        ],
    )
    (entry,) = report.calculation_log[EVENT_DAY]["gift-unconnected$FOO"]
    # Six units were left when the holding doubled, so the four given away
    # are eight, at the £80 market value the row stated for the four.
    assert entry.quantity == Decimal(8)
    assert round_decimal(entry.amount, 2) == Decimal(80)
    assert entry.allowable_cost == Decimal(40)
    assert calculator.portfolio["FOO"].quantity == Decimal(12)
    assert calculator.portfolio["FOO"].amount == Decimal(60)


def test_a_same_day_row_from_another_input_is_refused() -> None:
    """Nothing says which side of the reorganisation it falls.

    Neither input carries times, and the order the registry merges two
    sources in is calculation plumbing rather than evidence of when a
    corporate action happened.
    """
    with pytest.raises(CalculationError, match="cannot be placed either side"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                trade(EVENT_DAY, ActionType.BUY, "FOO", "2", "10", source=elsewhere(0)),
                legacy_split(EVENT_DAY, "FOO", "10"),
            ]
        )


def test_a_row_between_the_two_halves_of_a_reorganisation_is_refused() -> None:
    """Its unit system is exactly what cannot be told."""
    opened = datetime.datetime(2023, 5, 15, 7, 44, 13, tzinfo=datetime.UTC)
    closed = datetime.datetime(2023, 5, 15, 7, 44, 14, tzinfo=datetime.UTC)
    between = TransactionSource(
        parser="Testing",
        account="Other account",
        file=Path("other.csv"),
        index=0,
        timestamp=datetime.datetime(
            2023, 5, 15, 7, 44, 13, 500000, tzinfo=datetime.UTC
        ),
    )
    with pytest.raises(CalculationError, match="cannot be placed either side"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                trade(EVENT_DAY, ActionType.BUY, "FOO", "2", "10", source=between),
                paired_split(
                    EVENT_DAY,
                    "FOO",
                    "12",
                    "24",
                    Fraction(2),
                    instants=(opened, closed),
                ),
            ]
        )


# ===== keeping the broker's own count =====


def test_the_brokers_count_survives_an_earlier_purchase_the_same_day() -> None:
    """The reconciliation delta keeps the count the broker actually reported.

    Trading 212 consolidated one AVX share 1-for-9 and reported the result as
    0.1111111100, where scaling the pool by the recovered 1/9 gives
    0.1111111111. The whole holding sits at that broker, so its count is the
    one to end the day on, and a disposal of all of it must leave zero.
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.9", "100"),
            trade(EVENT_DAY, ActionType.BUY, "FOO", "0.1", "100"),
            paired_split(
                EVENT_DAY, "FOO", "1.0000000000", "0.1111111100", Fraction(1, 9)
            ),
            trade(LATER_DAY, ActionType.SELL, "FOO", "0.11111111", "900"),
        ],
    )
    transformation = calculator.splits[EVENT_DAY]["FOO"]
    assert transformation.mode is SplitMode.BROKER_EXACT
    assert transformation.reconciliation_delta == Decimal("-1.1E-9")
    assert transformation.expected_day_close == Decimal("0.1111111100")

    (entry,) = report.calculation_log[EVENT_DAY]["split$FOO"]
    assert entry.new_quantity == Decimal("0.1111111100")
    assert entry.new_pool_cost == Decimal(100)
    assert entry.stock_split is not None
    assert entry.stock_split.scaled_day_open_quantity == Decimal("0.1000000000")
    assert entry.stock_split.reconciliation_delta == Decimal("-1.1E-9")

    assert calculator.portfolio["FOO"].quantity == Decimal(0)
    assert calculator.portfolio["FOO"].amount == Decimal(0)


def test_a_pool_the_event_does_not_account_for_is_scaled_instead() -> None:
    """One broker's rounded count must not adjust a pool built from several.

    The event states the holding at its own broker. The rest of the pool is
    somewhere else, so the ratio applies to all of it and no broker-specific
    correction is made.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "1", "100"),
            trade(POOL_DAY, ActionType.BUY, "FOO", "1", "100", source=elsewhere(0)),
            paired_split(
                EVENT_DAY, "FOO", "1.0000000000", "0.1111111100", Fraction(1, 9)
            ),
        ]
    )
    transformation = calculator.splits[EVENT_DAY]["FOO"]
    assert transformation.mode is SplitMode.RATIO_ONLY
    assert transformation.reconciliation_delta == Decimal(0)
    assert calculator.portfolio["FOO"].quantity == Decimal("0.2222222222")
    assert calculator.portfolio["FOO"].amount == Decimal(200)


# ===== a ratio that could not be recovered =====


UNRESOLVED = UnresolvedRatio(
    reason="multiple",
    samples=(Fraction(1, 150), Fraction(1, 149)),
    observed=Fraction(667, 100000),
    bound=RATIO_SEARCH_BOUND,
)


def test_an_unresolved_ratio_replaces_a_holding_it_states_in_full() -> None:
    """No division is needed when the broker states both counts.

    The holding is the one the export describes, so the after count is simply
    what it becomes. Refusing here would reject a whole export for a number
    nothing asked for.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0001", "1000"),
            paired_split(EVENT_DAY, "FOO", "0.0001000000", "0.0000066700", UNRESOLVED),
        ]
    )
    transformation = calculator.splits[EVENT_DAY]["FOO"]
    assert transformation.mode is SplitMode.DIRECT_SUBSTITUTION
    assert transformation.ratio is None
    assert "unresolved" in transformation.ratio_description
    assert calculator.portfolio["FOO"].quantity == Decimal("0.0000066700")
    assert calculator.portfolio["FOO"].amount == Decimal("0.1")


def test_an_unresolved_ratio_is_refused_when_an_earlier_row_needs_restating() -> None:
    """Replacing the day-opening pool cannot also restate the day's rows.

    Both routes need the ratio, even though the holding at the event is
    exactly the one the export states.
    """
    with pytest.raises(CalculationError, match="also changed hands earlier that day"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "0.00009", "1000"),
                trade(EVENT_DAY, ActionType.BUY, "FOO", "0.00001", "1000"),
                paired_split(
                    EVENT_DAY, "FOO", "0.0001000000", "0.0000066700", UNRESOLVED
                ),
            ]
        )


def test_an_unresolved_ratio_is_refused_when_the_holding_must_be_scaled() -> None:
    """A pool the event does not state in full has to be scaled, and cannot be."""
    with pytest.raises(CalculationError, match="has to be scaled by the corporate"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "0.0002", "1000"),
                paired_split(
                    EVENT_DAY, "FOO", "0.0001000000", "0.0000066700", UNRESOLVED
                ),
            ]
        )


def test_an_unresolved_ratio_nothing_needs_is_calculated_normally() -> None:
    """Inside a 30-day window is not the same as being needed.

    Nothing is re-acquired after this disposal, so no count crosses the
    reorganisation and no conversion is called for.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0002", "1000"),
            trade(
                EVENT_DAY - datetime.timedelta(days=1),
                ActionType.SELL,
                "FOO",
                "0.0001",
                "1000",
            ),
            paired_split(EVENT_DAY, "FOO", "0.0001000000", "0.0000066700", UNRESOLVED),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal("0.0000066700")


def test_an_unresolved_ratio_blocks_a_bed_and_breakfast_conversion() -> None:
    """The two counts are in different units and cannot be compared."""
    with pytest.raises(CalculationError, match="identified under the 30-day rule"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "0.0002", "1000"),
                trade(
                    EVENT_DAY - datetime.timedelta(days=1),
                    ActionType.SELL,
                    "FOO",
                    "0.0001",
                    "1000",
                ),
                paired_split(
                    EVENT_DAY, "FOO", "0.0001000000", "0.0000066700", UNRESOLVED
                ),
                trade(
                    EVENT_DAY + datetime.timedelta(days=1),
                    ActionType.BUY,
                    "FOO",
                    "0.000005",
                    "1000",
                ),
            ]
        )


# ===== matching across a reorganisation =====


def test_a_repurchase_after_a_split_is_matched_in_converted_units() -> None:
    """The 30-day rule identifies counts stated in two different units."""
    sale_day = datetime.date(2023, 5, 10)
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(datetime.date(2023, 4, 7), ActionType.BUY, "FOO", "12", "5"),
            trade(sale_day, ActionType.SELL, "FOO", "2", "6"),
            legacy_split(EVENT_DAY, "FOO", "10"),
            trade(datetime.date(2023, 6, 8), ActionType.BUY, "FOO", "2", "5"),
        ],
    )
    entries = report.calculation_log[sale_day]["sell$FOO"]
    # The two units repurchased are one unit in the units of the disposal, so
    # half the sale is matched to them and the rest comes from the pool.
    assert [entry.rule_type for entry in entries] == [
        RuleType.BED_AND_BREAKFAST,
        RuleType.SECTION_104,
    ]
    assert entries[0].quantity == Decimal(1)
    assert entries[0].allowable_cost == Decimal(10)


def test_a_repurchase_after_a_consolidation_is_matched_in_converted_units() -> None:
    """The conversion works the same way for a ratio below one."""
    sale_day = datetime.date(2023, 5, 10)
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(datetime.date(2023, 4, 7), ActionType.BUY, "FOO", "12", "5"),
            trade(sale_day, ActionType.SELL, "FOO", "4", "6"),
            legacy_split(EVENT_DAY, "FOO", "-4"),
            trade(datetime.date(2023, 6, 8), ActionType.BUY, "FOO", "1", "20"),
        ],
    )
    entries = report.calculation_log[sale_day]["sell$FOO"]
    # Eight units left were consolidated 2-for-1, so one unit repurchased is
    # two in the units the disposal was stated in.
    assert entries[0].rule_type is RuleType.BED_AND_BREAKFAST
    assert entries[0].quantity == Decimal(2)
    assert entries[0].allowable_cost == Decimal(20)


def test_two_reorganisations_compose_as_one_exact_fraction() -> None:
    """Composing rounded decimal multipliers is what compounds the error.

    Two 1-for-15 consolidations of the same holding compose to 1/225, which
    no product of ten-place decimals reaches exactly.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "225", "1"),
            legacy_split(EVENT_DAY, "FOO", "-210"),
            legacy_split(EVENT_DAY + datetime.timedelta(days=1), "FOO", "-14"),
        ]
    )
    first = calculator.splits[EVENT_DAY]["FOO"]
    second = calculator.splits[EVENT_DAY + datetime.timedelta(days=1)]["FOO"]
    assert first.ratio is not None
    assert second.ratio is not None
    assert first.ratio * second.ratio == Fraction(1, 225)
    assert calculator.portfolio["FOO"].quantity == Decimal(1)
    assert calculator.portfolio["FOO"].amount == Decimal(225)


def test_a_split_on_the_disposal_day_is_not_applied_twice() -> None:
    """The day's own reorganisation already restated the disposal's row."""
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(datetime.date(2023, 4, 7), ActionType.BUY, "FOO", "12", "5"),
            trade(EVENT_DAY, ActionType.SELL, "FOO", "2", "6"),
            legacy_split(EVENT_DAY, "FOO", "10"),
            trade(datetime.date(2023, 6, 8), ActionType.BUY, "FOO", "4", "3"),
        ],
    )
    entries = report.calculation_log[EVENT_DAY]["sell$FOO"]
    # The sale of two pre-split units is four after the restatement, and all
    # four are matched against the four repurchased, in the same units.
    assert entries[0].rule_type is RuleType.BED_AND_BREAKFAST
    assert entries[0].quantity == Decimal(4)
    assert entries[0].allowable_cost == Decimal(12)


# ===== what the report shows =====


def test_the_report_shows_a_reorganisation_rather_than_a_free_purchase(
    tmp_path: Path,
) -> None:
    """It is not a zero-cost buy, and must not be rendered as one."""
    from cgt_calc.render_latex import render_pdf  # noqa: PLC0415

    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.9", "100"),
            trade(EVENT_DAY, ActionType.BUY, "FOO", "0.1", "100"),
            paired_split(
                EVENT_DAY, "FOO", "1.0000000000", "0.1111111100", Fraction(1, 9)
            ),
        ],
    )
    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")

    assert "Share reorganisation" in source
    assert "ratio 1/9" in source
    assert strip_zeros(Decimal("-1.1E-9")) in source
    assert "0.1111111100"[:10] in source


def test_normalisation_leaves_the_exported_row_alone() -> None:
    """The restated count is carried alongside the row, not written over it.

    The exported quantity, price and amount are what validation,
    deduplication and diagnostics read. Restating the quantity in place would
    leave it paired with a price it no longer matches, which reads as a
    discrepancy in the export.
    """
    purchase = trade(EVENT_DAY, ActionType.BUY, "FOO", "10", "10")
    run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            purchase,
            legacy_split(EVENT_DAY, "FOO", "20"),
        ]
    )
    assert purchase.quantity == Decimal(10)
    assert purchase.price == Decimal(10)
    assert purchase.amount == Decimal(-100)
    assert purchase.calculation_quantity == Decimal(20)
    assert purchase.pool_quantity == Decimal(20)


def test_compounding_consolidations_follow_each_reported_count() -> None:
    """Two consolidations of one holding, as Trading 212 exported them.

    XXII was consolidated 1-for-23 and then 1-for-15, and the second event's
    before count is exactly the first event's after count. Each is honoured
    on its own, and the two ratios compose to 1/345 exactly: it is composing
    their rounded quotients that would drift.
    """
    second_day = EVENT_DAY + datetime.timedelta(days=30)
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0953545300", "1000"),
            paired_split(
                EVENT_DAY, "FOO", "0.0953545300", "0.0041458400", Fraction(1, 23)
            ),
            paired_split(
                second_day, "FOO", "0.0041458400", "0.0002763800", Fraction(1, 15)
            ),
        ]
    )
    first = calculator.splits[EVENT_DAY]["FOO"]
    second = calculator.splits[second_day]["FOO"]
    assert first.ratio is not None
    assert second.ratio is not None
    assert first.ratio * second.ratio == Fraction(1, 345)
    # Scaling alone would have left 0.0041458491 and then 0.0002763899.
    assert first.expected_day_close == Decimal("0.0041458400")
    assert calculator.portfolio["FOO"].quantity == Decimal("0.0002763800")


def test_a_partly_matched_repurchase_keeps_both_unit_systems_straight() -> None:
    """The disposal takes what it can; the rest of the acquisition stays.

    Everything reserved is counted in the acquisition's own units and the
    whole remainder is converted once, so neither side is over-consumed and
    neither remainder goes negative.
    """
    sale_day = datetime.date(2023, 5, 10)
    buy_back_day = datetime.date(2023, 6, 8)
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(datetime.date(2023, 4, 7), ActionType.BUY, "FOO", "700", "1"),
            trade(sale_day, ActionType.SELL, "FOO", "350", "2"),
            # 350 units left become 150: three new shares for every seven old.
            legacy_split(EVENT_DAY, "FOO", "-200"),
            trade(buy_back_day, ActionType.BUY, "FOO", "200", "5"),
        ],
    )
    assert calculator.splits[EVENT_DAY]["FOO"].ratio == Fraction(3, 7)
    entries = report.calculation_log[sale_day]["sell$FOO"]
    # All 350 disposed units are matched, and they take 150 of the 200 units
    # repurchased: 350 old units are 150 new ones.
    assert [entry.rule_type for entry in entries] == [RuleType.BED_AND_BREAKFAST]
    assert entries[0].quantity == Decimal(350)
    assert entries[0].allowable_cost == Decimal(750)
    matched, pooled = report.calculation_log[buy_back_day]["buy$FOO"]
    assert matched.rule_type is RuleType.BED_AND_BREAKFAST
    assert matched.quantity == Decimal(150)
    # The 50 units the match did not reach are pooled at what they cost.
    assert pooled.rule_type is RuleType.SECTION_104
    assert pooled.quantity == Decimal(50)
    assert calculator.portfolio["FOO"].quantity == Decimal(350)
    assert calculator.portfolio["FOO"].amount == Decimal(950)


def test_a_fully_consumed_repurchase_leaves_no_residual_share() -> None:
    """A ratio that repeats must not leave a sliver behind or take one twice."""
    sale_day = datetime.date(2023, 5, 10)
    buy_back_day = datetime.date(2023, 6, 8)
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(datetime.date(2023, 4, 7), ActionType.BUY, "FOO", "600", "1"),
            trade(sale_day, ActionType.SELL, "FOO", "300", "2"),
            # Three old units for every one new.
            legacy_split(EVENT_DAY, "FOO", "-200"),
            trade(buy_back_day, ActionType.BUY, "FOO", "10", "3"),
        ],
    )
    assert calculator.splits[EVENT_DAY]["FOO"].ratio == Fraction(1, 3)
    entries = report.calculation_log[sale_day]["sell$FOO"]
    assert [entry.rule_type for entry in entries] == [
        RuleType.BED_AND_BREAKFAST,
        RuleType.SECTION_104,
    ]
    # Ten units repurchased are thirty in the units the sale was stated in,
    # and every one of the ten is consumed.
    assert entries[0].quantity == Decimal(30)
    assert entries[0].allowable_cost == Decimal(30)
    (matched,) = report.calculation_log[buy_back_day]["buy$FOO"]
    assert matched.rule_type is RuleType.BED_AND_BREAKFAST
    assert matched.quantity == Decimal(10)
    assert calculator.portfolio["FOO"].quantity == Decimal(110)


def test_an_acquisition_before_an_unresolved_split_needs_no_ratio() -> None:
    """The match is settled in one unit system, so nothing crosses the event."""
    sale_day = EVENT_DAY - datetime.timedelta(days=10)
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0002", "1000"),
            trade(sale_day, ActionType.SELL, "FOO", "0.0001", "1000"),
            trade(
                sale_day + datetime.timedelta(days=1),
                ActionType.BUY,
                "FOO",
                "0.0001",
                "900",
            ),
            paired_split(EVENT_DAY, "FOO", "0.0002000000", "0.0000133400", UNRESOLVED),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal("0.0000133400")


def test_a_disposal_already_matched_does_not_reach_an_unresolved_split() -> None:
    """A later acquisition is only converted for a disposal still unmatched."""
    sale_day = EVENT_DAY - datetime.timedelta(days=10)
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0002", "1000"),
            trade(sale_day, ActionType.SELL, "FOO", "0.0001", "1000"),
            # Matches the whole disposal before the reorganisation is reached.
            trade(
                sale_day + datetime.timedelta(days=1),
                ActionType.BUY,
                "FOO",
                "0.0001",
                "900",
            ),
            paired_split(EVENT_DAY, "FOO", "0.0002000000", "0.0000133400", UNRESOLVED),
            trade(
                EVENT_DAY + datetime.timedelta(days=1),
                ActionType.BUY,
                "FOO",
                "0.0000066700",
                "1000",
            ),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal("0.0000200100")


def test_a_substituted_holding_says_so_in_the_report(tmp_path: Path) -> None:
    """A reader has to be able to see that no ratio was used."""
    from cgt_calc.render_latex import render_pdf  # noqa: PLC0415

    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0001", "1000"),
            paired_split(EVENT_DAY, "FOO", "0.0001000000", "0.0000066700", UNRESOLVED),
        ],
    )
    (entry,) = report.calculation_log[EVENT_DAY]["split$FOO"]
    assert entry.stock_split is not None
    assert entry.stock_split.ratio_description == (
        "unresolved — broker 0.0001 -> 0.00000667 used directly"
    )
    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")
    assert "unresolved — broker 0.0001 -> 0.00000667 used directly" in source


def _event(**overrides: object) -> StockSplitEvent:
    fields: dict[str, object] = {
        "date": EVENT_DAY,
        "broker": "Testing",
        "symbol_before": "FOO",
        "symbol_after": "FOO",
        "isin_before": None,
        "isin_after": None,
        "before_quantity": Decimal(10),
        "after_quantity": Decimal(20),
        "ratio": Fraction(2),
    }
    fields.update(overrides)
    return StockSplitEvent(**fields)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_an_event_cannot_change_the_ticker() -> None:
    """Combining two securities needs relationship data no input carries."""
    with pytest.raises(ValueError, match="cannot change the ticker"):
        _event(symbol_after="BAR")


def test_an_event_cannot_change_the_identifier() -> None:
    """The same, where only the ISIN moves."""
    with pytest.raises(ValueError, match="cannot change the ISIN"):
        _event(isin_before=Isin("US0000000002"), isin_after=Isin("US0000000010"))


def test_an_event_keeps_whichever_identifier_it_has() -> None:
    """One populated identifier and one blank is the same security."""
    assert _event(isin_after=Isin("US0000000002")).isin == Isin("US0000000002")
    assert _event().isin is None


def broker_source(index: int) -> TransactionSource:
    """Provenance for a broker export, whose same-date order is its own.

    Several parsers reorder their rows for the calculation's sake, so the
    order one was read in is not evidence of when it happened.
    """
    return TransactionSource(
        parser="Testing",
        account="Testing account",
        file=Path("testing.csv"),
        row=index + 2,
        index=index,
        rows_in_time_order=False,
    )


def test_a_holding_first_acquired_on_the_split_day_is_restated() -> None:
    """The day-opening pool may be nothing and the event still applies.

    What has to survive the reorganisation is the holding at the event, not
    the holding at the start of the day.
    """
    calculator = run(
        [
            trade(EVENT_DAY, ActionType.BUY, "FOO", "10", "10"),
            legacy_split(EVENT_DAY, "FOO", "10"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_the_brokers_count_survives_a_holding_opened_the_same_day() -> None:
    """Nothing held at the open, bought, then consolidated, still lands exact."""
    calculator = run(
        [
            trade(EVENT_DAY, ActionType.BUY, "FOO", "1", "100"),
            paired_split(
                EVENT_DAY, "FOO", "1.0000000000", "0.1111111100", Fraction(1, 9)
            ),
            trade(LATER_DAY, ActionType.SELL, "FOO", "0.11111111", "900"),
        ]
    )
    transformation = calculator.splits[EVENT_DAY]["FOO"]
    assert transformation.mode is SplitMode.BROKER_EXACT
    assert transformation.day_open_quantity == Decimal(0)
    assert transformation.reconciliation_delta == Decimal("-1.1E-9")
    assert transformation.expected_day_close == Decimal("0.1111111100")
    assert calculator.portfolio["FOO"].quantity == Decimal(0)
    assert calculator.portfolio["FOO"].amount == Decimal(0)


def test_a_reorganisation_that_leaves_nothing_is_still_refused() -> None:
    """The holding at the event has to survive it."""
    with pytest.raises(CalculationError, match="leaves 0 of a holding of 10"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                legacy_split(EVENT_DAY, "FOO", "-10"),
            ]
        )


def test_a_legacy_delta_that_is_not_a_number_is_refused() -> None:
    """Refuse a decimal that is not a count of shares.

    "Infinity" parses, but left alone it reaches Fraction() and raises
    OverflowError, with nothing to say which row caused it.

    """
    with pytest.raises(CalculationError, match="which is not a number of shares"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                legacy_split(EVENT_DAY, "FOO", "Infinity"),
            ]
        )


def test_a_match_too_small_to_express_in_both_unit_systems_is_refused() -> None:
    """A large ratio can leave a real quantity with nothing on the other side.

    Ten decimal places is what both counts are kept to, so a thousand-fold
    split turns a hundred-millionth of a share into nothing at all. Dividing
    by that is a crash, and matching against it is a disposal identified at
    nil cost.
    """
    sale_day = POOL_DAY + datetime.timedelta(days=1)
    with pytest.raises(CalculationError, match="less than the ten decimal places"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                trade(sale_day, ActionType.SELL, "FOO", "5", "12"),
                paired_split(
                    EVENT_DAY, "FOO", "5.0000000000", "5000.0000000000", Fraction(1000)
                ),
                trade(
                    EVENT_DAY + datetime.timedelta(days=1),
                    ActionType.BUY,
                    "FOO",
                    "0.00000001",
                    "1",
                ),
            ]
        )


def test_a_disposal_too_small_to_reserve_any_acquisition_is_refused() -> None:
    """The mirror case: the disposal converts to nothing of the acquisition.

    Left alone this matched the disposal at nil allowable cost and reserved
    no units, leaving the whole acquisition free to be matched again.
    """
    sale_day = POOL_DAY + datetime.timedelta(days=1)
    with pytest.raises(CalculationError, match="less than the ten decimal places"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10000", "1"),
                trade(sale_day, ActionType.SELL, "FOO", "0.00000004", "1"),
                paired_split(
                    EVENT_DAY,
                    "FOO",
                    "9999.99999996",
                    "9.9999999999",
                    Fraction(1, 1000),
                ),
                trade(
                    EVENT_DAY + datetime.timedelta(days=1),
                    ActionType.BUY,
                    "FOO",
                    "1",
                    "1",
                ),
            ]
        )


def test_a_broker_exports_own_row_order_does_not_decide_the_units() -> None:
    """Only a format that documents its same-date order may be read that way.

    A RAW history states that its rows for one date are in the order they
    happened. A broker export states no such thing, and several parsers
    reorder rows for the calculation's sake, so the order one was read in
    cannot settle which side of a reorganisation it falls.
    """
    with pytest.raises(CalculationError, match="cannot be placed either side"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                trade(
                    EVENT_DAY,
                    ActionType.BUY,
                    "FOO",
                    "2",
                    "10",
                    source=broker_source(0),
                ),
                legacy_split(EVENT_DAY, "FOO", "10", source=broker_source(1)),
            ]
        )


def test_a_positive_reconciliation_is_in_before_the_day_sells_down() -> None:
    """A decrease is checked against what the day holds, not a running count.

    Restating the opening pool and the day's earlier rows separately rounds
    each of them, and on counts this small the difference is the whole
    holding: six ten-billionths open, four ten-billionths leave, and the
    reconciliation puts back the two ten-billionths that rounding took. Held
    back until the reorganisation's own row, the count drains to nothing and
    the day's last disposal is refused for shares the history has.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0000000006", "1000000"),
            *[
                trade(EVENT_DAY, ActionType.SELL, "FOO", "0.0000000001", "1000000")
                for _ in range(4)
            ],
            paired_split(
                EVENT_DAY, "FOO", "0.0000000002", "0.0000000001", Fraction(1, 2)
            ),
        ]
    )
    transformation = calculator.splits[EVENT_DAY]["FOO"]
    assert transformation.scaled_day_open_quantity == Decimal("3E-10")
    assert transformation.reconciliation_delta == Decimal("2E-10")
    assert transformation.expected_day_close == Decimal("1E-10")
    assert calculator.portfolio["FOO"].quantity == Decimal("1E-10")


def test_a_negative_reconciliation_does_not_hold_the_day_below_zero() -> None:
    """The mirror case, and why no single point in the scan can serve.

    Here the reconciliation takes two ten-billionths away rather than adding
    them, and the day's sale comes before its purchases. Applied with the
    opening it leaves the count below nothing until those purchases land, and
    the sale is refused; applied at the reorganisation's own row it is the
    previous case that breaks. The day's ledger is what both are checked
    against.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.0000000001", "1000000"),
            trade(EVENT_DAY, ActionType.SELL, "FOO", "0.0000000001", "1000000"),
            *[
                trade(EVENT_DAY, ActionType.BUY, "FOO", "0.0000000001", "1000000")
                for _ in range(4)
            ],
            paired_split(
                EVENT_DAY, "FOO", "0.0000000004", "0.0000000002", Fraction(1, 2)
            ),
        ]
    )
    transformation = calculator.splits[EVENT_DAY]["FOO"]
    assert transformation.scaled_day_open_quantity == Decimal("1E-10")
    assert transformation.reconciliation_delta == Decimal("-2E-10")
    assert transformation.expected_day_close == Decimal("2E-10")
    assert calculator.portfolio["FOO"].quantity == Decimal("2E-10")


# ===== which accounts a holding's units came from =====


def raw_split(
    date: datetime.date,
    symbol: str,
    delta: str,
    *,
    price: str = "0.00",
    fees: str = "0.00",
) -> RawTransaction:
    """Build a hand-written RAW row stating the whole holding's change."""
    return RawTransaction(
        [str(date), "STOCK_SPLIT", symbol, delta, price, fees, "GBP"],
        Path("raw.csv"),
    )


def rename(date: datetime.date, old: str, new: str) -> BrokerTransaction:
    """Build the row that says a holding now trades under another ticker."""
    return BrokerTransaction(
        date=date,
        action=ActionType.RENAME,
        symbol=new,
        description=f"{RENAME_DESCRIPTION_PREFIX}{old}",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=None,
        currency=GBP,
        broker="Testing",
    )


def test_a_hand_written_transfer_out_does_not_make_a_holding_multi_source() -> None:
    """Shares leaving are not shares arriving.

    A transfer to a spouse is written by hand in a RAW file while the shares
    it moves were bought through a broker export, which is what the docs tell
    the user to do. The RAW row supplies no units, so the holding is still
    the broker's alone and its own split row still states the whole change.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10", source=elsewhere(0)),
            trade(
                RENAME_DAY,
                ActionType.TRANSFER_TO_SPOUSE,
                "FOO",
                "2",
                "0",
                source=elsewhere(0, account="Hand-written RAW file"),
            ),
            legacy_split(EVENT_DAY, "FOO", "8", source=elsewhere(1)),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(16)
    assert calculator.portfolio["FOO"].amount == Decimal(80)


def test_an_account_sold_out_of_is_no_longer_a_source() -> None:
    """A holding rebuilt somewhere else is that account's holding alone.

    The ticker was held at one broker and sold in full a year before it was
    bought at another. None of the first account's units are in the pool the
    reorganisation restates, so its own row states the whole change.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10", source=elsewhere(0)),
            trade(RENAME_DAY, ActionType.SELL, "FOO", "10", "12", source=elsewhere(1)),
            trade(LATER_DAY, ActionType.BUY, "FOO", "4", "20", source=elsewhere(0)),
            legacy_split(
                LATER_DAY + datetime.timedelta(days=1),
                "FOO",
                "4",
                source=elsewhere(1),
            ),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(8)
    assert calculator.portfolio["FOO"].amount == Decimal(80)


def test_a_running_count_touching_zero_within_a_day_forgets_nothing() -> None:
    """A same-day sell processed ahead of a buy is not the holding emptying.

    Trading 212 sorts equal-instant sells ahead of buys, so account B's sale
    of ten arrives before its purchase of fifteen and the running count
    touches zero while account A's ten units are still in the pool all day.
    Forgetting A there let B's later single-row split, its own five shares
    doubling, restate the whole fifteen by a 4/3 ratio that was never the
    corporate one.
    """
    with pytest.raises(CalculationError, match="also has units from"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10", source=elsewhere(0)),
                trade(EVENT_DAY, ActionType.SELL, "FOO", "10", "12"),
                trade(EVENT_DAY, ActionType.BUY, "FOO", "15", "12"),
                legacy_split(LATER_DAY, "FOO", "5"),
            ]
        )


def test_a_holding_that_closes_a_day_empty_clears_its_sources() -> None:
    """Emptied at the day's end, not merely mid-day, the accounts are gone.

    The mirror of the test above: the same sale and purchase a day apart
    really do empty the holding overnight, so the pool the purchase rebuilds
    is the buying account's alone and its own split row states the change.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10", source=elsewhere(0)),
            trade(EVENT_DAY, ActionType.SELL, "FOO", "10", "12"),
            trade(
                EVENT_DAY + datetime.timedelta(days=1),
                ActionType.BUY,
                "FOO",
                "15",
                "12",
            ),
            legacy_split(LATER_DAY, "FOO", "15"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(30)


def test_a_rename_is_not_a_second_reading_of_the_running_count() -> None:
    """A rename must not decide emptiness the day boundary was left to decide.

    The first test of the three, routed through a rename: the sale processed
    ahead of the purchase takes the count to zero, and the rename meets it
    there. Handing the old name's accounts on or dropping them is the same
    question the sweep answers a day later, and answering it here from a
    count that is not chronology lets the buying account's own split row
    restate a pool that may still hold the other account's ten.
    """
    with pytest.raises(CalculationError, match="also has units from"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10", source=elsewhere(0)),
                trade(RENAME_DAY, ActionType.SELL, "OLD", "10", "12"),
                rename(RENAME_DAY, "OLD", "NEW"),
                trade(RENAME_DAY, ActionType.BUY, "NEW", "15", "12"),
                legacy_split(LATER_DAY, "NEW", "5"),
            ]
        )


def test_a_sold_out_holding_renamed_the_same_day_frees_its_sources() -> None:
    """A rename of an emptied holding must not keep its old accounts alive.

    Renaming a sold-out holding creates the new name as a zero-quantity
    entry, and a sweep that only forgets names absent from the portfolio
    left the selling account recorded against a name that held nothing.
    The pool later rebuilt there is the buying account's alone, so its own
    split row states the whole change.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10", source=elsewhere(0)),
            trade(RENAME_DAY, ActionType.SELL, "OLD", "10", "12", source=elsewhere(1)),
            rename(RENAME_DAY, "OLD", "NEW"),
            trade(EVENT_DAY, ActionType.BUY, "NEW", "4", "20"),
            legacy_split(LATER_DAY, "NEW", "4"),
        ]
    )
    assert calculator.portfolio["NEW"].quantity == Decimal(8)
    # The sale and the rename share a day, so the repurchase a week later is
    # a repurchase of the same security: the 30-day rule identifies four of
    # the ten shares sold against it, and those four keep the £10 a share
    # they cost in the pool they left rather than the £20 paid for them.
    assert calculator.portfolio["NEW"].amount == Decimal(40)


def test_a_rename_that_moves_no_units_still_carries_its_accounts() -> None:
    """A rename onto a live holding cannot rule the accounts out either.

    Here the old name really was sold out before the rename. Nothing at the
    rename row establishes that: the count it would read is the one the day
    boundary exists to distrust, and the same reading on a day whose rows
    are not in the order they happened drops accounts whose units are still
    pooled. A destination holding units of its own never closes a day empty,
    so the sweep cannot revisit the question later, and the conservative
    answer is the only one the rename row can safely give. The split one
    account reports is refused, and the error names the RAW row that states
    the change to the whole holding.
    """
    with pytest.raises(CalculationError, match="also has units from"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10", source=elsewhere(0)),
                trade(POOL_DAY, ActionType.BUY, "NEW", "4", "20"),
                trade(
                    RENAME_DAY,
                    ActionType.SELL,
                    "OLD",
                    "10",
                    "12",
                    source=elsewhere(1),
                ),
                rename(RENAME_DAY, "OLD", "NEW"),
                legacy_split(LATER_DAY, "NEW", "4"),
            ]
        )


def test_a_rename_carries_the_accounts_over_with_the_units() -> None:
    """Only the name changes, so the units keep the accounts that put them there.

    The renamed holding and one already under the new name make a pool built
    from two accounts, and one account's own row is not its change.
    """
    with pytest.raises(CalculationError, match="also has units from"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "6", "10", source=elsewhere(0)),
                trade(POOL_DAY, ActionType.BUY, "NEW", "4", "10"),
                rename(RENAME_DAY, "OLD", "NEW"),
                legacy_split(EVENT_DAY, "NEW", "10"),
            ]
        )


# ===== more than one row for one event =====


def test_a_raw_row_settles_a_day_a_broker_also_reported() -> None:
    """The RAW row states the whole holding's change, so it is the one used.

    A broker's own row is refused against a pool built from two accounts, and
    a RAW row is what the refusal asks for. Needing the broker's row deleted
    from its own export as well would leave that remedy unusable.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "6", "10", source=elsewhere(0)),
            trade(POOL_DAY, ActionType.BUY, "FOO", "4", "10"),
            legacy_split(EVENT_DAY, "FOO", "6", source=elsewhere(1)),
            raw_split(EVENT_DAY, "FOO", "10"),
        ]
    )
    # The RAW row's whole-holding change, not the broker's own six units.
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


@pytest.mark.parametrize("broker_change", ["-6", "0", "11", "NaN"])
def test_a_raw_row_does_not_override_conflicting_broker_rows(
    broker_change: str,
) -> None:
    """An opposite or larger change cannot be one account's part of the event."""
    with pytest.raises(CalculationError, match="cannot be part of its change"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                legacy_split(EVENT_DAY, "FOO", broker_change),
                raw_split(EVENT_DAY, "FOO", "10"),
            ]
        )


def test_a_raw_row_does_not_collapse_untimed_rows_from_one_source() -> None:
    """Equal net change cannot erase two unproved events around a disposal."""
    first = legacy_split(EVENT_DAY, "FOO", "10", source=elsewhere(0))
    second = legacy_split(EVENT_DAY, "FOO", "15", source=elsewhere(1))

    with pytest.raises(CalculationError, match="same source reports"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "25"),
                first,
                trade(EVENT_DAY, ActionType.SELL, "FOO", "5", "20"),
                second,
            ]
        )


def test_different_sources_may_timestamp_one_reorganisation_differently() -> None:
    """Independent exports need not record one corporate action simultaneously."""
    morning = datetime.datetime.combine(EVENT_DAY, datetime.time(9))
    afternoon = datetime.datetime.combine(EVENT_DAY, datetime.time(11))
    first_source = replace(elsewhere(0, account="First account"), timestamp=morning)
    second_source = replace(elsewhere(0, account="Second account"), timestamp=afternoon)

    calculator = run(
        [
            trade(
                POOL_DAY,
                ActionType.BUY,
                "FOO",
                "5",
                "10",
                source=first_source,
            ),
            trade(
                POOL_DAY,
                ActionType.BUY,
                "FOO",
                "5",
                "10",
                source=second_source,
            ),
            legacy_split(EVENT_DAY, "FOO", "5", source=first_source),
            legacy_split(EVENT_DAY, "FOO", "5", source=second_source),
            raw_split(EVENT_DAY, "FOO", "10"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_a_raw_row_does_not_collapse_separately_timed_reorganisations() -> None:
    """One export's distinct instants prove that it reports separate events."""
    morning = datetime.datetime.combine(EVENT_DAY, datetime.time(9))
    afternoon = datetime.datetime.combine(EVENT_DAY, datetime.time(11))
    first = legacy_split(EVENT_DAY, "FOO", "10", source=elsewhere(0))
    second = legacy_split(EVENT_DAY, "FOO", "15", source=elsewhere(1))
    assert first.source is not None
    assert second.source is not None
    first.source = replace(first.source, timestamp=morning)
    second.source = replace(second.source, timestamp=afternoon)

    with pytest.raises(CalculationError, match="same source reports") as error:
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "25"),
                first,
                trade(EVENT_DAY, ActionType.SELL, "FOO", "5", "20"),
                second,
            ]
        )
    assert "Leave the day's STOCK_SPLIT rows out and work this day out by hand" in str(
        error.value
    )


def test_one_accounts_events_conflict_even_from_different_files() -> None:
    """One account exported as several files is still one source.

    A directory of date-range exports is how a Trading 212 account is
    supplied, so which CSV an event landed in says nothing about how many
    events there were. Keying the conflict check on the file put each event
    in a bucket of its own, found no conflict, and let one RAW row stand for
    both -- restating the day's disposal into the wrong units while leaving
    the closing share count right, so nothing else noticed.
    """
    account = "One account"
    morning = datetime.datetime.combine(EVENT_DAY, datetime.time(9))
    afternoon = datetime.datetime.combine(EVENT_DAY, datetime.time(11))
    first = legacy_split(EVENT_DAY, "FOO", "10", source=elsewhere(0, account=account))
    second = legacy_split(EVENT_DAY, "FOO", "15", source=elsewhere(1, account=account))
    assert first.source is not None
    assert second.source is not None
    # The only difference from the same-file case: two exports of one account.
    first.source = replace(first.source, timestamp=morning, file=Path("a-morning.csv"))
    second.source = replace(
        second.source, timestamp=afternoon, file=Path("b-afternoon.csv")
    )

    with pytest.raises(CalculationError, match="same source reports") as error:
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "25"),
                first,
                trade(EVENT_DAY, ActionType.SELL, "FOO", "5", "20"),
                second,
            ]
        )
    assert "Leave the day's STOCK_SPLIT rows out and work this day out by hand" in str(
        error.value
    )


def test_two_accounts_in_different_files_still_do_not_conflict() -> None:
    """Separately configured inputs remain independent after that fix.

    Two accounts timing one corporate action differently is the case the RAW
    override exists for, and it must survive keying the check on the account.
    """
    morning = datetime.datetime.combine(EVENT_DAY, datetime.time(9))
    afternoon = datetime.datetime.combine(EVENT_DAY, datetime.time(11))
    first = legacy_split(EVENT_DAY, "FOO", "5", source=elsewhere(0, account="First"))
    second = legacy_split(EVENT_DAY, "FOO", "5", source=elsewhere(0, account="Second"))
    assert first.source is not None
    assert second.source is not None
    first.source = replace(first.source, timestamp=morning, file=Path("first.csv"))
    second.source = replace(second.source, timestamp=afternoon, file=Path("second.csv"))

    calculator = run(
        [
            trade(
                POOL_DAY,
                ActionType.BUY,
                "FOO",
                "5",
                "10",
                source=elsewhere(0, account="First"),
            ),
            trade(
                POOL_DAY,
                ActionType.BUY,
                "FOO",
                "5",
                "10",
                source=elsewhere(0, account="Second"),
            ),
            first,
            second,
            raw_split(EVENT_DAY, "FOO", "10"),
        ]
    )
    assert calculator.portfolio["FOO"].quantity == Decimal(20)


def test_an_invalid_raw_override_keeps_its_named_quantity_error() -> None:
    """Override validation must not turn a bad RAW delta into a Decimal error."""
    with pytest.raises(ValueError, match="Invalid decimal in column 'quantity'"):
        raw_split(EVENT_DAY, "FOO", "NaN")


def test_two_raw_rows_for_one_day_are_still_refused() -> None:
    """Two rows that each claim the whole holding cannot both be right.

    Adding another RAW row is the remedy when brokers disagree, so it cannot
    also be the remedy here: the one to give is to take a RAW row away.
    """
    with pytest.raises(
        CalculationError, match="Leave all but one of the RAW STOCK_SPLIT rows out"
    ) as excinfo:
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "10"),
                raw_split(EVENT_DAY, "FOO", "20"),
            ]
        )

    # A row renders over two lines, so both rows are listed at one depth and
    # each row's own second line one deeper. Indenting only the first line
    # would show two rows as four items.
    message = str(excinfo.value)
    assert message.count(f"\n  {EVENT_DAY.isoformat()} Stock split") == 2
    assert message.count("\n    ") == 2


# ===== a reorganisation row that also states money =====


def test_a_reorganisation_that_states_a_price_is_refused() -> None:
    """Nothing is bought, so a price on the row is a figure with no meaning."""
    with pytest.raises(InvalidTransactionError, match="The price column"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "10", price="5.00"),
            ]
        )


def test_a_reorganisation_that_states_a_fee_is_refused() -> None:
    """A fee on the row is money the calculation would otherwise drop."""
    with pytest.raises(InvalidTransactionError, match="The fees column"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "10", fees="1.50"),
            ]
        )


def test_a_reorganisation_fee_error_does_not_prescribe_cash_treatment() -> None:
    """Cash in lieu has two treatments and cgt-calc computes neither."""
    with pytest.raises(
        InvalidTransactionError,
        match="cgt-calc can represent neither",
    ):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                raw_split(EVENT_DAY, "FOO", "10", fees="1.50"),
            ]
        )


def test_a_reorganisation_that_states_an_amount_is_refused() -> None:
    """Money in the amount column alone must not vanish.

    A RAW row derives its amount from the price, so a price-less amount can
    only arrive from a broker export, the way a Schwab row carries an Amount
    with no Price. It is still money the calculation would otherwise drop.
    """
    broker_row = legacy_split(EVENT_DAY, "FOO", "10")
    broker_row.amount = Decimal("123.45")
    with pytest.raises(InvalidTransactionError, match="The amount column"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                broker_row,
            ]
        )


def test_an_amount_on_an_overridden_broker_row_is_refused() -> None:
    """Choosing a RAW row must not silently discard money on another row."""
    broker_row = legacy_split(EVENT_DAY, "FOO", "10")
    broker_row.amount = Decimal("123.45")
    with pytest.raises(InvalidTransactionError, match="The amount column"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                broker_row,
                raw_split(EVENT_DAY, "FOO", "10"),
            ]
        )


def test_a_fee_on_an_overridden_broker_row_is_refused() -> None:
    """Choosing a RAW row must not silently discard money on another row."""
    broker_row = legacy_split(EVENT_DAY, "FOO", "10")
    broker_row.fees = Decimal("1.50")
    with pytest.raises(InvalidTransactionError, match="The fees column"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                broker_row,
                raw_split(EVENT_DAY, "FOO", "10"),
            ]
        )


def test_a_foreign_fee_on_a_reorganisation_is_refused() -> None:
    """Foreign fees are still money even before currency conversion runs."""
    broker_row = legacy_split(EVENT_DAY, "FOO", "10")
    broker_row.foreign_fees[CurrencyCode("USD")] = Decimal("1.50")
    with pytest.raises(InvalidTransactionError, match="The fees column"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                broker_row,
            ]
        )


# ===== a rename on the day of a reorganisation =====


def test_a_rename_into_a_reorganised_holding_is_refused() -> None:
    """The renamed shares may or may not have been part of the event.

    Restating one holding and then taking another into it the same day leaves
    units the reorganisation never touched, and nothing in the input says
    whether the rename came before it or after.
    """
    with pytest.raises(
        CalculationError, match="pool it with OLD, which holds shares of its own"
    ):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
                trade(EVENT_DAY, ActionType.BUY, "NEW", "4", "10"),
                legacy_split(EVENT_DAY, "NEW", "4"),
                rename(EVENT_DAY, "OLD", "NEW"),
            ]
        )


def test_a_reorganisation_of_a_holding_renamed_into_another_is_refused() -> None:
    """The same clash from the other end, and the same answer.

    Here the reorganised holding is the one the rename moves. Restating it and
    then pooling it with a holding already under the new name leaves that one
    unrestated, which is the same shares counted in two unit systems.
    """
    with pytest.raises(
        CalculationError, match="pool it with NEW, which holds shares of its own"
    ):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
                trade(POOL_DAY, ActionType.BUY, "NEW", "4", "10"),
                legacy_split(EVENT_DAY, "OLD", "10"),
                rename(EVENT_DAY, "OLD", "NEW"),
            ]
        )


def test_an_ordinary_ticker_change_on_the_day_of_a_reorganisation_is_fine() -> None:
    """Nothing is pooled with anything, so there is nothing to be unsure of.

    The holding is restated under the old name and the rename carries all of
    it to a name that held nothing, which is what a ticker change is.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
            legacy_split(EVENT_DAY, "OLD", "10"),
            rename(EVENT_DAY, "OLD", "NEW"),
        ]
    )
    assert "OLD" not in calculator.portfolio
    assert calculator.portfolio["NEW"].quantity == Decimal(20)
    assert calculator.portfolio["NEW"].amount == Decimal(100)


def test_a_rename_of_a_holding_that_is_gone_does_not_block_a_reorganisation() -> None:
    """A rename brings nothing in, so the reorganisation restates everything.

    The old ticker was sold out of before the rename, so nothing joins the
    reorganised holding and there is nothing the event failed to restate.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
            trade(RENAME_DAY, ActionType.SELL, "OLD", "10", "12"),
            trade(EVENT_DAY, ActionType.BUY, "NEW", "4", "10"),
            legacy_split(EVENT_DAY, "NEW", "4"),
            rename(EVENT_DAY, "OLD", "NEW"),
        ]
    )
    assert calculator.portfolio["NEW"].quantity == Decimal(8)
    assert calculator.portfolio["NEW"].amount == Decimal(40)


def test_a_reorganisation_of_a_ticker_renamed_from_an_empty_one_is_fine() -> None:
    """The mirror of the case above: the name it moves to holds nothing."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "NEW", "10", "10"),
            trade(POOL_DAY, ActionType.BUY, "OLD", "4", "10"),
            trade(RENAME_DAY, ActionType.SELL, "NEW", "10", "12"),
            legacy_split(EVENT_DAY, "OLD", "4"),
            rename(EVENT_DAY, "OLD", "NEW"),
        ]
    )
    assert calculator.portfolio["NEW"].quantity == Decimal(8)
    assert calculator.portfolio["NEW"].amount == Decimal(40)


def test_a_rename_chain_on_the_day_of_a_reorganisation_is_refused() -> None:
    """Renamed onward the same day, so where it ends up is the row order.

    The renames are applied in the order the input lists them, and a date
    carries no order, so a holding renamed twice on one day has no settled
    destination and nothing to pool it with can be established either. Listing
    the two rows the other way round moves it somewhere else, so neither
    reading is refused in favour of the other.
    """
    with pytest.raises(CalculationError, match="is renamed to BBB, and BBB is renamed"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "AAA", "10", "10"),
                legacy_split(EVENT_DAY, "AAA", "10"),
                rename(EVENT_DAY, "AAA", "BBB"),
                rename(EVENT_DAY, "BBB", "CCC"),
            ]
        )


def test_a_rename_chain_listed_backwards_is_refused_the_same_way() -> None:
    """The order the rows happen to be in does not change the answer.

    Listed this way the pool stops at the middle name instead of reaching the
    last one. That it computes at all is the row order talking, so it is the
    same refusal rather than a different result.
    """
    with pytest.raises(CalculationError, match="is renamed to BBB, and BBB is renamed"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "AAA", "10", "10"),
                legacy_split(EVENT_DAY, "AAA", "10"),
                rename(EVENT_DAY, "BBB", "CCC"),
                rename(EVENT_DAY, "AAA", "BBB"),
            ]
        )


def test_a_rename_cycle_on_the_day_of_a_reorganisation_is_refused() -> None:
    """A cycle is a chain joined up, and is refused by the same test."""
    with pytest.raises(CalculationError, match="is renamed to BBB, and BBB is renamed"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "AAA", "10", "10"),
                legacy_split(EVENT_DAY, "AAA", "10"),
                rename(EVENT_DAY, "AAA", "BBB"),
                rename(EVENT_DAY, "BBB", "AAA"),
            ]
        )


def test_a_rename_chain_ending_on_a_second_holding_is_refused() -> None:
    """The holding it would be pooled with is one rename further on.

    Restating ten units and carrying them two names along, to a name that
    already holds three, leaves those three unrestated. Naming the chain says
    why without the calculator having to work out where the pool landed.
    """
    with pytest.raises(CalculationError, match="is renamed to BBB, and BBB is renamed"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "AAA", "10", "10"),
                trade(POOL_DAY, ActionType.BUY, "CCC", "3", "10"),
                legacy_split(EVENT_DAY, "AAA", "10"),
                rename(EVENT_DAY, "AAA", "BBB"),
                rename(EVENT_DAY, "BBB", "CCC"),
            ]
        )


def test_a_holding_renamed_to_two_names_in_a_day_is_refused() -> None:
    """A holding goes to one name or the other, and the input says neither.

    The rows are applied one after another, so the first moves the shares and
    the second finds nothing to move, but the record of where the holding went
    keeps only the last row. Replaying that record sends the pool somewhere it
    never was, silently, so the second row is refused rather than allowed to
    overwrite the first.
    """
    with pytest.raises(CalculationError, match="renamed to both AAA and BBB"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
                trade(POOL_DAY, ActionType.BUY, "AAA", "3", "10"),
                legacy_split(EVENT_DAY, "OLD", "10"),
                rename(EVENT_DAY, "OLD", "AAA"),
                rename(EVENT_DAY, "OLD", "BBB"),
            ]
        )


def test_the_same_rename_written_twice_is_not_a_conflict() -> None:
    """Two exports repeating one rename say the same thing about it."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
            legacy_split(EVENT_DAY, "OLD", "10"),
            rename(EVENT_DAY, "OLD", "NEW"),
            rename(EVENT_DAY, "OLD", "NEW"),
        ]
    )
    assert calculator.portfolio["NEW"].quantity == Decimal(20)


def test_a_holding_renamed_along_into_the_reorganised_one_is_refused() -> None:
    """The arriving holding is two renames away, and arrives all the same.

    Nothing renames straight into the reorganised name, so looking only at the
    renames it is named in finds an empty holding and lets the day through.
    The three units come along the chain and are never restated. The chain is
    refused before the day's transaction rows are processed.
    """
    with pytest.raises(
        CalculationError, match="is renamed to MID, and MID is renamed to NEW"
    ):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "3", "10"),
                trade(POOL_DAY, ActionType.BUY, "NEW", "10", "10"),
                legacy_split(EVENT_DAY, "NEW", "10"),
                rename(EVENT_DAY, "OLD", "MID"),
                rename(EVENT_DAY, "MID", "NEW"),
            ]
        )


def test_a_second_holding_renamed_onto_the_same_name_is_refused() -> None:
    """Two holdings renamed onto one name meet there, unrestated.

    Neither rename names the other holding, so a test that only looks at the
    renames the reorganised holding appears in sees two ordinary ticker
    changes and lets both through.
    """
    with pytest.raises(
        CalculationError, match="pool it with BBB, which holds shares of its own"
    ):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "AAA", "10", "10"),
                trade(POOL_DAY, ActionType.BUY, "BBB", "3", "10"),
                legacy_split(EVENT_DAY, "AAA", "10"),
                rename(EVENT_DAY, "AAA", "CCC"),
                rename(EVENT_DAY, "BBB", "CCC"),
            ]
        )


# ===== matching across a reorganisation that also renames =====


def test_a_repurchase_is_matched_across_a_split_recorded_under_the_old_name() -> None:
    """The reorganisation is recorded under the name it happened under.

    A holding split and renamed on one day has its event recorded under the
    name it entered the day with, so following the rename before looking for
    it walks straight past. The disposal is then matched against half as many
    units as it bought, at half the cost.
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
            trade(EVENT_DAY, ActionType.SELL, "OLD", "5", "20"),
            legacy_split(EVENT_DAY + datetime.timedelta(days=1), "OLD", "5"),
            rename(EVENT_DAY + datetime.timedelta(days=1), "OLD", "NEW"),
            trade(
                EVENT_DAY + datetime.timedelta(days=2), ActionType.BUY, "NEW", "10", "6"
            ),
        ],
    )
    (entry,) = report.calculation_log[EVENT_DAY]["sell$OLD"]
    assert entry.rule_type is RuleType.BED_AND_BREAKFAST
    # Five old units are ten new ones, so all ten repurchased are matched.
    assert entry.quantity == Decimal(5)
    assert entry.allowable_cost == Decimal(60)
    assert entry.gain == Decimal(40)
    reserved = calculator.bnb_list[EVENT_DAY + datetime.timedelta(days=2)]["NEW"]
    assert reserved.quantity == Decimal(10)
    assert calculator.portfolio["NEW"].quantity == Decimal(20)
    assert calculator.portfolio["NEW"].amount == Decimal(100)


def test_a_repurchase_is_matched_across_a_split_recorded_under_the_new_name() -> None:
    """The mirror: the event is recorded under the name the day ends with.

    Here the old name is emptied by the disposal, so nothing is pooled by the
    rename and the reorganisation belongs to the holding already under the new
    name. The walk has to find it there instead.
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
            trade(EVENT_DAY, ActionType.SELL, "OLD", "10", "20"),
            trade(
                EVENT_DAY + datetime.timedelta(days=1), ActionType.BUY, "NEW", "5", "10"
            ),
            legacy_split(EVENT_DAY + datetime.timedelta(days=1), "NEW", "5"),
            rename(EVENT_DAY + datetime.timedelta(days=1), "OLD", "NEW"),
            trade(
                EVENT_DAY + datetime.timedelta(days=2), ActionType.BUY, "NEW", "10", "6"
            ),
        ],
    )
    first, second = report.calculation_log[EVENT_DAY]["sell$OLD"]
    # The five bought that morning are ten after the reorganisation, and match
    # five of the ten old units disposed of; the rest come from the next day.
    assert first.rule_type is RuleType.BED_AND_BREAKFAST
    assert first.quantity == Decimal(5)
    assert first.allowable_cost == Decimal(50)
    assert second.rule_type is RuleType.BED_AND_BREAKFAST
    assert second.quantity == Decimal(5)
    assert second.allowable_cost == Decimal(60)
    assert calculator.portfolio["NEW"].quantity == Decimal(20)


# ===== placing a same-day row by the instants an export stamped =====


def _stamped(when: datetime.datetime) -> TransactionSource:
    """Provenance for a row from another input that states its instant."""
    return TransactionSource(
        parser="Testing",
        account="Other account",
        file=Path("other.csv"),
        index=0,
        timestamp=when,
    )


SPLIT_OPENED = datetime.datetime(2023, 5, 15, 7, 44, 13, tzinfo=datetime.UTC)
SPLIT_CLOSED = datetime.datetime(2023, 5, 15, 7, 44, 14, tzinfo=datetime.UTC)


def test_a_row_stamped_before_the_event_is_restated() -> None:
    """An exported instant settles the units a same-day row is stated in.

    It is the only thing that does, across two inputs, and a purchase stamped
    ahead of the reorganisation is counted in the units before it.
    """
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            trade(
                EVENT_DAY,
                ActionType.BUY,
                "FOO",
                "2",
                "10",
                source=_stamped(SPLIT_OPENED - datetime.timedelta(minutes=1)),
            ),
            paired_split(
                EVENT_DAY,
                "FOO",
                "12",
                "24",
                Fraction(2),
                instants=(SPLIT_OPENED, SPLIT_CLOSED),
            ),
        ]
    )
    # Twelve units before the event, so the purchase is restated to four.
    assert calculator.portfolio["FOO"].quantity == Decimal(24)
    assert calculator.portfolio["FOO"].amount == Decimal(120)


def test_a_row_stamped_after_the_event_is_left_alone() -> None:
    """The same instant on the other side leaves the count as exported."""
    calculator = run(
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
            trade(
                EVENT_DAY,
                ActionType.BUY,
                "FOO",
                "2",
                "10",
                source=_stamped(SPLIT_CLOSED + datetime.timedelta(minutes=1)),
            ),
            paired_split(
                EVENT_DAY,
                "FOO",
                "10",
                "20",
                Fraction(2),
                instants=(SPLIT_OPENED, SPLIT_CLOSED),
            ),
        ]
    )
    # Already in the day's closing units, so twenty plus the two bought.
    assert calculator.portfolio["FOO"].quantity == Decimal(22)
    assert calculator.portfolio["FOO"].amount == Decimal(120)


def test_a_row_stamped_before_a_lone_booking_instant_is_refused() -> None:
    """One row states when the event was booked, not when the units changed.

    A market restates at the opening of the ex-date and nothing says the
    broker's book-keeping ran to the same clock, so a row ahead of a single
    posted instant may have been made in either unit system. Placing it would
    not merely misstate that row: a single row states a net change, so the
    ratio for the whole holding is derived from what was placed before it.
    """
    with pytest.raises(CalculationError, match="cannot be placed either side") as error:
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                trade(
                    EVENT_DAY,
                    ActionType.BUY,
                    "FOO",
                    "2",
                    "10",
                    source=_stamped(SPLIT_OPENED - datetime.timedelta(minutes=1)),
                ),
                legacy_split(EVENT_DAY, "FOO", "10", source=_stamped(SPLIT_OPENED)),
            ]
        )
    message = str(error.value)
    assert "the instant on it is when the broker booked the entry" in message
    # Both rows state their times, so asking for times would send the reader
    # after a file that does not exist.
    assert "in one input that states times" not in message


def test_a_row_stamped_after_a_lone_booking_instant_is_left_alone() -> None:
    """What a lone instant does prove is that the holding was already restated.

    The broker wrote the row against the new count, so anything it booked
    later is stated in the new units too.
    """
    calculator = run(
        [
            trade(
                POOL_DAY,
                ActionType.BUY,
                "FOO",
                "10",
                "10",
                source=_stamped(SPLIT_OPENED - datetime.timedelta(days=1)),
            ),
            legacy_split(EVENT_DAY, "FOO", "10", source=_stamped(SPLIT_OPENED)),
            trade(
                EVENT_DAY,
                ActionType.BUY,
                "FOO",
                "2",
                "10",
                source=_stamped(SPLIT_CLOSED + datetime.timedelta(minutes=1)),
            ),
        ]
    )
    # Ten doubled to twenty, and the two bought after that are already twenty's
    # units.
    assert calculator.portfolio["FOO"].quantity == Decimal(22)
    assert calculator.portfolio["FOO"].amount == Decimal(120)


def test_a_day_that_sells_more_than_it_can_give_is_refused() -> None:
    """The day's own ledger has to survive the reorganisation."""
    with pytest.raises(CalculationError, match="would leave -10 units"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "FOO", "10", "10"),
                legacy_split(EVENT_DAY, "FOO", "10"),
                trade(EVENT_DAY, ActionType.SELL, "FOO", "30", "5"),
            ]
        )


# ===== the disposal a same-day reorganisation used to cost wrongly =====


def test_a_sale_above_a_same_day_split_keeps_its_share_of_the_pool() -> None:
    """A sale written above the split row sold units the split had not reached.

    Regression test for issue #1045. The reorganisation used to be sized from
    the holding at the sale's place in the stream and its new units pooled as
    a free acquisition ahead of the same-day disposal, so the disposal was
    costed against a pool that never existed: eleven units costing £15,793.25
    allowed £631.73 against a sale of five, where five elevenths of the pool
    is £7,178.75.
    """
    calculator = create_calculator(tax_year=2023, balance_check=False)
    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "11", "1435.75"),
            trade(EVENT_DAY, ActionType.SELL, "FOO", "5", "1924.464"),
            legacy_split(EVENT_DAY, "FOO", "114"),
        ],
    )
    (entry,) = report.calculation_log[EVENT_DAY]["sell$FOO"]
    assert entry.rule_type is RuleType.SECTION_104
    # Five pre-split units are a hundred after the restatement, out of 220.
    assert entry.quantity == Decimal(100)
    assert entry.allowable_cost == Decimal("7178.75")
    assert round_decimal(entry.gain, 2) == Decimal("2443.57")
    assert calculator.portfolio["FOO"].quantity == Decimal(120)
    assert calculator.portfolio["FOO"].amount == Decimal("8614.50")


@pytest.mark.parametrize(
    ("order", "allowable_cost", "gain", "closing"),
    [
        (("split", "sell", "rename"), 25, 15, (15, 75)),
        (("split", "rename", "sell"), 25, 15, (15, 75)),
        (("rename", "split", "sell"), 25, 15, (15, 75)),
        (("sell", "split", "rename"), 50, -10, (10, 50)),
        (("sell", "rename", "split"), 50, -10, (10, 50)),
        (("rename", "sell", "split"), 50, -10, (10, 50)),
    ],
)
def test_a_reorganisation_is_not_a_disposal_row_of_its_own(
    order: tuple[str, ...],
    allowable_cost: int,
    gain: int,
    closing: tuple[int, int],
) -> None:
    """A restated count is not evidence that a disposal took shares away.

    A sale on the day of a reorganisation and a rename on that day are each
    fine on their own. Together they meet the identification code's check
    that no earlier row today has already claimed the day's purchase, and a
    reorganisation moves the count without anything having left.

    Where the sale sits changes what was sold, not what the rename means: a
    row before the reorganisation is a sale of units it had not yet restated,
    which is why those three orders sell five of ten rather than five of
    twenty. Both readings are what `origin/main` gives for the same rows, and
    the rename does not touch either.
    """
    rows = {
        "split": paired_split(EVENT_DAY, "OLD", "10", "20", Fraction(2)),
        "sell": trade(EVENT_DAY, ActionType.SELL, "OLD", "5", "8"),
        "rename": rename(EVENT_DAY, "OLD", "NEW"),
    }
    calculator = create_calculator(tax_year=2023, balance_check=False)

    report = get_report(
        calculator,
        [
            trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
            *(rows[name] for name in order),
        ],
    )

    (entry,) = report.calculation_log[EVENT_DAY]["sell$OLD"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == Decimal(allowable_cost)
    assert entry.gain == Decimal(gain)
    assert calculator.portfolio["NEW"] == Position(
        Decimal(closing[0]), Decimal(closing[1])
    )


def test_a_reorganised_holding_s_rename_day_is_still_read_row_by_row() -> None:
    """A split day keeps the existing row-position handling of renames.

    The later purchase stays under OLD, so selling 25 NEW exceeds the 20
    shares carried across by the rename.
    """
    with pytest.raises(InvalidTransactionError, match=r"available balance\(20\.0*\)"):
        run(
            [
                trade(POOL_DAY, ActionType.BUY, "OLD", "10", "10"),
                legacy_split(EVENT_DAY, "OLD", "10"),
                rename(EVENT_DAY, "OLD", "NEW"),
                trade(EVENT_DAY, ActionType.BUY, "OLD", "5", "10"),
                trade(LATER_DAY, ActionType.SELL, "NEW", "25", "10"),
            ]
        )
