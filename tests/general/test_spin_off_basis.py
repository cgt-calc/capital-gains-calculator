"""The cost a spin-off carries to the new holding comes from the real pool.

The first pass can only estimate the source pool, and the estimate is wrong
after any profitable sale and in any currency but GBP. Every case here uses a
90/10 split, so the new holding should receive a tenth of whatever the source
pool really cost, with the expected figures worked out by hand.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalGainsReport,
    CurrencyCode,
    RuleType,
)
from cgt_calc.parsers.broker_registry import _transaction_sort_key
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.util import round_decimal

from .calc_test_data import GBP, USD, transaction, transfer_to_spouse_transaction
from .test_calc import get_report

BUY_DAY = datetime.date(2024, 6, 1)
SALE_DAY = datetime.date(2024, 6, 10)
SPIN_OFF_DAY = datetime.date(2024, 7, 5)
# Inside the 30 days before the spin-off, so the 30-day rule reaches its day.
EARLY_SALE_DAY = datetime.date(2024, 6, 25)
LATER = datetime.date(2024, 9, 1)

# FOO at £90 and BAR at £10 on the day, with as many BAR as FOO units, puts a
# tenth of the market value in BAR, so a tenth of the cost goes with it.
PRICES = {"FOO": {SPIN_OFF_DAY: Decimal(90)}, "BAR": {SPIN_OFF_DAY: Decimal(10)}}
FLAT = Decimal(1)


def _holding(report: CapitalGainsReport, symbol: str) -> tuple[Decimal, Decimal]:
    """Return the units and pooled cost the report closes with."""
    (entry,) = (item for item in report.portfolio if item.symbol == symbol)
    return entry.quantity, round_decimal(entry.amount, 2)


def spin_off(
    transactions: list[BrokerTransaction],
    foo_units: int,
    rates: dict[datetime.date, dict[CurrencyCode, Decimal]],
    currency: CurrencyCode = GBP,
    after: list[BrokerTransaction] | None = None,
) -> tuple[CapitalGainsCalculator, CapitalGainsReport]:
    """Run `transactions` then spin `foo_units` of BAR out of FOO.

    Rows in `after` are listed behind the spin-off, which is what a row on the
    spin-off day itself needs: the sort keeps the order it is given, and a
    disposal read before the shares arrive is refused.
    """
    converter = CurrencyConverter(None, rates)
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, PRICES),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    # Date order, as the CLI reads them: the first pass works out the split
    # from the units held on the day, so a later sale must not come first.
    report = get_report(
        calculator,
        sorted(
            [
                *transactions,
                transaction(
                    SPIN_OFF_DAY,
                    ActionType.SPIN_OFF,
                    "BAR",
                    foo_units,
                    10,
                    0,
                    currency=currency,
                ),
                *(after or []),
            ],
            key=_transaction_sort_key,
        ),
    )
    return calculator, report


def test_baseline_without_a_prior_sale() -> None:
    """Sanity check of the harness: 10 units costing £100, a tenth goes to BAR."""
    calculator, _ = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP)],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_after_a_profitable_sale() -> None:
    """Half sold at a profit leaves 5 units costing £50, so BAR gets £5.

    The first-pass pool had the £100 of proceeds taken off it instead of the
    £50 of cost, leaving nothing for BAR to take a share of.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, GBP),
        ],
        5,
        {BUY_DAY: {GBP: FLAT}, SALE_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    assert calculator.portfolio["BAR"].amount == Decimal(5)


def test_cost_is_not_revalued_at_the_spin_off_days_rate() -> None:
    """Cost is what the shares cost in sterling when bought, not at today's rate.

    Rates are quoted as dollars per pound. $100 at $0.50/£ cost £200, so a
    tenth of that, £20, goes to BAR. The first pass kept the $100 and turned it
    into pounds at the spin-off day's $1.00/£, handing BAR £10.
    """
    calculator, _ = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, USD)],
        10,
        {BUY_DAY: {USD: Decimal("0.5")}, SPIN_OFF_DAY: {USD: Decimal("1.0")}},
        currency=USD,
    )

    assert calculator.portfolio["FOO"].amount == Decimal(180)
    assert calculator.portfolio["BAR"].amount == Decimal(20)


