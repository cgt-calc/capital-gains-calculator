"""Convert ISIN to tickers using transaction data or manual mappings."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from importlib import resources
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pyrate_limiter import limiter_factory
from pyrate_limiter.abstracts.rate import Duration
from pyrate_limiter.extras.requests_limiter import RateLimitedRequestsSession
from requests import exceptions as requests_exceptions

from .const import (
    CGT_MODE,
    INITIAL_ISIN_TRANSLATION_RESOURCE,
    ISIN_TICKER_ALIASES,
    RuntimeMode,
)
from .exceptions import (
    ExternalApiError,
    InvalidTransactionError,
    IsinTranslationError,
    ParsingError,
    UnexpectedColumnCountError,
    UnexpectedHeaderError,
    reading_as,
)
from .model import Isin
from .resources import RESOURCES_PACKAGE
from .util import open_with_parents

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from .model import BrokerTransaction

ISIN_TRANSLATION_HEADER: Final = ["ISIN", "symbol"]
ISIN_TRANSLATION_COLUMNS_NUM: Final = len(ISIN_TRANSLATION_HEADER)
LOGGER = logging.getLogger(__name__)


@dataclass
class IsinTranslationEntry:
    """Entry from ISIN Translation file."""

    isin: Isin
    symbols: set[str]

    def __init__(self, row: list[str], file: Path):
        """Create entry from CSV row."""
        if len(row) < ISIN_TRANSLATION_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, ISIN_TRANSLATION_COLUMNS_NUM, file)
        isin = Isin.parse(row[0])
        if isin is None:
            raise ParsingError(file, f"Row contains invalid ISIN '{row[0]}'")
        self.isin = isin
        # Spaces typed beside the comma are not part of a ticker.
        self.symbols = {symbol.strip() for symbol in row[1:]}
        if "" in self.symbols and len(self.symbols) > 1:
            raise ParsingError(
                file,
                "This row has an empty ticker beside a real one. Remove the extra "
                "comma.",
            )


class IsinConverter:
    """Converter which holds ISIN-to-ticker mappings."""

    def __init__(
        self,
        isin_translation_file: Path | None = None,
    ):
        """Create the IsinConverter."""
        # https://www.openfigi.com/api/documentation#rate-limits
        limiter = limiter_factory.create_inmemory_limiter(
            rate_per_duration=24, duration=Duration.MINUTE
        )
        self.session = RateLimitedRequestsSession(limiter)
        self.isin_translation_file = isin_translation_file
        # Reference data: the bundled table, the user's cache file and live
        # OpenFIGI results. One consistent set of mappings, which is what
        # validate_data checks, so the tickers this run's transactions carry
        # are kept out of it and tracked below instead.
        self.data: dict[Isin, set[str]] = {}
        # The subset written back to the cache file. That file caches OpenFIGI
        # lookups; a ticker learned from a transaction stored there is what
        # makes one run's holding conflict with the next run's.
        self.write_data: dict[Isin, set[str]] = {}
        # The tickers this run's transactions carry, and the reverse index.
        # Two transactions disagreeing about a security is the one case where
        # a pool really splits, and the only one refused here.
        self.transaction_symbols: dict[Isin, set[str]] = {}
        self.transaction_isins: dict[str, Isin] = {}
        # The ISIN each ticker was exported under, before any alias rewrite.
        # A broker that names the security only on some of its rows - IBKR
        # puts the ISIN in a dividend description but not in a trade one -
        # identifies the rest of them through this.
        self.exported_isins: dict[str, Isin] = {}
        self.validate_data(self._read_isin_translation_data())

    def validate_data(self, file_rows: dict[Isin, int] | None = None) -> None:
        """Refuse a ticker that two ISINs claim.

        ``file_rows`` holds the row of each ISIN in the user's file, so that the
        error can name it. The check after a lookup passes none: a looked-up
        ISIN has no row.
        """
        rows = file_rows or {}
        file = self.isin_translation_file
        reverse_cache: dict[str, Isin] = {}
        for isin, symbols in self.data.items():
            if symbols == {""}:
                continue
            # In order, so that a row clashing on two tickers names the same
            # one on every run.
            for symbol in sorted(symbols):
                # Only a lookup can bring this: a row of a file that has it is
                # refused where it is read.
                if not symbol:
                    raise IsinTranslationError(
                        f"Ticker list for ISIN {isin} contains an empty value"
                    )
                existing_isin = reverse_cache.setdefault(symbol, isin)
                if existing_isin == isin:
                    continue
                # Whichever of the two was met first, report at the user's row,
                # and at the later of two: the bundled list counts as row 0.
                first, second = sorted(
                    (existing_isin, isin), key=lambda one: rows.get(one, 0)
                )
                # The file is never None where there are rows: that half is
                # for the type checker.
                if file is None or second not in rows:
                    raise IsinTranslationError(
                        f"Ticker {symbol} already linked to ISIN {first}; "
                        f"cannot also link to {second}"
                    )
                where = (
                    f"on row {rows[first]}" if first in rows else "in the bundled list"
                )
                raise ParsingError(
                    file,
                    f"Ticker {symbol} already linked to ISIN {first} {where}; "
                    f"cannot also link to {second}.",
                    row_index=rows[second],
                )

    def _isin_for_symbol(self, symbol: str) -> Isin | None:
        """Return the one reference ISIN a ticker is known under, if any.

        validate_data() guarantees at most one, so a match is unambiguous.
        """
        return next(
            (isin for isin, symbols in self.data.items() if symbol in symbols),
            None,
        )

    def add_from_transaction(self, transaction: BrokerTransaction) -> None:
        """Normalise the transaction's ticker and record what it links to.

        A known exchange alias is rewritten in place, on the transaction the
        rest of the calculation reads, so the holding pools, matches and
        prices under one ticker instead of one per listing.

        A row without an ISIN is resolved from what the rest of the export
        says its ticker stands for, or failing that from reference data, and
        then treated exactly like a row that carried that ISIN - alias
        rewriting included. A broker that never reports an ISIN would
        otherwise let a holding split past every check below, and past the
        alias table, just by leaving the field blank. Resolving from the
        export needs add_from_transactions(), which reads every row's
        identity before the first is normalised, so a trade is not left
        split by arriving ahead of the dividend that names its security.

        Anything else is recorded as this run's name for the ISIN. Two
        transactions disagreeing is refused, in either direction: one ISIN
        arriving under two tickers, which splits its pool, and one ticker
        arriving under two ISINs, which pools two securities as one. A ticker
        that merely differs from reference data for the SAME ISIN does
        neither, and refusing it turned a stale cache row from an earlier run
        into a failure of an otherwise correct one - so only that direction
        excuses a reference-only mismatch. The other direction stays refused
        even against reference data: validate_data() already guarantees one
        ISIN per ticker there, so a transaction reusing a ticker reference
        already gave to a different security is exactly the case this check
        exists for, not a stale row to look past.
        """
        if not transaction.symbol:
            return

        isin = transaction.isin
        if isin is None:
            isin = self.exported_isins.get(transaction.symbol) or self._isin_for_symbol(
                transaction.symbol
            )
            if isin is None:
                return

        canonical = ISIN_TICKER_ALIASES.get((isin, transaction.symbol))
        if canonical is not None:
            LOGGER.debug(
                "ISIN %s: reporting exchange alias %s as %s",
                isin,
                transaction.symbol,
                canonical,
            )
            transaction.symbol = canonical

        symbol = transaction.symbol
        recorded = self.transaction_symbols.get(isin) or set()
        known = self.data.get(isin) or set()
        # Reference data listing all of them on one row is a security known by
        # several names rather than two exports disagreeing. Those holdings
        # pool separately, as they always have: that is a wider problem than
        # this check, and failing here would not choose a ticker for them.
        if recorded and symbol not in recorded and not recorded | {symbol} <= known:
            raise InvalidTransactionError(
                transaction,
                f"Ticker {symbol} does not match "
                f"{', '.join(sorted(recorded))}, used by another transaction for "
                f"ISIN {isin}. One security under two tickers would "
                "be pooled and matched as two holdings. Check the exports; if "
                "both are listings of the same security, report the ISIN and "
                "both tickers so the pair can be added.",
            )

        owner = self.transaction_isins.get(symbol) or self._isin_for_symbol(symbol)
        if owner is not None and owner != isin:
            raise InvalidTransactionError(
                transaction,
                f"Ticker {symbol} is already used for ISIN {owner}, so it "
                f"cannot also stand for ISIN {isin}. Two securities under one "
                "ticker would be pooled and matched as one holding.",
            )

        self.transaction_symbols.setdefault(isin, set()).add(symbol)
        self.transaction_isins[symbol] = isin

    def add_from_transactions(self, transactions: list[BrokerTransaction]) -> None:
        """Normalise a whole run's tickers, reading their identities first.

        Which security a ticker stands for is settled across the input before
        any row is normalised, so the answer does not depend on the order the
        broker exported its rows in.

        One exported ticker standing for two ISINs is refused here, and has to
        be: the alias rewrite below moves an aliased row to its canonical
        ticker before the ownership check can see the collision, so `NVD`
        under Nvidia and `NVD` under anything else would both go through, and
        a row naming no ISIN of its own would join whichever the export listed
        first. A ticker code belongs to an exchange, so two brokers really can
        export one code for two securities; which of them a trade belongs to
        is not in the file, so cgt-calc refuses rather than pick one.
        """
        for transaction in transactions:
            if not transaction.symbol or not transaction.isin:
                continue
            known = self.exported_isins.setdefault(transaction.symbol, transaction.isin)
            if known != transaction.isin:
                raise InvalidTransactionError(
                    transaction,
                    f"Ticker {transaction.symbol} is exported for ISIN {known} "
                    f"and for ISIN {transaction.isin}. Two securities under one "
                    "ticker cannot be told apart, and a row naming no ISIN of "
                    "its own would be pooled with whichever listing the export "
                    "stated first. Check the exports and calculate the two "
                    "securities in separate runs.",
                )
        for transaction in transactions:
            self.add_from_transaction(transaction)

    def get_symbols(self, isin: Isin) -> set[str]:
        """Return the set of symbols associated with the input ISIN (may be empty).

        A ticker this run's transactions carry answers for the ISIN as well as
        a stored one does, and spares the lookup.
        """
        recorded = self.transaction_symbols.get(isin) or set()
        result = self.data.get(isin)
        if result is None and not recorded:
            result = self._fetch_live(isin)
            self.data[isin] = result
            if result:
                self.write_data[isin] = result
                self._write_isin_translation_file()
        return (result or set()) | recorded

    def get_symbol_to_isin_map(self) -> dict[str, Isin]:
        """Return a map from symbols to ISINs.

        An ISIN whose ticker is unknown is recorded with an empty symbol, so
        empty entries are skipped here. Keeping them would put an "" key in the
        map, and every transaction without a symbol - interest, transfers, fees
        - would then resolve to whichever ISIN happened to own that key.
        """
        symbol_to_isin = {}
        for isin, symbols in self.data.items():
            for symbol in symbols:
                if symbol:
                    symbol_to_isin[symbol] = isin
        # Last, so that a ticker this run's transactions carry outranks a
        # stored row that gives it to another ISIN.
        symbol_to_isin.update(self.transaction_isins)
        return symbol_to_isin

    def _read_isin_translation_data(self) -> dict[Isin, int]:
        """Read the bundled list, then the user's file on top of it.

        Returns the row of each ISIN in the user's file.
        """

        def load(
            source: Traversable | Path,
        ) -> tuple[dict[Isin, set[str]], dict[Isin, int]]:
            """Load a CSV source: its mappings, and the row of each ISIN."""
            file_label = (
                source if isinstance(source, Path) else Path("resources") / source.name
            )
            with (
                reading_as("utf-8-sig", file_label),
                source.open(encoding="utf-8-sig") as csv_file,
            ):
                # A space after a comma is skipped here, not left to the trim
                # below: a name in quotes is only read as quoted when the
                # quote is the first thing in its cell.
                lines = list(csv.reader(csv_file, skipinitialspace=True))
            if not lines:
                return {}, {}
            header = lines[0]
            if header != ISIN_TRANSLATION_HEADER:
                raise UnexpectedHeaderError(header, ISIN_TRANSLATION_HEADER, file_label)
            entries: dict[Isin, set[str]] = {}
            # The row each ISIN was first read from, to name it in the error.
            first_rows: dict[Isin, int] = {}
            for index, row in enumerate(lines[1:], start=2):
                # Skip harmless blank rows left by editors or tooling.
                if not any(cell.strip() for cell in row):
                    continue
                # No ticker spans lines: a cell that does is a quoted name
                # left open, which has taken the following rows into itself.
                if any("\n" in cell for cell in row):
                    raise ParsingError(
                        file_label,
                        "This row has a double quote that is never closed, so the "
                        "lines after it were read as part of one name. Add the "
                        "closing quote.",
                        row_index=index,
                    )
                try:
                    entry = IsinTranslationEntry(row, file_label)
                except ParsingError as err:
                    err.add_row_context(index)
                    raise
                # Keeping the later row would drop the earlier one's tickers
                # without a word. The same tickers twice say nothing new.
                earlier = entries.get(entry.isin)
                if earlier is not None and earlier != entry.symbols:
                    raise ParsingError(
                        file_label,
                        f"ISIN {entry.isin} is also on row "
                        f"{first_rows[entry.isin]}, and the two rows do not list "
                        "the same tickers. Keep one row for it, with every ticker "
                        "that is right.",
                        row_index=index,
                    )
                entries[entry.isin] = entry.symbols
                first_rows.setdefault(entry.isin, index)
            return entries, first_rows

        bundled_source = resources.files(RESOURCES_PACKAGE).joinpath(
            INITIAL_ISIN_TRANSLATION_RESOURCE
        )
        self.data.update(load(bundled_source)[0])

        file_rows: dict[Isin, int] = {}
        if (
            self.isin_translation_file is not None
            and self.isin_translation_file.is_file()
        ):
            self.write_data, file_rows = load(self.isin_translation_file)
            self.data.update(self.write_data)
        return file_rows

    def _write_isin_translation_file(self) -> None:
        # Over every reference source, not just the rows about to be written:
        # the next run reads this file alongside the bundled table, so a
        # looked-up ticker that another ISIN already owns has to be refused
        # here rather than break that run's construction.
        self.validate_data()
        if self.isin_translation_file is None or CGT_MODE != RuntimeMode.PROD:
            return
        with open_with_parents(self.isin_translation_file) as fout:
            data_rows = [
                [isin, *sorted(symbols)] for isin, symbols in self.write_data.items()
            ]
            writer = csv.writer(fout)
            writer.writerows([ISIN_TRANSLATION_HEADER, *data_rows])

    def _fetch_live(self, isin: Isin) -> set[str]:
        LOGGER.info("Looking up ISIN %s via OpenFIGI...", isin)
        url = "https://api.openfigi.com/v3/mapping"
        headers = {"Content-type": "application/json"}
        data = [{"idType": "ID_ISIN", "idValue": isin}]
        response_text = ""
        try:
            response = self.session.post(url, json=data, headers=headers, timeout=10)
            response_text = response.text
            json_response = response.json()
        except (requests_exceptions.RequestException, ValueError) as err:
            msg = f"Failed to fetch ISIN information for {isin}. "
            if response_text:
                msg += f"Server response: {response_text}. "
            msg += "Try again later or, if you're confident about the ticker, add it "
            msg += f"manually to {self.isin_translation_file}. Error: {err}"
            raise ExternalApiError(url, msg) from err

        if not json_response or "data" not in json_response[0]:
            LOGGER.warning(
                "Couldn't translate ISIN %s: Invalid Response: %s", isin, json_response
            )
            return set()

        json_data = json_response[0]["data"]

        # https://www.openfigi.com/assets/content/OpenFIGI_Exchange_Codes-3d3e5936ba.csv
        # Get London exchange first
        result = {data["ticker"] for data in json_data if data["exchCode"] == "LN"}

        if result:
            return result

        # Get all the other UK exchanges
        result = {
            data["ticker"]
            for data in json_data
            if data["exchCode"] in {"LC", "LT", "LI", "LO"}
        }

        if result:
            return result

        # Get the shorter ticker as final fallback
        all_tickers = [data["ticker"] for data in json_data if data]
        if all_tickers:
            return {min(all_tickers, key=len)}

        LOGGER.warning(
            "Couldn't translate ISIN %s: Match not found in %s", isin, json_data
        )
        return set()
