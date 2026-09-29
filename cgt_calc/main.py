#!/usr/bin/env python3
"""Capital Gain Calculator main module."""

from __future__ import annotations

from decimal import Decimal
import logging
import sys
from typing import TYPE_CHECKING

from colorama import Fore

from .calculator_state import CalculatorState
from .cli import calculate_cgt, init, main
from .const import CAPITAL_GAIN_ALLOWANCES, DIVIDEND_ALLOWANCES
from .dates import get_tax_year_end, get_tax_year_start, is_date
from .income import IncomeProcessor
from .ingestion import TransactionIngester
from .logging import style_text
from .matching import Matcher
from .model import (
    BrokerTransaction,
    CapitalGainsReport,
    ExcessReportedIncomeLog,
    HmrcTransactionLog,
    PortfolioEntry,
    Position,
)
from .util import round_decimal

if TYPE_CHECKING:
    import datetime

    from .currency_converter import CurrencyConverter
    from .current_price_fetcher import CurrentPriceFetcher
    from .initial_prices import InitialPrices
    from .isin_converter import IsinConverter
    from .spin_off_handler import SpinOffHandler
    from .stock_splits import SplitTransformation

# The CLI entry points live in cgt_calc.cli now; re-exported here so
# existing imports and `python -m cgt_calc.main` keep working.
__all__ = ["CapitalGainsCalculator", "calculate_cgt", "init", "main"]

LOGGER = logging.getLogger(__name__)


