"""A day that both renames a ticker and spins a holding off it.

A rename changes what shares are called; a spin-off moves cost from one
holding to another, apportioned at the reorganisation itself (TCGA 1992 s127,
CG51700, CG51976). So the spelling a row happens to use, and the place an
export puts the RENAME row, must not change any figure: equivalent inputs give
the same answer or the same refusal.

`SRC` costs £100 and is worth £90 a unit on the day; `DST` is worth £10, and
as many are received as `SRC` is held, so a tenth of the value, and a tenth of
the cost, goes to `DST`.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import itertools

import pytest

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CapitalGainsReport
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.util import round_decimal

from .calc_test_data import GBP, transaction, transfer_to_spouse_transaction
from .test_calc import _gbp_fee, get_report

BUY_DAY = datetime.date(2024, 6, 1)
DAY = datetime.date(2024, 7, 5)
FLAT = Decimal(1)
PRICES = {
    name: {DAY: Decimal(90) if name.startswith("SRC") else Decimal(10)}
    for name in ("SRC", "SRCNEW", "DST", "DSTNEW")
}


def rename_row(old: str, new: str) -> BrokerTransaction:
    """Build a RENAME row moving the pool from one ticker to another."""
    return BrokerTransaction(
        date=DAY,
        action=ActionType.RENAME,
        symbol=new,
        description=f"{RENAME_DESCRIPTION_PREFIX}{old}",
        quantity=Decimal(0),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=GBP,
        broker="Testing",
    )


def spin_off_row(symbol: str, units: int = 10) -> BrokerTransaction:
    """Build the SPIN_OFF row that creates `units` of `symbol`."""
    return transaction(DAY, ActionType.SPIN_OFF, symbol, units, 10, 0, currency=GBP)


def run(
    rows: list[BrokerTransaction],
    source: str,
    prices: dict[str, dict[datetime.date, Decimal]] | None = None,
    sources: dict[str, str] | None = None,
) -> CapitalGainsReport:
    """Run these rows with the spin-offs file naming `source` for every ticker."""
    converter = CurrencyConverter(None, {BUY_DAY: {GBP: FLAT}, DAY: {GBP: FLAT}})
    handler = SpinOffHandler()
    handler.cache = sources or dict.fromkeys(("DST", "DSTNEW"), source)
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, prices if prices is not None else PRICES),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    return get_report(calculator, rows)


def holding(report: CapitalGainsReport, symbol: str) -> tuple[Decimal, Decimal]:
    """Return the units and pooled cost the report closes with."""
    (entry,) = (item for item in report.portfolio if item.symbol == symbol)
    return entry.quantity, round_decimal(entry.amount, 4)


SPELLING = pytest.mark.parametrize("spelling", ["SRC", "SRCNEW"], ids=["old", "new"])
RENAME_FIRST = pytest.mark.parametrize(
    "rename_first", [True, False], ids=["rename first", "rename last"]
)


@SPELLING
@RENAME_FIRST
def test_a_renamed_source_gives_up_its_share_whichever_name_is_recorded(
    spelling: str, *, rename_first: bool
) -> None:
    """The four ways to write one day all apportion the same cost.

    Two of them used to be refused, with a message saying the source held no
    shares. It held them under the day's other name.
    """
    rename = rename_row("SRC", "SRCNEW")
    spin = spin_off_row("DST")
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
            *([rename, spin] if rename_first else [spin, rename]),
        ],
        spelling,
    )

    assert holding(report, "SRCNEW") == (Decimal(10), Decimal(90))
    assert holding(report, "DST") == (Decimal(10), Decimal(10))


@pytest.mark.parametrize("dest_spelling", ["DST", "DSTNEW"], ids=["old", "new"])
@pytest.mark.parametrize("held", [False, True], ids=["empty", "already held"])
@RENAME_FIRST
def test_a_renamed_destination_receives_its_share_under_either_name(
    dest_spelling: str, *, held: bool, rename_first: bool
) -> None:
    """A rename of the destination cannot change what it receives.

    Four units costing £20 held beforehand simply pool with the ten that
    arrive carrying £10.
    """
    rename = rename_row("DST", "DSTNEW")
    spin = spin_off_row(dest_spelling)
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
            *(
                [transaction(BUY_DAY, ActionType.BUY, "DST", 4, 5, 0, -20, GBP)]
                if held
                else []
            ),
            *([rename, spin] if rename_first else [spin, rename]),
        ],
        "SRC",
    )

    assert holding(report, "SRC") == (Decimal(10), Decimal(90))
    expected = (Decimal(14), Decimal(30)) if held else (Decimal(10), Decimal(10))
    assert holding(report, "DSTNEW") == expected


def _source_activity(kind: str, symbol: str) -> BrokerTransaction:
    """Build a row that touches the source holding on the spin-off day."""
    if kind == "sold":
        return transaction(DAY, ActionType.SELL, symbol, 2, 95, 0, 190, GBP)
    if kind == "bought":
        return transaction(DAY, ActionType.BUY, symbol, 2, 95, 0, -190, GBP)
    if kind == "transferred to a spouse":
        return transfer_to_spouse_transaction(DAY, symbol, 2)
    return _gbp_fee(DAY, symbol, 5)


@pytest.mark.parametrize(
    "kind", ["sold", "bought", "transferred to a spouse", "charged a fee"]
)
@SPELLING
def test_a_source_traded_on_the_spin_off_day_is_refused_under_either_name(
    kind: str, spelling: str
) -> None:
    """Trading the source that day is refused whichever name the row uses.

    The cost is apportioned at the reorganisation (CG51976), so whether the
    trade came before or after it decides what carries across, and a date
    carries no time of day. Recorded under the day's other name the trade used
    to go unseen, and the day was worked out as though it had come after.
    """
    rename = rename_row("SRC", "SRCNEW")
    spin = spin_off_row("DST")
    activity = _source_activity(kind, spelling)
    day_rows = (
        [spin, activity, rename] if spelling == "SRC" else [spin, rename, activity]
    )
    with pytest.raises(CalculationError) as excinfo:
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
                *day_rows,
            ],
            "SRC",
        )

    assert f"was also {kind} that day" in str(excinfo.value)


@pytest.mark.parametrize("sale_spelling", ["OLD", "NEW"], ids=["old", "new"])
def test_a_rename_elsewhere_on_a_spin_off_day_still_reads_as_one_holding(
    sale_spelling: str,
) -> None:
    """A spin-off does not stop an unrelated rename being read as one holding.

    `OLD` and `NEW` have nothing to do with the spin-off, and a sale of them
    used to be rejected as a sale of shares not owned whenever a spin-off
    happened to fall on the same day.
    """
    rows = [
        transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
        transaction(BUY_DAY, ActionType.BUY, "OLD", 100, 10, 0, -1000, GBP),
        spin_off_row("DST"),
        rename_row("OLD", "NEW"),
        transaction(DAY, ActionType.SELL, sale_spelling, 50, 20, 0, 1000, GBP),
    ]

    report = run(rows, "SRC")

    assert report.total_gain() == Decimal(500)
    assert holding(report, "NEW") == (Decimal(50), Decimal(500))
    assert holding(report, "DST") == (Decimal(10), Decimal(10))


@pytest.mark.parametrize(
    "sale_spelling", ["DST", "DSTNEW"], ids=["sold old", "sold new"]
)
@pytest.mark.parametrize(
    "dest_spelling", ["DST", "DSTNEW"], ids=["into old", "into new"]
)
@pytest.mark.parametrize("held", [True, False], ids=["held", "new holding"])
@RENAME_FIRST
def test_a_renamed_destination_is_one_holding_however_it_is_spelled(
    sale_spelling: str, dest_spelling: str, *, held: bool, rename_first: bool
) -> None:
    """Selling the destination that day gives one answer under either name.

    The spin-off's shares are not identified against (see
    `_identifiable_acquisition`), so the sale draws on the pool. Where the day
    opened holding the destination that is four units costing £20 plus the ten
    that carried £10, and two of the fourteen leave; where it did not, the ten
    the spin-off created are the whole pool.

    The spin-off's row and the sale each state one of the day's two names for
    the destination, and the RENAME row sits either side of them, so the same
    holding is written down sixteen ways. Spelling the spin-off's row
    differently from the day's pool used to leave the new shares stranded
    under their own name: the sale priced itself against the four units the
    day opened with, or, with nothing held to open with, against an empty pool
    that crashed the run.
    """
    rename = rename_row("DST", "DSTNEW")
    spin = spin_off_row(dest_spelling)
    sale = transaction(DAY, ActionType.SELL, sale_spelling, 2, 20, 0, 40, GBP)
    opening = [transaction(BUY_DAY, ActionType.BUY, "DST", 4, 5, 0, -20, GBP)]
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
            *(opening if held else []),
            *([rename, spin, sale] if rename_first else [spin, sale, rename]),
        ],
        "SRC",
    )

    quantity = Decimal(14 if held else 10)
    cost = Decimal(30 if held else 10)
    (entry,) = report.calculation_log[DAY][f"sell${sale_spelling}"]
    assert entry.allowable_cost == round_decimal(cost * 2 / quantity, 10)
    assert holding(report, "DSTNEW") == (
        quantity - 2,
        round_decimal(cost * (quantity - 2) / quantity, 4),
    )


@RENAME_FIRST
def test_a_destination_renamed_into_the_source_merges_back(
    *, rename_first: bool
) -> None:
    """The new holding is renamed straight back into the one it came from.

    A tenth of the cost goes to `DST` and the rename puts it back under `SRC`,
    so the day ends where it started: twenty units carrying the whole £100.
    """
    rename = rename_row("DST", "SRC")
    spin = spin_off_row("DST")
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
            *([rename, spin] if rename_first else [spin, rename]),
        ],
        "SRC",
    )

    assert holding(report, "SRC") == (Decimal(20), Decimal(100))


@RENAME_FIRST
def test_a_source_renamed_to_the_name_it_spins_off_is_refused(
    *, rename_first: bool
) -> None:
    """One ticker cannot name both ends of the same reorganisation.

    The day says `SRC` is now called `DST` and that `SRC` spins off a new
    holding called `DST`. What the source was worth is read from its ticker,
    and that ticker would have to stand for both holdings at once, so there is
    no price to read. Refused whichever order the rows come in: it used to be
    refused one way, with a message saying the source held no shares, and
    computed the other.
    """
    rename = rename_row("SRC", "DST")
    spin = spin_off_row("DST")
    with pytest.raises(CalculationError, match="names both the holding"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 10, 10, 0, -100, GBP),
                *([rename, spin] if rename_first else [spin, rename]),
            ],
            "SRC",
        )


def _priced(**prices: int) -> dict[str, dict[datetime.date, Decimal]]:
    """Give each named ticker its own closing price on the spin-off day."""
    return {name: {DAY: Decimal(price)} for name, price in prices.items()}


@RENAME_FIRST
def test_two_prices_for_one_renamed_source_are_refused(*, rename_first: bool) -> None:
    """A holding cannot be worth two different amounts on one day.

    `SRC` is priced at £75 and `SRCNEW` at £60, and the day's renames say they
    are one holding. Which of them splits the cost used to depend on where the
    RENAME row sat: reading `SRC` left `DST` with £36.00 and reading `SRCNEW`
    left it with £42.35, from the same twelve shares.
    """
    rename = rename_row("SRC", "SRCNEW")
    spin = spin_off_row("DST", 12)
    with pytest.raises(CalculationError, match="priced differently"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                *([rename, spin] if rename_first else [spin, rename]),
            ],
            "SRC",
            _priced(SRC=75, SRCNEW=60, DST=25),
        )


@pytest.mark.parametrize("spelling", ["DST", "DSTNEW"], ids=["old", "new"])
def test_two_prices_for_one_renamed_destination_are_refused(spelling: str) -> None:
    """The same, for the holding the shares arrive in.

    Priced as `DST` the new holding took £36.00 and priced as `DSTNEW` it took
    £50.09, for one twelve-share spin-off written down two ways.
    """
    with pytest.raises(CalculationError, match="priced differently"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                spin_off_row(spelling, 12),
                rename_row("DST", "DSTNEW"),
            ],
            "SRC",
            _priced(SRC=75, DST=25, DSTNEW=40),
        )


def test_one_spin_off_recorded_under_two_destination_names_is_refused() -> None:
    """Rows of one reorganisation spelled two ways cannot be joined.

    Five shares under `DST` and seven under `DSTNEW` are one twelve-share
    event, but each name keeps its own acquisition record, so they were
    apportioned separately and the second took a share of a pool the first had
    already reduced: £38.14 where twelve shares at once take £36.00.
    """
    with pytest.raises(CalculationError, match="the same holding as"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                spin_off_row("DST", 5),
                spin_off_row("DSTNEW", 7),
                rename_row("DST", "DSTNEW"),
            ],
            "SRC",
            _priced(SRC=75, DST=25, DSTNEW=25),
        )


@pytest.mark.parametrize(
    "rename_position", [0, 1, 2], ids=["before", "between", "after"]
)
def test_a_rename_between_two_rows_of_one_spin_off_is_still_one_event(
    rename_position: int,
) -> None:
    """Where the RENAME row sits cannot split one reorganisation in two.

    Both rows name `DST`, so they are one acquisition; the rename only decides
    whether each row's source was spelled `SRC` or `SRCNEW`. With the RENAME
    row between them the two used to be apportioned as separate events, giving
    the new holding £38.14 instead of £36.00.
    """
    day_rows = [spin_off_row("DST", 5), spin_off_row("DST", 7)]
    day_rows.insert(rename_position, rename_row("SRC", "SRCNEW"))
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
            *day_rows,
        ],
        "SRC",
        _priced(SRC=75, DST=25),
    )

    assert holding(report, "DST") == (Decimal(12), Decimal("36.00"))
    assert holding(report, "SRCNEW") == (Decimal(12), Decimal("108.00"))


@pytest.mark.parametrize("spelling", ["B", "BNEW"], ids=["old", "new"])
def test_a_renamed_holding_cannot_spin_off_before_it_is_created(
    spelling: str,
) -> None:
    """An out-of-order chain is refused whichever name its rows use.

    `B` spins off `C` and is itself spun off from `A` the same day, listed the
    wrong way round, so `C` was worked out on `B` before `B` had `A`'s shares.
    Spelling the second row `B` while the first had already recorded `BNEW`
    used to hide the clash, and the day was computed with `C` carrying £66.67
    where the required order gives about £46.67.
    """
    with pytest.raises(CalculationError, match="List the spin-off that creates"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "A", 10, 10, 0, -100, GBP),
                transaction(BUY_DAY, ActionType.BUY, "B", 10, 20, 0, -200, GBP),
                rename_row("B", "BNEW"),
                spin_off_row("C", 10),
                spin_off_row(spelling, 10),
            ],
            "A",
            _priced(A=90, B=90, BNEW=90, C=10),
            sources={"C": "B", "B": "A", "BNEW": "A"},
        )


def test_a_spin_offs_file_naming_a_holding_as_its_own_source_says_so() -> None:
    """No rename happened, so the refusal must not claim one did."""
    with pytest.raises(CalculationError) as excinfo:
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                spin_off_row("SRC", 12),
            ],
            "SRC",
            sources={"SRC": "SRC"},
        )

    assert "the spin-offs file gives SRC itself" in str(excinfo.value)
    assert "renames" not in str(excinfo.value)


@RENAME_FIRST
def test_a_chain_of_spin_offs_across_a_rename_keeps_its_order(
    *, rename_first: bool
) -> None:
    """`A` hands shares to `B`, which is renamed and hands some to `C`.

    `B` must be complete before it is apportioned, and the rename leaves its
    two spin-off rows naming it differently, so the order they are worked out
    in cannot be read from the spellings alone.
    """
    rename = rename_row("B", "BNEW")
    rows = [spin_off_row("B", 10), spin_off_row("C", 10)]
    rows.insert(0 if rename_first else 1, rename)
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "A", 10, 10, 0, -100, GBP),
            *rows,
        ],
        "A",
        _priced(A=90, B=10, BNEW=10, C=10),
        sources={"B": "A", "BNEW": "A", "C": "B"},
    )

    # A is worth nine times what it hands over, so £10 of its £100 goes to B.
    # B and C are worth the same, so B then halves what it has with C.
    assert holding(report, "A") == (Decimal(10), Decimal(90))
    assert holding(report, "BNEW") == (Decimal(10), Decimal(5))
    assert holding(report, "C") == (Decimal(10), Decimal(5))


@pytest.mark.parametrize("spelling", ["SRCOLD", "SRC"], ids=["old", "new"])
@pytest.mark.parametrize("rename_first", [True, False], ids=["renames first", "last"])
def test_two_prices_for_a_source_that_merges_with_its_new_holding_are_refused(
    spelling: str, *, rename_first: bool
) -> None:
    """The source's own two names disagree, and the merge must not hide it.

    `SRCOLD` becomes `SRC` and the new `DST` shares are renamed into `SRC` too,
    so by the day's close everything is one holding. That must not stop the
    source's own £75 and £60 being read as the contradiction they are: which
    was used decided whether `DST` carried £36.00 or £42.35.
    """
    renames = [rename_row("SRCOLD", "SRC"), rename_row("DST", "SRC")]
    spin = spin_off_row("DST", 12)
    with pytest.raises(CalculationError, match="neither holding's own"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRCOLD", 12, 12, 0, -144, GBP),
                *([*renames, spin] if rename_first else [spin, *renames]),
            ],
            spelling,
            _priced(SRCOLD=75, SRC=60, DST=25),
            sources={"DST": spelling},
        )


@pytest.mark.parametrize("spelling", ["DSTOLD", "DST"], ids=["old", "new"])
def test_a_third_price_in_a_merged_reorganisation_is_refused(spelling: str) -> None:
    """A price belonging to neither end of the reorganisation settles nothing.

    Both `DSTOLD` and `DST` are renamed into `SRC`, so the whole day closes
    under one name. Whichever of them the spin-off row states, the other is
    priced too, and nothing says whether that £25 or £40 prices the shares the
    reorganisation came from or the ones it created.
    """
    with pytest.raises(CalculationError, match="neither holding's own"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                spin_off_row(spelling, 12),
                rename_row("DSTOLD", "SRC"),
                rename_row("DST", "SRC"),
            ],
            "SRC",
            _priced(SRC=75, DSTOLD=25, DST=40),
            sources={"DSTOLD": "SRC", "DST": "SRC"},
        )


def test_two_spin_offs_into_one_renamed_destination_are_two_events() -> None:
    """Two holdings can each spin something off into one merged destination.

    `A` hands 10 shares to `DST` and `B` hands 20 to `DSTNEW`, and the day's
    rename makes those one holding. They are two reorganisations, each settled
    against its own source, and only pooled together at the day's close.
    """
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "A", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "B", 20, 20, 0, -400, GBP),
            spin_off_row("DST", 10),
            spin_off_row("DSTNEW", 20),
            rename_row("DST", "DSTNEW"),
        ],
        "A",
        _priced(A=90, B=80, DST=10, DSTNEW=10),
        sources={"DST": "A", "DSTNEW": "B"},
    )

    assert holding(report, "A") == (Decimal(10), Decimal("90.00"))
    assert holding(report, "B") == (Decimal(20), Decimal("355.56"))
    assert holding(report, "DSTNEW") == (Decimal(30), Decimal("54.44"))


def test_a_third_price_matching_one_end_is_refused_all_the_same() -> None:
    """A price that agrees with one end is not thereby that end's price.

    `SRCOLD` becomes `SRC`, and the new `DST` shares are renamed into `SRC`
    too, so all three end the day as one holding. `SRC` is priced at £25, the
    same as `DST`. Matching says only that those two do not contradict each
    other: `SRC` is the source's closing name as much as the destination's,
    and the source is priced at £75, so the day still has no settled value
    for either end.
    """
    with pytest.raises(CalculationError, match="neither holding's own"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRCOLD", 12, 12, 0, -144, GBP),
                spin_off_row("DST", 12),
                rename_row("SRCOLD", "SRC"),
                rename_row("DST", "SRC"),
            ],
            "SRCOLD",
            _priced(SRCOLD=75, SRC=25, DST=25),
            sources={"DST": "SRCOLD"},
        )


@pytest.mark.parametrize("spelling", ["DSTOLD", "DST"], ids=["old", "new"])
def test_a_destination_sibling_priced_differently_is_refused(spelling: str) -> None:
    """Two names that merge into one are one holding, at one price.

    `DSTOLD` and `DST` both become `SRC`, and `DST` is priced at £40, the same
    as `SRC`. Agreeing with the source's price does not make it the source's:
    `DST` is a name of the holding the shares arrived in, priced against
    `DSTOLD`'s £25, and which of the two the row happened to state decided
    what the reorganisation carried across.
    """
    with pytest.raises(CalculationError, match="neither holding's own"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                spin_off_row(spelling, 12),
                rename_row("DSTOLD", "SRC"),
                rename_row("DST", "SRC"),
            ],
            "SRC",
            _priced(SRC=40, DSTOLD=25, DST=40),
            sources={"DSTOLD": "SRC", "DST": "SRC"},
        )


@pytest.mark.parametrize("spelling", ["DSTOLD", "DST"], ids=["old", "new"])
def test_destination_siblings_merging_away_from_the_source_are_one_holding(
    spelling: str,
) -> None:
    """The same, where the merged name is neither end's.

    `DSTOLD` and `DST` become `DSTNEW`, which the source is no part of, so
    the two ends stay separate holdings all day. The received shares are still
    spelled two ways and priced two ways, and one of them has to be wrong.
    """
    with pytest.raises(CalculationError, match="priced differently"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRC", 12, 12, 0, -144, GBP),
                spin_off_row(spelling, 12),
                rename_row("DSTOLD", "DSTNEW"),
                rename_row("DST", "DSTNEW"),
            ],
            "SRC",
            _priced(SRC=75, DSTOLD=25, DST=40),
            sources={"DSTOLD": "SRC", "DST": "SRC"},
        )


ROW_ORDERS = pytest.mark.parametrize(
    "order", list(itertools.permutations(range(3))), ids=lambda o: "".join(map(str, o))
)


def _ordered(
    rows: list[BrokerTransaction], order: tuple[int, ...]
) -> list[BrokerTransaction]:
    """Return the day's rows in one of the orders an export might list them."""
    return [rows[position] for position in order]


