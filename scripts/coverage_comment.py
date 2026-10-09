"""Post the pull request coverage comment from Codecov's figures.

Codecov's own comment is cut down to a patch summary on the organisation's
plan. This builds a fuller one from Codecov's public API: the changed lines
that tests do not cover, total coverage before and after, and the figures by
component and by flag. The coverage-comment workflow runs it when Codecov's
patch check lands. To preview the comment for a pull request without posting:

    python3 scripts/coverage_comment.py --repo OWNER/NAME --pull 1151 --dry-run

Standard library only: the workflow runs it with the runner's Python, without
installing the project. GitHub is reached through the `gh` CLI, which holds
the token.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import re
import subprocess
from typing import NamedTuple, TypedDict
import urllib.request

# Starts the comment, so that a later run finds it and edits it in place.
MARKER = "<!-- cgt-calc-coverage -->"
# Codecov's codes for a line that no test runs, and for one whose branches
# are only partly taken.
LINE_STATES: dict[int | None, str] = {1: "missed", 2: "partial"}
# GitHub refuses a comment over 65,536 characters. These cuts keep an ordinary
# pull request's comment far below that.
MAX_LINES = 30
MAX_LINE_LENGTH = 100
MAX_FILES = 10


class Totals(TypedDict):
    """Coverage counts for a report, or for the changed lines of one."""

    coverage: Decimal | int
    lines: int
    hits: int


class TotalsByVersion(TypedDict):
    """Totals before the pull request, after it, and for its changed lines."""

    base: Totals | None
    head: Totals
    patch: Totals | None


class BaseAndHead(TypedDict):
    """A line's number or coverage code, before and after the pull request."""

    base: int | None
    head: int | None


class Line(TypedDict):
    """One line of a file's diff."""

    value: str
    number: BaseAndHead
    coverage: BaseAndHead
    added: bool


class FileName(TypedDict):
    """A file's path before and after the pull request.

    Codecov lists the files of the head report, so there is always a path
    after; a file the pull request adds has none before.
    """

    base: str | None
    head: str


class File(TypedDict):
    """One file of the coverage report."""

    name: FileName
    totals: TotalsByVersion
    has_diff: bool
    lines: list[Line]


class Compare(TypedDict):
    """Codecov's comparison of a pull request with its base."""

    head_commit: str
    totals: TotalsByVersion
    files: list[File]


class Breakdown(TypedDict):
    """The comparison for one component or one flag."""

    name: str
    base_report_totals: Totals | None
    head_report_totals: Totals | None
    diff_totals: Totals | None


class Check(TypedDict):
    """A check run named codecov/patch on the head commit, and whose it is."""

    app: str
    conclusion: str | None
    title: str | None


class Comment(NamedTuple):
    """A rendered comment, and whether it says enough to post a new one."""

    body: str
    worth_posting: bool


def plain(text: str) -> str:
    """Keep what a path, flag or component name needs and replace the rest.

    These names come from the pull request, which may be a fork's. Each is
    shown as code, where a bare web address or issue number is not turned
    into a link, and what is replaced here includes the backtick and the
    table bar that would end that code or its cell.
    """
    return re.sub(r"[^\w ./+-]", "?", text)


def percent(value: Decimal | int) -> str:
    """Format a coverage figure the way Codecov reports it."""
    return f"{Decimal(value):.2f}%"


