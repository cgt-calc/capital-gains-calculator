"""Tests for the pull request coverage comment built from Codecov's figures."""

from __future__ import annotations

from decimal import Decimal
from email.message import Message
import json
import subprocess
from typing import TYPE_CHECKING
import urllib.error

import pytest

from scripts import coverage_comment
from scripts.coverage_comment import MARKER, MAX_FILES, MAX_LINES, render

if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts.coverage_comment import Breakdown, Check, Compare, File, Line, Totals

    # What Codecov holds for each query: the comparison, then its components
    # and its flags. A number is the HTTP status it refuses the query with.
    Reports = dict[str, tuple[Compare, list[Breakdown], list[Breakdown]] | int]

REPO = "cgt-calc/capital-gains-calculator"
HEAD = "b653e4c424a99cc763c2b6458bf645d275c56f02"
OTHER_COMMIT = "2f038f41e779b8a7bffac9bf57a43a91e0601baf"
# The commit on main that CI merged pull request 7 into, and GitHub's test
# merge of the two, which is what CI checked out.
BASE = "e66176501d0d5f0b4d1c3a8f6c1f0a2b9d7e4c35"
MERGE = "53f97bfb8a1c2d3e4f5061728394a5b6c7d8e9f0"
PASSED: Check = {
    "app": "codecov",
    "conclusion": "success",
    "title": "91.66% of diff hit (target 90.00%)",
}
NOT_JUDGED: Check = {"app": "codecov", "conclusion": None, "title": None}
# A workflow job that someone named after Codecov's check. GitHub lists the
# newest check first, so it comes before the real one.
IMPOSTOR: Check = {"app": "github-actions", "conclusion": "failure", "title": None}
# The other way round: such a job that passed, listed before the check of
# Codecov's that failed.
JOB_PASSED: Check = {"app": "github-actions", "conclusion": "success", "title": None}
FAILED: Check = {
    "app": "codecov",
    "conclusion": "failure",
    "title": "0.00% of diff hit (target 90.00%)",
}
HIT, MISSED, PARTIAL = 0, 1, 2


def totals(coverage: str, lines: int = 0, hits: int = 0) -> Totals:
    """Build the totals Codecov gives for a report or for changed lines."""
    return {"coverage": Decimal(coverage), "lines": lines, "hits": hits}


def added(number: int, text: str, code: int | None) -> Line:
    """Build a line the pull request adds, with its coverage code.

    A line that is not measured, such as a comment, has no code.
    """
    return {
        "value": f"+{text}",
        "number": {"base": None, "head": number},
        "coverage": {"base": None, "head": code},
        "added": True,
    }


def context(number: int, text: str, code: int) -> Line:
    """Build an unchanged line that the diff shows around the added ones."""
    return {
        "value": f" {text}",
        "number": {"base": number, "head": number},
        "coverage": {"base": code, "head": code},
        "added": False,
    }


def file(
    path: str,
    lines: list[Line],
    base: str = "90.00",
    head: str = "90.00",
    *,
    new: bool = False,
    was: str | None = None,
) -> File:
    """Build one file of the comparison; one with no lines was not touched.

    A file the pull request adds has no name and no totals on the base side.
    One it renames had the name `was` there.
    """
    return {
        "name": {"base": None if new else was or path, "head": path},
        "totals": {
            "base": None if new else totals(base),
            "head": totals(head),
            "patch": None,
        },
        "has_diff": bool(lines),
        "lines": lines,
    }


def comparison(
    files: list[File],
    patch: Totals | None,
    base: str | None = "96.12",
    head: str = "96.12",
    commit: str = HEAD,
) -> Compare:
    """Build Codecov's comparison of a pull request with its base."""
    return {
        "head_commit": commit,
        "totals": {
            "base": totals(base) if base else None,
            "head": totals(head),
            "patch": patch,
        },
        "files": files,
    }


def breakdown(name: str, base: str | None, head: str | None) -> Breakdown:
    """Build the figures of one component or flag, none of whose lines changed."""
    return {
        "name": name,
        "base_report_totals": totals(base) if base else None,
        "head_report_totals": totals(head) if head else None,
        "diff_totals": None,
    }


def test_comment_shows_the_figures_and_the_uncovered_changed_lines() -> None:
    """The whole comment, for a pull request that leaves three lines uncovered.

    The pull request changes 36 measured lines: 33 run, 2 are missed and 1 has
    a branch only partly taken, so 33 of 36 are covered. Codecov reports that
    as 91.66%, cutting 91.666... short. Total coverage goes from 96.12% to
    96.00%, which is 0.12 points down.

    Line 86 of the first file is not covered either, but the pull request
    does not add it, so it is not listed. That file's own coverage fell from
    92% to 91%, yet it is not among the untouched files, because the pull
    request changed it. `util.py` is: its coverage fell although the pull
    request did not touch it. `model.py` is untouched and unchanged, so it
    appears nowhere. `matching.py` is a new file, with no name on the base
    side. The components and flags are given out of order and come out
    sorted by name.
    """
    compare = comparison(
        [
            file(
                "cgt_calc/stock_splits.py",
                [
                    added(83, "    quantity = transaction.quantity", HIT),
                    added(84, "    if quantity is None:", PARTIAL),
                    added(
                        85, "        raise QuantityMissingError(transaction)", MISSED
                    ),
                    context(86, "    return quantity", MISSED),
                ],
                base="92.00",
                head="91.00",
            ),
            file(
                "cgt_calc/matching.py",
                [added(10, "    return None", MISSED)],
                new=True,
            ),
            file("cgt_calc/util.py", [], base="97.10", head="95.00"),
            file("cgt_calc/model.py", [], base="99.00", head="99.00"),
        ],
        totals("91.66", lines=36, hits=33),
        head="96.00",
    )
    components: list[Breakdown] = [
        {
            "name": "Core calculation",
            "base_report_totals": totals("96.45"),
            "head_report_totals": totals("96.30"),
            "diff_totals": totals("91.66", lines=36, hits=33),
        },
        {
            "name": "Broker parsers",
            "base_report_totals": totals("95.85"),
            "head_report_totals": totals("95.85"),
            "diff_totals": None,
        },
    ]
    flags: list[Breakdown] = [
        {
            "name": "python-3.14-ubuntu-latest",
            "base_report_totals": totals("95.80"),
            "head_report_totals": totals("95.70"),
            "diff_totals": totals("88.88", lines=36, hits=32),
        },
        {
            "name": "docker",
            "base_report_totals": totals("95.93"),
            "head_report_totals": totals("95.81"),
            "diff_totals": totals("0", lines=0, hits=0),
        },
    ]

    comment = render(REPO, 7, PASSED, compare, components, flags, [])

    assert comment.worth_posting
    # Each anchor is the SHA-256 of the file's path, which is how GitHub names
    # a file on a pull request's "Files changed" page.
    assert comment.body == (
        "<!-- cgt-calc-coverage -->\n"
        "### :white_check_mark: 91.66% of changed lines covered\n"
        "\n"
        "33 of 36 changed lines covered, against a target of 90.00%.\n"
        "Total coverage: 96.12% -> 96.00% (-0.12 points).\n"
        "\n"
        "#### 3 changed lines are not covered\n"
        "\n"
        "[`cgt_calc/stock_splits.py`](https://github.com/cgt-calc/"
        "capital-gains-calculator/pull/7/files#diff-"
        "36672b7d6dfae4790265629383dc727bb7bd09547deb85c598a0d665e0dc466b)\n"
        "```text\n"
        "   84  partial      if quantity is None:\n"
        "   85  missed           raise QuantityMissingError(transaction)\n"
        "```\n"
        "\n"
        "[`cgt_calc/matching.py`](https://github.com/cgt-calc/"
        "capital-gains-calculator/pull/7/files#diff-"
        "0b43bdcf0db2b7bd8fd403c385de175b21f07dc42b52f193e1937833bc83334a)\n"
        "```text\n"
        "   10  missed       return None\n"
        "```\n"
        "\n"
        "#### Coverage changed in files this pull request did not touch\n"
        "\n"
        "| File | Before | After |\n"
        "|---|---|---|\n"
        "| `cgt_calc/util.py` | 97.10% | 95.00% |\n"
        "\n"
        "<details>\n"
        "<summary>Components</summary>\n"
        "\n"
        "| Component | Before | After | Changed lines |\n"
        "|---|---|---|---|\n"
        "| `Broker parsers` | 95.85% | 95.85% | none |\n"
        "| `Core calculation` | 96.45% | 96.30% | 91.66% of 36 |\n"
        "\n"
        "</details>\n"
        "\n"
        "<details>\n"
        "<summary>Flags</summary>\n"
        "\n"
        "| Flag | Before | After | Changed lines |\n"
        "|---|---|---|---|\n"
        "| `docker` | 95.93% | 95.81% | none |\n"
        "| `python-3.14-ubuntu-latest` | 95.80% | 95.70% | 88.88% of 36 |\n"
        "\n"
        "</details>\n"
        "\n"
        "[Full report on Codecov](https://app.codecov.io/gh/cgt-calc/"
        "capital-gains-calculator/pull/7) for commit `b653e4c`.\n"
    )


