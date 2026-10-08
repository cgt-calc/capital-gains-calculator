"""Tests for currency converter exchange rate loading."""

from __future__ import annotations

import datetime
from decimal import Decimal
from itertools import permutations
import logging
import os
from pathlib import Path
import re
import stat
import sys
from typing import TYPE_CHECKING, NoReturn, override

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.const import SHIPPED_RATES_YEARS, RuntimeMode
import cgt_calc.currency_converter
from cgt_calc.currency_converter import (
    CurrencyConverter,
    StrictTestCurrencyConverter,
    TestCurrencyConverter as RecordingCurrencyConverter,
)
from cgt_calc.exceptions import (
    CalculationError,
    CgtError,
    ExternalApiError,
    HmrcRateMissingError,
    ParsingError,
)
from cgt_calc.model import CurrencyCode, ForeignCurrencyAmount

if TYPE_CHECKING:
    from collections.abc import Callable


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


def test_read_exchange_rates_accepts_a_byte_order_mark(tmp_path: Path) -> None:
    """A rates file saved as Excel's "CSV UTF-8" still has a month column."""
    rates_file = tmp_path / "marked.csv"
    rates_file.write_text(
        "\ufeffmonth,currency,rate\n2024-01-01,USD,1.25\n", encoding="utf8"
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)

    assert converter.cache[datetime.date(2024, 1, 1)] == {
        CurrencyCode("USD"): Decimal("1.25")
    }


@pytest.mark.parametrize(
    ("content", "match"),
    [
        pytest.param(
            "month,currency,rate\n2024/01/01,USD,1.25\n",
            "Invalid date '2024/01/01'",
            id="invalid date",
        ),
        pytest.param(
            "month,currency,rate\n2024-01-01,USD,one.two\n",
            re.escape("Invalid rate 'one.two'"),
            id="invalid rate",
        ),
        # Reported in the rates file, not later as a missing rate.
        pytest.param(
            "month,currency,rate\n2024-01-01,usd,1.25\n",
            "Invalid currency code 'usd' at line 2",
            id="malformed currency",
        ),
        pytest.param(
            "# generated\n# do not edit\nmonth,currency,rate\n2024-01-01,usd,1.25\n",
            "at line 4",
            id="comment lines count towards the line number",
        ),
        pytest.param(
            "month,currency,rate\n2024-01-01,USD,1.25\n2024-01-01,USD,1.30\n",
            "Duplicate currency entry for USD on 2024-01-01",
            id="duplicate",
        ),
        pytest.param(
            "month,currency,rate,extra\n2024-01-01,USD,1.25,x\n",
            "must be the header 'month,currency,rate'",
            id="unexpected columns",
        ),
        pytest.param(
            "month,currency,rate\n2024-01-01,USD,\n",
            "Missing data",
            id="missing value",
        ),
        pytest.param(
            "month,currency,rate\n2024-01-01,USD,1.25,\n",
            "Too many values in exchange rate file at line 2: expected 3, found 4",
            id="value beyond the last column",
        ),
        # The header is the error, not the rows it makes too long.
        pytest.param(
            "month,currency\n2024-01-01,USD,1.25\n",
            re.escape("but it is:\n  'month,currency'"),
            id="header shorter than its rows",
        ),
        # Not read as a header with no rows under it, which is an empty file.
        pytest.param(
            "2024-01-01,USD,1.25\n",
            re.escape("but it is:\n  '2024-01-01,USD,1.25'"),
            id="one rate and no header",
        ),
        # Not accepted with the last column of that name deciding the rate.
        pytest.param(
            "month,currency,rate,rate\n2024-01-01,USD,1.25,1.30\n",
            re.escape("but it is:\n  'month,currency,rate,rate'"),
            id="column named twice",
        ),
        pytest.param(
            "# generated\n# do not edit\n2024-01-01,USD,1.25\n",
            "row 3: This line must be the header",
            id="comment lines count towards the header's line number",
        ),
    ],
)
def test_read_exchange_rates_refuses_a_malformed_file(
    tmp_path: Path, content: str, match: str
) -> None:
    """A malformed rates file is refused with a message naming the problem."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(content, encoding="utf8")

    with pytest.raises(ParsingError, match=match):
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

    def __init__(self) -> None:
        """Start with nothing asked."""
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> NoReturn:
        """Simulate a network failure, noting the address asked."""
        self.urls.append(url)
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


class FakeResponse:
    """Canned HMRC API response."""

    def __init__(self, *, ok: bool, status_code: int = 200, text: str = "") -> None:
        """Store response fields."""
        self.ok = ok
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Session stub that returns the same response for every request."""

    def __init__(self, response: FakeResponse) -> None:
        """Store the canned response."""
        self._response = response
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        """Return the canned response, noting the address asked."""
        self.urls.append(url)
        return self._response


