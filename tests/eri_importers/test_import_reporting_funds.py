"""Tests for the script that writes HMRC's reporting fund dates."""

from __future__ import annotations

import csv
import itertools
from pathlib import Path
import sys
from xml.sax.saxutils import escape
import zipfile

import pytest

from cgt_calc import resources
from scripts import import_reporting_funds

HEADER = [
    "Letter Group",
    "Reporting Fund Ref",
    "Parent Fund",
    "HMRC Share Class Ref No.'s",
    "Sub Fund Name",
    "ISIN No",
    "CUSIP No",
    "Reporting Fund, with effect from",
    "Ceased to be an RF on",
]


def entry(isin: str, start: str, end: str) -> list[str]:
    """Return one share class row of HMRC's list."""
    return [
        "A",
        "A0001",
        "A Fund plc",
        "A0001-0001",
        "A Fund",
        isin,
        "no data",
        start,
        end,
    ]


def write_ods(path: Path, rows: list[list[str]]) -> None:
    """Write `rows` as a spreadsheet laid out as HMRC's file is.

    Equal neighbouring cells are stored once with a repeat count, and a run of
    blank cells ends each row. A row given as [] has no cells at all.
    """
    xml_rows = []
    for row in rows:
        cells = []
        for value, group in itertools.groupby(row):
            count = len(list(group))
            repeat = f' table:number-columns-repeated="{count}"' if count > 1 else ""
            cells.append(
                f'<table:table-cell office:value-type="string"{repeat}>'
                f"<text:p>{escape(value)}</text:p></table:table-cell>"
            )
        if row:
            cells.append('<table:table-cell table:number-columns-repeated="16375"/>')
        xml_rows.append(f"<table:table-row>{''.join(cells)}</table:table-row>")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?><office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<office:body><office:spreadsheet><table:table table:name="Weblist">'
        f"{''.join(xml_rows)}</table:table></office:spreadsheet></office:body>"
        "</office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", content)


def run_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[list[str]]
) -> Path:
    """Run the script on a spreadsheet of `rows`, writing into `tmp_path`."""
    ods = tmp_path / "Master-Weblist.ods"
    write_ods(ods, rows)
    output = tmp_path / "reporting_funds.csv"
    monkeypatch.setattr(import_reporting_funds, "OUTPUT_FILE", output)
    monkeypatch.setattr(sys, "argv", ["import_reporting_funds.py", str(ods)])
    import_reporting_funds.main()
    return output


