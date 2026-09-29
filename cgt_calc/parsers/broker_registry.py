"""Registry for all broker parsers."""

from __future__ import annotations

import csv
import io
import logging
import sys
from typing import TYPE_CHECKING, ClassVar

from colorama import Fore

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.logging import style_text
from cgt_calc.model import ActionType
from cgt_calc.parsers.eri.raw import ERIRawParser
from cgt_calc.parsers.freetrade import FreetradeParser
from cgt_calc.parsers.hl import HargreavesLansdownParser
from cgt_calc.parsers.interactive_brokers import InteractiveBrokersParser
from cgt_calc.parsers.mssb import MSSBParser
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.revolut import RevolutParser
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.schwab_equity_award_json import SchwabEquityAwardsJSONParser
from cgt_calc.parsers.sharesight import SharesightParser
from cgt_calc.parsers.trading212 import Trading212Parser
from cgt_calc.parsers.vanguard import VanguardParser, VanguardTransaction

if TYPE_CHECKING:
    import argparse
    import datetime

    from cgt_calc.isin_converter import IsinConverter
    from cgt_calc.model import BrokerTransaction, Isin

    from .base_parsers import BaseParser

LOGGER = logging.getLogger(__name__)


def _resolve_isins(
    transactions: list[BrokerTransaction], isin_map: dict[str, Isin]
) -> tuple[set[Isin], list[str]]:
    """Resolve the run's ISINs and list Vanguard symbols that have none.

    Both answers come from one pass, so the warning cannot drift from the ERI
    filter it describes.

    A symbol is matchable when any row carries its ISIN or the cache maps it,
    even a row from another broker: warning about the Vanguard rows for it
    would send the user after a mapping that changes nothing. A rename moves
    the holding to the new symbol, so a mapping for the new name covers the
    rows still carrying the old one.
    """
    isins: set[Isin] = set()
    resolved: set[str] = set()
    renamed_to: dict[str, str] = {}
    vanguard_symbols: set[str] = set()

    for transaction in transactions:
        symbol = transaction.symbol
        isin = transaction.isin or (isin_map.get(symbol) if symbol else None)
        if isin is not None:
            isins.add(isin)
            if symbol:
                resolved.add(symbol)
        if (
            transaction.action is ActionType.RENAME
            and symbol
            and transaction.description.startswith(RENAME_DESCRIPTION_PREFIX)
        ):
            old_symbol = transaction.description[len(RENAME_DESCRIPTION_PREFIX) :]
            if old_symbol:
                renamed_to[old_symbol] = symbol
        if isinstance(transaction, VanguardTransaction) and symbol and symbol.strip():
            vanguard_symbols.add(symbol)

    unmapped = sorted(
        symbol
        for symbol in vanguard_symbols
        if symbol not in resolved and renamed_to.get(symbol) not in resolved
    )
    return isins, unmapped


def _transaction_sort_key(
    transaction: BrokerTransaction,
) -> tuple[datetime.date, int]:
    """Sort by date, with share vests first and transfers to a spouse last.

    A broker export can list the sale of vested shares before the vest itself
    (e.g. Schwab or Morgan Stanley equity awards). A disposal is validated
    against the current holding as soon as it is read, so the unsorted order
    fails with "Tried to sell not owned symbol". Vesting (``STOCK_ACTIVITY``)
    does not move the cash balance, so ordering it first cannot introduce a
    negative balance.

    Shares received from a spouse are written by hand too, and like a vest
    they cost nothing in cash, so they go first as well.

    Transfers to a spouse and gifts go last for the same reason from the
    other end. They are recorded by hand in a RAW file while the shares they
    move were acquired through a broker export, and parsers are merged in
    registry order, so such a row would otherwise be validated before the
    same-day acquisition that makes it possible. No consideration changes
    hands, so ordering them last cannot introduce a negative balance either.

    Everything else keeps its relative order, so the order each parser gives
    a day's rows, such as reading its sales after its purchases, is preserved.
    """
    if transaction.action in {
        ActionType.STOCK_ACTIVITY,
        ActionType.TRANSFER_FROM_SPOUSE,
    }:
        order = 0
    elif transaction.action in {
        ActionType.TRANSFER_TO_SPOUSE,
        ActionType.GIFT,
        ActionType.GIFT_UNCONNECTED,
    }:
        order = 2
    else:
        order = 1
    return (transaction.date, order)


