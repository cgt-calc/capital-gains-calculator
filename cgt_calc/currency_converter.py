"""Convert currencies to GBP using rate history."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import datetime
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib import resources
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from pyrate_limiter import limiter_factory
from pyrate_limiter.abstracts.rate import Duration
from pyrate_limiter.extras.requests_limiter import RateLimitedRequestsSession
from requests.adapters import HTTPAdapter, Retry

from .const import (
    CGT_MODE,
    SHIPPED_RATES_FOLDER,
    SHIPPED_RATES_YEARS,
    UK_CURRENCY,
    RuntimeMode,
)
from .dates import is_date
from .exceptions import (
    CalculationError,
    ExternalApiError,
    HmrcRateMissingError,
    ParsingError,
    UnexpectedHeaderError,
    rates_file_row,
    reading_as,
    saving,
    without_file,
)
from .model import CurrencyCode, ForeignCurrencyAmount
from .resources import RESOURCES_PACKAGE
from .util import exclusive_lock, is_blank_row, open_with_parents, save_csv

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .model import BrokerTransaction

LOGGER = logging.getLogger(__name__)

EXCHANGE_RATES_HEADER: Final = ["month", "currency", "rate"]
NEW_ENDPOINT_FROM_YEAR: Final = 2021
# The first month HMRC's legacy endpoint has a file for at the address used.
# Nothing is asked for an earlier date, and a row in the rates file for one was
# typed in: no download wrote it.
FIRST_DOWNLOADED_RATES_MONTH: Final = datetime.date(2015, 2, 1)

type _StartingRates = dict[datetime.date, Decimal]


class CurrencyConverter:
    """Converter which holds rate history."""

    def __init__(
        self,
        exchange_rates_file: Path | None = None,
        initial_data: dict[datetime.date, dict[CurrencyCode, Decimal]] | None = None,
    ):
        """Load data from exchange_rates_file and optionally from initial_data."""
        self.exchange_rates_file = exchange_rates_file
        read_data = self._read_exchange_rates_file(exchange_rates_file)
        self.cache = {
            **read_data,
            **(initial_data or {}),
        }
        self._reported: set[tuple[datetime.date, CurrencyCode]] = set()
        # The rates of each month downloaded in this run, by its first day.
        self._downloaded: dict[datetime.date, dict[CurrencyCode, Decimal]] = {}

        # Limit borrowed from the Companies House API guidance:
        # https://developer-specs.company-information.service.gov.uk/guides/rateLimiting
        limiter = limiter_factory.create_inmemory_limiter(
            rate_per_duration=600, duration=Duration.MINUTE * 5
        )
        self.session = RateLimitedRequestsSession(limiter)
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    @staticmethod
    def create(exchange_rates_file: Path | None = None) -> CurrencyConverter:
        """Create the appropriate CurrencyConverter for the current runtime mode."""
        match CGT_MODE:
            case RuntimeMode.PROD:
                return CurrencyConverter(exchange_rates_file)
            case RuntimeMode.TEST_STRICT:
                return StrictTestCurrencyConverter(exchange_rates_file)
            case RuntimeMode.TEST:
                return TestCurrencyConverter(exchange_rates_file)
        raise NotImplementedError(
            f"Missing CurrencyConverter implementation for {CGT_MODE}"
        )

    @staticmethod
    def _read_exchange_rates_data(
        exchange_rates_file: Path, fin: Iterable[str]
    ) -> defaultdict[datetime.date, dict[CurrencyCode, Decimal]]:
        cache: defaultdict[datetime.date, dict[CurrencyCode, Decimal]] = defaultdict(
            dict
        )
        # Keep physical line numbers so errors point at the right line even
        # when comment lines precede the data.
        kept = [
            (number, line)
            for number, line in enumerate(fin, start=1)
            if not line.lstrip().startswith("#")
        ]
        line_numbers = [number for number, _ in kept]
        csv_reader = csv.DictReader(line for _, line in kept)
        header = csv_reader.fieldnames
        if header is None:
            # File is empty.
            return cache
        # Checked before any row: a file of one rate and no header has no row
        # left to check, and would be read as empty.
        if sorted(header) != sorted(EXCHANGE_RATES_HEADER):
            raise UnexpectedHeaderError(
                header,
                EXCHANGE_RATES_HEADER,
                exchange_rates_file,
                row_index=line_numbers[0],
            )
        for line in csv_reader:
            row_number = line_numbers[csv_reader.line_num - 1]
            # A value beyond the last column is filed under the key None.
            extra = line.pop(None, None)
            if extra is not None:
                raise ParsingError(
                    exchange_rates_file,
                    f"Too many values in exchange rate file at line {row_number}: "
                    f"expected {len(EXCHANGE_RATES_HEADER)}, found "
                    f"{len(EXCHANGE_RATES_HEADER) + len(extra)}",
                )

            # Trim values so that whitespace-only cells count as empty.
            normalized_values = {
                field: (line[field].strip() if line[field] is not None else "")
                for field in EXCHANGE_RATES_HEADER
            }

            # Skip harmless blank lines left by editors or tooling.
            if is_blank_row(normalized_values.values()):
                continue

            # Missing values mean we cannot trust the rate entry.
            missing_fields = [
                field for field, value in normalized_values.items() if not value
            ]
            if missing_fields:
                raise ParsingError(
                    exchange_rates_file,
                    "Missing data in exchange rate file at line "
                    f"{row_number}: {', '.join(sorted(missing_fields))}",
                )

            month = normalized_values["month"]
            currency_raw = normalized_values["currency"]
            rate_value = normalized_values["rate"]

            try:
                date = datetime.date.fromisoformat(month)
            except ValueError as err:
                raise ParsingError(
                    exchange_rates_file,
                    f"Invalid date '{month}' at line {row_number}",
                ) from err

            currency = CurrencyCode.parse(currency_raw)
            if currency is None:
                raise ParsingError(
                    exchange_rates_file,
                    f"Invalid currency code '{currency_raw}' at line {row_number}",
                )

            try:
                rate = Decimal(rate_value)
            except (InvalidOperation, ValueError) as err:
                raise ParsingError(
                    exchange_rates_file,
                    f"Invalid rate '{rate_value}' at line {row_number}",
                ) from err
            if not rate.is_finite() or rate <= 0:
                raise ParsingError(
                    exchange_rates_file,
                    f"Exchange rate must be finite and positive at line "
                    f"{row_number}: {rate_value!r}",
                )

            # Duplicates suggest conflicting data, so fail fast.
            if currency in cache[date]:
                raise ParsingError(
                    exchange_rates_file,
                    "Duplicate currency entry for "
                    f"{currency} on {month} at line {row_number}",
                )

            cache[date][currency] = rate
        return cache

    @staticmethod
    def _read_exchange_rates_file(
        exchange_rates_file: Path | None,
    ) -> defaultdict[datetime.date, dict[CurrencyCode, Decimal]]:
        if not exchange_rates_file or not exchange_rates_file.is_file():
            return defaultdict(dict)
        with (
            reading_as("utf-8-sig", exchange_rates_file),
            exchange_rates_file.open(encoding="utf-8-sig") as fin,
        ):
            return CurrencyConverter._read_exchange_rates_data(exchange_rates_file, fin)

    @staticmethod
    @cache
    def _shipped_rates(
        year: int,
    ) -> dict[datetime.date, dict[CurrencyCode, _StartingRates]]:
        """Read the rates that ship with cgt-calc for `year`, if any do.

        They are kept by month, named by its first day, then by currency, then by
        the day each rate starts.
        """
        if year not in SHIPPED_RATES_YEARS:
            return {}
        name = f"{year}.csv"
        with (
            resources.files(RESOURCES_PACKAGE)
            .joinpath(SHIPPED_RATES_FOLDER)
            .joinpath(name)
            .open(encoding="utf8") as fin
        ):
            by_date = CurrencyConverter._read_exchange_rates_data(
                Path("resources") / SHIPPED_RATES_FOLDER / name, fin
            )
        rates: dict[datetime.date, dict[CurrencyCode, _StartingRates]] = {}
        for start, by_currency in by_date.items():
            month = rates.setdefault(start.replace(day=1), {})
            for currency, rate in by_currency.items():
                month.setdefault(currency, {})[start] = rate
        return rates

    @staticmethod
    def _shipped_rate(currency: CurrencyCode, date: datetime.date) -> Decimal | None:
        """Return the shipped rate in force on `date`, if the table has one.

        That is the latest of the month's rates starting on or before the date.
        """
        starts = (
            CurrencyConverter._shipped_rates(date.year)
            .get(date.replace(day=1), {})
            .get(currency, {})
        )
        in_force = [start for start in starts if start <= date]
        return starts[max(in_force)] if in_force else None

    @staticmethod
    def _write_exchange_rates_file(
        exchange_rates_file: Path | None,
        data: dict[datetime.date, dict[CurrencyCode, Decimal]],
    ) -> None:
        if not exchange_rates_file:
            return
        data_rows = sorted(
            [month, symbol, rate]
            for month, rates in data.items()
            for symbol, rate in rates.items()
        )
        with saving(
            "exchange rates",
            exchange_rates_file,
            without_file("--exchange-rates-file"),
        ):
            save_csv(exchange_rates_file, [EXCHANGE_RATES_HEADER, *data_rows])

    def _query_hmrc_api(self, date: datetime.date, currency: CurrencyCode) -> None:
        """Store the month's rates under `date`; `currency` is the one wanted.

        HMRC publishes one file for a month, so it is downloaded once in a run
        however many of the month's dates need it.
        """
        first = date.replace(day=1)
        if first not in self._downloaded:
            self._downloaded[first] = self._download_month(date, currency)
        # Rows already held for the date stay in use: one may have been typed in.
        self.cache[date] = {**self._downloaded[first], **self.cache.get(date, {})}
        self._write_exchange_rates_file(self.exchange_rates_file, self.cache)

    def _download_month(
        self, date: datetime.date, currency: CurrencyCode
    ) -> dict[CurrencyCode, Decimal]:
        """Download the month's rates for `date`; `currency` is the one wanted."""
        month = f"{date:%Y-%m}"
        LOGGER.info("Fetching HMRC exchange rates for %s...", month)
        # Pre 2021 we need to use the old HMRC endpoint
        if date.year < NEW_ENDPOINT_FROM_YEAR:
            url = (
                "https://www.hmrc.gov.uk/softwaredevelopers/rates/"
                f"exrates-monthly-{date:%m%y}.xml"
            )
        else:
            url = (
                "https://www.trade-tariff.service.gov.uk/uk/api/"
                f"exchange_rates/files/monthly_xml_{month}.xml"
            )
        advice = "Try again later, or add the rate to " + rates_file_row(
            currency, date, self.exchange_rates_file
        )

        def failed(problem: str) -> ExternalApiError:
            """Say what went wrong, then what to do, the same way for every failure."""
            return ExternalApiError(url, problem, advice)

        try:
            response = self.session.get(url, timeout=10)
        except Exception as err:
            raise failed(
                f"Failed to retrieve HMRC exchange rates for {month}. Error: {err}"
            ) from err

        def reply_start() -> str:
            """Give the start of what the service sent, to follow a line about it."""
            # On one line, however many the reply has: a page of markup would
            # otherwise push the address and the advice out of sight.
            body = " ".join(response.text.split())
            if not body:
                return ""
            limit = 200
            return f" Response body: {body[:limit]}{'...' if len(body) > limit else ''}"

        if not response.ok:
            raise failed(
                f"HMRC API returned HTTP {response.status_code} for {month}."
                f"{reply_start()}"
            )

        try:
            tree = ET.fromstring(response.text)
        except (ET.ParseError, DefusedXmlException) as err:
            # A maintenance or sign-in page sent with status 200, for example, or
            # XML that declares an entity, which defusedxml refuses to read.
            raise failed(
                f"HMRC API response for {month} cannot be read as XML.{reply_start()}"
            ) from err
        rates: dict[CurrencyCode, Decimal] = {}
        for row in tree:
            currency_code_elem = row.find("currencyCode")
            rate_new_elem = row.find("rateNew")
            if (
                currency_code_elem is None
                or currency_code_elem.text is None
                or rate_new_elem is None
                or rate_new_elem.text is None
            ):
                raise failed(
                    f"HMRC API response for {month} is missing expected currency data."
                )
            listed = CurrencyCode.parse(currency_code_elem.text)
            if listed is None:
                raise failed(
                    f"HMRC API response for {month} contains invalid currency code: "
                    f"{currency_code_elem.text!r}."
                )
            try:
                rate = Decimal(rate_new_elem.text)
            except (InvalidOperation, ValueError) as err:
                raise failed(
                    f"HMRC API response for {month} contains invalid rate: "
                    f"{rate_new_elem.text}."
                ) from err
            if not rate.is_finite() or rate <= 0:
                raise failed(
                    f"HMRC API response for {month} contains a non-positive "
                    f"or non-finite rate: {rate_new_elem.text}."
                )
            rates[listed] = rate
        if not rates:
            raise failed(f"HMRC API response for {month} has no rates.")
        return rates

    def currency_to_gbp_rate(
        self, currency: CurrencyCode, date: datetime.date
    ) -> Decimal:
        """Get the number of currency units per GBP at the given date."""
        assert is_date(date)
        # offshore (Hong Kong) Chinese Yuan handling
        if currency == "CNH":
            currency = CurrencyCode("CNY")
        shipped = self._shipped_rate(currency, date)
        if shipped is None:
            return self._recorded_rate(currency, date)
        on_file = self.cache.get(date, {}).get(currency)
        if on_file is None or on_file == shipped:
            return shipped
        # A row on file that differs from the shipped rate. Before February
        # 2015 it was typed in, perhaps to match a return already made, so it
        # is used. From then on the file is also the cache of what was
        # downloaded, and the download never showed a change HMRC made during
        # a month: the row is stale, and HMRC's rate is used. Either way the
        # row is named once, with HMRC's rate, so it can be checked or removed.
        typed = date < FIRST_DOWNLOADED_RATES_MONTH
        if (date, currency) not in self._reported:
            self._reported.add((date, currency))
            row = (self.exchange_rates_file, on_file, currency, date, shipped)
            if typed:
                LOGGER.warning(
                    "%s gives %s %s per £1 for %s, and it is used instead of "
                    "HMRC's rate for that date, %s. Remove that row to use HMRC's "
                    "rate.",
                    *row,
                )
            else:
                LOGGER.warning(
                    "%s gives %s %s per £1 for %s, but HMRC's rate for that date "
                    "is %s, which is used instead. Remove that row to stop this "
                    "warning.",
                    *row,
                )
        return on_file if typed else shipped

    def _recorded_rate(self, currency: CurrencyCode, date: datetime.date) -> Decimal:
        """Get a rate from the rates file, downloading the month if it lacks one."""
        # The test is for the currency, not the date: a row typed in for one
        # currency leaves the date's other currencies to be downloaded.
        # At the address used, HMRC serves no file for a month before February
        # 2015, and a month that ships is never downloaded: the download could
        # add no currency, and would only put the rates the month opened with
        # on file, to be overruled the next time they are read.
        month = date.replace(day=1)
        if (
            currency not in self.cache.get(date, {})
            and date >= FIRST_DOWNLOADED_RATES_MONTH
            and month not in self._shipped_rates(date.year)
        ):
            self._query_hmrc_api(date, currency)
        if currency not in self.cache.get(date, {}):
            raise HmrcRateMissingError(currency, date, self.exchange_rates_file)

        return self.cache[date][currency]

    def to_gbp(
        self, amount: Decimal, currency: CurrencyCode, date: datetime.date
    ) -> Decimal:
        """Convert amount from given currency to GBP."""
        if currency == "GBP":
            return amount
        return amount / self.currency_to_gbp_rate(currency, date)

    def to_gbp_for(self, amount: Decimal, transaction: BrokerTransaction) -> Decimal:
        """Convert amount from transaction currency to GBP."""
        return self.to_gbp(amount, transaction.currency, transaction.date)

    def combine_amounts(
        self,
        first: ForeignCurrencyAmount,
        second: ForeignCurrencyAmount,
        date: datetime.date,
        *,
        autoconvert: bool,
    ) -> ForeignCurrencyAmount:
        """Add two amounts that belong to the same report row.

        Adding them refuses a mismatch in currency, because a figure the
        report states in one currency cannot be the sum of two currencies.
        With `autoconvert`, a GBP row is converted at `date` into the one
        foreign currency present and the sum is stated in that, so that the
        currency the payment arrived in survives to name its source country.
        Two foreign currencies are still refused: where no ISIN names the
        source country, picking one of them decides the double taxation
        treaty on nothing better than which row was larger or came first.
        """
        if (
            autoconvert
            and first.currency is not None
            and second.currency is not None
            and first.currency != second.currency
        ):
            if UK_CURRENCY not in {first.currency, second.currency}:
                raise CalculationError(
                    "Cannot combine amounts in different currencies: "
                    f"{first.currency} and {second.currency}. "
                    "--autoconvert-currency combines GBP with one foreign "
                    "currency; it does not convert between foreign "
                    "currencies. Report these rows in one currency."
                )
            foreign, sterling = (
                (second, first) if first.currency == UK_CURRENCY else (first, second)
            )
            assert foreign.currency is not None
            # ponytail: only GBP plus one foreign currency; retain all source
            # currencies before widening this rule.
            return ForeignCurrencyAmount(
                foreign.amount
                + sterling.amount * self.currency_to_gbp_rate(foreign.currency, date),
                foreign.currency,
            )
        return first + second


