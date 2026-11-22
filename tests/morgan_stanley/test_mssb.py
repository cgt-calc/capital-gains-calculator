"""Tests for Morgan Stanley parser."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import CgtError, ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.mssb import (
    COLUMNS_RELEASE,
    COLUMNS_WITHDRAWAL,
    RELEASES_REPORT_FILENAME,
    UNREAD_HEADERS,
    WITHDRAWALS_REPORT_FILENAME,
    MSSBParser,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_csv(file_path: Path, rows: list[list[str]]) -> None:
    """Write rows to a CSV file."""

    with file_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_read_mssb_transactions_empty_file(tmp_path: Path) -> None:
    """Ensure parser fails fast when the CSV file has no content."""
    empty_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    empty_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    assert "CSV doesn't have a header" in str(exc.value)


def test_read_mssb_release_success(tmp_path: Path) -> None:
    """Parse a valid release report row and return a populated transaction."""

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    rows = [
        COLUMNS_RELEASE,
        [
            "25-Mar-2023",
            "ORDER-1",
            "GSU Class C",
            "Release",
            "Complete",
            "$10.00",
            "3.000",
            "$0.00",
            "3",
            "Fractional Shares",
        ],
    ]
    _write_csv(release_file, rows)

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.symbol == "GOOG"
    assert transaction.action == ActionType.STOCK_ACTIVITY
    assert transaction.quantity == Decimal(3)
    assert transaction.price == Decimal("10.00")
    assert transaction.amount == Decimal(30)


def test_reports_whose_names_differ_only_in_case_are_both_read(tmp_path: Path) -> None:
    """Two releases reports named alike but for their case load as they always have.

    Morgan Stanley is left out of the check for a transaction that two files
    hold, so its directories load as they did before that check. Where the
    file system tells the two names apart, the loader is given two releases
    reports and reads the release of 3 shares in each.
    """
    rows = [
        COLUMNS_RELEASE,
        [
            "25-Mar-2023",
            "ORDER-1",
            "GSU Class C",
            "Release",
            "Complete",
            "$10.00",
            "3.000",
            "$0.00",
            "3",
            "Fractional Shares",
        ],
    ]
    _write_csv(tmp_path / RELEASES_REPORT_FILENAME, rows)
    lower_case_name = tmp_path / RELEASES_REPORT_FILENAME.lower()
    if lower_case_name.exists():
        pytest.skip("this file system does not tell the two names apart")
    _write_csv(lower_case_name, rows)

    transactions = MSSBParser.load_from_dir(tmp_path)

    assert [transaction.quantity for transaction in transactions] == [
        Decimal(3),
        Decimal(3),
    ]


def test_read_mssb_withdrawal_skips_notice(tmp_path: Path) -> None:
    """Ensure withdrawal parser ignores trailing notice rows."""

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "02-Apr-2021",
            "ORDER-2",
            "Cash",
            "Sale",
            "Complete",
            "$1.00",
            "-4,218.95",
            "$4,218.95",
            "0",
            "N/A",
        ],
        [
            "Please note that any Alphabet share sales, transfers, or deposits that occurred on or prior to the July 15, 2022 stock split are reflected in pre-split. Any sales, transfers, or deposits that occurred after July 15, 2022 are in post-split values. For GSU vests, your activity is displayed in post-split values.",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal("-4218.95")
    assert transaction.fees == Decimal(0)


def test_read_mssb_withdrawal_with_uppercase_csv_extension(tmp_path: Path) -> None:
    """Parse a withdrawals report whose CSV extension is uppercase."""
    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME.replace(".csv", ".CSV")
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "02-Apr-2021",
            "ORDER-UPPERCASE",
            "Cash",
            "Sale",
            "Complete",
            "$1.00",
            "-100.00",
            "$100.00",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser.load_from_dir(tmp_path)

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.TRANSFER
    assert transactions[0].amount == Decimal("-100.00")


@pytest.mark.parametrize(
    ("execution_date", "price", "quantity"),
    [
        # The window that went unadjusted while STOCK_SPLIT_INFO dated the
        # split 15 June 2022 instead of 15 July.
        pytest.param("01-Jul-2022", "$2,240.00", "-2.00", id="before-split"),
        # The report notice says sales "on or prior to" 15 July are pre-split.
        pytest.param("15-Jul-2022", "$2,240.00", "-2.00", id="on-split-date"),
        # Later sales are already exported in post-split values.
        pytest.param("18-Jul-2022", "$112.00", "-40.00", id="after-split"),
    ],
)
def test_read_mssb_withdrawal_goog_sale_split_adjustment(
    tmp_path: Path, execution_date: str, price: str, quantity: str
) -> None:
    """Normalise GOOG sales either side of 15 July 2022 to post-split units.

    Morgan Stanley's notice states that sales "on or prior to the July 15,
    2022 stock split are reflected in pre-split" values and later sales in
    post-split values, so only the former are scaled by the split factor.
    """

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            execution_date,
            "ORDER-4",
            "GSU Class C",
            "Sale",
            "Complete",
            price,
            quantity,
            "$4,479.90",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.SELL
    assert transaction.symbol == "GOOG"
    assert transaction.quantity == Decimal("40.00")
    assert transaction.price == Decimal("112.00")


def test_read_mssb_withdrawal_invalid_decimal(tmp_path: Path) -> None:
    """Raise parsing error when numeric values cannot be parsed."""

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "09-Feb-2023",
            "ORDER-3",
            "GSU Class C",
            "Sale",
            "Complete",
            "$105.70",
            "bad",
            "$3,170.93",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in column 'Quantity'" in message


@pytest.mark.parametrize(
    ("filename", "rows", "column"),
    [
        pytest.param(
            RELEASES_REPORT_FILENAME,
            [
                COLUMNS_RELEASE,
                [
                    "2021-03-25",
                    "ORDER-1",
                    "GSU Class C",
                    "Release",
                    "Complete",
                    "$10.00",
                    "3.000",
                    "$0.00",
                    "3",
                    "Fractional Shares",
                ],
            ],
            "Vest Date",
            id="Vest Date",
        ),
        pytest.param(
            WITHDRAWALS_REPORT_FILENAME,
            [
                COLUMNS_WITHDRAWAL,
                [
                    "2021-03-25",
                    "ORDER-3",
                    "GSU Class C",
                    "Sale",
                    "Complete",
                    "$105.70",
                    "30",
                    "$3,170.93",
                    "0",
                    "N/A",
                ],
            ],
            "Execution Date",
            id="Execution Date",
        ),
    ],
)
def test_read_mssb_date_in_another_format_names_its_column(
    tmp_path: Path, filename: str, rows: list[list[str]], column: str
) -> None:
    """A spreadsheet's re-saved date is refused with the format both reports use."""
    _write_csv(tmp_path / filename, rows)

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    assert str(exc.value) == (
        f"While parsing {tmp_path / filename}, row 2: Invalid date in column "
        f"'{column}': '2021-03-25', expected DD-Mon-YYYY, e.g. '25-Mar-2021'"
    )


