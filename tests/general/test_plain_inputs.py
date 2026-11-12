"""Tests for broker exports passed as plain INPUTs and recognised by their contents.

The figures each export gives stay with its broker's own tests, which read it
through the broker's option. Comparing a plain INPUT with the option shows the
file reached the same reader, under the same account boundary; it cannot show
the figures are right, because both runs share the loader. Where a run reads
files together that no existing test reads together, the rows are written out.
"""

from __future__ import annotations

import csv
from decimal import Decimal, localcontext
import io
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser, parse_arguments
from cgt_calc.cli import main
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.broker_registry import BrokerRegistry
from cgt_calc.transaction_dumper import DUMP_FIELDS, dump_transactions
from tests.hl.test_hl import _create_buy_pdf, _create_sell_pdf

if TYPE_CHECKING:
    from collections.abc import Callable

TESTS = Path(__file__).resolve().parents[1]
SCHWAB = TESTS / "schwab" / "data"
MSSB = TESTS / "morgan_stanley" / "data"
SHARESIGHT = TESTS / "sharesight" / "data" / "inputs"
T212 = TESTS / "trading212" / "data"
RAW = TESTS / "raw" / "data" / "test_data.csv"
ERI = TESTS.parent / "cgt_calc" / "resources" / "eri" / "blackrock_eri.csv"
NOT_RECOGNISED = (
    "cgt-calc recognises an export by its column headings, so a file from a "
    "report or broker it does not read, or one without its heading row, is not "
    "recognised."
)


def _load(*argv: str | Path) -> list[BrokerTransaction]:
    """Parse the arguments as the CLI does and load every broker input."""
    parser = create_parser()
    args = parse_arguments(parser, ["--year", "2023", *map(str, argv)])
    return BrokerRegistry.load_all_transactions(args, IsinConverter())