@ROW_ORDERS
def test_a_uniformly_priced_merge_computes(order: tuple[int, ...]) -> None:
    """One price for the whole day settles it, so there is nothing to refuse.

    `SRCOLD` and the new `DST` shares both become `SRC`, and all three names
    are priced at £40. Whichever of them a figure is read as, the two ends are
    worth the same, so the cost splits down the middle and the rename puts both
    halves back together.
    """
    rows = [
        rename_row("SRCOLD", "SRC"),
        rename_row("DST", "SRC"),
        spin_off_row("DST", 12),
    ]
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRCOLD", 12, 12, 0, -144, GBP),
            *_ordered(rows, order),
        ],
        "SRCOLD",
        _priced(SRCOLD=40, SRC=40, DST=40),
        sources={"DST": "SRCOLD"},
    )

    assert holding(report, "SRC") == (Decimal(24), Decimal(144))


@ROW_ORDERS
def test_a_uniformly_priced_merge_of_unequal_sides_computes(
    order: tuple[int, ...],
) -> None:
    """One price does not mean an even split: the counts still differ.

    Twenty shares costing £280 hand seven to the new holding, all priced at
    £40, so the value splits 800 to 280 and the cost with it.
    """
    rows = [
        rename_row("SRCOLD", "SRC"),
        rename_row("DST", "SRC"),
        spin_off_row("DST", 7),
    ]
    report = run(
        [
            transaction(BUY_DAY, ActionType.BUY, "SRCOLD", 20, 14, 0, -280, GBP),
            *_ordered(rows, order),
        ],
        "SRCOLD",
        _priced(SRCOLD=40, SRC=40, DST=40),
        sources={"DST": "SRCOLD"},
    )

    (entry,) = report.calculation_log[DAY]["buy$DST"]
    assert round_decimal(entry.allowable_cost, 2) == Decimal("72.59")
    assert holding(report, "SRC") == (Decimal(27), Decimal(280))


def test_a_third_price_against_agreeing_ends_is_still_refused() -> None:
    """Ends that agree do not make every other figure safe.

    `SRCOLD` and `DST` are both worth £40, but `SRC`, which the day's renames
    make of both of them, is priced at £25. That is a second valuation of the
    day, and which end it belongs to still decides how the cost splits.
    """
    with pytest.raises(CalculationError, match="neither holding's own"):
        run(
            [
                transaction(BUY_DAY, ActionType.BUY, "SRCOLD", 12, 12, 0, -144, GBP),
                spin_off_row("DST", 12),
                rename_row("SRCOLD", "SRC"),
                rename_row("DST", "SRC"),
            ],
            "SRCOLD",
            _priced(SRCOLD=40, SRC=25, DST=40),
            sources={"DST": "SRCOLD"},
        )
