"""Tests for no gain/no loss transfers to a spouse or civil partner.

Covers the RAW TRANSFER_TO_SPOUSE action, how a transfer is identified
against the transferor's own acquisitions, and the broker gift rows that
have to be classified before anything can be calculated.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import (
    CalculationError,
    InvalidTransactionError,
    PriceMissingError,
    QuantityNotPositiveError,
    UnclassifiedGiftError,
)
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, RuleType
from cgt_calc.parsers.broker_registry import _transaction_sort_key
from cgt_calc.parsers.raw import RawParser
from cgt_calc.spin_off_handler import SpinOffHandler

from .calc_test_data import (
    GBP,
    split_transaction,
    transaction,
    transfer_to_spouse_transaction,
)
from .test_calc import _gbp_trade, _rename_transaction, create_calculator, get_report

if TYPE_CHECKING:
    from pathlib import Path


def test_transfer_to_spouse_from_pool_is_no_gain_no_loss() -> None:
    """A transfer to spouse leaves the Section 104 pool at base cost with nil gain."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                buy_day, ActionType.BUY, "BAR", 5, 5, 0, -25, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 4),
            transfer_to_spouse_transaction(transfer_day, "BAR", 2),
        ],
    )

    # Not a taxable disposal.
    assert report.total_gain() == Decimal(0)
    assert report.disposal_count == 0
    assert report.disposal_proceeds == Decimal(0)

    # Pool reduced by 4 units at the £10 average cost.
    assert calculator.portfolio["FOO"].quantity == Decimal(6)
    assert calculator.portfolio["FOO"].amount == Decimal(60)

    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert all(e.rule_type is RuleType.TRANSFER_TO_SPOUSE for e in entries)
    assert sum((e.quantity for e in entries), Decimal(0)) == Decimal(4)
    assert sum((e.gain for e in entries), Decimal(0)) == Decimal(0)
    # £40 base cost passes to the recipient (4 units * £10 average).
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(40)

    # The second symbol transferred that day is handled independently.
    bar = report.calculation_log[transfer_day]["transfer-to-spouse$BAR"]
    assert sum((e.allowable_cost for e in bar), Decimal(0)) == Decimal(10)
    assert calculator.portfolio["BAR"].quantity == Decimal(3)


def test_transfer_to_spouse_matches_repurchase_within_30_days() -> None:
    """A purchase within 30 days after a transfer is matched first (s106A).

    The base cost passing to the spouse is then the cost of the re-purchased
    shares, not the Section 104 average - the case a plain pool reduction gets
    wrong.
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    rebuy_day = datetime.date(2024, 6, 15)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 5),
            transaction(
                rebuy_day, ActionType.BUY, "FOO", 5, 12, 0, -60, CurrencyCode(GBP)
            ),
        ],
    )

    assert report.total_gain() == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    # Matched to the £12 re-purchase, so base cost is £60 not the £50 average.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(60)
    # The original 10 @ £10 pool is left untouched by the transfer.
    assert calculator.portfolio["FOO"].quantity == Decimal(10)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_transfer_to_spouse_entire_holding_empties_pool() -> None:
    """Transferring the whole holding removes it from the portfolio with nil gain."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 10),
        ],
    )

    assert report.total_gain() == Decimal(0)
    assert calculator.portfolio["FOO"].quantity == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(100)


def test_transfer_to_spouse_matches_same_day_acquisition() -> None:
    """A purchase on the transfer day is matched first (same day rule).

    The base cost passing to the spouse is then the cost of the shares bought
    that day, not the Section 104 average.
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                transfer_day, ActionType.BUY, "FOO", 3, 20, 0, -60, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 3),
        ],
    )

    assert report.total_gain() == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    # Matched to the £20 same-day purchase, so base cost is £60 not the £10 average.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(60)
    # The original 10 @ £10 pool is left untouched by the transfer.
    assert calculator.portfolio["FOO"].quantity == Decimal(10)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


@pytest.mark.parametrize(
    "acquisition_day",
    [
        pytest.param(datetime.date(2024, 6, 10), id="same-day"),
        pytest.param(datetime.date(2024, 7, 9), id="within-30-days"),
    ],
)
def test_transfer_to_spouse_same_day_as_sale_is_rejected(
    acquisition_day: datetime.date,
) -> None:
    """A sale and a transfer competing for the same acquisitions is unsupported."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(CalculationError, match="does not say how to split") as err:
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transaction(
                    event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
                ),
                transfer_to_spouse_transaction(event_day, "FOO", 2),
                transaction(
                    acquisition_day,
                    ActionType.BUY,
                    "FOO",
                    4,
                    30,
                    0,
                    -120,
                    CurrencyCode(GBP),
                ),
            ],
        )

    # The message names the clash concretely so it can be acted on.
    message = str(err.value)
    assert "sold 3 units of FOO and transferred 2" in message
    assert str(acquisition_day) in message


def test_transfer_to_spouse_same_day_as_sale_from_pool_is_allowed() -> None:
    """A sale and a transfer both drawing on the pool alone are unambiguous.

    Neither can be identified against an acquisition, so both take the same
    Section 104 average cost and the order between them cannot matter.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
        ],
    )

    # Only the sale is taxable: £60 proceeds against a £30 allowable cost.
    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(60)
    assert report.total_gain() == Decimal(30)

    entries = report.calculation_log[event_day]["transfer-to-spouse$FOO"]
    # The transfer takes the same £10 average, so £20 passes to the recipient.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)
    assert calculator.portfolio["FOO"].quantity == Decimal(5)
    assert calculator.portfolio["FOO"].amount == Decimal(50)


def test_transfer_to_spouse_more_than_owned_is_rejected() -> None:
    """Transferring more units than held is refused."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(InvalidTransactionError, match="more than the available"):
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transfer_to_spouse_transaction(transfer_day, "FOO", 11),
            ],
        )


