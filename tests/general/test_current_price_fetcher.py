"""Test CurrentPriceFetcher."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, localcontext
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError  # type: ignore[import-untyped]

from cgt_calc.cli import main
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CurrentPriceRequestError, MarketDataMissingError
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import CurrencyCode
from tests.utils import run_cli, stderr_alerts, summary_figure

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


class FakeTicker:
    """Stand-in for yf.Ticker that returns a canned info dict."""

    def __init__(self, info: dict[str, float | str]) -> None:
        """Store the info dict to return."""
        self.info = info


# What yfinance passes on from curl_cffi when there is no connection.
OFFLINE = CurlConnectionError(
    "Failed to perform, curl: (7) Failed to connect to query2.finance.yahoo.com "
    "port 443 after 0 ms: Could not connect to server"
)


class FailingTicker:
    """Stand-in for yf.Ticker whose info request raises `error`.

    Given `history`, its price history answers, as when Yahoo starts refusing
    partway through; otherwise the history request fails first, as offline.
    """

    def __init__(
        self, error: Exception, history: FakeHistoryTicker | None = None
    ) -> None:
        """Store the error to raise and the history, if any, to answer with."""
        self._error = error
        self._history = history

    @property
    def info(self) -> dict[str, float | str]:
        """Fail the info request."""
        raise self._error

    def history(self, **kwargs: object) -> pd.DataFrame:
        """Answer from the stored history, or fail the request."""
        if self._history is None:
            raise self._error
        return self._history.history(**kwargs)


def _fetcher() -> CurrentPriceFetcher:
    today = dt.datetime.now().date()
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


def _lookup_fake(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, dict[str, float | str] | None]
) -> list[str]:
    """Make yf.Ticker answer from a dict, where None is an unknown ISIN."""
    lookups: list[str] = []

    def ticker(name: str) -> FakeTicker:
        lookups.append(name)
        answer = answers.get(name, {})
        if answer is None:
            raise ValueError(f"Invalid ISIN number: {name}")
        return FakeTicker(answer)

    monkeypatch.setattr("yfinance.Ticker", ticker)
    return lookups


PENCE_QUOTE: dict[str, float | str] = {"regularMarketPrice": 900.0, "currency": "GBp"}


def test_isin_is_asked_for_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known ISIN answers alone, as the bare symbol can be another security."""
    lookups = _lookup_fake(monkeypatch, {"IE0005042456": PENCE_QUOTE})
    price = _fetcher().get_current_market_price("ISF", "IE0005042456")
    assert price == Decimal(9)
    assert lookups == ["IE0005042456"]


def test_missing_isin_uses_the_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no ISIN the symbol answers, and London isn't asked."""
    lookups = _lookup_fake(
        monkeypatch, {"AAPL": {"currentPrice": 500.0, "currency": "USD"}}
    )
    assert _fetcher().get_current_market_price("AAPL") == Decimal(400)
    assert lookups == ["AAPL"]


@pytest.mark.parametrize("isin_answer", [None, {}], ids=["unknown", "no-price"])
def test_isin_without_a_price_falls_back_to_the_symbol(
    monkeypatch: pytest.MonkeyPatch, isin_answer: dict[str, float | str] | None
) -> None:
    """An ISIN Yahoo can't price leaves the symbol to try."""
    lookups = _lookup_fake(
        monkeypatch, {"IE0005042456": isin_answer, "ISF": PENCE_QUOTE}
    )
    price = _fetcher().get_current_market_price("ISF", "IE0005042456")
    assert price == Decimal(9)
    assert lookups == ["IE0005042456", "ISF"]


