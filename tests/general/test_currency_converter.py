"""Tests for currency converter exchange rate loading."""

from __future__ import annotations

import datetime
from decimal import Decimal
from itertools import permutations
import logging
import re
from typing import TYPE_CHECKING, NoReturn

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.const import RuntimeMode
import cgt_calc.currency_converter
from cgt_calc.currency_converter import (
    CurrencyConverter,
    StrictTestCurrencyConverter,
    TestCurrencyConverter as RecordingCurrencyConverter,
)
from cgt_calc.exceptions import (
    CalculationError,
    ExchangeRateMissingError,
    ExternalApiError,
    ParsingError,
)
from cgt_calc.model import CurrencyCode, ForeignCurrencyAmount

if TYPE_CHECKING:
    from pathlib import Path


def test_read_exchange_rates_handles_empty_file(tmp_path: Path) -> None:
    """Empty rates files produce an empty cache without failing."""
    rates_file = tmp_path / "empty.csv"
    # create an empty file
    rates_file.touch()

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache
    assert cache == {}


def test_read_exchange_rates_skips_blank_rows(tmp_path: Path) -> None:
    """Blank rows are ignored rather than causing parsing errors."""
    rates_file = tmp_path / "blank.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,1.25\n,,\n2024-02-01,EUR,1.10\n",
        encoding="utf8",
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache

    january = datetime.date(2024, 1, 1)
    february = datetime.date(2024, 2, 1)
    assert cache[january] == {CurrencyCode("USD"): Decimal("1.25")}
    assert cache[february] == {CurrencyCode("EUR"): Decimal("1.10")}


def test_read_exchange_rates_raises_on_invalid_date(tmp_path: Path) -> None:
    """Invalid month values raise a parsing error with context."""
    rates_file = tmp_path / "invalid_date.csv"
    rates_file.write_text(
        "month,currency,rate\n2024/01/01,USD,1.25\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match="Invalid date '2024/01/01'"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_raises_on_invalid_rate(tmp_path: Path) -> None:
    """Non-decimal rate values raise a parsing error."""
    rates_file = tmp_path / "invalid_rate.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,one.two\n",
        encoding="utf8",
    )

    with pytest.raises(
        ParsingError,
        match=re.escape("Invalid rate 'one.two'"),
    ):
        CurrencyConverter(exchange_rates_file=rates_file)


