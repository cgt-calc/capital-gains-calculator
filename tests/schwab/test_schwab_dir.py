"""Test parsing a directory of Schwab exports with --schwab-dir.

Schwab limits how much history one export covers, so a long history arrives as
several date-range CSVs. These tests cover what splitting an export across
files changes: a Cancel Buy whose Buy is in the neighbouring file, the ordering
of the merged result, and the overlap that cannot be deduplicated away.
"""

from __future__ import annotations

from decimal import Decimal
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.exceptions import CgtError, ParsingError
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser
from tests.utils import build_cmd

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction

HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"

# Newest-first within each file, as a real Schwab export is.
NEWER = (
    "03/10/2024,Sell,AAPL,APPLE INC,$180.00,5,$0.00,$900.00\n"
    "02/02/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
)
OLDER = (
    "11/20/2023,Buy,AAPL,APPLE INC,$120.00,20,$0.00,-$2400.00\n"
    "06/05/2023,MoneyLink Transfer,,Deposit,,,,$5000.00\n"
)


@pytest.fixture(autouse=True)
def _reset_awards_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep changes to the class-level award prices out of other test modules."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))


def _load(**flags: str) -> list[BrokerTransaction]:
    """Load through the CLI wiring rather than calling the parser directly.

    load_from_args is what registers --schwab-dir and fills in awards_prices,
    so bypassing it would let the flag be misregistered with every test still
    passing.
    """
    argv = ["--year", "2023"]
    for flag, value in flags.items():
        argv += [f"--{flag.replace('_', '-')}", value]
    args = create_parser().parse_args(argv)
    return SchwabParser.load_from_args(args)


def test_cancel_buy_matches_a_buy_in_another_file(tmp_path: Path) -> None:
    """The Buy a Cancel Buy reverses may sit in the neighbouring export.

    The pair is within CANCEL_BUY_SEARCH_DAYS but falls either side of a chunk
    boundary. Parsing each file alone leaves the cancellation unmatched, which
    is a hard error, so this only works once the files are merged.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "newer.csv").write_text(
        HEADER + "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
    )
    (directory / "older.csv").write_text(
        HEADER + "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
    )

    assert _load(schwab_dir=str(directory)) == []


def test_split_export_matches_the_whole_file(tmp_path: Path) -> None:
    """A split export gives what the same rows give as one file.

    The chunks are named so that sorting by filename yields the OLDEST first,
    which is the opposite of the newest-first order the pipeline needs. Named
    the other way round, filename order would happen to be correct and this
    test would pass against an implementation that never sorts by date.
    """
    whole = tmp_path / "whole.csv"
    whole.write_text(HEADER + NEWER + OLDER)

    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "a_2023.csv").write_text(HEADER + OLDER)
    (directory / "z_2024.csv").write_text(HEADER + NEWER)

    from_dir = _load(schwab_dir=str(directory))
    from_file = _load(schwab_file=str(whole))

    # repr, not str: str names the file each row was read from, which is
    # exactly what differs between a directory and one whole file.
    assert [repr(transaction) for transaction in from_dir] == [
        repr(transaction) for transaction in from_file
    ]
    assert [transaction.date for transaction in from_dir] == sorted(
        transaction.date for transaction in from_dir
    )


def test_single_file_directory_matches_the_file_through_the_award_path(
    tmp_path: Path,
) -> None:
    """A directory of one file equals that file, awards included.

    Only transactions.csv is copied: the fixture directory also holds
    awards.csv, which the *.csv glob would read as transaction history and
    reject for missing Price and Fees & Comm.

    That fixture's only Stock Plan Activity has a blank Price, so this raises
    "Cannot price a vest" unless awards_prices reaches the row parser.
    """
    fixture = Path("tests") / "schwab" / "data" / "rsu_settlement"
    directory = tmp_path / "schwab"
    directory.mkdir()
    shutil.copy(fixture / "transactions.csv", directory / "transactions.csv")
    awards = str(fixture / "awards.csv")

    from_dir = _load(schwab_dir=str(directory), schwab_award_file=awards)
    from_file = _load(
        schwab_file=str(fixture / "transactions.csv"), schwab_award_file=awards
    )

    assert from_dir
    # repr, not str: str names the file each row was read from, which is
    # exactly what differs between a directory and one whole file.
    assert [repr(transaction) for transaction in from_dir] == [
        repr(transaction) for transaction in from_file
    ]


def test_overlapping_exports_are_refused(tmp_path: Path) -> None:
    """Overlapping exports cannot be deduplicated, so they are refused."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "first.csv").write_text(HEADER + NEWER + OLDER)
    (directory / "second.csv").write_text(HEADER + NEWER)

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "second.csv" in message
    assert "first.csv" in message
    assert "no transaction id" in message


