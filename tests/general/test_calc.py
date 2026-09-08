"""Unit and integration tests."""

from __future__ import annotations

import copy
import datetime
from decimal import Decimal, localcontext
from enum import Enum
import itertools
import logging
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.const import BALANCE_CHECK_CONTEXT_ROWS, RENAME_DESCRIPTION_PREFIX
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import (
    AmountMissingError,
    CalculationError,
    InvalidTransactionError,
    IsinMissingError,
    PriceMissingError,
    QuantityNotPositiveError,
)
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator, main
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalGainsReport,
    CurrencyCode,
    Isin,
    Position,
    RuleType,
    TransactionSource,
)
from cgt_calc.parsers.broker_registry import _transaction_sort_key
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.rename_planning import RENAME_DAY_UNSUPPORTED_ACTIONS
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.stock_splits import (
    QUANTITY_DECREASING_ACTIONS,
    QUANTITY_INCREASING_ACTIONS,
)
from cgt_calc.util import round_decimal
from tests.utils import build_cmd, report_path

from .calc_test_data import (
    GBP,
    buy_transaction,
    calc_basic_data,
    eri_transaction,
    sell_transaction,
    split_transaction,
    transaction,
    transfer_transaction,
)
from .calc_test_data_2 import calc_basic_data_2

if TYPE_CHECKING:
    import argparse

    from cgt_calc.model import CalculationLog


# USD to GBP exchange rate used in tests (creates repeating decimals)
USD_TO_GBP = Decimal(6) / Decimal(7)  # 0.857142857...


def gbp_from_usd(usd: str, qty: int) -> Decimal:
    """Convert USD amount to GBP for testing with repeating decimal exchange rate."""
    return Decimal(qty) * Decimal(usd) * USD_TO_GBP


def get_report(
    calculator: CapitalGainsCalculator, broker_transactions: list[BrokerTransaction]
) -> CapitalGainsReport:
    """Get calculation report.

    Hand-built transactions stand for one history written in order, the way a
    RAW file is, so they are given the provenance a parser would give them:
    one declared account, one file, and the order they are listed in. The
    calculator needs that to place a same-day row either side of a share
    reorganisation, and refuses rather than guess without it. A test that
    means two separate sources sets its own provenance.
    """
    for index, broker_transaction in enumerate(broker_transactions):
        if broker_transaction.source is None:
            broker_transaction.source = TransactionSource(
                parser="Testing",
                account="Testing account",
                file=Path("testing.csv"),
                row=index + 2,
                index=index,
                rows_in_time_order=True,
            )
    calculator.prepare_history(broker_transactions)
    prepared = copy.deepcopy(calculator.state.history)
    report = calculator.calculate_capital_gain()
    assert calculator.state.history == prepared, (
        "the matching replay wrote into the prepared history"
    )
    return report


def create_calculator(
    *,
    tax_year: int,
    balance_check: bool = True,
    cgt_exempt_tickers: list[str] | None = None,
) -> CapitalGainsCalculator:
    """Create a calculator with standard test configuration.

    Mirrors the command line: the tax year has to be given, by name, and
    the balance check is on unless a test turns it off. The converter
    comes from the factory so it follows the runtime mode, sharing the
    recorded rates file with the command-line runs: a missing rate is
    fetched and recorded under plain pytest and refused, with a message
    saying how to record it, under the strict pre-commit and CI runs.
    """
    currency_converter = CurrencyConverter.create(Path("tests/exchange_rates_data.csv"))
    price_fetcher = CurrentPriceFetcher(currency_converter, {}, {})
    return CapitalGainsCalculator(
        tax_year,
        currency_converter,
        IsinConverter(),
        price_fetcher,
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        cgt_exempt_tickers=cgt_exempt_tickers,
        balance_check=balance_check,
    )


