"""Tests for the printable view of a capital gains report.

The golden `.tex` files prove the template and the view agree with what the
report used to say. These prove the numbers the view carries: which log key
means which event, what is counted, what the running totals come to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.exceptions import CalculationError
from cgt_calc.model import (
    CalculationEntry,
    CapitalGainsReport,
    Dividend,
    ExcessReportedIncome,
    RuleType,
    SpinOff,
    TaxTreaty,
)
from cgt_calc.report_view import (
    EventKind,
    EventView,
    IncomeEventView,
    IncomeKind,
    Outcome,
    build_report_view,
)

DAY = datetime.date(2024, 6, 10)
OTHER_DAY = datetime.date(2024, 6, 11)


def _entry(
    rule_type: RuleType = RuleType.SECTION_104,
    *,
    quantity: str = "1",
    amount: str = "0",
    fees: str = "0",
    gain: str | None = None,
    allowable_cost: str = "0",
    new_quantity: str = "0",
    new_pool_cost: str = "0",
    bed_and_breakfast_date_index: datetime.date | None = None,
    spin_off: SpinOff | None = None,
    dividend: Dividend | None = None,
    eris: list[ExcessReportedIncome] | None = None,
    renamed_to: str | None = None,
) -> CalculationEntry:
    """Build a calculation entry from strings, so the decimals stay exact."""
    return CalculationEntry(
        rule_type=rule_type,
        quantity=Decimal(quantity),
        amount=Decimal(amount),
        fees=Decimal(fees),
        gain=Decimal(gain) if gain is not None else None,
        allowable_cost=Decimal(allowable_cost),
        new_quantity=Decimal(new_quantity),
        new_pool_cost=Decimal(new_pool_cost),
        bed_and_breakfast_date_index=bed_and_breakfast_date_index,
        spin_off=spin_off,
        dividend=dividend,
        eris=eris,
        renamed_to=renamed_to,
    )


def _disposal(gain: str, *, quantity: str = "1") -> CalculationEntry:
    """Build a disposal whose proceeds are its cost plus the gain asked for."""
    cost = Decimal(100)
    return _entry(
        quantity=quantity,
        amount=str(cost + Decimal(gain)),
        allowable_cost=str(cost),
        gain=gain,
    )


def _report(
    log: dict[datetime.date, dict[str, list[CalculationEntry]]] | None = None,
    yields: dict[datetime.date, dict[str, list[CalculationEntry]]] | None = None,
) -> CapitalGainsReport:
    """Build a report carrying nothing but the calculation logs under test."""
    return CapitalGainsReport(
        tax_year=2024,
        portfolio=[],
        disposal_count=0,
        disposal_proceeds=Decimal(0),
        allowable_costs=Decimal(0),
        capital_gain=Decimal(0),
        capital_loss=Decimal(0),
        capital_gain_allowance=Decimal(3000),
        dividend_allowance=Decimal(500),
        calculation_log=log or {},
        calculation_log_yields=yields or {},
        total_uk_interest=Decimal(0),
        total_foreign_interest=Decimal(0),
        total_interest_tax=Decimal(0),
        show_unrealized_gains=False,
    )


def _events(log: dict[str, list[CalculationEntry]]) -> list[EventView]:
    """Build the view of one day's events."""
    return list(build_report_view(_report({DAY: log})).days[0].events)


SPIN_OFF = SpinOff(source="FOO", dest="BAR", date=DAY)
ERI = ExcessReportedIncome(
    price=Decimal("0.5"),
    symbol="FOO",
    date=DAY,
    distribution_date=OTHER_DAY,
    is_interest=False,
)


@pytest.mark.parametrize(
    ("key", "entry", "kind"),
    [
        ("sell$FOO", _disposal("10"), EventKind.DISPOSAL),
        ("buy$FOO", _entry(amount="-100", allowable_cost="100"), EventKind.ACQUISITION),
        (
            "spin-off$FOO",
            _entry(rule_type=RuleType.SPIN_OFF, amount="-100", spin_off=SPIN_OFF),
            EventKind.SPIN_OFF,
        ),
        (
            "excess-reported-income$FOO",
            _entry(
                rule_type=RuleType.EXCESS_REPORTED_INCOME,
                amount="-10",
                allowable_cost="1",
                new_pool_cost="11",
                eris=[ERI],
            ),
            EventKind.ERI,
        ),
        ("rename$OLD", _entry(rule_type=RuleType.RENAME, renamed_to="NEW"), None),
        ("split$FOO", _entry(rule_type=RuleType.STOCK_SPLIT), EventKind.SPLIT),
        (
            "transfer-to-spouse$FOO",
            _entry(rule_type=RuleType.TRANSFER_TO_SPOUSE),
            EventKind.TRANSFER_TO_SPOUSE,
        ),
        ("gift$FOO", _disposal("10"), EventKind.GIFT_CONNECTED),
        ("gift-unconnected$FOO", _disposal("10"), EventKind.GIFT_UNCONNECTED),
        ("exempt$FOO", _disposal("10"), EventKind.EXEMPT),
        ("option$META call", _entry(rule_type=RuleType.OPTION), EventKind.OPTION_GRANT),
    ],
)
def test_every_log_key_names_its_event(
    key: str, entry: CalculationEntry, kind: EventKind | None
) -> None:
    """Each prefix decodes to one kind, and the symbol is what follows the $."""
    (event,) = _events({key: [entry]})
    assert event.kind == (kind or EventKind.RENAME)
    assert event.symbol == key.split("$", 1)[1]