def _usd_file(rate: str) -> FakeResponse:
    """Stand in for HMRC's monthly file, which gives one rate for the month."""
    xml = (
        "<exchangeRateMonthList><exchangeRate>"
        f"<currencyCode>USD</currencyCode><rateNew>{rate}</rateNew>"
        "</exchangeRate></exchangeRateMonthList>"
    )
    return FakeResponse(ok=True, text=xml)


def _monthly_usd(rate: str) -> FakeSession:
    """Give the same monthly file whichever month is asked for."""
    return FakeSession(_usd_file(rate))


class MonthlyFiles:
    """Session stub that gives each month's file its own USD rate."""

    def __init__(self, rates: dict[str, str]) -> None:
        """Store each file's rate by the MMYY in its name."""
        self._rates = rates
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        """Return the file for the month the address names."""
        self.urls.append(url)
        return _usd_file(self._rates[url.removesuffix(".xml")[-4:]])


DATE = datetime.date(2024, 1, 1)
GBP = CurrencyCode("GBP")
USD = CurrencyCode("USD")
PLN = CurrencyCode("PLN")


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


# The last month asked of the legacy service and the first asked of the Trade
# Tariff API.
DECEMBER_2020 = datetime.date(2020, 12, 14)
JANUARY_2021 = datetime.date(2021, 1, 11)
RATES_ADDRESS = {
    DECEMBER_2020: (
        "https://www.hmrc.gov.uk/softwaredevelopers/rates/exrates-monthly-1220.xml"
    ),
    JANUARY_2021: (
        "https://www.trade-tariff.service.gov.uk/uk/api/exchange_rates/files/"
        "monthly_xml_2021-01.xml"
    ),
}


def _reply(text: str, *, status: int = 200) -> FakeSession:
    return FakeSession(FakeResponse(ok=status == 200, status_code=status, text=text))