@pytest.mark.parametrize(
    ("check", "verdict"),
    [
        pytest.param(
            {
                "app": "codecov",
                "conclusion": "failure",
                "title": "50.00% of diff hit (target 90.00%)",
            },
            "### :x: 50.00% of changed lines covered\n"
            "\n"
            "1 of 2 changed lines covered, against a target of 90.00%.\n",
            id="a failed check is marked as one",
        ),
        pytest.param(
            {
                "app": "codecov",
                "conclusion": "success",
                "title": "Patch coverage looks fine",
            },
            "### :white_check_mark: 50.00% of changed lines covered\n"
            "\n"
            "1 of 2 changed lines covered.\n",
            id="a title that names no target leaves the target out",
        ),
    ],
)
def test_verdict_follows_the_patch_check(check: Check, verdict: str) -> None:
    """The mark and the target come from Codecov's check, not from the figures.

    One of the two changed lines is covered, which is 50%. Whether that passes
    is Codecov's call: the same figures get a cross under a failed check and a
    tick under a passed one.
    """
    compare = comparison(
        [file("cgt_calc/util.py", [added(1, "x = 1", HIT), added(2, "y = 2", MISSED)])],
        totals("50.00", lines=2, hits=1),
    )

    assert verdict in render(REPO, 7, check, compare, [], [], []).body


@pytest.mark.parametrize(
    ("patch", "untouched", "head", "worth_posting", "total"),
    [
        pytest.param(
            totals("0", lines=0, hits=0),
            file("cgt_calc/util.py", []),
            "96.12",
            False,
            "Total coverage: 96.12% -> 96.12% (no change).\n",
            id="nothing moved",
        ),
        pytest.param(
            None,
            file("cgt_calc/util.py", [], base="97.10", head="97.20"),
            "96.12",
            True,
            "Total coverage: 96.12% -> 96.12% (no change).\n"
            "\n"
            "#### Coverage changed in files this pull request did not touch\n"
            "\n"
            "| File | Before | After |\n"
            "|---|---|---|\n"
            "| `cgt_calc/util.py` | 97.10% | 97.20% |\n",
            id="an untouched file moved",
        ),
        pytest.param(
            totals("0", lines=0, hits=0),
            file("cgt_calc/util.py", []),
            "96.30",
            True,
            "Total coverage: 96.12% -> 96.30% (+0.18 points).\n",
            id="only the total moved, as when a file is deleted",
        ),
    ],
)
def test_pull_request_that_changes_no_measured_lines(
    patch: Totals | None,
    untouched: File,
    head: str,
    worth_posting: bool,
    total: str,
) -> None:
    """A new comment is posted only when some coverage figure moved.

    With no measured lines changed there is no verdict to give, and with no
    components or flags there are no tables. Codecov gives the totals of the
    changed lines as zeros in that case, as it did for a pull request that
    only edited `codecov.yml`, and the comment must read the same when it
    leaves them out. The total rises from 96.12% to 96.30% in the last case,
    which is 0.18 points up.
    """
    comment = render(
        REPO,
        7,
        {"app": "codecov", "conclusion": "success", "title": "Coverage not affected"},
        comparison([untouched], patch, head=head),
        [],
        [],
        [],
    )

    assert comment.worth_posting is worth_posting
    assert comment.body == (
        "<!-- cgt-calc-coverage -->\n"
        "### No measured lines changed\n"
        "\n"
        f"{total}"
        "\n"
        "[Full report on Codecov](https://app.codecov.io/gh/cgt-calc/"
        "capital-gains-calculator/pull/7) for commit `b653e4c`.\n"
    )


