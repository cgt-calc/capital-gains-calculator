"""Obtain current prices to calculate unrealized gains."""

from __future__ import annotations

from contextlib import suppress
import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import yfinance as yf  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from .currency_converter import CurrencyConverter
from .exceptions import ExchangeRateMissingError, MarketDataMissingError
from .model import CurrencyCode


class CurrentPriceFetcher:
    """Fetches current and historical market prices and converts them to GBP."""

    def __init__(
        self,
        converter: CurrencyConverter,
        current_prices_data: dict[str, Decimal | None] | None = None,
        historical_prices_data: dict[str, dict[datetime.date, Decimal]] | None = None,
    ):
        """Store the converter and optional pre-seeded price data."""
        self.current_prices_data = current_prices_data
        self.historical_prices_data = historical_prices_data or {}
        self.converter = converter

    def _convert_to_gbp(
        self, price: Decimal, currency: str | None, date: datetime.date
    ) -> Decimal:
        """Convert a price quoted in the given yfinance currency to GBP.

        yfinance uses the non-ISO code "GBp" (lowercase p) for LSE-listed
        instruments quoted in pence rather than pounds, so that case is
        converted directly instead of going through CurrencyConverter, which
        only knows real ISO currency codes and has no exchange rate for it.
        """
        if currency == "GBp":
            return price / Decimal(100)
        code = CurrencyCode.parse(currency or "USD")
        if code is None:
            raise ExchangeRateMissingError(currency or "USD", date)
        return self.converter.to_gbp(price, code, date)

    def get_current_market_price(self, symbol: str) -> Decimal | None:
        """Given a symbol gets the current market price."""
        if self.current_prices_data is not None and symbol in self.current_prices_data:
            return self.current_prices_data[symbol]

        ticker = yf.Ticker(symbol).info
        if not ticker:
            return None
        # ETFs often lack currentPrice, e.g. VTI only has regularMarketPrice
        # and navPrice.
        market_price = (
            ticker.get("currentPrice")
            or ticker.get("regularMarketPrice")
            or ticker.get("navPrice")
        )
        if market_price is None:
            return None
        market_price_decimal = Decimal(format(market_price, ".15g"))
        return self._convert_to_gbp(
            market_price_decimal, ticker.get("currency"), datetime.datetime.now().date()
        )

    def known_closing_price(self, symbol: str, date: datetime.date) -> Decimal | None:
        """Return an already-known closing price, or None without fetching.

        For asking about a ticker that may not exist. Only the prices given to
        this run answer; nothing is looked up, so a name the market never
        carried costs nothing to ask about and cannot fail the run.
        """
        return self.historical_prices_data.get(symbol, {}).get(date)

    def get_closing_price(self, symbol: str, date: datetime.date) -> Decimal:
        """Get the price of the share on closing time."""
        with suppress(KeyError):
            return self.historical_prices_data[symbol][date]

        yf_ticker = yf.Ticker(symbol)
        prices = yf_ticker.history(
            interval="1d",
            start=date.strftime("%Y-%m-%d"),
            end=(date + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if prices.empty:
            raise MarketDataMissingError(symbol, date)
        closing_price = prices.iloc[0]["Close"]
        closing_price_decimal = Decimal(format(closing_price, ".15g"))
        currency = yf_ticker.info.get("currency") if yf_ticker.info else None
        return self._convert_to_gbp(closing_price_decimal, currency, date)