def _dump(transactions: list[BrokerTransaction]) -> list[dict[str, str]]:
    """Return the dump of the transactions, every column of every row.

    Each run counts its account boundaries on from where the last stopped, so
    each token becomes the order it first appears in: rows from one boundary
    still share one, and rows from two do not.
    """
    stream = io.StringIO(newline="")
    dump_transactions(transactions, stream)
    tokens: dict[str, str] = {}
    rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
    for row in rows:
        row["source"] = re.sub(
            r"#\d+",
            lambda match: f"#{tokens.setdefault(match[0], str(len(tokens)))}",
            row["source"],
        )
    return rows


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the line printed for each plain INPUT."""
    return [
        message.replace(str(TESTS.parent) + "/", "")
        for message in caplog.messages
        if message.startswith(("Recognised", "Skipped"))
    ]


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *argv: str | Path,
    stdin: str | None = None,
) -> tuple[int, str]:
    """Run cgt-calc in this process and return its exit status and stderr.

    Only for runs that stop before the calculation: no cache is read or saved
    unless the test names one.
    """
    given = [str(value) for value in argv]
    defaults = [
        f"{option}="
        for option in (
            "--exchange-rates-file",
            "--isin-translation-file",
            "--spin-offs-file",
        )
        if option not in given
    ]
    monkeypatch.setattr(
        sys, "argv", ["cgt-calc", "--year", "2023", "--no-report", *defaults, *given]
    )
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with localcontext():
        status = main()
    return status, capsys.readouterr().err.replace(str(TESTS.parent) + "/", "")


def _hl_folder(folder: Path) -> Path:
    """Put the HL Transaction Summary and the two contract notes it names in a folder."""
    folder.mkdir()
    shutil.copy2(
        TESTS / "hl" / "data" / "inputs" / "hl-transaction-summary.csv", folder
    )
    _create_buy_pdf(str(folder / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(folder / "S302087055_SOLD_Vanguard.pdf"))
    return folder


@pytest.mark.parametrize(
    ("option", "plain", "lines"),
    [
        pytest.param(
            ["--schwab-file", SCHWAB / "schwab_transactions.csv"],
            [SCHWAB / "schwab_transactions.csv"],
            [
                (
                    "Recognised tests/schwab/data/schwab_transactions.csv as Charles "
                    "Schwab transaction history"
                )
            ],
            id="Schwab transactions",
        ),
        pytest.param(
            [
                "--schwab-award-file",
                SCHWAB / "equity_award/schwab_equity_award_v2.json",
            ],
            [SCHWAB / "equity_award/schwab_equity_award_v2.json"],
            [
                (
                    "Recognised tests/schwab/data/equity_award/schwab_equity_award_v2.json "
                    "as a Charles Schwab Equity Awards export"
                )
            ],
            id="Schwab complete award JSON",
        ),
        pytest.param(
            [
                "--schwab-award-file",
                SCHWAB / "equity_award/schwab_equity_award_v1.json",
            ],
            [SCHWAB / "equity_award/schwab_equity_award_v1.json"],
            [
                (
                    "Recognised tests/schwab/data/equity_award/schwab_equity_award_v1.json "
                    "as a Charles Schwab Equity Awards export"
                )
            ],
            id="Schwab award JSON, first schema",
        ),
        pytest.param(
            ["--schwab-award-file", SCHWAB / "equity_award/schwab_equity_award_v2.csv"],
            [SCHWAB / "equity_award/schwab_equity_award_v2.csv"],
            [
                (
                    "Recognised tests/schwab/data/equity_award/schwab_equity_award_v2.csv "
                    "as a Charles Schwab Equity Awards export"
                )
            ],
            id="Schwab complete award CSV",
        ),
        pytest.param(
            [
                "--schwab-file",
                SCHWAB / "rsu_settlement/transactions.csv",
                "--schwab-award-file",
                SCHWAB / "rsu_settlement/awards.csv",
            ],
            [SCHWAB / "rsu_settlement"],
            [
                (
                    "Recognised tests/schwab/data/rsu_settlement/awards.csv as a "
                    "Charles Schwab Equity Awards export"
                ),
                (
                    "Recognised tests/schwab/data/rsu_settlement/transactions.csv as "
                    "Charles Schwab transaction history"
                ),
            ],
            id="Schwab folder with award-price CSV",
        ),
        pytest.param(
            ["--freetrade-file", TESTS / "freetrade/data/transactions.csv"],
            [TESTS / "freetrade/data/transactions.csv"],
            ["Recognised tests/freetrade/data/transactions.csv as a Freetrade export"],
            id="Freetrade",
        ),
        pytest.param(
            [
                "--interactive-brokers-file",
                TESTS / "interactive_brokers/data/test_basic.csv",
            ],
            [TESTS / "interactive_brokers/data/test_basic.csv"],
            [
                (
                    "Recognised tests/interactive_brokers/data/test_basic.csv as an "
                    "Interactive Brokers Transaction History"
                )
            ],
            id="Interactive Brokers",
        ),
        pytest.param(
            ["--mssb-dir", MSSB],
            [MSSB],
            [
                (
                    "Skipped tests/morgan_stanley/data/Releases Net Shares Report.csv: "
                    "a Morgan Stanley report cgt-calc does not read"
                ),
                (
                    "Recognised tests/morgan_stanley/data/Releases Report.csv as a "
                    "Morgan Stanley Releases Report"
                ),
                (
                    "Skipped tests/morgan_stanley/data/Withdrawal Wire Report.csv: "
                    "a Morgan Stanley report cgt-calc does not read"
                ),
                (
                    "Recognised tests/morgan_stanley/data/Withdrawals Report.csv as a "
                    "Morgan Stanley Withdrawals Report"
                ),
            ],
            id="Morgan Stanley download",
        ),
        pytest.param(
            ["--revolut-file", TESTS / "revolut/data/transactions.csv"],
            [TESTS / "revolut/data/transactions.csv"],
            [
                (
                    "Recognised tests/revolut/data/transactions.csv as a Revolut "
                    "account statement"
                )
            ],
            id="Revolut",
        ),
        pytest.param(
            ["--sharesight-dir", SHARESIGHT],
            [SHARESIGHT],
            [
                (
                    "Recognised tests/sharesight/data/inputs/All Trades Report - Test.csv "
                    "as a Sharesight All Trades Report"
                ),
                (
                    "Recognised tests/sharesight/data/inputs/Taxable Income Report - "
                    "Test.csv as a Sharesight Taxable Income Report"
                ),
            ],
            id="Sharesight",
        ),
        pytest.param(
            ["--trading212-dir", T212 / "2020"],
            [T212 / "2020/from_2020-09-11_to_2021-04-02.csv"],
            [
                (
                    "Recognised tests/trading212/data/2020/from_2020-09-11_to_2021-04-02.csv"
                    " as a Trading 212 export"
                )
            ],
            id="Trading 212, Time column",
        ),
        pytest.param(
            ["--trading212-dir", T212 / "2026/inputs"],
            [T212 / "2026/inputs/transactions.csv"],
            [
                (
                    "Recognised tests/trading212/data/2026/inputs/transactions.csv as a "
                    "Trading 212 export"
                )
            ],
            id="Trading 212, Time (UTC) column",
        ),
        pytest.param(
            ["--vanguard-file", TESTS / "vanguard/data/cash_investment_report.csv"],
            [TESTS / "vanguard/data/cash_investment_report.csv"],
            [
                (
                    "Recognised tests/vanguard/data/cash_investment_report.csv as a "
                    "Vanguard report"
                )
            ],
            id="Vanguard",
        ),
        pytest.param(
            ["--raw-file", RAW],
            [RAW],
            ["Recognised tests/raw/data/test_data.csv as a RAW format file"],
            id="RAW",
        ),
    ],
)
def test_an_export_given_as_input_is_read_as_its_option_reads_it(
    caplog: pytest.LogCaptureFixture,
    option: list[str | Path],
    plain: list[Path],
    lines: list[str],
) -> None:
    """Each export is named once, then read as if its option had been given.

    The option run prints no such line: its file is not looked at first.
    """
    with caplog.at_level(logging.INFO):
        expected = _dump(_load(*option))
        assert _lines(caplog) == []
        assert _dump(_load(*plain)) == expected
    assert _lines(caplog) == lines


def test_hl_csv_and_its_contract_notes_given_as_inputs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The CSV is read as `--hl-dir` reads it, and each PDF is said to go with it.

    The summary is the Windows-1252 file HL writes, so this is also the one
    export recognised when it is not UTF-8.
    """
    folder = _hl_folder(tmp_path / "hl")

    with caplog.at_level(logging.INFO):
        expected = _dump(_load("--hl-dir", folder))
        files = sorted(folder.iterdir())
        assert _dump(_load(*files)) == expected

    shown = str(folder).replace(str(TESTS.parent) + "/", "")
    assert _lines(caplog) == [
        (
            f"Recognised {shown}/B302087054_BOUGHT_Vanguard.pdf as a Hargreaves "
            "Lansdown contract note, read with the Transaction Summary in its folder"
        ),
        (
            f"Recognised {shown}/S302087055_SOLD_Vanguard.pdf as a Hargreaves "
            "Lansdown contract note, read with the Transaction Summary in its folder"
        ),
        (
            f"Recognised {shown}/hl-transaction-summary.csv as a Hargreaves Lansdown "
            "Transaction Summary"
        ),
    ]


