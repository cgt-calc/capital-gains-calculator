"""Test CurrentPriceFetcher."""

from __future__ import annotations

import datetime
from decimal import Decimal
import sys
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import MarketDataMissingError
from cgt_calc.model import CurrencyCode
from tests.utils import run_cli

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeTicker:
    """Stand-in for yf.Ticker that returns a canned info dict."""

    def __init__(self, info: dict[str, float | str]) -> None:
        """Store the info dict to return."""
        self.info = info


def _fetcher() -> CurrentPriceFetcher:
    today = datetime.datetime.now().date()
    converter = CurrencyConverter(
        None,
        {
            today: {
                CurrencyCode("USD"): Decimal("1.25"),
                CurrencyCode("EUR"): Decimal("1.15"),
            }
        },
    )
    return CurrentPriceFetcher(converter)


def test_uses_current_price_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """currentPrice, when present, is used as-is.

    The ticker info has no "currency" field, so the price is read as USD.
    """
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0}),
    )
    price = _fetcher().get_current_market_price("AAPL")
    assert price == Decimal("100.0") / Decimal("1.25")


def test_falls_back_to_regular_market_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """ETFs like VTI lack currentPrice but carry regularMarketPrice and navPrice.

    See https://github.com/cgt-calc/capital-gains-calculator/issues/801.
    """
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({"regularMarketPrice": 366.79, "navPrice": 366.74}),
    )
    price = _fetcher().get_current_market_price("VTI")
    assert price == Decimal("366.79") / Decimal("1.25")


def test_falls_back_to_nav_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some tickers only carry navPrice, with no currentPrice or regularMarketPrice."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({"navPrice": 42.5}),
    )
    price = _fetcher().get_current_market_price("BND")
    assert price == Decimal("42.5") / Decimal("1.25")


def test_returns_none_when_no_price_field_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No known price field means the price is genuinely unavailable."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({"someOtherField": 1}),
    )
    assert _fetcher().get_current_market_price("XYZ") is None


def test_returns_none_when_ticker_info_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty info dict (e.g. an unknown symbol) yields no price."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({}),
    )
    assert _fetcher().get_current_market_price("UNKNOWN") is None


def test_gbp_pence_quoted_ticker_is_divided_by_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LSE-listed tickers are quoted in pence ("GBp"), not pounds or USD.

    yfinance's "GBp" is not a real ISO currency code, so it must be
    converted directly to GBP rather than looked up via CurrencyConverter.
    """
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0, "currency": "GBp"}),
    )
    price = _fetcher().get_current_market_price("VOD.L")
    assert price == Decimal("1.0")


def test_eur_quoted_ticker_uses_eur_rate_not_usd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A EUR-quoted ticker should be converted using the EUR rate, not USD."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0, "currency": "EUR"}),
    )
    price = _fetcher().get_current_market_price("MC.PA")
    assert price == Decimal("100.0") / Decimal("1.15")


class FakeEmptyHistoryTicker:
    """Stand-in for yf.Ticker with no historical data."""

    def history(self, **kwargs: str) -> pd.DataFrame:
        """Return an empty price history."""
        return pd.DataFrame()


def test_raises_clear_error_when_no_market_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty price history raises an error naming the symbol and date."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeEmptyHistoryTicker(),
    )
    with pytest.raises(MarketDataMissingError, match=r"FOO.*2021-05-10"):
        _fetcher().get_closing_price("FOO", datetime.date(2021, 5, 10))


class FakeHistoryTicker:
    """Stand-in for yf.Ticker with daily price history from the day asked for.

    Yahoo answers a request with no end with every day from the start, and
    `Stock Splits` holds each day's split ratio, 0 where there was none.
    """

    def __init__(
        self,
        close: float,
        currency: str,
        *,
        splits: Sequence[float] = (0.0,),
        days_late: int = 0,
    ) -> None:
        """Store the first day's close, each day's split and the currency.

        `splits` has one entry per day, the first for the day asked for.
        `days_late` starts the history that many days after it, as a day the
        market was shut does.
        """
        self._close = close
        self._splits = splits
        self._days_late = days_late
        self.info = {"currency": currency}
        self.requested: dict[str, object] = {}

    def history(self, **kwargs: object) -> pd.DataFrame:
        """Return the history and remember what was asked for."""
        self.requested = kwargs
        start = pd.Timestamp(str(kwargs["start"]), tz="America/New_York")
        days = pd.date_range(
            start + pd.Timedelta(days=self._days_late),
            periods=len(self._splits),
            freq="D",
        )
        return pd.DataFrame(
            {"Close": self._close, "Stock Splits": list(self._splits)}, index=days
        )


def test_closing_price_converted_at_historical_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical closing prices are converted at their own date's rate.

    Seeding today's rate with a different value pins the regression where
    the conversion silently used today's rate instead of the historical one.
    """
    historical_date = datetime.date(2021, 5, 10)
    today = datetime.datetime.now().date()
    converter = CurrencyConverter(
        None,
        {
            historical_date: {CurrencyCode("USD"): Decimal("1.25")},
            today: {CurrencyCode("USD"): Decimal(2)},
        },
    )
    fetcher = CurrentPriceFetcher(converter)
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeHistoryTicker(100.0, "USD"),
    )

    price = fetcher.get_closing_price("AAPL", historical_date)

    assert price == Decimal(100) / Decimal("1.25")


