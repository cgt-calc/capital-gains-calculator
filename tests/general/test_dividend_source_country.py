"""Tests for resolving the country a dividend was paid from."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, Isin, RuleType
from cgt_calc.spin_off_handler import SpinOffHandler

if TYPE_CHECKING:
    import pytest

TAX_YEAR = 2024
DATE = datetime.date(2024, 6, 3)

# A US-domiciled security. Interactive Brokers reports its dividends in the
# account's base currency, GBP for a UK account, while Schwab reports in USD.
US_ISIN = Isin("US9220427424")
US_WITHHOLDING_RATE = Decimal("0.15")


def _dividend_pair(
    currency: CurrencyCode,
    isin: Isin | None,
    withholding_rate: Decimal = US_WITHHOLDING_RATE,
) -> list[BrokerTransaction]:
    """Build a dividend of 100 and the tax withheld at source on it."""
    amount = Decimal(100)

    def make(action: ActionType, value: Decimal) -> BrokerTransaction:
        return BrokerTransaction(
            date=DATE,
            action=action,
            symbol="FOO",
            description="dividend",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=value,
            currency=currency,
            broker="Test Broker",
            isin=isin,
        )

    return [
        make(ActionType.DIVIDEND, amount),
        make(ActionType.DIVIDEND_TAX, -amount * withholding_rate),
    ]


def _treaty_country(transactions: list[BrokerTransaction]) -> str | None:
    """Run the calculation and return the treaty applied to the dividend."""
    gbp_prices = {DATE: {t.currency: Decimal(1) for t in transactions}}
    currency_converter = CurrencyConverter(None, gbp_prices)
    calculator = CapitalGainsCalculator(
        TAX_YEAR,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    calculator.prepare_history(transactions)
    report = calculator.calculate_capital_gain()

    entries = [
        entry
        for entries in report.calculation_log_yields.values()
        for entry_list in entries.values()
        for entry in entry_list
        if entry.rule_type == RuleType.DIVIDEND
    ]
    assert len(entries) == 1
    dividend = entries[0].dividend
    assert dividend is not None
    return dividend.tax_treaty.country if dividend.tax_treaty else None


def test_treaty_from_isin_when_currency_is_not_the_source_country() -> None:
    """A US dividend converted to GBP by the broker still gets treaty relief."""
    assert _treaty_country(_dividend_pair(CurrencyCode("GBP"), US_ISIN)) == "USA"


def test_treaty_from_currency_when_no_isin_is_available() -> None:
    """Brokers that supply no ISIN keep the behaviour they had before."""
    assert _treaty_country(_dividend_pair(CurrencyCode("USD"), None)) == "USA"


def test_isin_wins_over_the_currency_guess() -> None:
    """A US security paid in zloty is still a US dividend.

    The currency alone would say Poland, whose 19% withholding rate does not
    match the 15% withheld, so no treaty would apply.
    """
    assert _treaty_country(_dividend_pair(CurrencyCode("PLN"), US_ISIN)) == "USA"


def test_no_treaty_when_the_source_country_cannot_be_determined(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without an ISIN, GBP says nothing about where the income came from."""
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        assert _treaty_country(_dividend_pair(CurrencyCode("GBP"), None)) is None
    assert "Source country of the GBP dividend is unknown" in caplog.text


def test_no_treaty_when_withholding_does_not_match_the_treaty_rate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Relief is refused when the rate deducted is not the treaty rate."""
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        assert (
            _treaty_country(
                _dividend_pair(CurrencyCode("GBP"), US_ISIN, Decimal("0.30"))
            )
            is None
        )
    assert "does not match the base taxation rules" in caplog.text