@pytest.mark.parametrize(
    ("date", "session", "problem"),
    [
        pytest.param(
            DECEMBER_2020,
            OfflineSession(),
            "Failed to retrieve HMRC exchange rates for 2020-12. Error: offline: "
            f"{RATES_ADDRESS[DECEMBER_2020]}",
            id="unreachable",
        ),
        # Not 502, 503 or 504: the session retries those and then raises, which
        # is the row above.
        pytest.param(
            JANUARY_2021,
            _reply("Not Found", status=404),
            "HMRC API returned HTTP 404 for 2021-01. Response body: Not Found",
            id="HTTP error",
        ),
        # A maintenance or sign-in page sent with status 200. Its lines are
        # joined, so the reason stays on one.
        pytest.param(
            DECEMBER_2020,
            _reply("<html>\n  <body>\n    Back at 18:00<br>\n  </body>\n</html>\n"),
            "HMRC API response for 2020-12 cannot be read as XML. Response body: "
            "<html> <body> Back at 18:00<br> </body> </html>",
            id="not XML",
        ),
        pytest.param(
            DECEMBER_2020,
            _reply(""),
            "HMRC API response for 2020-12 cannot be read as XML.",
            id="empty reply",
        ),
        pytest.param(
            DECEMBER_2020,
            _reply('<!DOCTYPE r [<!ENTITY a "b">]><r>&a;</r>'),
            "HMRC API response for 2020-12 cannot be read as XML. Response body: "
            '<!DOCTYPE r [<!ENTITY a "b">]><r>&a;</r>',
            id="XML that declares an entity",
        ),
        pytest.param(
            DECEMBER_2020,
            _reply("<exchangeRateMonthList/>"),
            "HMRC API response for 2020-12 has no rates.",
            id="no rows",
        ),
        pytest.param(
            DECEMBER_2020,
            _reply(
                "<exchangeRateMonthList>"
                "<exchangeRate><currencyCode>USD</currencyCode></exchangeRate>"
                "</exchangeRateMonthList>"
            ),
            "HMRC API response for 2020-12 is missing expected currency data.",
            id="row without a rate",
        ),
        pytest.param(
            DECEMBER_2020,
            _reply(
                "<exchangeRateMonthList><exchangeRate>"
                "<currencyCode>usd</currencyCode><rateNew>1.25</rateNew>"
                "</exchangeRate></exchangeRateMonthList>"
            ),
            "HMRC API response for 2020-12 contains invalid currency code: 'usd'.",
            id="code not three capitals",
        ),
        pytest.param(
            DECEMBER_2020,
            _monthly_usd("not-a-rate"),
            "HMRC API response for 2020-12 contains invalid rate: not-a-rate.",
            id="rate not a number",
        ),
        *(
            pytest.param(
                DECEMBER_2020,
                _monthly_usd(rate),
                "HMRC API response for 2020-12 contains a non-positive or "
                f"non-finite rate: {rate}.",
                id=f"rate {rate}",
            )
            for rate in ("0", "-1.25", "NaN", "Infinity")
        ),
    ],
)
def test_a_failed_download_says_what_went_wrong_and_what_to_do(
    date: datetime.date,
    session: FakeSession | OfflineSession,
    problem: str,
    tmp_path: Path,
) -> None:
    """Every way the download fails gives the reason, then the same next step.

    The reason comes with its address on one line; the next line names the
    row for the currency wanted, with <rate> in it so that pasting it
    unchanged is refused, and where to read more. A reply that is not XML
    used to end in a traceback, one with no rows passed for a month without
    the currency, and only an unreachable service said what to do.
    """
    rates_file = tmp_path / "rates.csv"
    converter = CurrencyConverter(exchange_rates_file=rates_file)
    converter.session = session  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(ExternalApiError) as excinfo:
        converter.currency_to_gbp_rate(CurrencyCode("EUR"), date)

    assert str(excinfo.value) == (
        f"{problem} (source: {RATES_ADDRESS[date]})\n"
        f"Try again later, or add the rate to {rates_file}: a CSV file with the "
        f"header 'month,currency,rate' and a row '{date},EUR,<rate>', the rate "
        "being units of EUR per £1. "
        "See https://cgt-calc.uk/extra-data-and-options/#exchange-rates"
    )
    # A failure is not remembered, as rates or as an error: the next lookup
    # asks again.
    with pytest.raises(ExternalApiError):
        converter.currency_to_gbp_rate(CurrencyCode("EUR"), date)
    assert session.urls == [RATES_ADDRESS[date]] * 2
    # A failed download leaves the rates file as it was: here, not yet written.
    assert not rates_file.exists()


def test_cnh_is_treated_as_cny() -> None:
    """Convert offshore yuan using the CNY rate."""
    converter = CurrencyConverter(
        initial_data={DATE: {CurrencyCode("CNY"): Decimal(9)}}
    )

    assert converter.currency_to_gbp_rate(CurrencyCode("CNH"), DATE) == Decimal(9)


def test_a_month_is_downloaded_once_however_many_of_its_dates_need_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """HMRC publishes one file for a month, so a second date in it asks nothing.

    Each month keeps its own rates: the next month's and the same month a year
    on differ here. The last date is served from memory after both of those
    were downloaded, and still gets June 2019's rate and its own row in the
    rates file. As on a first run, the folder for that file is not there yet.
    """
    rates_file = tmp_path / "out" / "rates.csv"
    converter = CurrencyConverter(exchange_rates_file=rates_file)
    session = MonthlyFiles({"0619": "1.2611", "0719": "1.2532", "0620": "1.2331"})
    converter.session = session  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with caplog.at_level(logging.INFO):
        for date, rate in (
            (datetime.date(2019, 6, 3), "1.2611"),
            (datetime.date(2019, 7, 1), "1.2532"),
            (datetime.date(2020, 6, 1), "1.2331"),
            (datetime.date(2019, 6, 14), "1.2611"),
        ):
            assert converter.currency_to_gbp_rate(USD, date) == Decimal(rate)

    legacy = "https://www.hmrc.gov.uk/softwaredevelopers/rates/"
    assert session.urls == [
        f"{legacy}exrates-monthly-0619.xml",
        f"{legacy}exrates-monthly-0719.xml",
        f"{legacy}exrates-monthly-0620.xml",
    ]
    assert [record.getMessage() for record in caplog.records] == [
        "Fetching HMRC exchange rates for 2019-06...",
        "Fetching HMRC exchange rates for 2019-07...",
        "Fetching HMRC exchange rates for 2020-06...",
    ]
    assert rates_file.read_text(encoding="utf8") == (
        "month,currency,rate\n"
        "2019-06-03,USD,1.2611\n"
        "2019-06-14,USD,1.2611\n"
        "2019-07-01,USD,1.2532\n"
        "2020-06-01,USD,1.2331\n"
    )