def _as_csv_fields(symbols: list[str]) -> str:
    """Render symbols the way the cache file has to spell them.

    A Vanguard symbol falls back to the fund name, which can contain a comma
    or a quote. The listed text is meant to be copied into the translation
    CSV, so write it with the same module that reads the cache: each field
    then survives the round trip verbatim, and a comma inside a name is
    quoted rather than read as a separator.
    """
    buffer = io.StringIO()
    csv.writer(buffer).writerow(symbols)
    return buffer.getvalue().rstrip("\r\n")


class BrokerRegistry:
    """Registry for all broker parsers."""

    # Ordered by pretty name, matching the docs, with the generic RAW format
    # last. Add new brokers in their sorted place.
    _BROKERS: ClassVar[list[type[BaseParser]]] = [
        SchwabParser,
        SchwabEquityAwardsJSONParser,
        FreetradeParser,
        HargreavesLansdownParser,
        InteractiveBrokersParser,
        MSSBParser,
        RevolutParser,
        SharesightParser,
        Trading212Parser,
        VanguardParser,
        RawParser,
    ]

    @staticmethod
    def register_all_arguments(broker_group: argparse._ArgumentGroup) -> None:
        """Register arguments for all brokers."""
        for broker_class in BrokerRegistry._BROKERS:
            broker_class.register_arguments(broker_group)

        # ERI Raw is not a broker but is close enough to one to be here
        ERIRawParser.register_arguments(broker_group)

    @staticmethod
    def load_all_transactions(
        args: argparse.Namespace, isin_converter: IsinConverter
    ) -> list[BrokerTransaction]:
        """Load transactions from all brokers."""
        all_transactions: list[BrokerTransaction] = []
        for broker_class in BrokerRegistry._BROKERS:
            transactions = broker_class.load_from_args(args)
            if transactions:
                LOGGER.info(
                    "Loaded %d transactions from %s",
                    len(transactions),
                    broker_class.pretty_name,
                )
                all_transactions += transactions

        # ERI transactions are appended after this count, so scope the wording.
        msg = f"Found {len(all_transactions)} broker transactions"
        if len(all_transactions) == 0:
            LOGGER.warning(msg)
        else:
            LOGGER.info(
                "\n%s",
                style_text(msg, colour=Fore.CYAN, emoji="📄", stream=sys.stderr),
            )

        # ERI Raw is not a broker but is close enough to one to be here
        # Only add ERI for funds that show up in the portfolio
        isin_map = isin_converter.get_symbol_to_isin_map()
        isins, unmapped_vanguard_symbols = _resolve_isins(all_transactions, isin_map)
        if unmapped_vanguard_symbols:
            # Name the cache the converter actually read; the flag can be
            # cleared, leaving no location to point the user to.
            cache_path = isin_converter.isin_translation_file
            location = f" at {cache_path}" if cache_path is not None else ""
            LOGGER.warning(
                "Vanguard transactions do not contain ISINs, and no ISIN mapping "
                "was found for: %s. cgt-calc cannot match Excess Reported "
                "Income for these holdings if it applies; add verified mappings "
                "to the --isin-translation-file cache%s. Give each ISIN a single "
                "row listing every symbol it is known by: a row that leaves one "
                "out replaces the existing mapping and can stop a later run. "
                "See https://cgt-calc.uk/extra-data-and-options/#isin-to-ticker-translation",
                _as_csv_fields(unmapped_vanguard_symbols),
                location,
            )
        all_transactions += [
            trx for trx in ERIRawParser.load_from_args(args) if trx.isin in isins
        ]

        all_transactions.sort(key=_transaction_sort_key)
        return all_transactions