class CapitalGainsCalculator:
    """Main calculator class."""

    def __init__(
        self,
        tax_year: int,
        currency_converter: CurrencyConverter,
        isin_converter: IsinConverter,
        price_fetcher: CurrentPriceFetcher,
        spin_off_handler: SpinOffHandler,
        initial_prices: InitialPrices,
        interest_fund_tickers: list[str],
        *,
        cgt_exempt_tickers: list[str] | None = None,
        balance_check: bool = True,
        autoconvert_currency: bool = False,
        calc_unrealized_gains: bool = False,
        period_start: datetime.date | None = None,
        period_end: datetime.date | None = None,
    ):
        """Create calculator object.

        period_start/period_end narrow the reporting window within the
        tax year, e.g. for the HMRC 2024/25 CGT adjustment calculation.
        """
        self.tax_year = tax_year
        self.period_start = period_start
        self.period_end = period_end

        self.tax_year_start_date = period_start or get_tax_year_start(tax_year)
        self.tax_year_end_date = period_end or get_tax_year_end(tax_year)

        self.currency_converter = currency_converter
        self.isin_converter = isin_converter
        self.price_fetcher = price_fetcher
        self.spin_off_handler = spin_off_handler
        self.initial_prices = initial_prices
        self.balance_check = balance_check
        self.autoconvert_currency = autoconvert_currency
        self.calc_unrealized_gains = calc_unrealized_gains
        self.interest_fund_tickers = interest_fund_tickers
        self.cgt_exempt_tickers = frozenset(
            ticker.upper() for ticker in cgt_exempt_tickers or []
        )
        self.state = CalculatorState()
        self.history = self.state.history
        self.run = self.state.run
        self.income = IncomeProcessor(
            self.state,
            currency_converter,
            isin_converter,
            interest_fund_tickers,
            self.date_in_tax_year,
            autoconvert_currency=autoconvert_currency,
        )
        self.ingester = TransactionIngester(
            self.state,
            self.income,
            currency_converter,
            isin_converter,
            price_fetcher,
            spin_off_handler,
            initial_prices,
            interest_fund_tickers,
            balance_check=balance_check,
            autoconvert_currency=autoconvert_currency,
            date_in_tax_year=self.date_in_tax_year,
        )
        self.matcher = Matcher(
            self.state,
            self.tax_year_start_date,
            self.tax_year_end_date,
            interest_fund_tickers,
            self.date_in_tax_year,
            self.is_cgt_exempt,
        )

    @property
    def portfolio(self) -> dict[str, Position]:
        """Holdings built up so far, by symbol."""
        return self.run.portfolio

    @property
    def bnb_list(self) -> HmrcTransactionLog:
        """Acquisitions reserved by the bed and breakfast rule."""
        return self.run.bnb_list

    @property
    def splits(self) -> dict[datetime.date, dict[str, SplitTransformation]]:
        """What each share reorganisation does to a holding."""
        return self.history.splits

    @property
    def eris(self) -> ExcessReportedIncomeLog:
        """Excess Reported Income by date and symbol."""
        return self.history.eris

    def date_in_tax_year(self, date: datetime.date) -> bool:
        """Check if date is within current tax year."""
        assert is_date(date)
        return self.tax_year_start_date <= date <= self.tax_year_end_date

    def is_cgt_exempt(self, symbol: str) -> bool:
        """Check if symbol is exempt from Capital Gains Tax."""
        return symbol.upper() in self.cgt_exempt_tickers

    def prepare_history(
        self,
        transactions: list[BrokerTransaction],
    ) -> None:
        """Run the first pass over the broker transactions.

        Runs once per calculator, whether or not it succeeds: a second run
        would accumulate into the history the first one recorded.
        """
        if self.run.ingestion_started:
            raise RuntimeError(
                "prepare_history() runs once per calculator; build "
                "a new one to ingest again"
            )
        self.run.ingestion_started = True
        self.ingester.prepare_history(transactions)
        self.run.ingested = True

    def calculate(
        self,
        transactions: list[BrokerTransaction],
    ) -> CapitalGainsReport:
        """Run both passes and return the report."""
        self.prepare_history(transactions)
        return self.calculate_capital_gain()

    def _warn_unmatched_cgt_exempt_tickers(self) -> None:
        """Warn for any exempt ticker with no transactions."""
        logged_symbols = {
            sym.upper()
            for log in (self.history.acquisition_list, self.history.disposal_list)
            for day in log.values()
            for sym in day
        }
        for ticker in sorted(self.cgt_exempt_tickers):
            if ticker not in logged_symbols:
                LOGGER.warning(
                    "CGT-exempt ticker '%s' was not found in acquisitions or disposals",
                    ticker,
                )

    def calculate_capital_gain(
        self,
    ) -> CapitalGainsReport:
        """Calculate capital gain and return generated report.

        It may be run again, and each run starts from a fresh `RunState` over
        the same prepared history.
        """
        if not self.run.ingested:
            raise RuntimeError(
                "prepare_history() must complete before "
                "calculate_capital_gain(); use calculate() to run both in order"
            )
        # The walk and the income processing accumulate into run state, so a
        # rerun starts from nothing rather than adding to the last run.
        self.run.reset()
        self._warn_unmatched_cgt_exempt_tickers()
        walked = self.matcher.walk()

        self.income.process_dividends()
        self.income.process_interests()

        LOGGER.info(
            "\n%s\n",
            style_text(
                "Second pass complete", colour=Fore.GREEN, emoji="✅", stream=sys.stderr
            ),
        )
        allowance = CAPITAL_GAIN_ALLOWANCES.get(self.tax_year)
        dividend_allowance = DIVIDEND_ALLOWANCES.get(self.tax_year)

        return CapitalGainsReport(
            self.tax_year,
            [
                self.make_portfolio_entry(symbol, position.quantity, position.amount)
                for symbol, position in self.run.portfolio.items()
            ],
            walked.disposal_count,
            round_decimal(walked.disposal_proceeds, 2),
            round_decimal(walked.allowable_costs, 2),
            round_decimal(walked.capital_gain, 2),
            round_decimal(walked.capital_loss, 2),
            Decimal(allowance) if allowance is not None else None,
            Decimal(dividend_allowance) if dividend_allowance is not None else None,
            walked.calculation_log,
            dict(sorted(self.run.calculation_log_yields.items())),
            round_decimal(self.run.total_uk_interest, 2),
            round_decimal(self.run.total_foreign_interest, 2),
            round_decimal(self.run.total_interest_tax, 2),
            show_unrealized_gains=self.calc_unrealized_gains,
            gift_loss=round_decimal(walked.gift_loss, 2),
            period_start=self.period_start,
            period_end=self.period_end,
            exempt_disposal_count=walked.exempt_disposal_count,
            exempt_disposal_proceeds=round_decimal(walked.exempt_disposal_proceeds, 2),
        )

    def make_portfolio_entry(
        self, symbol: str, quantity: Decimal, amount: Decimal
    ) -> PortfolioEntry:
        """Create a portfolio entry in the report."""
        unrealized_gains = None
        if self.calc_unrealized_gains:
            current_price = (
                self.price_fetcher.get_current_market_price(symbol)
                if quantity > 0
                else 0
            )
            if current_price is not None:
                unrealized_gains = current_price * quantity - amount
        return PortfolioEntry(
            symbol,
            quantity,
            amount,
            unrealized_gains,
        )


if __name__ == "__main__":
    init()