def test_a_row_typed_for_one_currency_leaves_the_others_to_be_downloaded(
    tmp_path: Path,
) -> None:
    """A date on file with one currency still has its month asked for another.

    The row was typed in while HMRC could not be reached. It is kept and still
    used, also after the month is stored under the date a second time, from
    memory; the download fills in the date's other currencies beside it. A
    currency the download lacks as well is refused, naming the file its row
    goes in. The typed rate stays on its own date: another date in the month
    gets HMRC's.
    """
    date = datetime.date(2019, 6, 14)
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate\n2019-06-14,USD,1.2682\n", encoding="utf8"
    )
    converter = CurrencyConverter(exchange_rates_file=rates_file)
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate>"
        "<currencyCode>USD</currencyCode><rateNew>1.2611</rateNew>"
        "</exchangeRate>"
        "<exchangeRate>"
        "<currencyCode>EUR</currencyCode><rateNew>1.1307</rateNew>"
        "</exchangeRate>"
        "</exchangeRateMonthList>"
    )
    session = FakeSession(FakeResponse(ok=True, text=xml))
    converter.session = session  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    assert converter.currency_to_gbp_rate(CurrencyCode("EUR"), date) == Decimal(
        "1.1307"
    )
    # Asking for a currency the month lacks stores the month under the date
    # again, this time from memory.
    with pytest.raises(HmrcRateMissingError, match=r"Add it to .*rates\.csv: "):
        converter.currency_to_gbp_rate(CurrencyCode("XAU"), date)
    assert converter.currency_to_gbp_rate(USD, date) == Decimal("1.2682")
    assert rates_file.read_text(encoding="utf8") == (
        "month,currency,rate\n2019-06-14,EUR,1.1307\n2019-06-14,USD,1.2682\n"
    )
    assert converter.currency_to_gbp_rate(USD, datetime.date(2019, 6, 20)) == Decimal(
        "1.2611"
    )
    assert len(session.urls) == 1


TYPED_ROW = b"month,currency,rate\r\n2019-06-14,USD,1.2682\r\n"


class DuringTheSave(Decimal):
    """A rate that runs `act` when it is turned into text for its row.

    The csv writer does that as it writes the row, so a save has its temporary
    file open at that moment and has handed it the rows before this one.
    """

    act: Callable[[], None]

    @override
    def __str__(self) -> str:
        """Run `act`, then give the rate's text."""
        self.act()
        return super().__str__()


NOT_PART_WAY = (
    "only the rates file was in its folder when the rate became text: either "
    "the save no longer writes its temporary file there, or the rate became "
    "text before the save began, which this test needs to happen as the rate's "
    "row is written"
)


def test_a_run_stopped_as_it_saves_leaves_the_rates_file_as_it_was(
    tmp_path: Path,
) -> None:
    """Ctrl-C during a save loses no row that was on file, and leaves no other file.

    The rows are saved in date order, so the save here is stopped at the row
    for June 2020, after the typed row and July 2019's.
    """
    rates_file = tmp_path / "rates.csv"
    rates_file.write_bytes(TYPED_ROW)
    files_when_stopped: list[int] = []

    def stop() -> None:
        files_when_stopped.append(len(list(tmp_path.iterdir())))
        raise KeyboardInterrupt

    rate = DuringTheSave("1.2331")
    rate.act = stop
    converter = CurrencyConverter(
        exchange_rates_file=rates_file,
        initial_data={datetime.date(2020, 6, 1): {USD: rate}},
    )
    converter.session = _monthly_usd("1.2532")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(KeyboardInterrupt):
        converter.currency_to_gbp_rate(USD, datetime.date(2019, 7, 1))

    assert rates_file.read_bytes() == TYPED_ROW
    assert list(tmp_path.iterdir()) == [rates_file]
    assert files_when_stopped == [2], NOT_PART_WAY