def test_main_prints_help_when_no_arguments() -> None:
    """Ensure CLI prints help text when invoked without arguments."""
    result = subprocess.run(
        [sys.executable, "-m", "cgt_calc.main"],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "Calculate UK capital gains" in result.stdout
    assert "--no-pdflatex" in result.stdout


def test_no_report_completion_message() -> None:
    """Ensure a terminal-only run does not claim to generate a report."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cgt_calc.main",
            "--year",
            "2022",
            "--raw-file",
            "tests/raw/data/test_data.csv",
            "--no-balance-check",
            "--no-report",
            "--exchange-rates-file",
            "tests/exchange_rates_data.csv",
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert stderr_lines[-1] == "Done! Calculations complete (PDF generation skipped)."


def test_interest_tax_totals_are_positive() -> None:
    """Ensure interest tax totals are reported as positive amounts."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(
        None, {date: {CurrencyCode("USD"): Decimal(1)}}
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    broker_transactions = [
        BrokerTransaction(
            date=date,
            action=ActionType.INTEREST_TAX,
            symbol=None,
            description="NRA Tax Adj",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("-20.31"),
            currency=CurrencyCode("USD"),
            broker="Charles Schwab",
        )
    ]
    report = get_report(calculator, broker_transactions)
    assert report.total_interest_tax == Decimal("20.31")


def test_interest_tax_reversals_cancel_across_months() -> None:
    """A withholding reversed in a later month must net to zero tax."""
    march = datetime.date(2025, 3, 1)
    april = datetime.date(2025, 4, 1)
    currency_converter = CurrencyConverter(
        None,
        {
            march: {CurrencyCode("USD"): Decimal(1)},
            april: {CurrencyCode("USD"): Decimal(1)},
        },
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    broker_transactions = [
        BrokerTransaction(
            date=march,
            action=ActionType.INTEREST_TAX,
            symbol=None,
            description="NRA Tax Adj",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("-5.00"),
            currency=CurrencyCode("USD"),
            broker="Charles Schwab",
        ),
        BrokerTransaction(
            date=april,
            action=ActionType.INTEREST_TAX,
            symbol=None,
            description="NRA Tax Adj reversal",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("5.00"),
            currency=CurrencyCode("USD"),
            broker="Charles Schwab",
        ),
    ]
    report = get_report(calculator, broker_transactions)
    assert report.total_interest_tax == Decimal(0)


ERI_ISIN = Isin("US5949181045")


def test_eri_duplicate_report_within_tolerance_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second ERI report within the 0.0001 tolerance is a harmless duplicate.

    ``add_eri`` compares consecutive reports for the same symbol/date with
    ``approx_equal(previous_price, price, Decimal("0.0001"))``. Prices this
    close should be treated as the same report arriving twice (e.g. from
    overlapping input files) and simply skipped with a warning.
    """
    date = datetime.date(2024, 6, 30)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.isin_converter.data[ERI_ISIN] = {"VWRL"}

    transactions: list[BrokerTransaction] = [
        ERITransaction(
            date=date,
            isin=ERI_ISIN,
            price=Decimal("1.00000"),
            currency=CurrencyCode("GBP"),
        ),
        ERITransaction(
            date=date,
            isin=ERI_ISIN,
            price=Decimal("1.00005"),
            currency=CurrencyCode("GBP"),
        ),
    ]

    calculator.prepare_history(transactions)

    assert "Skipping duplicated ERI transaction" in caplog.text
    assert calculator.eris[date]["VWRL"].price == Decimal("1.00000")


def test_eri_conflicting_report_raises_invalid_transaction_error() -> None:
    """A second ERI report that materially differs must raise, not be accepted.

    Regression test: ``approx_equal`` used to ignore its ``approx_quantity``
    argument and always compare against a hardcoded ``Decimal("0.01")``. Since
    ``add_eri`` calls it with a much tighter ``Decimal("0.0001")`` tolerance to
    tell apart a duplicate report from a genuinely conflicting one, that bug
    made any two ERI prices within 0.01 GBP of each other silently accepted as
    duplicates (first-seen price wins), even though they should have raised
    ``InvalidTransactionError``. Here the prices differ by 0.005, which is
    more than the intended 0.0001 tolerance but less than the old, buggy 0.01
    one.
    """
    date = datetime.date(2024, 6, 30)
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.isin_converter.data[ERI_ISIN] = {"VWRL"}

    transactions: list[BrokerTransaction] = [
        ERITransaction(
            date=date,
            isin=ERI_ISIN,
            price=Decimal("1.0000"),
            currency=CurrencyCode("GBP"),
        ),
        ERITransaction(
            date=date,
            isin=ERI_ISIN,
            price=Decimal("1.0050"),
            currency=CurrencyCode("GBP"),
        ),
    ]

    with pytest.raises(InvalidTransactionError, match="conflicting ERI report"):
        calculator.prepare_history(transactions)


@pytest.mark.parametrize(
    ("isin", "price", "error"),
    [
        (None, Decimal(1), IsinMissingError),
        (ERI_ISIN, None, PriceMissingError),
        (ERI_ISIN, Decimal(-1), InvalidTransactionError),
        (ERI_ISIN, Decimal("NaN"), InvalidTransactionError),
    ],
)
def test_eri_missing_required_data_has_a_transaction_error(
    isin: Isin | None,
    price: Decimal | None,
    error: type[InvalidTransactionError],
) -> None:
    """Malformed ERI input identifies the transaction instead of asserting."""
    transaction = BrokerTransaction(
        date=datetime.date(2024, 6, 30),
        action=ActionType.EXCESS_REPORTED_INCOME,
        symbol=None,
        description="",
        quantity=None,
        price=price,
        fees=Decimal(0),
        amount=None,
        currency=CurrencyCode("GBP"),
        broker="ERI input",
        isin=isin,
    )

    with pytest.raises(error):
        create_calculator(tax_year=2024, balance_check=False).prepare_history(
            [transaction]
        )


def test_dividend_and_tax_currency_mismatch_has_a_calculation_error() -> None:
    """Inconsistent income rows produce a user-facing data error."""
    day = datetime.date(2024, 6, 3)
    transactions = [
        BrokerTransaction(
            date=day,
            action=action,
            symbol="FOO",
            description="dividend",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=amount,
            currency=currency,
            broker="Test",
        )
        for action, amount, currency in [
            (ActionType.DIVIDEND, Decimal(100), CurrencyCode("USD")),
            (ActionType.DIVIDEND_TAX, Decimal(-15), CurrencyCode("GBP")),
        ]
    ]
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.prepare_history(transactions)

    with pytest.raises(CalculationError, match="currencies do not match"):
        calculator.calculate_capital_gain()


@pytest.mark.parametrize(
    ("amount", "error_match"),
    [
        (Decimal(1), "must not be positive"),
        (Decimal("NaN"), "must be a finite number"),
        (Decimal("Infinity"), "must be a finite number"),
        (Decimal("-Infinity"), "must be a finite number"),
    ],
)
def test_invalid_management_fee_has_a_transaction_error(
    amount: Decimal, error_match: str
) -> None:
    """A fee that would corrupt pooled cost is rejected before an invariant fires."""
    fee = BrokerTransaction(
        date=datetime.date(2024, 6, 3),
        action=ActionType.FEE,
        symbol="FOO",
        description="management fee",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=amount,
        currency=CurrencyCode("GBP"),
        broker="Test",
    )

    with pytest.raises(InvalidTransactionError, match=error_match):
        create_calculator(tax_year=2024, balance_check=False).prepare_history([fee])


def test_zero_management_fee_is_accepted() -> None:
    """A zero fee leaves pooled cost unchanged and remains valid input."""
    fee = BrokerTransaction(
        date=datetime.date(2024, 6, 3),
        action=ActionType.FEE,
        symbol="FOO",
        description="management fee",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode("GBP"),
        broker="Test",
    )

    create_calculator(tax_year=2024, balance_check=False).prepare_history([fee])


@pytest.mark.parametrize(
    (
        "tax_year",
        "broker_transactions",
        "expected",
        "expected_unrealized",
        "gbp_prices",
        "current_prices",
        "expected_uk_interest",
        "expected_foreign_interest",
        "expected_dividend",
        "expected_dividend_gain",
        "calculation_log",
        "calculation_log_yields",
    ),
    calc_basic_data + calc_basic_data_2,
)
def test_basic(
    tax_year: int,
    broker_transactions: list[BrokerTransaction],
    expected: float,
    expected_unrealized: float | None,
    gbp_prices: dict[datetime.date, dict[CurrencyCode, Decimal]] | None,
    current_prices: dict[str, Decimal | None] | None,
    expected_uk_interest: float,
    expected_foreign_interest: float,
    expected_dividend: float,
    expected_dividend_gain: float,
    calculation_log: CalculationLog | None,
    calculation_log_yields: CalculationLog | None,
) -> None:
    """Generate basic tests for test data."""
    if gbp_prices is None:
        gbp_prices = {
            t.date: {CurrencyCode("USD"): Decimal(1)} for t in broker_transactions
        }
    currency_converter = CurrencyConverter(None, gbp_prices)
    isin_converter = IsinConverter()
    historical_prices = {
        "FOO": {datetime.date(day=5, month=7, year=2023): Decimal(90)},
        "BAR": {datetime.date(day=5, month=7, year=2023): Decimal(12)},
    }
    price_fetcher = CurrentPriceFetcher(
        currency_converter, current_prices, historical_prices
    )
    spin_off_handler = SpinOffHandler()
    spin_off_handler.cache = {"BAR": "FOO"}
    initial_prices = InitialPrices()
    calculator = CapitalGainsCalculator(
        tax_year,
        currency_converter,
        isin_converter,
        price_fetcher,
        spin_off_handler,
        initial_prices,
        interest_fund_tickers=["FOO"],
        calc_unrealized_gains=expected_unrealized is not None,
    )
    report = get_report(calculator, broker_transactions)
    assert report.total_gain() == round_decimal(Decimal(expected), 2)
    print(str(report))
    if expected_unrealized is not None:
        assert report.total_unrealized_gains() == round_decimal(
            Decimal(expected_unrealized), 2
        )
    assert round_decimal(report.total_uk_interest, 2) == round_decimal(
        Decimal(expected_uk_interest), 2
    )
    assert round_decimal(report.total_foreign_interest, 2) == round_decimal(
        Decimal(expected_foreign_interest), 2
    )
    assert round_decimal(report.total_dividends_amount(), 2) == round_decimal(
        Decimal(expected_dividend), 2
    )
    assert round_decimal(report.total_dividend_taxable_gain(), 2) == round_decimal(
        Decimal(expected_dividend_gain), 2
    )
    if calculation_log is not None:
        result_log = report.calculation_log
        assert len(result_log) == len(calculation_log), (
            f"Actual:\n{result_log}\n\nExpected:\n{calculation_log}\n\n"
        )
        for date_index, expected_entries_map in calculation_log.items():
            assert date_index in result_log
            result_entries_map = result_log[date_index]
            print(date_index)
            print(result_entries_map)
            assert len(result_entries_map) == len(expected_entries_map)
            for entries_type, expected_entries_list in expected_entries_map.items():
                assert entries_type in result_entries_map
                result_entries_list = result_entries_map[entries_type]
                assert len(result_entries_list) == len(expected_entries_list)
                for i, expected_entry in enumerate(expected_entries_list):
                    result_entry = result_entries_list[i]
                    assert result_entry.rule_type == expected_entry.rule_type
                    assert result_entry.quantity == expected_entry.quantity
                    assert result_entry.new_quantity == expected_entry.new_quantity
                    assert round_decimal(
                        result_entry.new_pool_cost, 4
                    ) == round_decimal(expected_entry.new_pool_cost, 4)
                    assert round_decimal(result_entry.gain, 4) == round_decimal(
                        expected_entry.gain, 4
                    )
                    assert round_decimal(result_entry.amount, 4) == round_decimal(
                        expected_entry.amount, 4
                    )
                    assert round_decimal(
                        result_entry.allowable_cost, 4
                    ) == round_decimal(expected_entry.allowable_cost, 4)
                    assert (
                        result_entry.bed_and_breakfast_date_index
                        == expected_entry.bed_and_breakfast_date_index
                    )
                    assert round_decimal(result_entry.fees, 4) == round_decimal(
                        expected_entry.fees, 4
                    )

    if calculation_log_yields is not None:
        result_log = report.calculation_log_yields
        assert len(result_log) == len(calculation_log_yields)
        for date_index, expected_entries_map in calculation_log_yields.items():
            assert date_index in result_log
            result_entries_map = result_log[date_index]
            print(date_index)
            print(result_entries_map)
            assert len(result_entries_map) == len(expected_entries_map)
            for entries_type, expected_entries_list in expected_entries_map.items():
                assert entries_type in result_entries_map
                result_entries_list = result_entries_map[entries_type]
                assert len(result_entries_list) == len(expected_entries_list)
                for i, expected_entry in enumerate(expected_entries_list):
                    result_entry = result_entries_list[i]
                    assert result_entry.rule_type == expected_entry.rule_type
                    assert round_decimal(result_entry.amount, 4) == round_decimal(
                        expected_entry.amount, 4
                    )


def test_bed_and_breakfast_zero_available_quantity_skip() -> None:
    """Later acquisitions are ignored if the disposal was already satisfied."""

    currency_converter = CurrencyConverter(None, {})
    price_fetcher = CurrentPriceFetcher(currency_converter, {}, {})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        price_fetcher,
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
    )

    symbol = "TEST"
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 1, 1),
            action=ActionType.TRANSFER,
            symbol=None,
            description="deposit",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal(500),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 1, 10),
            action=ActionType.BUY,
            symbol=symbol,
            description="initial buy",
            quantity=Decimal(10),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-100),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 3, 1),
            action=ActionType.SELL,
            symbol=symbol,
            description="disposal",
            quantity=Decimal(5),
            price=Decimal(12),
            fees=Decimal(0),
            amount=Decimal(60),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 3, 5),
            action=ActionType.BUY,
            symbol=symbol,
            description="bed and breakfast buy",
            quantity=Decimal(5),
            price=Decimal(11),
            fees=Decimal(0),
            amount=Decimal(-55),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 3, 10),
            action=ActionType.BUY,
            symbol=symbol,
            description="unrelated buy",
            quantity=Decimal(3),
            price=Decimal(9),
            fees=Decimal(0),
            amount=Decimal(-27),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    # The original disposal is fully matched against the 5-share buy, so no gain.
    assert report.total_gain() == Decimal(0)

    first_match = datetime.date(2024, 3, 5)
    assert calculator.bnb_list[first_match][symbol].quantity == Decimal(5)

    second_match = datetime.date(2024, 3, 10)
    assert symbol not in calculator.bnb_list.get(second_match, {})


def test_proportional_disposal_no_rounding_error() -> None:
    """Test that disposing all shares doesn't cause rounding errors.

    This test verifies the fix for the issue where sequential disposals
    using the divide-then-multiply pattern could accumulate rounding errors,
    causing assertions like "current amount -1E-23" to fail.

    The fix reorders operations from quantity * (amount / total) to
    (quantity * amount) / total, which ensures exact cancellation when
    disposing all shares: (total * amount) / total = amount.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    symbol = "TEST"

    # Create scenario that would trigger rounding errors:
    # Buy 3 shares for £10, then dispose all 3 shares
    # Using 3 creates a repeating decimal (10/3 = 3.333...) which triggers the issue
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),  # Tax year 2024 (Apr 6, 2024 - Apr 5, 2025)
            action=ActionType.BUY,
            symbol=symbol,
            description="buy 3 shares",
            quantity=Decimal(3),
            price=Decimal("3.33"),
            fees=Decimal("0.01"),
            amount=Decimal("-10.00"),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 6, 1),  # Tax year 2024
            action=ActionType.SELL,
            symbol=symbol,
            description="sell all 3 shares",
            quantity=Decimal(3),
            price=Decimal("5.00"),
            fees=Decimal(0),
            amount=Decimal("15.00"),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    # This should complete without AssertionError about rounding errors
    # Before the fix, this could fail with: AssertionError: current amount -1E-23
    report = get_report(calculator, transactions)

    # Verify the calculation completed successfully
    # The exact gain is: £15.00 proceeds - £10.00 cost = £5.00 gain
    assert report.total_gain() == Decimal("5.00")

    # Verify portfolio is now empty (all shares disposed)
    assert calculator.portfolio[symbol].quantity == Decimal(0)

    # Verify pool amount is exactly zero (no rounding errors)
    # This is the key assertion - without operation reordering,
    # the pool amount could be a tiny non-zero value like -1E-27
    assert calculator.portfolio[symbol].amount == Decimal(0), (
        "Pool amount should be exactly zero (no rounding error)"
    )