@pytest.mark.parametrize(
    ("base", "components", "flags", "worth_posting"),
    [
        pytest.param(
            "96.12",
            [breakdown("Core calculation", "96.45", "96.45")],
            [breakdown("windows", "95.00", "96.00")],
            True,
            id="one flag moved",
        ),
        pytest.param(
            "96.12",
            [breakdown("Core calculation", "96.45", "96.30")],
            [breakdown("windows", "95.00", "95.00")],
            True,
            id="one component moved",
        ),
        pytest.param(
            "96.12",
            [breakdown("Core calculation", "96.45", "96.45")],
            [breakdown("windows", None, "95.00")],
            True,
            id="a flag has figures for the first time",
        ),
        pytest.param(
            "96.12",
            [breakdown("Core calculation", "96.45", None)],
            [breakdown("windows", "95.00", "95.00")],
            True,
            id="a component has figures no longer",
        ),
        pytest.param(
            "96.12",
            [breakdown("Core calculation", "96.45", "96.45")],
            [
                {
                    "name": "windows",
                    "base_report_totals": totals("95.00", lines=80000, hits=76000),
                    "head_report_totals": totals("95.00", lines=80000, hits=76001),
                    "diff_totals": None,
                },
            ],
            False,
            id="one more line is run, which is too little to show",
        ),
        pytest.param(
            None,
            [breakdown("Core calculation", None, "96.45")],
            [breakdown("windows", None, "95.00")],
            False,
            id="there is no report from before to have moved from",
        ),
    ],
)
def test_figure_that_moves_for_one_flag_or_component_alone_is_worth_a_comment(
    base: str | None,
    components: list[Breakdown],
    flags: list[Breakdown],
    worth_posting: bool,
) -> None:
    """A figure the comment shows can move while the total and every file stay put.

    A test that runs only on Windows can cover lines another platform already
    covers: the Windows figure rises from 95% to 96% and the combined ones do
    not move. No measured line changed here and no file moved, so the rows by
    component and by flag are all that can make the comment worth posting.

    What counts is the figure as the comment shows it. 76,000 of 80,000 lines
    is 95.00%, and one line more is 95.00125%, which still reads 95.00%: the
    comment would say nothing new.
    """
    compare = comparison([file("cgt_calc/util.py", [])], None, base=base)

    comment = render(REPO, 7, PASSED, compare, components, flags, [])

    assert comment.worth_posting is worth_posting


def test_fully_covered_change_without_a_base_report() -> None:
    """With nothing to compare against, the comment gives only today's figures.

    The one changed line is covered, so there is no list of uncovered lines,
    and a covered change is by itself worth a comment. No file has figures
    from before, the untouched `model.py` included, so none can be said to
    have moved.
    """
    compare = comparison(
        [
            file("cgt_calc/util.py", [added(1, "x = 1", HIT)], new=True),
            file("cgt_calc/model.py", [], new=True),
        ],
        totals("100.00", lines=1, hits=1),
        base=None,
        head="96.00",
    )
    flags: list[Breakdown] = [
        {
            "name": "docker",
            "base_report_totals": None,
            "head_report_totals": totals("95.81"),
            "diff_totals": totals("100.00", lines=1, hits=1),
        },
    ]

    comment = render(REPO, 7, PASSED, compare, [], flags, [])

    assert comment.worth_posting
    assert "Total coverage: 96.00%.\n" in comment.body
    assert "| `docker` | n/a | 95.81% | 100.00% of 1 |\n" in comment.body
    assert "not covered" not in comment.body
    assert "did not touch" not in comment.body


def test_text_from_the_pull_request_cannot_break_out_of_its_place() -> None:
    """Source lines, paths and names are a fork's to choose, so they are tamed.

    A line holding three backticks would end an ordinary code block, so the
    block is fenced with four. A path or a name keeps letters, digits, spaces
    and `. / + - _`; anything else, such as the characters that start a link,
    a table cell, a tag, a mention or an issue number, or the line break that
    would end a table row, becomes a question mark. What is left is shown as
    code, where GitHub does not turn a bare web address or `GH-1` into a link.
    """
    compare = comparison(
        [
            file(
                "cgt_calc/a](http://evil)`.py",
                [added(1, "x = '```' # </details> @someone", MISSED)],
            ),
            file("docs/<b>|x", [], base="1.00", head="2.00"),
        ],
        totals("0", lines=1, hits=0),
    )
    components: list[Breakdown] = [
        {
            "name": "www.evil.example.com GH-1",
            "base_report_totals": None,
            "head_report_totals": None,
            "diff_totals": None,
        },
    ]
    flags: list[Breakdown] = [
        {
            "name": "x|y<b>@z#1*&!\nrow c++_api",
            "base_report_totals": None,
            "head_report_totals": None,
            "diff_totals": None,
        },
    ]

    body = render(REPO, 7, PASSED, compare, components, flags, []).body

    assert "[`cgt_calc/a??http?//evil??.py`](https://github.com/" in body
    assert "````text\n    1  missed   x = '```' # </details> @someone\n````\n" in body
    assert "| `docs/?b??x` | 1.00% | 2.00% |\n" in body
    assert "| `www.evil.example.com GH-1` | n/a | n/a | none |\n" in body
    assert "| `x?y?b??z?1????row c++_api` | n/a | n/a | none |\n" in body


@pytest.mark.parametrize(
    ("per_file", "uncovered", "rest"),
    [
        pytest.param([28, 2], 30, "", id="at the limit, all are listed"),
        pytest.param(
            [29, 2, 2],
            33,
            "And 3 more in the full report.\n",
            id="past the limit, the rest are counted",
        ),
    ],
)
def test_long_list_of_uncovered_lines_is_cut(
    per_file: list[int],
    uncovered: int,
    rest: str,
) -> None:
    """Only the first thirty uncovered lines are listed, across all files.

    In the second case the first file holds 29 and the second 2, so the limit
    falls inside the second file. The third file's 2 lines come after it and
    the file is not named at all: 33 uncovered, 30 listed, 3 more.
    """
    assert MAX_LINES == 30
    files = [
        file(
            f"cgt_calc/f{index}.py",
            [added(number, "pass", MISSED) for number in range(1, count + 1)],
        )
        for index, count in enumerate(per_file)
    ]
    compare = comparison(files, totals("0", lines=uncovered, hits=0))

    body = render(REPO, 7, PASSED, compare, [], [], []).body

    assert f"#### {uncovered} changed lines are not covered\n" in body
    assert body.count("  missed   pass\n") == 30
    assert "cgt_calc/f2.py" not in body
    assert ("more in the full report" in body) is bool(rest)
    assert rest in body


@pytest.mark.parametrize(
    ("moved", "rest"),
    [
        pytest.param(10, "", id="at the limit, all are listed"),
        pytest.param(
            11,
            "And 1 more in the full report.\n",
            id="one over the limit is counted, not listed",
        ),
    ],
)
def test_long_list_of_untouched_files_is_cut(moved: int, rest: str) -> None:
    """Only the first ten untouched files whose coverage moved are listed."""
    assert MAX_FILES == 10
    files = [
        file(f"cgt_calc/f{number}.py", [], base="50.00", head="60.00")
        for number in range(moved)
    ]

    body = render(REPO, 7, PASSED, comparison(files, None), [], [], []).body

    assert body.count("| 50.00% | 60.00% |\n") == 10
    assert ("more in the full report" in body) is bool(rest)
    assert rest in body


def test_one_uncovered_line_and_a_very_long_one() -> None:
    """A single uncovered line reads in the singular and is cut at 100 characters."""
    compare = comparison(
        [file("cgt_calc/a.py", [added(1, "a" * 100 + "b", MISSED)])],
        totals("0", lines=1, hits=0),
    )

    body = render(REPO, 7, PASSED, compare, [], [], []).body

    assert "#### 1 changed line is not covered\n" in body
    assert f"    1  missed   {'a' * 100}\n```\n" in body


