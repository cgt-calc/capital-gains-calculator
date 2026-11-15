"""Tests for disposals from a holding of a fund that may not have been a reporting fund.

A gain may be an offshore income gain, taxed as income, if the fund is a
non-reporting fund at the disposal, or became a reporting fund while the units
were held (Offshore Funds (Tax) Regulations 2009, reg 17(2), (3)). cgt-calc
knows when a bundled fund was a reporting fund from HMRC's list, so these runs
use real bundled funds with made-up transactions:

- VUAG, IE00BFMXXD54: a reporting fund from 2019-05-14, figures for periods
  ending 30 June from 2019, in dollars;
- LGMLP, IE00BHZKHS06: a reporting fund from 2014-05-19 to 2024-10-11, one
  figure, for the period ending 2023-06-30, in dollars;
- LUFUND, LU0827875005: a reporting fund from 2016-09-01 to 2023-03-03, nil
  figures only, the last for the period ending 2023-03-02;
- QDIV, IE00BKM4H312: a reporting fund from 2014-06-06, figures for periods
  ending 31 October from 2018, in dollars, nil but for 2018 and 2025.

A dollar figure is converted at the rate for its distribution date (D), six
months after its period ends, and only when a run may use it: for units held
at its period end, or for a disposal on that day or in the 30 days before it,
which the 30-day rule may match with units bought after it. The figures these
runs may use have D 2019-12-30 or 2020-12-30, which have rates in
tests/exchange_rates_data.csv. A data update that adds a figure these runs may
use needs them moved: a non-nil LGMLP figure for 2024-06-30 or 2024-10-11 would
change a cost below or need an uncached rate, and a non-nil QDIV figure dated
on or before 2018-03-31, 30 days after its sale, would reach its holding or
need an uncached rate.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.general.test_eri_gaps import ERI_HEADER, RAW_HEADER, _report
from tests.utils import build_cmd

if TYPE_CHECKING:
    from pathlib import Path

VUAG = "IE00BFMXXD54,VUAG"
LGMLP = "IE00BHZKHS06,LGMLP"
DOCS = "https://cgt-calc.uk/offshore-funds/#unsupported-functionality"
STOP_ADVICE = (
    "cgt-calc does not calculate it. Work out this holding separately and run "
    "cgt-calc without it."
)
LATER_GAIN = (
    "A gain on a later disposal may be an offshore income gain, taxed as income, "
    "which cgt-calc does not calculate."
)
REGULATION_48 = (
    " An election to be taxed on the gain up to that date instead (regulation 48) "
    "has to be made in your tax return for this year, and cgt-calc does not "
    "include that gain."
)
REGULATION_100 = (
    " An election to be treated as disposing of your units on that date "
    "(regulation 100) has to be made in your tax return for this year, and "
    "cgt-calc does not include that disposal."
)
UNCHECKED = (
    "WARNING: HMRC's list shows VUAG (ISIN IE00BFMXXD54) became a reporting fund on "
    "2019-05-14, and you held units of it under the name OLDFUND before that date. "
    "cgt-calc cannot check OLDFUND's status. If OLDFUND was not a reporting "
    "fund, a gain on these units may be an offshore income gain, taxed as income, "
    "which cgt-calc does not calculate. If OLDFUND is a fund cgt-calc has figures "
    "for, add its ISIN to your ISIN translation file and cgt-calc will check it. "
    f"See {DOCS}\n"
)
CANNOT_CALCULATE = "cgt-calc cannot calculate the disposal"
HMRC_LIST = "HMRC's list shows"
# A nil figure for the period ending 2024-06-30, so that the missing figure
# for it, whose income is in 2024/25, does not stop the run first.
LGMLP_2024 = _report(dt.date(2024, 6, 30), "0.00", "IE00BHZKHS06")


def _run(
    tmp_path: Path,
    trades: list[str],
    *options: str,
    reports: tuple[str, ...] = (),
    translations: str = VUAG,
    renames: tuple[tuple[str, str, str], ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run cgt-calc over RAW `trades`, whatever it returns.

    `renames` are (date, old, new) name changes, given in a Vanguard export,
    the one broker file that records a fund's change of name.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(RAW_HEADER + "".join(f"{row}\n" for row in trades), "utf-8")
    eri = tmp_path / "eri.csv"
    eri.write_text(ERI_HEADER + "".join(f"{row}\n" for row in reports), "utf-8")
    isins = tmp_path / "isins.csv"
    isins.write_text(f"ISIN,symbol\n{translations}\n", "utf-8")
    files = ["--raw-file", str(raw), "--eri-raw-file", str(eri)]
    if renames:
        vanguard = tmp_path / "vanguard.csv"
        vanguard.write_text(
            "Cash Transactions\n"
            "Date,Details,Amount,Balance\n"
            "01/01/2010,Regular Deposit,100,100\n"
            "Investment Transactions\n"
            "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
            + "".join(
                f"{day},Old ({old}),NameChange: {old},0,0,0\n"
                f"{day},New ({new}),NameChange: {old} replaced with {new},0,0,0\n"
                for day, old, new in renames
            ),
            "utf-8",
        )
        files += ["--vanguard-file", str(vanguard)]
    return subprocess.run(
        build_cmd(
            *files,
            "--isin-translation-file",
            str(isins),
            "--no-balance-check",
            "--no-report",
            *options,
        ),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize(
    ("action", "quantity"),
    [
        pytest.param("SELL", "10", id="a sale"),
        pytest.param("GIFT", "10", id="a gift"),
        pytest.param("SELL", "4", id="a sale of part of the holding"),
    ],
)
def test_a_disposal_held_since_before_the_fund_reported_stops(
    tmp_path: Path, action: str, quantity: str
) -> None:
    """Units bought before HMRC's start date and disposed of after it.

    Bought on 2019-01-10, before VUAG became a reporting fund on 2019-05-14,
    and disposed of on 2019-06-03, in the finished 2019/20 report. The fund
    became a reporting fund while the units were held, so the gain may be an
    offshore income gain (reg 17(3)), unless the holder elected under reg 48.
    A gift is a disposal at market value, so it stops the run as a sale does.
    The 6 units a part sale leaves are held at 2019-06-30, whose dollar figure
    is applied at D, 2019-12-30; the stop already covers the holding, so there
    is no separate warning that it is still held.
    """
    result = _run(
        tmp_path,
        [
            "2019-01-10,BUY,VUAG,10,40.00,5.00,GBP",
            f"2019-06-03,{action},VUAG,{quantity},45.00,0.00,GBP",
        ],
        "--year",
        "2019",
    )

    assert result.returncode == 1
    assert (
        "ERROR: cgt-calc cannot calculate the disposal of VUAG (ISIN IE00BFMXXD54) "
        "on 2019-06-03. HMRC's list shows the fund became a reporting fund on "
        "2019-05-14, and you have held units since before that date. The gain may "
        "be an offshore income gain, taxed as income, depending on the fund's status "
        f"then and on any election you made. {STOP_ADVICE} If the fund published a "
        "report for a period before that date, add its figure with --eri-raw-file. "
        f"See {DOCS}\n"
    ) in result.stderr
    assert "Tax summary" not in result.stdout
    assert "No disposal in this report is affected" not in result.stderr


@pytest.mark.parametrize(
    "bought",
    [
        pytest.param("2019-01-10", id="held overnight"),
        pytest.param("2019-03-01", id="bought and sold on one day"),
    ],
)
def test_a_disposal_before_the_fund_reported_stops(tmp_path: Path, bought: str) -> None:
    """A sale on 2019-03-01, before VUAG became a reporting fund on 2019-05-14.

    The fund was not a reporting fund at the sale, so the gain may be an
    offshore income gain (reg 17(2)). The sale is in the finished 2018/19
    report. Units bought that same day were never held at a day's end before
    the start, and still stop the run.
    """
    result = _run(
        tmp_path,
        [
            f"{bought},BUY,VUAG,10,40.00,5.00,GBP",
            "2019-03-01,SELL,VUAG,10,45.00,5.00,GBP",
        ],
        "--year",
        "2018",
    )

    assert result.returncode == 1
    assert (
        "ERROR: cgt-calc cannot calculate the disposal of VUAG (ISIN IE00BFMXXD54) "
        "on 2019-03-01. HMRC's list shows the fund became a reporting fund on "
        "2019-05-14, after this disposal. The gain may be an offshore income gain"
    ) in result.stderr


@pytest.mark.parametrize(
    ("trades", "year", "reports", "translations"),
    [
        pytest.param(
            [
                "2018-12-10,BUY,VUAG,10,40.00,5.00,GBP",
                "2019-01-15,SELL,VUAG,10,42.00,5.00,GBP",
                "2019-06-10,BUY,VUAG,10,44.00,5.00,GBP",
                "2019-08-01,SELL,VUAG,10,46.00,5.00,GBP",
            ],
            "2019",
            (),
            VUAG,
            id="sold out before the start and bought again after",
        ),
        pytest.param(
            [
                "2019-05-14,BUY,VUAG,10,40.00,5.00,GBP",
                "2019-08-01,SELL,VUAG,10,45.00,5.00,GBP",
            ],
            "2019",
            (),
            VUAG,
            id="bought on the start date",
        ),
        pytest.param(
            [
                "2019-01-10,BUY,VUAG,10,40.00,5.00,GBP",
                "2019-06-03,SELL,VUAG,10,45.00,5.00,GBP",
            ],
            "2019",
            (_report(dt.date(2018, 6, 30), "0.00", "IE00BFMXXD54"),),
            VUAG,
            id="a figure for a period before the start",
        ),
        pytest.param(
            [
                "2018-08-01,BUY,VUAG,10,40.00,5.00,GBP",
                "2019-06-03,SELL,VUAG,10,45.00,5.00,GBP",
            ],
            "2019",
            (_report(dt.date(2019, 5, 14), "0.00", "IE00BFMXXD54"),),
            VUAG,
            id="a figure for a period ending on the start date",
        ),
        pytest.param(
            [
                "2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP",
                "2024-10-11,SELL,LGMLP,10,18.00,5.00,GBP",
            ],
            "2024",
            (LGMLP_2024,),
            LGMLP,
            id="sold on the end date",
        ),
        pytest.param(
            [
                "2013-01-10,BUY,QDIV,10,30.00,0.00,GBP",
                "2018-03-01,SELL,QDIV,10,35.00,0.00,GBP",
            ],
            "2017",
            (),
            "XS0123456781,EXAMPLE",
            id="a share whose ticker is also a fund's",
        ),
    ],
)
def test_a_disposal_that_does_not_stop(
    tmp_path: Path,
    trades: list[str],
    year: str,
    reports: tuple[str, ...],
    translations: str,
) -> None:
    """Disposals that are not from a fund before or after it reported.

    - VUAG units sold on 2019-01-15, before 2019-05-14, emptied the holding;
      that sale is in 2018/19. The units sold on 2019-08-01 were bought on
      2019-06-10, after the start. They were held at 2019-06-30, whose dollar
      figure is applied at D, 2019-12-30.
    - VUAG is a reporting fund from 2019-05-14 itself, so units bought that day
      were never held before it was one.
    - A figure for a period ending 2018-06-30 shows the fund reported for a
      period that began up to a year earlier, by 2017-06-30, so units bought on
      2019-01-10 were not held before it became a reporting fund.
    - A figure for a period ending on the start date, 2019-05-14, does the
      same: the start moves to 2018-05-14, a year before it, so units bought
      on 2018-08-01 were not held before the fund reported either.
    - LGMLP is a reporting fund until the end of 2024-10-11 (reg 116(1)), so a
      sale that day is from a reporting fund.
    - The RAW holding QDIV has no ISIN. The bundled list gives the ticker to
      IE00BKM4H312, a reporting fund from 2014-06-06, but QDIV is also a US
      symbol, so the holding may not be that fund and its dates are not used.
      Its figures, from the period ending 2018-10-31, come after the 2017/18
      report, which ends on 2018-04-05.
    """
    result = _run(
        tmp_path,
        trades,
        "--year",
        year,
        reports=reports,
        translations=translations,
    )

    assert result.returncode == 0, result.stderr
    assert CANNOT_CALCULATE not in result.stderr
    assert HMRC_LIST not in result.stderr


@pytest.mark.parametrize(
    ("period", "election"),
    [
        pytest.param(
            ("--year", "2019"), REGULATION_48, id="the report the fund started in"
        ),
        pytest.param(
            ("--from", "2019-06-01", "--to", "2019-12-31"),
            REGULATION_48,
            id="part of that tax year, after the start",
        ),
        pytest.param(("--year", "2020"), "", id="a later report"),
    ],
)
def test_a_holding_held_since_before_the_fund_reported_warns(
    tmp_path: Path, period: tuple[str, ...], election: str
) -> None:
    """VUAG bought on 2019-01-10 and still held, with no disposal.

    No disposal in the report is affected, but a later one may be. The
    regulation 48 election's deemed disposal is on 2019-05-13, the day before
    the fund became a reporting fund (reg 48(8)), so it belongs in the 2019/20
    return (reg 48(6)), and a report for part of that year names it too. The
    figures for 2019-06-30 and 2020-06-30 are applied at D, 2019-12-30 and
    2020-12-30.
    """
    result = _run(tmp_path, ["2019-01-10,BUY,VUAG,10,40.00,5.00,GBP"], *period)

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: HMRC's list shows VUAG (ISIN IE00BFMXXD54) became a reporting fund "
        "on 2019-05-14, and you have held units since before that date. No disposal "
        f"in this report is affected. {LATER_GAIN}{election} See {DOCS}\n"
    ) in result.stderr


def test_a_transfer_to_a_spouse_from_such_a_holding_warns(tmp_path: Path) -> None:
    """VUAG bought on 2019-01-10 and given to a spouse on 2019-08-01.

    A transfer between spouses gives no gain (TCGA 1992 s58), so there is no
    offshore income gain either (reg 39(1)), and the run is not stopped. The
    2019-06-30 figure is applied at D, 2019-12-30, while the units were held at
    the period end.
    """
    result = _run(
        tmp_path,
        [
            "2019-01-10,BUY,VUAG,10,40.00,5.00,GBP",
            "2019-08-01,TRANSFER_TO_SPOUSE,VUAG,10,0.00,0.00,GBP",
        ],
        "--year",
        "2019",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: HMRC's list shows VUAG (ISIN IE00BFMXXD54) became a reporting fund "
        "on 2019-05-14, and you have held units since before that date. Your "
        "transfer to your spouse or civil partner gives no gain, so this report is "
        f"not affected. {LATER_GAIN}{REGULATION_48} See {DOCS}\n"
    ) in result.stderr


@pytest.mark.parametrize(
    "sold",
    [
        pytest.param(["2024-11-12,SELL,LGMLP,10,18.00,5.00,GBP"], id="32 days after"),
        pytest.param(
            [
                "2024-10-20,SELL,LGMLP,5,18.00,5.00,GBP",
                "2024-11-01,SELL,LGMLP,5,18.00,5.00,GBP",
            ],
            id="part of the holding within 31 days",
        ),
    ],
)
def test_a_disposal_after_the_fund_stopped_reporting_stops(
    tmp_path: Path, sold: list[str]
) -> None:
    """A disposal after LGMLP stopped being a reporting fund on 2024-10-11.

    The fund was not a reporting fund at the disposal, so the gain may be an
    offshore income gain (reg 17(2)). Selling the whole holding within 31 days
    after the end date is taken as the fund's closure; selling all of it 32
    days after, or part of it within the 31 days, is not. Both runs stop at the
    first such disposal in the finished 2024/25 report.
    """
    result = _run(
        tmp_path,
        ["2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP", *sold],
        "--year",
        "2024",
        reports=(LGMLP_2024,),
        translations=LGMLP,
    )
    day = sold[0][:10]

    assert result.returncode == 1
    assert (
        f"ERROR: cgt-calc cannot calculate the disposal of LGMLP (ISIN "
        f"IE00BHZKHS06) on {day}. HMRC's list shows the fund stopped being a "
        "reporting fund on 2024-10-11. A gain on units disposed of after that date "
        f"may be an offshore income gain, taxed as income. {STOP_ADVICE} If the fund "
        "published a report for a period after that date, add its figure with "
        f"--eri-raw-file. See {DOCS}\n"
    ) in result.stderr


@pytest.mark.parametrize(
    "day",
    [
        pytest.param("2024-10-20", id="9 days after"),
        pytest.param("2024-11-11", id="31 days after"),
    ],
)
def test_emptying_a_holding_just_after_the_end_date_is_a_closure(
    tmp_path: Path, day: str
) -> None:
    """The whole LGMLP holding sold within 31 days after its end date, 2024-10-11.

    A closing fund is a reporting fund until it leaves (reg 57(3)), and its
    payout is often recorded a few days later, so the gain is an ordinary one.
    10 units sold at 18.00 give proceeds of 180.00. They cost 200.00 with 5.00
    fees to buy and 5.00 to sell, 210.00 allowable, a loss of 30.00.
    """
    result = _run(
        tmp_path,
        [
            "2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP",
            f"{day},SELL,LGMLP,10,18.00,5.00,GBP",
        ],
        "--year",
        "2024",
        reports=(LGMLP_2024,),
        translations=LGMLP,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: HMRC's list shows LGMLP (ISIN IE00BHZKHS06) stopped being a "
        f"reporting fund on 2024-10-11, and your disposal on {day} emptied the "
        "holding. cgt-calc treats this as the fund's closure and calculates an "
        "ordinary capital gain. If the fund carried on after 2024-10-11 without "
        "reporting status, the gain may be an offshore income gain, taxed as income, "
        f"which cgt-calc does not calculate. See {DOCS}\n"
    ) in result.stderr
    assert (
        "  Disposals:                1\n"
        "  Disposal proceeds:  £180.00\n"
        "  Allowable costs:    £210.00\n"
        "  Gain:                 £0.00\n"
        "  Loss:                £30.00\n"
    ) in result.stdout


@pytest.mark.parametrize(
    ("bought", "sold", "renames", "year", "expected"),
    [
        pytest.param(
            "LUFUND",
            "2023-03-10,SELL,LUFUND,10",
            (),
            "2022",
            "ERROR: cgt-calc cannot calculate the disposal of LUFUND (ISIN "
            "LU0827875005) on 2023-03-10. HMRC's list shows the fund became a "
            "reporting fund on 2016-09-01, and you have held units since before that "
            "date.",
            id="held since before the start, sold",
        ),
        pytest.param(
            "LUFUND",
            "2023-06-01,SELL,NEWX,10",
            (("10/03/2023", "LUFUND", "NEWX"),),
            "2023",
            "ERROR: cgt-calc cannot calculate the disposal of NEWX on 2023-06-01. "
            "You held these units as LUFUND before. HMRC's list shows LUFUND (ISIN "
            "LU0827875005) became a reporting fund on 2016-09-01, and you have held "
            "units since before that date.",
            id="held since before the start, renamed, then sold",
        ),
        pytest.param(
            "OLDFUND",
            "2023-03-04,SELL,LUFUND,5",
            (("01/10/2016", "OLDFUND", "LUFUND"),),
            "2022",
            "ERROR: cgt-calc cannot calculate the disposal of LUFUND (ISIN "
            "LU0827875005) on 2023-03-04. HMRC's list shows the fund stopped being a "
            "reporting fund on 2023-03-03.",
            id="renamed in from a name with no dates, part sold after the end",
        ),
    ],
)
def test_a_disposal_just_after_the_end_date_that_is_no_closure(
    tmp_path: Path,
    bought: str,
    sold: str,
    renames: tuple[tuple[str, str, str], ...],
    year: str,
    expected: str,
) -> None:
    """Units bought on 2016-08-01 and gone just after LUFUND's end date.

    LUFUND was a reporting fund from 2016-09-01 to 2023-03-03.

    - Units held as LUFUND before it became a reporting fund may give an
      offshore income gain even while it is one (reg 17(3)). A sale or rename
      on 2023-03-10, 7 days after the end date, would otherwise be its closure,
      but the run stops, and renamed units keep that history to the sale as
      NEWX.
    - Units held as OLDFUND, a name with no dates, and renamed to LUFUND after
      its start, only warn while LUFUND reports. Half of them sold the day
      after the end date are a disposal from a fund that was then not a
      reporting fund (reg 17(2)), and do not empty the holding, so the run
      stops.
    """
    result = _run(
        tmp_path,
        [f"2016-08-01,BUY,{bought},10,20.00,5.00,GBP", f"{sold},22.00,5.00,GBP"],
        "--year",
        year,
        translations="LU0827875005,LUFUND",
        renames=renames,
    )

    assert result.returncode == 1
    assert expected in result.stderr
    assert "treats this as the fund's closure" not in result.stderr


@pytest.mark.parametrize(
    ("sold", "year", "expected"),
    [
        pytest.param(
            ["2019-06-03,SELL,VUAG,10,45.00,5.00,GBP"],
            "2019",
            "ERROR: cgt-calc cannot calculate the disposal of VUAG (ISIN "
            "IE00BFMXXD54) on 2019-06-03. cgt-calc's earliest figure for the fund, "
            "for the period ending 2018-06-30, shows it was a reporting fund from "
            "about 2017-06-30, and you have held units since before that date. The "
            "gain may be an offshore income gain",
            id="sold",
        ),
        pytest.param(
            [],
            "2017",
            "WARNING: cgt-calc's earliest figure for VUAG (ISIN IE00BFMXXD54), for "
            "the period ending 2018-06-30, shows it was a reporting fund from about "
            "2017-06-30, and you have held units since before that date. No disposal "
            f"in this report is affected. {LATER_GAIN} See {DOCS}\n",
            id="held",
        ),
    ],
)
def test_a_start_taken_from_a_figure_is_named_as_such(
    tmp_path: Path, sold: list[str], year: str, expected: str
) -> None:
    """VUAG bought on 2017-01-10, with a nil figure for a period ending 2018-06-30.

    The figure moves VUAG's start from HMRC's 2019-05-14 to 2017-06-30, a year
    before it, and the units were held before that, so the sale stops the run.
    HMRC's list does not show 2017-06-30, so the messages name the figure.
    The real start is only known to within a year, so the 2017/18 report, the
    year of the day before the moved start, names no regulation 48 election.
    """
    result = _run(
        tmp_path,
        ["2017-01-10,BUY,VUAG,10,40.00,5.00,GBP", *sold],
        "--year",
        year,
        reports=(_report(dt.date(2018, 6, 30), "0.00", "IE00BFMXXD54"),),
    )

    assert result.returncode == (1 if sold else 0), result.stderr
    assert expected in result.stderr


@pytest.mark.parametrize(
    ("bought", "sold", "renamed", "year", "expected"),
    [
        pytest.param(
            "2019-01-10,BUY,VUAG",
            "2019-06-03,SELL,NEWX",
            ("10/01/2019", "VUAG", "NEWX"),
            "2019",
            "ERROR: cgt-calc cannot calculate the disposal of NEWX on 2019-06-03. "
            "You held these units as VUAG before. HMRC's list shows VUAG (ISIN "
            "IE00BFMXXD54) became a reporting fund on 2019-05-14, and you have held "
            "units since before that date.",
            id="bought and renamed on one day",
        ),
        pytest.param(
            "2016-08-01,BUY,LUFUND",
            "2023-03-10,SELL,NEWX",
            ("10/03/2023", "LUFUND", "NEWX"),
            "2022",
            "ERROR: cgt-calc cannot calculate the disposal of LUFUND (ISIN "
            "LU0827875005) on 2023-03-10. HMRC's list shows the fund became a "
            "reporting fund on 2016-09-01, and you have held units since before that "
            "date.",
            id="sold under the new name on the rename day",
        ),
        pytest.param(
            "2022-08-01,BUY,LUFUND",
            "2023-06-01,SELL,NEWX",
            ("10/03/2023", "LUFUND", "NEWX"),
            "2023",
            None,
            id="a closure by rename in an earlier report",
        ),
    ],
)
def test_a_rename_day_keeps_the_holding_s_history(
    tmp_path: Path,
    bought: str,
    sold: str,
    renamed: tuple[str, str, str],
    year: str,
    expected: str | None,
) -> None:
    """Holdings renamed to NEWX, a name with no ISIN, on a day with other trades.

    - VUAG bought on 2019-01-10, before its 2019-05-14 start, and renamed the
      same day: no day's end saw the units as VUAG, but they were held before
      the fund reported, and the sale as NEWX stops the run.
    - LUFUND held since 2016-08-01, before its 2016-09-01 start, and sold as
      NEWX on the day of the rename: the sale is identified with the holding
      the day opened with, LUFUND, so it stops the run.
    - LUFUND bought on 2022-08-01 and renamed on 2023-03-10, 7 days after its
      2023-03-03 end date: a closure, but in the 2022/23 report, so the
      2023/24 report says nothing of it.
    """
    result = _run(
        tmp_path,
        [f"{bought},10,40.00,5.00,GBP", f"{sold},10,45.00,5.00,GBP"],
        "--year",
        year,
        translations=f"{VUAG}\nLU0827875005,LUFUND",
        renames=(renamed,),
    )

    assert result.returncode == (1 if expected else 0), result.stderr
    if expected is None:
        assert HMRC_LIST not in result.stderr
    else:
        assert expected in result.stderr


@pytest.mark.parametrize(
    ("sold", "year", "reports", "election"),
    [
        pytest.param(
            [], "2024", (LGMLP_2024,), REGULATION_100, id="the report it ended in"
        ),
        pytest.param(
            ["2024-10-11,SELL,LGMLP,5,18.00,5.00,GBP"],
            "2024",
            (LGMLP_2024,),
            REGULATION_100,
            id="half sold on the end date",
        ),
        pytest.param(
            [],
            "2025",
            (LGMLP_2024, _report(dt.date(2024, 10, 11), "0.00", "IE00BHZKHS06")),
            "",
            id="a later report",
        ),
    ],
)
def test_a_holding_kept_after_the_fund_stopped_reporting_warns(
    tmp_path: Path,
    sold: list[str],
    year: str,
    reports: tuple[str, ...],
    election: str,
) -> None:
    """LGMLP bought on 2023-08-01 and partly held after it ended on 2024-10-11.

    The fund is a reporting fund until the end of 2024-10-11 (reg 116(1)), so
    a sale that day is an ordinary one, and no disposal in the report is
    affected. The regulation 100 election's deemed disposal is on the end date
    (reg 100(8)), so it belongs in the 2024/25 return. The 2025/26 run has a
    nil figure for the final period, ending on the end date, whose income
    would otherwise be missing from it.
    """
    result = _run(
        tmp_path,
        ["2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP", *sold],
        "--year",
        year,
        reports=reports,
        translations=LGMLP,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: HMRC's list shows LGMLP (ISIN IE00BHZKHS06) stopped being a "
        "reporting fund on 2024-10-11, and you still held units after that date. "
        f"No disposal in this report is affected. {LATER_GAIN}{election} See "
        f"{DOCS}\n"
    ) in result.stderr
    assert CANNOT_CALCULATE not in result.stderr


def test_a_figure_after_the_end_date_shows_the_fund_carried_on(
    tmp_path: Path,
) -> None:
    """A figure for a period ending 2025-06-30, after LGMLP's end date.

    The fund reported after 2024-10-11, so it did not stop being a reporting
    fund then, and a sale on 2024-11-12 is an ordinary disposal.
    """
    result = _run(
        tmp_path,
        [
            "2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP",
            "2024-11-12,SELL,LGMLP,10,18.00,5.00,GBP",
        ],
        "--year",
        "2024",
        reports=(LGMLP_2024, _report(dt.date(2025, 6, 30), "0.00", "IE00BHZKHS06")),
        translations=LGMLP,
    )

    assert result.returncode == 0, result.stderr
    assert HMRC_LIST not in result.stderr


@pytest.mark.parametrize(
    ("sold", "stops"),
    [
        pytest.param("2019-04-05", False, id="the day before the report"),
        pytest.param("2019-04-06", True, id="the report's first day"),
        pytest.param("2020-04-05", True, id="the report's last day"),
    ],
)
def test_only_a_disposal_in_the_report_stops_it(
    tmp_path: Path, sold: str, stops: bool
) -> None:
    """VUAG bought on 2019-01-10 and sold in full around the 2019/20 report.

    A sale on 2019-04-05 is in 2018/19, and it emptied the holding, so the
    2019/20 report has nothing to say. A sale on 2020-04-05 was held at
    2019-06-30, whose figure is applied at D, 2019-12-30.
    """
    result = _run(
        tmp_path,
        [
            "2019-01-10,BUY,VUAG,10,40.00,5.00,GBP",
            f"{sold},SELL,VUAG,10,45.00,5.00,GBP",
        ],
        "--year",
        "2019",
    )

    assert result.returncode == (1 if stops else 0), result.stderr
    assert (
        f"cannot calculate the disposal of VUAG (ISIN IE00BFMXXD54) on {sold}."
        in result.stderr
    ) is stops
    assert HMRC_LIST in result.stderr if stops else HMRC_LIST not in result.stderr


@pytest.mark.parametrize(
    ("bought", "renamed", "sold", "expected"),
    [
        pytest.param(
            "2019-01-10", "01/06/2019", True, UNCHECKED, id="renamed after the start"
        ),
        pytest.param("2019-01-10", "01/06/2019", False, UNCHECKED, id="still held"),
        pytest.param(
            "2019-01-10",
            "01/03/2019",
            True,
            "ERROR: cgt-calc cannot calculate the disposal of VUAG (ISIN "
            "IE00BFMXXD54) on 2019-08-01. HMRC's list shows the fund became a "
            "reporting fund on 2019-05-14, and you have held units since before "
            "that date.",
            id="renamed before the start",
        ),
        pytest.param(
            "2019-05-20", "01/06/2019", True, None, id="bought after the start"
        ),
    ],
)
def test_units_renamed_from_a_name_cgt_calc_cannot_check(
    tmp_path: Path, bought: str, renamed: str, sold: bool, expected: str | None
) -> None:
    """OLDFUND, a name with no ISIN, renamed to VUAG, which started on 2019-05-14.

    cgt-calc has no dates for OLDFUND, which may have been another fund, so
    units it held before VUAG's start warn when renamed after it, whether sold
    on 2019-08-01 or still held. Renamed on 2019-03-01, before the start, the
    units were held as VUAG itself before it became a reporting fund, so the
    sale stops the run. Units OLDFUND bought on 2019-05-20, after the start,
    were not held before it. Every run holds the units at 2019-06-30, whose
    figure is applied at D, 2019-12-30.
    """
    result = _run(
        tmp_path,
        [
            f"{bought},BUY,OLDFUND,10,40.00,5.00,GBP",
            *(["2019-08-01,SELL,VUAG,10,45.00,5.00,GBP"] if sold else []),
        ],
        "--year",
        "2019",
        renames=((renamed, "OLDFUND", "VUAG"),),
    )

    assert result.returncode == (1 if expected and "ERROR" in expected else 0)
    if expected is None:
        assert HMRC_LIST not in result.stderr
    else:
        assert expected in result.stderr
        assert result.stderr.count(HMRC_LIST) == 1


@pytest.mark.parametrize(
    ("sold", "renamed", "expected"),
    [
        pytest.param(
            "2024-12-02,SELL,NEWFUND",
            "20/10/2024",
            "WARNING: HMRC's list shows LGMLP (ISIN IE00BHZKHS06) stopped being a "
            "reporting fund on 2024-10-11, and the rename of your holding on "
            "2024-10-20 emptied the holding. cgt-calc treats this as the fund's "
            "closure. If the fund carried on after 2024-10-11 without reporting "
            "status, the gain may be an offshore income gain, taxed as income, which "
            f"cgt-calc does not calculate. See {DOCS}\n",
            id="within 31 days",
        ),
        pytest.param(
            "2024-12-02,SELL,NEWFUND",
            "20/11/2024",
            "ERROR: cgt-calc cannot calculate the disposal of NEWFUND on 2024-12-02. "
            "You held these units as LGMLP before. HMRC's list shows LGMLP (ISIN "
            "IE00BHZKHS06) stopped being a reporting fund on 2024-10-11. A gain on "
            "units disposed of after that date may be an offshore income gain",
            id="later",
        ),
        pytest.param(
            "2024-10-20,SELL,LGMLP",
            "25/10/2024",
            "WARNING: HMRC's list shows LGMLP (ISIN IE00BHZKHS06) stopped being a "
            "reporting fund on 2024-10-11, and your disposal on 2024-10-20 emptied "
            "the holding.",
            id="after the holding was sold",
        ),
        pytest.param(
            "2024-10-20,SELL,LGMLP",
            "20/10/2024",
            "WARNING: HMRC's list shows LGMLP (ISIN IE00BHZKHS06) stopped being a "
            "reporting fund on 2024-10-11, and your disposal on 2024-10-20 emptied "
            "the holding.",
            id="on the day the holding was sold",
        ),
    ],
)
def test_a_rename_after_the_end_date(
    tmp_path: Path, sold: str, renamed: str, expected: str
) -> None:
    """LGMLP, which ended on 2024-10-11, renamed to NEWFUND.

    A rename within 31 days after the end date moves the holding as a closure
    would, so the fund is taken to have closed into NEWFUND, and the sale on
    2024-12-02 is NEWFUND's. Renamed 40 days after, the units were held after
    the fund stopped being a reporting fund, and that stays with them: the
    sale under the new name stops the run. A rename after the holding was sold,
    later or the same day, moves nothing, so only the sale is a closure.
    """
    result = _run(
        tmp_path,
        ["2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP", f"{sold},10,18.00,5.00,GBP"],
        "--year",
        "2024",
        reports=(LGMLP_2024,),
        translations=LGMLP,
        renames=((renamed, "LGMLP", "NEWFUND"),),
    )

    assert result.returncode == (0 if expected.startswith("WARNING") else 1)
    assert expected in result.stderr
    assert result.stderr.count(HMRC_LIST) == 1
