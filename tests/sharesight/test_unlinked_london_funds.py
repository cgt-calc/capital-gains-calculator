"""The warning for a Sharesight holding that may be a fund with bundled ERI.

Sharesight names a London holding `LSE:<code>` and gives it no ISIN, while
cgt-calc's ISIN data knows a fund by its ticker alone, so the fund's excess
reported income reaches the holding only once the user links the two.

Every trade is a GBP buy at £24.00 a unit with no fees, made up for these
tests. Bundled reports used, GBP a unit unless stated:

- VAGP (IE00BG47KG48), `vanguard_eri.csv`: 0.0198 for the year to 30 June
  2020, distributed on 31 December 2020, in 2020/21; 0.0134 for the year to
  30 June 2024, distributed on 31 December 2024, in 2024/25.
- VAPX and VDPX (IE00B9F5YL18), `vanguard_eri.csv`: USD 0.0512 for the year
  to 30 June 2020, converted at December 2020's USD 1.3303.
"""

from __future__ import annotations

import csv
import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.utils import build_cmd, run_cli, summary_figure

if TYPE_CHECKING:
    from pathlib import Path

TRADES_HEADER = [
    "Market",
    "Code",
    "Name",
    "Type",
    "Date",
    "Quantity",
    "Price *",
    "Brokerage *",
    "Currency",
    "Exchange Rate",
    "Value",
    "",
    "Comments",
]
ERI_HEADER = [
    "ISIN",
    "Fund Reporting Period End Date",
    "Currency",
    "Excess of reporting income over distribution",
]
WARNING = re.compile(
    r"^WARNING: cgt-calc has excess reported income reports for fund .*?"
    r"^See https://\S+$",
    re.MULTILINE | re.DOTALL,
)


def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


def _sharesight(
    tmp_path: Path,
    trades: list[tuple[str, str, str, int]],
    income: list[list[str]] | None = None,
) -> Path:
    """Write the reports: each trade a (market, code, date, quantity) buy."""
    directory = tmp_path / "sharesight"
    directory.mkdir()
    rows = [["Probe Portfolio - All Trades Report"], [""], TRADES_HEADER]
    rows += [
        [
            market,
            code,
            code,
            "Buy",
            date,
            str(quantity),
            "24.00",
            "0",
            "GBP",
            "1",
            f"{quantity * 24}.00",
            "All Trades",
            "",
        ]
        for market, code, date, quantity in trades
    ]
    _write_csv(directory / "All Trades Report.csv", rows)
    if income is not None:
        _write_csv(
            directory / "Taxable Income Report.csv",
            [
                ["Probe Portfolio - Taxable Income Report"],
                [""],
                ["Local Income"],
                [""],
                ["Dividend Payments"],
                [
                    "Code",
                    "Name",
                    "Date Paid",
                    "Net Dividend",
                    "Tax Deducted",
                    "Tax Credit",
                    "Gross Dividend",
                    "Comments",
                ],
                *income,
            ],
        )
    return directory


def _cli(year: int, *args: str) -> list[str]:
    return build_cmd("--year", str(year), *args, "--no-balance-check", "--no-report")


def _warnings(stderr: str) -> list[str]:
    """Return each whole warning about an unlinked holding, in order."""
    return WARNING.findall(stderr)


def _holding(warning: str) -> str:
    """Return the holding a warning is about."""
    match = re.search(r"your holding (\S+)\. So", warning)
    assert match is not None, warning
    return match[1]


def _row(warning: str) -> str:
    """Return the translation row a warning shows."""
    return warning.split("add this row:\n    ")[1].split("\n", maxsplit=1)[0]


def _follow(warning: str, translation: Path) -> None:
    """Do what the warning says, as a user who has checked the ISIN would.

    Add the holding's name to the fund's row in the file, keeping every name
    on it, or add the warning's row when the file has none for the fund.
    """
    advised = next(csv.reader([_row(warning)]))
    rows = (
        list(csv.reader(translation.read_text(encoding="utf-8").splitlines()))
        if translation.exists()
        else [["ISIN", "symbol"]]
    )
    for row in rows[1:]:
        if row[0] == advised[0]:
            row.append(_holding(warning))
            break
    else:
        rows.append(advised)
    _write_csv(translation, rows)


def _eri(stdout: str) -> dict[str, str]:
    """Return the run's Excess Reported Income list, holding to amount."""
    if "Excess Reported Income\n" not in stdout:
        return {}
    block = stdout.split("Excess Reported Income\n", 1)[1].split("\n\n", 1)[0]
    return dict(
        line.strip().removesuffix(" (included as dividend)").split(": ")
        for line in block.splitlines()
    )