def test_a_folder_gives_its_json_exports_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Schwab's Equity Awards site saves JSON, so a folder's .json files count."""
    award = SCHWAB / "equity_award/schwab_equity_award_v2.json"
    shutil.copy(award, tmp_path / "awards.json")

    with caplog.at_level(logging.INFO):
        expected = _dump(_load("--schwab-award-file", tmp_path / "awards.json"))
        assert _dump(_load(tmp_path)) == expected

    assert _lines(caplog) == [
        f"Recognised {tmp_path}/awards.json as a Charles Schwab Equity Awards export"
    ]


def test_trading212_exports_given_as_files_are_one_account() -> None:
    """Two exports of one account are read together, as their folder would be.

    The first file holds 4 rows and the second 5. FOO's split is closed on
    2024-06-10 in the first and opened the same instant in the second; BAR's
    is closed and opened in the second. Each split is one event made of two
    rows, so 9 rows give 7 transactions. Read as two accounts, FOO's split
    would have half of it in each and could not be paired.
    """
    folder = T212 / "splits/inputs"
    transactions = _load(*sorted(folder.iterdir()))

    assert [(row.date.isoformat(), row.action, row.symbol) for row in transactions] == [
        ("2024-06-01", ActionType.TRANSFER, None),
        ("2024-06-03", ActionType.BUY, "FOO"),
        ("2024-06-04", ActionType.BUY, "BAR"),
        ("2024-06-10", ActionType.STOCK_SPLIT, "FOO"),
        ("2024-07-15", ActionType.STOCK_SPLIT, "BAR"),
        ("2024-09-02", ActionType.SELL, "FOO"),
        ("2024-10-02", ActionType.SELL, "BAR"),
    ]
    assert len({row.source.account for row in transactions if row.source}) == 1


def test_sharesight_reports_are_read_whatever_their_names(tmp_path: Path) -> None:
    """Each report goes to its reader by its contents, not by its name.

    The All Trades Report lists 13 trades. The Taxable Income Report pays a
    local dividend of 3.00 on FUND1.UKF and a foreign one of 5.00 on
    FOO.NASDAQ, from which 0.75 was withheld.
    """
    shutil.copy(SHARESIGHT / "All Trades Report - Test.csv", tmp_path / "trades.csv")
    shutil.copy(
        SHARESIGHT / "Taxable Income Report - Test.csv", tmp_path / "income.csv"
    )

    transactions = _load(tmp_path / "trades.csv", tmp_path / "income.csv")

    trades = [
        row
        for row in transactions
        if row.action in {ActionType.BUY, ActionType.SELL, ActionType.STOCK_ACTIVITY}
    ]
    income = [
        (row.date.isoformat(), row.action, row.symbol, row.amount)
        for row in transactions
        if row not in trades
    ]
    assert len(trades) == 13
    assert income == [
        ("2020-10-10", ActionType.DIVIDEND, "FOO.NASDAQ", Decimal("5.00")),
        ("2020-10-10", ActionType.DIVIDEND_TAX, "FOO.NASDAQ", Decimal("-0.75")),
        ("2020-11-10", ActionType.DIVIDEND, "FUND1.UKF", Decimal("3.00")),
    ]


def test_a_renamed_morgan_stanley_report_is_read_as_the_report_it_holds(
    tmp_path: Path,
) -> None:
    """A second download saved with " (1)" is still read.

    `--mssb-dir` reads a report only under its own name. The Withdrawals
    Report sells 2 GOOG on 2021-04-01 and 30 on 2023-02-07; the first is
    before the 20-for-1 split of 2022-07-15, which the report leaves in
    pre-split units, so it is read as 40.
    """
    renamed = tmp_path / "Withdrawals Report (1).csv"
    shutil.copy(MSSB / "Withdrawals Report.csv", renamed)

    sales = [
        (row.date.isoformat(), row.quantity)
        for row in _load(renamed)
        if row.action is ActionType.SELL and row.symbol == "GOOG"
    ]

    assert sales == [("2021-04-01", Decimal(40)), ("2023-02-07", Decimal(30))]


def test_a_vest_in_a_folder_is_priced_from_the_award_file_beside_it() -> None:
    """The award-price CSV in the folder prices the main history's vest.

    The history posts a vest of 200 BAR on 2023-08-18 "as of 08/15/2023";
    the award file states the lapse on 2023-08-15 at $140.35. Without the
    award file the vest could not be priced at all.
    """
    vests = [
        (row.date.isoformat(), row.quantity, row.price)
        for row in _load(SCHWAB / "rsu_settlement")
        if row.action is ActionType.STOCK_ACTIVITY
    ]

    assert vests == [("2023-08-15", Decimal(200), Decimal("140.35"))]


def test_schwab_exports_that_overlap_are_refused_as_from_their_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Several main histories are read as `--schwab-dir` reads them."""
    for name in ("a.csv", "b.csv"):
        shutil.copy(SCHWAB / "schwab_transactions.csv", tmp_path / name)

    status, err = _cli(monkeypatch, capsys, tmp_path / "a.csv", tmp_path / "b.csv")

    # Both are the one fixture, whose rows run from 2016-03-01 to 2021-02-03.
    assert status == 1
    assert (
        "b.csv: has transactions from 2016-03-01 to 2021-02-03, overlapping a.csv "
        "(2016-03-01 to 2021-02-03)."
    ) in err