def code_block(lines: list[str]) -> list[str]:
    """Fence source lines so that no run of backticks in them ends the block."""
    longest = max((len(run) for run in re.findall(r"`+", "\n".join(lines))), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", *lines, fence]


def uncovered_lines(repo: str, pull: int, files: list[File]) -> list[str]:
    """List the added lines that tests miss or only partly take, by file."""
    section: list[str] = []
    total = 0
    for file in files:
        found = [
            f"{line['number']['head']:>5}  {state:<7}  "
            + line["value"][1 : MAX_LINE_LENGTH + 1]
            for line in file["lines"]
            if line["added"] and (state := LINE_STATES.get(line["coverage"]["head"]))
        ]
        shown = found[: max(0, MAX_LINES - total)]
        total += len(found)
        path = file["name"]["head"]
        if shown:
            anchor = hashlib.sha256(path.encode()).hexdigest()
            link = f"https://github.com/{repo}/pull/{pull}/files#diff-{anchor}"
            section += [f"[`{plain(path)}`]({link})", *code_block(shown), ""]
    if not total:
        return []
    heading = "1 changed line is" if total == 1 else f"{total} changed lines are"
    if total > MAX_LINES:
        section += [f"And {total - MAX_LINES} more in the full report.", ""]
    return [f"#### {heading} not covered", "", *section]


def indirect_changes(files: list[File]) -> list[str]:
    """List the files the pull request did not touch whose coverage moved."""
    rows = [
        f"| `{plain(file['name']['head'])}` "
        f"| {percent(base['coverage'])} | {percent(head['coverage'])} |"
        for file in files
        if not file["has_diff"]
        and (base := file["totals"]["base"])
        and base["coverage"] != (head := file["totals"]["head"])["coverage"]
    ]
    if not rows:
        return []
    section = [
        "#### Coverage changed in files this pull request did not touch",
        "",
        "| File | Before | After |",
        "|---|---|---|",
        *rows[:MAX_FILES],
        "",
    ]
    if len(rows) > MAX_FILES:
        section += [f"And {len(rows) - MAX_FILES} more in the full report.", ""]
    return section


def breakdown(title: str, column: str, rows: list[Breakdown]) -> list[str]:
    """Tabulate the figures by component or by flag, folded away."""
    if not rows:
        return []

    def figure(totals: Totals | None) -> str:
        return percent(totals["coverage"]) if totals else "n/a"

    def changed(diff: Totals | None) -> str:
        if diff and diff["lines"]:
            return f"{percent(diff['coverage'])} of {diff['lines']}"
        return "none"

    table = [
        f"| `{plain(row['name'])}` | {figure(row['base_report_totals'])} "
        f"| {figure(row['head_report_totals'])} | {changed(row['diff_totals'])} |"
        for row in sorted(rows, key=lambda row: row["name"])
    ]
    return [
        "<details>",
        f"<summary>{title}</summary>",
        "",
        f"| {column} | Before | After | Changed lines |",
        "|---|---|---|---|",
        *table,
        "",
        "</details>",
        "",
    ]


def render(
    repo: str,
    pull: int,
    check: Check,
    compare: Compare,
    components: list[Breakdown],
    flags: list[Breakdown],
) -> Comment:
    """Build the comment for a pull request from Codecov's figures."""
    totals = compare["totals"]
    base, head, patch = totals["base"], totals["head"], totals["patch"]
    measured = patch["lines"] if patch else 0
    indirect = indirect_changes(compare["files"])
    delta = (
        Decimal(head["coverage"]) - Decimal(base["coverage"]) if base else Decimal(0)
    )

    if patch and measured:
        icon = ":white_check_mark:" if check["conclusion"] == "success" else ":x:"
        target = re.search(r"target ([\d.]+)%", check["title"] or "")
        against = f", against a target of {target[1]}%" if target else ""
        verdict = [
            f"### {icon} {percent(patch['coverage'])} of changed lines covered",
            "",
            f"{patch['hits']} of {measured} changed lines covered{against}.",
        ]
    else:
        verdict = ["### No measured lines changed", ""]
    if base:
        change = f"{delta:+.2f} points" if delta else "no change"
        total = f"{percent(base['coverage'])} -> {percent(head['coverage'])} ({change})"
    else:
        total = percent(head["coverage"])

    footer = (
        f"[Full report on Codecov](https://app.codecov.io/gh/{repo}/pull/{pull})"
        f" for commit `{compare['head_commit'][:7]}`."
    )
    body = [
        MARKER,
        *verdict,
        f"Total coverage: {total}.",
        "",
        *uncovered_lines(repo, pull, compare["files"]),
        *indirect,
        *breakdown("Components", "Component", components),
        *breakdown("Flags", "Flag", flags),
        footer,
    ]
    return Comment("\n".join(body) + "\n", bool(measured or indirect or delta))


def gh(*args: str, stdin: str | None = None) -> str:
    """Call the GitHub API through the gh CLI and return what it prints."""
    return subprocess.run(
        ["gh", "api", *args],
        input=stdin,
        stdout=subprocess.PIPE,
        check=True,
        text=True,
        encoding="utf-8",
    ).stdout


def codecov(repo: str, path: str) -> str:
    """Read one endpoint of Codecov's public API for the repository."""
    owner, name = repo.split("/")
    url = f"https://api.codecov.io/api/v2/github/{owner}/repos/{name}/{path}"
    with urllib.request.urlopen(url, timeout=60) as response:
        text: str = response.read().decode()
    return text


def main() -> None:
    """Render the comment for one pull request, then post or update it."""
    parser = argparse.ArgumentParser(
        description="Post the pull request coverage comment from Codecov's figures.",
    )
    parser.add_argument("--repo", required=True, help="repository as OWNER/NAME")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--sha",
        help="head commit of an open pull request, as the workflow knows it",
    )
    target.add_argument("--pull", type=int, help="pull request number, to preview")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the comment and post nothing",
    )
    args = parser.parse_args()
    repo: str = args.repo
    pull: int | None = args.pull
    sha: str | None = args.sha

    if pull is None:
        # Codecov's check does not name its pull request, and GitHub's lookup
        # of a commit's pull requests misses those from forks, so match the
        # commit against the head of every open one.
        heads: list[tuple[int, str]] = [
            json.loads(line)
            for line in gh(
                f"repos/{repo}/pulls?state=open&per_page=100",
                "--paginate",
                "--jq",
                ".[] | [.number, .head.sha] | @json",
            ).split("\n")
            if line
        ]
        matching = [number for number, head in heads if head == sha]
        if not matching:
            print(f"No open pull request has {sha} as its head; nothing to do.")
            return
        pull = matching[0]
    else:
        sha = gh(f"repos/{repo}/pulls/{pull}", "--jq", ".head.sha").strip()

    compare: Compare = json.loads(
        codecov(repo, f"compare/?pullid={pull}"),
        parse_float=Decimal,
    )
    if compare["head_commit"] != sha:
        print(f"Codecov has not compared {sha} yet; nothing to do.")
        return
    components: list[Breakdown] = json.loads(
        codecov(repo, f"compare/components?pullid={pull}"),
        parse_float=Decimal,
    )
    flags: list[Breakdown] = json.loads(
        codecov(repo, f"compare/flags?pullid={pull}"),
        parse_float=Decimal,
    )
    checks: list[Check] = json.loads(
        gh(
            f"repos/{repo}/commits/{sha}/check-runs?check_name=codecov/patch&per_page=100",
            "--jq",
            "[.check_runs[] | {app: .app.slug, conclusion, title: .output.title}]",
        ),
    )
    # A workflow job can be given the same name, so the check is taken by its
    # app. One without a conclusion is still running.
    judged = [
        check for check in checks if check["app"] == "codecov" and check["conclusion"]
    ]
    if not judged:
        print(f"Codecov's patch check has not reported on {sha}; nothing to do.")
        return
    comment = render(repo, pull, judged[0], compare, components, flags)
    if args.dry_run:
        print(comment.body, end="")
        return

    # One JSON document a line. Split on the newline alone: a comment may hold
    # other characters that str.splitlines() also breaks at, which jq leaves
    # as they are inside a string.
    comments: list[tuple[int, str, str]] = [
        json.loads(line)
        for line in gh(
            f"repos/{repo}/issues/{pull}/comments?per_page=100",
            "--paginate",
            "--jq",
            ".[] | [.id, .user.login, .body] | @json",
        ).split("\n")
        if line
    ]
    own = [
        comment_id
        for comment_id, author, text in comments
        if author == "github-actions[bot]" and text.startswith(MARKER)
    ]
    if own:
        gh(
            "--method",
            "PATCH",
            f"repos/{repo}/issues/comments/{own[0]}",
            "--field",
            "body=@-",
            stdin=comment.body,
        )
    elif comment.worth_posting:
        gh(
            f"repos/{repo}/issues/{pull}/comments",
            "--field",
            "body=@-",
            stdin=comment.body,
        )
    else:
        print("Nothing measured changed and there is no comment to update.")


if __name__ == "__main__":
    main()