def test_writes_hmrc_dates_for_each_bundled_fund(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each bundled fund HMRC dates gets one row, its entries merged.

    The ISINs are bundled funds from five providers' ERI files; the dates are
    HMRC's, some changed or split to make each case. A fund listed more than
    once takes its earliest start, and an end date only when every entry has
    one, the latest of them. A fund with an entry whose start is "To Be
    Confirmed" gets no row, even when another entry has a date.
    """
    output = run_script(
        tmp_path,
        monkeypatch,
        [
            HEADER,
            # Lower case and padded, as HMRC writes some: still IE00B3XXRP09.
            entry(" ie00b3xxrp09\N{NO-BREAK SPACE}", "24/05/2012", "no data"),
            entry("IE00B2NPL135", "10/07/2010", "14/08/2025"),
            # No start date: no row.
            entry("IE00BYZNF849", "To Be Confirmed", "no data"),
            # An unconfirmed entry beside a dated one: still no row.
            entry("IE00B3VVMM84", "To Be Confirmed", "28/01/2022"),
            entry("IE00B3VVMM84", "29/01/2022", "no data"),
            # Overlapping by a day, the second still reporting: no end.
            entry("LU1230136894", "27/05/2015", "10/03/2025"),
            entry("LU1230136894", "10/03/2025", "no data"),
            # Listed out of order and following on (31/12/2015, then
            # 01/01/2016): start 2012-09-01, end 2019-12-03.
            entry("LU0871639380", "01/01/2016", "03/12/2019"),
            entry("LU0871639380", "01/09/2012", "31/12/2015"),
            # The second entry lies inside the first: the end is 2020-12-31,
            # the later of the two, not the end of the entry listed last.
            entry("IE00BQXKVQ19", "05/02/2015", "31/12/2020"),
            entry("IE00BQXKVQ19", "01/01/2017", "30/06/2018"),
            # The entry still reporting comes first: no end, though the
            # later entry has one.
            entry("LU0476289623", "01/01/2011", "no data"),
            entry("LU0476289623", "01/01/2013", "31/12/2014"),
            # Equal start and end are stored as one repeated cell.
            entry("LU2518861054", "07/09/2022", "07/09/2022"),
            # HMRC writes this bundled ISIN in lower case.
            entry("lu2278361055", "30/12/2020", "no data"),
            [],
            # Share classes with no ISIN are not read.
            entry("no data", "To Be Confirmed", "no data"),
        ],
    )

    assert output.read_text(encoding="utf8") == (
        "isin,start,end\n"
        "IE00B2NPL135,2010-07-10,2025-08-14\n"
        "IE00B3XXRP09,2012-05-24,\n"
        "IE00BQXKVQ19,2015-02-05,2020-12-31\n"
        "LU0476289623,2011-01-01,\n"
        "LU0871639380,2012-09-01,2019-12-03\n"
        "LU1230136894,2015-05-27,\n"
        "LU2278361055,2020-12-30,\n"
        "LU2518861054,2022-09-07,2022-09-07\n"
    )
    out = capsys.readouterr().out
    for isin in ("IE00BYZNF849", "IE00B3VVMM84"):
        assert (
            f"WARNING: an entry for {isin} in HMRC's list gives its start as "
            '"To Be Confirmed", so it has no row.\n'
        ) in out
    assert "WARNING: IE00BFMXXD54 is not in HMRC's list, so it has no row.\n" in out
    assert f"Wrote 8 rows to {output}.\n" in out


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        pytest.param(
            [
                HEADER,
                entry("LU0476289623", "01/01/2011", "31/12/2014"),
                entry("LU0476289623", "02/01/2015", "no data"),
            ],
            "LU0476289623: HMRC's list shows it ceased to be a reporting fund on "
            "2014-12-31 and became one again on 2015-01-02. The dates file cannot "
            "record a gap; check the fund's status by hand.",
            id="gap between entries",
        ),
        pytest.param(
            [HEADER, entry("IE00B2NPL135", "10/07/2010", "2025-08-14")],
            "IE00B2NPL135: '2025-08-14' in HMRC's list is not a DD/MM/YYYY date.",
            id="date in another format",
        ),
        pytest.param(
            [
                [*HEADER[:-1], "Ceased to be a reporting fund on"],
                entry("IE00B2NPL135", "10/07/2010", "14/08/2025"),
            ],
            "HMRC's list has no 'Ceased to be an RF on' column.",
            id="renamed column",
        ),
    ],
)
def test_refuses_dates_it_cannot_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rows: list[list[str]],
    message: str,
) -> None:
    """The script stops, and leaves the dates file as it was."""
    with pytest.raises(SystemExit) as exit_info:
        run_script(tmp_path, monkeypatch, rows)

    assert exit_info.value.code == 2
    assert capsys.readouterr().err.endswith(f"error: {message}\n")
    assert not (tmp_path / "reporting_funds.csv").exists()


def test_every_bundled_eri_fund_has_reporting_fund_dates() -> None:
    """The dates file has a row for each bundled fund, except IE00BYZNF849.

    HMRC's list of 2 November 2026 gives that fund's start as "To Be Confirmed".
    """
    folder = Path(resources.__file__).parent
    bundled: set[str] = set()
    for path in (folder / "eri").glob("*.csv"):
        with path.open(encoding="utf8") as file:
            bundled |= {row["ISIN"].upper() for row in csv.DictReader(file)}
    with (folder / "reporting_funds.csv").open(encoding="utf8") as file:
        dated = {row["isin"] for row in csv.DictReader(file)}

    assert dated == bundled - {"IE00BYZNF849"}