@pytest.mark.parametrize("year", [2020, 2024])
def test_unlinked_holding_of_a_bundled_fund_warns_once(
    tmp_path: Path, year: int
) -> None:
    """A Sharesight VAGP holding gets no ERI, so the run says how to link it.

    The pooled holding of 600 and 400 units is held at every period end of
    the fund's reports, yet the run warns once. It is not linked, so it still
    gets no ERI and its cost stays 1,000 x £24.00 = £24,000.00. The custom
    report disagrees with the bundled one for 2020, which stops a run that
    links VAGP, but none of the fund's reports reach this run.
    """
    sharesight = _sharesight(
        tmp_path,
        [("LSE", "VAGP", "02/03/2020", 600), ("LSE", "VAGP", "01/06/2020", 400)],
    )
    custom = _write_csv(
        tmp_path / "eri.csv",
        [ERI_HEADER, ["IE00BG47KG48", "30/06/2020", "GBP", "0.5000"]],
    )
    translation = tmp_path / "isin_translation.csv"
    result = run_cli(
        _cli(
            year,
            "--sharesight-dir",
            str(sharesight),
            "--eri-raw-file",
            str(custom),
            "--isin-translation-file",
            str(translation),
        )
    )

    assert _warnings(result.stderr) == [
        (
            "WARNING: cgt-calc has excess reported income reports for fund "
            "IE00BG47KG48, whose ticker in cgt-calc's ISIN data is VAGP, but no ISIN "
            "mapping or rename links them to your holding LSE:VAGP. So none of that "
            "fund's excess reported income is added to LSE:VAGP, and the holding is "
            "not checked for missing reports.\n"
            "If LSE:VAGP is that fund (check the ISIN on your contract note or the "
            "fund's documents), add it to your ISIN translation file "
            f"{translation}. Keep one row for IE00BG47KG48. If the file already has "
            "that row, add LSE:VAGP to it and keep every name already on it. If not, "
            "add this row:\n"
            "    IE00BG47KG48,LSE:VAGP,VAGP\n"
            "See https://cgt-calc.uk/extra-data-and-options/#isin-to-ticker-translation"
        )
    ]
    assert _eri(result.stdout) == {}
    assert "  LSE:VAGP: 1,000.00, £24,000.00\n" in result.stdout


def test_without_a_translation_file_the_warning_names_the_option(
    tmp_path: Path,
) -> None:
    """With `--isin-translation-file` cleared there is no file to name."""
    sharesight = _sharesight(tmp_path, [("LSE", "VAGP", "01/06/2020", 1000)])

    result = run_cli(
        _cli(2020, "--sharesight-dir", str(sharesight), "--isin-translation-file", "")
    )

    [warning] = _warnings(result.stderr)
    assert (
        "If LSE:VAGP is that fund (check the ISIN on your contract note or the "
        "fund's documents), add it to an ISIN translation file, and pass it with "
        "--isin-translation-file. Keep one row for IE00BG47KG48." in warning
    )