def test_transfer_to_spouse_of_not_owned_symbol_is_rejected() -> None:
    """Transferring a symbol that was never acquired is refused."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(InvalidTransactionError, match="not owned symbol"):
        get_report(
            calculator,
            [transfer_to_spouse_transaction(datetime.date(2024, 6, 10), "FOO", 1)],
        )


def test_transfer_to_spouse_warns_about_the_ignored_price(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gift has no consideration, so any price on the row is ignored."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with caplog.at_level(logging.WARNING, logger="cgt_calc.ingestion"):
        report = get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transaction(
                    transfer_day,
                    ActionType.TRANSFER_TO_SPOUSE,
                    "FOO",
                    4,
                    25,
                    0,
                    0,
                    CurrencyCode(GBP),
                ),
            ],
        )

    assert "Ignoring the price" in caplog.text
    # Still the pool average, not the £25 that was in the price column.
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(40)


def test_transfer_to_spouse_fee_is_added_to_the_base_cost() -> None:
    """A fee is an incidental cost of the disposal, so the recipient inherits it.

    s58 fixes the consideration at the amount giving neither gain nor loss, and
    that amount has to cover the fee, so the fee ends up in the base cost the
    recipient takes on (TCGA 1992 s38(2), CG15250).
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                transfer_day,
                ActionType.TRANSFER_TO_SPOUSE,
                "FOO",
                4,
                None,
                5,
                -5,
                CurrencyCode(GBP),
            ),
        ],
    )

    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    # £40 of pool cost plus the £5 fee.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(45)
    # Still no gain to the transferor.
    assert sum((e.gain for e in entries), Decimal(0)) == Decimal(0)
    assert report.total_gain() == Decimal(0)
    # The fee does not come out of the pool the transferor keeps.
    assert calculator.portfolio["FOO"].amount == Decimal(60)


def test_transfer_to_spouse_shown_in_report() -> None:
    """The text report lists each transfer with the base cost passed on."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 1000, 10, 0, -10000, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 400),
        ],
    )

    output = str(report)
    assert "Transferred to spouse" in output
    # Quantities are shown as entered, like the PDF, so fractions survive.
    assert f"{transfer_day}: FOO 400 units, base cost £4,000.00" in output


def test_unclassified_gift_is_rejected_with_instructions() -> None:
    """A broker gift with no classification refuses and says how to fix it."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(UnclassifiedGiftError) as err:
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transaction(
                    gift_day,
                    ActionType.UNCLASSIFIED_GIFT,
                    "FOO",
                    4,
                    currency=CurrencyCode(GBP),
                ),
            ],
        )

    message = str(err.value)
    # The exact RAW line to paste, so the fix is mechanical.
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,4,0.00,0.00,GBP" in message
    # Both outcomes are named, so a non-spouse gift is not quietly treated as one.
    assert "spouse or civil partner" in message
    assert "disposal at market value" in message


def test_gift_matched_by_transfer_to_spouse_is_accounted_for() -> None:
    """A matching transfer row classifies the gift, which is then not counted twice."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                gift_day,
                ActionType.UNCLASSIFIED_GIFT,
                "FOO",
                4,
                currency=CurrencyCode(GBP),
            ),
            transfer_to_spouse_transaction(gift_day, "FOO", 4),
        ],
    )

    assert report.total_gain() == Decimal(0)
    # Four units left once, not twice: the broker row is redundant, not extra.
    assert calculator.portfolio["FOO"].quantity == Decimal(6)
    assert calculator.portfolio["FOO"].amount == Decimal(60)
    entries = report.calculation_log[gift_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(40)


def test_gift_with_mismatched_transfer_is_rejected() -> None:
    """A transfer row that does not match the gift does not classify it."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(UnclassifiedGiftError):
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transaction(
                    gift_day,
                    ActionType.UNCLASSIFIED_GIFT,
                    "FOO",
                    4,
                    currency=CurrencyCode(GBP),
                ),
                # Same day and symbol, different quantity.
                transfer_to_spouse_transaction(gift_day, "FOO", 3),
            ],
        )


def test_transfer_to_spouse_without_quantity_is_rejected() -> None:
    """A transfer row has to say how many units moved."""
    buy_day = datetime.date(2024, 6, 1)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(QuantityNotPositiveError):
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transfer_to_spouse_transaction(datetime.date(2024, 6, 10), "FOO", 0),
            ],
        )


