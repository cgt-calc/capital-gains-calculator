"""Tests for reporting periods a held fund has no excess reported income figure for.

A reporting fund reports every period, a nil one included (Offshore Funds (Tax)
Regulations 2009, regs 90 and 92(4)), so a period end with no row while units
were held is a figure missing from the data. Most runs use a made-up sterling
fund, XS0123456781 held as EXAMPLE, with its rows supplied by --eri-raw-file:
sterling needs no exchange rate, and the fund has no bundled data, so cgt-calc
has no HMRC dates for it. A period's income is treated as received six months
after its end (reg 94(3)), called D below.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta
import pytest

from cgt_calc.dates import get_tax_year_for_date, get_tax_year_start
from tests.utils import build_cmd

if TYPE_CHECKING:
    from pathlib import Path

ISIN = "XS0123456781"
OTHER_ISIN = "XS9999999998"
RAW_HEADER = "date,action,symbol,quantity,price,fees,currency\n"
ERI_HEADER = (
    "ISIN,Fund Reporting Period End Date,Currency,"
    "Excess of reporting income over distribution\n"
)
DOCS = "https://cgt-calc.uk/offshore-funds/#missing-reporting-periods"
ADVICE_ONE = (
    "Nothing was added to the cost of your units for this period, so a gain on "
    "them may be shown too high. A newer cgt-calc may include the figure: upgrade "
    "first. If the latest release still lacks it, add it from the fund's report "
    f"with --eri-raw-file, entering 0 if the report states nil. See {DOCS}"
)
ADVICE_MANY = (
    "Nothing was added to the cost of your units for these periods, so a gain on "
    "them may be shown too high. A newer cgt-calc may include the figures: upgrade "
    "first. If the latest release still lacks one, add it from the fund's report "
    f"with --eri-raw-file, entering 0 if the report states nil. See {DOCS}"
)
STOP_ADVICE = (
    "A newer cgt-calc may include these figures: upgrade first "
    "(https://cgt-calc.uk/installation/#upgrade-cgt-calc). If the latest release "
    "still lacks one, add it from the fund's report with --eri-raw-file, entering "
    "0 if the report states nil. If the fund has not published a figure, ask the "
    "fund for it; if it cannot give one, take professional advice before entering "
    "one. If the fund was not a reporting fund for the period, see "
    f"{DOCS}"
)
DISPOSAL_NOTE = (
    "You disposed of units of this holding in this report. If the fund was not a "
    "reporting fund for one of these periods, the gain may be taxed as income (an "
    "offshore income gain), which cgt-calc does not calculate."
)
MISSING_FIGURE_WARNING = "No excess reported income figure"


def _report(period_end: dt.date, amount: str = "0.50", isin: str = ISIN) -> str:
    """Return a sterling ERI row for `period_end`."""
    return f"{isin},{period_end:%d/%m/%Y},GBP,{amount}"


def _run(
    tmp_path: Path,
    trades: list[str],
    reports: list[str],
    *options: str,
    translations: str = f"{ISIN},EXAMPLE",
) -> subprocess.CompletedProcess[str]:
    """Run cgt-calc over RAW `trades` and ERI `reports`, whatever it returns."""
    raw = tmp_path / "raw.csv"
    raw.write_text(RAW_HEADER + "".join(f"{row}\n" for row in trades), "utf-8")
    eri = tmp_path / "eri.csv"
    eri.write_text(ERI_HEADER + "".join(f"{row}\n" for row in reports), "utf-8")
    isins = tmp_path / "isins.csv"
    isins.write_text(f"ISIN,symbol\n{translations}\n", "utf-8")
    if "--output" not in options:
        options = (*options, "--no-report")
    return subprocess.run(
        build_cmd(
            "--raw-file",
            str(raw),
            "--eri-raw-file",
            str(eri),
            "--isin-translation-file",
            str(isins),
            "--no-balance-check",
            *options,
        ),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def _recent(day: dt.date) -> dt.date:
    """Return `day`, or the 27th of its month when it is later in the month.

    A date on or before the 27th steps a year or six months to the same day of
    the month, so a run built from today has no month end to round.
    """
    return day if day.day <= 27 else day.replace(day=27)


def _tax_year(day: dt.date) -> str:
    year = get_tax_year_for_date(day)
    return f"{year}/{(year + 1) % 100:02d}"


def _next_period_end() -> dt.date:
    """Return a day after today on or before the 27th of its month."""
    day = dt.date.today() + dt.timedelta(days=1)
    return day if day.day <= 27 else (day + relativedelta(months=1)).replace(day=1)


NEXT_END = _next_period_end()


def test_a_missing_period_whose_income_falls_after_the_report_warns(
    tmp_path: Path,
) -> None:
    """A period end after the last row, held, with its income in a later year.

    Rows end 2020-12-31 and 2021-12-31, so the next period ends around
    2022-12-31, inside the 2022/23 report. Its D is 2023-06-30, in 2023/24 and
    long past, so the report was due. Nothing is missing from 2022/23's income,
    so it is a warning and the PDF is written. The period before the first row,
    2019-12-31, is not named: the units were bought on 2020-06-01.
    """
    output = tmp_path / "report.pdf"
    result = _run(
        tmp_path,
        ["2020-06-01,BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(dt.date(2020, 12, 31)), _report(dt.date(2021, 12, 31))],
        "--year",
        "2022",
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: No excess reported income figure for EXAMPLE (ISIN XS0123456781) "
        "for a reporting period when you held units. cgt-calc's figures for this "
        "fund cover periods ending 2020-12-31 to 2021-12-31.\n"
        "  Period predicted to end around 2022-12-31: its report was due around "
        "2023-06-30, and any income from it falls in the 2023/24 tax year.\n"
        f"{ADVICE_ONE}\n"
    ) in result.stderr
    assert result.stderr.count(MISSING_FIGURE_WARNING) == 1
    assert output.exists()


def test_income_missing_from_a_finished_report_stops_the_run(tmp_path: Path) -> None:
    """A period end after the last row whose income falls in a finished report.

    Rows end 2020-06-30 and 2021-06-30, so the next period ends around
    2022-06-30. Its D, 2022-12-30, is inside 2022/23, which has ended: the
    report would leave income out, so the run stops with no report printed.
    """
    result = _run(
        tmp_path,
        ["2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(dt.date(2020, 6, 30)), _report(dt.date(2021, 6, 30))],
        "--year",
        "2022",
    )

    assert result.returncode == 1
    assert (
        "ERROR: Excess reported income is missing from the income in this report.\n"
        "  EXAMPLE (ISIN XS0123456781): no figure for the reporting period "
        "predicted to end around 2022-06-30, when you held units. Its income is "
        "treated as received on 2022-12-30. cgt-calc's figures for this fund cover "
        "periods ending 2020-06-30 to 2021-06-30.\n"
        f"{STOP_ADVICE}\n"
    ) in result.stderr
    assert "Tax summary" not in result.stdout


def test_missing_income_in_a_report_still_running_holds_back_the_pdf(
    tmp_path: Path,
) -> None:
    """The same gap in the tax year in progress warns and writes no PDF.

    The dates are set from today. D is today, or the 27th of this month when
    today is later, so the report was due; the period ended six months before
    D, a year after the last row. D's tax year has not ended, so the income
    may still be added: a warning, and the PDF waits.
    """
    distribution = _recent(dt.date.today())
    period_end = distribution - relativedelta(months=6)
    last_row = period_end - relativedelta(months=12)
    output = tmp_path / "report.pdf"
    result = _run(
        tmp_path,
        [f"{last_row - relativedelta(months=18)},BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(last_row - relativedelta(months=12)), _report(last_row)],
        "--year",
        str(get_tax_year_for_date(distribution)),
        "--output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert (
        f"  Period predicted to end around {period_end}: its income is treated as "
        f"received on {distribution}, inside this report, and its report was due "
        "around then.\n"
    ) in result.stderr
    assert (
        "WARNING: The PDF report was not written: the income in this report lacks "
        "the excess reported income listed above. A newer cgt-calc may include "
        "these figures: upgrade first. Otherwise add each figure with "
        "--eri-raw-file once the fund has published it, and run cgt-calc again.\n"
    ) in result.stderr
    assert "Done! Calculations complete (PDF report not written)." in result.stderr
    assert not output.exists()


def test_a_period_whose_report_is_not_due_yet(tmp_path: Path) -> None:
    """A period that ended yesterday has until D for its report.

    The dates are set from today: the period ends yesterday, or on the 27th of
    this month, a year after the last row. The window runs from the start of
    that tax year to the period end, so D, six months on, is after it and after
    today.
    """
    period_end = _recent(dt.date.today() - dt.timedelta(days=1))
    last_row = period_end - relativedelta(months=12)
    distribution = period_end + relativedelta(months=6)
    result = _run(
        tmp_path,
        [f"{last_row - relativedelta(months=1)},BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(last_row)],
        "--from",
        str(get_tax_year_start(get_tax_year_for_date(period_end))),
        "--to",
        str(period_end),
    )

    assert result.returncode == 0, result.stderr
    assert (
        f"cgt-calc's figures for this fund cover the period ending {last_row} only.\n"
        f"  Period predicted to end around {period_end}: the fund has until around "
        f"{distribution} to publish its report, and any income from it falls in "
        f"the {_tax_year(distribution)} tax year.\n"
    ) in result.stderr


@pytest.mark.parametrize(
    ("trades", "reports", "options"),
    [
        pytest.param(
            ["2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
            [
                _report(dt.date(2020, 6, 30)),
                _report(dt.date(2021, 6, 30), "0.00"),
                _report(dt.date(2022, 6, 30)),
            ],
            ["--year", "2022"],
            id="a nil report covers its period",
        ),
        pytest.param(
            [
                "2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
                "2022-05-01,SELL,EXAMPLE,10,110.00,5.00,GBP",
            ],
            [_report(dt.date(2020, 6, 30)), _report(dt.date(2021, 6, 30))],
            ["--year", "2022"],
            id="sold before the period ended",
        ),
        pytest.param(
            [
                "2019-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
                "2021-11-01,SELL,EXAMPLE,10,110.00,5.00,GBP",
            ],
            [
                _report(dt.date(2019, 6, 30)),
                _report(dt.date(2020, 6, 30)),
                _report(dt.date(2020, 12, 31)),
                _report(dt.date(2021, 10, 15)),
            ],
            ["--year", "2021"],
            id="a new year end and a final short period",
        ),
        pytest.param(
            ["2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
            [_report(dt.date(2020, 6, 30)), _report(dt.date(2021, 6, 30))],
            ["--year", "2022", "--non-uk-resident-periods", "2022-10-01:2023-03-31"],
            id="income while not UK resident",
        ),
        pytest.param(
            ["2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
            [_report(dt.date(2020, 6, 30)), _report(dt.date(2021, 7, 31))],
            ["--year", "2021"],
            id="a period end a month later",
        ),
        pytest.param(
            ["2016-05-01,BUY,QDIV,10,30.00,0.00,GBP"],
            [],
            ["--year", "2019"],
            id="a share whose ticker is also a fund's",
        ),
        pytest.param(
            [f"{NEXT_END - relativedelta(months=13)},BUY,EXAMPLE,10,100.00,5.00,GBP"],
            [_report(NEXT_END - relativedelta(months=12))],
            ["--year", str(get_tax_year_for_date(NEXT_END))],
            id="a period that has not ended yet",
        ),
    ],
)
def test_nothing_is_missing(
    tmp_path: Path, trades: list[str], reports: list[str], options: list[str]
) -> None:
    """Runs where every period the units were held through has a row.

    - A nil report is a row: 2021-06-30 has one at 0.00.
    - Units sold on 2022-05-01 were not held at the period predicted to end
      around 2022-06-30.
    - A short period from 2020-07-01 to 2020-12-31 moves the year end, so the
      next row a year later is not a gap; the last period, 2021-01-01 to
      2021-10-15, ends before the sale on 2021-11-01.
    - The period predicted to end around 2022-06-30 has its D, 2022-12-30,
      inside the non-residence period, so its income is not taxed here.
    - A row on 2021-07-31, a year and a month after 2020-06-30, is the next
      period with its year end moved, not a period missed in between: 2021-06-30
      is within 31 days of it.
    - The RAW holding QDIV has no ISIN. cgt-calc's bundled list gives the
      ticker to a fund whose reports are all nil (IE00BKM4H312, periods ending
      31 October from 2019), but QDIV is also a US symbol, so the holding may
      not be that fund and is not checked. In sterling, so no exchange rate is
      needed. All-nil reports are the case this guards; if the bundled data
      gives the fund a figure, move it to another such fund, such as SAWG
      (IE00BKBF6K52).
    - The period a year after the last row ends after today, inside the tax
      year in progress, so its report is not missing yet.
    """
    result = _run(tmp_path, trades, reports, *options)

    assert result.returncode == 0, result.stderr
    assert MISSING_FIGURE_WARNING not in result.stderr


def test_a_gap_between_two_rows(tmp_path: Path) -> None:
    """Rows two years apart leave the year between them missing.

    Rows end 2018-06-30 and 2020-06-30; the period ending around 2019-06-30 has
    none. Its D, 2019-12-30, is in 2019/20, before the 2020/21 report. The
    period after the last row, 2021-06-30, ends after 2020/21.
    """
    result = _run(
        tmp_path,
        ["2018-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(dt.date(2018, 6, 30)), _report(dt.date(2020, 6, 30))],
        "--year",
        "2020",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "cgt-calc's figures for this fund cover periods ending 2018-06-30 to "
        "2020-06-30.\n"
        "  Period predicted to end around 2019-06-30: its report was due around "
        "2019-12-30, and any income from it belonged in your 2019/20 tax return.\n"
        f"{ADVICE_ONE}\n"
    ) in result.stderr


@pytest.mark.parametrize(
    ("bought", "expected"),
    [
        pytest.param(
            "2020-01-10",
            "  Period predicted to end around 2020-06-30: its report was due around "
            "2020-12-30, and any income from it belonged in your 2020/21 tax "
            "return.\n",
            id="held before the first row",
        ),
        pytest.param("2020-07-10", None, id="bought after that period"),
    ],
)
def test_a_period_before_the_first_row(
    tmp_path: Path, bought: str, expected: str | None
) -> None:
    """The period a year before the first row is missing only for a holder then.

    Rows end 2021-06-30 and 2022-06-30, so the period before ends around
    2020-06-30, with D on 2020-12-30 in 2020/21. A share bought on 2020-01-02
    starts the history earlier, so 2020-06-30 is a day cgt-calc looks at either
    way.
    """
    result = _run(
        tmp_path,
        [
            "2020-01-02,BUY,SHARE,10,10.00,0.00,GBP",
            f"{bought},BUY,EXAMPLE,10,100.00,5.00,GBP",
        ],
        [_report(dt.date(2021, 6, 30)), _report(dt.date(2022, 6, 30))],
        "--year",
        "2022",
    )

    assert result.returncode == 0, result.stderr
    if expected is None:
        assert MISSING_FIGURE_WARNING not in result.stderr
    else:
        assert expected in result.stderr


def test_no_period_is_predicted_before_december_2010(tmp_path: Path) -> None:
    """A fund without HMRC dates has no period end before 2010-12-01.

    A twelve-month period ending earlier began before the regulations came into
    force on 1 December 2009. Held since 2006, before a first row ending
    2015-06-30, the units were held through the periods ending around
    2011-06-30 to 2014-06-30, all named in one block; 2010-06-30 is not. Each D
    is 30 December of that year, in that year's tax year.
    """
    result = _run(
        tmp_path,
        ["2006-05-01,BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(dt.date(2015, 6, 30)), _report(dt.date(2016, 6, 30))],
        "--year",
        "2016",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: No excess reported income figure for EXAMPLE (ISIN XS0123456781) "
        "for reporting periods when you held units. cgt-calc's figures for this "
        "fund cover periods ending 2015-06-30 to 2016-06-30.\n"
        + "".join(
            f"  Period predicted to end around {year}-06-30: its report was due "
            f"around {year}-12-30, and any income from it belonged in your "
            f"{year}/{(year + 1) % 100:02d} tax return.\n"
            for year in range(2011, 2015)
        )
        + f"{ADVICE_MANY}\n"
    ) in result.stderr


def test_a_period_end_at_a_month_end_stays_at_one(tmp_path: Path) -> None:
    """A row on 28 February predicts 29 February in a leap year.

    Rows end 2018-02-28 and 2019-02-28, both the last day of their month, so
    the periods before end around 2017-02-28 and 2016-02-29. Units bought on
    2015-12-01 were held at both.
    """
    result = _run(
        tmp_path,
        ["2015-12-01,BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(dt.date(2018, 2, 28)), _report(dt.date(2019, 2, 28))],
        "--year",
        "2018",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "  Period predicted to end around 2016-02-29: its report was due around "
        "2016-08-29, and any income from it belonged in your 2016/17 tax return.\n"
        "  Period predicted to end around 2017-02-28: its report was due around "
        "2017-08-28, and any income from it belonged in your 2017/18 tax return.\n"
    ) in result.stderr


TWO_GAP_ROWS = [_report(dt.date(2018, 6, 30)), _report(dt.date(2021, 6, 30))]


@pytest.mark.parametrize(
    ("trades", "reports", "missing", "noted"),
    [
        pytest.param(
            [
                "2018-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
                "2021-08-01,SELL,EXAMPLE,5,110.00,5.00,GBP",
            ],
            TWO_GAP_ROWS,
            ["2019-06-30", "2020-06-30"],
            True,
            id="sold in the report",
        ),
        pytest.param(
            ["2018-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
            TWO_GAP_ROWS,
            ["2019-06-30", "2020-06-30"],
            False,
            id="no disposal",
        ),
        pytest.param(
            [
                "2018-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
                "2021-03-01,SELL,EXAMPLE,5,110.00,5.00,GBP",
            ],
            TWO_GAP_ROWS,
            ["2019-06-30", "2020-06-30"],
            False,
            id="sold before the report",
        ),
        pytest.param(
            [
                "2019-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
                "2021-05-01,SELL,EXAMPLE,5,110.00,5.00,GBP",
            ],
            [_report(dt.date(2019, 12, 31)), _report(dt.date(2020, 12, 31))],
            ["2021-12-31"],
            True,
            id="sold during the missing period",
        ),
    ],
)
def test_a_disposal_in_the_report_warns_of_an_income_gain(
    tmp_path: Path,
    trades: list[str],
    reports: list[str],
    missing: list[str],
    noted: bool,
) -> None:
    """A sale may be taxed as income if the fund was not reporting for a gap.

    cgt-calc has no HMRC dates for the made-up fund, so it cannot tell a
    missing figure from a year the fund was not a reporting fund. A gain is an
    offshore income gain if the fund was non-reporting at the disposal (reg
    17(2)), or at some time after the units were bought (reg 17(3)). Every
    missing period began before the report, so the note needs a sale in it:

    - Rows ending 2018-06-30 and 2021-06-30 leave the periods ending around
      2019-06-30 and 2020-06-30 missing, both before 2021/22. A sale on
      2021-08-01 is in the report; one on 2021-03-01 is before it.
    - Rows ending 2019-12-31 and 2020-12-31 leave the period from 2021-01-01
      to around 2021-12-31 missing, with its D, 2022-06-30, after 2021/22. A
      sale on 2021-05-01 is in the report and during that period.
    """
    result = _run(tmp_path, trades, reports, "--year", "2021")

    assert result.returncode == 0, result.stderr
    for period_end in missing:
        assert f"  Period predicted to end around {period_end}:" in result.stderr
    assert (DISPOSAL_NOTE in result.stderr) is noted
    if noted:
        # The note ends the block, after its link.
        assert f"See {DOCS}\n{DISPOSAL_NOTE}\n" in result.stderr


def test_income_missing_for_two_funds_is_one_error(tmp_path: Path) -> None:
    """Every fund whose income is missing is named in the one error.

    Both funds have rows ending 2020-06-30 and 2021-06-30 and were held through
    the period ending around 2022-06-30, whose D is in 2022/23.
    """
    result = _run(
        tmp_path,
        [
            "2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
            "2020-01-10,BUY,OTHER,10,100.00,5.00,GBP",
        ],
        [
            _report(dt.date(2020, 6, 30)),
            _report(dt.date(2021, 6, 30)),
            _report(dt.date(2020, 6, 30), isin=OTHER_ISIN),
            _report(dt.date(2021, 6, 30), isin=OTHER_ISIN),
        ],
        "--year",
        "2022",
        translations=f"{ISIN},EXAMPLE\n{OTHER_ISIN},OTHER",
    )

    assert result.returncode == 1
    assert (
        "ERROR: Excess reported income is missing from the income in this report.\n"
        "  EXAMPLE (ISIN XS0123456781): no figure for the reporting period "
        "predicted to end around 2022-06-30, when you held units. Its income is "
        "treated as received on 2022-12-30. cgt-calc's figures for this fund cover "
        "periods ending 2020-06-30 to 2021-06-30.\n"
        "  OTHER (ISIN XS9999999998): no figure for the reporting period predicted "
        "to end around 2022-06-30, when you held units. Its income is treated as "
        "received on 2022-12-30. cgt-calc's figures for this fund cover periods "
        "ending 2020-06-30 to 2021-06-30.\n"
    ) in result.stderr


def test_adding_the_missing_figure_lets_the_run_finish(tmp_path: Path) -> None:
    """With the row for 2022-06-30 added, nothing is missing and it is counted.

    The run that stopped without it now finishes. 10 units cost £1,000 plus
    £5 fees. Each row adds its excess per unit times the 10 units held at the
    period end: £5.00 for 2020-06-30 and for 2021-06-30 at 0.50, and £4.00 for
    2022-06-30 at 0.40, so the pool costs £1,019.00. Of these, only the
    2022-06-30 figure has its D, 2022-12-30, in 2022/23, so the report's ERI
    income is £4.00.
    """
    result = _run(
        tmp_path,
        ["2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [
            _report(dt.date(2020, 6, 30)),
            _report(dt.date(2021, 6, 30)),
            _report(dt.date(2022, 6, 30), "0.40"),
        ],
        "--year",
        "2022",
    )

    assert result.returncode == 0, result.stderr
    assert MISSING_FIGURE_WARNING not in result.stderr
    assert (
        "Portfolio at the end of 2022/2023 tax year\n  EXAMPLE: 10.00, £1,019.00\n"
    ) in result.stdout
    assert (
        "Excess Reported Income\n  EXAMPLE: £4.00 (included as dividend)\n"
    ) in result.stdout


def test_a_custom_window_that_has_ended_stops(tmp_path: Path) -> None:
    """A --from/--to window ending before today is finished, whatever the year.

    The dates are set from today. The window ends yesterday, inside the tax
    year in progress except on 6 April. D is yesterday, or the 27th of its
    month, so inside the window; its period ended six months earlier, a year
    after the last row.
    """
    window_end = dt.date.today() - dt.timedelta(days=1)
    distribution = _recent(window_end)
    period_end = distribution - relativedelta(months=6)
    last_row = period_end - relativedelta(months=12)
    result = _run(
        tmp_path,
        [f"{last_row - relativedelta(months=1)},BUY,EXAMPLE,10,100.00,5.00,GBP"],
        [_report(last_row)],
        "--from",
        str(get_tax_year_start(get_tax_year_for_date(distribution))),
        "--to",
        str(window_end),
    )

    assert result.returncode == 1
    assert (
        f"  EXAMPLE (ISIN XS0123456781): no figure for the reporting period "
        f"predicted to end around {period_end}, when you held units. Its income is "
        f"treated as received on {distribution}."
    ) in result.stderr


def test_a_fund_is_not_given_periods_before_hmrc_lists_it(tmp_path: Path) -> None:
    """Periods start after HMRC's start date, and a known fund gets no income note.

    HMRC lists LU0274208692 as a reporting fund from 2011-01-01; its first row
    ends 2024-12-31. Held from 2010-06-01 to a sale on 2012-03-01, the units
    were held at the period end predicted around 2011-12-31, and at 2010-12-31,
    which is before the start, so not a reporting period. 2011-12-31's D,
    2012-06-30, is after the 2011/12 report. The fund's status is known, so the
    sale gets no warning that its gain may be income here; that check is
    HMRC's dates, not a guess.
    """
    result = _run(
        tmp_path,
        [
            "2010-06-01,BUY,XTRK,10,20.00,5.00,GBP",
            "2012-03-01,SELL,XTRK,10,22.00,5.00,GBP",
        ],
        [],
        "--year",
        "2011",
        translations="LU0274208692,XTRK",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "WARNING: No excess reported income figure for XTRK (ISIN LU0274208692) "
        "for a reporting period when you held units. cgt-calc's figures for this "
        "fund cover periods ending 2024-12-31 to 2025-12-31.\n"
        "  Period predicted to end around 2011-12-31: its report was due around "
        "2012-06-30, and any income from it falls in the 2012/13 tax year.\n"
        f"{ADVICE_ONE}\n"
    ) in result.stderr
    assert DISPOSAL_NOTE not in result.stderr


@pytest.mark.parametrize(
    ("reports", "expected"),
    [
        pytest.param(
            [],
            "  Period predicted to end around 2024-10-11: its report was due around "
            "2025-04-11, and any income from it falls in the 2025/26 tax year.\n",
            id="the end date is its final period",
        ),
        pytest.param(
            [_report(dt.date(2024, 10, 1), "0.00", "IE00BHZKHS06")],
            None,
            id="a row within 31 days of the end date",
        ),
        pytest.param(
            [_report(dt.date(2025, 6, 30), "0.00", "IE00BHZKHS06")],
            None,
            id="a row after the end date",
        ),
    ],
)
def test_hmrc_end_date_is_a_fund_s_last_period_end(
    tmp_path: Path, reports: list[str], expected: str | None
) -> None:
    """A fund leaving HMRC's list reports a final period ending then.

    HMRC lists IE00BHZKHS06 as a reporting fund until 2024-10-11, and the only
    bundled row ends 2023-06-30; L&G published no later figure. Units bought on
    2023-08-01 and redeemed on 2024-10-20 were held at the period end predicted
    around 2024-06-30, whose D, 2024-12-30, is in the finished 2024/25 report,
    so the run stops; and at the end date, whose D, 2025-04-11, is in 2025/26.

    - A row within 31 days of the end date is the final period, so the end date
      is not predicted again.
    - A row after the end date shows the fund did not end then; the year
      between the two rows is still missing.
    """
    result = _run(
        tmp_path,
        [
            "2023-08-01,BUY,LGMLP,10,20.00,5.00,GBP",
            "2024-10-20,SELL,LGMLP,10,18.00,5.00,GBP",
        ],
        reports,
        "--year",
        "2024",
        translations="IE00BHZKHS06,LGMLP",
    )

    assert result.returncode == 1
    assert (
        "  LGMLP (ISIN IE00BHZKHS06): no figure for the reporting period predicted "
        "to end around 2024-06-30, when you held units. Its income is treated as "
        "received on 2024-12-30."
    ) in result.stderr
    if expected is None:
        assert "around 2024-10-11" not in result.stderr
    else:
        assert expected in result.stderr


def test_a_yearly_period_end_just_before_the_end_date_is_not_a_second_one(
    tmp_path: Path,
) -> None:
    """The final period ends on the end date, not also a few days before it.

    HMRC lists IE00BHZKHS06 as a reporting fund until 2024-10-11. With a
    made-up figure for a period ending 2023-10-01, a year on is 2024-10-01,
    ten days before the end date: one final period, predicted on the end date
    only. Units bought on 2023-11-01 and redeemed on 2024-10-20 were held
    then. Its D, 2025-04-11, is after 2024/25, so it is a warning.
    """
    result = _run(
        tmp_path,
        [
            "2023-11-01,BUY,LGMLP,10,20.00,5.00,GBP",
            "2024-10-20,SELL,LGMLP,10,18.00,5.00,GBP",
        ],
        [_report(dt.date(2023, 10, 1), "0.00", "IE00BHZKHS06")],
        "--year",
        "2024",
        translations="IE00BHZKHS06,LGMLP",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "for a reporting period when you held units. cgt-calc's figures for this "
        "fund cover periods ending 2023-06-30 to 2023-10-01.\n"
        "  Period predicted to end around 2024-10-11: its report was due around "
        "2025-04-11, and any income from it falls in the 2025/26 tax year.\n"
    ) in result.stderr


@pytest.mark.parametrize(
    ("bought_back", "stops"),
    [
        pytest.param("2022-07-05", True, id="within 30 days"),
        pytest.param("2022-08-05", False, id="after 30 days"),
    ],
)
def test_units_bought_back_within_30_days_count_as_held(
    tmp_path: Path, bought_back: str, stops: bool
) -> None:
    """Units sold before a period end and bought back after it count as held.

    A sale identified with a repurchase in the next period is treated as held
    at the period end (reg 94(3A)). All 10 units are sold on 2022-06-20, ten
    days before the period predicted to end around 2022-06-30. Bought back on
    2022-07-05, within 30 days, the sale is matched with them, the units count
    as held, and the period's income, with D on 2022-12-30 in the finished
    2022/23, is missing. Bought back on 2022-08-05, the sale is not matched,
    and nothing was held at the period end.
    """
    result = _run(
        tmp_path,
        [
            "2020-01-10,BUY,EXAMPLE,10,100.00,5.00,GBP",
            "2022-06-20,SELL,EXAMPLE,10,110.00,5.00,GBP",
            f"{bought_back},BUY,EXAMPLE,10,105.00,5.00,GBP",
        ],
        [_report(dt.date(2020, 6, 30)), _report(dt.date(2021, 6, 30))],
        "--year",
        "2022",
    )

    assert result.returncode == (1 if stops else 0), result.stderr
    assert (
        "no figure for the reporting period predicted to end around 2022-06-30"
        in result.stderr
    ) is stops
    assert MISSING_FIGURE_WARNING not in result.stderr