def test_after_a_transfer_to_spouse_matched_on_its_day() -> None:
    """A same-day purchase matched to a transfer leaves the pool at 10 @ £100.

    The first pass booked the transfer at the pool average instead, so its
    estimate of the pool was £133.33 and BAR was handed a third more cost than
    it should have been.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SALE_DAY, ActionType.BUY, "FOO", 5, 20, 0, -100, GBP),
            transfer_to_spouse_transaction(SALE_DAY, "FOO", 5),
        ],
        10,
        {BUY_DAY: {GBP: FLAT}, SALE_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_cost_is_conserved_across_both_holdings() -> None:
    """What FOO gives up is exactly what BAR receives.

    Selling FOO afterwards applies its side of the split: £45 of allowable
    cost on the sale, and the £5 that went to BAR, add back up to the £50 the
    pool held before the spin-off.
    """
    calculator, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, GBP),
            transaction(LATER, ActionType.SELL, "FOO", 5, 30, 0, 150, GBP),
        ],
        5,
        {
            BUY_DAY: {GBP: FLAT},
            SALE_DAY: {GBP: FLAT},
            SPIN_OFF_DAY: {GBP: FLAT},
            LATER: {GBP: FLAT},
        },
    )

    later_sale = report.calculation_log[LATER]["sell$FOO"]
    foo_cost_given_up = sum((e.allowable_cost for e in later_sale), Decimal(0))
    assert foo_cost_given_up == Decimal(45)
    assert calculator.portfolio["BAR"].amount == Decimal(5)
    assert foo_cost_given_up + calculator.portfolio["BAR"].amount == Decimal(50)


def test_source_gives_up_its_share_on_the_day() -> None:
    """The original cost is apportioned at the reorganisation (CG51976).

    Applying it only when FOO is next sold left a holding that was never sold
    again at its full cost, and one bought into before the sale handing over
    a share of shares that had nothing to do with the spin-off.
    """
    calculator, _ = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP)],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_a_later_purchase_of_the_source_is_untouched() -> None:
    """£200 of FOO bought after the spin-off keeps all £200 of its cost."""
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(LATER, ActionType.BUY, "FOO", 20, 10, 0, -200, GBP),
            transaction(
                datetime.date(2024, 10, 1),
                ActionType.SELL,
                "FOO",
                30,
                20,
                0,
                600,
                GBP,
            ),
        ],
        10,
        {
            BUY_DAY: {GBP: FLAT},
            SPIN_OFF_DAY: {GBP: FLAT},
            LATER: {GBP: FLAT},
            datetime.date(2024, 10, 1): {GBP: FLAT},
        },
    )

    # £90 left from the spin-off plus £200 bought later, not 90% of £300.
    assert report.allowable_costs == Decimal(290)


def test_a_purchase_of_the_new_holding_on_the_day_keeps_its_cost() -> None:
    """Only the spin-off's share of the day's acquisitions is replaced.

    The day's acquisitions of BAR are one entry, so overwriting it with the
    spin-off's figure threw away the £50 paid for five more.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 5, 10, 0, -50, GBP),
        ],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    assert calculator.portfolio["BAR"].quantity == Decimal(15)
    assert calculator.portfolio["BAR"].amount == Decimal(60)


