"""Tests for resolving the country a dividend was paid from."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, Isin, RuleType
from cgt_calc.share_prices import SharePrices
from cgt_calc.spin_off_handler import SpinOffHandler

if TYPE_CHECKING:
    import pytest

    from cgt_calc.model import CapitalGainsReport

TAX_YEAR = 2024
DATE = dt.date(2024, 6, 3)

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


def _report(
    transactions: list[BrokerTransaction],
    interest_fund_tickers: list[str] | None = None,
) -> CapitalGainsReport:
    """Run the calculation at a rate of 1 to GBP for every currency."""
    gbp_prices = {DATE: {t.currency: Decimal(1) for t in transactions}}
    currency_converter = CurrencyConverter(None, gbp_prices)
    calculator = CapitalGainsCalculator(
        TAX_YEAR,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        SharePrices(),
        interest_fund_tickers=interest_fund_tickers or [],
        balance_check=False,
    )
    calculator.prepare_history(transactions)
    return calculator.calculate_capital_gain()


def _treaty_country(transactions: list[BrokerTransaction]) -> str | None:
    """Run the calculation and return the treaty applied to the dividend."""
    report = _report(transactions)

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
    assert (
        "The source country of the GBP dividend from FOO on 2024-06-03 is unknown, "
        "so the report gives no treaty relief for the tax withheld from it. Add "
        "the share's ISIN to the ISIN translation file, or check where the paying "
        "company is resident and work out any relief outside cgt-calc."
    ) in caplog.messages


def test_no_treaty_when_none_is_on_file_for_the_country(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A German ISIN names a country, but only US and Polish treaties are known."""
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        assert (
            _treaty_country(_dividend_pair(CurrencyCode("EUR"), Isin("DE0007164600")))
            is None
        )
    assert (
        "cgt-calc has no double taxation treaty on file for country code DE, so "
        "the report gives no treaty relief for the tax withheld from the FOO "
        "dividend on 2024-06-03. Check where the paying company is resident and "
        "work out any relief under the UK's treaty with that country outside "
        "cgt-calc."
    ) in caplog.messages


def test_no_treaty_when_withholding_does_not_match_the_treaty_rate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Relief is refused when the rate deducted is not the treaty rate.

    30% of the 100 dividend is 30.00 withheld, where the US rate of 15%
    expects 15.00.
    """
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        assert (
            _treaty_country(
                _dividend_pair(CurrencyCode("GBP"), US_ISIN, Decimal("0.30"))
            )
            is None
        )
    assert (
        "Tax of 30.00 withheld from the FOO dividend on 2024-06-03 does not match "
        "the 15.00 (15%) that cgt-calc expects for USA, so the report gives it no "
        "treaty relief. Check the tax against your broker's statement and work out "
        "any relief outside cgt-calc."
    ) in caplog.messages


def test_withholding_mismatch_names_the_rate_the_country_withholds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Poland withholds 19% but the treaty limits relief to 10%.

    The warning compares with what is withheld: 19% of the 100 dividend is
    19.00, against the 30.00 taken.
    """
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        assert (
            _treaty_country(_dividend_pair(CurrencyCode("PLN"), None, Decimal("0.30")))
            is None
        )
    assert (
        "Tax of 30.00 withheld from the FOO dividend on 2024-06-03 does not match "
        "the 19.00 (19%) that cgt-calc expects for Poland, so the report gives it "
        "no treaty relief. Check the tax against your broker's statement and work "
        "out any relief outside cgt-calc."
    ) in caplog.messages


def test_bond_fund_withholding_is_left_out_of_the_interest_tax(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bond fund's distribution is interest, but its withheld tax is in no total.

    The 100 distribution counts as foreign interest. The 15.00 withheld from
    it is not interest tax, and the warning says so.
    """
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        report = _report(
            _dividend_pair(CurrencyCode("USD"), US_ISIN), interest_fund_tickers=["FOO"]
        )
    assert report.total_foreign_interest == Decimal(100)
    assert report.total_interest_tax == Decimal(0)
    assert (
        "Tax withheld from the FOO bond fund distribution on 2024-06-03 is not "
        "counted in the report's interest tax paid, and the report gives it no "
        "treaty relief. Take the tax from your broker's statement and work out any "
        "foreign tax relief outside cgt-calc."
    ) in caplog.messages