def test_high_precision_amount_no_rounding_error() -> None:
    """Test that high-precision amounts (29+ digits) don't cause rounding errors.

    This test uses USD->GBP currency conversion (6/7 exchange rate) which creates
    repeating decimals. When combined with realistic share quantities, the amounts
    accumulate 29+ significant digits of precision.

    Without the fix (28-digit precision), this fails with:
    AssertionError: current amount 2E-23
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    symbol = "ACME"

    transactions: list[BrokerTransaction] = [
        # Acquisition 1
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol=symbol,
            description="Vest",
            quantity=Decimal(10000),
            price=Decimal("50.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=-gbp_from_usd("50.00", 10000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        # Acquisition 2
        BrokerTransaction(
            date=datetime.date(2024, 6, 1),
            action=ActionType.BUY,
            symbol=symbol,
            description="Vest",
            quantity=Decimal(10000),
            price=Decimal("60.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=-gbp_from_usd("60.00", 10000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        # Sell all - triggers disposal with high-precision amounts
        BrokerTransaction(
            date=datetime.date(2024, 7, 1),
            action=ActionType.SELL,
            symbol=symbol,
            description="Sale",
            quantity=Decimal(20000),
            price=Decimal("70.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=gbp_from_usd("70.00", 20000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    # Without proper precision handling we get
    # AssertionError: current amount 2E-23
    get_report(calculator, transactions)

    assert calculator.portfolio[symbol].quantity == Decimal(0)
    assert calculator.portfolio[symbol].amount == Decimal(0), (
        f"Rounding error: {calculator.portfolio[symbol].amount}"
    )


def test_same_day_rule_all_shares_disposed_no_rounding_error() -> None:
    """Test that same day rule disposing ALL shares doesn't cause rounding errors."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    symbol = "TEST"
    same_day = datetime.date(2024, 5, 1)

    # Use 3 shares to create 1/3 repeating decimal (strongest case)
    buy_quantity = Decimal(3)
    buy_amount_gbp = -gbp_from_usd("100.00", 3)  # Creates amount/3 repeating
    sell_quantity = Decimal(3)
    sell_amount_gbp = gbp_from_usd("120.00", 3)

    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=same_day,
            action=ActionType.BUY,
            symbol=symbol,
            description="buy 3 shares USD",
            quantity=buy_quantity,
            price=Decimal("100.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=buy_amount_gbp,
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=same_day,
            action=ActionType.SELL,
            symbol=symbol,
            description="sell all 3 shares same day",
            quantity=sell_quantity,
            price=Decimal("120.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=sell_amount_gbp,
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    # No AssertionError should get thrown here
    report = get_report(calculator, transactions)

    # Verify the gain calculation
    # Buy: 3 * £100 * (6/7) = £257.14, Sell: 3 * £120 * (6/7) = £308.57
    # Gain = £308.57 - £257.14 = £51.43
    assert report.total_gain() == Decimal("51.43")

    assert calculator.portfolio[symbol].quantity == Decimal(0)
    assert calculator.portfolio[symbol].amount == Decimal(0), (
        f"Pool amount should be exactly zero after disposing all shares on same day, "
        f"got {calculator.portfolio[symbol].amount}"
    )


def _rename_transaction(date: datetime.date, old: str, new: str) -> BrokerTransaction:
    """Build a RENAME BrokerTransaction that moves the pool from old to new."""
    return BrokerTransaction(
        date=date,
        action=ActionType.RENAME,
        symbol=new,
        description=f"{RENAME_DESCRIPTION_PREFIX}{old}",
        quantity=Decimal(0),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode("GBP"),
        broker="Test",
    )


def _gbp_trade(
    date: datetime.date,
    action: ActionType,
    symbol: str,
    quantity: Decimal | int,
    amount: Decimal | int,
) -> BrokerTransaction:
    """Build a GBP BUY or SELL, whole numbers or exact decimals."""
    return BrokerTransaction(
        date=date,
        action=action,
        symbol=symbol,
        description=f"{action.name.lower()} {symbol}",
        quantity=Decimal(quantity),
        price=Decimal(amount) / Decimal(quantity),
        fees=Decimal(0),
        amount=Decimal(-amount if action is ActionType.BUY else amount),
        currency=CurrencyCode("GBP"),
        broker="Test",
    )


def _gbp_fee(date: datetime.date, symbol: str, amount: int) -> BrokerTransaction:
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
        currency=CurrencyCode("GBP"),
        broker="Test",
    )


@pytest.mark.parametrize(
    ("isin", "alias", "canonical"),
    [
        (Isin("US67066G1040"), "NVD", "NVDA"),
        (Isin("US11135F1012"), "1YD", "AVGO"),
    ],
)
def test_exchange_alias_pools_under_one_ticker(
    isin: Isin, alias: str, canonical: str
) -> None:
    """One security bought under two of its listings is one Section 104 pool.

    Trading 212 exports the Xetra line of a US share under its German code,
    so a history can hold both. Pooling and matching are keyed by ticker, so
    without normalisation the two halves never meet: the sale below has only
    half the units it needs under its own name.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    buy_alias = BrokerTransaction(
        date=datetime.date(2024, 5, 1),
        action=ActionType.BUY,
        symbol=alias,
        description=f"buy {alias}",
        quantity=Decimal(10),
        price=Decimal(10),
        fees=Decimal(0),
        amount=Decimal(-100),
        currency=CurrencyCode("GBP"),
        broker="Trading 212",
        isin=isin,
    )
    transactions: list[BrokerTransaction] = [
        buy_alias,
        BrokerTransaction(
            date=datetime.date(2024, 6, 1),
            action=ActionType.BUY,
            symbol=canonical,
            description=f"buy {canonical}",
            quantity=Decimal(10),
            price=Decimal(20),
            fees=Decimal(0),
            amount=Decimal(-200),
            currency=CurrencyCode("GBP"),
            broker="Trading 212",
            isin=isin,
        ),
        BrokerTransaction(
            date=datetime.date(2024, 9, 1),
            action=ActionType.SELL,
            symbol=canonical,
            description=f"sell {canonical}",
            quantity=Decimal(15),
            price=Decimal(30),
            fees=Decimal(0),
            amount=Decimal(450),
            currency=CurrencyCode("GBP"),
            broker="Trading 212",
            isin=isin,
        ),
    ]

    report = get_report(calculator, transactions)

    assert buy_alias.symbol == canonical
    assert alias not in calculator.portfolio
    # 20 units costing 300 in one pool: 15 sold for 450 leaves 5 costing 75.
    assert calculator.portfolio[canonical].quantity == Decimal(5)
    assert calculator.portfolio[canonical].amount == Decimal(75)
    assert report.total_gain() == Decimal(225)


def test_exchange_alias_pools_under_one_ticker_without_a_transaction_isin() -> None:
    """The alias still normalises when a broker never reports an ISIN.

    docs/extra-data-and-options.md documents putting every verified ticker
    for an ISIN on one cache row, such as ``US67066G1040,NVD,NVDA``. A broker
    that never supplies an ISIN has its ISIN resolved from that row alone, so
    the alias has to be applied there too: resolving the ISIN without then
    normalising the ticker would leave this pooling as two holdings, exactly
    as it did before either check existed.
    """
    isin = Isin("US67066G1040")
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.isin_converter.data[isin] = {"NVD", "NVDA"}

    buy_alias = BrokerTransaction(
        date=datetime.date(2024, 5, 1),
        action=ActionType.BUY,
        symbol="NVD",
        description="buy NVD",
        quantity=Decimal(10),
        price=Decimal(10),
        fees=Decimal(0),
        amount=Decimal(-100),
        currency=CurrencyCode("GBP"),
        broker="Test",
    )
    transactions: list[BrokerTransaction] = [
        buy_alias,
        BrokerTransaction(
            date=datetime.date(2024, 6, 1),
            action=ActionType.BUY,
            symbol="NVDA",
            description="buy NVDA",
            quantity=Decimal(10),
            price=Decimal(20),
            fees=Decimal(0),
            amount=Decimal(-200),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 9, 1),
            action=ActionType.SELL,
            symbol="NVDA",
            description="sell NVDA",
            quantity=Decimal(5),
            price=Decimal(30),
            fees=Decimal(0),
            amount=Decimal(150),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    assert buy_alias.symbol == "NVDA"
    assert "NVD" not in calculator.portfolio
    # 20 units costing 300 in one pool: 5 sold for 150 leaves 15 costing 225.
    assert calculator.portfolio["NVDA"].quantity == Decimal(15)
    assert calculator.portfolio["NVDA"].amount == Decimal(225)
    assert report.total_gain() == Decimal(75)


def test_transaction_ticker_cannot_reassign_a_reference_owned_isin() -> None:
    """A transaction cannot silently move a reference-linked ticker to a new ISIN.

    Regression test: the ISIN/ticker link is also read to route ERI reports
    to the right pool via ``get_symbols``. If a transaction were allowed to
    quietly reassign a ticker reference already gave to a different ISIN,
    an ERI report for that other ISIN would be applied to both the correct
    holding and the one that stole its ticker, inflating the wrong pool's
    cost. This must be refused up front instead.
    """
    reassigned_isin = Isin("US0378331005")
    reference_isin = Isin("US5949181045")
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.isin_converter.data[reference_isin] = {"SAME"}

    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="SAME",
            description="buy SAME",
            quantity=Decimal(10),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-100),
            currency=CurrencyCode("GBP"),
            broker="Test",
            isin=reassigned_isin,
        ),
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="B2",
            description="buy B2",
            quantity=Decimal(10),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-100),
            currency=CurrencyCode("GBP"),
            broker="Test",
            isin=reference_isin,
        ),
    ]

    with pytest.raises(InvalidTransactionError, match="already used for"):
        calculator.prepare_history(transactions)


def test_rename_transfers_pool_to_new_ticker() -> None:
    """RENAME moves pool cost and quantity from old to new ticker; logs a RENAME entry."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    rename_date = datetime.date(2024, 5, 10)
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD",
            quantity=Decimal(100),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        _rename_transaction(rename_date, "OLD", "NEW"),
    ]

    report = get_report(calculator, transactions)

    assert "OLD" not in calculator.portfolio
    assert calculator.portfolio["NEW"].quantity == Decimal(100)
    assert calculator.portfolio["NEW"].amount == Decimal(1000)
    assert report.total_gain() == Decimal(0)

    entries = report.calculation_log[rename_date]["rename$OLD"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.rule_type is RuleType.RENAME
    assert entry.renamed_to == "NEW"
    assert entry.quantity == Decimal(100)
    assert entry.allowable_cost == Decimal(1000)
    assert entry.new_quantity == Decimal(100)
    assert entry.new_pool_cost == Decimal(1000)


@pytest.mark.parametrize("description", ["", RENAME_DESCRIPTION_PREFIX])
def test_rename_without_old_symbol_has_a_transaction_error(description: str) -> None:
    """A rename row that cannot name its source is invalid input."""
    transaction = _rename_transaction(datetime.date(2024, 5, 10), "OLD", "NEW")
    transaction.description = description

    with pytest.raises(InvalidTransactionError, match="old symbol"):
        create_calculator(tax_year=2024, balance_check=False).prepare_history(
            [transaction]
        )


def test_rename_without_a_new_symbol_has_a_transaction_error() -> None:
    """A rename row that cannot name its destination is invalid input."""
    transaction = _rename_transaction(datetime.date(2024, 5, 10), "OLD", "NEW")
    transaction.symbol = None

    with pytest.raises(InvalidTransactionError, match="Symbol missing"):
        create_calculator(tax_year=2024, balance_check=False).prepare_history(
            [transaction]
        )


def test_section_104_disposal_uses_renamed_pool_cost() -> None:
    """After a rename, S104 disposal under NEW uses the pool cost carried from OLD."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    sell_date = datetime.date(2024, 6, 1)
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD tranche 1",
            quantity=Decimal(100),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 5, 2),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD tranche 2",
            quantity=Decimal(50),
            price=Decimal(20),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        _rename_transaction(datetime.date(2024, 5, 15), "OLD", "NEW"),
        BrokerTransaction(
            date=sell_date,
            action=ActionType.SELL,
            symbol="NEW",
            description="partial sell NEW",
            quantity=Decimal(75),
            price=Decimal(20),
            fees=Decimal(0),
            amount=Decimal(1500),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    # Pool before sale: 150 units, £2,000 cost → £13.333.../unit.
    # Sell 75 units: cost £1,000, proceeds £1,500, gain £500.
    assert report.total_gain() == Decimal("500.00")
    sell_entries = report.calculation_log[sell_date]["sell$NEW"]
    assert len(sell_entries) == 1
    entry = sell_entries[0]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.quantity == Decimal(75)
    assert entry.allowable_cost == Decimal(1000)
    assert entry.gain == Decimal(500)
    # Remaining pool: 75 units at the same £13.333.../unit = £1,000.
    assert entry.new_quantity == Decimal(75)
    assert round_decimal(entry.new_pool_cost, 4) == Decimal(1000)
    assert calculator.portfolio["NEW"].quantity == Decimal(75)
    assert round_decimal(calculator.portfolio["NEW"].amount, 4) == Decimal(1000)


def test_bed_and_breakfast_matches_across_rename() -> None:
    """Sell of OLD is B&B-matched against a buy of NEW after a rename within 30 days."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD",
            quantity=Decimal(100),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 5, 10),
            action=ActionType.SELL,
            symbol="OLD",
            description="sell OLD",
            quantity=Decimal(100),
            price=Decimal(8),
            fees=Decimal(0),
            amount=Decimal(800),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
        _rename_transaction(datetime.date(2024, 5, 15), "OLD", "NEW"),
        BrokerTransaction(
            date=datetime.date(2024, 5, 20),
            action=ActionType.BUY,
            symbol="NEW",
            description="rebuy under NEW",
            quantity=Decimal(100),
            price=Decimal(9),
            fees=Decimal(0),
            amount=Decimal(-900),
            currency=CurrencyCode("GBP"),
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    sell_date = datetime.date(2024, 5, 10)
    rebuy_date = datetime.date(2024, 5, 20)
    sell_entries = report.calculation_log[sell_date]["sell$OLD"]
    assert len(sell_entries) == 1
    sell_entry = sell_entries[0]
    assert sell_entry.rule_type is RuleType.BED_AND_BREAKFAST
    assert sell_entry.bed_and_breakfast_date_index == rebuy_date
    assert sell_entry.allowable_cost == Decimal(900)

    # 100 sold at £8 = £800 proceeds against £900 B&B cost → £100 loss.
    assert report.total_gain() == Decimal(-100)


RENAME_DAY = datetime.date(2024, 5, 10)


def _rename_day_rows(
    sale_spelling: str, order: tuple[str, ...], sale_quantity: int
) -> list[BrokerTransaction]:
    """Open with 100 OLD at GBP 10; buy 50 NEW at GBP 15 and sell at GBP 20."""
    rows = {
        "rename": _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        "sell": _gbp_trade(
            RENAME_DAY,
            ActionType.SELL,
            sale_spelling,
            sale_quantity,
            sale_quantity * 20,
        ),
        "buy": _gbp_trade(RENAME_DAY, ActionType.BUY, "NEW", 50, 750),
    }
    return [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        *(rows[name] for name in order),
    ]


@pytest.mark.parametrize(
    ("sale_quantity", "allowable_cost", "gain", "closing_quantity"),
    [(50, 750, 250, 100), (120, 1450, 950, 30)],
    ids=["same-day match", "purchase needed for capacity"],
)
@pytest.mark.parametrize("sale_spelling", ["OLD", "NEW"])
@pytest.mark.parametrize(
    "order",
    list(itertools.permutations(("rename", "sell", "buy"))),
    ids=",".join,
)
def test_same_day_match_carries_the_disposal_date_rename(
    sale_spelling: str,
    order: tuple[str, ...],
    sale_quantity: int,
    allowable_cost: int,
    gain: int,
    closing_quantity: int,
) -> None:
    """A sale is matched with the day's purchase under either of its names.

    Selling 120 requires the day's purchase to count towards capacity even
    when the sale is read first. Each case must agree across all row orders.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)

    report = get_report(
        calculator, _rename_day_rows(sale_spelling, order, sale_quantity)
    )

    entries = report.calculation_log[RENAME_DAY][f"sell${sale_spelling}"]
    assert len(entries) == (1 if sale_quantity == 50 else 2)
    assert entries[0].rule_type is RuleType.SAME_DAY
    assert entries[0].quantity == Decimal(50)
    assert entries[0].allowable_cost == Decimal(750)
    assert entries[0].gain == Decimal(250)
    if sale_quantity == 120:
        assert entries[1].rule_type is RuleType.SECTION_104
        assert entries[1].quantity == Decimal(70)
        assert entries[1].allowable_cost == Decimal(700)
        assert entries[1].gain == Decimal(700)
    assert report.allowable_costs == Decimal(allowable_cost)
    assert report.total_gain() == Decimal(gain)
    assert calculator.portfolio["NEW"] == Position(
        Decimal(closing_quantity), Decimal(closing_quantity * 10)
    )


def test_a_sale_under_the_new_name_draws_on_the_pool_under_the_old_one() -> None:
    """The holding is one pool whichever of the day's names the sale states.

    The rename is applied at the end of the day, so the pool is still under
    `OLD` while the sale, written `NEW`, is identified. Reading the pool from
    the row's own ticker finds an empty one.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "NEW", 50, 1000),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[RENAME_DAY]["sell$NEW"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.quantity == Decimal(50)
    assert entry.allowable_cost == Decimal(500)
    assert entry.gain == Decimal(500)
    assert report.total_gain() == Decimal(500)
    assert calculator.portfolio["NEW"] == Position(Decimal(50), Decimal(500))


def test_a_sale_the_day_s_purchase_only_half_covers_takes_the_rest_from_the_pool() -> (
    None
):
    """What the same-day rule does not identify is identified from the pool.

    The purchase and the sale are both written `NEW`; the pool they fall back
    on is still under `OLD` until the day closes.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "NEW", 20, 300),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "NEW", 50, 1000),
    ]

    report = get_report(calculator, transactions)

    same_day, section_104 = report.calculation_log[RENAME_DAY]["sell$NEW"]
    assert same_day.rule_type is RuleType.SAME_DAY
    assert same_day.quantity == Decimal(20)
    assert same_day.allowable_cost == Decimal(300)
    assert section_104.rule_type is RuleType.SECTION_104
    assert section_104.quantity == Decimal(30)
    assert section_104.allowable_cost == Decimal(300)
    assert report.total_gain() == Decimal(400)
    assert calculator.portfolio["NEW"] == Position(Decimal(70), Decimal(700))


def test_bed_and_breakfast_carries_the_disposal_date_rename() -> None:
    """A repurchase under the new name is still a repurchase of what was sold.

    The rename is recorded on the day of the sale, so the 30-day search has
    to start under the name it leaves the holding under (TCGA 1992 s106A).
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 100, 800),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _gbp_trade(datetime.date(2024, 5, 20), ActionType.BUY, "NEW", 100, 900),
    ]

    report = get_report(calculator, transactions)

    entries = report.calculation_log[RENAME_DAY]["sell$OLD"]
    assert len(entries) == 1
    assert entries[0].rule_type is RuleType.BED_AND_BREAKFAST
    assert entries[0].bed_and_breakfast_date_index == datetime.date(2024, 5, 20)
    assert entries[0].allowable_cost == Decimal(900)
    # 100 sold at £8 = £800 proceeds against £900 of repurchase cost.
    assert report.total_gain() == Decimal(-100)


