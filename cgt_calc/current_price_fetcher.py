"""Obtain current prices to calculate unrealized gains."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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

        # Imported here because it loads pandas, and most runs fetch no price.
        import yfinance as yf  # type: ignore[import-untyped]  # noqa: PLC0415

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
        """Get the price the share closed at on the day, as it traded then.

        Yahoo rewrites past prices: by default it lowers them for every later
        dividend, and it divides them by every later split whatever is asked.
        So the dividend adjustment is not requested, and the splits after the
        day are multiplied back, read from the same history. A split entry on
        the day itself is not: that day's close is already after it, and
        Yahoo records some spin-offs as splits on their own day.
        """
        # Imported here because it loads pandas, and most runs fetch no price.
        import yfinance as yf  # noqa: PLC0415

        yf_ticker = yf.Ticker(symbol)
        prices = yf_ticker.history(
            interval="1d",
            start=date.strftime("%Y-%m-%d"),
            auto_adjust=False,
            actions=True,
        )
        # With no end, Yahoo starts at the next day it has a price for: after
        # a day the market was shut, or at today's quote for a ticker it has
        # no history for.
        if prices.empty or prices.index[0].date() != date:
            raise MarketDataMissingError(symbol, date)
        closing_price = Decimal(format(prices.iloc[0]["Close"], ".15g"))
        for ratio in prices["Stock Splits"].iloc[1:]:
            if ratio:
                closing_price *= Decimal(format(ratio, ".15g"))
        currency = yf_ticker.info.get("currency") if yf_ticker.info else None
        return self._convert_to_gbp(closing_price, currency, date)
