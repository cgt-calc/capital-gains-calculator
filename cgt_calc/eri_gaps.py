"""Find reporting periods of a held fund that have no excess reported income figure.

A reporting fund reports on every reporting period within six months of its end,
and a period with no excess is reported as nil (Offshore Funds (Tax)
Regulations 2009, regs 90 and 92(4)). So once cgt-calc has a fund's figures, a
period end with no row while the user held units is a figure missing from the
data, not a period with nothing to report. Period ends are predicted a year
apart from the rows the run has, bounded for a fund with bundled data by the
dates HMRC's list gives it as a reporting fund.
"""

from __future__ import annotations

from collections import defaultdict
import csv
import datetime as dt
import functools
from importlib import resources
import io
import itertools
import logging
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta

from .const import ERI_TAX_DATE_DELTA
from .dates import get_tax_year_for_date
from .exceptions import CalculationError
from .resources import RESOURCES_PACKAGE

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from .model import DatePeriod, HmrcTransactionLog, Isin

LOGGER = logging.getLogger(__name__)

REPORTING_FUNDS_FILE = "reporting_funds.csv"
PERIOD_MONTHS = 12
# Two period ends this close are one period end, moved or reported late.
SLACK = dt.timedelta(days=31)
# The regulations came into force on 1 December 2009 (reg 1(1)), so a
# twelve-month period ending earlier than a year later began before them.
REGULATIONS_FLOOR = dt.date(2010, 12, 1)
DOCS_URL = "https://cgt-calc.uk/offshore-funds/#missing-reporting-periods"
UPGRADE_URL = "https://cgt-calc.uk/installation/#upgrade-cgt-calc"


@functools.cache
def reporting_fund_dates() -> dict[str, tuple[dt.date, dt.date | None]]:
    """Return HMRC's start and end dates for each bundled fund, by ISIN.

    The end is None while the fund is still a reporting fund.
    """
    text = (
        resources.files(RESOURCES_PACKAGE)
        .joinpath(REPORTING_FUNDS_FILE)
        .read_text(encoding="utf-8")
    )
    return {
        row["isin"]: (
            dt.date.fromisoformat(row["start"]),
            dt.date.fromisoformat(row["end"]) if row["end"] else None,
        )
        for row in csv.DictReader(io.StringIO(text))
    }


def _step(anchor: dt.date, months: int) -> dt.date:
    """Return the date `months` from `anchor`, kept at a month end if it is one."""
    day = anchor + relativedelta(months=months)
    if (anchor + dt.timedelta(days=1)).day == 1:
        return day + relativedelta(day=31)
    return day


def predicted_period_ends(
    rows: Collection[dt.date],
    status: tuple[dt.date, dt.date | None] | None,
    until: dt.date,
) -> list[dt.date]:
    """Return the period ends a fund with these rows should also have reported.

    Ends are predicted a year apart before the first row, after the last and
    across a gap of more than a year and 31 days between two rows. `status`
    is HMRC's start and end date, known for the funds with bundled data. A
    fund with them has none on or before its start date and none after its
    end date, and its end date is its final period end unless a row lies
    within 31 days of it. Any other fund has none before 1 December 2010. A
    row after the end date outranks it: the fund did not end then. None is
    after `until`, wherever the rows are: the 30-day rule looks at days past
    the report.
    """
    ends = sorted(rows)
    first, last = ends[0], ends[-1]
    start, end = status if status is not None else (None, None)
    if end is not None and last > end:
        end = None

    predicted = []
    for count in itertools.count(1):
        day = _step(first, -PERIOD_MONTHS * count)
        if day < REGULATIONS_FLOOR if start is None else day <= start:
            break
        predicted.append(day)
    for earlier, later in itertools.pairwise(ends):
        for count in itertools.count(1):
            day = _step(earlier, PERIOD_MONTHS * count)
            if day >= later - SLACK:
                break
            predicted.append(day)

    final = end is not None and all(abs(row - end) > SLACK for row in ends)
    for count in itertools.count(1):
        day = _step(last, PERIOD_MONTHS * count)
        # A predicted end date stops the yearly ends as a later row would.
        if day > until or (
            end is not None and (day >= end - SLACK if final else day > end)
        ):
            break
        predicted.append(day)
    if end is not None and final:
        predicted.append(end)
    return sorted(day for day in predicted if day <= until)


def predict_missing_period_ends(
    rows_by_isin: Mapping[Isin, Collection[dt.date]],
    holdings_by_isin: Mapping[Isin, Collection[str]],
    unchecked: Collection[str],
    non_uk_resident_periods: Collection[DatePeriod],
    until: dt.date,
) -> dict[dt.date, dict[str, Isin]]:
    """Return each predicted period end, by date and holding, with its fund.

    A holding in `unchecked` reaches its fund's rows only through a ticker that
    is also a US symbol, so it may not be that fund and is not checked. A
    period whose income falls in a period of non-residence is not charged to
    UK tax, so its figure is not needed.
    """
    dates = reporting_fund_dates()
    predicted: dict[dt.date, dict[str, Isin]] = defaultdict(dict)
    for isin, rows in rows_by_isin.items():
        holdings = sorted(set(holdings_by_isin.get(isin, ())) - set(unchecked))
        if not holdings:
            continue
        for day in predicted_period_ends(rows, dates.get(isin), until):
            distribution = day + ERI_TAX_DATE_DELTA
            if any(period.includes(distribution) for period in non_uk_resident_periods):
                continue
            for holding in holdings:
                predicted[day][holding] = isin
    return predicted


