"""Convert currencies to GBP using rate history."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import datetime
from decimal import Decimal, InvalidOperation
import logging
from typing import TYPE_CHECKING, Final, TextIO, override

from defusedxml import ElementTree as ET
from pyrate_limiter import limiter_factory
from pyrate_limiter.abstracts.rate import Duration
from pyrate_limiter.extras.requests_limiter import RateLimitedRequestsSession
from requests.adapters import HTTPAdapter, Retry

from .const import CGT_MODE, UK_CURRENCY, RuntimeMode
from .dates import is_date
from .exceptions import (
    CalculationError,
    ExchangeRateMissingError,
    ExternalApiError,
    ParsingError,
)
from .model import CurrencyCode, ForeignCurrencyAmount
from .util import exclusive_lock, open_with_parents

if TYPE_CHECKING:
    from pathlib import Path

    from .model import BrokerTransaction

LOGGER = logging.getLogger(__name__)

EXCHANGE_RATES_HEADER: Final = ["month", "currency", "rate"]
NEW_ENDPOINT_FROM_YEAR: Final = 2021


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
        exchange_rates_file: Path, fin: TextIO
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
        if csv_reader.fieldnames is None:
            # File is empty.
            return cache
        for line in csv_reader:
            row_number = line_numbers[csv_reader.line_num - 1]
            # Guard against schema drift before touching row contents.
            if sorted(EXCHANGE_RATES_HEADER) != sorted(line.keys()):
                raise ParsingError(
                    exchange_rates_file,
                    "Unexpected columns in exchange rate file: "
                    f"found {sorted(line.keys())}, expected {EXCHANGE_RATES_HEADER}",
                )

            # Trim values so that whitespace-only cells count as empty.
            normalized_values = {
                field: (line[field].strip() if line[field] is not None else "")
                for field in EXCHANGE_RATES_HEADER
            }

            # Skip harmless blank lines left by editors or tooling.
            if not any(normalized_values.values()):
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
        with exchange_rates_file.open(encoding="utf8") as fin:
            return CurrencyConverter._read_exchange_rates_data(exchange_rates_file, fin)

    @staticmethod
    def _write_exchange_rates_file(
        exchange_rates_file: Path | None,
        data: dict[datetime.date, dict[CurrencyCode, Decimal]],
    ) -> None:
        if not exchange_rates_file:
            return
        with open_with_parents(exchange_rates_file) as fout:
            data_rows = [
                [month, symbol, str(rate)]
                for month, rates in data.items()
                for symbol, rate in rates.items()
            ]
            data_rows.sort()
            writer = csv.writer(fout)
            writer.writerows([EXCHANGE_RATES_HEADER, *data_rows])

    def _query_hmrc_api(self, date: datetime.date) -> None:
        LOGGER.info("Fetching HMRC exchange rates for %s...", date.strftime("%Y-%m"))
        # Pre 2021 we need to use the old HMRC endpoint
        if date.year < NEW_ENDPOINT_FROM_YEAR:
            month_str = date.strftime("%m%y")
            url = (
                "https://www.hmrc.gov.uk/softwaredevelopers/rates/"
                f"exrates-monthly-{month_str}.xml"
            )
        else:
            month_str = date.strftime("%Y-%m")
            url = (
                "https://www.trade-tariff.service.gov.uk/uk/api/"
                f"exchange_rates/files/monthly_xml_{month_str}.xml"
            )
        try:
            response = self.session.get(url, timeout=10)
        except Exception as err:
            msg = f"Failed to retrieve HMRC exchange rates for {month_str} from {url}. "
            if self.exchange_rates_file:
                msg += (
                    "Try again later or record the rates manually in "
                    f"{self.exchange_rates_file}. "
                )
            else:
                msg += "Try again later or provide the rates manually. "
            msg += f"Error: {err}"
            raise ExternalApiError(url, msg) from err

        if not response.ok:
            body = response.text.strip()
            extra = ""
            if body:
                snippet_length_limit = 200
                snippet = body[:snippet_length_limit]
                if len(body) > snippet_length_limit:
                    snippet += "..."
                extra = f" Response body: {snippet}"
            raise ExternalApiError(
                url,
                f"HMRC API returned HTTP {response.status_code} for {month_str}.{extra}",
            )

        tree = ET.fromstring(response.text)
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
                raise ExternalApiError(
                    url,
                    f"HMRC API response for {month_str} is missing expected currency data",
                )
            currency = CurrencyCode.parse(currency_code_elem.text)
            if currency is None:
                raise ExternalApiError(
                    url,
                    f"HMRC API response for {month_str} contains invalid currency code: "
                    f"{currency_code_elem.text!r}",
                )
            try:
                rate = Decimal(rate_new_elem.text)
            except (InvalidOperation, ValueError) as err:
                raise ExternalApiError(
                    url,
                    f"HMRC API response for {month_str} contains invalid rate: {rate_new_elem.text}",
                ) from err
            if not rate.is_finite() or rate <= 0:
                raise ExternalApiError(
                    url,
                    f"HMRC API response for {month_str} contains a non-positive "
                    f"or non-finite rate: {rate_new_elem.text}",
                )
            rates[currency] = rate
        self.cache[date] = rates
        self._write_exchange_rates_file(self.exchange_rates_file, self.cache)

    def currency_to_gbp_rate(
        self, currency: CurrencyCode, date: datetime.date
    ) -> Decimal:
        """Get the number of currency units per GBP at the given date."""
        assert is_date(date)
        # offshore (Hong Kong) Chinese Yuan handling
        if currency == "CNH":
            currency = CurrencyCode("CNY")
        if date not in self.cache:
            self._query_hmrc_api(date)
        if currency not in self.cache[date]:
            raise ExchangeRateMissingError(currency, date)

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
    def currency_to_gbp_rate(
        self, currency: CurrencyCode, date: datetime.date
    ) -> Decimal:
        """Get the number of currency units per GBP at the given date.

        When the value is missing from the view of the test_file_cache, append it
        to the exchange rate CSV file.
        This allows us to record the rates that are being used in tests.
        """
        result = super().currency_to_gbp_rate(currency, date)
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
    def _query_hmrc_api(self, date: datetime.date) -> None:
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
