"""Dividend and interest processing."""

from __future__ import annotations

from collections import defaultdict
import datetime as dt
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

from .const import (
    DIVIDEND_CURRENCY_TO_COUNTRY,
    DIVIDEND_DOUBLE_TAXATION_RULES,
    DIVIDEND_TAX_LEAD_DAYS,
    DIVIDEND_TAX_MATCH_DAYS,
    ISIN_COUNTRY_CODE_LENGTH,
    UK_CURRENCY,
)
from .exceptions import CalculationError
from .model import (
    CalculationEntry,
    Dividend,
    DividendTaxAttribution,
    ForeignCurrencyAmount,
    RuleType,
)
from .util import approx_equal, round_decimal

if TYPE_CHECKING:
    from collections.abc import Callable

    from .calculator_state import CalculatorState
    from .currency_converter import CurrencyConverter
    from .isin_converter import IsinConverter
    from .model import CurrencyCode, ForeignAmountLog

LOGGER = logging.getLogger(__name__)


class IncomeProcessor:
    """Turns recorded dividends and interest into report entries."""

    def __init__(
        self,
        state: CalculatorState,
        currency_converter: CurrencyConverter,
        isin_converter: IsinConverter,
        interest_fund_tickers: list[str],
        date_in_tax_year: Callable[[dt.date], bool],
        *,
        autoconvert_currency: bool = False,
    ):
        """Create income processor object."""
        self.history = state.history
        self.run = state.run
        self.currency_converter = currency_converter
        self.isin_converter = isin_converter
        self.interest_fund_tickers = interest_fund_tickers
        self.date_in_tax_year = date_in_tax_year
        self.autoconvert_currency = autoconvert_currency
        self._attributed_dividend_tax: DividendTaxAttribution | None = None

    def _group_by_month(
        self,
        entries: dict[tuple[str, CurrencyCode, dt.date], ForeignCurrencyAmount],
    ) -> dict[tuple[str, CurrencyCode, dt.date], ForeignCurrencyAmount]:
        """Group in-tax-year amounts by month, keyed by the month's last date."""
        monthly: dict[tuple[str, CurrencyCode, dt.date], ForeignCurrencyAmount] = (
            defaultdict(ForeignCurrencyAmount)
        )
        last_date: dt.date = dt.date.min
        last_broker: str | None = None
        last_currency: CurrencyCode | None = None

        for (broker, currency, date), foreign_amount in sorted(entries.items()):
            if not self.date_in_tax_year(date):
                continue
            if (
                broker == last_broker
                and currency == last_currency
                and (date.year, date.month) == (last_date.year, last_date.month)
            ):
                monthly[broker, currency, date] = monthly.pop(
                    (broker, currency, last_date)
                )
            monthly[broker, currency, date] += foreign_amount
            last_date = date
            last_broker = broker
            last_currency = currency
        return monthly

    def process_interests(self) -> None:
        """Process all interest events.

        It groups them by month, using the last date on each month for the report
        and updates the interest totals for the year.
        """
        monthly_interests = self._group_by_month(self.history.interest_list)

        for (broker, currency, date), foreign_amount in monthly_interests.items():
            gbp_amount = self.currency_converter.to_gbp(
                foreign_amount.amount, currency, date
            )
            if currency == UK_CURRENCY:
                self.run.total_uk_interest += gbp_amount
                rule_prefix = "interestUK"
            else:
                self.run.total_foreign_interest += gbp_amount
                rule_prefix = f"interest{currency}"

            self.run.calculation_log_yields[date][f"{rule_prefix}${broker}"] = [
                CalculationEntry(
                    rule_type=RuleType.INTEREST,
                    quantity=Decimal(1),
                    amount=gbp_amount,
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                    fees=Decimal(0),
                )
            ]

        monthly_interest_taxes = self._group_by_month(self.history.interest_tax_list)

        for (broker, currency, date), foreign_amount in monthly_interest_taxes.items():
            gbp_amount = self.currency_converter.to_gbp(
                foreign_amount.amount, currency, date
            )
            # Withholding rows are negative, so negate rather than abs():
            # positive reversal rows then cancel out across months.
            tax_amount = -gbp_amount
            rule_prefix = f"interestTax{currency}"
            self.run.total_interest_tax += tax_amount

            self.run.calculation_log_yields[date][f"{rule_prefix}${broker}"] = [
                CalculationEntry(
                    rule_type=RuleType.INTEREST_TAX,
                    quantity=Decimal(1),
                    amount=tax_amount,
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                    fees=Decimal(0),
                )
            ]

    def _dividend_source_country(
        self, symbol: str, currency: CurrencyCode
    ) -> str | None:
        """Return the country a dividend was paid from, or None if unknown.

        The ISIN a security is registered under is the authority. Where no
        parser supplied one, fall back to guessing from the currency, which is
        what the calculator did before ISINs were consulted.
        """
        isin = self.isin_converter.get_symbol_to_isin_map().get(symbol)
        if isin is not None:
            return isin[:ISIN_COUNTRY_CODE_LENGTH]
        return DIVIDEND_CURRENCY_TO_COUNTRY.get(currency)

    def _warn_unattributed_dividend_tax(
        self,
        symbol: str,
        date: dt.date,
        tax: ForeignCurrencyAmount,
        candidates: list[dt.date],
    ) -> None:
        """Warn about withheld tax that no single dividend can claim."""
        # The tax is missing from the report of every year that holds a
        # payment which could have carried it, not only from the year it was
        # taken in, and each of those years is entitled to hear so. A
        # withholding just after 5 April can leave a payment short on either
        # side of the boundary.
        if not any(self.date_in_tax_year(affected) for affected in (date, *candidates)):
            return
        # Tax below half a unit would print as a bare "-0.00", so it is
        # shown as it stands instead. Whether it is material is a question
        # about its value in GBP, and answering it here would put a rate
        # lookup, and the failure of one, in the way of a warning.
        amount = round_decimal(tax.amount, 2) or tax.amount
        if candidates:
            LOGGER.warning(
                "Dividend tax of %s %s for %s on %s could have been taken "
                "from the dividend on %s, so it is left out of the report! "
                "Date it in the export on the one it belongs to to have it "
                "counted.",
                amount,
                tax.currency,
                symbol,
                date,
                " or ".join(str(candidate) for candidate in candidates),
            )
            return
        LOGGER.warning(
            "Dividend tax of %s %s for %s on %s has no dividend in the %d "
            "days before it or the %d days after, so it is left out of the "
            "report!",
            amount,
            tax.currency,
            symbol,
            date,
            DIVIDEND_TAX_MATCH_DAYS,
            DIVIDEND_TAX_LEAD_DAYS,
        )

    @staticmethod
    def _dividends_for_tax(
        date: dt.date, dividend_dates: set[dt.date]
    ) -> list[dt.date]:
        """Return the dividends a withholding could have been taken from.

        A payment on the day of the withholding is not in doubt, whatever
        else lies nearby. Otherwise, every payment in the window counts as a
        candidate, and it is for the caller to refuse when there is more
        than one: which of two payments a correction belongs to cannot be
        told from a date and a net amount.

        The window is asymmetric because the directions mean different
        things. Tax follows the payment it was taken from, and a correction
        follows it by longer, so it reaches back weeks; a payment after the
        withholding only means the broker posted the tax early, which it
        does by days.
        """
        if date in dividend_dates:
            return [date]
        return sorted(
            candidate
            for candidate in dividend_dates
            if -DIVIDEND_TAX_MATCH_DAYS
            <= (candidate - date).days
            <= DIVIDEND_TAX_LEAD_DAYS
        )

    def _attribute_dividend_tax(self) -> DividendTaxAttribution:
        """Attribute each withholding to the dividend it was taken from.

        Brokers do not always date a withholding, or a later correction of
        one such as a Schwab ``NRA Tax Adj``, on the day of the payment it
        belongs to, so tax is matched to a dividend of the same broker and
        symbol nearby. Holding one symbol at two brokers therefore keeps
        each broker's withholding with that broker's own payments.

        Tax is attributed only where one payment can claim it. Where two
        could, the export does not record which, and no arrangement of dates
        and amounts recovers it: an adjustment reaching back to last month's
        payment looks exactly like ordinary tax on next month's. Those are
        left out of the report and warned about instead of guessed at. They
        still reach the summary, and dating one on its dividend in the input
        settles it.

        Matching runs over the whole history rather than the reported year,
        so a payment and its withholding either side of 5 April stay
        together, and one withholding cannot be claimed in two years.
        """
        matched: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        unmatched: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        for (broker, symbol, date, _), tax in self.history.dividend_tax_list.items():
            if not tax.amount:
                continue
            candidates = self._dividends_for_tax(
                date, self.history.dividend_dates.get((broker, symbol), set())
            )
            if len(candidates) == 1:
                # Dated on the dividend it belongs to, so that is the date
                # its currency is converted at where two brokers withheld in
                # different ones.
                matched[symbol, candidates[0]] = (
                    self.currency_converter.combine_amounts(
                        matched[symbol, candidates[0]],
                        tax,
                        candidates[0],
                        autoconvert=self.autoconvert_currency,
                    )
                )
                continue
            # Either nothing is near enough, or two payments are and the
            # export does not say which one this is. Guessing between them
            # puts a wrong figure in the report; leaving it out and saying so
            # does not.
            self._warn_unattributed_dividend_tax(symbol, date, tax, candidates)
            unmatched[symbol, date] = self.currency_converter.combine_amounts(
                unmatched[symbol, date],
                tax,
                date,
                autoconvert=self.autoconvert_currency,
            )
        return DividendTaxAttribution(matched, unmatched)

    def dividend_tax_attribution(self) -> DividendTaxAttribution:
        """Attribute withholding once, for both the summary and the report.

        The two must agree, and attributing twice would warn twice.
        """
        if self._attributed_dividend_tax is None:
            self._attributed_dividend_tax = self._attribute_dividend_tax()
        return self._attributed_dividend_tax

    def process_dividends(self) -> None:
        """Process all dividend events and taxes.

        It updates the interest total for the year if needed.
        """
        attributed_tax = self.dividend_tax_attribution().matched
        for (symbol, date), foreign_amount in self.history.dividend_list.items():
            # Dividends outside the computed tax year are not reported, so
            # neither are the warnings raised while resolving their treaty.
            if not self.date_in_tax_year(date):
                continue

            # Read without inserting: a dividend with no tax must not leave a
            # zero entry behind in the attribution.
            tax = attributed_tax.get((symbol, date), ForeignCurrencyAmount())
            currency = foreign_amount.currency
            assert currency is not None, f"Dividend for {symbol} has no currency"

            # The payment and the tax taken from it have to be in one currency
            # whichever way the tax runs and whatever the holding is, because
            # converting one at the other's rate misstates it. Checking this
            # only for tax deducted from an ordinary holding let a refund, or
            # any tax on a bond fund, be converted as though it were in the
            # dividend's currency. With --autoconvert-currency each is
            # converted at its own rate instead, but only where either
            # currency is GBP: converting between two foreign currencies would
            # leave no single currency to read the source country from.
            tax_currency = tax.currency or currency
            if tax.amount and tax_currency != currency:
                gbp_involved = UK_CURRENCY in {currency, tax_currency}
                if not self.autoconvert_currency or not gbp_involved:
                    raise CalculationError(
                        f"Dividend and withholding tax currencies do not match "
                        f"for {symbol} on {date}: dividend "
                        f"{foreign_amount.currency}, tax {tax.currency}"
                    )
                LOGGER.debug(
                    "Converting the %s dividend and its %s tax for %s on %s "
                    "to GBP separately",
                    currency,
                    tax_currency,
                    symbol,
                    date,
                )

            treaty = None
            is_interest_fund = symbol in self.interest_fund_tickers
            if tax.amount < 0:
                if is_interest_fund:
                    LOGGER.warning(
                        "Cannot apply taxation treaty for bond fund %s", symbol
                    )
                else:
                    country = self._dividend_source_country(symbol, currency)
                    if country is None:
                        LOGGER.warning(
                            "Source country of the %s dividend is unknown (ticker: %s), "
                            "double taxation rules cannot be determined!",
                            currency,
                            symbol,
                        )
                    elif country not in DIVIDEND_DOUBLE_TAXATION_RULES:
                        LOGGER.warning(
                            "Taxation treaty for %s country is missing (ticker: %s), "
                            "double taxation rules cannot be determined!",
                            country,
                            symbol,
                        )
                    else:
                        treaty = DIVIDEND_DOUBLE_TAXATION_RULES[country]
                        expected_tax = treaty.country_rate * -foreign_amount.amount
                        deducted_tax = tax.amount
                        if tax_currency != currency:
                            # Autoconverted: the rate the treaty expects is a
                            # proportion of the payment, so the two sides are
                            # only comparable once both are in GBP.
                            expected_tax = self.currency_converter.to_gbp(
                                expected_tax, currency, date
                            )
                            deducted_tax = self.currency_converter.to_gbp(
                                deducted_tax, tax_currency, date
                            )
                        if not approx_equal(expected_tax, deducted_tax):
                            LOGGER.warning(
                                "Determined double taxation treaty does not match the "
                                "base taxation rules (expected %.2f base tax for %s "
                                "but %.2f was deducted) for %s ticker!",
                                expected_tax,
                                treaty.country,
                                deducted_tax,
                                symbol,
                            )
                            treaty = None

            amount = self.currency_converter.to_gbp(
                foreign_amount.amount, currency, date
            )
            tax_amount = self.currency_converter.to_gbp(tax.amount, tax_currency, date)

            dividend = Dividend(
                date=date,
                symbol=symbol,
                amount=amount,
                tax_at_source=tax_amount,
                is_interest=is_interest_fund,
                tax_treaty=treaty,
            )

            self.run.calculation_log_yields[date][f"dividend${symbol}"] = [
                CalculationEntry(
                    rule_type=RuleType.DIVIDEND,
                    quantity=Decimal(1),
                    amount=amount,
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                    fees=Decimal(0),
                    dividend=dividend,
                )
            ]

            if is_interest_fund:
                self.run.total_foreign_interest += amount