def test_a_raw_file_on_standard_input_is_recognised_and_read(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """What recognition read is what the reader reads: stdin cannot be read twice.

    The file has a header and 16 rows. Sorted by date, the first is the
    purchase of 1 AMZN at 2480.00 on 2022-05-03, and the last the 2023-02-09
    dividend on OPRA, its bottom and top rows.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(RAW.read_text(encoding="utf-8")))

    with caplog.at_level(logging.INFO):
        transactions = _load("-")

    assert _lines(caplog) == ["Recognised standard input as a RAW format file"]
    assert len(transactions) == 16
    first, last = transactions[0], transactions[-1]
    assert (first.date.isoformat(), first.action, first.symbol, first.price) == (
        "2022-05-03",
        ActionType.BUY,
        "AMZN",
        Decimal(2480),
    )
    assert (last.date.isoformat(), last.action, last.symbol) == (
        "2023-02-09",
        ActionType.DIVIDEND,
        "OPRA",
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX only")
def test_a_pipe_given_as_input_is_read_once(tmp_path: Path) -> None:
    """A pipe, such as `<(convert.py)` in a shell, cannot be read twice either.

    A second read would find no writer and wait; the test opens the pipe once
    more to end that wait, and fails.
    """
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)

    def write() -> None:
        with pipe.open("w", encoding="utf-8") as file:
            file.write(RAW.read_text(encoding="utf-8"))

    loaded: list[list[BrokerTransaction]] = []
    threading.Thread(target=write, daemon=True).start()
    reader = threading.Thread(target=lambda: loaded.append(_load(pipe)), daemon=True)
    reader.start()
    reader.join(10)
    if reader.is_alive():
        pipe.open("w", encoding="utf-8").close()
        reader.join(10)
        pytest.fail("the pipe was read a second time")

    assert len(loaded[0]) == 16


def test_a_raw_heading_in_quotes_after_spaces_is_recognised(tmp_path: Path) -> None:
    """The RAW reader skips a space after a comma, and so does recognition."""
    text = RAW.read_text(encoding="utf-8").split("\n", 1)[1]
    heading = ", ".join(
        f'"{name}"' for name in RAW.read_text().split("\n")[0].split(",")
    )
    spaced = tmp_path / "spaced.csv"
    spaced.write_text(f"{heading}\n{text}", encoding="utf-8")

    assert len(_load(spaced)) == 16


@pytest.mark.parametrize(
    ("make", "problem"),
    [
        pytest.param(
            lambda folder: _write(folder / "a.csv", "a,b\n1,2\n"),
            f"a.csv is not recognised.\n  {NOT_RECOGNISED}",
            id="unknown",
        ),
        pytest.param(
            # Revolut's reader infers a missing header, but only with its option.
            lambda folder: _write(
                folder / "a.csv",
                (TESTS / "revolut/data/transactions.csv")
                .read_text(encoding="utf-8")
                .split("\n", 1)[1],
            ),
            f"a.csv is not recognised.\n  {NOT_RECOGNISED}",
            id="without its header row",
        ),
        pytest.param(
            # Longer than the csv module lets a cell be.
            lambda folder: _write(folder / "a.csv", '"' + "x" * 200_000),
            f"a.csv is not recognised.\n  {NOT_RECOGNISED}",
            id="quote left open",
        ),
        pytest.param(
            lambda folder: _write(folder / "a.csv", ""),
            "a.csv is empty, so cgt-calc cannot tell which broker it is from.",
            id="empty",
        ),
        pytest.param(
            lambda folder: _write(
                folder / "a.csv",
                (SHARESIGHT / "All Trades Report - Test.csv").read_text()
                + (SHARESIGHT / "Taxable Income Report - Test.csv").read_text(),
            ),
            "a.csv matches more than one format: a Sharesight All Trades Report, "
            "a Sharesight Taxable Income Report.",
            id="two exports in one file",
        ),
        pytest.param(
            lambda folder: _copy(ERI, folder / "a.csv"),
            "a.csv is ERI data, not a broker export: pass it with --eri-raw-file.",
            id="ERI data",
        ),
        pytest.param(
            lambda folder: _copy(
                TESTS / "general/data/example_isin_translation.csv", folder / "a.csv"
            ),
            "a.csv is an ISIN translation file, not a broker export: pass it with "
            "--isin-translation-file.",
            id="ISIN translation",
        ),
        pytest.param(
            lambda folder: _write(folder / "a.csv", "ISIN, symbol\n"),
            "a.csv is an ISIN translation file, not a broker export: pass it with "
            "--isin-translation-file.",
            id="ISIN translation, space after the comma",
        ),
        pytest.param(
            lambda folder: _copy(
                TESTS.parent / "cgt_calc/resources/share_prices.csv", folder / "a.csv"
            ),
            "a.csv is a share prices file, not a broker export: pass it with "
            "--prices-file.",
            id="share prices",
        ),
        pytest.param(
            lambda folder: _copy(TESTS / "exchange_rates_data.csv", folder / "a.csv"),
            "a.csv is an exchange rates cache, not a broker export: pass it with "
            "--exchange-rates-file.",
            id="exchange rates, after comment lines",
        ),
        pytest.param(
            lambda folder: _write(folder / "a.csv", "src,dst\nOLD,NEW\n"),
            "a.csv is a spin-offs file, not a broker export: pass it with "
            "--spin-offs-file.",
            id="spin-offs",
        ),
        pytest.param(
            lambda folder: _write(folder / "a.csv", ",".join(DUMP_FIELDS) + "\n"),
            "a.csv is the --dump-transactions output of cgt-calc, not a broker "
            "export: it is not read back: pass the exports it was written from.",
            id="transaction dump",
        ),
        pytest.param(
            lambda folder: (folder / "a.xlsx").write_bytes(b"PK\x03\x04rest"),
            "a.xlsx is a spreadsheet workbook. Save the worksheet as a CSV file "
            "and pass that.",
            id="workbook",
        ),
        pytest.param(
            lambda folder: (folder / "a.pdf").write_bytes(b"%PDF-1.4\n"),
            "a.pdf is a PDF. cgt-calc reads a PDF only as a Hargreaves Lansdown "
            "contract note, in the folder of the Transaction Summary passed with it.",
            id="PDF without an HL summary",
        ),
        pytest.param(
            lambda folder: (folder / "a.csv").write_bytes(
                RAW.read_text().encode("utf-16")
            ),
            "While parsing a.csv: this file is not UTF-8 text. Save it as UTF-8, "
            "or use the original file if another program re-saved it.",
            id="UTF-16 with a byte-order mark",
        ),
        pytest.param(
            lambda folder: (folder / "a.csv").write_bytes(
                RAW.read_text().encode("utf-16-le")
            ),
            "While parsing a.csv: this file is not UTF-8 text. Save it as UTF-8, "
            "or use the original file if another program re-saved it.",
            id="UTF-16 without a byte-order mark",
        ),
        pytest.param(
            # 0x81 is in neither UTF-8 nor Windows-1252.
            lambda folder: (folder / "a.csv").write_bytes(b"a,b\n\x81\n"),
            "While parsing a.csv: this file is not UTF-8 text. Save it as UTF-8, "
            "or use the original file if another program re-saved it.",
            id="neither UTF-8 nor Windows-1252",
        ),
        pytest.param(
            # Only a Hargreaves Lansdown summary is read in Windows-1252.
            lambda folder: (folder / "a.csv").write_bytes(RAW.read_bytes() + b"\xa3\n"),
            "While parsing a.csv: this file is not UTF-8 text. Save it as UTF-8, "
            "or use the original file if another program re-saved it.",
            id="Windows-1252 and not from HL",
        ),
        pytest.param(
            lambda folder: (folder / "sub").mkdir(),
            "sub holds no CSV or JSON file, so no broker can be named for it.",
            id="folder without an export",
        ),
    ],
)
def test_an_input_cgt_calc_cannot_read_as_an_export_stops_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make: Callable[[Path], object],
    problem: str,
) -> None:
    """Each such INPUT is named with what it is, or why it is not recognised."""
    monkeypatch.chdir(tmp_path)
    make(Path())
    (argument,) = Path().iterdir()

    status, err = _cli(monkeypatch, capsys, argument)

    assert status == 1
    assert f"ERROR: cgt-calc cannot use every INPUT:\n  {problem}" in err


@pytest.mark.parametrize(
    ("argv", "problem"),
    [
        pytest.param(
            [RAW, TESTS / "raw/data/test_data_2.csv"],
            "tests/raw/data/test_data.csv and tests/raw/data/test_data_2.csv are "
            "each a RAW format file, and cgt-calc reads one per run. Pass the one "
            "that holds the whole history.",
            id="second file of a one-file format",
        ),
        pytest.param(
            ["--raw-file", RAW, TESTS / "raw/data/test_data_2.csv"],
            "tests/raw/data/test_data_2.csv is a RAW format file, but --raw-file "
            "already gives that input. Give it one way: every such file with the "
            "option, or every one as INPUT.",
            id="file option and an input for it",
        ),
        pytest.param(
            [
                "--trading212-dir",
                T212 / "2024/inputs",
                T212 / "2026/inputs/transactions.csv",
            ],
            "tests/trading212/data/2026/inputs/transactions.csv is a Trading 212 "
            "export, but --trading212-dir already gives that input. Give it one "
            "way: every such file with the option, or every one as INPUT.",
            id="folder option and an input for it",
        ),
        pytest.param(
            [
                "--schwab-award-file",
                SCHWAB / "rsu_settlement/awards.csv",
                SCHWAB / "equity_award/schwab_equity_award_v2.json",
            ],
            "tests/schwab/data/equity_award/schwab_equity_award_v2.json is a "
            "Charles Schwab Equity Awards export, but --schwab-award-file already "
            "gives that input. Give it one way: every such file with the option, "
            "or every one as INPUT.",
            id="award option and an award input",
        ),
        pytest.param(
            [T212 / "2024/inputs", T212 / "2024/inputs/transactions.csv"],
            "tests/trading212/data/2024/inputs/transactions.csv is given twice, by "
            "tests/trading212/data/2024/inputs and by "
            "tests/trading212/data/2024/inputs/transactions.csv. Give each file once.",
            id="same file twice",
        ),
        pytest.param(
            ["--freetrade-file", RAW, RAW],
            "tests/raw/data/test_data.csv is given twice, by --freetrade-file and by "
            "tests/raw/data/test_data.csv. Give each file once.",
            id="same file with an option and as input",
        ),
    ],
)
def test_inputs_that_conflict_stop_the_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str | Path],
    problem: str,
) -> None:
    """A file is read once, by one route; nothing is merged or dropped."""
    monkeypatch.chdir(TESTS.parent)
    relative = [
        value.relative_to(TESTS.parent) if isinstance(value, Path) else value
        for value in argv
    ]

    status, err = _cli(monkeypatch, capsys, *relative)

    assert status == 1
    assert f"ERROR: cgt-calc cannot use every INPUT:\n  {problem}\n" in err


def test_an_award_input_goes_with_the_main_history_option(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The award file and the main history are two inputs of one broker.

    Given one with its option and the other as INPUT, the run is the one both
    options give.
    """
    main_history = SCHWAB / "rsu_settlement/transactions.csv"
    awards = SCHWAB / "rsu_settlement/awards.csv"

    with caplog.at_level(logging.INFO):
        expected = _dump(
            _load("--schwab-file", main_history, "--schwab-award-file", awards)
        )
        assert _dump(_load("--schwab-file", main_history, awards)) == expected

    assert _lines(caplog) == [
        (
            "Recognised tests/schwab/data/rsu_settlement/awards.csv as a Charles "
            "Schwab Equity Awards export"
        )
    ]


def test_two_morgan_stanley_reports_of_one_kind_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two reports never hold one transaction, so nothing checks a repeat."""
    copy = tmp_path / "Releases Report (1).csv"
    shutil.copy(MSSB / "Releases Report.csv", copy)

    status, err = _cli(monkeypatch, capsys, MSSB / "Releases Report.csv", copy)

    assert status == 1
    assert (
        f"  tests/morgan_stanley/data/Releases Report.csv and {copy} are each a "
        "Morgan Stanley Releases Report, and cgt-calc reads one per run."
    ) in err


def test_standard_input_beside_another_file_of_its_kind_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Several Schwab histories are read as their folder is, from files only."""
    history = SCHWAB / "schwab_transactions.csv"

    status, err = _cli(
        monkeypatch,
        capsys,
        "-",
        SCHWAB / "2023/transactions.csv",
        stdin=history.read_text(encoding="utf-8"),
    )

    assert status == 1
    assert (
        "  Standard input holds Charles Schwab transaction history, which "
        "cgt-calc reads together with the other files of that kind only from "
        "files: save it to a file and pass that.\n"
    ) in err


def test_an_award_file_on_standard_input_prices_the_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recognition reads stdin once and hands what it read to the award reader.

    The vest of 200 BAR is priced at $140.35 on 2023-08-15, from the award
    file alone, as in the folder test above.
    """
    awards = SCHWAB / "rsu_settlement/awards.csv"
    monkeypatch.setattr(sys, "stdin", io.StringIO(awards.read_text(encoding="utf-8")))

    vests = [
        (row.date.isoformat(), row.price)
        for row in _load(SCHWAB / "rsu_settlement/transactions.csv", "-")
        if row.action is ActionType.STOCK_ACTIVITY
    ]

    assert vests == [("2023-08-15", Decimal("140.35"))]


def test_standard_input_holding_a_folder_export_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Trading 212 is read from a folder, so its export cannot come on stdin."""
    export = (T212 / "2024/inputs/transactions.csv").read_text(encoding="utf-8")

    status, err = _cli(monkeypatch, capsys, "-", stdin=export)

    assert status == 1
    assert (
        "  Standard input holds a Trading 212 export, which cgt-calc reads only "
        "from files: save it to a file and pass that.\n"
    ) in err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        pytest.param(
            ["-", "-"],
            "'-' reads from stdin and can be given only once, but was given 2 "
            "times: as INPUT, INPUT",
            id="two inputs",
        ),
        pytest.param(
            ["--raw-file", "-", "-"],
            "'-' reads from stdin and can be given only once, but was given 2 "
            "times: as --raw-file, INPUT",
            id="option and input",
        ),
        pytest.param(
            ["--", "-weird.csv"],
            "argument INPUT: path does not exist: '-weird.csv'",
            id="name starting with a dash after --",
        ),
        pytest.param(
            ["--", "--year", "2024"],
            "argument INPUT: path does not exist: '--year'",
            id="option name after --",
        ),
    ],
)
def test_arguments_cgt_calc_refuses_before_reading(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    message: str,
) -> None:
    """Every word after `--` is an INPUT, on every Python cgt-calc supports."""
    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, capsys, *argv, stdin="")

    assert exc.value.code == 2
    assert capsys.readouterr().err.endswith(f"cgt-calc: error: {message}\n")


