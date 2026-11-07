"""The PDF report shows each figure and name as a reader should see it.

Every figure in the PDF is the exact amount rounded to the penny on its own,
so a line can be out by a penny against its own components; that bound is
arithmetic, not a defect. What must not happen is a line out by the fee: the
proceeds shown net of the sale fee beside an allowable cost that already
includes it. The figures are read back from the PDF's text, so the check
covers what the reader sees.

A name from the user's files prints as written: no character in it acts as
Typst markup. A run of whitespace in it prints as one space, and its
characters must come from the set the report's fonts are checked to show.

A report that cannot be saved says why, and the run says where the PDF went.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import os
from pathlib import Path
import re
import shutil
import sys
from typing import TYPE_CHECKING
import unicodedata

import pdfplumber
import pytest

from cgt_calc.exceptions import CgtError
from cgt_calc.model import ActionType
from cgt_calc.render_pdf import render_pdf
from tests.utils import build_cmd, pdf_text, report_text, run_cli

from .calc_test_data import GBP, transaction
from .test_calc import create_calculator, get_report

if TYPE_CHECKING:
    from cgt_calc.model import CapitalGainsReport

PENNY = Decimal("0.01")

POOL_DAY = dt.date(2024, 6, 1)
SPLIT_DAY = dt.date(2024, 6, 10)
BAR_BUY_DAY = dt.date(2024, 7, 1)
BAR_SALE_DAY = dt.date(2024, 8, 1)

# FOO: a pool of 100, then on one day a purchase of 20 and a sale of 120, so the
# sale is matched same day for 20 and against the pool for 100 and the sale fee
# is apportioned between the two lines. BAR: one purchase sold at a loss, one
# matching line, so only the heading carries its proceeds. A fee on every leg.
TRANSACTIONS = [
    transaction(POOL_DAY, ActionType.BUY, "FOO", 100, 10, 1, -1001, GBP),
    transaction(SPLIT_DAY, ActionType.BUY, "FOO", 20, 12, 1, -241, GBP),
    transaction(SPLIT_DAY, ActionType.SELL, "FOO", 120, 13, 1, 1559, GBP),
    transaction(BAR_BUY_DAY, ActionType.BUY, "BAR", 50, 20, 1, -1001, GBP),
    transaction(BAR_SALE_DAY, ActionType.SELL, "BAR", 50, 18, 1, 899, GBP),
]

HEADING = re.compile(r"^Disposal \d+: \S+ units of (\w+) for £([\d,.]+)")
ENTRY = re.compile(
    r"\d+\. [A-Z0-9 ]+\. Quantity: [\d.]+, "
    r"(?:disposal proceeds: £([\d,.]+), )?"
    r"allowable cost(?: on [^:]+)?: £([\d,.]+), (gain|loss): £([\d,.]+)\."
)
TOTAL = re.compile(r"Total disposal proceeds: £([\d,.]+)")

# The verified character set, written out here apart from cgt_calc.render_pdf.
VERIFIED_RANGES = [
    (0x0020, 0x007E),
    (0x00A0, 0x02FF),
    (0x0370, 0x03FF),
    (0x0400, 0x04FF),
    (0x1E00, 0x1EFF),
    (0x2010, 0x2027),
    (0x2030, 0x203A),
    (0x20A0, 0x20C0),
]
# The symbols beyond those ranges that the LaTeX report printed.
VERIFIED_SYMBOLS = [
    0x0E3F, 0x203B, 0x203D, 0x2044, 0x204E, 0x2052, 0x2103, 0x2116,
    0x2117, 0x211E, 0x2120, 0x2122, 0x2127, 0x212E, 0x2190, 0x2191,
    0x2192, 0x2193, 0x2422, 0x2423, 0x25E6, 0x25EF, 0x266A, 0x27E8,
    0x27E9, 0xFB00, 0xFB01, 0xFB02, 0xFB03, 0xFB04, 0xFB05, 0xFB06,
]  # fmt: skip


def money(text: str) -> Decimal:
    """Parse a figure as the report prints it."""
    return Decimal(text.replace(",", ""))


def render(tmp_path: Path) -> str:
    """Render the scenario and return the PDF's text on one line."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False), TRANSACTIONS
    )
    return report_text(report, tmp_path)