def test_gift_without_quantity_is_rejected() -> None:
    """A gift row with no quantity is refused before asking who received it."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(QuantityNotPositiveError):
        get_report(
            calculator,
            [
                transaction(
                    datetime.date(2024, 6, 10),
                    ActionType.UNCLASSIFIED_GIFT,
                    "FOO",
                    0,
                    currency=CurrencyCode(GBP),
                )
            ],
        )


def test_transfer_to_spouse_reserves_its_share_of_a_same_day_acquisition() -> None:
    """An earlier sale cannot bed-and-breakfast shares a later transfer will claim.

    The transfer is same-day with the repurchase, and the same-day rule beats
    bed and breakfast, so the sale has to leave those shares alone or they get
    counted twice.
    """
    buy_day = datetime.date(2024, 6, 1)
    sell_day = datetime.date(2024, 6, 10)
    rebuy_day = datetime.date(2024, 6, 15)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                sell_day, ActionType.SELL, "FOO", 5, 20, 0, 100, CurrencyCode(GBP)
            ),
            transaction(
                rebuy_day, ActionType.BUY, "FOO", 5, 30, 0, -150, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(rebuy_day, "FOO", 5),
        ],
    )

    # The sale falls through to the pool at £10 rather than matching the £30
    # repurchase, so the gain is £100 - £50 rather than £100 - £150.
    assert report.total_gain() == Decimal(50)
    sale = report.calculation_log[sell_day]["sell$FOO"]
    assert all(e.rule_type is RuleType.SECTION_104 for e in sale)
    # The transfer takes the repurchase under the same-day rule.
    entries = report.calculation_log[rebuy_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(150)
    assert calculator.portfolio["FOO"].quantity == Decimal(5)
    assert calculator.portfolio["FOO"].amount == Decimal(50)


def test_transfer_to_spouse_after_a_spin_off_takes_the_apportioned_cost() -> None:
    """A spin-off before the transfer has already moved part of the cost away.

    FOO keeps 90% of its £1,000 at the spin-off, so the 50 units transferred
    carry £450, and the spin-off is recorded under its own date.
    """
    buy_day = datetime.date(2024, 4, 10)
    spin_off_day = datetime.date(2024, 5, 5)
    transfer_day = datetime.date(2024, 6, 10)
    currency_converter = CurrencyConverter(None, {})
    spin_off_handler = SpinOffHandler()
    spin_off_handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(
            currency_converter,
            {},
            {
                "FOO": {spin_off_day: Decimal(90)},
                "BAR": {spin_off_day: Decimal(10)},
            },
        ),
        spin_off_handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 100, 10, 0, -1000, CurrencyCode(GBP)
            ),
            transaction(
                spin_off_day,
                ActionType.SPIN_OFF,
                "BAR",
                100,
                10,
                0,
                currency=CurrencyCode(GBP),
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 50),
        ],
    )

    assert report.total_gain() == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(450)
    (spin_off_entry,) = report.calculation_log[spin_off_day]["spin-off$FOO"]
    assert spin_off_entry.rule_type is RuleType.SPIN_OFF
    assert spin_off_entry.new_pool_cost == Decimal(900)
    assert calculator.portfolio["BAR"].amount == Decimal(100)


def test_transfer_to_spouse_before_the_tax_year_still_reduces_the_pool() -> None:
    """A historical transfer shapes the pool without appearing in the report.

    The whole history is walked to build the pool, but only events inside the
    reported period belong in the calculations.
    """
    buy_day = datetime.date(2022, 5, 1)
    transfer_day = datetime.date(2022, 6, 10)
    sell_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 4),
            transaction(
                sell_day, ActionType.SELL, "FOO", 6, 20, 0, 120, CurrencyCode(GBP)
            ),
        ],
    )

    assert transfer_day not in report.calculation_log
    # The pool lost 4 units at £10, so the later sale has a £60 allowable cost.
    assert report.disposal_count == 1
    assert report.total_gain() == Decimal(60)


def test_disposal_is_not_matched_against_a_same_day_split() -> None:
    """A split is not an acquisition, so it cannot be matched on the same day.

    TCGA 1992 s127 treats the new shares as acquired when the original ones
    were (CG51805). Matching against them would give the disposal their nil
    cost and leave the whole pool cost behind on fewer shares.
    """
    buy_day = datetime.date(2024, 6, 1)
    split_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            split_transaction(split_day, "FOO", 10),
            transaction(
                split_day, ActionType.SELL, "FOO", 4, 12, 0, 48, CurrencyCode(GBP)
            ),
        ],
    )

    entries = report.calculation_log[split_day]["sell$FOO"]
    assert all(e.rule_type is RuleType.SECTION_104 for e in entries)
    # 20 units hold £100, so 4 of them cost £20, not £0.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)
    assert report.total_gain() == Decimal(28)
    assert calculator.portfolio["FOO"].amount == Decimal(80)


def test_transfer_to_spouse_is_not_matched_against_a_same_day_split() -> None:
    """The same, for a transfer: the recipient must not inherit a nil base cost."""
    buy_day = datetime.date(2024, 6, 1)
    split_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            split_transaction(split_day, "FOO", 10),
            transfer_to_spouse_transaction(split_day, "FOO", 4),
        ],
    )

    entries = report.calculation_log[split_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)
    assert calculator.portfolio["FOO"].quantity == Decimal(16)
    assert calculator.portfolio["FOO"].amount == Decimal(80)


@pytest.mark.parametrize("confirmed", [Decimal(2), Decimal(40)])
def test_either_reading_of_an_ambiguous_gift_settles_it(confirmed: Decimal) -> None:
    """The row the user confirms settles the share count as well as who got them.

    A gift printed before a split could state either count, so the tool cannot
    pick. Whichever the user confirms from a statement is accepted.
    """
    gift_day = datetime.date(2024, 6, 10)
    gift = BrokerTransaction(
        date=gift_day,
        action=ActionType.UNCLASSIFIED_GIFT,
        symbol="FOO",
        description="",
        quantity=Decimal(2),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode(GBP),
        broker="Testing",
        ambiguous_quantity=Decimal(40),
    )
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                100,
                10,
                0,
                -1000,
                CurrencyCode(GBP),
            ),
            gift,
            transfer_to_spouse_transaction(gift_day, "FOO", float(confirmed)),
        ],
    )

    entries = report.calculation_log[gift_day]["transfer-to-spouse$FOO"]
    assert sum((e.quantity for e in entries), Decimal(0)) == confirmed
    assert calculator.portfolio["FOO"].quantity == Decimal(100) - confirmed


def test_ambiguous_gift_error_offers_both_readings() -> None:
    """Unresolved, the error shows both rows rather than guessing one."""
    gift_day = datetime.date(2024, 6, 10)
    gift = BrokerTransaction(
        date=gift_day,
        action=ActionType.UNCLASSIFIED_GIFT,
        symbol="FOO",
        description="",
        quantity=Decimal(2),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode(GBP),
        broker="Testing",
        ambiguous_quantity=Decimal(40),
    )
    with pytest.raises(UnclassifiedGiftError) as err:
        get_report(create_calculator(tax_year=2024, balance_check=False), [gift])

    message = str(err.value)
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,2,0.00,0.00,GBP" in message
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,40,0.00,0.00,GBP" in message


def test_a_certain_gift_is_not_stranded_by_an_uncertain_one() -> None:
    """A gift with one possible count must not lose its row to one with two.

    Taking rows in input order let the ambiguous gift claim the 2 that the
    unambiguous one had no alternative to, and the run failed with both rows
    present and correct.
    """
    gift_day = datetime.date(2024, 6, 10)

    def gift(quantity: int, ambiguous: Decimal | None = None) -> BrokerTransaction:
        return BrokerTransaction(
            date=gift_day,
            action=ActionType.UNCLASSIFIED_GIFT,
            symbol="FOO",
            description="",
            quantity=Decimal(quantity),
            price=None,
            fees=Decimal(0),
            amount=Decimal(0),
            currency=CurrencyCode(GBP),
            broker="Testing",
            ambiguous_quantity=ambiguous,
        )

    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                100,
                10,
                0,
                -1000,
                CurrencyCode(GBP),
            ),
            gift(2, Decimal(40)),
            gift(2),
            transfer_to_spouse_transaction(gift_day, "FOO", 40),
            transfer_to_spouse_transaction(gift_day, "FOO", 2),
        ],
    )

    assert report.total_gain() == Decimal(0)
    # Both gifts accounted for: 40 and 2 left the pool of 100.
    assert calculator.portfolio["FOO"].quantity == Decimal(58)


def _gift(
    day: datetime.date, quantity: int, ambiguous: Decimal | None = None
) -> BrokerTransaction:
    return BrokerTransaction(
        date=day,
        action=ActionType.UNCLASSIFIED_GIFT,
        symbol="FOO",
        description="",
        quantity=Decimal(quantity),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode(GBP),
        broker="Testing",
        ambiguous_quantity=ambiguous,
    )


def test_two_uncertain_gifts_are_read_the_way_that_fits_both() -> None:
    """Which reading each gift takes is settled for the day, not one by one.

    Read first come, first served, the 20-or-400 gift takes the 20 row and
    the 1-or-20 gift is stranded, although 400 and 20 fit them perfectly.
    """
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                1000,
                10,
                0,
                -10000,
                CurrencyCode(GBP),
            ),
            _gift(gift_day, 20, Decimal(400)),
            _gift(gift_day, 1, Decimal(20)),
            transfer_to_spouse_transaction(gift_day, "FOO", 400),
            transfer_to_spouse_transaction(gift_day, "FOO", 20),
        ],
    )

    assert report.total_gain() == Decimal(0)
    assert calculator.portfolio["FOO"].quantity == Decimal(580)


def test_gifts_no_reading_can_satisfy_still_name_the_one_left_out() -> None:
    """When no way of reading the day fits, the gift without a row is named."""
    gift_day = datetime.date(2024, 6, 10)
    with pytest.raises(UnclassifiedGiftError) as err:
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(
                    datetime.date(2024, 6, 1),
                    ActionType.BUY,
                    "FOO",
                    1000,
                    10,
                    0,
                    -10000,
                    CurrencyCode(GBP),
                ),
                _gift(gift_day, 20, Decimal(400)),
                _gift(gift_day, 20, Decimal(400)),
                # One row between two gifts that both need one.
                transfer_to_spouse_transaction(gift_day, "FOO", 20),
            ],
        )

    message = str(err.value)
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,20,0.00,0.00,GBP" in message
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,400,0.00,0.00,GBP" in message


def test_each_gift_needs_its_own_classification() -> None:
    """Two gifts of the same shares on one day are not settled by one row."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    with pytest.raises(UnclassifiedGiftError):
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, CurrencyCode(GBP)
                ),
                transaction(
                    gift_day,
                    ActionType.UNCLASSIFIED_GIFT,
                    "FOO",
                    4,
                    currency=CurrencyCode(GBP),
                ),
                transaction(
                    gift_day,
                    ActionType.UNCLASSIFIED_GIFT,
                    "FOO",
                    4,
                    currency=CurrencyCode(GBP),
                ),
                transfer_to_spouse_transaction(gift_day, "FOO", 4),
            ],
        )