def test_award_price_csv_alone_prices_and_imports_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """As with the option: the vests it prices are in a history not given."""
    with caplog.at_level(logging.INFO):
        assert _load(SCHWAB / "rsu_settlement/awards.csv") == []
    assert _lines(caplog) == [
        (
            "Recognised tests/schwab/data/rsu_settlement/awards.csv as a Charles "
            "Schwab Equity Awards export"
        )
    ]


def test_lapse_only_award_json_alone_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """As with the option: it prices a history and imports nothing of its own."""
    status, err = _cli(monkeypatch, capsys, SCHWAB / "lapse_pricing/awards.json")

    assert status == 1
    assert "every row in it is a Lapse" in err


def test_complete_award_export_beside_a_main_history_is_flagged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing reconciles the two, as with the options; the warning says how it came."""
    award = SCHWAB / "equity_award/schwab_equity_award_v2.json"

    with caplog.at_level(logging.WARNING):
        _load(award, SCHWAB / "schwab_transactions.csv")

    assert (
        f"{award} was imported as INPUT alongside the main Schwab history."
        in caplog.text
    )


def test_an_option_reads_its_file_without_recognising_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A RAW file given with --revolut-file meets the Revolut reader, unchecked."""
    status, err = _cli(monkeypatch, capsys, "--revolut-file", RAW)

    assert status == 1
    assert "Recognised" not in err
    assert "CSV header mismatch. Missing: 'Currency', 'Date'" in err