def disposals(
    text: str,
) -> list[tuple[str, Decimal, list[tuple[Decimal | None, Decimal, Decimal]]]]:
    """Each disposal's symbol and proceeds with its (proceeds, cost, gain) lines."""
    found = []
    for block in re.split(r"(?=Disposal \d+: )", text)[1:]:
        heading = HEADING.match(block)
        assert heading is not None, block
        lines = [
            (
                money(proceeds) if proceeds else None,
                money(cost),
                money(gain) if sign == "gain" else -money(gain),
            )
            for proceeds, cost, sign, gain in ENTRY.findall(block)
        ]
        found.append((heading[1], money(heading[2]), lines))
    return found


def name_report(name: str) -> CapitalGainsReport:
    """Buy, sell and take a dividend on a holding called `name`.

    The purchase heading reads "10 units of NAME for £1,000.00" and the
    dividend "NAME for £5.00".
    """
    return get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(POOL_DAY, ActionType.BUY, name, 10, 100, 0, -1000, GBP),
            transaction(BAR_SALE_DAY, ActionType.SELL, name, 10, 120, 0, 1200, GBP),
            transaction(BAR_SALE_DAY, ActionType.DIVIDEND, name, None, None, 0, 5, GBP),
        ],
    )


def test_proceeds_are_shown_gross(tmp_path: Path) -> None:
    """The heading shows what the shares sold for; the fee is in the cost."""
    by_symbol = {
        symbol: proceeds for symbol, proceeds, _ in disposals(render(tmp_path))
    }
    assert by_symbol == {"FOO": Decimal("1560.00"), "BAR": Decimal("900.00")}


def test_each_line_reconciles_to_the_penny(tmp_path: Path) -> None:
    """Proceeds less allowable cost is the gain on every line, give or take a penny."""
    found = disposals(render(tmp_path))
    assert [len(lines) for _, _, lines in found] == [2, 1]
    for _, heading_proceeds, lines in found:
        for proceeds, cost, gain in lines:
            shown = heading_proceeds if proceeds is None else proceeds
            assert abs(shown - cost - gain) <= PENNY, (shown, cost, gain)


def test_lines_add_up_to_their_heading_and_the_total(tmp_path: Path) -> None:
    """Each rounded figure may be half a penny out, so sums drift one penny per term."""
    text = render(tmp_path)
    found = disposals(text)
    for _, heading_proceeds, lines in found:
        if len(lines) > 1:
            parts = [proceeds for proceeds, _, _ in lines if proceeds is not None]
            assert len(parts) == len(lines)
            assert abs(sum(parts) - heading_proceeds) <= PENNY * len(parts)
    total = TOTAL.search(text)
    assert total is not None
    headings = [proceeds for _, proceeds, _ in found]
    assert abs(sum(headings) - money(total[1])) <= PENNY * len(headings)


@pytest.mark.parametrize(
    "name",
    [
        "S&P *80%* Fund",
        "A _B_ C",
        "A #1 B",
        "A $B$ C",
        "A @B",
        "A <B> C",
        "A `B` C",
        "A\\nB",
        "A\\",
        "A[B",
        "A]B",
        'A "B" C',
        "A 'B' C",
        "A // B",
        "A /* B",
        "A -- B",
        "A ... B",
        "A~B",
    ],
)
def test_a_name_prints_as_written(tmp_path: Path, name: str) -> None:
    r"""A name that Typst markup would change prints as written.

    Read as markup, `*80%*` and `_B_` would turn bold or italic and lose their
    marks, `#1` would be code printing 1, `$B$` maths, `@B` a reference to a
    missing label and `<B>` a label, `` `B` `` code text, a lone `[` or `]`
    would open or close a block, the quotes would curl, `//` would start a
    comment that drops the rest of the line, cost included, `/*` an unclosed
    comment, `--` and `...` a dash and an ellipsis, and `~` a no-break space.
    `\` and `"` are the two the string literal escapes: unescaped, `\n` would
    become a line break and a final `\` would swallow the closing quote.
    """
    text = report_text(name_report(name), tmp_path)

    assert f"units of {name} for £1,000.00" in text
    assert f"{name} for £5.00" in text


@pytest.mark.parametrize(
    ("name", "printed"),
    [
        pytest.param("A\tB", "A B", id="tab"),
        pytest.param("A\x0cB", "A B", id="form feed"),
        pytest.param("A\u2028B", "A B", id="line separator"),
        pytest.param("Ae\u0301B", "A\u00e9B", id="decomposed accent"),
        pytest.param("A\u2126B", "A\u03a9B", id="ohm sign"),
        pytest.param("Fund\u2122", "Fund\u2122", id="trade mark"),
        pytest.param("Αθήνα", "Αθήνα", id="Greek"),
        pytest.param("Газпром", "Газпром", id="Cyrillic"),
        pytest.param("Société", "Société", id="Latin-1"),
    ],
)
def test_a_name_prints_on_one_line_and_composed(
    tmp_path: Path, name: str, printed: str
) -> None:
    """Whitespace runs print as one space, as LaTeX printed a tab; NFC composes.

    The ohm sign is canonically the Greek capital omega, which NFC gives. The
    trade mark sign is one of the symbols beyond the ranges that LaTeX printed.
    """
    text = report_text(name_report(name), tmp_path)

    assert f"units of {printed} for £1,000.00" in text
    assert f"{printed} for £5.00" in text