class TestCurrencyConverter(CurrencyConverter):
    """Variant of CurrencyConverter that appends each used rate to the input exchange rate file.

    Created when RuntimeMode is TEST; this is meant to be used to populate test fixture data
    when adding new tests.
    """

    def __init__(self, exchange_rates_file: Path | None = None):
        """Load data from exchange_rates_file.

        Store the initial view of exchange rates to compare against later on.
        """
        super().__init__(exchange_rates_file)
        self._test_file_cache = deepcopy(self.cache)

    @override
    def _recorded_rate(self, currency: CurrencyCode, date: datetime.date) -> Decimal:
        """Get a rate from the rates file, downloading the month if it lacks one.

        When the value is missing from the view of the test_file_cache, append it
        to the exchange rate CSV file.
        This allows us to record the rates that are being used in tests.
        """
        result = super()._recorded_rate(currency, date)
        if date not in self._test_file_cache:
            self._test_file_cache[date] = {}
        if currency not in self._test_file_cache[date] and self.exchange_rates_file:
            self._test_file_cache[date][currency] = result
            self._append_exchange_rates_file(
                self.exchange_rates_file,
                date,
                currency,
                result,
            )
        return result

    @staticmethod
    def _append_exchange_rates_file(
        exchange_rates_file: Path,
        date: datetime.date,
        currency: CurrencyCode,
        value: Decimal,
    ) -> None:
        with (
            open_with_parents(exchange_rates_file, clear_content=False) as fout,
            exclusive_lock(fout),
        ):
            fout.seek(0)
            data = TestCurrencyConverter._read_exchange_rates_data(
                exchange_rates_file, fout
            )
            if date not in data or currency not in data[date]:
                writer = csv.writer(fout)
                writer.writerow([date, currency, str(value)])

    @staticmethod
    @override
    def _write_exchange_rates_file(
        exchange_rates_file: Path | None,
        data: dict[datetime.date, dict[CurrencyCode, Decimal]],
    ) -> None:
        pass


class StrictTestCurrencyConverter(CurrencyConverter):
    """Sandboxed variant of CurrencyConverter that is used to run tests in CI."""

    @override
    def _query_hmrc_api(self, date: datetime.date, currency: CurrencyCode) -> None:
        raise RuntimeError(
            f"HMRC values missing for {date:%Y-%m}! "
            "Run `pytest` (once) to populate them from HMRC data"
        )

    @staticmethod
    @override
    def _write_exchange_rates_file(
        exchange_rates_file: Path | None,
        data: dict[datetime.date, dict[CurrencyCode, Decimal]],
    ) -> None:
        pass
