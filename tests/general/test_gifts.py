"""Gifts of shares to someone other than a spouse: disposals at market value.

The consideration is the market value on the day (TCGA 1992 s17), and the
disposal is identified like a sale. A loss is reported on its own and kept
out of the loss total, since a loss on a gift to a connected person can
only be set against gains on disposals to that person (s18(3)).
"""

from __future__ import annotations

import dataclasses
import datetime
from decimal import Decimal
import re
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import (
    CalculationError,
    InvalidTransactionError,
    PriceMissingError,
    QuantityNotPositiveError,
    UnclassifiedGiftError,
)
from cgt_calc.model import ActionType, BrokerTransaction, RuleType
from cgt_calc.parsers.broker_registry import _transaction_sort_key
from cgt_calc.render_latex import render_pdf

from .calc_test_data import (
    GBP,
    split_transaction,
    transaction,
    transfer_to_spouse_transaction,
)
from .test_calc import create_calculator, get_report

if TYPE_CHECKING:
    from pathlib import Path

BUY_DAY = datetime.date(2024, 6, 1)
GIFT_DAY = datetime.date(2024, 6, 10)


def _gift(
    quantity: int,
    price: float | None,
    fees: float = 0,
    action: ActionType = ActionType.GIFT,
) -> BrokerTransaction:
    return transaction(GIFT_DAY, action, "FOO", quantity, price, fees, None, GBP)


def _unconnected_gift(quantity: int, price: float | None) -> BrokerTransaction:
    return _gift(quantity, price, action=ActionType.GIFT_UNCONNECTED)


def test_a_gift_with_a_gain_counts_like_a_sale() -> None:
    """Four shares costing £40 given away when worth £120: £80 of gain."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 30),
        ],
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(120)
    assert report.allowable_costs == Decimal(40)
    assert report.total_gain() == Decimal(80)
    assert "gift$FOO" in report.calculation_log[GIFT_DAY]
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


def test_a_loss_on_a_gift_is_reported_but_not_totalled() -> None:
    """Worth £20 when given, costing £40: a £20 loss, listed on its own."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 5),
        ],
    )

    # It is a disposal, and its proceeds and cost are reported as such.
    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(20)
    assert report.allowable_costs == Decimal(40)
    # But the loss does not reduce anything.
    assert report.capital_loss == Decimal(0)
    assert report.total_gain() == Decimal(0)
    # It is reported as its own total instead.
    assert report.gift_loss == Decimal(-20)
    text = str(report)
    assert "Losses on gifts" in text
    assert "Gifts at market value" in text
    assert "loss £20.00 (clogged)" in text


def test_a_loss_on_a_gift_to_an_unconnected_person_is_an_ordinary_loss() -> None:
    """No connected person, no clog: the £20 goes into Loss like a sale's."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _unconnected_gift(4, 5),
        ],
    )

    assert report.capital_loss == Decimal(-20)
    assert report.gift_loss == Decimal(0)
    assert report.total_gain() == Decimal(-20)
    assert "gift-unconnected$FOO" in report.calculation_log[GIFT_DAY]
    text = str(report)
    assert "loss £20.00" in text
    assert "clogged)" not in text
    assert "Losses on gifts" not in text


def test_a_gain_on_a_gift_to_an_unconnected_person_counts_like_any_other() -> None:
    """Connection only matters to a loss."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _unconnected_gift(4, 30),
        ],
    )

    assert report.total_gain() == Decimal(80)


@pytest.mark.parametrize("connected_first", [True, False])
def test_gifts_to_a_connected_and_an_unconnected_person_on_one_day_are_refused(
    connected_first: bool,
) -> None:
    """One disposal under s105(1); a loss on it could not be split for s18(3)."""
    gifts = [_gift(2, 30), _unconnected_gift(2, 30)]
    with pytest.raises(CalculationError, match="connected person and a gift"):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                *(gifts if connected_first else gifts[::-1]),
            ],
        )