@pytest.mark.parametrize("rate", ["0", "-1.25", "NaN", "Infinity"])
def test_read_exchange_rates_rejects_non_positive_or_non_finite_rate(
    tmp_path: Path, rate: str
) -> None:
    """Rates that cannot represent a real conversion are invalid input."""
    rates_file = tmp_path / "invalid_rate.csv"
    rates_file.write_text(
        f"month,currency,rate\n2024-01-01,USD,{rate}\n", encoding="utf8"
    )

    with pytest.raises(ParsingError, match="must be finite and positive"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_raises_on_malformed_currency(tmp_path: Path) -> None:
    """A malformed code in the rate file is reported there, not as a missing rate."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n2024-01-01,usd,1.25\n", encoding="utf8")

    with pytest.raises(ParsingError, match="Invalid currency code 'usd' at line 2"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_reports_physical_line_numbers(tmp_path: Path) -> None:
    """Comment lines count towards the reported line number."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "# generated\n# do not edit\nmonth,currency,rate\n2024-01-01,usd,1.25\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match="at line 4"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_raises_on_duplicate_currency(tmp_path: Path) -> None:
    """Duplicate currency entries for the same month raise a parsing error."""
    rates_file = tmp_path / "duplicate.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,1.25\n2024-01-01,USD,1.30\n",
        encoding="utf8",
    )

    with pytest.raises(
        ParsingError,
        match="Duplicate currency entry for USD on 2024-01-01",
    ):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_skips_comment_lines(tmp_path: Path) -> None:
    """Lines starting with # are ignored rather than causing parsing errors."""
    rates_file = tmp_path / "comments.csv"
    rates_file.write_text(
        "# This is a comment header\n# another comment line\nmonth,currency,rate\n2024-01-01,USD,1.25\n# middle comment\n2024-02-01,EUR,1.10\n",
        encoding="utf8",
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache

    january = datetime.date(2024, 1, 1)
    february = datetime.date(2024, 2, 1)
    assert cache[january] == {CurrencyCode("USD"): Decimal("1.25")}
    assert cache[february] == {CurrencyCode("EUR"): Decimal("1.10")}


class OfflineSession:
    """Session stub that fails every request."""

    def get(self, url: str, timeout: int) -> NoReturn:
        """Simulate a network failure."""
        raise requests_exceptions.ConnectionError(f"offline: {url}")


def test_hmrc_fetch_announces_itself(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetching rates from HMRC logs a progress line before the request."""
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", OfflineSession())

    with caplog.at_level(logging.INFO), pytest.raises(ExternalApiError):
        converter.currency_to_gbp_rate(CurrencyCode("USD"), datetime.date(2021, 5, 10))

    assert "Fetching HMRC exchange rates for 2021-05..." in caplog.text


class CannedResponse:
    """Minimal stand-in for a successful requests.Response."""

    ok = True

    def __init__(self, text: str) -> None:
        """Store the body to return."""
        self.text = text


class CannedSession:
    """Session stub that returns the same response for every request."""

    def __init__(self, response: CannedResponse) -> None:
        """Store the response to return."""
        self._response = response

    def get(self, url: str, timeout: int) -> CannedResponse:
        """Return the canned response."""
        return self._response


def test_hmrc_response_missing_rate_element_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row without a rateNew element raises ExternalApiError."""
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate><currencyCode>USD</currencyCode></exchangeRate>"
        "</exchangeRateMonthList>"
    )
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", CannedSession(CannedResponse(xml)))

    with pytest.raises(ExternalApiError, match="missing expected currency data"):
        converter.currency_to_gbp_rate(CurrencyCode("USD"), datetime.date(2021, 5, 10))


def test_hmrc_response_invalid_rate_value_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate value that is not a valid decimal raises ExternalApiError."""
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate>"
        "<currencyCode>USD</currencyCode>"
        "<rateNew>not-a-rate</rateNew>"
        "</exchangeRate>"
        "</exchangeRateMonthList>"
    )
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", CannedSession(CannedResponse(xml)))

    with pytest.raises(ExternalApiError, match="contains invalid rate"):
        converter.currency_to_gbp_rate(CurrencyCode("USD"), datetime.date(2021, 5, 10))


@pytest.mark.parametrize("rate", ["0", "-1.25", "NaN", "Infinity"])
def test_hmrc_response_rejects_non_positive_or_non_finite_rate(
    monkeypatch: pytest.MonkeyPatch, rate: str
) -> None:
    """Invalid numeric rates from the external service are reported as API errors."""
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate>"
        "<currencyCode>USD</currencyCode>"
        f"<rateNew>{rate}</rateNew>"
        "</exchangeRate>"
        "</exchangeRateMonthList>"
    )
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", CannedSession(CannedResponse(xml)))

    with pytest.raises(ExternalApiError, match="non-positive or non-finite"):
        converter.currency_to_gbp_rate(CurrencyCode("USD"), datetime.date(2021, 5, 10))


def test_hmrc_response_malformed_currency_code_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A currency code that is not three uppercase letters raises ExternalApiError."""
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate>"
        "<currencyCode>usd</currencyCode>"
        "<rateNew>1.25</rateNew>"
        "</exchangeRate>"
        "</exchangeRateMonthList>"
    )
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", CannedSession(CannedResponse(xml)))

    with pytest.raises(ExternalApiError, match="invalid currency code"):
        converter.currency_to_gbp_rate(CurrencyCode("USD"), datetime.date(2021, 5, 10))