def test_symbol_without_a_quote_falls_back_to_london(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """London is the last resort, for a symbol with no quote of its own."""
    lookups = _lookup_fake(monkeypatch, {"ISF.L": PENCE_QUOTE})
    assert _fetcher().get_current_market_price("ISF") == Decimal(9)
    assert lookups == ["ISF", "ISF.L"]


def test_a_failed_isin_request_is_not_followed_by_the_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The symbol would answer, but it can be another security: no price."""
    lookups: list[str] = []

    def ticker(name: str) -> FakeTicker | FailingTicker:
        lookups.append(name)
        return (
            FailingTicker(OFFLINE)
            if name == "IE0005042456"
            else FakeTicker(PENCE_QUOTE)
        )

    monkeypatch.setattr("yfinance.Ticker", ticker)
    with pytest.raises(CurrentPriceRequestError, match="IE0005042456"):
        _fetcher().get_current_market_price("ISF", "IE0005042456")
    assert lookups == ["IE0005042456"]


def test_nothing_found_anywhere_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every name is tried once before giving up."""
    lookups = _lookup_fake(monkeypatch, {})
    assert _fetcher().get_current_market_price("ISF", "IE0005042456") is None
    assert lookups == ["IE0005042456", "ISF", "ISF.L"]


def test_symbol_with_an_exchange_suffix_gets_no_london_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symbol that already names its exchange isn't given a second one."""
    lookups = _lookup_fake(monkeypatch, {})
    assert _fetcher().get_current_market_price("MC.PA") is None
    assert lookups == ["MC.PA"]


def test_portfolio_entry_passes_the_transactions_isin() -> None:
    """The calculator gives the fetcher the ISIN this run knows for the symbol."""
    asked: list[tuple[str, str | None]] = []

    def price(symbol: str, isin: str | None = None) -> Decimal:
        asked.append((symbol, isin))
        return Decimal(9)

    calculator = SimpleNamespace(
        calc_unrealized_gains=True,
        price_fetcher=SimpleNamespace(get_current_market_price=price),
        isin_converter=SimpleNamespace(transaction_isins={"ISF": "IE0005042456"}),
    )
    entry = CapitalGainsCalculator.make_portfolio_entry(
        cast("CapitalGainsCalculator", calculator),
        "ISF",
        Decimal(2),
        Decimal(10),
    )
    assert entry.unrealized_gains == Decimal(8)
    assert asked == [("ISF", "IE0005042456")]


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
        _fetcher().get_closing_price("FOO", dt.date(2021, 5, 10))


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
    historical_date = dt.date(2021, 5, 10)
    today = dt.datetime.now().date()
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

    price = _fetcher().get_closing_price("FOO", dt.date(2024, 1, 2))

    assert price == expected


def test_closing_price_is_asked_for_without_dividend_adjustment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yahoo's default, `auto_adjust=True`, lowers past closes for dividends."""
    ticker = FakeHistoryTicker(48.17, "GBP")
    monkeypatch.setattr("yfinance.Ticker", lambda symbol: ticker)

    _fetcher().get_closing_price("FOO", dt.date(2024, 1, 2))

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
        _fetcher().get_closing_price("FOO", dt.date(2024, 1, 1))


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


def _run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: str,
    ticker: Callable[[str], object],
    *args: str,
) -> int:
    """Run the CLI in this process on RAW rows, with yf.Ticker replaced."""
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n" + rows, encoding="utf-8"
    )
    monkeypatch.setattr("yfinance.Ticker", ticker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cgt-calc",
            "--year",
            "2024",
            "--raw-file",
            str(raw),
            "--no-report",
            "--no-balance-check",
            "--exchange-rates-file=",
            "--isin-translation-file=",
            *args,
        ],
    )
    # main() enables the FloatOperation trap on the active decimal context;
    # keep that from leaking into other tests in the same worker.
    with localcontext():
        return main()


@pytest.mark.parametrize(
    ("error", "history"),
    [
        pytest.param(OFFLINE, None, id="offline"),
        pytest.param(YFRateLimitError(), None, id="rate-limited"),
        pytest.param(
            YFRateLimitError(),
            FakeHistoryTicker(48.17, "GBP"),
            id="rate-limited-after-the-history",
        ),
    ],
)
def test_a_failed_request_for_a_spin_off_price_stops_with_what_to_do(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    history: FakeHistoryTicker | None,
) -> None:
    """The price divides the cost, so the run stops, saying why and what to do.

    BBB, the new holding, is priced first. A failed request is not a fault in
    cgt-calc: no traceback, and no advice to update it.
    """
    spin_offs = tmp_path / "spin_offs.csv"
    spin_offs.write_text("dst,src\nBBB,AAA\n", encoding="utf-8")

    status = _run_main(
        tmp_path,
        monkeypatch,
        "2024-05-01,BUY,AAA,10,4.00,0,GBP\n2024-06-03,SPIN_OFF,BBB,5,0,0,GBP\n",
        lambda symbol: FailingTicker(error, history),
        "--spin-offs-file",
        str(spin_offs),
    )

    assert status == 1
    err = capsys.readouterr().err
    assert stderr_alerts(err) == [
        (
            "ERROR: Could not get the closing price of BBB on 2024-06-03 from Yahoo "
            "Finance: the request failed. Check your connection and try again, or "
            "give the closing prices of both holdings in the spin-off on that day, "
            "in the same currency, in a file passed with --prices-file (header "
            "'date,symbol,price')."
        )
    ]
    assert "cgt-calc update" not in err


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(OFFLINE, id="offline"),
        pytest.param(YFRateLimitError(), id="rate-limited"),
    ],
)
def test_failed_requests_for_today_s_prices_are_named_in_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    """Their holdings are left out of the estimate, and the run finishes.

    The warning lists them as the report does, sorted: BBB is bought first.
    CCC cost £40.00 for 10 and is worth £5.00 each today, an unrealised gain of
    10 x £5.00 - £40.00 = £10.00, which is the whole total.
    """

    def ticker(name: str) -> FakeTicker | FailingTicker:
        if name == "CCC":
            return FakeTicker({"currentPrice": 5.0, "currency": "GBP"})
        return FailingTicker(error)

    status = _run_main(
        tmp_path,
        monkeypatch,
        "2024-05-01,BUY,BBB,5,2.00,0,GBP\n"
        "2024-05-01,BUY,AAA,10,4.00,0,GBP\n"
        "2024-05-01,BUY,CCC,10,4.00,0,GBP\n",
        ticker,
        "--unrealized-gains",
    )

    assert status == 0
    out, err = capsys.readouterr()
    assert stderr_alerts(err) == [
        (
            "WARNING: Could not get today's prices from Yahoo Finance for AAA, BBB: "
            "the request failed. Their unrealised gains are left out of the estimate."
        )
    ]
    assert "  AAA: 10.00, £40.00 (unrealized gains: unknown)\n" in out
    assert "  BBB: 5.00, £10.00 (unrealized gains: unknown)\n" in out
    assert "  CCC: 10.00, £40.00 (unrealized gains: £10.00)\n" in out
    assert summary_figure(out, "Unrealized gains") == "10.00"
    assert "WARNING: Some unrealized gains couldn't be calculated." in out
