"""The tickers a security traded under before its current one."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import csv
import dataclasses
from dataclasses import dataclass
import datetime as dt
from functools import cache
from importlib import resources
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .const import TICKER_RENAMES_RESOURCE
from .csv_columns import CsvColumns
from .exceptions import InvalidTransactionError
from .model import Isin, TransactionSource
from .resources import RESOURCES_PACKAGE
from .util import indent_entry

if TYPE_CHECKING:
    from collections.abc import Collection, Generator, Mapping, Sequence

    from .model import BrokerTransaction

# Every column the bundled table has, read or not, so a misspelt name gets
# the header message rather than a KeyError, or no notice at all.
TICKER_RENAMES_COLUMNS: Final = CsvColumns(
    ("old", "new", "market", "since", "until", "reused", "isin", "company", "source")
)
# One literal each, so that the link check reads the whole address. Not taken
# from the ISIN converter module, which imports a parser and, through it, this
# module.
_SEPARATE_RUNS_DOCS: Final = (
    "https://cgt-calc.uk/extra-data-and-options/#two-securities-in-separate-runs"
)
_TICKER_RENAMES_DOCS: Final = (
    "https://cgt-calc.uk/extra-data-and-options/#ticker-renames"
)
LOGGER = logging.getLogger(__name__)

# The rows each run renamed by a market rename while their files were read,
# with the file each was read from. A parser can drop or merge such a row
# before the run ends, and before its source names the file, but that the
# run exported the old ticker for the company stays true. Open only inside
# `market_rename_evidence()`, so a parser read on its own records nothing.
_open_runs: list[dict[TickerRename, list[tuple[BrokerTransaction, Path]]]] = []


@dataclass(frozen=True)
class TickerRename:
    """One ticker change of one security.

    A row under `old` without an ISIN is this security from `since` until
    `reused`, not just until the change: a broker or a RAW file may keep the
    old ticker afterwards, and nobody else used it in between.
    """

    old: str
    new: str
    # The market `old` named this security on, when another market used
    # `old` for another security. A row without an ISIN is then this
    # security only when it gives this market, or when the rest of the run
    # says so: see `rename_unmarked_rows()`. None: any row in the dates.
    market: str | None
    # The first day this security traded as `old`, where `old` named another
    # security before.
    since: dt.date | None
    # The first day another security traded as `old`.
    reused: dt.date | None
    isin: Isin
    company: str

    def covers(self, date: dt.date) -> bool:
        """Say whether a row under `old`, without an ISIN, is this security."""
        return (self.since is None or self.since <= date) and (
            self.reused is None or date < self.reused
        )


def current_ticker(
    symbol: str, date: dt.date, isin: Isin | None = None, market: str | None = None
) -> str:
    """Return the ticker a row's security trades under now.

    A row carrying an ISIN is matched by it, whatever its date; a row without
    one by its date and, for a rename held to one market, its market.
    """
    found = _rename_of(symbol, date, isin, market)
    return symbol if found is None else found.new


def rename_read_row(transaction: BrokerTransaction, file_path: Path) -> None:
    """Give a row just read from `file_path` its current ticker.

    A rename held to one market is logged for the run under way, as evidence
    of what the old ticker means in it.
    """
    if transaction.symbol is None:
        return
    found = _rename_of(
        transaction.symbol, transaction.date, transaction.isin, transaction.market
    )
    if found is None:
        return
    if found.market is not None and _open_runs:
        _open_runs[-1].setdefault(found, []).append((transaction, file_path))
    transaction.symbol = found.new


def _rename_of(
    symbol: str, date: dt.date, isin: Isin | None, market: str | None
) -> TickerRename | None:
    renames = ticker_renames().get(symbol, ())
    if isin is not None:
        return next((rename for rename in renames if rename.isin == isin), None)
    return next(
        (
            rename
            for rename in renames
            if rename.market in {None, market} and rename.covers(date)
        ),
        None,
    )


@contextmanager
def market_rename_evidence() -> Generator[
    Mapping[TickerRename, Sequence[tuple[BrokerTransaction, Path]]]
]:
    """Log, for one run, the rows each market rename renamed as they were read."""
    evidence: dict[TickerRename, list[tuple[BrokerTransaction, Path]]] = {}
    _open_runs.append(evidence)
    try:
        yield evidence
    finally:
        _open_runs.pop()


def rename_unmarked_rows(
    transactions: Sequence[BrokerTransaction],
    evidence: Mapping[TickerRename, Sequence[tuple[BrokerTransaction, Path]]],
) -> None:
    """Decide once for the run what the rows under an old ticker that show nothing are.

    A rename held to one market has already renamed the rows that give its
    ISIN or its market. A row under the old ticker with neither could be the
    company or the other security that used the ticker, so the rest of the
    run decides, from what it exported under the old ticker: the company
    alone renames them all, another security alone or nothing leaves them
    all, and both is refused. Rows exported under the new ticker say nothing
    about the old one: they are another history.
    """
    for renames in ticker_renames().values():
        for rename in renames:
            if rename.market is None:
                continue
            under_old = [row for row in transactions if row.symbol == rename.old]
            bare = [row for row in under_old if row.isin is None and row.market is None]
            if not bare:
                continue
            # The company's rows were renamed as they were read, so a row
            # still under the old ticker that names its security is another's.
            other = next(
                (
                    row
                    for row in under_old
                    if row.isin is not None or row.market is not None
                ),
                None,
            )
            company = evidence.get(rename, ())
            if company and other is not None:
                raise InvalidTransactionError(
                    bare[0], _two_securities(rename, company[0], other)
                )
            if company:
                for row in bare:
                    row.symbol = rename.new
            elif other is None:
                LOGGER.warning(
                    "Rows under %s name no ISIN or market, and no row of this run "
                    "exported under %s names its ISIN or market, so they are kept "
                    "as %s. They may be %s, whose ticker changed to %s, or another "
                    "security that used %s. If they are %s rows in your RAW file, "
                    "write %s in their market column. See %s",
                    rename.old,
                    rename.old,
                    rename.old,
                    rename.company,
                    rename.new,
                    rename.old,
                    rename.company,
                    rename.market,
                    _TICKER_RENAMES_DOCS,
                )


def _two_securities(
    rename: TickerRename,
    company: tuple[BrokerTransaction, Path],
    other: BrokerTransaction,
) -> str:
    return (
        f"Ticker {rename.old} names no ISIN or market on this row, and this run "
        f"shows two securities under {rename.old}: {rename.company}, whose ticker "
        f"changed to {rename.new}, on\n{indent_entry(_located(*company))}\n"
        f"and another security on\n{indent_entry(other)}\n"
        "so cgt-calc cannot tell which of them this row is. Two securities under "
        "one ticker would be pooled and matched as one holding. If this row is in "
        "your RAW file and you know where its security is listed, write that in "
        f"its market column: {rename.market} for {rename.company}, or the other "
        "security's market if it is US or LSE. Rows you cannot place that way, "
        "such as rows of a broker export, need the two securities calculated in "
        "separate runs; to split the files and combine the reports, see "
        f"{_SEPARATE_RUNS_DOCS}"
    )


def _located(row: BrokerTransaction, file_path: Path) -> BrokerTransaction:
    """Return the row with the file it was read from, for printing.

    A parser can drop a row before the shared loader records its file, as
    Schwab drops a cancelled purchase, and the row would then print with no
    place at all.
    """
    if row.source is not None and row.source.file is not None:
        return row
    located = copy.copy(row)
    located.source = dataclasses.replace(
        row.source or TransactionSource(), file=file_path
    )
    return located


def ambiguity(symbol: str, dates: Collection[dt.date]) -> str | None:
    """Explain why rows without an ISIN under `symbol` cannot be told apart.

    When some of the rows fall while `symbol` was a renamed security's and
    some do not, they may belong to two securities, or be one security's
    history written under one ticker, and nothing in the rows says which.
    """
    for rename in ticker_renames().get(symbol, ()):
        covered = [rename.covers(date) for date in dates]
        if all(covered) or not any(covered):
            continue
        if rename.since is not None and min(dates) < rename.since:
            crossed = f"{rename.since}, when {rename.company} started trading as"
        else:
            crossed = f"{rename.reused}, when another security started trading as"
        return (
            f"Rows under {symbol} fall on both sides of {crossed} {symbol}, so "
            f"cgt-calc cannot tell which rows belong to {rename.company}. Write "
            f"{rename.new} on every {rename.company} row."
        )
    return None


@cache
def ticker_renames() -> Mapping[str, Sequence[TickerRename]]:
    """Read the bundled rename table, by old ticker."""
    with (
        resources.files(RESOURCES_PACKAGE)
        .joinpath(TICKER_RENAMES_RESOURCE)
        .open(encoding="utf-8") as csv_file
    ):
        reader = csv.DictReader(csv_file)
        reader.fieldnames = list(
            TICKER_RENAMES_COLUMNS.positions(
                reader.fieldnames or [],
                Path("resources") / TICKER_RENAMES_RESOURCE,
            )
        )
        rows = list(reader)
    by_old: dict[str, list[TickerRename]] = {}
    for row in rows:
        rename = TickerRename(
            old=row["old"],
            new=row["new"],
            market=row["market"] or None,
            since=_optional_date(row["since"]),
            reused=_optional_date(row["reused"]),
            isin=Isin(row["isin"]),
            company=row["company"],
        )
        by_old.setdefault(rename.old, []).append(rename)
    return by_old


def _optional_date(value: str) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None