LEFT_OUT = (
    "Its count of covered lines is left out, and so are the uncovered lines of"
    " the files below, because Codecov may have matched their coverage to other"
    " lines. CI tests the pull request merged into its base branch, which may"
    " have changed these files since this branch started, and a line then has"
    " another number in what CI tested.\n"
)


def test_figures_that_may_be_for_other_lines_are_left_out() -> None:
    """A file whose lines may be misaligned loses its lines, and the counts go.

    The base branch changed `base_parsers.py` after this branch started, so
    the line Codecov calls missed at 387 may be another line of the merged
    file, and it is not listed. Codecov's 2 of 4 for the change counts that
    file's lines, so it goes too, from the verdict and from the flag's row.
    What stays is what does not depend on a line's number: that Codecov
    failed the change and its target, the total, which fell by 0.12 points,
    the untouched `model.py`, and the missed line of `util.py`, which the
    base branch left alone.
    """
    compare = comparison(
        [
            file(
                "cgt_calc/parsers/base_parsers.py",
                [
                    added(385, "        if not issubclass(cls, BaseDirParser):", HIT),
                    added(387, "            LOGGER.warning(message)", MISSED),
                ],
            ),
            file(
                "cgt_calc/util.py",
                [added(1, "x = 1", HIT), added(2, "y = 2", MISSED)],
            ),
            file("cgt_calc/model.py", [], base="99.00", head="98.00"),
        ],
        totals("50.00", lines=4, hits=2),
        head="96.00",
    )
    check: Check = {
        "app": "codecov",
        "conclusion": "failure",
        "title": "50.00% of diff hit (target 90.00%)",
    }
    flags: list[Breakdown] = [
        {
            "name": "docker",
            "base_report_totals": totals("95.93"),
            "head_report_totals": totals("95.81"),
            "diff_totals": totals("50.00", lines=4, hits=2),
        },
    ]

    comment = render(
        REPO,
        7,
        check,
        compare,
        [],
        flags,
        ["cgt_calc/parsers/base_parsers.py"],
    )

    assert comment.body == (
        "<!-- cgt-calc-coverage -->\n"
        "### :x: Codecov's check of the changed lines did not pass\n"
        "\n"
        f"Codecov's target is 90.00%. {LEFT_OUT}"
        "\n"
        "- `cgt_calc/parsers/base_parsers.py`\n"
        "\n"
        "Total coverage: 96.12% -> 96.00% (-0.12 points).\n"
        "\n"
        "#### 1 changed line is not covered in the other files\n"
        "\n"
        "[`cgt_calc/util.py`](https://github.com/cgt-calc/"
        "capital-gains-calculator/pull/7/files#diff-"
        "ed746bb60c95619bf97b3d56a108ef98346e885088fe3989b92791d0a03d8f81)\n"
        "```text\n"
        "    2  missed   y = 2\n"
        "```\n"
        "\n"
        "#### Coverage changed in files this pull request did not touch\n"
        "\n"
        "| File | Before | After |\n"
        "|---|---|---|\n"
        "| `cgt_calc/model.py` | 99.00% | 98.00% |\n"
        "\n"
        "<details>\n"
        "<summary>Flags</summary>\n"
        "\n"
        "| Flag | Before | After | Changed lines |\n"
        "|---|---|---|---|\n"
        "| `docker` | 95.93% | 95.81% | n/a |\n"
        "\n"
        "</details>\n"
        "\n"
        "[Full report on Codecov](https://app.codecov.io/gh/cgt-calc/"
        "capital-gains-calculator/pull/7) for commit `b653e4c`.\n"
    )


def test_comment_that_only_says_what_it_leaves_out_is_still_worth_posting() -> None:
    """Codecov's finding that no measured line changed may be wrong as well.

    The pull request adds one line to `util.py`, which Codecov takes for one
    that is not measured, so it counts no changed line and passes the change.
    The base branch changed the file too, so the line may be measured after
    all. The comment does not say that no measured lines changed, and it is
    posted although no figure moved, because nothing else tells the reader
    that Codecov's check may have judged other lines. The check's title
    names no target, so none is given.
    """
    compare = comparison(
        [file("cgt_calc/util.py", [added(1, "# Why this is here.", None)])],
        totals("0", lines=0, hits=0),
    )
    check: Check = {
        "app": "codecov",
        "conclusion": "success",
        "title": "Coverage not affected",
    }

    comment = render(REPO, 7, check, compare, [], [], ["cgt_calc/util.py"])

    assert comment.worth_posting
    assert comment.body == (
        "<!-- cgt-calc-coverage -->\n"
        "### :white_check_mark: Codecov's check of the changed lines passed\n"
        "\n"
        f"{LEFT_OUT}"
        "\n"
        "- `cgt_calc/util.py`\n"
        "\n"
        "Total coverage: 96.12% -> 96.12% (no change).\n"
        "\n"
        "[Full report on Codecov](https://app.codecov.io/gh/cgt-calc/"
        "capital-gains-calculator/pull/7) for commit `b653e4c`.\n"
    )


@pytest.mark.parametrize(
    ("left_out", "rest"),
    [
        pytest.param(10, "", id="at the limit, all are named"),
        pytest.param(11, "- and 1 more\n", id="one over the limit is counted"),
    ],
)
def test_long_list_of_files_left_out_is_cut(left_out: int, rest: str) -> None:
    """Only the first ten files whose figures are left out are named."""
    assert MAX_FILES == 10
    files = [
        file(f"cgt_calc/f{number}.py", [added(1, "pass", HIT)])
        for number in range(left_out)
    ]
    compare = comparison(files, totals("100.00", lines=left_out, hits=left_out))

    body = render(
        REPO,
        7,
        PASSED,
        compare,
        [],
        [],
        [one["name"]["head"] for one in files],
    ).body

    assert body.count("\n- `cgt_calc/f") == 10
    assert ("more\n" in body) is bool(rest)
    assert rest in body


# GitHub built the test merge two seconds before the pull request's CI began.
MERGED_AT = "2026-10-09T20:18:41Z"
RUN_AT = "2026-10-09T20:18:43Z"
# Times the script must not go by: when the test merge was authored, here
# long before it was committed, and when a run was last started again.
AUTHORED_AT = "2026-10-09T20:00:00Z"
RERUN_AT = "2026-10-09T20:50:00Z"