def _edited(
    source: Path, old: str, new: str, encoding: str = "utf-8"
) -> Callable[[Path], None]:
    """Make a copy of `source` with the first `old` replaced, in its own encoding."""

    def make(path: Path) -> None:
        text = source.read_bytes().decode(encoding).replace(old, new, 1)
        path.write_bytes(text.encode(encoding))

    return make


def _without(source: Path, column: str) -> Callable[[Path], None]:
    """Make a copy of `source` without one of its columns."""

    def make(path: Path) -> None:
        with source.open(encoding="utf-8", newline="") as file:
            rows = list(csv.reader(file))
        index = rows[0].index(column)
        with path.open("w", encoding="utf-8", newline="") as file:
            csv.writer(file).writerows(
                [*row[:index], *row[index + 1 :]] for row in rows
            )

    return make


T212_2024 = T212 / "2024/inputs/transactions.csv"


def _vanguard_without_tables(path: Path) -> None:
    """Rename a heading of each Vanguard table, so that neither table is found."""
    text = (TESTS / "vanguard/data/cash_investment_report.csv").read_text("utf-8")
    text = text.replace("Amount,Balance", "Amount,Total", 1)
    path.write_text(text.replace("Price,Cost", "Price,Value", 1), encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "make", "option", "refusal"),
    [
        pytest.param(
            "a.csv",
            _edited(SCHWAB / "schwab_transactions.csv", "Fees & Comm", "Fees"),
            "--schwab-file",
            "Missing columns in Schwab transaction file: Fees & Comm.",
            id="Schwab transactions",
        ),
        pytest.param(
            "a.csv",
            _edited(
                SCHWAB / "rsu_settlement/awards.csv", "FairMarketValuePrice", "Price"
            ),
            "--schwab-award-file",
            "This is not a Schwab Equity Awards export cgt-calc reads.",
            id="Schwab award CSV",
        ),
        pytest.param(
            "a.json",
            _edited(
                SCHWAB / "equity_award/schwab_equity_award_v2.json",
                '"Transactions"',
                '"Items"',
            ),
            "--schwab-award-file",
            "Expected top level field (transactions, Transactions) not found",
            id="Schwab award JSON",
        ),
        pytest.param(
            "a.csv",
            _edited(TESTS / "freetrade/data/transactions.csv", "Venue", "Place"),
            "--freetrade-file",
            "Missing columns: Venue",
            id="Freetrade",
        ),
        pytest.param(
            "a.csv",
            _edited(
                TESTS / "hl/data/inputs/hl-transaction-summary.csv",
                "Value (£)",
                "Value",
                "windows-1252",
            ),
            "--hl-dir",
            "CSV header mismatch. Missing: 'Value (£)'. Extra: 'Value'.",
            id="Hargreaves Lansdown",
        ),
        pytest.param(
            "a.csv",
            _edited(
                TESTS / "interactive_brokers/data/test_basic.csv",
                "Commission,",
                "Fee,",
            ),
            "--interactive-brokers-file",
            "CSV header mismatch. Missing: 'Commission'. Extra: 'Fee'.",
            id="Interactive Brokers",
        ),
        pytest.param(
            "Releases Report.csv",
            _edited(MSSB / "Releases Report.csv", "Vest Date", "Date"),
            "--mssb-dir",
            "CSV header mismatch. Missing: 'Vest Date'. Extra: 'Date'.",
            id="Morgan Stanley",
        ),
        pytest.param(
            "a.csv",
            _edited(TESTS / "revolut/data/transactions.csv", "FX Rate", "Rate"),
            "--revolut-file",
            "CSV header mismatch. Missing: 'FX Rate'. Extra: 'Rate'.",
            id="Revolut",
        ),
        pytest.param(
            "All Trades Report.csv",
            _edited(
                SHARESIGHT / "All Trades Report - Test.csv", "Market,Code", "Place,Code"
            ),
            "--sharesight-dir",
            "No row has the headings that start the All Trades table",
            id="Sharesight All Trades Report",
        ),
        *(
            pytest.param(
                "a.csv",
                _without(T212_2024, column),
                "--trading212-dir",
                f"row 1: Missing column(s) {missing}",
                id=f"Trading 212 without {column}",
            )
            for column, missing in (
                ("Action", "Action"),
                ("Ticker", "Ticker"),
                ("Name", "Name"),
                ("Currency (Price / share)", "Currency (Price / share)"),
                ("Time", "Time or Time (UTC)"),
            )
        ),
        pytest.param(
            "a.csv",
            _vanguard_without_tables,
            "--vanguard-file",
            "row 5: Found a row outside the Cash Transactions table",
            id="Vanguard",
        ),
        pytest.param(
            "a.csv",
            _edited(RAW, "fees", "fee"),
            "--raw-file",
            "Expected column 6 to be 'fees' but found 'fee'",
            id="RAW",
        ),
        pytest.param(
            "a.csv",
            _edited(ERI, "Currency", "Ccy"),
            "--eri-raw-file",
            "Unknown columns: Ccy",
            id="ERI data",
        ),
        pytest.param(
            "a.csv",
            _edited(
                TESTS / "general/data/example_isin_translation.csv", "symbol", "ticker"
            ),
            "--isin-translation-file",
            "This line must be the header 'ISIN,symbol'",
            id="ISIN translation",
        ),
        pytest.param(
            "a.csv",
            _edited(
                TESTS.parent / "cgt_calc/resources/share_prices.csv", "date,", "day,"
            ),
            "--prices-file",
            "This line must be the header 'date,symbol,price'",
            id="share prices",
        ),
        pytest.param(
            "a.csv",
            # The comment lines above the header name its columns too.
            _edited(
                TESTS / "exchange_rates_data.csv",
                "\nmonth,currency,rate",
                "\nmonth,currency,value",
            ),
            "--exchange-rates-file",
            "This line must be the header 'month,currency,rate'",
            id="exchange rates",
        ),
        pytest.param(
            "a.csv",
            lambda path: _write(path, "dst,source\nNEW,OLD\n"),
            "--spin-offs-file",
            "This line must be the header 'dst,src'",
            id="spin-offs",
        ),
    ],
)
def test_one_check_decides_a_file_for_its_option_and_as_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    make: Callable[[Path], None],
    option: str,
    refusal: str,
) -> None:
    """A file that fails a format's own check fails it by either route.

    Given with the option, its reader refuses it with the message that names
    what is wrong. Given as INPUT, it is not recognised as that format: the
    reader's check is the one recognition asks, not a copy of it.
    """
    folder = tmp_path / "in"
    folder.mkdir()
    path = folder / name
    make(path)

    status, err = _cli(
        monkeypatch, capsys, option, folder if option.endswith("-dir") else path
    )
    assert status == 1
    assert refusal in err

    status, err = _cli(monkeypatch, capsys, path)
    assert status == 1
    assert f"\n  {path} is not recognised.\n" in err


def test_income_report_sections_decide_the_report_by_either_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The income reader reads the sections it knows, and nothing else.

    Its directory option reads a report with neither section as no income,
    and recognition does not take it for the report.
    """
    folder = tmp_path / "in"
    folder.mkdir()
    path = folder / "Taxable Income Report.csv"
    text = (SHARESIGHT / "Taxable Income Report - Test.csv").read_text("utf-8")
    _write(
        path,
        text.replace("Local Income,", "Local,").replace("Foreign Income,", "Abroad,"),
    )

    assert _load("--sharesight-dir", folder) == []
    status, err = _cli(monkeypatch, capsys, path)
    assert status == 1
    assert f"\n  {path} is not recognised.\n" in err


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _copy(source: Path, target: Path) -> None:
    shutil.copy(source, target)
