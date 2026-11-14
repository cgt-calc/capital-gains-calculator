"""Find disposals from holdings of a fund that may not have been a reporting fund.

A gain on an interest in an offshore fund is an offshore income gain, taxed as
income, if the fund is a non-reporting fund when the interest is disposed of,
or has become a reporting fund since the interest was acquired (Offshore Funds
(Tax) Regulations 2009, reg 17(2), (3)). cgt-calc does not calculate one. HMRC's
list gives each bundled fund the dates it was a reporting fund between, so a
disposal from units held before the start date, or made after the end date,
stops the run. A disposal that empties the holding within 31 days after the
end date is taken as the fund's closure: the fund is a reporting fund until it
leaves (reg 57(3)), so the gain is an ordinary one. A transfer to a spouse or
civil partner gives no gain either way (reg 39(1), TCGA 1992 s58), so it warns.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Literal, NamedTuple

from .eri_gaps import SLACK, reporting_dates, reporting_fund_dates
from .exceptions import CalculationError

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from .model import Isin, Position

LOGGER = logging.getLogger(__name__)

# The old rules for distributing funds could apply to a period ending up to
# 31 May 2012 (Schedule 1 para 3(3B)), and HMRC's list does not say which did.
OLD_RULES_END = dt.date(2012, 6, 1)
DOCS_URL = "https://cgt-calc.uk/offshore-funds/#unsupported-functionality"


class FundStatus(NamedTuple):
    """A bundled fund's ISIN and the dates it was a reporting fund between.

    The start is None when it is too early for cgt-calc to say anything.
    """

    isin: Isin
    start: dt.date | None
    end: dt.date | None


class Mark(NamedTuple):
    """Why a holding's units may be in a fund that was not a reporting fund.

    - "start": held as `name` before `day`, when the fund became one;
    - "end": held as `name` after `day`, when the fund stopped being one;
    - "unchecked": held before `day`, when `name`'s fund became one, under
      `earlier`, a name with no HMRC dates, and renamed to `name` since.
    """

    kind: Literal["start", "end", "unchecked"]
    name: str
    isin: Isin
    day: dt.date
    earlier: str | None = None


class Disposal(NamedTuple):
    """A disposal, or a closure, in the report from a holding with a `Mark`."""

    day: dt.date
    holding: str
    mark: Mark
    spouse: bool = False
    closure: Literal["disposal", "rename"] | None = None


def fund_statuses(
    rows_by_isin: Mapping[Isin, Collection[dt.date]],
    holdings_by_isin: Mapping[Isin, Collection[str]],
    unchecked: Collection[str],
    owners: Mapping[str, Isin],
) -> dict[str, FundStatus]:
    """Return the status of each holding the run's ISIN data gives a bundled fund.

    A name joined to the fund by a rename alone may have been another fund,
    and a holding in `unchecked` may not be the fund at all, so neither has one.
    """
    dates = reporting_fund_dates()
    statuses = {}
    for isin, rows in rows_by_isin.items():
        if isin not in dates:
            continue
        start, end = reporting_dates(rows, dates[isin])
        for name in set(holdings_by_isin.get(isin, ())) - set(unchecked):
            if owners.get(name) == isin:
                statuses[name] = FundStatus(
                    isin, start if start > OLD_RULES_END else None, end
                )
    return statuses


def _keep(marks: dict[str, Mark], name: str, mark: Mark) -> None:
    """Mark `name`, unless it has a mark already that can stop a run."""
    held = marks.get(name)
    if held is None or (held.kind == "unchecked" and mark.kind != "unchecked"):
        marks[name] = mark


def mark_holdings(
    day: dt.date,
    portfolio: Mapping[str, Position],
    statuses: Mapping[str, FundStatus],
    marks: dict[str, Mark],
    held_since: dict[str, dt.date],
) -> None:
    """Bring each holding's mark up to the end of `day`.

    A holding emptied today loses its mark: units bought later are new ones.
    """
    for name, position in portfolio.items():
        if position.quantity <= 0:
            marks.pop(name, None)
            held_since.pop(name, None)
            continue
        held_since.setdefault(name, day)
        status = statuses.get(name)
        if status is None:
            continue
        if status.start is not None and day < status.start:
            _keep(marks, name, Mark("start", name, status.isin, status.start))
        elif status.end is not None and day > status.end:
            _keep(marks, name, Mark("end", name, status.isin, status.end))


def carry_mark(
    day: dt.date,
    old: str,
    new: str,
    statuses: Mapping[str, FundStatus],
    marks: dict[str, Mark],
    held_since: dict[str, dt.date],
) -> Disposal | None:
    """Move `old`'s mark to `new` with its units, or return the fund's closure.

    A rename within 31 days after the old name's end date is taken as the
    fund closing, as a disposal then is. Units renamed to a fund from a name
    with no HMRC dates, held since before the fund's start, are marked as
    unchecked: the earlier name may have been another fund (reg 17(3A)).
    """
    mark = marks.pop(old, None)
    since = held_since.pop(old, None)
    if since is None:
        return None
    held_since[new] = min(since, held_since.get(new, since))
    status = statuses.get(old)
    if (
        status is not None
        and status.end is not None
        and status.end < day <= status.end + SLACK
    ):
        closed = Mark("end", old, status.isin, status.end)
        return Disposal(day, old, closed, closure="rename")
    target = statuses.get(new)
    if (
        mark is None
        and status is None
        and target is not None
        and target.start is not None
        and since < target.start
    ):
        mark = Mark("unchecked", new, target.isin, target.start, earlier=old)
    if mark is not None:
        _keep(marks, new, mark)
    return None


def classify_disposal(
    day: dt.date,
    holding: str,
    status: FundStatus | None,
    mark: Mark | None,
    *,
    emptied: bool,
    spouse: bool,
) -> Disposal | None:
    """Return what a disposal from `holding` on `day` means, if anything.

    A mark from before the start outranks a closure: a fund that became a
    reporting fund while the units were held taxes the gain as income even
    while it is one (reg 17(3)).
    """
    if mark is not None and mark.kind != "end":
        return Disposal(day, holding, mark, spouse)
    if status is not None and status.end is not None and day > status.end:
        ended = Mark("end", holding, status.isin, status.end)
        if not spouse and emptied and day <= status.end + SLACK:
            return Disposal(day, holding, ended, closure="disposal")
        return Disposal(day, holding, ended, spouse)
    if mark is not None:
        return Disposal(day, holding, mark, spouse)
    # Units bought and disposed of on one day before the start, so never
    # marked at a day's end: the fund was not a reporting fund then.
    if status is not None and status.start is not None and day < status.start:
        started = Mark("start", holding, status.isin, status.start)
        return Disposal(day, holding, started, spouse)
    return None


def _fund(mark: Mark, holding: str) -> str:
    """Name the fund whose dates `mark` comes from, as the message's subject."""
    if mark.name == holding:
        return "the fund"
    return f"{mark.name} (ISIN {mark.isin}), the name you held these units under,"