class FakeGitHub:
    """Stand in for the gh CLI: answer the reads and record the writes."""

    def __init__(
        self,
        pulls: list[tuple[int, str]],
        comments: list[tuple[int, str, str]],
        checks: list[Check],
        *,
        merge: tuple[str, ...] | None = (MERGED_AT, BASE, HEAD),
        runs: tuple[str, ...] = (RUN_AT,),
        base: str = "main",
        on_base: tuple[str, ...] = (),
    ) -> None:
        """Hold what GitHub would say about the repository.

        `merge` is when the test merge of pull request 7 was committed, then
        its parents, or None when GitHub has none for it. `runs` is when each
        workflow run the pull request started on its head commit was created.
        `base` is the branch it targets, in a repository whose default is
        main. `on_base` is the files that branch changed between the commit
        the pull request's branch started from and the one CI merged it into.
        """
        self.pulls = pulls
        self.comments = comments
        self.checks = checks
        self.merge = merge
        self.runs = runs
        self.base = base
        self.on_base = on_base
        self.writes: list[tuple[str, str, str]] = []

    def pull(self, number: int, head: str) -> str:
        """Give a pull request as the script's filter leaves it.

        Only pull request 7 has a test merge that can be looked up: another
        one's is a commit this stand-in does not know.
        """
        merge = MERGE if self.merge else None
        return json.dumps(
            {
                "number": number,
                "head": head,
                "base": self.base,
                "default_branch": "main",
                "merge_commit_sha": merge if number == 7 else OTHER_COMMIT,
            },
        )

    def __call__(self, *args: str, stdin: str | None = None) -> str:
        """Answer one call the way `gh api` would.

        A body is sent only when the call names it as a field read from
        standard input. A listing is answered only when every page is asked
        for, and comes back one JSON document a line, with characters outside
        ASCII left as they are, which is how jq writes them. The test merge
        and each run come with both of their times, as GitHub gives them.
        """
        path = next(arg for arg in args if arg.startswith(f"repos/{REPO}/"))
        asked = path.removeprefix(f"repos/{REPO}/")
        if stdin is not None and args[-2:] == ("--field", "body=@-"):
            method = "PATCH" if args[:2] == ("--method", "PATCH") else "POST"
            self.writes.append((method, path, stdin))
            return ""
        if asked == "pulls?state=open&per_page=100" and "--paginate" in args:
            return "".join(f"{self.pull(*row)}\n" for row in self.pulls)
        if asked == "pulls/7":
            return self.pull(7, HEAD)
        if self.merge and asked == f"git/commits/{MERGE}":
            committed, *parents = self.merge
            return json.dumps(
                {
                    "sha": MERGE,
                    "author": {"date": AUTHORED_AT},
                    "committer": {"date": committed},
                    "parents": [{"sha": parent} for parent in parents],
                },
            )
        if (
            asked == f"actions/runs?head_sha={HEAD}&event=pull_request&per_page=100"
            and "--paginate" in args
        ):
            return "".join(
                f"{json.dumps({'created_at': run, 'run_started_at': RERUN_AT})}\n"
                for run in self.runs
            )
        if asked == f"compare/{HEAD}...{BASE}":
            return json.dumps(self.on_base)
        if asked == f"commits/{HEAD}/check-runs?check_name=codecov/patch&per_page=100":
            return json.dumps(self.checks)
        if asked == "issues/7/comments?per_page=100" and "--paginate" in args:
            return "".join(
                f"{json.dumps(row, ensure_ascii=False)}\n" for row in self.comments
            )
        raise AssertionError(args)


AGAINST_BASE = f"base={BASE}&head={HEAD}"
FOR_PULL = "pullid=7"


def codecov_holding(reports: Reports) -> Callable[[str, str], str]:
    """Stand in for Codecov's API, which holds only the comparisons given.

    Each is read at three addresses: the comparison itself, its components
    and its flags. A number in place of a comparison is the HTTP status that
    Codecov refuses it with: 404 is its answer for a commit it has no report
    for. Any other address is a mistake in the script, so it fails the test.
    """

    def read(repo: str, path: str) -> str:
        assert repo == REPO
        for query, held in reports.items():
            addresses = [
                f"compare/?{query}",
                f"compare/components?{query}",
                f"compare/flags?{query}",
            ]
            if path in addresses and isinstance(held, int):
                raise urllib.error.HTTPError(path, held, "Refused", Message(), None)
            if path in addresses and not isinstance(held, int):
                return json.dumps(held[addresses.index(path)], default=float)
        raise AssertionError(path)

    return read


def against_base(compare: Compare) -> Reports:
    """Hold one comparison with the commit CI merged into, with no breakdowns."""
    return {AGAINST_BASE: (compare, [], [])}


MEASURED = comparison(
    [file("cgt_calc/util.py", [added(1, "x = 1", HIT)])],
    totals("100.00", lines=1, hits=1),
)
UNCOVERED = comparison(
    [file("cgt_calc/util.py", [added(1, "x = 1", MISSED)])],
    totals("0", lines=1, hits=0),
)
UNMOVED = comparison([file("cgt_calc/util.py", [])], None)
# Pull request 7 as Codecov sees it when its branch is behind main and it
# changes no measured line. Against the commit CI merged it into, coverage
# fell in `model.py`, by one point, and in the total, from 96.12% to 96.00%,
# which is 0.12 points. Against the commit the branch started from, every
# figure from before is another one, and `currency_converter.py` has moved
# as well, though it was main that changed it.
BEHIND_MAIN: Reports = {
    AGAINST_BASE: (
        comparison(
            [
                file("cgt_calc/model.py", [], base="99.00", head="98.00"),
                file("cgt_calc/currency_converter.py", [], base="96.92", head="96.92"),
            ],
            None,
            base="96.12",
            head="96.00",
        ),
        [breakdown("Core calculation", "96.45", "96.30")],
        [breakdown("docker", "95.93", "95.81")],
    ),
    FOR_PULL: (
        comparison(
            [
                file("cgt_calc/model.py", [], base="99.50", head="98.00"),
                file("cgt_calc/currency_converter.py", [], base="96.76", head="96.92"),
            ],
            None,
            base="95.00",
            head="96.00",
        ),
        [breakdown("Core calculation", "95.50", "96.30")],
        [breakdown("docker", "95.20", "95.81")],
    ),
}
# The same pull request when Codecov has no report for the commit CI merged
# it into.
NO_BASE_REPORT: Reports = {**BEHIND_MAIN, AGAINST_BASE: 404}
COVERED = "### :white_check_mark: 100.00% of changed lines covered\n"
NOTHING = "### No measured lines changed\n"
# A comment holding a line separator, which once split the listing in two and
# stopped the run; a comment of the bot's that is not this one, though the
# marker appears later in it; and someone else's that only looks like it.
# None may be taken for the comment to edit.
OTHERS = [
    (9, "someone", "pasted\u2028text"),
    (10, "github-actions[bot]", f"The docs preview is ready. {MARKER}"),
    (12, "someone", f"{MARKER}\nnot the bot's"),
]
OWN = (11, "github-actions[bot]", f"{MARKER}\nfigures for an earlier commit")
# Two runs racing could each post a comment. The older one is kept up to date.
DUPLICATE = (13, "github-actions[bot]", f"{MARKER}\na second copy")
NEW = f"repos/{REPO}/issues/7/comments"
EDIT = f"repos/{REPO}/issues/comments/11"