def test_an_earlier_claim_cannot_take_the_rename_day_disposal_s_shares() -> None:
    """The day's own disposal has first claim on the day's purchase.

    The same-day rule comes before the 30-day rule (CG51560), so the earlier
    sale may only be identified against what the rename day's own sale leaves.
    Reserving that share only under the ticker the purchase was recorded
    under let both sales be identified against the same 50 shares.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    earlier_sale = datetime.date(2024, 5, 5)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 200, 2000),
        _gbp_trade(earlier_sale, ActionType.SELL, "OLD", 50, 1000),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "NEW", 50, 750),
    ]

    report = get_report(calculator, transactions)

    (earlier_entry,) = report.calculation_log[earlier_sale]["sell$OLD"]
    assert earlier_entry.rule_type is RuleType.SECTION_104
    assert earlier_entry.allowable_cost == Decimal(500)
    assert earlier_entry.gain == Decimal(500)
    (rename_day_entry,) = report.calculation_log[RENAME_DAY]["sell$OLD"]
    assert rename_day_entry.rule_type is RuleType.SAME_DAY
    assert rename_day_entry.allowable_cost == Decimal(750)
    assert rename_day_entry.gain == Decimal(250)
    assert report.total_gain() == Decimal(750)
    assert report.allowable_costs == Decimal(1250)
    assert calculator.portfolio["NEW"] == Position(Decimal(150), Decimal(1500))


def test_a_cost_only_pool_under_a_renamed_name_is_refused() -> None:
    """A fee recorded under the other name is cost the disposal cannot see.

    A management fee is pooled cost with no shares. Under the name the shares
    are held in it is part of what they cost; under the name the day's rename
    connects to them it only joins that pool at the end of the day, after the
    disposal has priced itself from the shares alone.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_fee(RENAME_DAY, "NEW", 500),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot compute the disposal of OLD on {RENAME_DAY}: the day's renames "
        "make NEW and OLD one holding, and pooled cost with no shares of its "
        "own is recorded under NEW"
    )


