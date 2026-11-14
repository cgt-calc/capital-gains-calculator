"""Write HMRC's reporting fund dates for the bundled ERI funds into the resources.

HMRC's list of reporting funds is a monthly spreadsheet at
https://www.gov.uk/government/publications/approved-offshore-reporting-funds.
Contains public sector information licensed under the Open Government Licence v3.0.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import csv
import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING
import zipfile

from defusedxml import ElementTree as ET

from cgt_calc import resources
from cgt_calc.const import ERI_RESOURCE_FOLDER
from cgt_calc.parsers.eri.raw import ERIRawParser

if TYPE_CHECKING:
    from collections.abc import Generator

RESOURCES = Path(resources.__file__).parent
OUTPUT_FILE = RESOURCES / "reporting_funds.csv"
COLUMNS = ("ISIN No", "Reporting Fund, with effect from", "Ceased to be an RF on")
NO_START = "To Be Confirmed"
NO_END = "no data"

_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def read_rows(ods: Path) -> Generator[list[str]]:
    """Yield the text of each row of the spreadsheet's cells."""
    with zipfile.ZipFile(ods) as archive, archive.open("content.xml") as content:
        # The sheet is about 150 MB of XML, so read it a row at a time.
        for _, element in ET.iterparse(content):
            if element.tag != _TABLE + "table-row":
                continue
            row: list[str] = []
            for cell in element:
                text = "".join(cell.itertext()).strip()
                # Equal neighbouring cells are stored once; the blank cells
                # after the last column repeat thousands of times.
                repeat = int(cell.get(_TABLE + "number-columns-repeated", "1"))
                row += [text] * min(repeat, 16)
            element.clear()
            yield row


def read_date(isin: str, text: str) -> dt.date:
    """Read one of HMRC's DD/MM/YYYY dates."""
    try:
        return dt.datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        raise ValueError(
            f"{isin}: '{text}' in HMRC's list is not a DD/MM/YYYY date."
        ) from None


def merge(isin: str, entries: list[tuple[str, str]]) -> tuple[dt.date, dt.date | None]:
    """Return the start and end date of an ISIN HMRC lists once or more.

    Entries that overlap or follow on make one period: the earliest start,
    and an end date only if every entry has one. A gap between an end and the
    next start, when the fund left the regime and came back, is refused.
    """
    periods = sorted(
        (
            (read_date(isin, start), None if end == NO_END else read_date(isin, end))
            for start, end in entries
        ),
        key=lambda period: period[0],
    )
    start, end = periods[0]
    for next_start, next_end in periods[1:]:
        if end is not None and next_start > end + dt.timedelta(days=1):
            raise ValueError(
                f"{isin}: HMRC's list shows it ceased to be a reporting fund on "
                f"{end} and became one again on {next_start}. The dates file "
                "cannot record a gap; check the fund's status by hand."
            )
        end = None if end is None or next_end is None else max(end, next_end)
    return start, end


def build_rows(ods: Path, isins: set[str]) -> list[list[str]]:
    """Return a row of HMRC's dates for each of `isins` that HMRC dates."""
    entries: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    # Closing the reader closes the file when a check stops it part-way.
    with closing(read_rows(ods)) as rows:
        header = next(rows)
        for column in COLUMNS:
            if column not in header:
                raise ValueError(f"HMRC's list has no '{column}' column.")
        isin_at, start_at, end_at = (header.index(column) for column in COLUMNS)
        for row in rows:
            row += [""] * (len(header) - len(row))
            # HMRC writes some ISINs in lower case or with spaces around them.
            if (isin := row[isin_at].upper()) in isins:
                entries[isin].append((row[start_at], row[end_at]))

    output = []
    for isin in sorted(isins):
        if isin not in entries:
            print(f"WARNING: {isin} is not in HMRC's list, so it has no row.")
        elif any(start == NO_START for start, _ in entries[isin]):
            # Its dated entries alone could give too late a start.
            print(
                f"WARNING: an entry for {isin} in HMRC's list gives its start as "
                f'"{NO_START}", so it has no row.'
            )
        else:
            start, end = merge(isin, entries[isin])
            output.append([isin, start.isoformat(), end.isoformat() if end else ""])
    return output


def bundled_isins() -> set[str]:
    """Return the ISIN of every fund with bundled ERI data."""
    return {
        str(transaction.isin)
        for path in sorted((RESOURCES / ERI_RESOURCE_FOLDER).glob("*.csv"))
        for transaction in ERIRawParser.load_from_file(path, show_parsing_msg=False)
    }


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Write HMRC's reporting fund dates for the bundled ERI funds."
    )
    parser.add_argument(
        "hmrc_list", type=Path, help="HMRC's list of reporting funds (.ods)"
    )
    args = parser.parse_args()
    try:
        rows = build_rows(args.hmrc_list, bundled_isins())
    except ValueError as err:
        parser.error(str(err))
    with OUTPUT_FILE.open("w", encoding="utf8", newline="") as fout:
        csv.writer(fout, lineterminator="\n").writerows(
            [["isin", "start", "end"], *rows]
        )
    print(f"Wrote {len(rows)} rows to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
