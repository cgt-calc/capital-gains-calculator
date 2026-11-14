"""Tests for Hargreaves Lansdown parser."""

from collections.abc import Callable
import datetime as dt
from decimal import Decimal
import io
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.hl import HargreavesLansdownParser
from tests.general.test_calc import REPORTING_ADVICE, _run_main
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

HL_CSV_HEADER = (
    "Transaction Summary, , , ,\n"
    "Client Name:,Anonymous User, , ,\n"
    "Client Number:,1234567, , ,\n"
    "Valuation as at,09-08-2026 16:40, , ,\n"
    "\n"
    "Trade date,Settle date,Reference,Description,Unit cost (p),Quantity,Value (£),\n"
)

# One purchase, on the line after the heading line (line 6), so row 7.
BUY_ROW = (
    '"24/01/2026","26/01/2026","B302087054","Vanguard FTSE Developed World UCITS '
    'ETF 4563 @  3287.199","3,287.199","4,563","-150,006.84"\n'
)

# The lines of that purchase's contract note that cgt-calc reads, as
# _create_buy_pdf writes them.
BUY_NOTE_LINES = [
    "ISIN: IE00BK5BQV03",
    "STOCK CODE: VHVG",
    "4,563.00 | 3287.199 | 149,994.89",
    "Dealing charge || 11.95",
]


def _create_buy_pdf(filename: str) -> None:
    """Generate a mock HL Buy Contract Note PDF."""
    c = canvas.Canvas(filename, pagesize=letter)
    _, height = letter

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 50, "Hargreaves Lansdown Contract Note")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, "Date 24/02/2026")
    c.drawString(50, height - 90, "Contract Note No. B302087054-02508682")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "**BOUGHT**")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 150, "ISIN: IE00BK5BQV03")
    c.drawString(50, height - 165, "STOCK CODE: VHVG")
    c.drawString(50, height - 180, "Security: Vanguard FTSE Developed World UCITS ETF")

    # Quantity | Price | Consideration
    c.drawString(50, height - 220, "4,563.00 | 3287.199 | 149,994.89")
    c.drawString(50, height - 260, "Dealing charge || 11.95")
    c.drawString(50, height - 290, "Settlement Date: 26/02/2026")

    c.save()


def _create_sell_pdf(filename: str) -> None:
    """Generate a mock HL Sell Contract Note PDF."""
    c = canvas.Canvas(filename, pagesize=letter)
    _, height = letter

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 50, "Hargreaves Lansdown Contract Note")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, "Date 25/02/2026")
    c.drawString(50, height - 90, "Contract Note No. S302087055-04500122")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "**SOLD**")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 150, "ISIN: IE00BK5BQV03")
    c.drawString(50, height - 165, "STOCK CODE: VHVG")
    c.drawString(50, height - 180, "Security: Vanguard FTSE Developed World UCITS ETF")

    # Quantity | Price | Consideration
    c.drawString(50, height - 220, "1,000.00 | 4500.00 | 45,000.00")
    c.drawString(50, height - 260, "Dealing charge || 11.95")
    c.drawString(50, height - 290, "Settlement Date: 27/02/2026")

    c.save()