def test_selling_the_new_holding_on_the_day_uses_the_corrected_cost() -> None:
    """A sale on the spin-off day draws on the pool at the corrected cost.

    Half of FOO sold at a profit first, so the first pass estimated nothing
    for BAR while the real share is 5. The day's acquisitions are walked
    before its disposals, so the sale must see the 5, not the estimate.

    The spin-off is not an acquisition, so the same-day rule passes it over
    and the sale takes its Section 104 cost. Here the pool holds nothing but
    the spun-off shares, so the £5 the sale is given is the whole corrected
    figure either way; the rule it is matched under is pinned so that a
    change of route shows up as a change of route.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, GBP),
        ],
        5,
        {BUY_DAY: {GBP: FLAT}, SALE_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
        after=[transaction(SPIN_OFF_DAY, ActionType.SELL, "BAR", 5, 10, 0, 50, GBP)],
    )

    (entry,) = report.calculation_log[SPIN_OFF_DAY]["sell$BAR"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == Decimal(5)


def test_selling_the_new_holding_just_before_the_spin_off_uses_its_own_pool() -> None:
    """A sale shortly before a spin-off is not identified against its shares.

    BAR was already held and sold ten days before more arrived by spin-off.
    Those shares were not acquired: a reorganisation is "not to be treated as
    involving ... any acquisition of the new holding" (TCGA 1992 s127), and
    CG51560 says the additional shares are "not treated as acquired within the
    30 days following the disposal". So the 30-day rule passes them over and
    the sale takes its own Section 104 cost, which needs nothing from the
    spin-off day.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "BAR", 2, 1, 0, -2, GBP),
            transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, GBP),
            transaction(EARLY_SALE_DAY, ActionType.SELL, "BAR", 2, 3, 0, 6, GBP),
        ],
        5,
        {
            BUY_DAY: {GBP: FLAT},
            SALE_DAY: {GBP: FLAT},
            EARLY_SALE_DAY: {GBP: FLAT},
            SPIN_OFF_DAY: {GBP: FLAT},
        },
    )

    (entry,) = report.calculation_log[EARLY_SALE_DAY]["sell$BAR"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == Decimal(2)


def test_a_spin_off_on_the_disposal_day_is_not_a_same_day_acquisition() -> None:
    """The day's only arrival is a reorganisation, so nothing is acquired.

    A reorganisation is "not to be treated as involving ... any acquisition of
    the new holding" (TCGA 1992 s127), and the same-day rule reads a day's
    acquisitions as one transaction (s105(1)(a)). With none, the sale takes
    its Section 104 cost from a pool the spin-off has already joined: 2 units
    costing £10 plus the 10 that carried £10 across, so £20 over 12 units.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "BAR", 2, 5, 0, -10, GBP),
        ],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
        after=[transaction(SPIN_OFF_DAY, ActionType.SELL, "BAR", 2, 20, 0, 40, GBP)],
    )

    (entry,) = report.calculation_log[SPIN_OFF_DAY]["sell$BAR"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == round_decimal(Decimal(20) * 2 / 12, 10)
    # What the spin-off carried in is not stranded: the pool keeps the rest.
    assert _holding(report, "BAR") == (
        Decimal(10),
        round_decimal(Decimal(20) * 10 / 12, 2),
    )


def test_a_purchase_beside_a_spin_off_prices_a_same_day_sale_on_its_own() -> None:
    """The five bought are the day's acquisition; the ten spun in are not.

    Blending them priced the sale at £60 over 15 units. The day's acquisition
    is the £50 paid for five shares, and the sale of five takes all of it.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 5, 10, 0, -50, GBP),
        ],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
        after=[transaction(SPIN_OFF_DAY, ActionType.SELL, "BAR", 5, 10, 0, 50, GBP)],
    )

    (entry,) = report.calculation_log[SPIN_OFF_DAY]["sell$BAR"]
    assert entry.rule_type is RuleType.SAME_DAY
    assert entry.allowable_cost == Decimal(50)
    assert report.total_gain() == Decimal(0)
    # Only the spin-off's own £10 is left behind for the ten shares still held.
    assert _holding(report, "BAR") == (Decimal(10), Decimal(10))