def test_two_gifts_are_settled_by_two_classifications() -> None:
    """Matching one row per gift accounts for both."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, CurrencyCode(GBP)
            ),
            transaction(
                gift_day,
                ActionType.UNCLASSIFIED_GIFT,
                "FOO",
                4,
                currency=CurrencyCode(GBP),
            ),
            transaction(
                gift_day,
                ActionType.UNCLASSIFIED_GIFT,
                "FOO",
                4,
                currency=CurrencyCode(GBP),
            ),
            transfer_to_spouse_transaction(gift_day, "FOO", 4),
            transfer_to_spouse_transaction(gift_day, "FOO", 4),
        ],
    )

    assert report.total_gain() == Decimal(0)
    # Both transfers left the pool: 20 - 8.
    assert calculator.portfolio["FOO"].quantity == Decimal(12)
    assert calculator.portfolio["FOO"].amount == Decimal(120)


def test_transfer_to_spouse_sorts_after_a_same_day_acquisition() -> None:
    """A hand-written transfer must not be read before the buy it depends on.

    Transfers come from a RAW file while the shares come from a broker export,
    and parsers merge in registry order, so without this the transfer is
    validated first and fails as "not owned".
    """
    day = datetime.date(2024, 6, 10)
    buy = transaction(day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP))
    transfer = transfer_to_spouse_transaction(day, "FOO", 4)

    # Worst case: the transfer is read first, as a RAW file would give it.
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        sorted([transfer, buy], key=_transaction_sort_key),
    )

    entries = report.calculation_log[day]["transfer-to-spouse$FOO"]
    assert sum((e.quantity for e in entries), Decimal(0)) == Decimal(4)


def test_management_fee_is_not_a_contended_acquisition() -> None:
    """A fee buys no shares, so it cannot be something two disposals fight over.

    Fees are recorded in the acquisition list with a cost and no quantity, so
    a naive "has a cost" test mistakes one for a purchase.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    fee_day = datetime.date(2024, 6, 20)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
            transaction(
                fee_day, ActionType.FEE, "FOO", None, None, 0, -1, CurrencyCode(GBP)
            ),
        ],
    )

    assert report.total_gain() == Decimal(30)
    entries = report.calculation_log[event_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)


