"""Distribution-date ERI income without changing CGT costs."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.currency_converter import StrictTestCurrencyConverter
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    ForeignCurrencyAmount,
    Isin,
)
from cgt_calc.parsers.eri.model import ERITransaction

from .test_calc import create_calculator, get_report, report_values

if TYPE_CHECKING:
    from cgt_calc.main import CapitalGainsCalculator
    from cgt_calc.model import CapitalGainsReport


REPORT_DATE = dt.date(2025, 3, 31)
DISTRIBUTION_DATE = dt.date(2025, 9, 30)
ISIN = Isin("IE00BFY0GT14")
USD = CurrencyCode("USD")


def trade(
    day: str, action: ActionType, quantity: str, amount: str
) -> BrokerTransaction:
    """Create a GBP trade with exact decimal inputs."""
    return BrokerTransaction(
        date=dt.date.fromisoformat(day),
        action=action,
        symbol="SWRD",
        description="",
        quantity=Decimal(quantity),
        price=abs(Decimal(amount) / Decimal(quantity)),
        fees=Decimal(0),
        amount=Decimal(amount),
        currency=CurrencyCode("GBP"),
        broker="Testing",
        isin=ISIN,
    )


CASES: dict[str, list[BrokerTransaction]] = {
    "held": [],
    "partial-sale": [trade("2025-06-01", ActionType.SELL, "5", "100")],
    "full-sale": [trade("2025-06-01", ActionType.SELL, "10", "200")],
    "30-day-match": [
        trade("2025-03-25", ActionType.SELL, "10", "200"),
        trade("2025-04-01", ActionType.BUY, "10", "-100"),
    ],
    "post-report-purchase": [trade("2025-06-01", ActionType.BUY, "10", "-100")],
    "same-day-match": [
        trade("2025-06-01", ActionType.BUY, "1", "-10"),
        trade("2025-06-01", ActionType.SELL, "1", "20"),
    ],
    "30-day-match-after-report": [
        trade("2025-06-01", ActionType.SELL, "1", "20"),
        trade("2025-06-10", ActionType.BUY, "1", "-10"),
    ],
    "cost-applied-once": [
        trade("2025-06-01", ActionType.SELL, "5", "100"),
        trade("2025-08-01", ActionType.SELL, "5", "100"),
    ],
}


def scenario(
    case: str, *, is_interest: bool = False
) -> tuple[CapitalGainsCalculator, CapitalGainsReport]:
    """Calculate one history with distinct report, sale and distribution rates."""
    calculator = create_calculator(
        tax_year=2025,
        balance_check=False,
        interest_fund_tickers=["SWRD"] if is_interest else [],
    )
    calculator.matcher.currency_converter = StrictTestCurrencyConverter(
        None,
        {
            REPORT_DATE: {USD: Decimal(2)},
            dt.date(2025, 4, 1): {USD: Decimal(4)},
            dt.date(2025, 6, 1): {USD: Decimal(4)},
            DISTRIBUTION_DATE: {USD: Decimal(5)},
        },
    )
    calculator.ingester.currency_converter = calculator.matcher.currency_converter
    rows = [
        trade("2025-01-01", ActionType.BUY, "10", "-100"),
        ERITransaction(date=REPORT_DATE, isin=ISIN, price=Decimal(10), currency=USD),
        *CASES[case],
    ]
    rows.sort(key=lambda row: row.date)
    return calculator, get_report(calculator, rows)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("is_interest", [False, True])
def test_income_date_is_independent_of_trades_and_cost_application(
    case: str, *, is_interest: bool
) -> None:
    """Every trading pattern preserves the £20 September income entitlement."""
    calculator, report = scenario(case, is_interest=is_interest)
    assert report.total_eri_amount(is_interest=is_interest) == Decimal(20)
    assert report.calculation_log_yields[DISTRIBUTION_DATE][
        "excess-reported-income-distribution$SWRD"
    ][0].quantity == Decimal(10)
    # Includes every report field, and get_report also checks history immutability.
    assert report_values(calculator.calculate_capital_gain()) == report_values(report)
    eri = calculator.eris[REPORT_DATE]["SWRD"]
    assert eri.reported_price == ForeignCurrencyAmount(Decimal(10), USD)
    assert eri.price == Decimal(5)


@pytest.mark.parametrize(
    ("case", "cost", "pool", "units"),
    [
        ("held", "0", "150", "10"),
        ("partial-sale", "75", "75", "5"),
        ("full-sale", "150", "0", "0"),
        ("post-report-purchase", "0", "250", "20"),
        ("cost-applied-once", "150", "0", "0"),
        ("same-day-match", "10", "150", "10"),
        ("30-day-match-after-report", "10", "150", "10"),
    ],
)
def test_income_change_preserves_report_date_cgt_policy(
    case: str, cost: str, pool: str, units: str
) -> None:
    """Separating income leaves existing pool and disposal cost unchanged."""
    _, report = scenario(case)
    assert report.allowable_costs == Decimal(cost)
    position = next(p for p in report.portfolio if p.symbol == "SWRD")
    assert (position.quantity, position.amount) == (Decimal(units), Decimal(pool))


def test_30_day_cost_uses_report_date_fx() -> None:
    """The matched cost keeps report-date FX while income uses September FX."""
    _, report = scenario("30-day-match")
    assert report.total_eri_amount(is_interest=False) == Decimal(20)
    position = next(p for p in report.portfolio if p.symbol == "SWRD")
    assert (position.quantity, position.amount) == (Decimal(10), Decimal(150))


def test_eri_income_uses_distribution_tax_year() -> None:
    """March ERI adds cost then, with income only in the next tax year."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    rates = StrictTestCurrencyConverter(None, {REPORT_DATE: {USD: Decimal(2)}})
    calculator.ingester.currency_converter = rates
    calculator.matcher.currency_converter = rates
    report = get_report(
        calculator,
        [
            trade("2025-01-01", ActionType.BUY, "10", "-100"),
            ERITransaction(
                date=REPORT_DATE, isin=ISIN, price=Decimal(10), currency=USD
            ),
        ],
    )

    assert report.total_eri_amount(is_interest=False) == Decimal(0)
    assert not report.calculation_log_yields
    assert report.portfolio[0].amount == Decimal(150)
    assert report.calculation_log[REPORT_DATE]["excess-reported-income$SWRD"][
        0
    ].allowable_cost == Decimal(50)


def test_unused_eri_needs_no_distribution_date_rate() -> None:
    """A report for an unheld fund needs no additional income FX quote."""
    calculator = create_calculator(tax_year=2025, balance_check=False)
    calculator.isin_converter.data[ISIN] = {"SWRD"}
    rates = StrictTestCurrencyConverter(None, {REPORT_DATE: {USD: Decimal(2)}})
    calculator.ingester.currency_converter = rates
    calculator.matcher.currency_converter = rates

    report = get_report(
        calculator,
        [
            ERITransaction(
                date=REPORT_DATE, isin=ISIN, price=Decimal(10), currency=USD
            ),
        ],
    )

    assert report.total_eri_amount(is_interest=False) == Decimal(0)
    assert not report.calculation_log_yields