def test_a_worthless_holding_given_to_a_connected_person_is_a_clogged_loss() -> None:
    """Market value zero: the whole cost becomes a loss, clogged as usual."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            dataclasses.replace(_gift(4, 30), price=Decimal(0)),
        ],
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(0)
    assert report.allowable_costs == Decimal(40)
    assert report.gift_loss == Decimal(-40)
    assert report.capital_loss == Decimal(0)


def test_a_worthless_holding_given_to_an_unconnected_person_is_a_loss() -> None:
    """The same at nil value, plus a fee: an ordinary loss of cost and fee."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            dataclasses.replace(
                _gift(4, 30, fees=5, action=ActionType.GIFT_UNCONNECTED),
                price=Decimal(0),
            ),
        ],
    )

    assert report.disposal_proceeds == Decimal(0)
    assert report.allowable_costs == Decimal(45)
    assert report.capital_loss == Decimal(-45)
    assert report.total_gain() == Decimal(-45)


def test_a_gift_is_identified_like_a_sale() -> None:
    """A repurchase within 30 days is matched first, as for any disposal."""
    rebuy_day = datetime.date(2024, 6, 20)
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 30),
            transaction(rebuy_day, ActionType.BUY, "FOO", 4, 25, 0, -100, GBP),
        ],
    )

    entries = report.calculation_log[GIFT_DAY]["gift$FOO"]
    assert [e.rule_type for e in entries] == [RuleType.BED_AND_BREAKFAST]
    # Matched to the £25 repurchase, not the £10 pool.
    assert report.allowable_costs == Decimal(100)
    assert report.total_gain() == Decimal(20)


def test_two_gifts_on_one_day_are_one_disposal() -> None:
    """Everything given away on a day at one value is a single disposal."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(2, 30),
            _gift(2, 30),
        ],
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(120)
    assert report.allowable_costs == Decimal(40)
    assert report.total_gain() == Decimal(80)
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


def test_gifts_at_different_values_on_one_day_are_refused() -> None:
    """A gain to one person cannot net a clogged loss to another."""
    with pytest.raises(CalculationError, match="different values per unit"):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                _gift(2, 30),
                _gift(2, 5),
            ],
        )


def test_unconnected_gifts_at_different_values_still_add_up() -> None:
    """No ring-fence, so the aggregate equals the sum of its parts."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _unconnected_gift(2, 30),
            _unconnected_gift(2, 5),
        ],
    )

    assert report.disposal_proceeds == Decimal(70)
    assert report.total_gain() == Decimal(30)


def test_a_fee_on_a_gift_is_allowed_as_a_cost() -> None:
    """£120 of value, £40 of cost, £5 to make the gift: £75 of gain."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 30, fees=5),
        ],
    )

    assert report.disposal_proceeds == Decimal(120)
    assert report.allowable_costs == Decimal(45)
    assert report.total_gain() == Decimal(75)


@pytest.mark.parametrize(
    ("price", "error"),
    [(None, PriceMissingError), (Decimal(-1), InvalidTransactionError)],
)
def test_a_gift_needs_the_market_value(
    price: Decimal | None, error: type[Exception]
) -> None:
    """The price cannot be absent or negative; zero is a real value."""
    with pytest.raises(error):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                dataclasses.replace(_gift(4, 30), price=price),
            ],
        )


def test_a_gift_of_shares_not_held_is_refused() -> None:
    """The row names a symbol the holding never had."""
    with pytest.raises(
        InvalidTransactionError,
        match=r"^Tried to give away BAR on 2024-06-10, but no BAR is held\."
        r" Check that the history includes the purchase, vest or transfer that"
        r" acquired the shares and any split or rename since, and that no"
        r" disposal appears twice\. A purchase, vest or split the exports cannot"
        r" supply can go in a file passed with --raw-file, using an action listed"
        r" at https://cgt-calc\.uk/brokers/raw/#actions-to-use; a rename cannot:"
        r" report the old and new tickers so the pair can be added\.\n",
    ):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(GIFT_DAY, ActionType.GIFT, "BAR", 4, 30, 0, None, GBP),
            ],
        )


@pytest.mark.parametrize("quantity", [0, -1])
def test_a_gift_needs_a_positive_quantity(quantity: int) -> None:
    """Nothing, or less than nothing, cannot be given away."""
    with pytest.raises(QuantityNotPositiveError):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                _gift(quantity, 30),
            ],
        )


def test_a_gift_of_more_than_held_is_refused() -> None:
    """Ten held, eleven given away: the history is wrong somewhere."""
    with pytest.raises(
        InvalidTransactionError,
        match=r"^Tried to give away 11 FOO on 2024-06-10, but the holding is 10\. Check",
    ):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                _gift(11, 30),
            ],
        )


def test_giving_away_the_whole_holding_closes_it() -> None:
    """All ten shares go, and nothing of the symbol is left."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(10, 30),
        ],
    )

    assert report.total_gain() == Decimal(200)
    assert calculator.portfolio["FOO"].quantity == Decimal(0)