def test_fractional_transfer_keeps_its_units_in_the_report() -> None:
    """Rounding to two places would report a real transfer as zero units."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 0.001),
        ],
    )

    assert "FOO 0.001 units" in str(report)


def test_split_shares_do_not_dilute_a_same_day_purchase() -> None:
    """A split on the day of a purchase must not spread its cost over free shares.

    Both land in the acquisition list for the day, so treating the total as one
    acquisition prices the purchase across shares that cost nothing.
    """
    day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                10,
                10,
                0,
                -100,
                CurrencyCode(GBP),
            ),
            split_transaction(day, "FOO", 10),
            transaction(day, ActionType.BUY, "FOO", 5, 20, 0, -100, CurrencyCode(GBP)),
            transfer_to_spouse_transaction(day, "FOO", 5),
        ],
    )

    entries = report.calculation_log[day]["transfer-to-spouse$FOO"]
    # The five shares bought that day cost £100, and that is what is passed on.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(100)
    # 10 original plus 10 free shares, still holding the original £100.
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_transfer_reservation_survives_a_split_before_the_repurchase() -> None:
    """Reservations are counted in the acquisition's units, not the disposal's.

    A split between an earlier sale and the repurchase means the two are
    counted differently; subtracting one from the other unconverted drove the
    available quantity negative.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                10,
                10,
                0,
                -100,
                CurrencyCode(GBP),
            ),
            transaction(
                datetime.date(2024, 6, 5),
                ActionType.SELL,
                "FOO",
                4,
                15,
                0,
                60,
                CurrencyCode(GBP),
            ),
            split_transaction(datetime.date(2024, 6, 8), "FOO", 6),
            transaction(
                datetime.date(2024, 6, 12),
                ActionType.BUY,
                "FOO",
                10,
                5,
                0,
                -50,
                CurrencyCode(GBP),
            ),
            transfer_to_spouse_transaction(datetime.date(2024, 6, 12), "FOO", 6),
        ],
    )

    assert report.disposal_count == 1
    assert report.total_gain() == Decimal(20)


def test_fully_matched_acquisition_is_not_contended() -> None:
    """An acquisition its own day's sale consumes cannot reach an earlier day."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    later = datetime.date(2024, 6, 15)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, CurrencyCode(GBP)
            ),
            transaction(
                event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
            transaction(
                later, ActionType.BUY, "FOO", 4, 30, 0, -120, CurrencyCode(GBP)
            ),
            transaction(
                later, ActionType.SELL, "FOO", 4, 31, 0, 124, CurrencyCode(GBP)
            ),
        ],
    )

    # £30 on the 10th (3 units at £10 cost) plus £4 on the 15th.
    assert report.total_gain() == Decimal(34)


def test_acquisition_an_earlier_disposal_took_is_not_contended() -> None:
    """A settled bed and breakfast match leaves nothing to argue over.

    The 5 June sale takes the whole 15 June purchase, so neither the sale nor
    the transfer on 10 June can reach it and both fall to the pool.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                20,
                10,
                0,
                -200,
                CurrencyCode(GBP),
            ),
            transaction(
                datetime.date(2024, 6, 5),
                ActionType.SELL,
                "FOO",
                4,
                15,
                0,
                60,
                CurrencyCode(GBP),
            ),
            transaction(
                datetime.date(2024, 6, 10),
                ActionType.SELL,
                "FOO",
                3,
                20,
                0,
                60,
                CurrencyCode(GBP),
            ),
            transfer_to_spouse_transaction(datetime.date(2024, 6, 10), "FOO", 2),
            transaction(
                datetime.date(2024, 6, 15),
                ActionType.BUY,
                "FOO",
                4,
                30,
                0,
                -120,
                CurrencyCode(GBP),
            ),
        ],
    )

    # £60 loss bed-and-breakfasted on 5 June, £30 gain from the pool on the 10th.
    assert report.total_gain() == Decimal(-30)
    entries = report.calculation_log[datetime.date(2024, 6, 10)][
        "transfer-to-spouse$FOO"
    ]
    assert all(e.rule_type is RuleType.TRANSFER_TO_SPOUSE for e in entries)


def test_same_day_sale_and_transfer_after_a_split_is_allowed() -> None:
    """A split is not an acquisition a sale and a transfer can contend over.

    It hands over shares for free, so the B&B rule skips it and neither
    disposal can be identified against it. Both fall through to the pool.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    split_day = datetime.date(2024, 6, 15)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transaction(
                event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
            split_transaction(split_day, "FOO", 5),
        ],
    )

    entries = report.calculation_log[event_day]["transfer-to-spouse$FOO"]
    # Straight out of the pool at the £10 average.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)


def test_same_day_sale_and_transfer_message_caps_the_dates_it_lists() -> None:
    """Too many contended acquisitions are summarised rather than all listed."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    rebuys = [
        transaction(
            event_day + datetime.timedelta(days=offset),
            ActionType.BUY,
            "FOO",
            1,
            30,
            0,
            -30,
            CurrencyCode(GBP),
        )
        for offset in (1, 2, 3, 4)
    ]
    with pytest.raises(CalculationError) as err:
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
                ),
                transaction(
                    event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
                ),
                transfer_to_spouse_transaction(event_day, "FOO", 2),
                *rebuys,
            ],
        )

    message = str(err.value)
    assert "2024-06-11, 2024-06-12, 2024-06-13 and 1 more" in message