@pytest.mark.parametrize(
    ("pulls", "comments", "checks", "reports", "writes"),
    [
        pytest.param(
            [(5, OTHER_COMMIT), (7, HEAD)],
            OTHERS,
            [IMPOSTOR, PASSED],
            against_base(MEASURED),
            [("POST", NEW, COVERED)],
            id="a first comment is posted on the pull request the commit heads",
        ),
        pytest.param(
            [(5, OTHER_COMMIT), (7, HEAD)],
            [*OTHERS, OWN, DUPLICATE],
            [IMPOSTOR, PASSED],
            against_base(MEASURED),
            [("PATCH", EDIT, COVERED)],
            id="its own earlier comment is edited in place",
        ),
        pytest.param(
            [(7, HEAD)],
            OTHERS,
            [JOB_PASSED, FAILED],
            against_base(UNCOVERED),
            [("POST", NEW, "### :x: 0.00% of changed lines covered\n")],
            id="Codecov failed the change, so the comment says so",
        ),
        pytest.param(
            [(7, HEAD)],
            OTHERS,
            [IMPOSTOR, PASSED],
            against_base(UNMOVED),
            [],
            id="nothing moved and no comment yet, so none is posted",
        ),
        pytest.param(
            [(7, HEAD)],
            [*OTHERS, OWN],
            [IMPOSTOR, PASSED],
            against_base(UNMOVED),
            [("PATCH", EDIT, NOTHING)],
            id="nothing moved but a comment exists, so it is brought up to date",
        ),
        pytest.param(
            [(7, HEAD)],
            OTHERS,
            [IMPOSTOR, PASSED],
            BEHIND_MAIN,
            [("POST", NEW, NOTHING)],
            id="figures moved against the commit CI merged into, so one is posted",
        ),
        pytest.param(
            [(7, HEAD)],
            OTHERS,
            [IMPOSTOR, PASSED],
            NO_BASE_REPORT,
            [],
            id="figures moved only since the branch started, so none is posted",
        ),
        pytest.param(
            [(5, OTHER_COMMIT)],
            [OWN],
            [IMPOSTOR, PASSED],
            against_base(MEASURED),
            [],
            id="the commit heads no open pull request",
        ),
        pytest.param(
            [(7, HEAD)],
            [OWN],
            [IMPOSTOR, PASSED],
            {
                AGAINST_BASE: 404,
                FOR_PULL: (
                    comparison(
                        MEASURED["files"],
                        MEASURED["totals"]["patch"],
                        commit=OTHER_COMMIT,
                    ),
                    [],
                    [],
                ),
            },
            [],
            id="Codecov compared an older commit",
        ),
        pytest.param(
            [(7, HEAD)],
            [OWN],
            [IMPOSTOR, NOT_JUDGED],
            against_base(MEASURED),
            [],
            id="Codecov has not judged the commit",
        ),
    ],
)
def test_what_a_workflow_run_writes_to_the_pull_request(
    pulls: list[tuple[int, str]],
    comments: list[tuple[int, str, str]],
    checks: list[Check],
    reports: Reports,
    writes: list[tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run creates the comment, edits its own, or leaves the pull request alone.

    The check is taken by its app. A job that shares its name does not turn
    the tick into a cross when it failed, or the cross into a tick when it
    passed, and does not stand in for a Codecov check that has yet to report.
    The one changed line of the failed change is missed, which is 0%.
    """
    github = FakeGitHub(pulls, comments, checks)
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr(coverage_comment, "codecov", codecov_holding(reports))
    monkeypatch.setattr(
        "sys.argv",
        ["coverage_comment.py", "--repo", REPO, "--sha", HEAD],
    )

    coverage_comment.main()

    assert [write[:2] for write in github.writes] == [write[:2] for write in writes]
    for (_, _, body), (_, _, heading) in zip(github.writes, writes, strict=True):
        assert body.startswith(f"{MARKER}\n{heading}")


def tables(component_before: str, flag_before: str) -> str:
    """Give the end of the comment for pull request 7 when its branch is behind."""
    return (
        "<details>\n"
        "<summary>Components</summary>\n"
        "\n"
        "| Component | Before | After | Changed lines |\n"
        "|---|---|---|---|\n"
        f"| `Core calculation` | {component_before} | 96.30% | none |\n"
        "\n"
        "</details>\n"
        "\n"
        "<details>\n"
        "<summary>Flags</summary>\n"
        "\n"
        "| Flag | Before | After | Changed lines |\n"
        "|---|---|---|---|\n"
        f"| `docker` | {flag_before} | 95.81% | none |\n"
        "\n"
        "</details>\n"
        "\n"
        "[Full report on Codecov](https://app.codecov.io/gh/cgt-calc/"
        "capital-gains-calculator/pull/7) for commit `b653e4c`.\n"
    )


COMPARED_WITH_BASE = (
    "<!-- cgt-calc-coverage -->\n"
    "### No measured lines changed\n"
    "\n"
    "Total coverage: 96.12% -> 96.00% (-0.12 points).\n"
    "\n"
    "#### Coverage changed in files this pull request did not touch\n"
    "\n"
    "| File | Before | After |\n"
    "|---|---|---|\n"
    "| `cgt_calc/model.py` | 99.00% | 98.00% |\n"
    "\n"
) + tables("96.45%", "95.93%")
CURRENT_FIGURES_ONLY = (
    "<!-- cgt-calc-coverage -->\n"
    "### No measured lines changed\n"
    "\n"
    "Total coverage: 96.00%.\n"
    "\n"
) + tables("n/a", "n/a")
NOT_KNOWN = (
    "Left out the figures from before the pull request: CI is not known to"
    " have merged it into a commit on the default branch.\n"
)
NO_REPORT = (
    "Left out the figures from before the pull request: Codecov has no report"
    f" for {BASE}, the commit CI merged it into.\n"
)


@pytest.mark.parametrize(
    ("base", "merge", "runs", "reports", "body", "log"),
    [
        pytest.param(
            "main",
            ("2026-10-09T20:18:43Z", BASE, HEAD),
            ("2026-10-09T20:18:50Z", "2026-10-09T20:18:43Z"),
            BEHIND_MAIN,
            COMPARED_WITH_BASE,
            "",
            id="the test merge is as old as the first run, so CI tested it",
        ),
        pytest.param(
            "main",
            ("2026-10-09T20:18:44Z", BASE, HEAD),
            ("2026-10-09T20:18:50Z", "2026-10-09T20:18:43Z"),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="the test merge was rebuilt a second after the first run began",
        ),
        pytest.param(
            "main",
            ("2026-10-09T20:18:44Z", BASE, HEAD),
            ("2026-10-09T20:18:43Z", "2026-10-09T20:18:50Z"),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="the first run is found wherever GitHub lists it",
        ),
        pytest.param(
            "main",
            (MERGED_AT, BASE, HEAD),
            (),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="the pull request started no run on this commit",
        ),
        pytest.param(
            "main",
            None,
            (RUN_AT,),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="GitHub has no test merge, as for a pull request that conflicts",
        ),
        pytest.param(
            "main",
            (MERGED_AT, BASE),
            (RUN_AT,),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="the pull request is merged, so GitHub names the commit on main",
        ),
        pytest.param(
            "main",
            (MERGED_AT, BASE, OTHER_COMMIT),
            (RUN_AT,),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="the test merge is of another head commit",
        ),
        pytest.param(
            "split-the-parser",
            (MERGED_AT, BASE, HEAD),
            (RUN_AT,),
            BEHIND_MAIN,
            CURRENT_FIGURES_ONLY,
            NOT_KNOWN,
            id="the pull request is stacked on another one's branch",
        ),
        pytest.param(
            "main",
            (MERGED_AT, BASE, HEAD),
            (RUN_AT,),
            NO_BASE_REPORT,
            CURRENT_FIGURES_ONLY,
            NO_REPORT,
            id="Codecov has no report for the commit CI merged into",
        ),
    ],
)
def test_figures_from_before_are_those_of_the_commit_ci_merged_into(
    base: str,
    merge: tuple[str, ...] | None,
    runs: tuple[str, ...],
    reports: Reports,
    body: str,
    log: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Coverage is compared with what CI merged into, or with nothing at all.

    CI tests the pull request merged into main as main then stood. When the
    branch is behind, Codecov's own comparison for the pull request starts
    from the older commit the branch forked at, so it would show the change
    main made to `currency_converter.py` as this pull request's, and 95.00%
    as the total before. The comment asks instead for the comparison with the
    first parent of GitHub's test merge, which shows only `model.py`.

    That holds only while the test merge GitHub names is the one CI checked
    out: it has the head commit as its second parent, and it was committed
    no later than the first workflow run the pull request started on that
    commit was created. When the test merge was authored, and when a run was
    started again, do not come into it. It holds only for a pull request into
    main, too: the first parent of a stacked one is another pull request's
    head, whose report is for that one's own test merge. When it does not
    hold, or Codecov has no report for the commit, the figures from before
    are left out, never taken from the other comparison, and the log says
    which of the two it was. The component's row is under Components and the
    flag's under Flags in both.
    """
    github = FakeGitHub(
        [(7, HEAD)],
        [OWN],
        [PASSED],
        merge=merge,
        runs=runs,
        base=base,
    )
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr(coverage_comment, "codecov", codecov_holding(reports))
    monkeypatch.setattr(
        "sys.argv",
        ["coverage_comment.py", "--repo", REPO, "--sha", HEAD],
    )

    coverage_comment.main()

    assert github.writes == [("PATCH", EDIT, body)]
    assert capsys.readouterr().err == log


def test_change_is_still_judged_when_the_figures_from_before_are_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the figures from before go: the verdict and today's figures stay.

    Codecov has no report for the commit CI merged into. The one line the
    pull request changes is missed, which is 0 of 1, and Codecov failed it.
    The comment is still posted, with the verdict, the missed line and, for
    the flag, how many lines changed. The anchor is the SHA-256 of the path.
    """
    reports: Reports = {
        AGAINST_BASE: 404,
        FOR_PULL: (
            UNCOVERED,
            [],
            [
                {
                    "name": "docker",
                    "base_report_totals": totals("95.93"),
                    "head_report_totals": totals("95.81"),
                    "diff_totals": totals("0", lines=1, hits=0),
                },
            ],
        ),
    }
    github = FakeGitHub([(7, HEAD)], OTHERS, [JOB_PASSED, FAILED])
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr(coverage_comment, "codecov", codecov_holding(reports))
    monkeypatch.setattr(
        "sys.argv",
        ["coverage_comment.py", "--repo", REPO, "--sha", HEAD],
    )

    coverage_comment.main()

    posted = (
        "<!-- cgt-calc-coverage -->\n"
        "### :x: 0.00% of changed lines covered\n"
        "\n"
        "0 of 1 changed lines covered, against a target of 90.00%.\n"
        "Total coverage: 96.12%.\n"
        "\n"
        "#### 1 changed line is not covered\n"
        "\n"
        "[`cgt_calc/util.py`](https://github.com/cgt-calc/"
        "capital-gains-calculator/pull/7/files#diff-"
        "ed746bb60c95619bf97b3d56a108ef98346e885088fe3989b92791d0a03d8f81)\n"
        "```text\n"
        "    1  missed   x = 1\n"
        "```\n"
        "\n"
        "<details>\n"
        "<summary>Flags</summary>\n"
        "\n"
        "| Flag | Before | After | Changed lines |\n"
        "|---|---|---|---|\n"
        "| `docker` | n/a | 95.81% | 0.00% of 1 |\n"
        "\n"
        "</details>\n"
        "\n"
        "[Full report on Codecov](https://app.codecov.io/gh/cgt-calc/"
        "capital-gains-calculator/pull/7) for commit `b653e4c`.\n"
    )
    assert github.writes == [("POST", NEW, posted)]


def test_fault_at_codecov_stops_the_run_and_drops_no_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a missing report is a reason to leave the figures from before out.

    A server error says nothing about the report for the commit CI merged
    into. The run fails, where it is seen and can be run again, and the
    comment already on the pull request is not rewritten without its figures.
    """
    github = FakeGitHub([(7, HEAD)], [OWN], [PASSED])
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr(
        coverage_comment,
        "codecov",
        codecov_holding({**BEHIND_MAIN, AGAINST_BASE: 500}),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["coverage_comment.py", "--repo", REPO, "--sha", HEAD],
    )

    with pytest.raises(urllib.error.HTTPError, match="HTTP Error 500"):
        coverage_comment.main()

    assert github.writes == []


# Pull request 7 when it changes three measured files and adds one line to
# each, all three missed: 0 of 3 covered. It renamed the last file, which is
# `old_name.py` on the base branch. It did not touch `model.py`.
PARSERS = "cgt_calc/parsers/base_parsers.py"
UTIL = "cgt_calc/util.py"
RENAMED = "cgt_calc/new_name.py"
THREE_FILES = comparison(
    [
        file(PARSERS, [added(387, "LOGGER.warning(message)", MISSED)]),
        file(UTIL, [added(1, "x = 1", MISSED)]),
        file(RENAMED, [added(5, "z = 3", MISSED)], was="cgt_calc/old_name.py"),
        file("cgt_calc/model.py", []),
    ],
    totals("0", lines=3, hits=0),
)
# The row of each file's missed line in the list of uncovered lines.
MISSED_LINE = {
    PARSERS: "  387  missed   LOGGER.warning(message)\n",
    UTIL: "    1  missed   x = 1\n",
    RENAMED: "    5  missed   z = 3\n",
}
ALL_LISTED = "#### 3 changed lines are not covered\n"
TWO_LISTED = "#### 2 changed lines are not covered in the other files\n"


@pytest.mark.parametrize(
    ("on_base", "merge", "base", "left_out", "heading"),
    [
        pytest.param(
            ("cgt_calc/model.py", "tests/general/test_util.py"),
            (MERGED_AT, BASE, HEAD),
            "main",
            [],
            ALL_LISTED,
            id="the base branch changed only files the pull request left alone",
        ),
        pytest.param(
            (PARSERS, "cgt_calc/model.py"),
            (MERGED_AT, BASE, HEAD),
            "main",
            [PARSERS],
            TWO_LISTED,
            id="the base branch changed one of the files too",
        ),
        pytest.param(
            ("cgt_calc/old_name.py",),
            (MERGED_AT, BASE, HEAD),
            "main",
            [RENAMED],
            TWO_LISTED,
            id="the base branch changed a file under the name it had before",
        ),
        pytest.param(
            tuple(f"docs/page{number}.md" for number in range(299)),
            (MERGED_AT, BASE, HEAD),
            "main",
            [],
            ALL_LISTED,
            id="GitHub lists 299 files, which is all there are",
        ),
        pytest.param(
            tuple(f"docs/page{number}.md" for number in range(300)),
            (MERGED_AT, BASE, HEAD),
            "main",
            [PARSERS, UTIL, RENAMED],
            "",
            id="GitHub lists 300 files, where it cuts the list",
        ),
        pytest.param(
            (),
            (MERGED_AT, BASE, OTHER_COMMIT),
            "main",
            [PARSERS, UTIL, RENAMED],
            "",
            id="the commit CI merged into is not known",
        ),
        pytest.param(
            (),
            (MERGED_AT, BASE, HEAD),
            "split-the-parser",
            [],
            ALL_LISTED,
            id="the pull request is stacked, and the branch below left them alone",
        ),
    ],
)
def test_lines_are_left_out_for_files_the_base_branch_changed_too(
    on_base: tuple[str, ...],
    merge: tuple[str, ...],
    base: str,
    left_out: list[str],
    heading: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codecov's figures for a file's lines are kept only where they must be right.

    CI measures the pull request merged into its base branch, and Codecov
    matches that coverage to the pull request's lines by number. Where the
    base branch changed a file after the pull request's branch started, the
    merged file's lines may be numbered otherwise, so that file is named as
    left out and its missed line is not listed. A file is known by either
    name when the pull request renamed it. With any file left out, Codecov's
    0 of 3 gives way to the plain verdict.

    When it cannot be told which files the base branch changed, every changed
    file is left out: GitHub cuts its list at 300 files, so a list that long
    may lack the one that matters, and without the commit CI merged into
    there is nothing to ask about. A stacked pull request has such a commit,
    on the branch below it, though it gets no figures from before.
    """
    reports: Reports = {
        AGAINST_BASE: (THREE_FILES, [], []),
        FOR_PULL: (THREE_FILES, [], []),
    }
    github = FakeGitHub(
        [(7, HEAD)],
        OTHERS,
        [FAILED],
        merge=merge,
        base=base,
        on_base=on_base,
    )
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr(
        coverage_comment,
        "codecov",
        codecov_holding(reports),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["coverage_comment.py", "--repo", REPO, "--sha", HEAD],
    )

    coverage_comment.main()

    [(method, path, body)] = github.writes
    assert (method, path) == ("POST", NEW)
    verdict = (
        "Codecov's check of the changed lines did not pass"
        if left_out
        else "0.00% of changed lines covered"
    )
    assert body.startswith(f"{MARKER}\n### :x: {verdict}\n")
    named = [line for line in body.split("\n") if line.startswith("- ")]
    assert named == [f"- `{path}`" for path in left_out]
    assert ("#### " in body) is bool(heading)
    assert heading in body
    for path, row in MISSED_LINE.items():
        assert (row in body) is (path not in left_out)


def test_fault_at_github_stops_the_run_and_takes_no_file_for_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list of the base branch's changes that cannot be read is not an empty one.

    Taking it for empty would show every line as rightly matched. The run
    fails, where it is seen and can be run again, and nothing is written.
    """
    github = FakeGitHub([(7, HEAD)], [OWN], [FAILED])

    def failing(*args: str, stdin: str | None = None) -> str:
        if f"repos/{REPO}/compare/{HEAD}...{BASE}" in args:
            raise subprocess.CalledProcessError(1, ["gh", "api", *args])
        return github(*args, stdin=stdin)

    monkeypatch.setattr(coverage_comment, "gh", failing)
    monkeypatch.setattr(
        coverage_comment,
        "codecov",
        codecov_holding(against_base(THREE_FILES)),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["coverage_comment.py", "--repo", REPO, "--sha", HEAD],
    )

    with pytest.raises(
        subprocess.CalledProcessError, match=r"/compare/.*exit status 1"
    ):
        coverage_comment.main()

    assert github.writes == []


@pytest.mark.parametrize(
    ("target", "pulls"),
    [
        pytest.param(["--sha", HEAD, "--dry-run"], [(7, HEAD)], id="for a commit"),
        pytest.param(["--pull", "7"], [], id="for a pull request number"),
    ],
)
def test_preview_prints_the_comment_and_writes_nothing(
    target: list[str],
    pulls: list[tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A preview shows the comment, whether or not one is already posted.

    A commit is previewed with `--dry-run`. A pull request number is always a
    preview, because the comment a person posted would not be the bot's to
    find and edit on the next run. Asked for by number, the pull request need
    not be open: its head commit is read from the pull request itself.
    """
    github = FakeGitHub(pulls, [OWN], [IMPOSTOR, PASSED])
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr(
        coverage_comment, "codecov", codecov_holding(against_base(MEASURED))
    )
    monkeypatch.setattr("sys.argv", ["coverage_comment.py", "--repo", REPO, *target])

    coverage_comment.main()

    assert github.writes == []
    assert capsys.readouterr().out.startswith(f"{MARKER}\n{COVERED}")


def test_run_without_a_commit_or_a_pull_request_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without either there is nothing to look up, so the script says so and stops."""
    github = FakeGitHub([(7, HEAD)], [OWN], [PASSED])
    monkeypatch.setattr(coverage_comment, "gh", github)
    monkeypatch.setattr("sys.argv", ["coverage_comment.py", "--repo", REPO])

    with pytest.raises(SystemExit):
        coverage_comment.main()

    assert "one of the arguments --sha --pull is required" in capsys.readouterr().err
    assert github.writes == []