def _stop_message(disposal: Disposal) -> str:
    mark = disposal.mark
    own = mark.name == disposal.holding
    lead = (
        f"cgt-calc cannot calculate the disposal of {disposal.holding}"
        f"{f' (ISIN {mark.isin})' if own else ''} on {disposal.day}. HMRC's list "
        f"shows {_fund(mark, disposal.holding)} "
    )
    if mark.kind == "start":
        status = (
            f"became a reporting fund only on {mark.day}, after this disposal."
            if disposal.day < mark.day
            else f"became a reporting fund on {mark.day}, and you have held units "
            "since before that date."
        )
        why = (
            "The gain may be an offshore income gain, taxed as income, depending on "
            "the fund's status then and on any election you made."
        )
        report = "before"
    else:
        status = f"stopped being a reporting fund on {mark.day}."
        why = (
            "A gain on units disposed of after that date may be an offshore income "
            "gain, taxed as income."
        )
        report = "after"
    return (
        f"{lead}{status} {why} cgt-calc does not calculate it. Work out this "
        "holding separately and run cgt-calc without it. If the fund published a "
        f"report for a period {report} that date, add its figure with "
        f"--eri-raw-file. See {DOCS_URL}"
    )


def _held_message(mark: Mark, *, spouse: bool, window: tuple[dt.date, dt.date]) -> str:
    window_start, window_end = window
    if mark.kind == "start":
        status = (
            f"became a reporting fund on {mark.day}, and you have held units since "
            "before that date."
        )
        # The election's deemed disposal is on the last day before the start
        # (reg 48(8)), and is made in the return for that year (reg 48(6)).
        election = window_start <= mark.day - dt.timedelta(days=1) <= window_end
        election_text = (
            " An election to be taxed on the gain up to that date instead "
            "(regulation 48) has to be made in your tax return for this year."
        )
    else:
        status = (
            f"stopped being a reporting fund on {mark.day}, and you still held units "
            "after that date."
        )
        # Its deemed disposal is on the end date (reg 100(6), (8)).
        election = window_start <= mark.day <= window_end
        election_text = (
            " An election to be treated as disposing of your units on that date "
            "(regulation 100) has to be made in your tax return for this year."
        )
    effect = (
        "Your transfer to your spouse or civil partner gives no gain, so this report "
        "is not affected."
        if spouse
        else "This report has no disposal from the holding, so its gains are not "
        "affected."
    )
    return (
        f"HMRC's list shows {mark.name} (ISIN {mark.isin}) {status} {effect} A gain "
        "on a later disposal may be an offshore income gain, taxed as income, which "
        f"cgt-calc does not calculate.{election_text if election else ''} See "
        f"{DOCS_URL}"
    )