def test_a_raw_gift_before_a_split_is_in_the_units_of_its_day() -> None:
    """A RAW history keeps each day's units, so the price is the day's.

    This is the convention the docs describe; a Schwab export instead
    restates counts for later splits, and there the price on the row must
    be the value of the whole gift divided by the restated count. That
    case is a matter of what the user enters, so it is covered by the
    wording of the refusal rather than by a calculation.
    """
    split_day = datetime.date(2024, 7, 1)
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(2, 30),
            dataclasses.replace(split_transaction(split_day, "FOO", 8), currency=GBP),
        ],
    )

    assert report.disposal_proceeds == Decimal(60)
    assert report.total_gain() == Decimal(40)


@pytest.mark.parametrize("gift_first", [True, False])
def test_a_sale_and_a_gift_to_an_unconnected_person_are_one_disposal(
    gift_first: bool,
) -> None:
    """Nothing to ring-fence, so s105(1) simply adds them up, as a sale."""
    sale = transaction(GIFT_DAY, ActionType.SELL, "FOO", 2, 30, 0, 60, GBP)
    gift = _unconnected_gift(2, 30)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            *([gift, sale] if gift_first else [sale, gift]),
        ],
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(120)
    assert report.total_gain() == Decimal(80)
    assert "sell$FOO" in report.calculation_log[GIFT_DAY]
    assert "gift-unconnected$FOO" not in report.calculation_log[GIFT_DAY]
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


def test_a_connected_gift_after_a_sale_and_an_unconnected_gift_is_refused() -> None:
    """The ordinary disposal is already there; a clogged part cannot join it."""
    with pytest.raises(CalculationError, match="sale and a gift to a connected"):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(GIFT_DAY, ActionType.SELL, "FOO", 2, 30, 0, 60, GBP),
                _unconnected_gift(2, 30),
                _gift(2, 30),
            ],
        )


@pytest.mark.parametrize("gift_first", [True, False])
def test_a_sale_and_a_gift_on_one_day_are_refused(gift_first: bool) -> None:
    """One disposal under s105(1), but a loss on it cannot be split for s18(3)."""
    sale = transaction(GIFT_DAY, ActionType.SELL, "FOO", 2, 30, 0, 60, GBP)
    gift = _gift(2, 30)
    with pytest.raises(CalculationError, match="sale and a gift"):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                *([gift, sale] if gift_first else [sale, gift]),
            ],
        )


def _unclassified(quantity: int, ambiguous: Decimal | None = None) -> BrokerTransaction:
    return BrokerTransaction(
        date=GIFT_DAY,
        action=ActionType.UNCLASSIFIED_GIFT,
        symbol="FOO",
        description="",
        quantity=Decimal(quantity),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=GBP,
        broker="Testing",
        ambiguous_quantity=ambiguous,
    )


@pytest.mark.parametrize("action", [ActionType.GIFT, ActionType.GIFT_UNCONNECTED])
def test_a_gift_row_classifies_a_broker_gift(action: ActionType) -> None:
    """The broker's row is accounted for by the user's row, and dropped."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _unclassified(4),
            _gift(4, 30, action=action),
        ],
    )

    assert report.total_gain() == Decimal(80)
    # Four shares left once, not twice.
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


def test_two_broker_gifts_on_one_day_need_two_gift_rows() -> None:
    """Counted, not a set: each broker row is accounted for by its own row."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _unclassified(2),
            _unclassified(2),
            _gift(2, 30),
            _gift(2, 30),
        ],
    )

    assert report.total_gain() == Decimal(80)
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


@pytest.mark.parametrize("confirmed", [2, 40])
def test_either_reading_of_an_ambiguous_broker_gift_can_be_a_gift(
    confirmed: int,
) -> None:
    """The GIFT row settles the count as well as the recipient."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 100, 10, 0, -1000, GBP),
            _unclassified(2, Decimal(40)),
            _gift(confirmed, 30),
        ],
    )

    assert calculator.portfolio["FOO"].quantity == Decimal(100 - confirmed)


def test_the_refusal_offers_the_gift_row_too() -> None:
    """Unclassified, the error shows both lines the user could add."""
    with pytest.raises(UnclassifiedGiftError) as err:
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                _unclassified(4),
            ],
        )

    message = str(err.value)
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,4,0.00,0.00,GBP" in message
    assert "2024-06-10,GIFT,FOO,4,<value per unit>,0.00,GBP" in message
    assert "divide the value of the whole gift by the restated count" in message
    assert "write GIFT_UNCONNECTED instead" in message


def test_a_gift_and_a_transfer_to_spouse_on_one_day_are_kept_apart() -> None:
    """Different recipients, both out of the pool: no clash to refuse."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(2, 30),
            transfer_to_spouse_transaction(GIFT_DAY, "FOO", 2),
        ],
    )

    assert report.total_gain() == Decimal(40)
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


