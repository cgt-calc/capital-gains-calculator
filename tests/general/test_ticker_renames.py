"""Check the bundled ticker rename table."""

from __future__ import annotations

import csv
import datetime
from importlib import resources

from cgt_calc.const import TICKER_RENAMES_RESOURCE
from cgt_calc.parsers.schwab_equity_award_json import SPLITS
from cgt_calc.resources import RESOURCES_PACKAGE
from cgt_calc.ticker_renames import ticker_renames


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
    with (
        resources.files(RESOURCES_PACKAGE)
        .joinpath(TICKER_RENAMES_RESOURCE)
        .open(encoding="utf-8") as csv_file
    ):
        rows = list(csv.DictReader(csv_file))
    # Loading checks each ISIN and date.
    assert sum(len(renames) for renames in ticker_renames().values()) == len(rows)

    def day(value: str) -> datetime.date | None:
        return datetime.date.fromisoformat(value) if value else None

    for row in rows:
        since, until, reused = day(row["since"]), day(row["until"]), day(row["reused"])
        assert until is not None, row
        assert since is None or since < until, row
        assert reused is None or reused >= until, row
        assert row["old"] not in SPLITS, row
        assert row["new"] not in SPLITS, row
        assert row["company"], row
        urls = row["source"].split()
        assert urls, row
        assert all(url.startswith("https://") for url in urls), row
        for other in rows:
            if other["new"] == row["old"]:
                assert since is not None, (other, row)
                assert since >= datetime.date.fromisoformat(other["until"]), (
                    other,
                    row,
                )
            if other is not row and other["old"] == row["old"]:
                other_since, other_reused = day(other["since"]), day(other["reused"])
                starts_before_other_ends = (
                    since is None or other_reused is None or since < other_reused
                )
                other_starts_before_ends = (
                    other_since is None or reused is None or other_since < reused
                )
                assert not (starts_before_other_ends and other_starts_before_ends), (
                    row,
                    other,
                )