def test_read_mssb_release_invalid_header(tmp_path: Path) -> None:
    """Error when release report header differs from expected schema."""

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    invalid_header = ["Vest date", *COLUMNS_RELEASE[1:], ""]
    _write_csv(release_file, [invalid_header])

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    # A column with no heading, as a line ending in a comma gives, is named too.
    assert (
        "CSV header mismatch. Missing: 'Vest Date'. Extra: (no name), 'Vest date'."
        in str(exc.value)
    )
    # Morgan Stanley may have renamed the column, so a cgt-calc update may be
    # needed.
    assert exc.value.needs_update


@pytest.mark.parametrize(
    ("filename", "rows"),
    [
        pytest.param(
            RELEASES_REPORT_FILENAME,
            [
                COLUMNS_RELEASE,
                [
                    "25-Mar-2023",
                    "ORDER-1",
                    "GSU Class C",
                    "Dividend",
                    "Complete",
                    "$10.00",
                    "3.000",
                    "$0.00",
                    "3",
                    "Fractional Shares",
                ],
            ],
            id="release",
        ),
        pytest.param(
            WITHDRAWALS_REPORT_FILENAME,
            [
                COLUMNS_WITHDRAWAL,
                [
                    "02-Apr-2021",
                    "ORDER-2",
                    "Cash",
                    "Dividend",
                    "Complete",
                    "$1.00",
                    "-4,218.95",
                    "$4,218.95",
                    "0",
                    "N/A",
                ],
            ],
            id="withdrawal",
        ),
    ],
)
def test_read_mssb_unknown_type_is_marked_as_needing_an_update(
    tmp_path: Path, filename: str, rows: list[list[str]]
) -> None:
    """A type cgt-calc does not know is refused, marked as needing an update."""
    _write_csv(tmp_path / filename, rows)

    with pytest.raises(ParsingError, match=r", row 2: Unknown type: Dividend$") as exc:
        MSSBParser().load_from_dir(tmp_path)

    assert exc.value.needs_update


def test_read_mssb_release_header_only_file(tmp_path: Path) -> None:
    """Raise a clean 'file is empty' error for a header with zero data rows.

    Regression test: `csv.DictReader` has no `__bool__`/`__len__`, so
    `bool(reader)` is always truthy even when the file has no data rows,
    which used to make the "file is empty" check dead code.
    """

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    _write_csv(release_file, [COLUMNS_RELEASE])

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    assert "file is empty" in str(exc.value)