def test_a_gift_to_a_connected_person_is_told_from_any_other_gift() -> None:
    """`gift$` and `gift-unconnected$` share a prefix but not a kind."""
    events = _events(
        {"gift$FOO": [_disposal("-10")], "gift-unconnected$FOO": [_disposal("-10")]}
    )
    connected, unconnected = events
    assert connected.kind is EventKind.GIFT_CONNECTED
    assert connected.outcome is Outcome.CLOGGED_LOSS
    assert unconnected.kind is EventKind.GIFT_UNCONNECTED
    assert unconnected.outcome is Outcome.LOSS
    assert all(event.is_gift for event in events)


def test_an_unknown_log_key_is_refused() -> None:
    """A key the report cannot name must fail rather than print nothing."""
    with pytest.raises(
        CalculationError, match=r"Unknown calculation log key: what\.FOO"
    ):
        _events({"what.FOO": [_disposal("1")]})


def test_an_unknown_income_log_key_is_refused() -> None:
    """The income log is decoded by the same rule."""
    with pytest.raises(CalculationError, match=r"Unknown income log key: what\.FOO"):
        build_report_view(_report(yields={DAY: {"what.FOO": [_entry()]}}))


def test_a_purchase_without_units_is_a_management_fee() -> None:
    """A `buy$` of nothing is the fee the report names separately."""
    (event,) = _events({"buy$FOO": [_entry(quantity="0", amount="-5")]})
    assert event.kind is EventKind.MANAGEMENT_FEE
    assert event.is_acquisition
    assert event.number is None


def test_the_counters_skip_what_the_report_does_not_count() -> None:
    """Disposals, option grants and gifts count; a fee and a nil spin-off do not."""
    view = build_report_view(
        _report(
            {
                DAY: {
                    "buy$FOO": [_entry(quantity="1", amount="-10")],
                    "buy$BAR": [_entry(quantity="0", amount="-5")],
                    "spin-off$FOO": [
                        _entry(
                            rule_type=RuleType.SPIN_OFF, amount="-1", spin_off=SPIN_OFF
                        )
                    ],
                    "sell$FOO": [_disposal("10")],
                    "option$META call": [_disposal("10")],
                    "gift$BAR": [_disposal("10")],
                    "exempt$T26": [_disposal("10")],
                },
                OTHER_DAY: {
                    "spin-off$FOO": [
                        _entry(
                            rule_type=RuleType.SPIN_OFF,
                            quantity="0",
                            amount="-1",
                            spin_off=SPIN_OFF,
                        )
                    ],
                },
            }
        )
    )
    assert view.summary.acquisition_count == 2
    assert view.summary.disposal_count == 3
    numbered = [(event.kind, event.number) for day in view.days for event in day.events]
    assert numbered == [
        (EventKind.ACQUISITION, 1),
        (EventKind.MANAGEMENT_FEE, None),
        (EventKind.SPIN_OFF, 2),
        (EventKind.DISPOSAL, 1),
        (EventKind.OPTION_GRANT, 2),
        (EventKind.GIFT_CONNECTED, 3),
        (EventKind.EXEMPT, None),
        (EventKind.SPIN_OFF, None),
    ]


def test_the_running_totals_add_up_the_rounded_events() -> None:
    """Gain, loss, nil and clogged loss each go to their own total."""
    view = build_report_view(
        _report(
            {
                DAY: {
                    "sell$FOO": [_disposal("10.005")],
                    "sell$BAR": [_disposal("-4")],
                    "sell$BAZ": [_disposal("0")],
                    "gift$QUX": [_disposal("-2.50")],
                    "gift-unconnected$FOO": [_disposal("1")],
                }
            }
        )
    )
    gain, loss, nil, clogged, unconnected = view.days[0].events
    assert (gain.outcome, gain.gain_to_date) == (Outcome.GAIN, Decimal("10.01"))
    assert (loss.outcome, loss.loss_to_date) == (Outcome.LOSS, Decimal("4.00"))
    assert (nil.outcome, nil.gain) == (Outcome.NIL, Decimal("0.00"))
    assert nil.gain_to_date is None
    assert (clogged.outcome, clogged.gift_loss_to_date) == (
        Outcome.CLOGGED_LOSS,
        Decimal("2.50"),
    )
    assert unconnected.gain_to_date == Decimal("11.01")
    assert view.summary.total_gain == Decimal("11.01")
    assert view.summary.total_loss == Decimal("4.00")
    assert view.summary.gift_loss == Decimal("2.50")
    assert view.summary.net_gain == Decimal("7.01")


