"""The tickers a security traded under before its current one."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING

from .const import TICKER_RENAMES_RESOURCE
from .model import Isin
from .resources import RESOURCES_PACKAGE

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence


@dataclass(frozen=True)
class TickerRename:
    """One ticker change of one security.

    A row under `old` without an ISIN is this security from `since` until
    `reused`, not just until the change: a broker or a RAW file may keep the
    old ticker afterwards, and nobody else used it in between.
    """

    old: str
    new: str
    # The first day this security traded as `old`, where `old` named another
    # security before.
    since: datetime.date | None
    # The first day another security traded as `old`.
    reused: datetime.date | None
    isin: Isin
    company: str

    def covers(self, date: datetime.date) -> bool:
        """Say whether a row under `old`, without an ISIN, is this security."""
        return (self.since is None or self.since <= date) and (
            self.reused is None or date < self.reused
        )


def current_ticker(symbol: str, date: datetime.date, isin: Isin | None = None) -> str:
    """Return the ticker a row's security trades under now.

    A row carrying an ISIN is matched by it, whatever its date; a row without
    one by its date.
    """
    renames = ticker_renames().get(symbol, ())
    if isin is not None:
        found = next((rename for rename in renames if rename.isin == isin), None)
    else:
        found = next((rename for rename in renames if rename.covers(date)), None)
    return symbol if found is None else found.new


def ambiguity(symbol: str, dates: Collection[datetime.date]) -> str | None:
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
        rows = list(csv.DictReader(csv_file))
    by_old: dict[str, list[TickerRename]] = {}
    for row in rows:
        rename = TickerRename(
            old=row["old"],
            new=row["new"],
            since=_optional_date(row["since"]),
            reused=_optional_date(row["reused"]),
            isin=Isin(row["isin"]),
            company=row["company"],
        )
        by_old.setdefault(rename.old, []).append(rename)
    return by_old


def _optional_date(value: str) -> datetime.date | None:
    return datetime.date.fromisoformat(value) if value else None