def test_a_gift_and_a_transfer_contending_for_a_purchase_are_refused() -> None:
    """The same clash as for a sale, and the message calls the gift a gift."""
    with pytest.raises(CalculationError) as err:
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(GIFT_DAY, ActionType.BUY, "FOO", 2, 20, 0, -40, GBP),
                _gift(2, 30),
                transfer_to_spouse_transaction(GIFT_DAY, "FOO", 2),
            ],
        )

    message = str(err.value)
    assert "gave away 2 units of FOO and transferred 2" in message
    assert "Both the gift and the transfer" in message


def _latex(tmp_path: Path, price: int, action: ActionType = ActionType.GIFT) -> str:
    """Render one gift to LaTeX source, flattened to one line per block."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, price, action=action),
        ],
    )
    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", source)


def test_the_pdf_shows_a_gain_on_a_gift_like_any_other(tmp_path: Path) -> None:
    """Worth £120, costing £40: a chargeable gain of £80, in the total."""
    source = _latex(tmp_path, 30)

    assert "4 units of FOO given away at a market value of £120.00" in source
    assert "Chargeable \\textbf{gain} is £80.00, before any relief" in source


def test_the_pdf_keeps_a_loss_on_a_gift_out_of_the_total(tmp_path: Path) -> None:
    """A £20 loss is shown with the s18(3) note and no running loss total."""
    source = _latex(tmp_path, 5)

    assert "\\textbf{Clogged loss} of £20.00 on a gift, kept out of the loss" in source
    assert "Losses on gifts, kept separate: £20.00" in source
    assert "Capital loss to date" not in source


def test_the_pdf_shows_an_unconnected_loss_as_an_ordinary_loss(
    tmp_path: Path,
) -> None:
    """No clog for an unconnected recipient: the loss runs into the total."""
    source = _latex(tmp_path, 5, ActionType.GIFT_UNCONNECTED)

    assert "given away at a market value of £20.00" in source
    assert "Chargeable \\textbf{loss} is £20.00" in source
    assert "Capital loss to date is £20.00" in source
    assert "Clogged" not in source


def test_the_pdf_does_not_call_a_gift_at_cost_a_loss(tmp_path: Path) -> None:
    """Given away at exactly its cost, a gift is a nil result, not a loss."""
    source = _latex(tmp_path, 10)

    assert "Neither a gain nor a loss." in source
    assert "loss} " not in source.lower()


@pytest.mark.parametrize("action", [ActionType.GIFT, ActionType.GIFT_UNCONNECTED])
def test_a_gift_sorts_after_a_same_day_acquisition(action: ActionType) -> None:
    """A hand-written gift must not be read before the buy it depends on.

    Gift rows come from a RAW file while the shares come from a broker
    export, and parsers merge in registry order, so without this the gift
    is validated first and fails as nothing held.
    """
    gift = _gift(4, 30, action=action)
    buy = transaction(GIFT_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP)

    # Worst case: the gift is read first, as a RAW file would give it.
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        sorted([gift, buy], key=_transaction_sort_key),
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(120)


def test_a_mixed_disposal_day_is_named_precisely_in_the_transfer_refusal() -> None:
    """Sold and given-away units share the count, so the verb covers both."""
    with pytest.raises(CalculationError) as err:
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                transaction(GIFT_DAY, ActionType.BUY, "FOO", 2, 20, 0, -40, GBP),
                transaction(GIFT_DAY, ActionType.SELL, "FOO", 2, 30, 0, 60, GBP),
                _unconnected_gift(2, 30),
                transfer_to_spouse_transaction(GIFT_DAY, "FOO", 2),
            ],
        )

    message = str(err.value)
    assert "sold or gave away 4 units of FOO and transferred 2" in message
    assert "Both the disposal and the transfer" in message