def _tax_year(day: dt.date) -> str:
    year = get_tax_year_for_date(day)
    return f"{year}/{(year + 1) % 100:02d}"


def _figures_cover(rows: Collection[dt.date]) -> str:
    ends = sorted(rows)
    if len(ends) == 1:
        return f"cover the period ending {ends[0]} only"
    return f"cover periods ending {ends[0]} to {ends[-1]}"


def report_missing_eris(
    missing: Mapping[tuple[Isin, dt.date], str],
    rows_by_isin: Mapping[Isin, Collection[dt.date]],
    holdings_by_isin: Mapping[Isin, Collection[str]],
    disposals: HmrcTransactionLog,
    window: tuple[dt.date, dt.date],
    today: dt.date,
) -> bool:
    """Warn about each missing figure, and stop when one leaves income out.

    `missing` gives the holding that had units at each fund's predicted period
    end with no row. A figure whose income falls in a finished report window
    stops the run; one in a window still running holds the PDF back, which the
    return value asks for; any other is a warning.
    """
    window_start, window_end = window
    finished = window_end < today
    by_isin: dict[Isin, list[tuple[dt.date, str]]] = defaultdict(list)
    stops = []
    running = False
    for (isin, period_end), holding in sorted(missing.items()):
        distribution = period_end + ERI_TAX_DATE_DELTA
        in_window = window_start <= distribution <= window_end
        if in_window and finished:
            stops.append(
                f"  {holding} (ISIN {isin}): no figure for the reporting period "
                f"predicted to end around {period_end}, when you held units. Its "
                f"income is treated as received on {distribution}. cgt-calc's "
                f"figures for this fund {_figures_cover(rows_by_isin[isin])}.\n"
            )
            continue
        running = running or in_window
        by_isin[isin].append((period_end, holding))

    statuses = reporting_fund_dates()
    for isin, gaps in by_isin.items():
        holding = gaps[-1][1]
        lines = []
        for period_end, _ in gaps:
            distribution = period_end + ERI_TAX_DATE_DELTA
            due = distribution <= today
            if distribution < window_start:
                clause = (
                    f"its report was due around {distribution}, and any income from "
                    f"it belonged in your {_tax_year(distribution)} tax return"
                )
            elif distribution > window_end:
                clause = (
                    f"its report was due around {distribution}"
                    if due
                    else f"the fund has until around {distribution} to publish its "
                    "report"
                ) + (
                    f", and any income from it falls in the "
                    f"{_tax_year(distribution)} tax year"
                )
            else:
                clause = (
                    f"its income is treated as received on {distribution}, inside "
                    "this report, and "
                    + (
                        "its report was due around then"
                        if due
                        else "the fund has until around then to publish its report"
                    )
                )
            lines.append(f"  Period predicted to end around {period_end}: {clause}.\n")
        one = len(gaps) == 1
        message = (
            f"No excess reported income figure for {holding} (ISIN {isin}) for "
            f"{'a reporting period' if one else 'reporting periods'} when you held "
            f"units. cgt-calc's figures for this fund "
            f"{_figures_cover(rows_by_isin[isin])}.\n"
            + "".join(lines)
            + f"Nothing was added to the cost of your units for "
            f"{'this period' if one else 'these periods'}, so a gain on them may be "
            f"shown too high. A newer cgt-calc may include "
            f"{'the figure' if one else 'the figures'}: upgrade first. If the latest "
            f"release still lacks {'it' if one else 'one'}, add it from the fund's "
            "report with --eri-raw-file, entering 0 if the report states nil. See "
            f"{DOCS_URL}"
        )
        holdings = set(holdings_by_isin[isin])
        # Every missing period began before the window, so a disposal in it
        # is during or after one: from a fund that may then have been
        # non-reporting (reg 17(2)), or was since acquisition (reg 17(3)).
        if isin not in statuses and any(
            window_start <= day <= window_end and holdings & set(disposals[day])
            for day in disposals
        ):
            message += (
                "\nYou disposed of units of this holding in this report. If the fund "
                "was not a reporting fund for one of these periods, the gain may be "
                "taxed as income (an offshore income gain), which cgt-calc does not "
                "calculate."
            )
        LOGGER.warning("%s", message)

    if stops:
        raise CalculationError(
            "Excess reported income is missing from the income in this report.\n"
            + "".join(stops)
            + "A newer cgt-calc may include these figures: upgrade first "
            f"({UPGRADE_URL}). If the latest release still lacks one, add it from "
            "the fund's report with --eri-raw-file, entering 0 if the report states "
            "nil. If the fund has not published a figure, ask the fund for it; if it "
            "cannot give one, take professional advice before entering one. If the "
            "fund was not a reporting fund for the period, see "
            f"{DOCS_URL}"
        )
    return running