def _prepare_pdf_test_data() -> str:
    test_dir = Path(__file__).resolve().parent

    source_file = Path(test_dir / "data" / "inputs" / "hl-transaction-summary.csv")

    # Use a stable repo-relative directory: its path is echoed in the parsing
    # output, so a machine-specific temp dir would break the expected-output
    # fixture on any other machine.
    input_dir = Path("out") / "test-hl-inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_file, input_dir)

    _create_buy_pdf(str(input_dir / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(input_dir / "S302087055_SOLD_Vanguard.pdf"))

    return str(input_dir)


def test_hl_parser(request: pytest.FixtureRequest) -> None:
    """Runs the tool and verifies it doesn't fail."""

    test_output_dir = _prepare_pdf_test_data()
    cmd = build_cmd(
        "--year",
        "2025",
        "--hl-dir",
        test_output_dir,
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests")
        / "hl"
        / "data"
        / "test_run_with_hl_files_no_balance_check_output.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_hl_parser_missing_pdf(request: pytest.FixtureRequest) -> None:
    """A trade whose contract note PDF is missing stops the run.

    The fixture's folder holds no PDF. Its first trade is the sale on row 9,
    after the fee and interest rows on rows 7 and 8, which need no note. The
    message says where the note goes, and that HL issues none for some fund
    trades, which then cannot be read from the summary.
    """
    cmd = build_cmd(
        "--year",
        "2025",
        "--hl-dir",
        "tests/hl/data/inputs/",
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    # The path as the program prints it, with this system's separator.
    csv_file = Path("tests") / "hl" / "data" / "inputs" / "hl-transaction-summary.csv"
    assert result.returncode == 1
    assert result.stderr.endswith(
        f"\nERROR: While parsing {csv_file}, "
        "row 9: Cannot find contract note pdf for transaction S302087055. "
        "Download its contract note from HL into this CSV's folder. If HL issued "
        "none, as for some regular fund investments, cgt-calc cannot read this "
        "trade from the Transaction Summary.\n\n"
    )


def _note(path: Path, lines: list[str]) -> None:
    """Write a contract note PDF holding `lines` of text, one under another."""
    c = canvas.Canvas(str(path), pagesize=letter)
    _, height = letter
    for number, line in enumerate(lines):
        c.drawString(50, height - 50 - 15 * number, line)
    c.save()


def _run_with_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_note: Callable[[Path], None],
) -> tuple[int, str, Path]:
    """Run on a folder of one purchase and its note, written by `write_note`.

    Return the exit status, what was printed to stderr and the note's path.
    """
    folder = tmp_path / "h"
    folder.mkdir()
    (folder / "summary.csv").write_text(
        HL_CSV_HEADER + BUY_ROW, encoding="windows-1252"
    )
    note = folder / "B302087054_BOUGHT_Vanguard.pdf"
    write_note(note)
    status = _run_main(
        monkeypatch, "--year", "2025", "--hl-dir", str(folder), "--no-report"
    )
    return status, capsys.readouterr().err, note


def _cut_off(path: Path) -> None:
    """Write the purchase's note, then keep only its first half."""
    _note(path, BUY_NOTE_LINES)
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])


@pytest.mark.parametrize(
    "write_note",
    [
        pytest.param(
            lambda path: path.write_text(
                "<html><body>Service unavailable</body></html>", encoding="utf-8"
            ),
            id="an error page",
        ),
        pytest.param(_cut_off, id="a cut-off download"),
    ],
)
def test_a_contract_note_that_is_not_a_pdf_is_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_note: Callable[[Path], None],
) -> None:
    """A page saved under the note's name, or a download cut off part way.

    Either used to end in "Unexpected error!" with a traceback. The file is
    the user's to replace, so no advice on updating cgt-calc follows.
    """
    status, err, note = _run_with_note(tmp_path, monkeypatch, capsys, write_note)

    assert status == 1
    assert err.endswith(
        f"\nERROR: While parsing {note}: this file is not a PDF cgt-calc can "
        "open. Download the contract note again from HL.\n\n"
    )
    assert "Unexpected error!" not in err


def _scan(path: Path) -> None:
    """Write a one-page note holding a picture and no text, as a scan does."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.rect(50, 400, 300, 200, fill=1)
    c.showPage()
    c.save()


def test_a_contract_note_with_no_text_is_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A page with no text, as a scanned note has, names the note.

    It used to be read as no stock code and nothing bought, and the run
    stopped with "Symbol missing." against the CSV row.
    """
    status, err, note = _run_with_note(tmp_path, monkeypatch, capsys, _scan)

    assert status == 1
    assert err.endswith(
        f"\nERROR: While parsing {note}: could not read the stock code, quantity "
        "and price from this contract note: it has no text cgt-calc can read. "
        "Download it again from HL; a scanned copy cannot be read.\n\n"
    )


@pytest.mark.parametrize(
    ("lines", "missing"),
    [
        pytest.param(
            ["**BOUGHT**", "ISIN: IE00BK5BQV03", "STOCK CODE: VHVG"],
            "quantity and price",
            id="no figures",
        ),
        pytest.param(
            ["**BOUGHT**", "4,563.00 | 3287.199 | 149,994.89"],
            "stock code",
            id="no stock code",
        ),
        pytest.param(
            ["Hargreaves Lansdown Contract Note"],
            "stock code, quantity and price",
            id="neither",
        ),
    ],
)
def test_a_contract_note_in_another_layout_names_what_was_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lines: list[str],
    missing: str,
) -> None:
    """A note with text, but not where cgt-calc looks, may need an update.

    The stock code is the ISIN followed by "STOCK CODE:", and the figures are
    the quantity, price and consideration on one line. Each note leaves out
    what the message names. A note without its figures used to stop with
    "Positive quantity required.", and one without its stock code with
    "Symbol missing.", both against the CSV row.
    """
    status, err, note = _run_with_note(
        tmp_path, monkeypatch, capsys, lambda path: _note(path, lines)
    )

    assert status == 1
    assert err.endswith(
        f"\nERROR: While parsing {note}: could not read the {missing} from this "
        f"contract note.\n\n{REPORTING_ADVICE}"
    )