def test_a_purchase_inside_the_30_days_is_matched_and_a_spin_off_beside_it_is_not() -> (
    None
):
    """Only the purchase is a re-acquisition; the spin-off beside it is not.

    The two sold are identified against two of the five bought, at the £10
    each they cost, and the pool has to give up exactly that. Priced over
    everything the day brought in it would give up £8, and the £12 left over
    would be relieved again on a later sale.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "BAR", 2, 5, 0, -10, GBP),
            transaction(EARLY_SALE_DAY, ActionType.SELL, "BAR", 2, 20, 0, 40, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 5, 10, 0, -50, GBP),
        ],
        10,
        {
            BUY_DAY: {GBP: FLAT},
            EARLY_SALE_DAY: {GBP: FLAT},
            SPIN_OFF_DAY: {GBP: FLAT},
        },
    )

    (entry,) = report.calculation_log[EARLY_SALE_DAY]["sell$BAR"]
    assert entry.rule_type is RuleType.BED_AND_BREAKFAST
    assert entry.allowable_cost == Decimal(20)
    # £10 + £50 + £10 of cost went in and £20 was relieved, so £50 remains.
    assert _holding(report, "BAR") == (Decimal(15), Decimal(50))


def test_a_later_day_s_own_sale_reserves_only_what_that_day_bought() -> None:
    """A day's sale of more than it bought leaves the earlier one nothing.

    The spin-off day buys two and sells five. The same-day rule takes those
    two first, so there is nothing left for the earlier sale to be identified
    against, and counting the ten spun in as available would hand it shares
    that were never acquired.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "BAR", 4, 5, 0, -20, GBP),
            transaction(EARLY_SALE_DAY, ActionType.SELL, "BAR", 2, 20, 0, 40, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 2, 15, 0, -30, GBP),
        ],
        10,
        {
            BUY_DAY: {GBP: FLAT},
            EARLY_SALE_DAY: {GBP: FLAT},
            SPIN_OFF_DAY: {GBP: FLAT},
        },
        after=[transaction(SPIN_OFF_DAY, ActionType.SELL, "BAR", 5, 12, 0, 60, GBP)],
    )

    (entry,) = report.calculation_log[EARLY_SALE_DAY]["sell$BAR"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == Decimal(10)


def test_a_sale_and_a_transfer_on_a_spin_off_day_do_not_contend() -> None:
    """Neither can be identified against the day's spin-off, so neither wins.

    A sale and a transfer to a spouse on one day are refused when both could
    be identified against the same acquisition, because HMRC does not say
    which one gets it. Shares a reorganisation brought in are not an
    acquisition either of them can reach, so both simply draw on the pool at
    the same average and the order between them changes nothing.
    """
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "BAR", 4, 5, 0, -20, GBP),
        ],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
        after=[
            transaction(SPIN_OFF_DAY, ActionType.SELL, "BAR", 2, 20, 0, 40, GBP),
            transfer_to_spouse_transaction(SPIN_OFF_DAY, "BAR", 2),
        ],
    )

    # 4 units costing £20 plus the 10 the spin-off carried £10 into: £30 over
    # 14 units, and each of the two takes two units of it.
    pooled = round_decimal(Decimal(30) * 2 / 14, 10)
    (sale,) = report.calculation_log[SPIN_OFF_DAY]["sell$BAR"]
    (transfer,) = report.calculation_log[SPIN_OFF_DAY]["transfer-to-spouse$BAR"]
    assert sale.rule_type is RuleType.SECTION_104
    assert sale.allowable_cost == pooled
    assert transfer.allowable_cost == pooled


