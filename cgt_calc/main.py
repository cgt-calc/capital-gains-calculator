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
from .const import (
    CAPITAL_GAIN_ALLOWANCES,
    DIVIDEND_ALLOWANCES,
    FIRST_DIVIDEND_ALLOWANCE_YEAR,
)
from .dates import get_tax_year_end, get_tax_year_start, is_date
from .eri_gaps import predict_missing_period_ends, report_missing_eris
from .exceptions import CurrentPriceRequestError
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
from .non_reporting import fund_statuses, report_non_reporting
from .util import round_decimal

if TYPE_CHECKING:
    import datetime as dt

    from .currency_converter import CurrencyConverter
    from .current_price_fetcher import CurrentPriceFetcher
    from .isin_converter import IsinConverter
    from .model import DatePeriod
    from .share_prices import SharePrices
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
        share_prices: SharePrices,
        interest_fund_tickers: list[str],
        *,
        cgt_exempt_tickers: list[str] | None = None,
        balance_check: bool = True,
        autoconvert_currency: bool = False,
        calc_unrealized_gains: bool = False,
        gifts_to_spouse: bool = False,
        non_uk_resident_periods: list[DatePeriod] | None = None,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
    ):
        """Create calculator object.

        period_start/period_end narrow the reporting window within the
        tax year, e.g. for the HMRC 2024/25 CGT adjustment calculation.
        Excess reported income whose fund distribution date falls in one of
        the periods of non-residence is left out.
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
        self.share_prices = share_prices
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
            share_prices,
            interest_fund_tickers,
            balance_check=balance_check,
            autoconvert_currency=autoconvert_currency,
            gifts_to_spouse=gifts_to_spouse,
            non_uk_resident_periods=non_uk_resident_periods,
            date_in_tax_year=self.date_in_tax_year,
        )
        self.matcher = Matcher(
            self.state,
            self.tax_year_start_date,
            self.tax_year_end_date,
            interest_fund_tickers,
            self.date_in_tax_year,
            self.is_cgt_exempt,
            self.ingester.eri_in_pounds,
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
    def splits(self) -> dict[dt.date, dict[str, SplitTransformation]]:
        """What each share reorganisation does to a holding."""
        return self.history.splits

    @property
    def eris(self) -> ExcessReportedIncomeLog:
        """Excess Reported Income by date and symbol."""
        return self.history.eris

    def date_in_tax_year(self, date: dt.date) -> bool:
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

    def _logged_symbols(self) -> set[str]:
        """Return every symbol acquired or disposed of, upper-cased."""
        return {
            sym.upper()
            for log in (self.history.acquisition_list, self.history.disposal_list)
            for day in log.values()
            for sym in day
        }

    def _warn_unmatched_cgt_exempt_tickers(self) -> None:
        """Warn for any exempt ticker with no transactions."""
        logged_symbols = self._logged_symbols()
        for ticker in sorted(self.cgt_exempt_tickers):
            if ticker not in logged_symbols:
                LOGGER.warning(
                    "CGT-exempt ticker '%s' was not found in acquisitions or disposals",
                    ticker,
                )

    def _warn_unmatched_interest_fund_tickers(self) -> None:
        """Warn for any interest fund ticker that matches no income.

        Income is matched by its exact symbol, which a broker can spell
        differently from the holding: Sharesight names a dividend `FOO.NASDAQ`
        and the holding `NASDAQ:FOO`. A ticker matches a dividend of any year,
        or excess reported income for a holding the files trade or rename to.
        A report also names tickers nobody holds, such as a bundled fund's
        `VAGP` beside a Sharesight holding `LSE:VAGP`, and those match nothing.
        The warning names the income whose symbol shares the ticker's code.
        """
        held = self._logged_symbols() | {
            new.upper()
            for day in self.history.rename_list.values()
            for new in day.values()
        }
        reported = {
            symbol.upper() for day in self.history.eris.values() for symbol in day
        }
        income = {symbol.upper() for symbol, _ in self.history.dividend_list}
        income |= held & reported

        def code(symbol: str) -> str:
            # FOO from FOO.NASDAQ or NASDAQ:FOO.
            return symbol.rsplit(":", maxsplit=1)[-1].split(".", maxsplit=1)[0]

        for ticker in sorted(set(self.interest_fund_tickers) - income):
            similar = sorted(
                symbol for symbol in income if code(symbol) == code(ticker)
            )
            LOGGER.warning(
                "Interest fund ticker '%s' matched no dividend or excess reported "
                "income, so it changed nothing. Give each fund's symbol exactly "
                "as its income shows it.%s",
                ticker,
                f" Your files have income under {', '.join(similar)}."
                if similar
                else "",
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
        self._warn_unmatched_interest_fund_tickers()
        # A period that has not ended yet has no report missing.
        self.run.predicted_eri_ends = predict_missing_period_ends(
            self.history.eri_fund_period_ends,
            self.history.eri_fund_holdings,
            self.history.ticker_only_eri_warnings,
            self.ingester.non_uk_resident_periods,
            min(self.tax_year_end_date, self.ingester.today),
        )
        self.run.fund_statuses = fund_statuses(
            self.history.eri_fund_period_ends,
            self.history.eri_fund_holdings,
            self.history.ticker_only_eri_warnings,
            self.isin_converter.get_symbol_to_isin_map(),
        )
        walked = self.matcher.walk()
        # Before the missing figures: taking the holding out, as a stop here
        # asks, also takes out its missing periods.
        report_non_reporting(
            self.run.non_reporting_disposals,
            self.run.non_reporting_marks,
            self.tax_year,
        )
        eri_missing = report_missing_eris(
            self.run.missing_eris,
            self.history.eri_fund_period_ends,
            self.history.eri_fund_holdings,
            self.history.disposal_list,
            (self.tax_year_start_date, self.tax_year_end_date),
            self.ingester.today,
        )

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
            self.make_portfolio(),
            walked.disposal_count,
            round_decimal(walked.disposal_proceeds, 2),
            round_decimal(walked.allowable_costs, 2),
            walked.gains_by_date,
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
            dividends_carry_tax_credit=self.tax_year < FIRST_DIVIDEND_ALLOWANCE_YEAR,
            eri_estimated_until=max(self.run.provisional_eris.values(), default=None),
            eri_missing=eri_missing,
        )

    def make_portfolio(self) -> list[PortfolioEntry]:
        """Create the report's portfolio entries.

        One warning names every holding whose price request failed, rather
        than one each: offline, all of them fail.
        """
        entries = []
        failed = []
        for symbol, position in self.run.portfolio.items():
            try:
                entry = self.make_portfolio_entry(
                    symbol, position.quantity, position.amount
                )
            except CurrentPriceRequestError:
                failed.append(symbol)
                entry = PortfolioEntry(symbol, position.quantity, position.amount, None)
            entries.append(entry)
        if failed:
            # Sorted as the report lists them.
            LOGGER.warning(
                "Could not get today's prices from Yahoo Finance for %s: the request "
                "failed. Their unrealised gains are left out of the estimate.",
                ", ".join(sorted(failed)),
            )
        return entries

    def make_portfolio_entry(
        self, symbol: str, quantity: Decimal, amount: Decimal
    ) -> PortfolioEntry:
        """Create a portfolio entry in the report."""
        unrealized_gains = None
        if self.calc_unrealized_gains:
            current_price = (
                self.price_fetcher.get_current_market_price(
                    symbol, self.isin_converter.transaction_isins.get(symbol)
                )
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