def test_an_exempt_disposal_stays_out_of_every_total() -> None:
    """Its gain is stated but disregarded, so no counter and no total moves."""
    view = build_report_view(_report({DAY: {"exempt$T26": [_disposal("10")]}}))
    (event,) = view.days[0].events
    assert (event.outcome, event.gain) == (Outcome.GAIN, Decimal("10.00"))
    assert event.pool_units_left
    assert view.summary.total_gain == Decimal(0)
    assert view.summary.disposal_count == 0


def test_a_line_shows_its_own_proceeds_only_beside_others() -> None:
    """One rule matching the whole disposal has nothing to break down."""
    (whole,) = _events({"sell$FOO": [_disposal("10", quantity="4")]})
    assert [line.shows_proceeds for line in whole.lines] == [False]
    (split,) = _events(
        {"sell$FOO": [_disposal("10", quantity="3"), _disposal("6", quantity="1")]}
    )
    assert [line.shows_proceeds for line in split.lines] == [True, True]


def test_the_events_stated_in_prose_carry_no_lines() -> None:
    """A rename, a reorganisation and a spouse transfer print no rule list."""
    events = _events(
        {
            "rename$OLD": [_entry(rule_type=RuleType.RENAME, renamed_to="NEW")],
            "split$FOO": [_entry(rule_type=RuleType.STOCK_SPLIT)],
            "transfer-to-spouse$FOO": [_entry(rule_type=RuleType.TRANSFER_TO_SPOUSE)],
            "sell$FOO": [_disposal("1")],
        }
    )
    assert [bool(event.lines) for event in events] == [False, False, False, True]


def test_a_pool_emptied_by_the_event_has_no_price_per_unit() -> None:
    """Nothing is divided by a pool of no units."""
    (event,) = _events(
        {"transfer-to-spouse$FOO": [_entry(rule_type=RuleType.TRANSFER_TO_SPOUSE)]}
    )
    assert event.pool_quantity == 0
    assert event.pool_per_unit is None


def test_excess_reported_income_prices_a_unit_to_four_places() -> None:
    """The unit price of ERI is the one figure kept beyond the penny."""
    (event,) = _events(
        {
            "excess-reported-income$FOO": [
                _entry(
                    rule_type=RuleType.EXCESS_REPORTED_INCOME,
                    amount="-10",
                    allowable_cost="1",
                    new_pool_cost="11",
                    eris=[ERI],
                )
            ]
        }
    )
    assert event.eri_unit_price == Decimal("0.5000")


def test_an_acquisition_carries_the_income_added_to_its_cost() -> None:
    """Each ERI on an acquisition line is priced and dated for the report."""
    (event,) = _events(
        {"buy$FOO": [_entry(quantity="3", amount="-30", eris=[ERI], spin_off=SPIN_OFF)]}
    )
    (line,) = event.lines
    assert line.spin_off_source == "FOO"
    assert [(eri.cost, eri.unit_price, eri.date) for eri in line.eris] == [
        (Decimal("1.50"), Decimal("0.5000"), DAY)
    ]


def test_a_disposal_line_keeps_the_sign_the_report_prints() -> None:
    """A gain too small to survive rounding is still stated as a gain."""
    (event,) = _events({"sell$FOO": [_disposal("0.001")]})
    (line,) = event.lines
    assert line.is_gain
    assert line.gain == Decimal("0.00")
    assert line.bnb_date is None


def test_a_bed_and_breakfast_line_carries_the_day_it_matched() -> None:
    """The date only reaches the report on the rule that used it."""
    (event,) = _events(
        {
            "sell$FOO": [
                _entry(
                    rule_type=RuleType.BED_AND_BREAKFAST,
                    amount="110",
                    allowable_cost="100",
                    gain="10",
                    bed_and_breakfast_date_index=OTHER_DAY,
                )
            ]
        }
    )
    assert event.lines[0].bnb_date == OTHER_DAY


def _yields(log: dict[str, list[CalculationEntry]]) -> list[IncomeEventView]:
    """Build the view of one day's income events."""
    return list(build_report_view(_report(yields={DAY: log})).income_days[0].events)