@pytest.mark.parametrize(
    ("splits", "expected"),
    [
        pytest.param(
            (0.0, 0.0, 10.0, 0.0), Decimal("481.70"), id="a-later-split-is-undone"
        ),
        pytest.param(
            (0.0, 10.0, 0.0, 0.8),
            Decimal("385.36"),
            id="later-split-and-consolidation-both-undone",
        ),
        pytest.param(
            (1.196,), Decimal("48.17"), id="the-day-s-own-entry-is-left-alone"
        ),
    ],
)
def test_closing_price_is_the_price_traded_that_day(
    monkeypatch: pytest.MonkeyPatch, splits: tuple[float, ...], expected: Decimal
) -> None:
    """Yahoo divides past closes by later splits; they are multiplied back.

    An entry on the day itself is left alone: that day's close is already
    after it, and Yahoo records some spin-offs that way, such as 3M's 1.196
    for Solventum on 2024-04-01.
    """
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeHistoryTicker(48.17, "GBP", splits=splits),
    )

    price = _fetcher().get_closing_price("FOO", datetime.date(2024, 1, 2))

    assert price == expected


def test_closing_price_is_asked_for_without_dividend_adjustment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yahoo's default, `auto_adjust=True`, lowers past closes for dividends."""
    ticker = FakeHistoryTicker(48.17, "GBP")
    monkeypatch.setattr("yfinance.Ticker", lambda symbol: ticker)

    _fetcher().get_closing_price("FOO", datetime.date(2024, 1, 2))

    assert ticker.requested.get("auto_adjust", True) is False


def test_a_history_starting_after_the_day_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day the market was shut is not priced from the next day's close."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeHistoryTicker(48.17, "GBP", days_late=1),
    )

    with pytest.raises(MarketDataMissingError, match=r"FOO.*2024-01-01"):
        _fetcher().get_closing_price("FOO", datetime.date(2024, 1, 1))


def test_a_run_that_fetches_no_price_loads_neither_yfinance_nor_pandas() -> None:
    """A run that fetches no price loads neither yfinance nor pandas.

    Together they double the modules a start loads. yfinance is imported
    only where Yahoo is asked for a price, and nothing else such a run
    imports may bring pandas back.
    """
    script = (
        "import sys\n"
        "from cgt_calc.cli import main\n"
        "status = main()\n"
        "print('loaded:', sorted({'yfinance', 'pandas'} & set(sys.modules)), file=sys.stderr)\n"
        "sys.exit(status)\n"
    )
    result = run_cli(
        [
            sys.executable,
            "-c",
            script,
            "--year",
            "2022",
            "--raw-file",
            "tests/raw/data/test_data.csv",
            "--no-balance-check",
            "--no-report",
            "--exchange-rates-file",
            "tests/exchange_rates_data.csv",
        ]
    )

    assert result.stderr.splitlines()[-1] == "loaded: []"