def test_a_spin_off_before_the_period_is_applied_but_not_reported() -> None:
    """Like a purchase before the period: it shapes the pool, not the report.

    It used to appear in the report under its own, earlier date whenever a
    disposal inside the period happened to be the one that triggered it.
    """
    earlier_buy = datetime.date(2023, 6, 1)
    earlier_spin_off = datetime.date(2023, 7, 5)
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {earlier_spin_off: Decimal(90)},
                "BAR": {earlier_spin_off: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    report = get_report(
        calculator,
        [
            transaction(earlier_buy, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(
                earlier_spin_off, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
            transaction(LATER, ActionType.SELL, "FOO", 10, 20, 0, 200, GBP),
        ],
    )

    # The 2024/25 sale gets the apportioned £90, and nothing from 2023 is listed.
    assert report.allowable_costs == Decimal(90)
    assert earlier_spin_off not in report.calculation_log


@pytest.mark.parametrize(
    "same_day",
    [
        pytest.param(
            [transaction(SPIN_OFF_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP)],
            id="bought",
        ),
        pytest.param(
            [
                transaction(SPIN_OFF_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(SPIN_OFF_DAY, ActionType.SELL, "FOO", 10, 10, 0, 100, GBP),
            ],
            id="bought-and-sold",
        ),
        pytest.param(
            [transaction(SPIN_OFF_DAY, ActionType.SELL, "FOO", 5, 10, 0, 50, GBP)],
            id="sold",
        ),
        pytest.param(
            [transfer_to_spouse_transaction(SPIN_OFF_DAY, "FOO", 5)],
            id="transferred-to-a-spouse",
        ),
        pytest.param(
            # A fee adds cost with no shares, so it slipped past a check on
            # quantity, and the pool was apportioned with the fee in it.
            [transaction(SPIN_OFF_DAY, ActionType.FEE, "FOO", None, None, 0, -10, GBP)],
            id="charged-a-fee",
        ),
    ],
)
def test_source_traded_on_the_spin_off_day_is_refused(
    same_day: list[BrokerTransaction],
) -> None:
    """The input has dates, not times, so which shares were reorganised is unknown.

    Bought and sold the same day, the shares match under the same-day rule
    and never enter the pool, so apportioning a pool that held them used a
    holding that never existed. Bought alone, whether the purchase came
    before the reorganisation is just as unknowable.
    """
    with pytest.raises(CalculationError, match="dates but not times"):
        spin_off(
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                *same_day,
            ],
            10,
            {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
        )


def test_a_source_renamed_on_the_day_is_still_apportioned() -> None:
    """OLD becomes NEW in the morning and NEW spins BAR off: a common demerger shape.

    Renames are applied after the day's acquisitions, so NEW's pool is still
    under OLD when BAR is apportioned; it used to be found empty, and BAR got
    a cost of nothing.
    """
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "NEW"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {"NEW": {SPIN_OFF_DAY: Decimal(90)}, "BAR": {SPIN_OFF_DAY: Decimal(10)}},
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    rename = BrokerTransaction(
        date=SPIN_OFF_DAY,
        action=ActionType.RENAME,
        symbol="NEW",
        description=f"{RENAME_DESCRIPTION_PREFIX}OLD",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=None,
        currency=GBP,
        broker="Testing",
    )
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "OLD", 10, 10, 0, -100, GBP),
            rename,
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["NEW"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert "spin-off$NEW" in report.calculation_log[SPIN_OFF_DAY]


def test_two_spin_offs_from_one_source_on_one_day_are_both_recorded() -> None:
    """Each spin-off gets its own entry, and each takes its share in turn."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
                "BAZ": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency=GBP
            ),
        ],
    )

    entries = report.calculation_log[SPIN_OFF_DAY]["spin-off$FOO"]
    # £100 -> £90 after BAR took a tenth, then £90 -> £81 after BAZ took a tenth.
    assert [e.new_pool_cost for e in entries] == [Decimal(90), Decimal(81)]
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert calculator.portfolio["BAZ"].amount == Decimal(9)
    assert calculator.portfolio["FOO"].amount == Decimal(81)


def _chain_calculator() -> CapitalGainsCalculator:
    """FOO spins BAR off, and BAR spins BAZ off, on the same day."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "BAR"}
    return CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
                "BAZ": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


def test_a_chain_of_spin_offs_on_one_day_is_applied_in_order() -> None:
    """BAZ must not be apportioned until BAR has received its shares from FOO.

    BAZ is bought before either spin-off, so it used to be processed first,
    while BAR was still empty, and took a share of nothing. FOO gives BAR a
    tenth of £100; BAR, worth the same as the BAZ it spins off, then gives
    BAZ half of that £10.
    """
    calculator = _chain_calculator()
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAZ", 5, 10, 0, -50, GBP),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(5)
    assert calculator.portfolio["BAZ"].amount == Decimal(55)


def test_a_chain_listed_out_of_order_is_refused() -> None:
    """BAR spinning BAZ off before FOO has spun BAR off cannot be worked out.

    The first pass goes by the order the input lists same-day rows in, and
    here it would apportion a BAR that has no shares yet.
    """
    with pytest.raises(
        CalculationError, match=r"(?i)list the spin-off that creates BAR"
    ):
        get_report(
            _chain_calculator(),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency=GBP
                ),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
                ),
            ],
        )


def test_sibling_spin_offs_are_applied_in_event_order() -> None:
    """Two spin-offs from FOO take their shares in the order they happened.

    BAR's comes first, so BAR takes a tenth of £100 and BAZ a tenth of the
    £90 left. BAZ was also bought earlier in the day, which put it first in
    the day's acquisitions, and it used to take its share first: £9 to BAR
    and £10 to BAZ, the right total in the wrong holdings.
    """
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
                "BAZ": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAZ", 5, 10, 0, -50, GBP),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["FOO"].amount == Decimal(81)
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert calculator.portfolio["BAZ"].amount == Decimal(59)


def test_rows_for_one_spin_off_from_two_brokers_are_one_event() -> None:
    """A holding split across brokers reports one spin-off as one row each.

    Worked out row by row, each row set the whole FOO holding against only
    its own five BAR, so each took a smaller share and the second took it
    from what the first had left: £10.25 to BAR instead of £10.
    """
    calculator, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 5, 10, 0, currency=GBP
            ),
        ],
        5,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert calculator.portfolio["BAR"].quantity == Decimal(10)
    # One reorganisation, one entry.
    assert len(report.calculation_log[SPIN_OFF_DAY]["spin-off$FOO"]) == 1


