"""Check the bundled ticker rename table."""

from __future__ import annotations

import csv
from importlib import resources

from cgt_calc.const import TICKER_RENAMES_RESOURCE
from cgt_calc.parsers.schwab_equity_award_json import SPLITS
from cgt_calc.resources import RESOURCES_PACKAGE
from cgt_calc.ticker_renames import TickerRename, ticker_renames


def test_every_rename_is_unambiguous_and_sourced() -> None:
    """A rename the table cannot apply safely is refused here, not at run time.

    Two renames covering one old ticker on one day would leave the row to
    whichever comes first in the file. A rename of a ticker that another
    rename created has to state when its security took that ticker, and that
    has to be once the other rename took effect: before then the ticker's
    rows may be the other security's history, relabelled. Meta's 2021 trades
    shown as META would otherwise move into Roundhill's ETF, which traded as
    META until January 2022. The Equity Awards parser keys its split table by
    ticker while reading, before renames apply, so renaming one of those
    tickers needs that parser changed first.
    """
    renames = [rename for group in ticker_renames().values() for rename in group]

    def overlap(a: TickerRename, b: TickerRename) -> bool:
        return (a.since is None or b.reused is None or a.since < b.reused) and (
            b.since is None or a.reused is None or b.since < a.reused
        )

    for rename in renames:
        assert rename.since is None or rename.since < rename.until, rename
        assert rename.reused is None or rename.reused >= rename.until, rename
        assert rename.old not in SPLITS, rename
        assert rename.new not in SPLITS, rename
        if rename.needs_isin:
            continue
        for other in renames:
            if other.new == rename.old:
                assert rename.since is not None, (other, rename)
                assert rename.since >= other.until, (other, rename)
            if other is not rename and other.old == rename.old and not other.needs_isin:
                assert not overlap(rename, other), (rename, other)

    with (
        resources.files(RESOURCES_PACKAGE)
        .joinpath(TICKER_RENAMES_RESOURCE)
        .open(encoding="utf-8") as csv_file
    ):
        rows = list(csv.DictReader(csv_file))
    for row in rows:
        assert row["company"], row
        urls = row["source"].split()
        assert urls, row
        assert all(url.startswith("https://") for url in urls), row
