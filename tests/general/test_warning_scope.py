"""Tests for which warnings a run is allowed to emit."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import CurrencyCode
from cgt_calc.share_prices import SharePrices
from cgt_calc.spin_off_handler import SpinOffHandler

from .calc_test_data import (
    buy_transaction,
    dividend_tax_transaction,
    dividend_transaction,
    sell_transaction,
)

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction

# The tax year under test, plus one date inside it and one in an earlier year,
# so that a transaction can be placed inside or outside the reported year.
TAX_YEAR = 2024
IN_YEAR_DATE = datetime.date(2024, 6, 3)
EARLIER_DATE = datetime.date(2022, 6, 3)


def _calculator(
    transactions: list[BrokerTransaction],
) -> CapitalGainsCalculator:
    """Create a calculator with a flat 1:1 USD rate for the dates in use."""
    gbp_prices = {t.date: {CurrencyCode("USD"): Decimal(1)} for t in transactions}
    currency_converter = CurrencyConverter(None, gbp_prices)
    return CapitalGainsCalculator(
        TAX_YEAR,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        SharePrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


def _dividend_with_unexpected_withholding(
    date: datetime.date,
) -> list[BrokerTransaction]:
    """Dividend taxed at 30% at source, twice the USA treaty rate."""
    return [
        dividend_transaction(date, "FOO", 100),
        dividend_tax_transaction(date, "FOO", 30),
    ]


@pytest.mark.parametrize(
    ("date", "expected_warnings"),
    [
        pytest.param(IN_YEAR_DATE, 1, id="in-year"),
        pytest.param(EARLIER_DATE, 0, id="earlier-year"),
    ],
)
def test_treaty_mismatch_warning_scoped_to_tax_year(
    date: datetime.date,
    expected_warnings: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only dividends of the computed year may warn about the treaty."""
    transactions = _dividend_with_unexpected_withholding(date)
    calculator = _calculator(transactions)

    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        calculator.prepare_history(transactions)
        calculator.calculate_capital_gain()

    warnings = [
        record
        for record in caplog.records
        if "double taxation treaty does not match" in record.message
    ]
    assert len(warnings) == expected_warnings


def test_bed_and_breakfast_in_tax_year_is_logged_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Matching a disposal to a repurchase is a record, not a problem."""
    transactions = [
        buy_transaction(datetime.date(2024, 5, 1), "FOO", 10, 10, 0, -100),
        sell_transaction(datetime.date(2024, 6, 3), "FOO", 10, 12, 0, 120),
        buy_transaction(datetime.date(2024, 6, 10), "FOO", 10, 11, 0, -110),
    ]
    calculator = _calculator(transactions)

    with caplog.at_level(logging.INFO, logger="cgt_calc.matching"):
        calculator.prepare_history(transactions)
        calculator.calculate_capital_gain()

    bed_and_breakfast = [
        record for record in caplog.records if "Bed & breakfast match" in record.message
    ]
    assert len(bed_and_breakfast) == 1
    assert bed_and_breakfast[0].levelno == logging.INFO
    assert not [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]


def test_bed_and_breakfast_outside_tax_year_is_logged_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The history walk records old matches without user-facing output."""
    transactions = [
        buy_transaction(datetime.date(2023, 5, 1), "FOO", 10, 10, 0, -100),
        sell_transaction(datetime.date(2023, 6, 3), "FOO", 10, 12, 0, 120),
        buy_transaction(datetime.date(2023, 6, 10), "FOO", 10, 11, 0, -110),
    ]
    calculator = _calculator(transactions)

    with caplog.at_level(logging.DEBUG, logger="cgt_calc.matching"):
        calculator.prepare_history(transactions)
        calculator.calculate_capital_gain()

    bed_and_breakfast = [
        record for record in caplog.records if "Bed & breakfast match" in record.message
    ]
    assert len(bed_and_breakfast) == 1
    assert bed_and_breakfast[0].levelno == logging.DEBUG
    assert not [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