def test_transfer_fee_does_not_move_the_cash_balance() -> None:
    """A hand-written transfer row has no funded ledger to charge the fee to.

    RAW rows come in under their own broker, so charging the fee there drives
    that balance straight below zero and trips the balance check.
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    transfer = BrokerTransaction(
        date=transfer_day,
        action=ActionType.TRANSFER_TO_SPOUSE,
        symbol="FOO",
        description="",
        quantity=Decimal(4),
        price=Decimal(0),
        fees=Decimal(5),
        amount=Decimal(-5),
        currency=CurrencyCode(GBP),
        broker="Unknown",
    )
    calculator = create_calculator(tax_year=2024)
    report = get_report(
        calculator,
        [
            transaction(
                buy_day,
                ActionType.TRANSFER,
                None,
                None,
                None,
                0,
                200,
                CurrencyCode(GBP),
            ),
            transaction(
                buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, CurrencyCode(GBP)
            ),
            transfer,
        ],
    )

    # The fee still reaches the recipient's base cost: £40 of pool plus £5.
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(45)


def test_transfer_from_spouse_enters_the_pool_at_the_given_cost() -> None:
    """The shares arrive at the base cost stated, with no cash paid for them.

    The balance check is on and the account is never funded: nothing was
    bought, so there is nothing for the check to find.
    """
    arrival = datetime.date(2024, 6, 15)
    sale = datetime.date(2024, 9, 1)
    calculator = create_calculator(tax_year=2024)
    report = get_report(
        calculator,
        [
            transaction(
                arrival,
                ActionType.TRANSFER_FROM_SPOUSE,
                "FOO",
                40,
                10,
                0,
                None,
                CurrencyCode(GBP),
            ),
            transaction(
                sale, ActionType.SELL, "FOO", 40, 20, 0, 800, CurrencyCode(GBP)
            ),
        ],
    )

    # £800 proceeds against the £400 the shares arrived with.
    assert report.disposal_count == 1
    assert report.allowable_costs == Decimal(400)
    assert report.total_gain() == Decimal(400)


def test_transfer_from_spouse_needs_the_cost() -> None:
    """The price column is the whole point of the row; it cannot be blank."""
    with pytest.raises(PriceMissingError):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(
                    datetime.date(2024, 6, 15),
                    ActionType.TRANSFER_FROM_SPOUSE,
                    "FOO",
                    40,
                    None,
                    0,
                    None,
                    CurrencyCode(GBP),
                ),
            ],
        )


def test_transfer_from_spouse_sorts_before_a_same_day_sale() -> None:
    """Shares sold on the day they arrive must be read as arriving first."""
    day = datetime.date(2024, 6, 15)
    sale = transaction(day, ActionType.SELL, "FOO", 40, 20, 0, 800, CurrencyCode(GBP))
    arrival = transaction(
        day,
        ActionType.TRANSFER_FROM_SPOUSE,
        "FOO",
        40,
        10,
        0,
        None,
        CurrencyCode(GBP),
    )

    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        sorted([sale, arrival], key=_transaction_sort_key),
    )

    assert report.total_gain() == Decimal(400)


def test_transferor_report_hands_over_the_recipients_row() -> None:
    """The text report prints the exact RAW row the recipient needs."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 1000, 10, 0, -10000, CurrencyCode(GBP)
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 400),
        ],
    )

    assert "2024-06-10,TRANSFER_FROM_SPOUSE,FOO,400,10,0.00,GBP" in str(report)


def test_hand_over_row_round_trips_through_the_raw_parser(tmp_path: Path) -> None:
    """What the transferor's report prints is exactly what the recipient reads.

    A pool of 15 shares costing £155 gives 3 of them a base cost of £31, and
    £31 over 3 shares does not terminate. The row still has to reproduce £31
    once parsed and pooled, with nothing rounded away on the way.
    """
    transfer_day = datetime.date(2024, 6, 10)
    sender = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(
                datetime.date(2024, 6, 1),
                ActionType.BUY,
                "FOO",
                10,
                10,
                0,
                -100,
                CurrencyCode(GBP),
            ),
            transaction(
                datetime.date(2024, 6, 2),
                ActionType.BUY,
                "FOO",
                5,
                11,
                0,
                -55,
                CurrencyCode(GBP),
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 3),
        ],
    )
    row = next(
        line.strip()
        for line in str(sender).splitlines()
        if "TRANSFER_FROM_SPOUSE" in line
    )
    raw_file = tmp_path / "transfers.csv"
    raw_file.write_text(f"date,action,symbol,quantity,price,fees,currency\n{row}\n")

    (arrival,) = RawParser().load_from_file(raw_file)
    recipient = get_report(
        create_calculator(tax_year=2024),
        [
            arrival,
            transaction(
                datetime.date(2024, 9, 1),
                ActionType.SELL,
                "FOO",
                3,
                20,
                0,
                60,
                CurrencyCode(GBP),
            ),
        ],
    )

    assert arrival.action is ActionType.TRANSFER_FROM_SPOUSE
    assert arrival.quantity == Decimal(3)
    assert recipient.allowable_costs == Decimal(31)
    assert recipient.total_gain() == Decimal(29)


def test_transfer_to_spouse_on_the_day_of_a_rename() -> None:
    """The pool is under the new name by the time a transfer is recorded.

    Transfers are the last thing a day does, after its renames have been
    applied, so the holding is already where the rename left it and the
    literal ticker on the row is the one that holds it.
    """
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "OLD", 100, 10, 0, -1000, CurrencyCode(GBP)
            ),
            _rename_transaction(rename_day, "OLD", "NEW"),
            transfer_to_spouse_transaction(rename_day, "NEW", 30),
        ],
    )

    (entry,) = report.calculation_log[rename_day]["transfer-to-spouse$NEW"]
    assert entry.rule_type is RuleType.TRANSFER_TO_SPOUSE
    assert entry.quantity == Decimal(30)
    assert entry.allowable_cost == Decimal(300)
    assert report.total_gain() == Decimal(0)
    assert calculator.portfolio["NEW"].quantity == Decimal(70)
    assert calculator.portfolio["NEW"].amount == Decimal(700)