def test_adjacent_exports_are_not_refused(tmp_path: Path) -> None:
    """Consecutive ranges that merely touch in time are fine."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "a_2023.csv").write_text(HEADER + OLDER)
    (directory / "z_2024.csv").write_text(HEADER + NEWER)

    assert len(_load(schwab_dir=str(directory))) == 4


def test_empty_directory_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A directory holding no CSV file reports nothing found."""
    directory = tmp_path / "schwab"
    directory.mkdir()

    with caplog.at_level(logging.WARNING):
        assert _load(schwab_dir=str(directory)) == []

    assert "No transactions detected in directory" in caplog.text


def test_csv_named_subdirectory_is_refused(tmp_path: Path) -> None:
    """A directory matching the CSV glob gets an actionable parsing error."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "archive.csv").mkdir()

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "archive.csv" in message
    assert "not a regular file" in message
    assert "Remove or move it" in message


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="permission bits are POSIX only and root ignores them",
)
def test_unreadable_csv_in_directory_is_refused(tmp_path: Path) -> None:
    """A file the current user cannot open gets a ParsingError, not a crash.

    The directory-named-*.csv case above is one way opening a directory
    entry can fail; a permission-denied file is another. Both must raise
    ParsingError rather than let the underlying OSError escape.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    locked = directory / "locked.csv"
    locked.write_text(HEADER + OLDER)
    locked.chmod(0o000)
    try:
        with pytest.raises(ParsingError) as exc_info:
            _load(schwab_dir=str(directory))
    finally:
        locked.chmod(0o644)

    message = str(exc_info.value)
    assert "locked.csv" in message
    assert "Remove or move it" in message


def test_awards_csv_in_directory_points_to_award_flag(tmp_path: Path) -> None:
    """An award-shaped CSV explains where it belongs."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "awards.csv").write_text(
        "Date,Symbol,FairMarketValuePrice\n2024/01/01,AAPL,$150.00\n"
    )

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "awards.csv" in message
    assert "every *.csv" in message.lower()
    assert "--schwab-award-file" in message
    assert "remove or move this file" in message


def test_foreign_csv_in_directory_says_to_remove_it(tmp_path: Path) -> None:
    """An unrelated CSV explains that directory imports consume every CSV."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "freetrade.csv").write_text("Title,Type,Timestamp\nTrade,BUY,now\n")

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "freetrade.csv" in message
    assert "every *.csv" in message.lower()
    assert "remove or move this file" in message


def test_empty_csv_in_directory_says_to_remove_it(tmp_path: Path) -> None:
    """A zero-byte stray CSV gets the same directory guidance as a bad header."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "empty.csv").write_text("")

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "empty.csv" in message
    assert "is empty" in message
    assert "remove or move this file" in message


def test_directory_of_only_cancelled_buys_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An all-cancelled directory is empty too, and has to say so.

    The rows survive parsing and are only dropped by cancellation matching,
    which runs after the files are merged. Checking for an empty result before
    that would leave this case silent.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(
        HEADER
        + "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        + "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
    )

    with caplog.at_level(logging.WARNING):
        assert _load(schwab_dir=str(directory)) == []

    assert "No transactions detected in directory" in caplog.text


def test_identical_trades_are_not_deduplicated(tmp_path: Path) -> None:
    """Two identical buys on one day are two real trades, not a duplicate.

    A Schwab CSV has no transaction id, so nothing distinguishes a repeated
    row from a genuine repeat. This is why the directory never deduplicates,
    and why overlapping exports are refused instead.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(
        HEADER
        + "02/02/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        + "02/02/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
    )

    assert len(_load(schwab_dir=str(directory))) == 2