def test_two_runs_that_save_at_once_both_finish(tmp_path: Path) -> None:
    """A run that saves while another is part way through a save stops neither.

    The second run saves as the first writes its last row. The file is left
    whole, with the rows of the run that finished last.
    """
    rates_file = tmp_path / "rates.csv"
    rates_file.write_bytes(TYPED_ROW)
    second = CurrencyConverter(exchange_rates_file=rates_file)
    second.session = _monthly_usd("1.2331")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    files_when_the_second_saved: list[int] = []

    def second_run_saves() -> None:
        files_when_the_second_saved.append(len(list(tmp_path.iterdir())))
        second.currency_to_gbp_rate(USD, datetime.date(2020, 6, 1))

    rate = DuringTheSave("1.4166")
    rate.act = second_run_saves
    first = CurrencyConverter(
        exchange_rates_file=rates_file,
        initial_data={datetime.date(2021, 6, 1): {USD: rate}},
    )
    first.session = _monthly_usd("1.2532")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    first.currency_to_gbp_rate(USD, datetime.date(2019, 7, 1))

    assert rates_file.read_bytes() == (
        TYPED_ROW + b"2019-07-01,USD,1.2532\r\n2021-06-01,USD,1.4166\r\n"
    )
    assert list(tmp_path.iterdir()) == [rates_file]
    assert files_when_the_second_saved == [2], NOT_PART_WAY


def test_a_rates_file_that_cannot_be_written_is_reported_and_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only rates file stops the run with what to do, and stays as it is.

    The message names the file as it was given, here relative to the folder
    the run is in.
    """
    monkeypatch.chdir(tmp_path)
    rates_file = Path("rates.csv")
    rates_file.write_bytes(TYPED_ROW)
    rates_file.chmod(0o444)
    if os.access(rates_file, os.W_OK):
        pytest.skip("this user may write to a read-only file")
    converter = CurrencyConverter(exchange_rates_file=rates_file)
    converter.session = _monthly_usd("1.2532")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(CgtError) as raised:
        converter.currency_to_gbp_rate(USD, datetime.date(2019, 7, 1))

    assert str(raised.value) == (
        "Cannot save exchange rates to rates.csv: Permission denied. Close the file "
        "if another program has it open, and check that it and its folder can be "
        "written to. To run without saving the rates, pass --exchange-rates-file= "
        "with nothing after the = sign."
    )
    assert rates_file.read_bytes() == TYPED_ROW
    assert list(tmp_path.iterdir()) == [tmp_path / "rates.csv"]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows needs a privilege to make a link and has no such permissions",
)
def test_a_save_keeps_a_linked_rates_file_and_its_permissions(tmp_path: Path) -> None:
    """A rates file that is a symbolic link stays one, and the file keeps its mode."""
    kept = tmp_path / "kept.csv"
    kept.write_bytes(TYPED_ROW)
    # No new file gets permission to execute, whatever the user's settings.
    kept.chmod(0o700)
    rates_file = tmp_path / "rates.csv"
    rates_file.symlink_to(kept)
    converter = CurrencyConverter(exchange_rates_file=rates_file)
    converter.session = _monthly_usd("1.2532")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    assert converter.currency_to_gbp_rate(USD, datetime.date(2019, 7, 1)) == Decimal(
        "1.2532"
    )

    assert rates_file.is_symlink()
    assert kept.read_bytes() == TYPED_ROW + b"2019-07-01,USD,1.2532\r\n"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o700


def test_test_converter_records_new_rates(tmp_path: Path) -> None:
    """Record rates fetched during tests in the rates file."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n", encoding="utf8")
    converter = RecordingCurrencyConverter(exchange_rates_file=rates_file)

    def fake_query(date: datetime.date, currency: CurrencyCode) -> None:
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