@pytest.mark.parametrize("spelling", ["OLD", "NEW"])
def test_transfer_to_spouse_matches_the_day_s_purchase_across_a_rename(
    spelling: str,
) -> None:
    """The same-day rule reaches the purchase under the holding's other name.

    A transfer to a spouse is a disposal (TCGA 1992 s58) and is identified
    like one, so the day's purchase is matched first (s105(1)(a), CG51560).
    A rename recorded that day means the purchase can be written under either
    ticker, and the transfer under either, without changing what was
    transferred.
    """
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "OLD", 100, 10, 0, -1000, CurrencyCode(GBP)
            ),
            transaction(
                rename_day, ActionType.BUY, "OLD", 50, 15, 0, -750, CurrencyCode(GBP)
            ),
            # The first pass reads the day in row order, so a transfer under
            # the name a rename has yet to create has to follow that rename,
            # and one under the name it retires has to come first.
            *(
                [
                    _rename_transaction(rename_day, "OLD", "NEW"),
                    transfer_to_spouse_transaction(rename_day, spelling, 50),
                ]
                if spelling == "NEW"
                else [
                    transfer_to_spouse_transaction(rename_day, spelling, 50),
                    _rename_transaction(rename_day, "OLD", "NEW"),
                ]
            ),
        ],
    )

    (entry,) = report.calculation_log[rename_day][f"transfer-to-spouse${spelling}"]
    assert entry.rule_type is RuleType.TRANSFER_TO_SPOUSE
    assert entry.quantity == Decimal(50)
    # The day's purchase, not a slice of the whole 150-unit pool.
    assert entry.allowable_cost == Decimal(750)
    assert report.total_gain() == Decimal(0)
    assert calculator.portfolio["NEW"].quantity == Decimal(100)
    assert calculator.portfolio["NEW"].amount == Decimal(1000)


def test_a_sale_and_a_transfer_under_two_names_of_one_holding_are_refused() -> None:
    """A rename does not stop a sale and a transfer contending for a purchase.

    The sale is written under the old ticker and the transfer under the new
    one, but the day's rename makes them disposals of the same holding, both
    identified against the same purchase.
    """
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="does not say how to split") as err:
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "OLD", 100, 5, 0, -500, CurrencyCode(GBP)
                ),
                transaction(
                    rename_day,
                    ActionType.BUY,
                    "NEW",
                    30,
                    20,
                    0,
                    -600,
                    CurrencyCode(GBP),
                ),
                transaction(
                    rename_day,
                    ActionType.SELL,
                    "OLD",
                    25,
                    20,
                    0,
                    500,
                    CurrencyCode(GBP),
                ),
                _rename_transaction(rename_day, "OLD", "NEW"),
                transfer_to_spouse_transaction(rename_day, "NEW", 25),
            ],
        )

    message = str(err.value)
    assert "sold 25 units of NEW and transferred 25" in message
    assert (
        "The sale is recorded under OLD, which the day's renames make one "
        "holding with NEW." in message
    )


CONTENTION_BUY_DAY = datetime.date(2024, 6, 1)
CONTENTION_DAY = datetime.date(2024, 6, 10)
CONTENTION_LATER_DAY = datetime.date(2024, 6, 20)


def _contention_history(
    later_day_rows: list[BrokerTransaction],
) -> list[BrokerTransaction]:
    """Build a sale and a transfer of one holding, then a later rename day."""
    return [
        _gbp_trade(CONTENTION_BUY_DAY, ActionType.BUY, "X", 100, 500),
        _gbp_trade(CONTENTION_DAY, ActionType.SELL, "X", 25, 500),
        transfer_to_spouse_transaction(CONTENTION_DAY, "X", 25),
        *later_day_rows,
        _rename_transaction(CONTENTION_LATER_DAY, "X", "Z"),
    ]


def test_a_later_day_s_own_sale_leaves_nothing_to_contend_over() -> None:
    """A later day's purchase its own sale has taken is not in dispute.

    The 20 June purchase is written under the new ticker and the sale of it
    under the retired one, which the day's rename makes one holding. That
    sale takes the whole purchase under the same-day rule, so neither the
    sale nor the transfer of 10 June can be identified against it and there
    is nothing for them to argue over.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator,
        _contention_history(
            [
                _gbp_trade(CONTENTION_LATER_DAY, ActionType.BUY, "Z", 30, 600),
                _gbp_trade(CONTENTION_LATER_DAY, ActionType.SELL, "X", 30, 600),
            ]
        ),
    )

    (sale,) = report.calculation_log[CONTENTION_DAY]["sell$X"]
    (transfer,) = report.calculation_log[CONTENTION_DAY]["transfer-to-spouse$X"]
    # Both price from the pool at its 5 a share average, which is what neither
    # of them reaching the 20 a share purchase of 20 June looks like.
    assert sale.rule_type is RuleType.SECTION_104
    assert sale.allowable_cost == Decimal(125)
    assert transfer.allowable_cost == Decimal(125)
    assert report.total_gain() == Decimal(375)


def test_a_later_day_s_sale_leaves_the_rest_of_its_purchase_to_contend_over() -> None:
    """What that day's own sale does not take is still in dispute."""
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="does not say how to split") as err:
        get_report(
            calculator,
            _contention_history(
                [
                    _gbp_trade(CONTENTION_LATER_DAY, ActionType.BUY, "Z", 60, 1200),
                    _gbp_trade(CONTENTION_LATER_DAY, ActionType.SELL, "X", 30, 600),
                ]
            ),
        )

    assert str(CONTENTION_LATER_DAY) in str(err.value)


def test_a_later_day_s_transfer_to_a_spouse_reserves_its_purchase_too() -> None:
    """A transfer claims a later day's purchase exactly as a sale does."""
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator,
        _contention_history(
            [
                _gbp_trade(CONTENTION_LATER_DAY, ActionType.BUY, "Z", 30, 600),
                transfer_to_spouse_transaction(CONTENTION_LATER_DAY, "X", 30),
            ]
        ),
    )

    # The whole 20 a share purchase, not a share of the 5 a share pool.
    (later,) = report.calculation_log[CONTENTION_LATER_DAY]["transfer-to-spouse$X"]
    assert later.allowable_cost == Decimal(600)
    assert report.total_gain() == Decimal(375)