def test_file_and_dir_together_are_refused(tmp_path: Path) -> None:
    """Merging a file and a directory would reintroduce undetectable overlap.

    Run the real CLI rather than calling the validator: the check only helps if
    main() actually invokes it, and calling it directly would still pass if that
    call were removed.
    """
    whole = tmp_path / "whole.csv"
    whole.write_text(HEADER + NEWER)
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(HEADER + OLDER)

    result = subprocess.run(
        build_cmd(
            "--year",
            "2023",
            "--schwab-file",
            str(whole),
            "--schwab-dir",
            str(directory),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--schwab-file and --schwab-dir cannot be used together" in result.stderr


def test_each_transaction_records_its_own_file(tmp_path: Path) -> None:
    """Provenance survives the merge: one account, but the right file each.

    load_from_dir does its own reading rather than going through
    load_from_file, so it has to stamp the source itself or every row in a
    directory would come back with no parser, account or file.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "a_2023.csv").write_text(HEADER + OLDER)
    (directory / "z_2024.csv").write_text(HEADER + NEWER)

    transactions = _load(schwab_dir=str(directory))
    sources = []
    for transaction in transactions:
        assert transaction.source is not None, "every row must record its origin"
        sources.append(transaction.source)

    assert {source.file.name for source in sources if source.file} == {
        "a_2023.csv",
        "z_2024.csv",
    }
    assert len({source.account for source in sources}) == 1, (
        "one directory is one declared account boundary"
    )
    assert {source.parser for source in sources} == {"Charles Schwab"}

    # index rises with time, as it does on the single-file path.
    older = [
        source.index
        for source in sources
        if source.file and source.file.name == "a_2023.csv" and source.index is not None
    ]
    assert len(older) == 2, "both rows of the older export are stamped"
    assert older == sorted(older)


def test_overlap_splitting_a_corporate_action_reports_the_overlap(
    tmp_path: Path,
) -> None:
    """The overlap is the fault, so the overlap is what gets reported.

    A Cash Merger and its adjustment share a date, so an overlap is the only
    thing that can put them in different files. Pairing them per file before
    checking for the overlap blames the half that is missing from each file
    and never mentions the redundant export that caused it.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "newer.csv").write_text(
        HEADER + "01/12/2024,Cash Merger,FOO,FOO CORP,,,,$1000.00\n"
    )
    (directory / "older.csv").write_text(
        HEADER + "01/12/2024,Cash Merger Adj,FOO,FOO CORP,,-100,,\n"
    )

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "newer.csv" in message
    assert "older.csv" in message
    assert "no transaction id" in message
    assert "Cash Merger Adj must be preceded" not in message


def test_corporate_action_pair_inside_one_file_still_combines(
    tmp_path: Path,
) -> None:
    """Pairing after the overlap check still pairs what belongs together."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(
        HEADER
        + "01/12/2024,Cash Merger,FOO,FOO CORP,,,,$1000.00\n"
        + "01/12/2024,Cash Merger Adj,FOO,FOO CORP,,-100,,\n"
    )

    (transaction,) = _load(schwab_dir=str(directory))
    assert transaction.quantity == Decimal(100)
    assert transaction.price == Decimal("10.00")


def test_unmatched_cancel_buy_names_its_own_file_and_row(tmp_path: Path) -> None:
    """The row to remove is in one file, and the error has to say which.

    Cancellation matching runs on the merged directory, so the path handed to
    it is the directory. "Remove this row" is unusable unless the error points
    at the file and line the row was read from.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "a_older.csv").write_text(
        HEADER + "01/01/2024,Buy,AAPL,APPLE INC,$1.00,1,$0.00,-$1.00\n"
    )
    (directory / "z_newer.csv").write_text(
        HEADER
        + "01/17/2024,Sell,AAPL,APPLE INC,$1.00,1,$0.00,$1.00\n"
        + "01/16/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
    )

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_dir=str(directory))

    message = str(exc_info.value)
    assert "z_newer.csv" in message
    assert "row 3" in message
    assert "no Buy to match it" in message


def test_load_from_args_refuses_file_and_dir_without_argparse(
    tmp_path: Path,
) -> None:
    """The parser refuses both flags itself, not only through main().

    argparse gives the usage message and exit code 2, but it is main() that
    calls it. Taking the directory branch on its own would drop every
    transaction in the file with nothing said.
    """
    whole = tmp_path / "whole.csv"
    whole.write_text(HEADER + NEWER)
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(HEADER + OLDER)

    with pytest.raises(CgtError, match="cannot be used together"):
        _load(schwab_file=str(whole), schwab_dir=str(directory))


def test_both_flags_are_refused_before_the_award_file_is_read(
    tmp_path: Path,
) -> None:
    """The flag conflict is reported even when the award file is broken too.

    load_from_args used to read --schwab-award-file before checking the
    flags, so a bad award file masked the real problem: fix the argument
    error before doing any I/O on the arguments.
    """
    whole = tmp_path / "whole.csv"
    whole.write_text(HEADER + NEWER)
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(HEADER + OLDER)
    bad_award_file = tmp_path / "bad-award.csv"
    bad_award_file.write_text("")  # would itself raise "CSV file is empty"

    with pytest.raises(CgtError, match="cannot be used together"):
        _load(
            schwab_file=str(whole),
            schwab_dir=str(directory),
            schwab_award_file=str(bad_award_file),
        )


def test_directory_load_honours_the_file_path_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory conventions are read through cls, not hardcoded.

    Schwab cannot inherit BaseDirParser, so glob_dir and file_path_filter are
    declared on it instead. Reading them through cls is what keeps a later
    change to those conventions from passing Schwab by.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "a_2023.csv").write_text(HEADER + OLDER)
    (directory / "z_2024.csv").write_text(HEADER + NEWER)

    assert SchwabParser.glob_dir == "*.csv"
    monkeypatch.setattr(
        SchwabParser,
        "file_path_filter",
        classmethod(lambda cls, file_path: file_path.name != "z_2024.csv"),
    )

    transactions = _load(schwab_dir=str(directory))
    assert {
        transaction.source.file.name
        for transaction in transactions
        if transaction.source and transaction.source.file
    } == {"a_2023.csv"}


def test_export_with_no_rows_is_skipped_not_refused(tmp_path: Path) -> None:
    """A range Schwab had nothing to report is a header and no rows.

    It contributes no dates, so it cannot take part in the overlap check and
    is simply left out rather than treated as an empty directory.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "a_empty.csv").write_text(HEADER)
    (directory / "z_2024.csv").write_text(HEADER + NEWER)

    transactions = _load(schwab_dir=str(directory))
    assert {
        transaction.source.file.name
        for transaction in transactions
        if transaction.source and transaction.source.file
    } == {"z_2024.csv"}
