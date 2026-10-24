"""Xtrackers ERI transaction parser."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, override

import dateutil.parser as date_parser
import pdfplumber

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import CurrencyCode, Isin
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.util import is_blank_row, round_decimal

from .model import ERIImporter, ERIImporterOutput

if TYPE_CHECKING:
    import datetime as dt
    from pathlib import Path

    from pdfplumber.page import CroppedPage, Page
    from pdfplumber.pdf import PDF

LOGGER = logging.getLogger(__name__)

# Matches both manually renamed reports and the official download names
# from https://etf.dws.com/en-gb/information/etf-documents/reportings/,
# e.g. Reporting-Funds-Xtrackers-II-2024.pdf or Xtrackers II - Investor Report (Client) - 2025.pdf
REPORT_FILE_REGEX = re.compile(
    r"^(Xtrackers.*Report.*|Reporting-Funds-Xtrackers.*)\.pdf$"
)

XTRACKERS_ERI_FILENAME = "xtrackers_eri.csv"

# Updated to support blank, not-applicable and Nil values sometimes found in the new reports
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

# Column indices configuration for Xtrackers tables
ISIN_COLUMN = 0
CURRENCY_COLUMN = 1
ERI_COLUMN = 2
MIN_VALID_YEAR = 2018

# Row bounding boxes are derived from detected lines, not from the
# full extent of the text in the row: descenders / the last line of a
# multi-line header can protrude one point below the bounding box.
# Cropping exactly at the bounding-box bottom therefore includes a
# slice of header text in the data area, which then merges into the
# first data row (confirmed 2026-10-24: adding 1 point to the header
# bounding-box bottom fixes all pages). Crop a little below the header instead.
HEADER_CROP_EPSILON = 1.0

# Number of columns a complete header map must contain (ISIN, currency, ERI)
EXPECTED_HEADER_COLUMNS = 3

# A column is trusted / realigned only if it is valid in at least
# this fraction of the rows with a valid ISIN
COLUMN_MATCH_THRESHOLD = 0.5


class XtrackersImporter(ERIImporter):
    """Parser for Xtrackers ERI reports.

    Covers the three Xtrackers umbrellas published by DWS: Xtrackers
    (summary format), and Xtrackers II / Xtrackers (IE) Plc (investor
    report format).
    """

    def __init__(self) -> None:
        """Create a new Xtrackers Parser instance."""
        super().__init__(name="Xtrackers")

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse the input PDF file and return ERI transactions."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None

        result = ERIImporterOutput(
            transactions=[], output_file_name=XTRACKERS_ERI_FILENAME
        )

        with pdfplumber.open(file) as pdf:
            if not pdf.pages:
                return None
            parsers = [
                XtrackersImporter._parse_summary_format,
                XtrackersImporter._parse_investor_report_format,
            ]
            for parser in parsers:
                parsed_data = parser(pdf, file)
                if parsed_data is not None:
                    result.transactions = parsed_data
                    break
            else:
                return None
        LOGGER.info("Done parsing Xtrackers file")
        return result

    @staticmethod
    def _extract_report_end_date(page: Page, prefix: str) -> dt.date | None:
        text_lines = [line["text"] for line in page.extract_text_lines()]

        # The old code sliced line[len(prefix):], which is only correct if
        # the prefix is at the very start of the line, and then kept only
        # the first word, so "31 December 2024" became "31" (parsed as
        # today in the current month/year). Find the prefix wherever it
        # occurs, strip an optional colon, and parse the whole remainder.
        for i, line in enumerate(text_lines):
            # Treat a trailing colon in the prefix as optional: 2024
            # reports sometimes write "Period of account ended" with
            # no colon, which would otherwise never match.
            base_prefix = prefix.rstrip(":")
            idx = line.lower().find(base_prefix.lower())
            if idx < 0:
                continue
            date_str = line[idx + len(base_prefix) :].strip().lstrip(":").strip()
            if not date_str and i + 1 < len(text_lines):
                # Check the next line if the current line only contained the prefix
                date_str = text_lines[i + 1].strip()
            if not date_str:
                continue
            try:
                date = date_parser.parse(date_str, fuzzy=True, dayfirst=True)
                return date.date()
            except (date_parser.ParserError, ValueError, OverflowError):
                # Fall back to an explicit date pattern in the remainder,
                # e.g. 31/12/2025, 31.12.2025 or 31 December 2024.
                match = re.search(
                    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
                    r"|\d{1,2}\s+"
                    r"(?:january|february|march|april|may|june|july|august|"
                    r"september|october|november|december)\s+\d{4}",
                    date_str,
                    re.IGNORECASE,
                )
                if match:
                    try:
                        date = date_parser.parse(
                            match.group(0), fuzzy=True, dayfirst=True
                        )
                        return date.date()
                    except (date_parser.ParserError, ValueError, OverflowError):
                        pass

        return None

    @staticmethod
    def _select_column(
        cur_header: list[str], patterns: list[re.Pattern[str]]
    ) -> int | None:
        """Return the first pattern that matches exactly one column.

        Patterns are tried in priority order. A pattern matching
        several columns does not win: a later, more specific pattern
        may match exactly one. This is the header-only disambiguation
        the upstream parser relied on, extended with fallbacks.
        """
        for pattern in patterns:
            matches = [i for i, h in enumerate(cur_header) if pattern.search(h)]
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def _first_data_row(
        extracted_header: list[list[str | None]], start_row: int
    ) -> int | None:
        # First row after the header whose cell looks like an ISIN.
        # ISIN parsing covers valid ISINs; the format check also covers
        # a data row whose ISIN would fail a checksum, so a corrupted
        # first row is still recognised as data, not as header.
        for idx in range(start_row + 1, len(extracted_header)):
            for cell in extracted_header[idx]:
                if not cell:
                    continue
                text = cell.strip()
                if Isin.parse(text) is not None or re.fullmatch(
                    r"[A-Z]{2}[A-Z0-9]{10}", text
                ):
                    return idx
        return None

    @staticmethod
    def _extract_header(
        page: Page, file: Path, page_num: int
    ) -> tuple[tuple[float, float, float, float], list[float], dict[int, int]]:
        header_table = page.find_table()
        if header_table is None:
            raise ParsingError(file, f"Could not find table headers on page {page_num}")
        extracted_header = header_table.extract()
        if not extracted_header or not extracted_header[0]:
            raise ParsingError(file, f"Table header is empty on page {page_num}")

        columns = [col.bbox[0] for col in header_table.columns]
        columns.append(header_table.rows[0].bbox[2])

        # Attempt A: upstream logic, header-only, first row only, with
        # anchored patterns. In the summary format this alone
        # disambiguates: only one first-row header starts with
        # "Share class currency" / "CURRENCY OF THE FOLLOWING AMOUNTS",
        # while "CLASS CURRENCY" is a sub-header under several amount
        # columns and is deliberately NOT in the upstream currency
        # pattern at all.
        first_row = [
            (col or "").replace("\n", " ").strip() for col in extracted_header[0]
        ]
        LOGGER.debug("first_row_header=%s", first_row)
        # All patterns are lower case: case-insensitive matching already covers
        # "Share class currency", "SHARE CLASS CURRENCY", etc., so no
        # mixed/upper-case variants are needed, and trailing wildcards are
        # redundant: _select_column uses search(), not match().
        upstream_patterns: dict[int, list[re.Pattern[str]]] = {
            ISIN_COLUMN: [re.compile(r"^isin", re.IGNORECASE)],
            CURRENCY_COLUMN: [
                re.compile(
                    r"^(share class currency|currency of the following amounts)",
                    re.IGNORECASE,
                )
            ],
            ERI_COLUMN: [
                re.compile(
                    r"^(excess reported income per share|"
                    r"per unit excess reportable income over distributions)",
                    re.IGNORECASE,
                )
            ],
        }
        colmap_a: dict[int, int] = {}
        for col, patterns in upstream_patterns.items():
            selected = XtrackersImporter._select_column(first_row, patterns)
            if selected is not None:
                colmap_a[col] = selected
        if len(colmap_a) == EXPECTED_HEADER_COLUMNS:
            LOGGER.debug("Header disambiguated from first row only: %s", colmap_a)
            bbox = header_table.rows[0].bbox
            return (
                (bbox[0], bbox[1], bbox[2], bbox[3] + HEADER_CROP_EPSILON),
                columns,
                colmap_a,
            )

        # Attempt B: multi-line headers (2025 investor reports), where
        # the header is split over several rows and "ISIN" is not in
        # row 0. Do NOT treat all currency phrases as equal. Try the
        # specific phrases first and "CLASS CURRENCY" last: in the
        # summary format the specific phrase already matches exactly
        # one column, so the repeated "CLASS CURRENCY" sub-headers
        # (2024: columns 8, 9, 10, 11) are never reached. In the SICAV
        # investor format the specific phrases match nothing, so the
        # "CLASS CURRENCY" fallback correctly matches its one column.
        # The header ends at the row before the first data row, not
        # after a fixed number of rows: on SICAV 2025 page 1 the
        # header is row 2, row 3 is the "Accumulating shares:"
        # section label, and row 4 is the first data row
        # Cropping after start_row + 2 therefore cropped that first
        # data row away, whatever the epsilon.
        start_row = 0
        for idx, row in enumerate(extracted_header[:10]):
            row_str = " ".join(str(c) for c in row if c).upper()
            if "ISIN" in row_str:
                start_row = idx
                break
        first_data_row = XtrackersImporter._first_data_row(extracted_header, start_row)
        if first_data_row is not None:
            header_end_row = first_data_row - 1
        else:
            # Header-only table: no data row to bound it, keep the
            # previous 3-row merge as a fallback.
            header_end_row = min(start_row + 2, len(extracted_header) - 1)
        cur_header = []
        num_cols = len(extracted_header[0])
        for i in range(num_cols):
            header_parts = [
                str(extracted_header[row_idx][i]).replace("\n", " ").strip()
                for row_idx in range(start_row, header_end_row + 1)
                if i < len(extracted_header[row_idx]) and extracted_header[row_idx][i]
            ]
            cur_header.append(" ".join(header_parts))
        LOGGER.debug("cur_header=%s", cur_header)
        priority_patterns: dict[int, list[re.Pattern[str]]] = {
            ISIN_COLUMN: [
                re.compile(r"\bisin\b", re.IGNORECASE),
            ],
            CURRENCY_COLUMN: [
                re.compile(r"\bshare class currency\b", re.IGNORECASE),
                re.compile(r"\bcurrency of the following amounts\b", re.IGNORECASE),
                re.compile(r"\bclass currency\b", re.IGNORECASE),
            ],
            ERI_COLUMN: [
                re.compile(r"\bexcess reported income per share\b", re.IGNORECASE),
                re.compile(
                    r"\bper unit excess reportable income over distributions\b",
                    re.IGNORECASE,
                ),
                re.compile(r"\bper unit excess reportable income\b", re.IGNORECASE),
                re.compile(r"\bexcess of reported income\b", re.IGNORECASE),
                re.compile(r"\bexcess reported income\b", re.IGNORECASE),
            ],
        }
        colmap: dict[int, int] = {}
        for col, patterns in priority_patterns.items():
            selected = XtrackersImporter._select_column(cur_header, patterns)
            if selected is not None:
                colmap[col] = selected
        if len(colmap) == EXPECTED_HEADER_COLUMNS:
            last_header_row = min(header_end_row, len(header_table.rows) - 1)
            bbox = header_table.rows[last_header_row].bbox
            return (
                (bbox[0], bbox[1], bbox[2], bbox[3] + HEADER_CROP_EPSILON),
                columns,
                colmap,
            )

        # Last resort (v2 behaviour): take the first match of the
        # loosest pattern so _process_data_table can still try to
        # disambiguate from the data rows. Only raise if a column
        # matches nothing at all.
        for col, patterns in priority_patterns.items():
            if col in colmap:
                continue
            for pattern in patterns:
                matches = [i for i, h in enumerate(cur_header) if pattern.search(h)]
                if matches:
                    LOGGER.warning(
                        "Multiple/no-unique header match for column %d on "
                        "page %d; using %d, data rows will disambiguate",
                        col,
                        page_num,
                        matches[0],
                    )
                    colmap[col] = matches[0]
                    break
            else:
                raise ParsingError(
                    file,
                    f"No column matching column {col} on page {page_num}",
                )
        last_header_row = min(header_end_row, len(header_table.rows) - 1)
        bbox = header_table.rows[last_header_row].bbox
        return (
            (bbox[0], bbox[1], bbox[2], bbox[3] + HEADER_CROP_EPSILON),
            columns,
            colmap,
        )

    @staticmethod
    def _extract_data_rows(
        cropped: CroppedPage,
        columns: list[float],
        file: Path,
        page_num: int,
    ) -> list[list[str | None]]:
        table_settings = {
            "vertical_strategy": "explicit",
            "explicit_vertical_lines": columns,
            "horizontal_strategy": "text",
        }
        data_table = cropped.extract_table(table_settings)
        if data_table is None:
            raise ParsingError(file, f"Could not extract data table on page {page_num}")
        return data_table

    @staticmethod
    def _shift_isin_column(
        data_table: list[list[str | None]],
        colmap: dict[int, int],
    ) -> dict[int, int]:
        # Detect the ISIN shift from the column that most often
        # contains a valid ISIN in the data rows. In the SICAV 2025
        # report the header table has one extra leading column
        # compared with the extracted data table, so the header map
        # says ISIN=1, currency=6, ERI=9 while the data rows are
        # actually ISIN=0, currency=5, ERI=8. The offsets between the
        # columns are preserved, so shifting every column by the ISIN
        # discrepancy fixes all three at once.
        isin_counts: Counter[int] = Counter()
        for raw in data_table:
            if not raw:
                continue
            for idx, cell in enumerate(raw):
                if cell and Isin.parse(cell.strip()) is not None:
                    isin_counts[idx] += 1
        if not isin_counts:
            return colmap
        best_idx, _ = isin_counts.most_common(1)[0]
        if best_idx == colmap[ISIN_COLUMN]:
            return colmap
        shift = best_idx - colmap[ISIN_COLUMN]
        shifted = {col: pos + shift for col, pos in colmap.items()}
        max_len = max(len(r) for r in data_table if r)
        if not all(0 <= pos < max_len for pos in shifted.values()):
            return colmap
        LOGGER.debug(
            "Realigning Xtrackers columns by %+d: %s -> %s",
            shift,
            colmap,
            shifted,
        )
        return shifted

    @staticmethod
    def _disambiguate_currency_eri(
        data_table: list[list[str | None]],
        colmap: dict[int, int],
    ) -> dict[int, int]:
        # Individually disambiguate currency and ERI, which the ISIN
        # shift alone cannot fix when the header matched several
        # columns (summary 2024 currency: 8, 9, 10, 11). Use only rows
        # with a valid ISIN, so header/footer noise does not vote.
        colmap = dict(colmap)
        isin_rows = [
            raw
            for raw in data_table
            if raw
            and colmap[ISIN_COLUMN] < len(raw)
            and raw[colmap[ISIN_COLUMN]]
            and Isin.parse((raw[colmap[ISIN_COLUMN]] or "").strip()) is not None
        ]
        if not isin_rows:
            return colmap
        max_len = max(len(r) for r in data_table if r)

        def _cell(raw: list[str | None], idx: int) -> str:
            return (raw[idx] or "").strip() if idx < len(raw) else ""

        def _currency_rate(idx: int) -> float:
            return sum(
                1
                for r in isin_rows
                if CurrencyCode.parse(_cell(r, idx).replace("JPN", "JPY")) is not None
            ) / len(isin_rows)

        if (
            _currency_rate(colmap[CURRENCY_COLUMN]) < COLUMN_MATCH_THRESHOLD
            and max_len > 1
        ):
            best_col, best_rate = max(
                (
                    (idx, _currency_rate(idx))
                    for idx in range(max_len)
                    if idx != colmap[ISIN_COLUMN]
                ),
                key=lambda pair: pair[1],
            )
            if best_rate >= COLUMN_MATCH_THRESHOLD:
                LOGGER.debug(
                    "Realigning Xtrackers currency column %d -> %d "
                    "(valid in %.0f%% of ISIN rows)",
                    colmap[CURRENCY_COLUMN],
                    best_col,
                    best_rate * 100,
                )
                colmap[CURRENCY_COLUMN] = best_col

        def _amount_rate(idx: int) -> float:
            return sum(
                1
                for r in isin_rows
                if re.match(
                    AMOUNT_REGEX,
                    _cell(r, idx).replace(",", "").replace(" ", ""),
                )
            ) / len(isin_rows)

        if _amount_rate(colmap[ERI_COLUMN]) < COLUMN_MATCH_THRESHOLD:
            candidates = [
                (idx, _amount_rate(idx))
                for idx in range(max_len)
                if idx not in {colmap[ISIN_COLUMN], colmap[CURRENCY_COLUMN]}
                and _amount_rate(idx) >= COLUMN_MATCH_THRESHOLD
            ]
            if candidates:
                # Several numeric columns can qualify (e.g. a
                # distribution-per-unit column as well as ERI),
                # so take the one closest to the header's ERI
                # position rather than the first numeric column.
                best_col, best_rate = min(
                    candidates,
                    key=lambda pair: (
                        abs(pair[0] - colmap[ERI_COLUMN]),
                        -pair[1],
                    ),
                )
                LOGGER.debug(
                    "Realigning Xtrackers ERI column %d -> %d",
                    colmap[ERI_COLUMN],
                    best_col,
                )
                colmap[ERI_COLUMN] = best_col
        return colmap

    @staticmethod
    def _realign_colmap(
        data_table: list[list[str | None]],
        colmap: dict[int, int],
    ) -> dict[int, int]:
        # Realign the header-derived column map against the data table.
        colmap = XtrackersImporter._shift_isin_column(data_table, colmap)
        return XtrackersImporter._disambiguate_currency_eri(data_table, colmap)

    @staticmethod
    def _process_row(
        page_num: int,
        row_num: int,
        raw_row: list[str | None],
        reporting_period_end: dt.date,
        colmap: dict[int, int],
        file: Path,
    ) -> tuple[ERITransaction | None, str | None, str | None]:
        # Returns (transaction, skip reason, ISIN value). Raises
        # ParsingError for a complete row with an invalid ISIN,
        # currency or amount, matching upstream; partial rows and
        # blank or not-applicable ERI rows are skipped, not raised.
        if is_blank_row(raw_row):
            return None, "blank", None
        row = {}
        for col, pos in colmap.items():
            row[col] = (raw_row[pos] or "").strip() if pos < len(raw_row) else ""

        # Check if row is just continuing text or empty before parsing.
        # Skip if EITHER column is missing: a row with an ISIN but
        # no amount, or an amount but no ISIN, is not a complete
        # data row, and must not reach the invalid* checks below.
        if not row[ISIN_COLUMN] or not row[ERI_COLUMN]:
            return None, "no_isin_no_eri", None

        isin_raw = row[ISIN_COLUMN]
        isin = Isin.parse(isin_raw)
        if isin is None:
            raise ParsingError(
                file,
                f"Invalid ISIN on page {page_num}, row {row_num}: {isin_raw!r}",
            )

        currency_raw = row[CURRENCY_COLUMN].replace("JPN", "JPY")
        currency = CurrencyCode.parse(currency_raw)
        if currency is None:
            raise ParsingError(
                file,
                f"Invalid currency on page {page_num}, row {row_num}: "
                f"{currency_raw!r} for ISIN {isin_raw}",
            )

        eri_val = row[ERI_COLUMN].replace(",", "").replace(" ", "")
        if eri_val.upper() in {"N/A", "NIL", ""}:
            # ERI is often left blank or not applicable if it does not apply
            return None, "eri_na_blank", isin_raw

        if not re.match(AMOUNT_REGEX, eri_val):
            # No post-hoc cleaning here by design: an interleaved
            # label in this cell means the table extraction itself
            # was wrong, and that is fixed by the header crop in
            # _extract_header, not by stripping letters out of a
            # corrupted amount.
            raise ParsingError(
                file,
                f"Invalid amount on page {page_num}, row {row_num}: "
                f"{eri_val!r} for ISIN {isin_raw}",
            )

        amount = round_decimal(Decimal(eri_val), 5)

        # Preserve zero ERI amounts too, as upstream does: a
        # share class with 0.0000 excess income is still a
        # reported row, not a row to drop.
        return (
            ERITransaction(
                isin=isin,
                date=reporting_period_end,
                price=amount,
                currency=currency,
            ),
            None,
            None,
        )

    @staticmethod
    def _process_data_table(
        page_num: int,
        data_table: list[list[str | None]],
        reporting_period_end: dt.date,
        colmap: dict[int, int],
        file: Path,
    ) -> list[ERITransaction]:
        colmap = XtrackersImporter._realign_colmap(data_table, colmap)

        # Unlike upstream, which raises on the first invalid row, this
        # version counts every skip by reason, so rows do not go
        # "missing" without a trace.
        skipped: Counter[str] = Counter()
        skipped_isins: dict[str, list[str]] = {}
        transactions = []
        for row_num, raw_row in enumerate(data_table, 1):
            transaction, reason, isin_raw = XtrackersImporter._process_row(
                page_num,
                row_num,
                raw_row,
                reporting_period_end,
                colmap,
                file,
            )
            if transaction is not None:
                transactions.append(transaction)
            elif reason is not None:
                skipped[reason] += 1
                if reason == "eri_na_blank" and isin_raw is not None:
                    skipped_isins.setdefault(reason, []).append(isin_raw)

        LOGGER.info(
            "Read %d rows from page %d (skipped: %s)",
            len(transactions),
            page_num,
            dict(skipped) if skipped else "none",
        )
        for reason, isins in skipped_isins.items():
            LOGGER.info("Page %d skipped %s: %s", page_num, reason, ", ".join(isins))
        return transactions

    @staticmethod
    def _parse_pages(
        pdf: PDF,
        reporting_period_end: dt.date,
        file: Path,
    ) -> list[ERITransaction]:
        transactions = []
        last_columns: list[float] | None = None
        last_colmap: dict[int, int] | None = None
        # First header error seen, if any. It is only raised at the
        # end, and only if no page ever yielded a header: that is a
        # report with no table at all, or with a required column
        # missing, which must fail loudly instead of silently
        # returning zero transactions. A header error on a later
        # page, or on a cover page before the table starts, is still
        # handled gracefully below, as before.
        first_header_error: ParsingError | None = None
        for page_num, page in enumerate(pdf.pages, 1):
            # Try/except block allows the parser to gracefully skip non-tabular trailing pages
            try:
                header_bbox, columns, colmap = XtrackersImporter._extract_header(
                    page, file, page_num
                )
            except ParsingError as exc:
                # Continuation pages often repeat the data table without
                # repeating the header. Reuse the previous page's columns
                # instead of silently dropping the whole page; rows
                # without a valid ISIN are filtered out anyway, so notes
                # pages contribute nothing.
                LOGGER.debug("No Xtrackers header on page %d: %s", page_num, exc)
                if last_columns is None or last_colmap is None:
                    if first_header_error is None:
                        first_header_error = exc
                    continue
                try:
                    cropped = page.crop((0, 0, page.width, page.height))
                    data_table = XtrackersImporter._extract_data_rows(
                        cropped, last_columns, file, page_num
                    )
                except ParsingError:
                    continue
                transactions += XtrackersImporter._process_data_table(
                    page_num,
                    data_table,
                    reporting_period_end,
                    last_colmap,
                    file,
                )
                continue

            (h_left, _h_top, h_right, h_bottom) = header_bbox
            cropped = page.crop((h_left, h_bottom, h_right, page.height))
            data_table = XtrackersImporter._extract_data_rows(
                cropped, columns, file, page_num
            )
            last_columns = columns
            last_colmap = colmap
            transactions += XtrackersImporter._process_data_table(
                page_num, data_table, reporting_period_end, colmap, file
            )
        if last_columns is None and first_header_error is not None:
            raise first_header_error
        return transactions

    @staticmethod
    def _parse_summary_format(pdf: PDF, file: Path) -> list[ERITransaction] | None:
        """Parse the summary format used by the main Xtrackers umbrella."""
        first_page = pdf.pages[0]
        prefix = "Period ended"
        if not first_page.search("Summary of reportable income calculations"):
            return None
        LOGGER.info("Detected Xtrackers summary format")

        reporting_period_end = XtrackersImporter._extract_report_end_date(
            first_page, prefix
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            return []

        return XtrackersImporter._parse_pages(pdf, reporting_period_end, file)

    @staticmethod
    def _parse_investor_report_format(
        pdf: PDF, file: Path
    ) -> list[ERITransaction] | None:
        """Parse the investor report format used by Xtrackers II and (IE) Plc."""
        first_page = pdf.pages[0]

        # Use relaxed case-insensitive searching in case of formatting variations
        text = (first_page.extract_text() or "").lower()
        if (
            "uk reporting fund status" not in text
            and "report to participants" not in text
        ):
            return None
        if "xtrackers" not in text:
            return None

        LOGGER.info("Detected Xtrackers investor report format")

        # 2025 reports often omit "of account" and sometimes omit colons
        reporting_period_end = XtrackersImporter._extract_report_end_date(
            first_page, "Period of account ended:"
        ) or XtrackersImporter._extract_report_end_date(first_page, "Period ended:")

        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            return []

        return XtrackersImporter._parse_pages(pdf, reporting_period_end, file)