def _closure_message(disposal: Disposal) -> str:
    mark = disposal.mark
    if disposal.closure == "rename":
        event = f"the rename of your holding on {disposal.day}"
        treated = "cgt-calc treats this as the fund's closure."
    else:
        event = f"your disposal on {disposal.day}"
        treated = (
            "cgt-calc treats this as the fund's closure and calculates an ordinary "
            "capital gain."
        )
    return (
        f"HMRC's list shows {mark.name} (ISIN {mark.isin}) stopped being a "
        f"reporting fund on {mark.day}, and {event} emptied the holding. {treated} "
        f"If the fund carried on after {mark.day} without reporting status, the gain "
        "may be an offshore income gain, taxed as income, which cgt-calc does not "
        f"calculate. See {DOCS_URL}"
    )


def _unchecked_message(mark: Mark) -> str:
    return (
        f"You held units of {mark.name} (ISIN {mark.isin}) under the name "
        f"{mark.earlier} before {mark.day}, when HMRC's list shows the fund became a "
        f"reporting fund, and cgt-calc cannot check {mark.earlier}'s status. If "
        f"{mark.earlier} was not a reporting fund, a gain on these units may be an "
        "offshore income gain, taxed as income, which cgt-calc does not calculate. "
        f"If {mark.earlier} is a fund cgt-calc has figures for, add its ISIN to your "
        f"ISIN translation file and cgt-calc will check it. See {DOCS_URL}"
    )


def report_non_reporting(
    disposals: Collection[Disposal],
    marks: Mapping[str, Mark],
    window: tuple[dt.date, dt.date],
) -> None:
    """Warn about each holding that may not be in a reporting fund; stop on a sale.

    `disposals` are the report's, in order, and `marks` those the holdings
    still have at its end. A disposal from a marked holding stops the run, a
    closure warns, and a holding that is still held or went to a spouse warns.
    """
    stops = []
    stopped = set()
    unchecked: dict[str, Mark] = {}
    held: dict[str, tuple[Mark, bool]] = {}
    for disposal in disposals:
        if disposal.closure is not None:
            LOGGER.warning("%s", _closure_message(disposal))
        elif disposal.mark.kind == "unchecked":
            unchecked.setdefault(disposal.holding, disposal.mark)
        elif disposal.spouse:
            held[disposal.holding] = (disposal.mark, True)
        else:
            stops.append(_stop_message(disposal))
            stopped.add(disposal.holding)
    for holding, mark in marks.items():
        if mark.kind == "unchecked":
            unchecked.setdefault(holding, mark)
        else:
            held.setdefault(holding, (mark, False))

    for mark in unchecked.values():
        LOGGER.warning("%s", _unchecked_message(mark))
    for holding, (mark, spouse) in held.items():
        if holding not in stopped:
            LOGGER.warning("%s", _held_message(mark, spouse=spouse, window=window))
    if stops:
        raise CalculationError("\n".join(stops))