def test_carried_cost_under_a_renamed_name_is_refused_behind_a_purchase() -> None:
    """A purchase under that name does not settle what the name carried in.

    The fee came in a week before the disposal date, so the name holds cost
    with no shares behind it until the day's purchase lands. Reading the pool
    after that purchase shows a positive quantity and hides the fee.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_fee(datetime.date(2024, 5, 2), "NEW", 500),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "NEW", 10, 100),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot compute the disposal of OLD on {RENAME_DAY}: the day's renames "
        "make NEW and OLD one holding, and pooled cost with no shares of its "
        "own is recorded under NEW"
    )


def test_carried_cost_is_refused_under_the_name_the_sale_states() -> None:
    """The refusal reads the same when the sale states the carrying name.

    The pool is under `OLD` and the fee under `NEW`, but the sale is written
    `NEW` too. Naming the whole holding keeps the message from saying it is
    one holding with itself.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_fee(datetime.date(2024, 5, 2), "NEW", 500),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "NEW", 50, 1000),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot compute the disposal of NEW on {RENAME_DAY}: the day's renames "
        "make NEW and OLD one holding, and pooled cost with no shares of its "
        "own is recorded under NEW"
    )


def test_carried_cost_under_the_held_name_is_part_of_what_it_cost() -> None:
    """The same two rows under the name the shares are held in are pooled."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_fee(datetime.date(2024, 5, 2), "OLD", 500),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "OLD", 10, 100),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    report = get_report(calculator, transactions)

    same_day, section_104 = report.calculation_log[RENAME_DAY]["sell$OLD"]
    assert same_day.rule_type is RuleType.SAME_DAY
    assert same_day.quantity == Decimal(10)
    assert same_day.allowable_cost == Decimal(100)
    assert section_104.rule_type is RuleType.SECTION_104
    assert section_104.quantity == Decimal(40)
    assert section_104.allowable_cost == Decimal(600)
    assert report.total_gain() == Decimal(300)
    assert calculator.portfolio["NEW"] == Position(Decimal(60), Decimal(900))


def test_a_cost_only_pool_under_the_held_name_is_part_of_what_it_cost() -> None:
    """The same fee recorded under the name the shares are held in is pooled."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_fee(RENAME_DAY, "OLD", 500),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[RENAME_DAY]["sell$OLD"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.allowable_cost == Decimal(750)
    assert entry.gain == Decimal(250)
    assert calculator.portfolio["NEW"] == Position(Decimal(50), Decimal(750))


def test_carried_cost_under_the_disposal_s_own_name_needs_no_refusal() -> None:
    """Cost in the pool the disposal prices from is not stranded by a rename.

    The fee, the purchase and the sale are all recorded under `OLD`, which is
    the pool the disposal draws on. What the rename does with that pool at the
    end of the day hides nothing from it.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(datetime.date(2024, 5, 2), ActionType.SELL, "OLD", 100, 1000),
        _gbp_fee(datetime.date(2024, 5, 3), "OLD", 500),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[RENAME_DAY]["sell$OLD"]
    assert entry.rule_type is RuleType.SAME_DAY
    assert entry.quantity == Decimal(50)
    assert entry.allowable_cost == Decimal(500)
    assert entry.gain == Decimal(500)
    assert report.total_gain() == Decimal(500)
    assert report.allowable_costs == Decimal(1500)
    assert calculator.portfolio["NEW"] == Position(Decimal(50), Decimal(1000))


def test_sales_under_two_names_of_one_holding_are_refused() -> None:
    """One acquisition cannot be shared between two disposal rows.

    Both sales are of the same holding and both are identified against the
    day's purchase, but each disposal row is matched against that purchase
    under a single name, so the second row would read the whole purchase
    again.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "NEW", 50, 1000),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 25, 500),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "NEW", 25, 500),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot compute the disposal of NEW on {RENAME_DAY}: the day's "
        "renames make NEW and OLD one holding, and a disposal recorded under "
        "another of those names has already been identified against the day's "
        "purchase."
    )


def test_a_holding_renamed_twice_on_a_disposal_date_is_refused() -> None:
    """Where the shares end up depends on which of the two rename rows ran.

    Refused as the day opens, before any of its rows is read, because a
    holding renamed twice has no one name for the day to close under.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "MID"),
        _rename_transaction(RENAME_DAY, "MID", "NEW"),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot apply the renames of OLD on {RENAME_DAY}: it is renamed to "
        "MID, and MID is renamed to NEW, the same day."
    )


def test_a_rename_pooling_two_holdings_on_a_disposal_date_is_refused() -> None:
    """Two pools meet only at the end of the day, so neither is the one sold."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "NEW", 30, 600),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot compute the disposal of OLD on {RENAME_DAY}: the day's renames "
        "pool NEW and OLD together, and each of them holds shares of its own."
    )


def test_a_day_buying_under_both_names_of_one_holding_is_refused() -> None:
    """One day's purchases are one acquisition, and it has one cost."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "OLD", 10, 150),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "NEW", 10, 250),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 5, 100),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot compute the disposal of OLD on {RENAME_DAY}: the day's renames "
        "make NEW and OLD one holding, and shares were bought under each of "
        "them today."
    )


@pytest.mark.parametrize("rename_first", [True, False], ids=["rename first", "last"])
def test_a_rename_day_refuses_more_units_than_the_whole_holding_has(
    *, rename_first: bool
) -> None:
    """Selling 60 under one alias leaves only 40 available under the other.

    The second sale must be refused whether the rename is read first or last.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    rename = _rename_transaction(RENAME_DAY, "OLD", "NEW")
    sales = [
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 60, 900),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "NEW", 60, 900),
    ]
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        *([rename, *sales] if rename_first else [*sales, rename]),
    ]

    with pytest.raises(
        InvalidTransactionError, match=r"more than the available balance\(40\)"
    ):
        get_report(calculator, transactions)


def _malformed_purchase(
    date: datetime.date, symbol: str, quantity: str
) -> BrokerTransaction:
    """Build a purchase whose stated count is not a positive number."""
    return BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol=symbol,
        description=f"malformed buy {symbol}",
        quantity=Decimal(quantity),
        price=Decimal(20),
        fees=Decimal(0),
        amount=Decimal(-1000),
        currency=CurrencyCode("GBP"),
        broker="Test",
    )


@pytest.mark.parametrize("quantity", ["-50", "0"], ids=["negative", "zero"])
@pytest.mark.parametrize("buy_first", [True, False], ids=["buy first", "sale first"])
def test_a_malformed_purchase_on_a_rename_day_blames_its_own_row(
    *, quantity: str, buy_first: bool
) -> None:
    """The row with the impossible count is named, not the sale it shrinks.

    A rename day works out what the whole holding can give up before any of
    its rows is read, so a count of less than nothing there takes shares off
    that total and the sale of the whole holding is the row refused for it.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    sale = _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 100, 2000)
    malformed = _malformed_purchase(RENAME_DAY, "NEW", quantity)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        *([malformed, sale] if buy_first else [sale, malformed]),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(QuantityNotPositiveError) as excinfo:
        get_report(calculator, transactions)

    assert "symbol='NEW'" in str(excinfo.value)


def test_a_purchase_under_the_retired_name_is_there_to_sell_later() -> None:
    """A purchase under the retired ticker is available under NEW on a later day."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _gbp_trade(RENAME_DAY, ActionType.BUY, "OLD", 50, 750),
        _gbp_trade(datetime.date(2024, 5, 20), ActionType.SELL, "NEW", 150, 3000),
    ]

    report = get_report(calculator, transactions)

    assert report.total_gain() == Decimal(1250)
    assert calculator.portfolio["NEW"] == Position()


def test_the_first_pass_leaves_a_rename_day_under_the_closing_ticker() -> None:
    """Reconciliation carries both shares and their source accounts to NEW."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    opening = _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000)
    opening.source = TransactionSource(account="Account A")
    purchase = _gbp_trade(RENAME_DAY, ActionType.BUY, "OLD", 50, 750)
    purchase.source = TransactionSource(account="Account B")

    calculator.prepare_history(
        [
            opening,
            _rename_transaction(RENAME_DAY, "OLD", "NEW"),
            purchase,
        ]
    )

    assert dict(calculator.portfolio) == {"NEW": Position(Decimal(150), Decimal(1750))}
    assert dict(calculator.state.history.holding_sources) == {
        "NEW": {"Account A", "Account B"}
    }


@pytest.mark.parametrize("backwards", [False, True], ids=["in order", "backwards"])
def test_a_day_whose_only_rows_are_a_rename_chain_is_refused(
    *, backwards: bool
) -> None:
    """Refuse a chain in either order even when no disposal invokes the matcher."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    renames = [
        _rename_transaction(RENAME_DAY, "OLD", "MID"),
        _rename_transaction(RENAME_DAY, "MID", "NEW"),
    ]
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        *(reversed(renames) if backwards else renames),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot apply the renames of OLD on {RENAME_DAY}: it is renamed to "
        "MID, and MID is renamed to NEW, the same day."
    )