def test_a_date_before_february_2015_that_does_not_ship_is_not_asked_of_hmrc(
    tmp_path: Path,
) -> None:
    """HMRC serves no file for it, so the answer is the row to add, not a request.

    March 2002 is before the first month that ships.
    """
    converter = CurrencyConverter(exchange_rates_file=tmp_path / "rates.csv")
    converter.session = OfflineSession()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(HmrcRateMissingError, match=r"a row '2002-03-28,USD,<rate>'"):
        converter.currency_to_gbp_rate(USD, datetime.date(2002, 3, 28))


@pytest.mark.parametrize(
    ("currency", "date", "rate"),
    [
        (USD, datetime.date(2016, 1, 26), Decimal("1.5003")),
        (USD, datetime.date(2016, 1, 27), Decimal("1.4144")),
        # The Swiss franc changed twice in February 2015, on the 4th and 11th.
        (CurrencyCode("CHF"), datetime.date(2015, 2, 10), Decimal("1.3692")),
        (CurrencyCode("CHF"), datetime.date(2015, 2, 11), Decimal("1.3995")),
        # Offshore yuan is priced as the yuan, whose rate changed that day.
        (CurrencyCode("CNH"), datetime.date(2015, 5, 27), Decimal("9.8007")),
    ],
)
def test_a_rate_hmrc_changed_during_the_month_applies_from_its_date(
    currency: CurrencyCode, date: datetime.date, rate: Decimal
) -> None:
    """HMRC's monthly file shows one rate; the change it made later does not.

    January 2016 opened at 1.5003 dollars to the pound and HMRC changed it to
    1.4144 from the 27th. The download gives 1.5003 for the whole month, so it
    is not asked: it would also leave that rate on file.
    """
    converter = CurrencyConverter()
    converter.session = _monthly_usd("1.5003")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    assert converter.currency_to_gbp_rate(currency, date) == rate
    assert converter.cache == {}


@pytest.mark.parametrize(
    ("currency", "date", "rate"),
    [
        # Each checked by hand against HMRC's page for the month, one for each
        # form HMRC published the months before February 2015 in.
        pytest.param(
            USD, datetime.date(2002, 4, 15), Decimal("1.4255"), id="first month"
        ),
        pytest.param(USD, datetime.date(2009, 6, 15), Decimal("1.5649"), id="web page"),
        pytest.param(USD, datetime.date(2005, 6, 15), Decimal("1.8344"), id="PDF"),
        pytest.param(
            CurrencyCode("EUR"),
            datetime.date(2006, 6, 15),
            Decimal("1.4746"),
            id="Word",
        ),
        pytest.param(USD, datetime.date(2015, 1, 15), Decimal("1.5562"), id="XML"),
        # November 2008 opened at 1.6336 and HMRC changed it from the 19th.
        pytest.param(
            USD, datetime.date(2008, 11, 18), Decimal("1.6336"), id="before a change"
        ),
        pytest.param(
            USD, datetime.date(2008, 11, 19), Decimal("1.5047"), id="from a change"
        ),
    ],
)
def test_rates_from_april_2002_come_with_cgt_calc(
    currency: CurrencyCode, date: datetime.date, rate: Decimal
) -> None:
    """HMRC's download has no file for these months, so they are not asked for."""
    converter = CurrencyConverter()
    converter.session = OfflineSession()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    assert converter.currency_to_gbp_rate(currency, date) == rate


@pytest.mark.parametrize("year", SHIPPED_RATES_YEARS)
def test_every_year_of_shipped_rates_loads(year: int) -> None:
    """A year's file is read whole on first use, so one bad row stops the year.

    A correction is a hand edit to one of these files; a duplicate or mistyped
    row would otherwise reach a user before any test opened that year.
    """
    converter = CurrencyConverter()
    converter.session = OfflineSession()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    assert converter.currency_to_gbp_rate(USD, datetime.date(year, 4, 15))


def test_a_currency_the_shipped_rates_lack_comes_from_the_rates_given() -> None:
    """HMRC lists no XAU in January 2016, so the rate supplied is the one used."""
    date = datetime.date(2016, 1, 27)
    gold = CurrencyCode("XAU")
    converter = CurrencyConverter(initial_data={date: {gold: Decimal("0.001")}})

    assert converter.currency_to_gbp_rate(gold, date) == Decimal("0.001")