@pytest.mark.parametrize(
    ("name", "codes"),
    [
        pytest.param("\u817e\u8baf", "U+817E, U+8BAF", id="CJK"),
        pytest.param("A\x00B", "U+0000", id="control character"),
        pytest.param("A\u00adB", "U+00AD", id="soft hyphen"),
        pytest.param("\u0410\u0483", "U+0483", id="combining mark"),
        pytest.param("A\u1f00B", "U+1F00", id="Greek Extended"),
        pytest.param(
            "A\u2329B", "U+3008, after Unicode normalisation", id="angle bracket"
        ),
    ],
)
def test_a_name_outside_the_verified_set_stops_the_pdf(
    tmp_path: Path, name: str, codes: str
) -> None:
    """A character the report's fonts are not checked to show is refused.

    The soft hyphen printed (invisibly) in the LaTeX report and Greek Extended
    letters may well be in a font, but neither is in the verified set. The
    Cyrillic titlo U+0483 sits inside the Cyrillic range but is a combining
    mark, which the set leaves out. NFC turns U+2329 into the CJK bracket
    U+3008, which the fonts lack.
    """
    report = name_report(name)

    with pytest.raises(CgtError) as raised:
        render_pdf(report, tmp_path / "report.pdf")

    assert str(raised.value) == (
        f"Cannot create the PDF report: the name '{name}' contains characters "
        f"outside the report's verified character set ({codes}). The figures "
        "printed above are complete. To get a PDF, write this holding's name "
        "using Latin, Greek or Cyrillic letters, digits and common punctuation "
        "in your input file, or pass --no-report to skip the PDF."
    )
    assert not (tmp_path / "report.pdf").exists()


def test_every_verified_character_prints_as_itself(tmp_path: Path) -> None:
    """Each character in the verified set is drawn and reads back as itself.

    The name holds every one, forty to a word so the heading can wrap, and the
    check reads pdfplumber's text for each glyph: its line extraction expands
    the ligature characters and puts spaces after some narrow accents. NFC
    turns three Greek marks into the Latin characters they equal. With the
    font's ligatures on, the letters "IJ" earlier in the name took the glyph of
    Ĳ, and Ĳ then read back as "IJ".
    """
    characters = [
        char
        for char in [
            chr(code) for low, high in VERIFIED_RANGES for code in range(low, high + 1)
        ]
        + [chr(code) for code in VERIFIED_SYMBOLS]
        if char.isprintable()
        and char != " "
        and unicodedata.category(char) not in {"Mn", "Me"}
    ]
    name = " ".join(
        "".join(characters[start : start + 40])
        for start in range(0, len(characters), 40)
    )

    render_pdf(name_report(name), tmp_path / "report.pdf")

    with pdfplumber.open(tmp_path / "report.pdf") as pdf:
        glyphs = "".join(char["text"] for page in pdf.pages for char in page.chars)
    assert "(cid:" not in glyphs
    expected = unicodedata.normalize("NFC", name).replace(" ", "")
    assert expected in glyphs.replace(" ", "")