def test_an_invalid_isin_in_a_contract_note_is_located_by_the_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The error names the note alone, without the CSV's row number.

    It used to say "row 7", the purchase's row in the CSV, after the note's
    name, as if the PDF had rows.
    """
    lines = [line.replace("IE00BK5BQV03", "GB0000000001") for line in BUY_NOTE_LINES]
    status, err, note = _run_with_note(
        tmp_path, monkeypatch, capsys, lambda path: _note(path, lines)
    )

    assert status == 1
    assert err.endswith(
        f"\nERROR: While parsing {note}: Invalid ISIN in contract note: "
        "'GB0000000001'\n\n"
    )


def test_the_date_on_a_contract_note_is_not_read(tmp_path: Path) -> None:
    """The trade date comes from the CSV, so the note's own date is not read.

    The note gives 31 February, a day that does not exist, which used to stop
    the run although the date was never used. The purchase loads with the
    CSV's trade date, 24 January 2026.
    """
    csv_file = tmp_path / "summary.csv"
    csv_file.write_text(HL_CSV_HEADER + BUY_ROW, encoding="windows-1252")
    _note(
        tmp_path / "B302087054_BOUGHT_Vanguard.pdf",
        ["Date 31/02/2025", *BUY_NOTE_LINES],
    )

    (transaction,) = HargreavesLansdownParser.load_from_file(csv_file)

    assert transaction.date == dt.date(2026, 1, 24)


def test_hl_unknown_reference_raises(tmp_path: Path) -> None:
    """Raise ParsingError instead of silently dropping unrecognized rows.

    HL statements routinely contain rows such as dividends or corporate
    actions whose "Reference" code isn't a Buy/Sell reference, INTEREST,
    FPC or MANAGE FEE. Previously these rows were silently skipped, which
    meant real dividend income could vanish from a user's report without
    any indication anything was wrong.
    """
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(
        HL_CSV_HEADER
        + '"04/04/2026","04/04/2026","D302087099","Vanguard FTSE Developed World '
        'UCITS ETF Dividend","n/a","n/a","12.34"\n',
        encoding="windows-1252",
    )

    with pytest.raises(ParsingError) as exc:
        HargreavesLansdownParser().load_from_file(csv_file)

    message = str(exc.value)
    assert "Unknown transaction type" in message
    assert "D302087099" in message
    # A cgt-calc update may read the row, so it is marked as needing one.
    assert exc.value.needs_update


@pytest.mark.parametrize(
    "row",
    [
        '"","","","Balance carried forward","","","0.00"\n',
        '"","","","Balance carried forward"\n',
    ],
    ids=["a cell under each heading", "short of cells"],
)
def test_hl_blank_reference_skipped(tmp_path: Path, row: str) -> None:
    """Rows with a blank reference (e.g. subtotal/summary lines) are skipped.

    Even one short of cells: the width check is for transactions.
    """
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(HL_CSV_HEADER + row, encoding="windows-1252")

    transactions = HargreavesLansdownParser().load_from_file(
        csv_file, warn_on_empty=False
    )

    assert transactions == []


@pytest.mark.parametrize(
    ("row", "message"),
    [
        pytest.param(
            # The description has lost its closing quote, so it runs on into
            # the next cell: 6 cells, with 3.87 under Quantity.
            '"09/03/2026","09/03/2026","INTEREST","Interest From 10/02/2026 TO '
            '09/03/2026,"n/a","n/a","3.87"\n',
            "This row has 6 columns, not 7",
            id="closing quote of the description missing",
        ),
        pytest.param(
            '"09/03/2026","09/03/2026"\n',
            "This row has 2 columns, not 7",
            id="line cut short before its reference",
        ),
        pytest.param(
            # One "n/a" too many puts 3.87 under the heading line's unnamed
            # last column, and "n/a" under Value.
            '"09/03/2026","09/03/2026","INTEREST","Interest From 10/02/2026 TO '
            '09/03/2026","n/a","n/a","n/a","3.87"\n',
            "This row has 8 columns, not 7",
            id="one cell too many",
        ),
        pytest.param(
            '"09/03/2026","09/03/2026","INTEREST","Interest From 10/02/2026 TO '
            '09/03/2026","n/a","n/a",""\n',
            "This row has no value in column 'Value (£)'",
            id="value cell empty",
        ),
    ],
)
def test_a_row_whose_value_is_not_under_its_heading_is_refused(
    tmp_path: Path, row: str, message: str
) -> None:
    """Refuse a row whose cells are not under their headings, or whose value is empty.

    Each row is an edited copy of an interest row of 3.87. Without the check,
    all but the line cut short would load with an amount of 0. The heading line
    names 7 columns (Trade date, Settle date, Reference, Description, Unit
    cost, Quantity, Value) and ends in a comma, which adds an unnamed 8th
    that HL's rows leave out.
    """
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(HL_CSV_HEADER + row, encoding="windows-1252")

    with pytest.raises(ParsingError, match=re.escape(message)) as excinfo:
        HargreavesLansdownParser.load_from_file(csv_file)

    # The heading line is line 6.
    assert excinfo.value.row_index == 7


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(
            HL_CSV_HEADER
            + '"09/03/2026","09/03/2026","INTEREST","Interest From 10/02/2026 TO '
            '09/03/2026","n/a","n/a","3.87",\n',
            id="a comma after the last cell, as on the heading line",
        ),
        pytest.param(
            HL_CSV_HEADER
            + '"09/03/2026","09/03/2026","INTEREST","Interest From 10/02/2026 TO '
            '09/03/2026","n/a","n/a","3.87", \n',
            id="a space after that comma",
        ),
        pytest.param(
            "Trade date,Settle date,Reference,Description,Unit cost (p),Quantity,"
            "Value (£)\n"
            "09/03/2026,09/03/2026,INTEREST,Interest From 10/02/2026 TO 09/03/2026,"
            "n/a,n/a,3.87\n",
            id="saved by a spreadsheet: no quotes and no unnamed column",
        ),
    ],
)
def test_a_row_with_a_cell_under_each_heading_loads(
    tmp_path: Path, content: str
) -> None:
    """Read the value from a row of 7 cells, with or without an empty cell after them.

    A cell of spaces counts as empty.
    """
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(content, encoding="windows-1252")

    transactions = HargreavesLansdownParser.load_from_file(csv_file)

    assert [(t.action, t.description, t.amount) for t in transactions] == [
        (
            ActionType.INTEREST,
            "Interest From 10/02/2026 TO 09/03/2026",
            Decimal("3.87"),
        )
    ]


def test_an_unreadable_trade_date_is_quoted_with_the_format_hl_writes(
    tmp_path: Path,
) -> None:
    """Quote the trade date that cannot be read and name the format HL writes.

    The interest row of 9 March 2026 with its trade date written year first.
    Its settle date stays day first, so the two date cells differ: a parser
    reading the settle date would not refuse the row, and a message quoting
    it would not match. The heading line is line 6, so the row is line 7.
    """
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(
        HL_CSV_HEADER
        + '"2026-03-09","09/03/2026","INTEREST","Interest From 10/02/2026 TO '
        '09/03/2026","n/a","n/a","3.87"\n',
        encoding="windows-1252",
    )

    with pytest.raises(ParsingError) as excinfo:
        HargreavesLansdownParser.load_from_file(csv_file)

    assert str(excinfo.value) == (
        f"While parsing {csv_file}, row 7: Invalid date in column 'Trade date': "
        "'2026-03-09', expected DD/MM/YYYY, e.g. '25/02/2026'"
    )


def test_hl_file_with_a_header_and_no_rows_is_called_a_csv(tmp_path: Path) -> None:
    """The error names the format of the file read, not both formats `--hl-dir` takes."""
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(HL_CSV_HEADER, encoding="windows-1252")

    with pytest.raises(ParsingError, match="Hargreaves Lansdown CSV file is empty"):
        HargreavesLansdownParser.load_from_dir(tmp_path)


def test_load_from_dir_accepts_uppercase_csv_extension(tmp_path: Path) -> None:
    """Discover an HL transaction report whose CSV extension is uppercase."""
    csv_file = tmp_path / "hl-transaction-summary.CSV"
    csv_file.write_text(
        HL_CSV_HEADER
        + '"01/03/2026","01/03/2026","Interest","Gross interest","n/a","n/a","12.34"\n',
        encoding="windows-1252",
    )

    transactions = HargreavesLansdownParser.load_from_dir(tmp_path)

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.INTEREST
    assert transactions[0].amount == Decimal("12.34")


@pytest.mark.parametrize(
    ("heading", "encoding"),
    [
        (HL_CSV_HEADER, "windows-1252"),
        (HL_CSV_HEADER, "utf-8"),
        # Excel's "CSV UTF-8" starts the file with a byte-order mark. Above
        # the preamble, the mark would be skipped along with the preamble, so
        # this file is cut down to its column headings, where the mark sits
        # before "Trade date".
        (HL_CSV_HEADER.splitlines(keepends=True)[-1], "utf-8-sig"),
    ],
    ids=[
        "Windows-1252, as HL writes it",
        "saved again as UTF-8",
        "UTF-8 with a byte-order mark before the headings",
    ],
)
def test_a_transaction_summary_is_read_in_either_encoding(
    tmp_path: Path, heading: str, encoding: str
) -> None:
    """The same row loads from the file HL writes and from a UTF-8 copy of it.

    Read as Windows-1252, the UTF-8 copy is refused for its heading, which
    comes out as "Value (Â£)". Read as UTF-8, the file HL wrote does not
    decode at all. The "é" and "€" in the description are single bytes in
    Windows-1252 that a neighbouring code page reads differently: Latin-1 has
    no "€", and the Greek page has another letter in place of "é".
    """
    (tmp_path / "hl-transaction-summary.csv").write_text(
        heading
        + '"09/03/2026","09/03/2026","INTEREST","Interest, Nestlé SA € bond","n/a",'
        '"n/a","3.87"\n',
        encoding=encoding,
    )

    transactions = HargreavesLansdownParser.load_from_dir(tmp_path)

    assert [(t.description, t.amount) for t in transactions] == [
        ("Interest, Nestlé SA € bond", Decimal("3.87"))
    ]


def test_real_preamble_preserves_physical_row_numbers(tmp_path: Path) -> None:
    """Count HL's preamble when recording transaction source lines."""
    csv_file = tmp_path / "hl-transaction-summary.csv"
    shutil.copy2(
        Path("tests/hl/data/inputs/hl-transaction-summary.csv"),
        csv_file,
    )
    _create_buy_pdf(str(tmp_path / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(tmp_path / "S302087055_SOLD_Vanguard.pdf"))

    transactions = HargreavesLansdownParser.load_from_file(csv_file)

    rows = {
        transaction.date.isoformat(): transaction.source.row
        for transaction in transactions
        if transaction.source
    }
    assert rows["2026-04-04"] == 7
    assert rows["2026-03-09"] == 8


def test_an_empty_line_does_not_shift_the_numbers_of_the_rows_below(
    tmp_path: Path,
) -> None:
    """A row is numbered by its line in the file, the empty lines above included."""
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(
        HL_CSV_HEADER
        + '"02/03/2026","02/03/2026","Interest","Later interest","n/a","n/a","1.00"\n'
        + "\n"
        + '"xx/03/2026","01/03/2026","Interest","Earlier interest","n/a","n/a","1.00"\n',
        encoding="windows-1252",
    )

    with pytest.raises(ParsingError, match="Invalid date") as excinfo:
        HargreavesLansdownParser.load_from_file(csv_file)

    # The header is on line 6 and the empty line on line 8.
    assert excinfo.value.row_index == 9


HL_FEE_ROW = (
    '"04/04/2026","04/04/2026","MANAGE FEE","HL Fund & Share Account To A/C '
    '01C0450818220","n/a","n/a","-4.05"\n'
)
HL_INTEREST_ROW = (
    '"09/03/2026","09/03/2026","INTEREST","Interest From 10/02/2026 TO 09/03/2026",'
    '"n/a","n/a","3.87"\n'
)
HL_SELL_ROW = (
    '"25/02/2026","27/02/2026","S302087055","Vanguard FTSE Developed World UCITS '
    'ETF 1000 @  4500.00","4,500.000","1,000","44,988.05"\n'
)
HL_BUY_ROW = (
    '"24/01/2026","26/01/2026","B302087054","Vanguard FTSE Developed World UCITS '
    'ETF 4563 @  3287.199","3,287.199","4,563","-150,006.84"\n'
)
HL_RECEIPT_ROW = (
    '"24/01/2026","24/01/2026","FPC","HL Fund & Share Account Receipt Pay By Bank",'
    '"n/a","n/a","50,000.00"\n'
)


def test_load_from_dir_refuses_a_copy_of_a_report_saved_as_utf8(
    tmp_path: Path,
) -> None:
    """A second copy of a report stops the load, though its bytes differ.

    The copy is the same text saved as UTF-8, as a spreadsheet program does.
    The directory is read in name order, so the copy is read first and the
    report is refused at its oldest row, the receipt on line 9.
    """
    report = tmp_path / "summary.csv"
    report.write_text(
        HL_CSV_HEADER + HL_FEE_ROW + HL_INTEREST_ROW + HL_RECEIPT_ROW,
        encoding="windows-1252",
    )
    copy = tmp_path / "summary copy.csv"
    copy.write_text(report.read_text(encoding="windows-1252"), encoding="utf-8")

    with pytest.raises(
        ParsingError, match="Two files in the directory hold this transaction"
    ) as excinfo:
        HargreavesLansdownParser.load_from_dir(tmp_path)

    message = str(excinfo.value)
    assert message.startswith(f"While parsing {report}: ")
    assert f'Receipt Pay By Bank", read from row 9 of {copy}\n' in message
    assert f'Receipt Pay By Bank", read from row 9 of {report}\n' in message


def test_load_from_dir_refuses_reports_whose_periods_overlap(tmp_path: Path) -> None:
    """A sale that two reports both hold stops the load, and both are named.

    The later report was downloaded second, so the browser added " (1)" to
    its name and it is read first. A third report, read between the two,
    holds interest that neither has. The earlier report then holds a receipt
    and a purchase that are in no other file, and the sale of 25 February,
    which is on line 8 of the later report and line 7 of this one.
    """
    later = tmp_path / "summary (1).csv"
    later.write_text(HL_CSV_HEADER + HL_FEE_ROW + HL_SELL_ROW, encoding="windows-1252")
    (tmp_path / "summary (2).csv").write_text(
        HL_CSV_HEADER + HL_INTEREST_ROW, encoding="windows-1252"
    )
    earlier = tmp_path / "summary.csv"
    earlier.write_text(
        HL_CSV_HEADER + HL_SELL_ROW + HL_BUY_ROW + HL_RECEIPT_ROW,
        encoding="windows-1252",
    )
    _create_buy_pdf(str(tmp_path / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(tmp_path / "S302087055_SOLD_Vanguard.pdf"))

    with pytest.raises(
        ParsingError, match="Two files in the directory hold this transaction"
    ) as excinfo:
        HargreavesLansdownParser.load_from_dir(tmp_path)

    message = str(excinfo.value)
    assert message.startswith(f"While parsing {earlier}: ")
    assert f"(ISIN IE00BK5BQV03), read from row 8 of {later}\n" in message
    assert f"(ISIN IE00BK5BQV03), read from row 7 of {earlier}\n" in message
    assert message.count("2026-02-25 Sell 1000 VHVG") == 2
    # The rows only one of the reports holds are not what stopped it.
    assert "Receipt Pay By Bank" not in message
    assert "To A/C 01C0450818220" not in message


def test_load_from_dir_reads_reports_with_no_transaction_in_common(
    tmp_path: Path,
) -> None:
    """Rows that differ in one thing only are two transactions, and both load.

    The two receipts differ only in their description, and the two payments
    of interest only in their value.
    """
    (tmp_path / "first.csv").write_text(
        HL_CSV_HEADER + HL_INTEREST_ROW + HL_RECEIPT_ROW, encoding="windows-1252"
    )
    (tmp_path / "second.csv").write_text(
        HL_CSV_HEADER
        + HL_INTEREST_ROW.replace("3.87", "3.88")
        + HL_RECEIPT_ROW.replace("Pay By Bank", "Pay By Cheque"),
        encoding="windows-1252",
    )

    transactions = HargreavesLansdownParser.load_from_dir(tmp_path)

    assert [
        (transaction.description, transaction.amount) for transaction in transactions
    ] == [
        ("HL Fund & Share Account Receipt Pay By Bank", Decimal("50000.00")),
        ("HL Fund & Share Account Receipt Pay By Cheque", Decimal("50000.00")),
        ("Interest From 10/02/2026 TO 09/03/2026", Decimal("3.87")),
        ("Interest From 10/02/2026 TO 09/03/2026", Decimal("3.88")),
    ]


def test_load_from_dir_counts_each_of_two_matching_rows_in_one_file(
    tmp_path: Path,
) -> None:
    """Two payments of one amount on one day, in one report, are both real."""
    (tmp_path / "first.csv").write_text(
        HL_CSV_HEADER + HL_RECEIPT_ROW + HL_RECEIPT_ROW, encoding="windows-1252"
    )
    (tmp_path / "second.csv").write_text(
        HL_CSV_HEADER + HL_INTEREST_ROW, encoding="windows-1252"
    )

    transactions = HargreavesLansdownParser.load_from_dir(tmp_path)

    assert [transaction.amount for transaction in transactions] == [
        Decimal("50000.00"),
        Decimal("50000.00"),
        Decimal("3.87"),
    ]


# The tests below call the parser directly: the end-to-end tests above
# run in a subprocess, which is invisible to coverage.


def test_read_row_interest() -> None:
    """Read an interest row without a contract note."""
    row = {
        "Reference": "Interest",
        "Description": "Gross interest",
        "Trade date": "01/03/2026",
        "Value (£)": "12.34",
    }

    transaction = HargreavesLansdownParser.read_row(row, Path("statement.csv"))

    assert transaction is not None
    assert transaction.action == ActionType.INTEREST
    assert transaction.amount == Decimal("12.34")
    assert transaction.currency == "GBP"
    assert transaction.fees == Decimal(0)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-a-number"])
def test_read_row_unreadable_value(value: str) -> None:
    """Refuse a value column that is not a finite amount, naming the column."""
    row = {
        "Reference": "Interest",
        "Description": "Gross interest",
        "Trade date": "01/03/2026",
        "Value (\u00a3)": value,
    }

    with pytest.raises(ValueError, match=r"Invalid decimal in column 'Value"):
        HargreavesLansdownParser.read_row(row, Path("statement.csv"))


def test_read_row_transfer_with_na_value() -> None:
    """Read a fee row where the value column is n/a."""
    row = {
        "Reference": "MANAGE FEE",
        "Description": "Management fee",
        "Trade date": "01/03/2026",
        "Value (£)": "n/a",
    }

    transaction = HargreavesLansdownParser.read_row(row, Path("statement.csv"))

    assert transaction is not None
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal(0)


def test_read_row_blank_reference_returns_none() -> None:
    """Skip rows without a reference."""
    row = {"Reference": " ", "Description": "x", "Trade date": "01/03/2026"}

    assert HargreavesLansdownParser.read_row(row, Path("statement.csv")) is None


def test_read_row_buy_with_uppercase_contract_note_extension(tmp_path: Path) -> None:
    """Cross-reference a buy row with an uppercase PDF extension."""
    _create_buy_pdf(str(tmp_path / "B123_contract.PDF"))
    row = {
        "Reference": "B123",
        "Description": "Buy VHVG",
        "Trade date": "24/02/2026",
        "Value (£)": "149,994.89",
    }

    transaction = HargreavesLansdownParser.read_row(row, tmp_path / "statement.csv")

    assert transaction is not None
    assert transaction.action == ActionType.BUY
    assert transaction.symbol == "VHVG"
    assert transaction.isin == "IE00BK5BQV03"
    assert transaction.quantity == Decimal("4563.00")
    # Prices in the contract note are in pence.
    assert transaction.price == Decimal("32.87199")
    assert transaction.fees == Decimal("11.95")


def test_read_transactions_reverses_order() -> None:
    """Return rows in reverse file order."""
    content = HL_CSV_HEADER + (
        "02/03/2026,02/03/2026,Interest,Later interest,n/a,n/a,20.00,\n"
        "01/03/2026,01/03/2026,Interest,Earlier interest,n/a,n/a,10.00,\n"
    )

    transactions = HargreavesLansdownParser.read_transactions(
        io.StringIO(content), Path("statement.csv")
    )

    assert [transaction.description for transaction in transactions] == [
        "Earlier interest",
        "Later interest",
    ]


def test_pre_reading_missing_header() -> None:
    """Raise when the trade date header cannot be found."""
    file = io.StringIO("no,header,here\n1,2,3\n")

    with pytest.raises(ParsingError, match="Could not find the 'Trade date' header"):
        list(HargreavesLansdownParser.pre_reading(file, Path("statement.csv")))