def test_a_month_that_ships_is_never_downloaded() -> None:
    """The download has no currency the shipped month lacks, only stale rates.

    Asking for it would put the rates the month opened with on file, and the
    next run would overrule the rows this one wrote.
    """
    converter = CurrencyConverter()
    converter.session = OfflineSession()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(HmrcRateMissingError):
        converter.currency_to_gbp_rate(CurrencyCode("XAU"), datetime.date(2016, 1, 27))


def test_a_different_rate_on_file_is_overruled_and_reported_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The rates file is also the cache, and the cache never saw HMRC's change.

    A row that agrees with HMRC says nothing. One that differs is not used,
    and is named so it can be removed: once for each row, however many times
    it is read.
    """
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate\n"
        "2016-01-26,USD,1.5003\n"
        "2016-01-28,USD,1.5003\n"
        "2016-01-29,USD,1.5003\n"
        "2016-01-29,EUR,1.371\n",
        encoding="utf8",
    )
    converter = CurrencyConverter(exchange_rates_file=rates_file)
    eur = CurrencyCode("EUR")

    def rate(currency: CurrencyCode, day: int) -> Decimal:
        return converter.currency_to_gbp_rate(currency, datetime.date(2016, 1, day))

    with caplog.at_level(logging.WARNING):
        assert rate(USD, 26) == Decimal("1.5003")
        assert rate(USD, 28) == rate(USD, 28) == Decimal("1.4144")
        assert rate(USD, 29) == Decimal("1.4144")
        assert rate(eur, 29) == Decimal("1.2978")

    def overruled(row: str, hmrc: str) -> str:
        return (
            f"{rates_file} gives {row}, but HMRC's rate for that date is {hmrc}, "
            "which is used instead. Remove that row to stop this warning."
        )

    assert [record.getMessage() for record in caplog.records] == [
        overruled("1.5003 USD per £1 for 2016-01-28", "1.4144"),
        overruled("1.5003 USD per £1 for 2016-01-29", "1.4144"),
        overruled("1.371 EUR per £1 for 2016-01-29", "1.2978"),
    ]


def test_a_row_typed_for_a_date_before_february_2015_is_used_and_reported_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing could be downloaded for these dates, so a row on file was typed in.

    It may match a return already made, so it is used. Where it differs from
    HMRC's rate it is named with that rate, once however often it is read: the
    row for the 20th was typed from November 2008's opening rate and misses
    the change HMRC made on the 19th. From 1 February 2015 a row can be a
    download from before such a change, and HMRC's rate is used.
    """
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate\n"
        "2008-11-18,USD,1.6336\n"
        "2008-11-20,USD,1.6336\n"
        "2015-01-31,USD,1.5\n"
        "2015-02-01,USD,1.5\n",
        encoding="utf8",
    )
    converter = CurrencyConverter(exchange_rates_file=rates_file)

    def rate(year: int, month: int, day: int) -> Decimal:
        return converter.currency_to_gbp_rate(USD, datetime.date(year, month, day))

    with caplog.at_level(logging.WARNING):
        assert rate(2008, 11, 18) == Decimal("1.6336")
        assert rate(2008, 11, 20) == rate(2008, 11, 20) == Decimal("1.6336")
        assert rate(2015, 1, 31) == Decimal("1.5")
        assert rate(2015, 2, 1) == Decimal("1.512")

    def used(row: str, hmrc: str) -> str:
        return (
            f"{rates_file} gives {row}, and it is used instead of HMRC's rate for "
            f"that date, {hmrc}. Remove that row to use HMRC's rate."
        )

    assert [record.getMessage() for record in caplog.records] == [
        used("1.6336 USD per £1 for 2008-11-20", "1.5047"),
        used("1.5 USD per £1 for 2015-01-31", "1.5562"),
        (
            f"{rates_file} gives 1.5 USD per £1 for 2015-02-01, but HMRC's rate "
            "for that date is 1.512, which is used instead. Remove that row to "
            "stop this warning."
        ),
    ]


def test_test_converter_does_not_record_a_shipped_rate(tmp_path: Path) -> None:
    """A rate that ships needs no fixture row, so the tracked file is left alone."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n", encoding="utf8")
    converter = RecordingCurrencyConverter(exchange_rates_file=rates_file)

    converter.currency_to_gbp_rate(USD, datetime.date(2016, 1, 27))

    assert rates_file.read_text(encoding="utf-8") == "month,currency,rate\n"