def test_a_rename_of_another_symbol_on_the_day_is_ignored() -> None:
    """Only a rename of the source itself changes where its pool is looked up."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, PRICES),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    rename = BrokerTransaction(
        date=SPIN_OFF_DAY,
        action=ActionType.RENAME,
        symbol="NEW",
        description=f"{RENAME_DESCRIPTION_PREFIX}OLD",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=None,
        currency=GBP,
        broker="Testing",
    )
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "OLD", 3, 7, 0, -21, GBP),
            rename,
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert calculator.portfolio["NEW"].amount == Decimal(21)


def _rename(old: str, new: str) -> BrokerTransaction:
    return BrokerTransaction(
        date=SPIN_OFF_DAY,
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


@pytest.mark.parametrize("backwards", [False, True], ids=["in order", "backwards"])
def test_a_source_renamed_twice_on_the_day_is_refused(*, backwards: bool) -> None:
    """A spin-off must not bypass chain validation in either rename row order."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "NEW"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {"NEW": {SPIN_OFF_DAY: Decimal(90)}, "BAR": {SPIN_OFF_DAY: Decimal(10)}},
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    renames = [_rename("OLD", "MID"), _rename("MID", "NEW")]

    with pytest.raises(
        CalculationError,
        match="is renamed to MID, and MID is renamed to NEW, the same day",
    ):
        get_report(
            calculator,
            [
                transaction(BUY_DAY, ActionType.BUY, "OLD", 10, 10, 0, -100, GBP),
                *(reversed(renames) if backwards else renames),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
                ),
            ],
        )