def test_every_action_says_what_a_rename_day_does_with_it() -> None:
    """Every action must explicitly support planning, trigger fallback, or do neither.

    Adding an action without classifying it must fail this test.
    """
    read_as_one_holding = {
        ActionType.BUY,
        ActionType.SELL,
        ActionType.RENAME,
        # A spin-off names the holding it creates, which is the only one whose
        # count it changes, so a component can take it into account.
        ActionType.SPIN_OFF,
    }
    touch_no_holding = {
        ActionType.ADJUSTMENT,
        ActionType.CANCEL_BUY,
        ActionType.CAPITAL_GAIN,
        ActionType.DIVIDEND,
        ActionType.DIVIDEND_TAX,
        ActionType.INTEREST,
        ActionType.INTEREST_TAX,
        ActionType.OPTION_ASSIGNMENT,
        ActionType.OPTION_CLOSE,
        ActionType.OPTION_EXPIRY,
        ActionType.OPTION_GRANT,
        ActionType.REINVEST_DIVIDENDS,
        ActionType.TRANSFER,
        ActionType.WIRE_FUNDS_RECEIVED,
    }
    readings = [read_as_one_holding, touch_no_holding, RENAME_DAY_UNSUPPORTED_ACTIONS]

    assert set().union(*readings) == set(ActionType)
    assert sum(len(reading) for reading in readings) == len(ActionType)
    # Two of the unsupported rows carry no units at all, so a check on what
    # moves a share count would not notice either of them going missing.
    assert {ActionType.FEE, ActionType.EXCESS_REPORTED_INCOME} <= (
        RENAME_DAY_UNSUPPORTED_ACTIONS
    )
    # Nothing that moves a share count is read as part of the whole holding
    # but an ordinary purchase or sale, and a spin-off, which adds units to
    # the holding its own row names.
    assert (QUANTITY_INCREASING_ACTIONS | QUANTITY_DECREASING_ACTIONS) - {
        ActionType.BUY,
        ActionType.SELL,
        ActionType.SPIN_OFF,
    } <= RENAME_DAY_UNSUPPORTED_ACTIONS


def test_two_holdings_renamed_on_one_day_are_told_apart() -> None:
    """Sales under two retired tickers draw on their respective holdings."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    buy_day = datetime.date(2024, 5, 1)
    transactions = [
        _gbp_trade(buy_day, ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(buy_day, ActionType.BUY, "FOO", 100, 2000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _rename_transaction(RENAME_DAY, "FOO", "BAR"),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 60, 900),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "FOO", 60, 1500),
    ]

    report = get_report(calculator, transactions)

    assert report.total_gain() == Decimal(600)
    assert calculator.portfolio["NEW"] == Position(Decimal(40), Decimal(400))
    assert calculator.portfolio["BAR"] == Position(Decimal(40), Decimal(800))


def test_one_renamed_holding_cannot_borrow_the_other_s_shares() -> None:
    """A sale exceeding its holding's capacity cannot use another holding's shares."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    buy_day = datetime.date(2024, 5, 1)
    transactions = [
        _gbp_trade(buy_day, ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(buy_day, ActionType.BUY, "FOO", 50, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
        _rename_transaction(RENAME_DAY, "FOO", "BAR"),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 60, 900),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "FOO", 60, 900),
    ]

    with pytest.raises(
        InvalidTransactionError, match=r"more than the available balance\(50\)"
    ):
        get_report(calculator, transactions)


def test_a_holding_renamed_to_two_names_on_a_trading_day_is_refused() -> None:
    """The holding goes to one name or the other, and the day says neither."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _rename_transaction(RENAME_DAY, "OLD", "AAA"),
        _rename_transaction(RENAME_DAY, "OLD", "BBB"),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 50, 1000),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"OLD is renamed to both AAA and BBB on {RENAME_DAY}."
    )


@pytest.mark.parametrize("backwards", [False, True], ids=["in order", "backwards"])
@pytest.mark.parametrize("third_row", ["fee", "excess reported income"])
def test_a_rename_chain_is_refused_whatever_else_the_day_holds(
    third_row: str, *, backwards: bool
) -> None:
    """Fees and ERI trigger different fallback paths.

    Neither may bypass rename-chain validation, in either row order.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.isin_converter.data[ERI_ISIN] = {"OLD"}
    renames = [
        _rename_transaction(RENAME_DAY, "OLD", "MID"),
        _rename_transaction(RENAME_DAY, "MID", "NEW"),
    ]
    extra: BrokerTransaction = (
        _gbp_fee(RENAME_DAY, "OLD", 5)
        if third_row == "fee"
        else ERITransaction(
            date=RENAME_DAY,
            isin=ERI_ISIN,
            price=Decimal(1),
            currency=CurrencyCode("GBP"),
        )
    )
    transactions = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        extra,
        *(reversed(renames) if backwards else renames),
        _gbp_trade(datetime.date(2024, 5, 20), ActionType.SELL, "NEW", 100, 1500),
    ]

    with pytest.raises(CalculationError) as excinfo:
        get_report(calculator, transactions)

    assert str(excinfo.value).startswith(
        f"Cannot apply the renames of OLD on {RENAME_DAY}: it is renamed to "
        "MID, and MID is renamed to NEW, the same day."
    )


def test_a_rename_day_carrying_excess_reported_income_is_read_row_by_row() -> None:
    """An ERI day preserves the existing refusal of a sale before its ticker exists.

    ERI names an ISIN, so ticker-based filtering cannot exclude affected holdings.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.isin_converter.data[ERI_ISIN] = {"OLD"}
    transactions: list[BrokerTransaction] = [
        _gbp_trade(datetime.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
        _gbp_trade(RENAME_DAY, ActionType.SELL, "NEW", 50, 1000),
        ERITransaction(
            date=RENAME_DAY,
            isin=ERI_ISIN,
            price=Decimal(1),
            currency=CurrencyCode("GBP"),
        ),
        _rename_transaction(RENAME_DAY, "OLD", "NEW"),
    ]

    with pytest.raises(InvalidTransactionError, match="not owned symbol"):
        get_report(calculator, transactions)


def test_same_day_vest_ordered_before_sale() -> None:
    """A sale listed before the same-day vest is reordered so it can be processed.

    Equity-award exports (e.g. Schwab or Morgan Stanley) can list the sale of
    vested shares before the vest itself. A disposal is validated against the
    current holding as soon as it is read, so the raw order fails; the registry
    sort places the balance-neutral vest first so the sale is processed.
    """
    day = datetime.date(2024, 6, 3)
    vest = transaction(
        day,
        ActionType.STOCK_ACTIVITY,
        "SYM",
        quantity=10,
        price=10,
        currency=CurrencyCode("GBP"),
    )
    sale = transaction(
        day,
        ActionType.SELL,
        "SYM",
        quantity=5,
        price=20,
        amount=100,
        currency=CurrencyCode("GBP"),
    )

    # Raw order (sale before vest) fails the ownership check.
    with pytest.raises(InvalidTransactionError):
        get_report(create_calculator(tax_year=2024, balance_check=False), [sale, vest])

    # The registry sort orders the vest first, so the sale succeeds.
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        sorted([sale, vest], key=_transaction_sort_key),
    )
    assert report.total_gain() == Decimal(50)  # 5 * (20 - 10)


def test_same_day_sale_funding_purchase_survives_sort() -> None:
    """Ordering vests first must not disturb the 'sales before purchases' order.

    Only vests are moved, so a same-day sale that funds a purchase is still
    processed before the purchase and the running balance stays non-negative
    under the balance check.
    """
    day0 = datetime.date(2024, 6, 1)
    day1 = datetime.date(2024, 6, 2)
    transactions = [
        transaction(day0, ActionType.TRANSFER, amount=50, currency=CurrencyCode("GBP")),
        transaction(
            day0,
            ActionType.BUY,
            "SYM",
            quantity=5,
            price=10,
            amount=-50,
            currency=CurrencyCode("GBP"),
        ),
        transaction(
            day1,
            ActionType.SELL,
            "SYM",
            quantity=5,
            price=20,
            amount=100,
            currency=CurrencyCode("GBP"),
        ),
        transaction(
            day1,
            ActionType.BUY,
            "OTH",
            quantity=10,
            price=10,
            amount=-100,
            currency=CurrencyCode("GBP"),
        ),
    ]

    report = get_report(
        create_calculator(tax_year=2024),
        sorted(transactions, key=_transaction_sort_key),
    )
    assert report.total_gain() == Decimal(50)  # 5 * (20 - 10)


def test_disposal_debug_log_keeps_fractional_quantity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fractional share quantities must not be truncated in debug logs.

    Quantities were formatted with ``%d``, which truncates a ``Decimal`` to an
    integer (e.g. 2.5 -> 2). They must use ``%s`` so partial shares are logged
    in full.
    """
    buy_day = datetime.date(2024, 6, 1)
    sell_day = datetime.date(2024, 6, 10)
    transactions = [
        transaction(
            buy_day,
            ActionType.BUY,
            "SYM",
            quantity=5,
            price=10,
            amount=-50,
            currency=CurrencyCode("GBP"),
        ),
        transaction(
            sell_day,
            ActionType.SELL,
            "SYM",
            quantity=2.5,
            price=20,
            amount=50,
            currency=CurrencyCode("GBP"),
        ),
    ]

    with caplog.at_level(logging.DEBUG, logger="cgt_calc.matching"):
        get_report(create_calculator(tax_year=2024, balance_check=False), transactions)

    disposal_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("DISPOSAL on")
    ]
    assert disposal_logs
    assert all("quantity 2.5" in message for message in disposal_logs)