DATE = datetime.date(2024, 1, 1)
GBP = CurrencyCode("GBP")
USD = CurrencyCode("USD")
PLN = CurrencyCode("PLN")


class FakeResponse:
    """Canned HMRC API response."""

    def __init__(self, *, ok: bool, status_code: int = 200, text: str = "") -> None:
        """Store response fields."""
        self.ok = ok
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Session stub returning a canned response and recording the URL."""

    def __init__(self, response: FakeResponse) -> None:
        """Store the canned response."""
        self._response = response
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        """Record the URL and return the canned response."""
        self.urls.append(url)
        return self._response


def test_create_for_each_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the converter matching the runtime mode."""
    monkeypatch.setattr(cgt_calc.currency_converter, "CGT_MODE", RuntimeMode.PROD)
    assert type(CurrencyConverter.create()) is CurrencyConverter

    monkeypatch.setattr(
        cgt_calc.currency_converter, "CGT_MODE", RuntimeMode.TEST_STRICT
    )
    assert type(CurrencyConverter.create()) is StrictTestCurrencyConverter

    monkeypatch.setattr(cgt_calc.currency_converter, "CGT_MODE", RuntimeMode.TEST)
    assert type(CurrencyConverter.create()) is RecordingCurrencyConverter


def test_read_exchange_rates_rejects_unexpected_columns(tmp_path: Path) -> None:
    """Reject rates files with a different schema."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate,extra\n2024-01-01,USD,1.25,x\n", encoding="utf8"
    )

    with pytest.raises(ParsingError, match="Unexpected columns"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_rejects_missing_values(tmp_path: Path) -> None:
    """Reject rows with missing values."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n2024-01-01,USD,\n", encoding="utf8")

    with pytest.raises(ParsingError, match="Missing data"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_write_exchange_rates_file(tmp_path: Path) -> None:
    """Write the cache back as sorted CSV rows."""
    rates_file = tmp_path / "rates.csv"

    CurrencyConverter._write_exchange_rates_file(  # noqa: SLF001
        rates_file,
        {
            DATE: {
                CurrencyCode("USD"): Decimal("1.25"),
                CurrencyCode("EUR"): Decimal("1.10"),
            }
        },
    )

    assert rates_file.read_text(encoding="utf-8") == (
        "month,currency,rate\n2024-01-01,EUR,1.10\n2024-01-01,USD,1.25\n"
    )


def test_query_hmrc_api_old_endpoint_error_includes_https_url_and_rates_file(
    tmp_path: Path,
) -> None:
    """Use HTTPS before 2021 and mention the rates file on errors."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n", encoding="utf8")
    converter = CurrencyConverter(exchange_rates_file=rates_file)

    class FailingSession:
        def get(self, url: str, timeout: int) -> NoReturn:
            raise requests_exceptions.ConnectionError(f"offline: {url}")

    converter.session = FailingSession()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(ExternalApiError, match=r"rates\.csv") as excinfo:
        converter.currency_to_gbp_rate(CurrencyCode("USD"), datetime.date(2019, 5, 1))

    message = str(excinfo.value)
    assert "https://www.hmrc.gov.uk/" in message
    assert "exrates-monthly-0519" in message


def test_query_hmrc_api_http_error_includes_snippet() -> None:
    """Include a truncated response body in HTTP errors."""
    converter = CurrencyConverter()
    response = FakeResponse(ok=False, status_code=500, text="x" * 300)
    converter.session = FakeSession(response)  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(ExternalApiError, match="HTTP 500") as excinfo:
        converter.currency_to_gbp_rate(CurrencyCode("USD"), DATE)

    message = str(excinfo.value)
    assert "xxx" in message
    assert "..." in message
    # The full 300 character body must not leak into the message.
    assert "x" * 300 not in message


def test_cnh_is_treated_as_cny() -> None:
    """Convert offshore yuan using the CNY rate."""
    converter = CurrencyConverter(
        initial_data={DATE: {CurrencyCode("CNY"): Decimal(9)}}
    )

    assert converter.currency_to_gbp_rate(CurrencyCode("CNH"), DATE) == Decimal(9)


def test_missing_currency_for_known_date() -> None:
    """Raise when the date is cached but the currency is missing."""
    converter = CurrencyConverter(
        initial_data={DATE: {CurrencyCode("USD"): Decimal(1)}}
    )

    with pytest.raises(ExchangeRateMissingError):
        converter.currency_to_gbp_rate(CurrencyCode("EUR"), DATE)


def test_test_converter_records_new_rates(tmp_path: Path) -> None:
    """Record rates fetched during tests in the rates file."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n", encoding="utf8")
    converter = RecordingCurrencyConverter(exchange_rates_file=rates_file)

    def fake_query(date: datetime.date) -> None:
        converter.cache[date] = {CurrencyCode("USD"): Decimal("1.25")}

    converter._query_hmrc_api = fake_query  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]  # noqa: SLF001

    assert converter.currency_to_gbp_rate(CurrencyCode("USD"), DATE) == Decimal("1.25")
    assert "2024-01-01,USD,1.25" in rates_file.read_text(encoding="utf-8")

    # Appending the same rate again is a no-op.
    RecordingCurrencyConverter._append_exchange_rates_file(  # noqa: SLF001
        rates_file, DATE, CurrencyCode("USD"), Decimal("1.25")
    )
    assert rates_file.read_text(encoding="utf-8").count("USD") == 1