@pytest.mark.parametrize(
    ("renames", "reason"),
    [
        pytest.param(
            [("A", "NEW"), ("B", "NEW")], "separate holdings", id="two-into-one"
        ),
        pytest.param(
            [("NEW", "B"), ("B", "NEW")],
            "is renamed to NEW, and NEW is renamed to B",
            id="circle",
        ),
    ],
)
def test_a_rename_graph_with_no_single_pool_is_refused(
    renames: list[tuple[str, str]], reason: str
) -> None:
    """Two symbols renamed into one, or renames that loop, leave no pool to apportion."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "NEW"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {"NEW": {SPIN_OFF_DAY: Decimal(90)}, "BAR": {SPIN_OFF_DAY: Decimal(10)}},
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    first_names = {old for old, _ in renames} - {new for _, new in renames}
    with pytest.raises(CalculationError, match=reason):
        get_report(
            calculator,
            [
                *(
                    transaction(BUY_DAY, ActionType.BUY, name, 10, 10, 0, -100, GBP)
                    for name in sorted(first_names or {"NEW"})
                ),
                *(_rename(old, new) for old, new in renames),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
                ),
            ],
        )


def _renaming_calculator(cache: dict[str, str]) -> CapitalGainsCalculator:
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = cache
    return CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "NEW": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


@pytest.mark.parametrize(
    "old_holding",
    [
        pytest.param(
            [transaction(BUY_DAY, ActionType.BUY, "OLD", 10, 10, 0, -100, GBP)],
            id="shares",
        ),
        pytest.param(
            # Sold out, then charged a fee: cost with no shares, which the
            # rename merges into NEW all the same.
            [
                transaction(BUY_DAY, ActionType.BUY, "OLD", 1, 10, 0, -10, GBP),
                transaction(BUY_DAY, ActionType.SELL, "OLD", 1, 10, 0, 10, GBP),
                transaction(
                    datetime.date(2024, 6, 2),
                    ActionType.FEE,
                    "OLD",
                    None,
                    None,
                    0,
                    -10,
                    GBP,
                ),
            ],
            id="cost-only-origin",
        ),
    ],
)
def test_a_rename_into_a_holding_already_held_is_refused(
    old_holding: list[BrokerTransaction],
) -> None:
    """OLD renamed to a NEW that already has a pool: two holdings became one.

    Apportioning NEW's pool alone assumes the spin-off came before the
    rename, and the input carries no order. With shares in OLD that gave
    NEW £181.82 and BAR £18.18 where the combined pool gives £180 and £20;
    with only a fee's cost in OLD it gave £100 and £10 where the combined
    pool gives £99 and £11.
    """
    with pytest.raises(CalculationError, match="separate holdings"):
        get_report(
            _renaming_calculator({"BAR": "NEW"}),
            [
                *old_holding,
                transaction(BUY_DAY, ActionType.BUY, "NEW", 10, 10, 0, -100, GBP),
                _rename("OLD", "NEW"),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 20, 10, 0, currency=GBP
                ),
            ],
        )


def test_a_rename_whose_origin_holds_nothing_uses_the_target_pool() -> None:
    """The pool is wherever the shares are, not necessarily the first name."""
    calculator = _renaming_calculator({"BAR": "NEW"})
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "NEW", 10, 10, 0, -100, GBP),
            _rename("OLD", "NEW"),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["NEW"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_renames_of_unrelated_symbols_into_one_name_do_not_block_a_spin_off() -> None:
    """A and B both renamed to X that day has nothing to do with FOO."""
    calculator = _renaming_calculator({"BAR": "FOO"})
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(BUY_DAY, ActionType.BUY, "A", 1, 1, 0, -1, GBP),
            transaction(BUY_DAY, ActionType.BUY, "B", 1, 1, 0, -1, GBP),
            _rename("A", "X"),
            _rename("B", "X"),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_a_rename_from_an_origin_never_held_is_no_merge() -> None:
    """A and EMPTY both renamed to NEW, but only A ever held anything.

    One real pool, so nothing is ambiguous; refusing it was safe but
    needless.
    """
    calculator = _renaming_calculator({"BAR": "NEW"})
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "A", 10, 10, 0, -100, GBP),
            _rename("A", "NEW"),
            _rename("EMPTY", "NEW"),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
            ),
        ],
    )

    assert calculator.portfolio["NEW"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_a_chain_out_of_order_is_refused_before_prices_are_needed() -> None:
    """The clear refusal, not a missing-price error, when both would apply."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "BAR"}
    # No BAZ price at all: a chain in the right order would fail on that.
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, PRICES),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    with pytest.raises(
        CalculationError, match=r"(?i)list the spin-off that creates BAR"
    ):
        get_report(
            calculator,
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency=GBP
                ),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
                ),
            ],
        )


def test_a_chain_out_of_order_from_a_holding_already_held_is_refused() -> None:
    """BAR held from before, so its own row passes; the later row catches it.

    With shares already in BAR, the first row is worked out on them as if
    that were the whole holding, and only when FOO's spin-off into BAR is
    read does it show the two were out of order.
    """
    with pytest.raises(
        CalculationError, match=r"(?i)list the spin-off that creates BAR"
    ):
        get_report(
            _chain_calculator(),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(BUY_DAY, ActionType.BUY, "BAR", 2, 10, 0, -20, GBP),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency=GBP
                ),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency=GBP
                ),
            ],
        )