@pytest.mark.parametrize("purchase_spelling", ["X", "Z"])
def test_a_later_day_s_purchase_is_contended_under_either_of_its_names(
    purchase_spelling: str,
) -> None:
    """A rename that day does not put the purchase beyond one of the two.

    The 20 June purchase is the same holding whichever of the day's tickers
    it is written under, so the sale and the transfer of 10 June both have a
    claim on it either way. Looking only under the name the day ends with
    found it spelled Z and missed it spelled X, and the two were then
    computed with nothing said, whichever ran first taking the whole
    purchase.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="does not say how to split") as err:
        get_report(
            calculator,
            _contention_history(
                [
                    _gbp_trade(
                        CONTENTION_LATER_DAY, ActionType.BUY, purchase_spelling, 30, 600
                    )
                ]
            ),
        )

    assert str(CONTENTION_LATER_DAY) in str(err.value)


def _free_shares(date: datetime.date, symbol: str, quantity: int) -> BrokerTransaction:
    """Build a purchase of shares that cost nothing."""
    return BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol=symbol,
        description=f"free {symbol}",
        quantity=Decimal(quantity),
        price=Decimal(0),
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode(GBP),
        broker="Testing",
    )


def test_a_sale_and_a_transfer_contending_for_free_shares_are_refused() -> None:
    """Shares that cost nothing are still worth arguing over.

    Whichever of the two is identified against them takes a nil allowable
    cost and the other takes a share of the pool instead, so which one gets
    them changes both answers.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="does not say how to split") as err:
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, CurrencyCode(GBP)
                ),
                _free_shares(event_day, "FOO", 2),
                transaction(
                    event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
                ),
                transfer_to_spouse_transaction(event_day, "FOO", 2),
            ],
        )

    assert "sold 3 units of FOO and transferred 2" in str(err.value)


def test_free_shares_are_matched_same_day_when_only_one_disposal_wants_them() -> None:
    """With nothing to argue over the nil-cost shares are matched as usual."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, CurrencyCode(GBP)
            ),
            _free_shares(event_day, "FOO", 2),
            transaction(
                event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, CurrencyCode(GBP)
            ),
        ],
    )

    same_day, section_104 = report.calculation_log[event_day]["sell$FOO"]
    assert same_day.rule_type is RuleType.SAME_DAY
    assert same_day.quantity == Decimal(2)
    assert same_day.allowable_cost == Decimal(0)
    assert section_104.quantity == Decimal(1)
    assert section_104.allowable_cost == Decimal(10)
    assert calculator.portfolio["FOO"].quantity == Decimal(19)
    assert calculator.portfolio["FOO"].amount == Decimal(190)


def test_two_transfers_under_two_names_of_one_holding_are_refused() -> None:
    """Only one transfer row can be identified against the day's purchase.

    Both are no gain/no loss, so nothing about the tax turns on which of them
    gets the day's purchase, but this tool matches each row against the
    acquisitions under a single name and cannot share them.
    """
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="cannot share them") as err:
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "OLD", 100, 10, 0, -1000, CurrencyCode(GBP)
                ),
                transaction(
                    rename_day,
                    ActionType.BUY,
                    "NEW",
                    30,
                    20,
                    0,
                    -600,
                    CurrencyCode(GBP),
                ),
                transfer_to_spouse_transaction(rename_day, "OLD", 10),
                _rename_transaction(rename_day, "OLD", "NEW"),
                transfer_to_spouse_transaction(rename_day, "NEW", 10),
            ],
        )

    message = str(err.value)
    assert (
        "you transferred 10 units of OLD to a spouse and 10 of NEW, which the "
        "day's renames make one holding" in message
    )
    assert "record the day's transfers under one name" in message


def test_a_transfer_on_a_day_two_pools_are_renamed_together_is_refused() -> None:
    """Whether the transfer took the merged pool's average cannot be told.

    The transfer row sits before the rename row, but a day carries no order
    the calculator can rely on for this: it applies the day's renames before
    its transfers either way. Taking 50 of the 100 `OLD` shares costs GBP 500;
    taking 50 of a merged 200-share pool costs GBP 750, and the recipient
    carries that figure forward as their own acquisition cost (CG22200). An
    ordinary sale of the same shares is already refused here.
    """
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="pool NEW and OLD together"):
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "OLD", 100, 10, 0, -1000, CurrencyCode(GBP)
                ),
                transaction(
                    buy_day, ActionType.BUY, "NEW", 100, 20, 0, -2000, CurrencyCode(GBP)
                ),
                transfer_to_spouse_transaction(rename_day, "OLD", 50),
                _rename_transaction(rename_day, "OLD", "NEW"),
            ],
        )


def test_a_transfer_and_a_fee_under_the_other_name_are_refused() -> None:
    """The same question about cost the rename brings in, not shares."""
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(CalculationError, match="pooled cost with no shares"):
        get_report(
            calculator,
            [
                transaction(
                    buy_day, ActionType.BUY, "OLD", 100, 10, 0, -1000, CurrencyCode(GBP)
                ),
                _management_fee(rename_day, "NEW", 500),
                transfer_to_spouse_transaction(rename_day, "OLD", 50),
                _rename_transaction(rename_day, "OLD", "NEW"),
            ],
        )


def test_a_transfer_and_a_fee_under_the_held_name_still_computes() -> None:
    """A fee on the shares themselves costs the same either side of a rename."""
    buy_day = datetime.date(2024, 6, 1)
    rename_day = datetime.date(2024, 6, 10)
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator,
        [
            transaction(
                buy_day, ActionType.BUY, "OLD", 100, 10, 0, -1000, CurrencyCode(GBP)
            ),
            _management_fee(rename_day, "OLD", 500),
            transfer_to_spouse_transaction(rename_day, "OLD", 50),
            _rename_transaction(rename_day, "OLD", "NEW"),
        ],
    )

    (entry,) = report.calculation_log[rename_day]["transfer-to-spouse$OLD"]
    # 100 shares costing GBP 1,500 all in, so half of them cost GBP 750.
    assert entry.allowable_cost == Decimal(750)
    assert calculator.portfolio["NEW"].quantity == Decimal(50)
    assert calculator.portfolio["NEW"].amount == Decimal(750)


def _management_fee(date: datetime.date, symbol: str, amount: int) -> BrokerTransaction:
    """Build a management fee: pooled cost with no shares."""
    return BrokerTransaction(
        date=date,
        action=ActionType.FEE,
        symbol=symbol,
        description="management fee",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=Decimal(-amount),
        currency=CurrencyCode(GBP),
        broker="Testing",
    )
