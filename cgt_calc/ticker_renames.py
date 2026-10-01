"""The tickers a security traded under before its current one."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Final

from .const import TICKER_RENAMES_RESOURCE
from .model import Isin
from .resources import RESOURCES_PACKAGE

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

# A London rename applies only to a row that carries its ISIN: old London
# tickers are reused, and several are live US tickers (ABG, PFG).
ISIN_ONLY_EXCHANGES: Final = frozenset({"LSE"})


@dataclass(frozen=True)
class TickerRename:
    """One ticker change of one security.

    A row under `old` without an ISIN is this security from `since` until
    `reused`, not just until `until`: a broker or a RAW file may keep the old
    ticker after the change, and nobody else used it in between. A London
    rename needs the row's ISIN instead.
    """

    old: str
    new: str
    # The first day this security traded as `old`, where `old` named another
    # security before.
    since: datetime.date | None
    # The first trading day under `new`.
    until: datetime.date
    # The first day another security traded as `old`.
    reused: datetime.date | None
    isin: Isin
    # Where `old` traded.
    exchange: str
    company: str

    @property
    def needs_isin(self) -> bool:
        """Say whether only a row carrying the ISIN can be this security."""
        return self.exchange in ISIN_ONLY_EXCHANGES

    def covers(self, date: datetime.date) -> bool:
        """Say whether a row under `old`, without an ISIN, is this security."""
        return (
            not self.needs_isin
            and (self.since is None or self.since <= date)
            and (self.reused is None or date < self.reused)
        )


def current_ticker(symbol: str, date: datetime.date, isin: Isin | None = None) -> str:
    """Return the ticker a row's security trades under now.

    A row carrying an ISIN is matched by it, whatever its date; a row without
    one by its date, which a London rename never matches.
    """
    renames = ticker_renames().get(symbol, ())
    if isin is not None:
        found = next((rename for rename in renames if rename.isin == isin), None)
    else:
        found = next((rename for rename in renames if rename.covers(date)), None)
    return symbol if found is None else found.new


def ambiguity(symbol: str, dates: Collection[datetime.date]) -> str | None:
    """Explain why rows without an ISIN under `symbol` cannot be told apart.

    Rows on both sides of a day `symbol` changed hands may belong to two
    securities, or be one security's history written under one ticker, and
    nothing in the rows says which.
    """
    for rename in ticker_renames().get(symbol, ()):
        for boundary, event in (
            (rename.since, f"when {rename.company} started trading as"),
            (rename.reused, "when another security started trading as"),
        ):
            if boundary is not None and min(dates) < boundary <= max(dates):
                return (
                    f"Rows under {symbol} fall on both sides of {boundary}, "
                    f"{event} {symbol}, so cgt-calc cannot tell which rows belong "
                    f"to {rename.company}. Write {rename.new} on "
                    f"every {rename.company} row."
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
        rows = list(csv.DictReader(csv_file))
    by_old: dict[str, list[TickerRename]] = {}
    for row in rows:
        rename = TickerRename(
            old=row["old"],
            new=row["new"],
            since=_optional_date(row["since"]),
            until=datetime.date.fromisoformat(row["until"]),
            reused=_optional_date(row["reused"]),
            isin=Isin(row["isin"]),
            exchange=row["exchange"],
            company=row["company"],
        )
        by_old.setdefault(rename.old, []).append(rename)
    return by_old


def _optional_date(value: str) -> datetime.date | None:
    return datetime.date.fromisoformat(value) if value else None