def test_a_linked_holding_still_stops_at_a_conflicting_report(
    tmp_path: Path,
) -> None:
    """The same custom report stops a run whose VAGP the ISIN data links.

    The warning leaves the reports a run reads unchanged, so a holding that a
    report reaches still meets the existing refusal.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2020-06-01,BUY,VAGP,1000,24.00,0,GBP\n",
        encoding="utf-8",
    )
    custom = _write_csv(
        tmp_path / "eri.csv",
        [ERI_HEADER, ["IE00BG47KG48", "30/06/2020", "GBP", "0.5000"]],
    )
    cmd = _cli(
        2020,
        "--raw-file",
        str(raw),
        "--eri-raw-file",
        str(custom),
        "--isin-translation-file",
        str(tmp_path / "isin_translation.csv"),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)

    assert result.returncode == 1
    assert (
        "Two different excess reported income figures for VAGP for the period "
        "ending 2020-06-30" in result.stderr
    )
    assert _warnings(result.stderr) == []


@pytest.mark.parametrize(
    ("existing", "first_rows", "linked"),
    [
        pytest.param(
            None,
            [
                "IE00B9F5YL18,LSE:VAPX,VAPX,VDPX",
                "IE00B9F5YL18,LSE:VDPX,VAPX,VDPX",
            ],
            {"LSE:VAPX", "LSE:VDPX", "VAPX", "VDPX"},
            id="no row for the fund yet",
        ),
        pytest.param(
            ["IE00B9F5YL18", "VAPX", "VDPX", "VAPXOLD"],
            [
                "IE00B9F5YL18,LSE:VAPX,VAPX,VAPXOLD,VDPX",
                "IE00B9F5YL18,LSE:VDPX,VAPX,VAPXOLD,VDPX",
            ],
            {"LSE:VAPX", "LSE:VDPX", "VAPX", "VAPXOLD", "VDPX"},
            id="the user's own row for the fund",
        ),
    ],
)
def test_two_holdings_of_one_fund_end_on_one_row(
    tmp_path: Path,
    existing: list[str] | None,
    first_rows: list[str],
    linked: set[str],
) -> None:
    """Following both warnings for one ISIN keeps both names on its one row.

    Each holding gets its own warning. After both are followed, the rerun
    gives each its ERI: LSE:VAPX 100 x USD 0.0512 = USD 5.12, at 1.3303 is
    £3.85, and LSE:VDPX 200 x USD 0.0512 = USD 10.24, at 1.3303 is £7.70.
    """
    sharesight = _sharesight(
        tmp_path,
        [("LSE", "VAPX", "01/06/2020", 100), ("LSE", "VDPX", "01/06/2020", 200)],
    )
    translation = tmp_path / "isin_translation.csv"
    if existing is not None:
        _write_csv(translation, [["ISIN", "symbol"], existing])
    cmd = _cli(
        2020,
        "--sharesight-dir",
        str(sharesight),
        "--isin-translation-file",
        str(translation),
    )

    warnings = _warnings(run_cli(cmd).stderr)
    assert [_row(warning) for warning in warnings] == first_rows
    for warning, name in zip(warnings, ["LSE:VAPX", "LSE:VDPX"], strict=True):
        assert (
            f"Keep one row for IE00B9F5YL18. If the file already has that row, "
            f"add {name} to it and keep every name already on it." in warning
        )
        _follow(warning, translation)
    [header, row] = csv.reader(translation.read_text(encoding="utf-8").splitlines())
    assert header == ["ISIN", "symbol"]
    assert row[0] == "IE00B9F5YL18"
    assert set(row[1:]) == linked

    result = run_cli(cmd)
    assert _warnings(result.stderr) == []
    assert _eri(result.stdout) == {"LSE:VAPX": "£3.85", "LSE:VDPX": "£7.70"}


def test_following_the_warning_keeps_the_eri_of_the_bare_ticker(
    tmp_path: Path,
) -> None:
    """The row the warning gives keeps VAGP, which another broker's file uses.

    Sharesight's LSE:VAGP and a RAW VAGP are two holdings. Before the row
    only VAGP gets ERI: 500 x £0.0198 = £9.90, cost £12,009.90. After it both
    do: LSE:VAGP 1,000 x £0.0198 = £19.80, cost £24,019.80, and VAGP keeps
    its £9.90. The two stay separate holdings.
    """
    sharesight = _sharesight(tmp_path, [("LSE", "VAGP", "01/06/2020", 1000)])
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2020-06-01,BUY,VAGP,500,24.00,0,GBP\n",
        encoding="utf-8",
    )
    translation = tmp_path / "isin_translation.csv"
    cmd = _cli(
        2020,
        "--sharesight-dir",
        str(sharesight),
        "--raw-file",
        str(raw),
        "--isin-translation-file",
        str(translation),
    )

    before = run_cli(cmd)
    [warning] = _warnings(before.stderr)
    assert _eri(before.stdout) == {"VAGP": "£9.90"}
    _follow(warning, translation)
    after = run_cli(cmd)

    assert _warnings(after.stderr) == []
    assert _eri(after.stdout) == {"LSE:VAGP": "£19.80", "VAGP": "£9.90"}
    assert "  LSE:VAGP: 1,000.00, £24,019.80\n" in after.stdout
    assert "  VAGP: 500.00, £12,009.90\n" in after.stdout


@pytest.mark.parametrize(
    ("trades", "files", "warned", "eri"),
    [
        # The user's row links the holding, so it gets its ERI and no warning.
        pytest.param(
            [("LSE", "VAGP", "01/06/2020", 1000)],
            {"isin_translation.csv": "ISIN,symbol\nIE00BG47KG48,LSE:VAGP,VAGP\n"},
            [],
            {"LSE:VAGP": "£19.80"},
            id="linked by the user's row",
        ),
        # An ISIN of the holding's own outranks the suggestion.
        pytest.param(
            [("LSE", "VAGP", "01/06/2020", 1000)],
            {"isin_translation.csv": "ISIN,symbol\nGB0002634946,LSE:VAGP\n"},
            [],
            {},
            id="an ISIN of its own",
        ),
        # A RAW row marks VAGP a US security, so the bundled list's VAGP is set
        # aside from the fund, and the fund's reports do not bring it back.
        pytest.param(
            [("LSE", "VAGP", "01/06/2020", 1000)],
            {
                "raw.csv": "date,action,symbol,quantity,price,fees,currency,market\n"
                "2020-06-01,BUY,VAGP,10,24.00,0,GBP,US\n"
            },
            [],
            {},
            id="set aside for a US security",
        ),
        # ISIBF is the US over-the-counter symbol the bundled list gives
        # IE000JAYMBQ8, which has reports, not a London ticker.
        pytest.param(
            [("LSE", "ISIBF", "01/06/2024", 1000)],
            {},
            [],
            {},
            id="not a London ticker",
        ),
        # IBC8D's fund, IE00BDZVHF28, has no bundled report.
        pytest.param(
            [("LSE", "IBC8D", "01/06/2020", 1000)],
            {},
            [],
            {},
            id="a fund with no reports",
        ),
        # Nil reports are reports: two of them give one warning, and no ERI.
        pytest.param(
            [("LSE", "IBC8D", "01/06/2020", 1000)],
            {
                "eri.csv": ",".join(ERI_HEADER) + "\n"
                "IE00BDZVHF28,30/06/2020,GBP,0.0000\n"
                "IE00BDZVHF28,30/06/2021,GBP,0.0000\n"
            },
            ["LSE:IBC8D"],
            {},
            id="a fund with nil reports only",
        ),
        # Another market's holding is not checked.
        pytest.param(
            [("NASDAQ", "VAGP", "01/06/2020", 1000)],
            {},
            [],
            {},
            id="another market",
        ),
    ],
)
def test_which_holdings_are_warned_about(
    tmp_path: Path,
    trades: list[tuple[str, str, str, int]],
    files: dict[str, str],
    warned: list[str],
    eri: dict[str, str],
) -> None:
    """Warn only for an unlinked London code of a fund the run has reports for."""
    args = ["--sharesight-dir", str(_sharesight(tmp_path, trades))]
    options = {
        "isin_translation.csv": "--isin-translation-file",
        "raw.csv": "--raw-file",
        "eri.csv": "--eri-raw-file",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        args += [options[name], str(tmp_path / name)]
    if "isin_translation.csv" not in files:
        args += ["--isin-translation-file", str(tmp_path / "isin_translation.csv")]
    year = int(trades[0][2][-4:])

    result = run_cli(_cli(year, *args))

    assert [_holding(warning) for warning in _warnings(result.stderr)] == warned
    assert _eri(result.stdout) == eri


@pytest.mark.parametrize(
    ("dividend", "tickers", "interest", "warnings"),
    [
        # £10.00 paid under VAGP.LSE and £19.80 ERI under LSE:VAGP: both are
        # listed, so both are interest, £29.80, and nothing is unmatched.
        pytest.param(
            True, "VAGP.LSE,LSE:VAGP", "29.80", [], id="distributions and ERI"
        ),
        # ERI alone: its holding's name is enough, and is all there is.
        pytest.param(False, "LSE:VAGP", "19.80", [], id="ERI only"),
        # ERI alone with the distributions' name too: that name has no income.
        pytest.param(
            False,
            "VAGP.LSE,LSE:VAGP",
            "19.80",
            [
                (
                    "WARNING: Interest fund ticker 'VAGP.LSE' matched no dividend or "
                    "excess reported income, so it changed nothing. Give each fund's "
                    "symbol exactly as its income shows it. Your files have income "
                    "under LSE:VAGP."
                )
            ],
            id="ERI only, with the distributions' name",
        ),
    ],
)
def test_a_linked_bond_fund_lists_the_names_its_income_has(
    tmp_path: Path,
    dividend: bool,
    tickers: str,
    interest: str,
    warnings: list[str],
) -> None:
    """Name each kind of income the files have, as the Sharesight guide says.

    The holding is linked by the user's row, so its ERI, 1,000 x £0.0198 =
    £19.80, goes by LSE:VAGP, and a distribution by its income row's name.
    """
    income = (
        [["VAGP.LSE", "VAGP", "15/10/2020", "10.00", "0.00", "0.00", "10.00", ""]]
        if dividend
        else None
    )
    sharesight = _sharesight(tmp_path, [("LSE", "VAGP", "01/06/2020", 1000)], income)
    translation = tmp_path / "isin_translation.csv"
    translation.write_text(
        "ISIN,symbol\nIE00BG47KG48,LSE:VAGP,VAGP\n", encoding="utf-8"
    )

    result = run_cli(
        _cli(
            2020,
            "--sharesight-dir",
            str(sharesight),
            "--isin-translation-file",
            str(translation),
            "--interest-fund-tickers",
            tickers,
        )
    )

    assert [
        line for line in result.stderr.splitlines() if line.startswith("WARNING")
    ] == warnings
    assert summary_figure(result.stdout, "Foreign proceeds") == interest
    assert summary_figure(result.stdout, "Proceeds") == "0.00"
