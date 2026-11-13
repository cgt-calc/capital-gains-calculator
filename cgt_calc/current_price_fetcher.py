"""Obtain current prices to calculate unrealized gains."""

from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from .currency_converter import CurrencyConverter
from .exceptions import (
    CgtError,
    ClosingPriceRequestError,
    CurrentPriceRequestError,
    ExchangeRateMissingError,
    MarketDataMissingError,
)
from .model import CurrencyCode


@contextmanager
def _asking_yahoo(failed: CgtError) -> Generator[None]:
    """Report a request to Yahoo Finance that fails as `failed`.

    The HTTP libraries yfinance uses, `curl_cffi` and `requests`, raise
    subclasses of `OSError` for no connection, DNS, a proxy, TLS or a timeout.
    Yahoo refusing with HTTP 429 is `YFRateLimitError`.
    """
    # Imported here because it loads pandas, and most runs fetch no price.
    from yfinance.exceptions import YFRateLimitError  # type: ignore[import-untyped]  # noqa: PLC0415

    try:
        yield
    except (OSError, YFRateLimitError) as err:
        raise failed from err


class CurrentPriceFetcher:
    """Fetches current and historical market prices and converts them to GBP."""

    def __init__(
        self,
        converter: CurrencyConverter,
        current_prices_data: dict[str, Decimal | None] | None = None,
        historical_prices_data: dict[str, dict[dt.date, Decimal]] | None = None,
    ):
        """Store the converter and optional pre-seeded price data."""
        self.current_prices_data = current_prices_data
        self.historical_prices_data = historical_prices_data or {}
        self.converter = converter

    def _convert_to_gbp(
        self, price: Decimal, currency: str | None, date: dt.date
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

    def get_current_market_price(
        self, symbol: str, isin: str | None = None
    ) -> Decimal | None:
        """Get a symbol's current market price, by the first name Yahoo can price.

        Yahoo reads a bare symbol as a US listing, which can be another security
        entirely or nothing at all, while an ISIN names the security itself. So
        the ISIN comes first if known, then the symbol, then the symbol's London
        listing for one with no quote of its own. A failed request raises
        `CurrentPriceRequestError` rather than trying the next name, which after
        the ISIN could price another security.
        """
        if self.current_prices_data is not None and symbol in self.current_prices_data:
            return self.current_prices_data[symbol]

        if isin:
            try:
                price = self._fetch_market_price(isin)
            except ValueError:  # how Yahoo's library reports an unknown ISIN
                price = None
            if price is not None:
                return price
        price = self._fetch_market_price(symbol)
        if price is None and "." not in symbol:
            price = self._fetch_market_price(f"{symbol}.L")
        return price

    def _fetch_market_price(self, name: str) -> Decimal | None:
        """Ask Yahoo for the current price of a symbol or ISIN."""
        # Imported here because it loads pandas, and most runs fetch no price.
        import yfinance as yf  # type: ignore[import-untyped]  # noqa: PLC0415

        with _asking_yahoo(CurrentPriceRequestError(name)):
            ticker = yf.Ticker(name).info
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
            market_price_decimal, ticker.get("currency"), dt.datetime.now().date()
        )

    def known_closing_price(self, symbol: str, date: dt.date) -> Decimal | None:
        """Return an already-known closing price, or None without fetching.

        For asking about a ticker that may not exist. Only the prices given to
        this run answer; nothing is looked up, so a name the market never
        carried costs nothing to ask about and cannot fail the run.
        """
        return self.historical_prices_data.get(symbol, {}).get(date)

    def get_closing_price(self, symbol: str, date: dt.date) -> Decimal:
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

        failed = ClosingPriceRequestError(symbol, date)
        with _asking_yahoo(failed):
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
        with _asking_yahoo(failed):
            info = yf_ticker.info
        currency = info.get("currency") if info else None
        return self._convert_to_gbp(closing_price, currency, date)