def test_read_mssb_withdrawal_short_row(tmp_path: Path) -> None:
    """Raise a clean ParsingError for a row with fewer fields than the header.

    Regression test: a row with fewer fields than the header gets padded by
    `csv.DictReader` with `None` values for the missing trailing columns
    (rather than being rejected outright). Before the fix, this crashed deep
    in `_parse_decimal` with an unhandled `AttributeError` ('NoneType' object
    has no attribute 'replace') instead of raising a clean ParsingError with
    row/file context.
    """

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        # Only the first 6 columns are provided; Quantity, Net Amount,
        # Net Share Proceeds and Tax Payment Method are missing entirely.
        ["02-Apr-2021", "ORDER-2", "Cash", "Sale", "Complete", "$10.00"],
    ]
    _write_csv(withdrawal_file, rows)

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    # It must be a *clean* ParsingError with row/file context, not an
    # unhandled AttributeError/TypeError bubbling out of the parser.
    assert exc.type is ParsingError
    assert "row 2" in str(exc.value)


def test_mssb_dir_refuses_each_report_under_another_name(tmp_path: Path) -> None:
    """A renamed report would be left out without a word, so each one is named.

    A second download is saved under a name such as `Withdrawals Report 2.csv`.
    Read by name alone, the folder would give the vest in `Releases Report.csv`
    and nothing from the two renamed reports.
    """
    _write_csv(
        tmp_path / RELEASES_REPORT_FILENAME,
        [
            COLUMNS_RELEASE,
            [
                "25-Mar-2023",
                "ORDER-1",
                "GSU Class C",
                "Release",
                "Complete",
                "$10.00",
                "3.000",
                "$0.00",
                "3",
                "Fractional Shares",
            ],
        ],
    )
    _write_csv(
        tmp_path / "Releases Report (1).csv",
        [COLUMNS_RELEASE, ["25-Mar-2023", "ORDER-1", "GSU Class C", "Release"]],
    )
    _write_csv(
        tmp_path / "Withdrawals Report 2.csv",
        [COLUMNS_WITHDRAWAL, ["07-Feb-2023", "ORDER-2", "GSU Class C", "Sale"]],
    )

    with pytest.raises(CgtError) as exc:
        MSSBParser.load_from_dir(tmp_path)

    assert str(exc.value) == (
        "--mssb-dir reads Morgan Stanley reports only under the names Releases "
        "Report.csv and Withdrawals Report.csv, so it would leave out:\n"
        f"  {tmp_path / 'Releases Report (1).csv'} is a Morgan Stanley Releases "
        "Report\n"
        f"  {tmp_path / 'Withdrawals Report 2.csv'} is a Morgan Stanley Withdrawals "
        "Report\n"
        "Keep one copy of each report, holding the whole history, and move the "
        "other copies out of the directory. Then either give each report its name, "
        "or pass the directory as INPUT, without --mssb-dir: that reads a report "
        "whatever its name."
    )


def test_mssb_dir_ignores_every_other_entry_beside_the_reports(tmp_path: Path) -> None:
    """Only a renamed releases or withdrawals report stops the run.

    A renamed copy of a report cgt-calc does not read, a CSV in another
    encoding, one the csv module cannot read and a folder named like a CSV are
    each left alone, as before: only the release of 3 shares is read.
    """
    _write_csv(
        tmp_path / RELEASES_REPORT_FILENAME,
        [
            COLUMNS_RELEASE,
            [
                "25-Mar-2023",
                "ORDER-1",
                "GSU Class C",
                "Release",
                "Complete",
                "$10.00",
                "3.000",
                "$0.00",
                "3",
                "Fractional Shares",
            ],
        ],
    )
    _write_csv(tmp_path / "Withdrawal Wire Report (1).csv", [UNREAD_HEADERS[1]])
    # 0x81 is not UTF-8.
    (tmp_path / "notes.csv").write_bytes(b"Date,Note\n01-Jan-2023,\x81\n")
    # One character past the csv module's default field limit of 131,072.
    (tmp_path / "long.csv").write_text("x" * 131_073 + "\n", encoding="utf-8")
    (tmp_path / "old.csv").mkdir()

    transactions = MSSBParser.load_from_dir(tmp_path)

    assert [(t.date, t.quantity) for t in transactions] == [
        (dt.date(2023, 3, 25), Decimal(3))
    ]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows needs a privilege to make a link and has no read permission bit",
)
def test_mssb_dir_ignores_an_entry_it_cannot_open(tmp_path: Path) -> None:
    """A broken link or a CSV closed to this user is left alone, as before.

    Neither can be read as a report, so only the release of 3 shares is read.
    Run as root, `closed.csv` opens, and is ignored as an unrelated file.
    """
    _write_csv(
        tmp_path / RELEASES_REPORT_FILENAME,
        [
            COLUMNS_RELEASE,
            [
                "25-Mar-2023",
                "ORDER-1",
                "GSU Class C",
                "Release",
                "Complete",
                "$10.00",
                "3.000",
                "$0.00",
                "3",
                "Fractional Shares",
            ],
        ],
    )
    (tmp_path / "gone.csv").symlink_to("missing.csv")
    closed = tmp_path / "closed.csv"
    closed.write_text("Date,Note\n", encoding="utf-8")
    closed.chmod(0)

    transactions = MSSBParser.load_from_dir(tmp_path)

    assert [(t.date, t.quantity) for t in transactions] == [
        (dt.date(2023, 3, 25), Decimal(3))
    ]