def test_the_report_is_set_in_new_computer_modern(tmp_path: Path) -> None:
    """Typst found the font the template names; it did not fall back to another."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False), TRANSACTIONS
    )

    render_pdf(report, tmp_path / "report.pdf")

    with pdfplumber.open(tmp_path / "report.pdf") as pdf:
        fonts = {
            char["fontname"].split("+")[-1] for page in pdf.pages for char in page.chars
        }
    assert fonts == {"NewCM10-Regular", "NewCM10-Bold"}


def test_the_gain_to_date_starts_its_own_paragraph(tmp_path: Path) -> None:
    """After a gain, "Capital gain to date" begins a paragraph of its own.

    #947 once joined it to the gain sentence. A paragraph break shows as a
    larger gap above the line than the gap between the lines of one wrapped
    paragraph, here the gift's two-line gain sentence (about 9 points against
    3 when this was written). Gift: 2 of 10 shares bought at £10, given at £30,
    a £40 gain; sale: 2 more sold at £30, another £40.
    """
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(POOL_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SPLIT_DAY, ActionType.GIFT, "FOO", 2, 30, 0, None, GBP),
            transaction(BAR_BUY_DAY, ActionType.SELL, "FOO", 2, 30, 0, 60, GBP),
        ],
    )
    render_pdf(report, tmp_path / "report.pdf")

    lines: list[tuple[str, float | None]] = []
    with pdfplumber.open(tmp_path / "report.pdf") as pdf:
        for page in pdf.pages:
            previous = None
            for line in page.extract_text_lines():
                gap: float | None = (
                    None if previous is None else line["top"] - previous["bottom"]
                )
                lines.append((line["text"], gap))
                previous = line
    texts = [text for text, _ in lines]
    gift = texts.index(
        next(
            t
            for t in texts
            if t.startswith("Chargeable gain is £40.00, before any relief")
        )
    )
    continuation_text, wrapped_gap = lines[gift + 1]
    assert not continuation_text.startswith("Capital gain to date")
    assert wrapped_gap is not None
    to_date = [
        gap for text, gap in lines if text.startswith("Capital gain to date is £")
    ]
    assert len(to_date) == 2
    assert all(gap is not None and gap > wrapped_gap for gap in to_date)


@pytest.mark.parametrize(
    ("locked", "output", "named"),
    [
        pytest.param(
            "out/report.pdf",
            "out/report.pdf",
            "out/report.pdf",
            id="the PDF is read-only",
        ),
        pytest.param(
            "out/report.pdf",
            "out/report",
            "out/report.pdf",
            id="the PDF is read-only and --output does not end in .pdf",
        ),
        pytest.param(
            "out",
            "out/new/report.pdf",
            "out/new/report.pdf",
            id="the folder cannot be made",
        ),
    ],
)
def test_a_report_that_cannot_be_saved_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    locked: str,
    output: str,
    named: str,
) -> None:
    """A report that cannot be written stops the run with the reason and what to do.

    `locked` is made read-only: a file where the name has a suffix, else a
    folder. `named` is the file the message gives: the PDF, named after the
    output's stem.
    """
    monkeypatch.chdir(tmp_path)
    blocked = Path(locked)
    if blocked.suffix:
        blocked.parent.mkdir()
        blocked.write_bytes(b"an earlier report")
        blocked.chmod(0o444)
    else:
        blocked.mkdir()
        blocked.chmod(0o555)
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False), TRANSACTIONS
    )
    try:
        if os.access(blocked, os.W_OK):
            pytest.skip("this user may write there all the same")

        with pytest.raises(CgtError) as raised:
            render_pdf(report, Path(output))
    finally:
        blocked.chmod(0o755)

    assert str(raised.value) == (
        f"Cannot save the report to {Path(named)}: Permission denied. Close the file "
        "if another program has it open, and check that it and its folder can be "
        "written to. To save it somewhere else, pass --output."
    )


@pytest.mark.parametrize("name", ["report.pdf", "report.txt", "report"])
def test_the_progress_line_names_the_pdf_written(tmp_path: Path, name: str) -> None:
    """The PDF takes the output's stem whatever its suffix, and the run says so."""
    result = run_cli(
        build_cmd(
            "--year",
            "2022",
            "--raw-file",
            "tests/raw/data/test_data.csv",
            "--no-balance-check",
            "--output",
            str(tmp_path / name),
        )
    )

    written = tmp_path / "report.pdf"
    assert f"Writing PDF report to {written}..." in result.stderr.splitlines()
    assert [path.name for path in tmp_path.iterdir()] == ["report.pdf"]


def test_the_docs_example_report_is_current(tmp_path: Path) -> None:
    """docs/assets/example_report.pdf has the text the renderer prints today."""
    rates = tmp_path / "exchange_rates.csv"
    shutil.copy(Path("scripts/example_data/exchange_rates.csv"), rates)
    output = tmp_path / "example_report.pdf"

    run_cli(
        [
            sys.executable,
            "-m",
            "cgt_calc.main",
            "--year",
            "2025",
            "--schwab-file",
            "scripts/example_data/schwab_transactions.csv",
            "--exchange-rates-file",
            str(rates),
            "--output",
            str(output),
        ]
    )

    assert pdf_text(output) == pdf_text(Path("docs/assets/example_report.pdf")), (
        "The example report in the docs is out of date. Regenerate it with "
        "scripts/generate_example_report.sh, then its images with "
        "scripts/generate_example_preview.sh."
    )