def test_run_with_example_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--schwab-file",
        "tests/schwab/data/schwab_transactions.csv",
        "--trading212-dir",
        "tests/trading212/data/2020/",
        "--mssb-dir",
        "tests/morgan_stanley/data/",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    # The progress narrative is a contract: it must reach stderr, in order.
    # The last two lines depend on whether pdflatex runs, so match by prefix.
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert stderr_lines[:-2] == [
        "Parsing tests/schwab/data/schwab_transactions.csv...",
        "Loaded 13 transactions from Charles Schwab",
        "Parsing tests/morgan_stanley/data/Releases Report.csv...",
        "Parsing tests/morgan_stanley/data/Withdrawals Report.csv...",
        "Loaded 6 transactions from Morgan Stanley",
        "Parsing tests/trading212/data/2020/from_2020-09-11_to_2021-04-02.csv...",
        "Loaded 8 transactions from Trading 212",
        "Found 27 broker transactions",
        "First pass complete",
        "Bed & breakfast match: VUAG disposed 2020-06-03, re-acquired 2020-07-02",
        "Second pass complete",
    ]
    assert stderr_lines[-2].startswith("Writing ")
    assert stderr_lines[-1].startswith("Done!")
    expected_file = (
        Path("tests") / "general" / "data" / "test_run_with_example_files_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )

    # The LaTeX source is pinned too. pdflatex leaves no source behind, so
    # the one CI job that runs it compiles the PDF instead of checking this.
    if "--no-pdflatex" in cmd:
        output = Path(report_path(request))
        tex_path = output.parent / f"{output.stem}.tex"
        expected_tex_file = (
            Path("tests")
            / "general"
            / "data"
            / "test_run_with_example_files_report.tex"
        )
        assert tex_path.read_text(encoding="utf-8") == expected_tex_file.read_text(
            encoding="utf-8"
        ), (
            "Run with example files generated an unexpected LaTeX report, "
            "if the change is intended update the golden with:\n"
            f"{cmd_str} && cp {tex_path} {expected_tex_file}"
        )


def test_main_returns_failure_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception must produce a failure exit code."""

    def explode(args: argparse.Namespace) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("cgt_calc.cli.calculate_cgt", explode)
    monkeypatch.setattr(sys, "argv", ["cgt-calc", "--year", "2021"])

    # main() enables the FloatOperation trap on the active decimal context;
    # keep that from leaking into other tests in the same worker.
    with localcontext():
        assert main() == 1


def test_negative_balance_error_trims_long_history() -> None:
    """A long transaction dump is trimmed to the most recent entries."""
    start = datetime.date(2024, 5, 1)
    transactions = [transfer_transaction(start, 13.0)] + [
        transfer_transaction(start + datetime.timedelta(days=day), -1.0)
        for day in range(1, 15)
    ]
    calculator = create_calculator(tax_year=2024)

    with pytest.raises(CalculationError) as excinfo:
        calculator.prepare_history(transactions)

    message = str(excinfo.value)
    assert "... 5 earlier transaction(s) omitted ..." in message
    assert message.count("Balance after transaction=") == BALANCE_CHECK_CONTEXT_ROWS
    assert "use --no-balance-check" in message


def test_negative_balance_error_shows_short_history_in_full() -> None:
    """A dump that fits within the limit is shown without omissions."""
    transactions = [transfer_transaction(datetime.date(2024, 5, 1), -1.0)]
    calculator = create_calculator(tax_year=2024)

    with pytest.raises(CalculationError) as excinfo:
        calculator.prepare_history(transactions)

    message = str(excinfo.value)
    assert "Reached a negative balance(-1.000000)" in message
    assert "omitted" not in message
    assert message.count("Balance after transaction=") == 1
    assert "use --no-balance-check" in message


def test_negative_balance_error_shows_only_relevant_transactions() -> None:
    """Other currencies and cash-neutral transactions are left out of the dump."""
    day = datetime.date(2024, 5, 1)
    transactions = [
        transfer_transaction(day, 100.0),
        buy_transaction(
            day + datetime.timedelta(days=1),
            "FOO",
            quantity=1,
            price=10.0,
            fees=0.0,
            amount=-10.0,
        ),
        split_transaction(day + datetime.timedelta(days=2), "FOO", quantity=1),
        transaction(
            day + datetime.timedelta(days=3),
            ActionType.TRANSFER,
            amount=5.0,
            currency=CurrencyCode("EUR"),
        ),
        transfer_transaction(day + datetime.timedelta(days=4), -91.0),
    ]
    calculator = create_calculator(tax_year=2024)

    with pytest.raises(CalculationError) as excinfo:
        calculator.prepare_history(transactions)

    message = str(excinfo.value)
    assert message.count("Balance after transaction=") == 3
    assert "currency='EUR'" not in message
    assert "Split of FOO" not in message


@pytest.mark.parametrize("deposit_first", [True, False])
def test_balance_check_accepts_same_day_funding_in_either_order(
    deposit_first: bool,
) -> None:
    """A purchase funded on the day it is made passes whichever row is first."""
    day = datetime.date(2024, 5, 1)
    rows = [
        transfer_transaction(day, 5000.0),
        buy_transaction(day, "FOO", quantity=100, price=50.0, fees=0.0, amount=-5000.0),
    ]
    calculator = create_calculator(tax_year=2024)

    calculator.prepare_history(rows if deposit_first else rows[::-1])


def test_balance_check_reports_an_overdraft_left_at_the_end_of_the_day() -> None:
    """A day that ends short is still reported, and the message names the day."""
    day = datetime.date(2024, 5, 1)
    transactions = [
        transfer_transaction(day, 4000.0),
        buy_transaction(day, "FOO", quantity=100, price=50.0, fees=0.0, amount=-5000.0),
    ]
    calculator = create_calculator(tax_year=2024)

    with pytest.raises(CalculationError) as excinfo:
        calculator.prepare_history(transactions)

    message = str(excinfo.value)
    assert "Reached a negative balance(-1000.000000)" in message
    assert "at the end of 2024-05-01 after processing" in message


@pytest.mark.parametrize(
    ("date", "broker", "currency"),
    [
        (datetime.date(2024, 5, 2), "Testing", CurrencyCode("USD")),
        (datetime.date(2024, 5, 1), "Another broker", CurrencyCode("USD")),
        (datetime.date(2024, 5, 1), "Testing", CurrencyCode("EUR")),
    ],
    ids=["later date", "other broker", "other currency"],
)
def test_balance_check_is_not_funded_by_a_later_day_or_another_pool(
    date: datetime.date, broker: str, currency: CurrencyCode
) -> None:
    """Funding must be in the same broker and currency by the purchase date.

    A deposit dated on or before the purchase funds it, wherever it sits
    in the export. A deposit dated after the purchase does not, and
    neither does money at another broker or in another currency.
    """
    purchase = buy_transaction(
        datetime.date(2024, 5, 1),
        "FOO",
        quantity=100,
        price=50.0,
        fees=0.0,
        amount=-5000.0,
    )
    deposit = transaction(
        date, ActionType.TRANSFER, amount=5000.0, currency=currency, broker=broker
    )
    calculator = create_calculator(tax_year=2024)

    with pytest.raises(CalculationError, match=r"negative balance\(-5000"):
        calculator.prepare_history(
            sorted([deposit, purchase], key=lambda row: row.date)
        )


def test_balance_check_is_not_silenced_by_a_trailing_eri_row() -> None:
    """Excess reported income skips the check, so it cannot stand in for day close."""
    day = datetime.date(2024, 5, 1)
    isin = Isin("USFOO0000006")
    transactions = [
        transfer_transaction(day, 100.0),
        buy_transaction(
            day, "FOO", quantity=1, price=101.0, fees=0.0, amount=-101.0, isin=isin
        ),
        eri_transaction(day, isin, 2.1),
    ]
    calculator = create_calculator(tax_year=2024)

    with pytest.raises(CalculationError, match=r"negative balance\(-1"):
        calculator.prepare_history(transactions)


def test_custom_period_narrows_reporting_window() -> None:
    """Only dates inside the custom period count towards the report."""
    currency_converter = CurrencyConverter(None, {})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
        period_start=datetime.date(2024, 4, 6),
        period_end=datetime.date(2024, 10, 29),
    )

    assert calculator.date_in_tax_year(datetime.date(2024, 10, 29))
    assert not calculator.date_in_tax_year(datetime.date(2024, 10, 30))
    assert calculator.tax_year_end_date == datetime.date(2024, 10, 29)


def test_report_labels_custom_period() -> None:
    """Report headings and title show the custom period when set."""
    report = CapitalGainsReport(
        2024,
        [],
        0,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        None,
        None,
        {},
        {},
        Decimal(0),
        Decimal(0),
        Decimal(0),
        show_unrealized_gains=False,
        period_start=datetime.date(2024, 4, 6),
        period_end=datetime.date(2024, 10, 29),
    )

    assert report.title_period == "2024-04-06 to 2024-10-29"
    assert "period 2024-04-06 to 2024-10-29" in str(report)


def test_report_labels_full_tax_year() -> None:
    """Report headings keep the tax year wording without a custom period."""
    report = CapitalGainsReport(
        2024,
        [],
        0,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        None,
        None,
        {},
        {},
        Decimal(0),
        Decimal(0),
        Decimal(0),
        show_unrealized_gains=False,
    )

    assert report.title_period == "2024-25"
    assert "Tax summary for 2024/2025" in str(report)


def test_taxable_gain_requires_an_allowance() -> None:
    """Taxable gain cannot be derived when the tax-year allowance is unknown."""
    report = CapitalGainsReport(
        2024,
        [],
        0,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        None,
        None,
        {},
        {},
        Decimal(0),
        Decimal(0),
        Decimal(0),
        show_unrealized_gains=False,
    )

    with pytest.raises(CalculationError, match=r"allowance.*unavailable"):
        report.taxable_gain()


def test_foreign_fees_folded_into_gbp_transaction() -> None:
    """Convert foreign fees to the transaction currency and re-derive price."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(
        None, {date: {CurrencyCode("USD"): Decimal("1.25")}}
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    buy = BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal(10),
        fees=Decimal(0),
        amount=Decimal(-100),
        currency=CurrencyCode("GBP"),
        broker="Trading212",
        foreign_fees={CurrencyCode("USD"): Decimal("1.25")},
    )

    calculator.prepare_history([buy])

    assert buy.foreign_fees == {}
    assert buy.fees == Decimal(1)
    assert buy.price == Decimal("9.9")


def test_foreign_fees_folded_into_non_gbp_transaction() -> None:
    """Convert GBP fees into a non-GBP transaction currency."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(
        None, {date: {CurrencyCode("USD"): Decimal("1.25")}}
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    buy = BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal("12.5"),
        fees=Decimal(0),
        amount=Decimal(-125),
        currency=CurrencyCode("USD"),
        broker="Trading212",
        foreign_fees={CurrencyCode("GBP"): Decimal(1)},
    )

    calculator.prepare_history([buy])

    assert buy.foreign_fees == {}
    assert buy.fees == Decimal("1.25")
    assert buy.price == Decimal("12.375")


def test_multiple_foreign_fee_currencies_on_sell() -> None:
    """Convert each foreign fee currency and re-derive the sell price."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(
        None,
        {
            date: {
                CurrencyCode("USD"): Decimal("1.25"),
                CurrencyCode("EUR"): Decimal("1.10"),
            }
        },
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    buy = BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal(9),
        fees=Decimal(0),
        amount=Decimal(-90),
        currency=CurrencyCode("GBP"),
        broker="Trading212",
    )
    sell = BrokerTransaction(
        date=date,
        action=ActionType.SELL,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal("9.93"),
        fees=Decimal(0),
        amount=Decimal("99.30"),
        currency=CurrencyCode("GBP"),
        broker="Trading212",
        foreign_fees={
            CurrencyCode("USD"): Decimal("0.25"),
            CurrencyCode("EUR"): Decimal("0.55"),
        },
    )

    calculator.prepare_history([buy, sell])

    assert sell.foreign_fees == {}
    assert sell.fees == Decimal("0.70")
    assert sell.price == Decimal(10)