def test_strict_converter_refuses_to_fetch() -> None:
    """The CI converter never calls the HMRC API."""
    converter = StrictTestCurrencyConverter()

    with pytest.raises(RuntimeError, match="HMRC values missing for 2024-01"):
        converter.currency_to_gbp_rate(CurrencyCode("USD"), DATE)


def test_combine_amounts_is_order_independent_for_gbp_and_one_foreign() -> None:
    """Any ordering of supported rows produces the same foreign amount."""
    converter = CurrencyConverter(initial_data={DATE: {USD: Decimal("1.25")}})
    rows = [
        ForeignCurrencyAmount(Decimal(100), USD),
        ForeignCurrencyAmount(Decimal(40), GBP),
        ForeignCurrencyAmount(Decimal(40), GBP),
    ]

    for ordered in permutations(rows):
        total = ForeignCurrencyAmount()
        for row in ordered:
            total = converter.combine_amounts(total, row, DATE, autoconvert=True)
        assert total == ForeignCurrencyAmount(Decimal(200), USD)


def test_combine_amounts_keeps_mixed_currency_error_without_opt_in() -> None:
    """The new conversion never weakens the default validation."""
    converter = CurrencyConverter(initial_data={DATE: {USD: Decimal("1.25")}})

    with pytest.raises(CalculationError, match="different currencies: USD and GBP"):
        converter.combine_amounts(
            ForeignCurrencyAmount(Decimal(100), USD),
            ForeignCurrencyAmount(Decimal(80), GBP),
            DATE,
            autoconvert=False,
        )


@pytest.mark.parametrize(
    "pln_amount",
    [
        # Equal to the USD row by value in GBP, so only a tie-break could
        # pick a winner.
        pytest.param(Decimal(400), id="equal-value"),
        # The larger row by value, so only its size could pick a winner.
        pytest.param(Decimal(4000), id="larger-value"),
    ],
)
def test_combine_amounts_refuses_two_foreign_currencies_with_opt_in(
    pln_amount: Decimal,
) -> None:
    """A row's size or position cannot choose a source country."""
    converter = CurrencyConverter(
        initial_data={DATE: {USD: Decimal("1.25"), PLN: Decimal(5)}}
    )
    usd = ForeignCurrencyAmount(Decimal(100), USD)
    pln = ForeignCurrencyAmount(pln_amount, PLN)

    for first, second in ((usd, pln), (pln, usd)):
        with pytest.raises(CalculationError, match="different currencies"):
            converter.combine_amounts(first, second, DATE, autoconvert=True)