def _dividend(
    *, is_interest: bool = False, treaty: TaxTreaty | None = None
) -> Dividend:
    """Build a dividend of £100 with £15 withheld."""
    return Dividend(
        date=DAY,
        symbol="FOO",
        amount=Decimal(100),
        tax_at_source=Decimal(-15),
        is_interest=is_interest,
        tax_treaty=treaty,
    )


def test_the_income_log_keys_name_their_events() -> None:
    """Interest is named after its currency, everything else after the $."""
    events = _yields(
        {
            "dividend$FOO": [
                _entry(rule_type=RuleType.DIVIDEND, amount="100", dividend=_dividend())
            ],
            "interestUK$Testing": [_entry(rule_type=RuleType.INTEREST, amount="3")],
            "interestUSD$Testing": [_entry(rule_type=RuleType.INTEREST, amount="4")],
            "interestTaxGBP$Testing": [
                _entry(rule_type=RuleType.INTEREST_TAX, amount="1")
            ],
            "interestTaxUSD$Testing": [
                _entry(rule_type=RuleType.INTEREST_TAX, amount="2")
            ],
            "excess-reported-income-distribution$BAR": [
                _entry(
                    rule_type=RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                    amount="22.26",
                    eris=[ERI],
                )
            ],
        }
    )
    assert [(e.kind, e.symbol, e.origin, e.number) for e in events] == [
        (IncomeKind.DIVIDEND, "FOO", None, 1),
        (IncomeKind.INTEREST, "Testing", "UK", 1),
        (IncomeKind.INTEREST, "USD", "Foreign", 2),
        (IncomeKind.INTEREST_TAX, "GBP", "UK", 1),
        (IncomeKind.INTEREST_TAX, "USD", "Foreign", 2),
        (IncomeKind.ERI_DISTRIBUTION, "BAR", None, 1),
    ]
    assert events[-1].unit_price == Decimal("0.5000")
    assert events[-1].is_interest is False


def test_a_dividend_states_the_tax_withheld_and_any_treaty_relief() -> None:
    """Both figures reach the report already rounded to the penny."""
    treaty = TaxTreaty(
        country="USA", country_rate=Decimal("0.30"), treaty_rate=Decimal("0.15")
    )
    (event,) = _yields(
        {
            "dividend$FOO": [
                _entry(
                    rule_type=RuleType.DIVIDEND,
                    amount="100",
                    dividend=_dividend(treaty=treaty),
                )
            ]
        }
    )
    assert event.amount == Decimal("100.00")
    assert event.tax_at_source == Decimal("15.00")
    assert (event.treaty_country, event.treaty_rate, event.treaty_amount) == (
        "USA",
        Decimal("15.00"),
        Decimal("15.00"),
    )


def test_a_dividend_paid_as_interest_carries_no_treaty() -> None:
    """Nothing is claimed back on interest, so the treaty fields stay empty."""
    (event,) = _yields(
        {
            "dividend$FOO": [
                _entry(
                    rule_type=RuleType.DIVIDEND,
                    amount="100",
                    dividend=_dividend(is_interest=True),
                )
            ]
        }
    )
    assert event.is_interest
    assert event.treaty_country is None
    assert event.treaty_rate is None
    assert event.treaty_amount is None


def test_the_income_section_is_left_out_when_nothing_was_paid() -> None:
    """An empty yields log means the report has no income pages."""
    view = build_report_view(_report({DAY: {"sell$FOO": [_disposal("1")]}}))
    assert view.income_days == ()
    assert view.income_summary.eri_dividends is None
    assert view.income_summary.eri_interest is None
    assert view.income_summary.dividend_count == 0


def test_the_income_summary_splits_excess_reported_income_by_fund() -> None:
    """Interest funds and dividend funds are stated on their own lines."""
    interest_eri = ExcessReportedIncome(
        price=Decimal("0.25"),
        symbol="BAR",
        date=DAY,
        distribution_date=OTHER_DAY,
        is_interest=True,
    )
    view = build_report_view(
        _report(
            yields={
                DAY: {
                    "excess-reported-income-distribution$FOO": [
                        _entry(
                            rule_type=RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                            amount="10",
                            eris=[ERI],
                        )
                    ],
                    "excess-reported-income-distribution$BAR": [
                        _entry(
                            rule_type=RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                            amount="4",
                            eris=[interest_eri],
                        )
                    ],
                }
            }
        )
    )
    assert view.income_summary.eri_count == 2
    assert view.income_summary.eri_dividends == Decimal("10.00")
    assert view.income_summary.eri_interest == Decimal("4.00")
    assert view.income_summary.total_dividends == Decimal("10.00")