def report_values(obj: object) -> object:
    """Rebuild a report out of things that compare by value.

    Two reports built by separate runs cannot be compared with ==:
    `PortfolioEntry` and `CalculationEntry` define no `__eq__`, so the
    generated comparison falls back to identity on the portfolio and both
    calculation logs. Rendering to text is not the comparison either -- it
    leaves out most calculation log entries, drops empty positions and
    rounds. This walks the whole structure into dicts and lists, so every
    field of every entry is compared, including the ones only the PDF reads.
    """
    if isinstance(obj, Enum):
        return obj
    if isinstance(obj, dict):
        return {key: report_values(value) for key, value in obj.items()}
    if isinstance(obj, list | tuple):
        return [report_values(item) for item in obj]
    attributes = getattr(obj, "__dict__", None)
    if attributes is None:
        return obj
    return {name: report_values(value) for name, value in attributes.items()}


def test_calculate_capital_gain_is_repeatable() -> None:
    """A second run rebuilds the same report from the same prepared history."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    calculator.prepare_history(
        [
            transaction(
                datetime.date(2024, 6, 1), ActionType.BUY, "FOO", 10, 10, 0, -100, GBP
            ),
            transaction(
                datetime.date(2024, 6, 10), ActionType.SELL, "FOO", 10, 12, 0, 120, GBP
            ),
            # Within 30 days of the sale, so the walk records a bed and
            # breakfast claim as it matches it.
            transaction(
                datetime.date(2024, 6, 20), ActionType.BUY, "FOO", 10, 11, 0, -110, GBP
            ),
            transaction(
                datetime.date(2024, 7, 1),
                ActionType.DIVIDEND,
                "FOO",
                None,
                None,
                0,
                5,
                GBP,
            ),
            transaction(
                datetime.date(2024, 7, 2),
                ActionType.INTEREST,
                None,
                None,
                None,
                0,
                3,
                GBP,
            ),
        ]
    )
    prepared = copy.deepcopy(calculator.state.history)

    first = calculator.calculate_capital_gain()
    second = calculator.calculate_capital_gain()

    assert report_values(second) == report_values(first)
    assert calculator.state.history == prepared


def test_prepare_history_runs_once() -> None:
    """A second ingestion would accumulate into the history already recorded."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        transaction(
            datetime.date(2024, 6, 1), ActionType.BUY, "FOO", 1, 10, 0, -10, GBP
        )
    ]
    calculator.prepare_history(transactions)

    with pytest.raises(RuntimeError, match="runs once per calculator"):
        calculator.prepare_history(transactions)


def test_calculate_capital_gain_requires_ingestion() -> None:
    """Calculating before ingesting used to return an empty report."""
    calculator = create_calculator(tax_year=2024, balance_check=False)

    with pytest.raises(RuntimeError, match="must complete before"):
        calculator.calculate_capital_gain()


def test_failed_ingestion_can_be_neither_retried_nor_calculated() -> None:
    """A history that stopped part way through is not a history to report on."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        buy_transaction(datetime.date(2024, 5, 1), "FOO", 10, 10, 0, -100),
        transaction(datetime.date(2024, 5, 2), ActionType.BUY, "BAR", 10, 10, 0, None),
    ]

    with pytest.raises(AmountMissingError):
        calculator.prepare_history(transactions)

    with pytest.raises(RuntimeError, match="runs once per calculator"):
        calculator.prepare_history(transactions)

    with pytest.raises(RuntimeError, match="must complete before"):
        calculator.calculate_capital_gain()


def test_calculate_matches_the_two_step_sequence() -> None:
    """calculate() is the two steps in the only order that works."""

    def transactions() -> list[BrokerTransaction]:
        return [
            buy_transaction(datetime.date(2024, 5, 1), "FOO", 10, 10, 0, -100),
            sell_transaction(datetime.date(2024, 6, 3), "FOO", 10, 12, 0, 120),
            buy_transaction(datetime.date(2024, 6, 10), "FOO", 10, 11, 0, -110),
        ]

    combined = create_calculator(tax_year=2024, balance_check=False)
    two_step = create_calculator(tax_year=2024, balance_check=False)
    two_step.prepare_history(transactions())

    assert str(combined.calculate(transactions())) == str(
        two_step.calculate_capital_gain()
    )


BUY_DAY = datetime.date(2024, 5, 1)
SELL_DAY = datetime.date(2024, 5, 10)
REBUY_DAY = datetime.date(2024, 5, 20)


def test_a_repurchase_of_shares_that_cost_nothing_computes() -> None:
    """Shares that cost nothing leave a repurchase holding nothing.

    A repurchase within 30 days of a disposal gives up what its own units
    cost and takes on the basis the disposal carried. Where the shares sold
    cost nothing, the two cancel and the pool keeps exactly nothing, which is
    a real cost rather than a broken invariant. The run used to stop here on a
    bare assertion with no message.
    """
    calculator = create_calculator(tax_year=2024)
    transactions = [
        _gbp_trade(BUY_DAY, ActionType.BUY, "FOO", 10, 0),
        _gbp_trade(SELL_DAY, ActionType.SELL, "FOO", 10, 100),
        _gbp_trade(REBUY_DAY, ActionType.BUY, "FOO", 10, 50),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[SELL_DAY]["sell$FOO"]
    assert entry.rule_type is RuleType.BED_AND_BREAKFAST
    assert entry.bed_and_breakfast_date_index == REBUY_DAY
    assert entry.allowable_cost == Decimal(50)
    # GBP 100 of proceeds against the GBP 50 the repurchase cost.
    assert report.total_gain() == Decimal(50)
    assert calculator.portfolio["FOO"] == Position(Decimal(10), Decimal(0))


def test_a_part_of_a_cost_never_comes_to_more_than_the_whole() -> None:
    """A repurchase's cost is not always on the grid its share is rounded to.

    The share of a repurchase claimed under the 30-day rule is rounded to ten
    decimal places, and an amount converted out of another currency need not
    sit on that grid, so the part can round to more than all of it. Here
    2.9999999999 of the 3 shares repurchased for GBP 1.00000000009 are
    claimed, and the unrounded share rounds up past the whole. The run used to
    stop on a bare assertion once the pool went a hundred-billionth of a pound
    negative.
    """
    calculator = create_calculator(tax_year=2024)
    quantity = Decimal("2.9999999999")
    transactions = [
        _gbp_trade(BUY_DAY, ActionType.BUY, "FOO", quantity, 0),
        _gbp_trade(SELL_DAY, ActionType.SELL, "FOO", quantity, 3),
        _gbp_trade(REBUY_DAY, ActionType.BUY, "FOO", 3, Decimal("1.00000000009")),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[SELL_DAY]["sell$FOO"]
    assert entry.rule_type is RuleType.BED_AND_BREAKFAST
    assert entry.allowable_cost == Decimal("1.00000000009")
    # GBP 3 of proceeds against the whole of what the repurchase cost.
    assert report.total_gain() == Decimal(2)
    assert calculator.portfolio["FOO"].amount == Decimal(0)


def test_proceeds_sitting_on_a_rounding_step_reconcile() -> None:
    """The recorded proceeds and the rebuilt ones are compared as one figure.

    A disposal's proceeds are checked against the sum rebuilt from its
    calculation entries. One side is the amount as recorded, the other comes
    from a unit price, so they can differ far below the calculator's
    precision. Rounding each on its own used to send a recorded amount sitting
    exactly on a rounding step to a different grid point from the rebuilt one,
    and the run stopped.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    quantity = Decimal("0.6245225058")
    transactions = [
        _gbp_trade(BUY_DAY, ActionType.BUY, "FOO", quantity, Decimal("6.245225058")),
        _gbp_trade(
            SELL_DAY, ActionType.SELL, "FOO", quantity, Decimal("9.40968379635")
        ),
    ]

    report = get_report(calculator, transactions)

    # GBP 9.40968379635 of proceeds against a GBP 6.245225058 cost.
    assert report.total_gain() == Decimal("3.16")


def test_claiming_the_whole_of_an_acquisition_costs_the_whole_of_it() -> None:
    """All of a repurchase costs all of it, down to the last figure.

    The share claimed under the 30-day rule is rounded to ten decimal places,
    and an amount converted out of another currency need not sit on that grid.
    Where the whole repurchase is claimed, going through the arithmetic rounds
    a figure that is already exact, and the relief the disposal is given comes
    out short of what the repurchase cost, so the whole is handed over as it
    stands. The pool is normalised as it is written and shows nothing of this;
    the allowable cost is where it survives.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    cost = Decimal("1.00000000001")
    transactions = [
        _gbp_trade(BUY_DAY, ActionType.BUY, "FOO", 3, 30),
        _gbp_trade(SELL_DAY, ActionType.SELL, "FOO", 3, 5),
        _gbp_trade(REBUY_DAY, ActionType.BUY, "FOO", 3, cost),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[SELL_DAY]["sell$FOO"]
    assert entry.rule_type is RuleType.BED_AND_BREAKFAST
    assert entry.allowable_cost == cost
    # GBP 5 of proceeds against the GBP 1.00000000001 the repurchase cost.
    assert report.total_gain() == Decimal(4)
    # The repurchase keeps the basis of the shares it replaces, and nothing of
    # its own cost is left behind.
    assert calculator.portfolio["FOO"] == Position(Decimal(3), Decimal(30))


@pytest.mark.parametrize(
    ("proceeds", "reported"),
    [(Decimal("1.005"), Decimal("0.01")), (Decimal("0.995"), Decimal("-0.01"))],
)
def test_a_gain_of_exactly_half_a_penny_is_reported_not_rejected(
    proceeds: Decimal, reported: Decimal
) -> None:
    """A gain landing exactly on the half-penny is rounded, not refused.

    What a disposal reports is its gain rounded to the penny, and the walk
    checks that figure against the entries it was built from. Half a penny
    rounds up to a whole one, so measuring how far the reported figure moved
    lands on the same step again and the run stopped. Asking instead whether
    the raw gain rounds to the figure already reported settles it.
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    transactions = [
        _gbp_trade(BUY_DAY, ActionType.BUY, "ABC", 1, 1),
        _gbp_trade(SELL_DAY, ActionType.SELL, "ABC", 1, proceeds),
    ]

    report = get_report(calculator, transactions)

    (entry,) = report.calculation_log[SELL_DAY]["sell$ABC"]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.gain == proceeds - 1
    assert report.total_gain() == reported
