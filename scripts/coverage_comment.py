"""Post the pull request coverage comment from Codecov's figures.

Codecov's own comment is cut down to a patch summary on the organisation's
plan. This builds a fuller one from Codecov's public API: the changed lines
that tests do not cover, total coverage before and after, and the figures by
component and by flag. "Before" is the commit on the default branch that CI
merged the pull request into, not the older one its branch started from.
Where the base branch has changed a file that the pull request changes too,
Codecov may match coverage to the wrong lines of it, so the comment leaves
that file's lines and every count of changed lines out.
The coverage-comment workflow runs it when Codecov's patch check lands. To
print the comment for a pull request without posting it:

    python3 scripts/coverage_comment.py --repo OWNER/NAME --pull 1151

Standard library only: the workflow runs it with the runner's Python, without
installing the project. GitHub is reached through the `gh` CLI, which holds
the token.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
from http import HTTPStatus
import json
import re
import subprocess
import sys
from typing import NamedTuple, TypedDict
import urllib.error
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
# GitHub lists at most this many files when it compares two commits, so a
# list this long may have been cut short.
MAX_COMPARED_FILES = 300
# The jq filter that cuts GitHub's account of a pull request down to what
# PullRequest holds.
PULL = (
    "{number, merge_commit_sha, head: .head.sha, base: .base.ref,"
    " default_branch: .base.repo.default_branch}"
)


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


class PullRequest(TypedDict):
    """A pull request: its head commit, the branch it targets, its test merge.

    GitHub names no test merge for a pull request that conflicts.
    """

    number: int
    head: str
    base: str
    default_branch: str
    merge_commit_sha: str | None


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


def figure(totals: Totals | None) -> str:
    """Format the coverage of a component or flag, which may have no report."""
    return percent(totals["coverage"]) if totals else "n/a"


def code_block(lines: list[str]) -> list[str]:
    """Fence source lines so that no run of backticks in them ends the block."""
    longest = max((len(run) for run in re.findall(r"`+", "\n".join(lines))), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", *lines, fence]


def uncovered_lines(repo: str, pull: int, files: list[File], where: str) -> list[str]:
    """List the added lines that tests miss or only partly take, by file.

    `where` ends the heading when `files` are not all the changed ones.
    """
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
    return [f"#### {heading} not covered{where}", "", *section]


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


def breakdown(
    title: str,
    column: str,
    rows: list[Breakdown],
    *,
    exact: bool,
) -> list[str]:
    """Tabulate the figures by component or by flag, folded away.

    The count of changed lines is given only when it is `exact`. A row's
    count may include the lines of a file that are misaligned, and Codecov
    does not say which rows hold which files.
    """
    if not rows:
        return []

    def changed(diff: Totals | None) -> str:
        if not exact:
            return "n/a"
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
    shifted: list[str],
) -> Comment:
    """Build the comment for a pull request from Codecov's figures.

    `shifted` names the changed files whose lines Codecov may have matched
    wrongly. Their uncovered lines are left out, and so is every count of
    changed lines: the one for the whole change includes theirs, and a
    component's or a flag's may. Codecov's verdict stays, and the comment is
    worth posting to say what it leaves out.
    """
    totals = compare["totals"]
    base, head, patch = totals["base"], totals["head"], totals["patch"]
    measured = patch["lines"] if patch else 0
    indirect = indirect_changes(compare["files"])
    delta = (
        Decimal(head["coverage"]) - Decimal(base["coverage"]) if base else Decimal(0)
    )
    # A figure can move for one flag or component alone, as when a test only
    # adds coverage on Windows. Without a report from before, none has moved.
    moved = base and any(
        figure(row["base_report_totals"]) != figure(row["head_report_totals"])
        for row in [*components, *flags]
    )

    icon = ":white_check_mark:" if check["conclusion"] == "success" else ":x:"
    target = re.search(r"target ([\d.]+)%", check["title"] or "")
    if shifted:
        outcome = "passed" if check["conclusion"] == "success" else "did not pass"
        names = [f"- `{plain(path)}`" for path in shifted]
        if len(names) > MAX_FILES:
            names[MAX_FILES:] = [f"- and {len(names) - MAX_FILES} more"]
        verdict = [
            f"### {icon} Codecov's check of the changed lines {outcome}",
            "",
            (f"The check's target is {target[1]}%. " if target else "")
            + "Codecov's count of covered lines is left out, and so are the"
            " uncovered lines of the files below, because their coverage may"
            " have been matched to other lines. CI tests the pull request"
            " merged into its base branch, which may have changed these files"
            " since this branch started, and a line then has another number in"
            " what CI tested.",
            "",
            *names,
            "",
        ]
    elif patch and measured:
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
    aligned = [file for file in compare["files"] if file["name"]["head"] not in shifted]
    body = [
        MARKER,
        *verdict,
        f"Total coverage: {total}.",
        "",
        *uncovered_lines(
            repo,
            pull,
            aligned,
            " in the other files" if shifted else "",
        ),
        *indirect,
        *breakdown("Components", "Component", components, exact=not shifted),
        *breakdown("Flags", "Flag", flags, exact=not shifted),
        footer,
    ]
    return Comment(
        "\n".join(body) + "\n",
        bool(shifted or measured or indirect or delta or moved),
    )


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


def merged_into(repo: str, pull: PullRequest) -> str | None:
    """Find the commit on its base branch that CI merged the pull request into.

    CI tests GitHub's test merge of the pull request, not its head commit, so
    the report Codecov files under the head is for that merge. The commit
    merged into is the merge's first parent, which is newer than the commit
    the branch started from whenever the branch is behind. The answer is
    None, for a commit that cannot be told, unless both of these hold:

    - GitHub names a test merge whose second parent is this head commit. It
      names none for a pull request that conflicts, the commit on its base
      branch for one that is merged, and just after a push the merge of the
      head before.
    - That merge is no newer than any workflow run the pull request started
      on this head commit, and there is such a run. GitHub may rebuild the
      test merge after the base branch moves, and CI tested the one that
      stood when the run was created. A re-run keeps that time.
    """
    merge = pull["merge_commit_sha"]
    if not merge:
        return None
    commit = json.loads(gh(f"repos/{repo}/git/commits/{merge}"))
    committed: str = commit["committer"]["date"]
    parents: list[str] = [parent["sha"] for parent in commit["parents"]]
    created: list[str] = [
        json.loads(line)["created_at"]
        for line in gh(
            f"repos/{repo}/actions/runs"
            f"?head_sha={pull['head']}&event=pull_request&per_page=100",
            "--paginate",
            "--jq",
            ".workflow_runs[] | {created_at} | @json",
        ).split("\n")
        if line
    ]
    # Both times are UTC to the second in one format, so they compare as text.
    if parents[1:] == [pull["head"]] and created and committed <= min(created):
        return parents[0]
    return None


def shifted_files(
    repo: str,
    pull: PullRequest,
    tested: str | None,
    files: list[File],
) -> list[str]:
    """Name the changed files whose lines Codecov may have matched wrongly.

    Codecov holds the coverage of the test merge and matches it to the pull
    request's own lines by number. The numbers agree for a file that the base
    branch has left alone since the pull request's branch started; any change
    to it there can move the lines of the merged file. So a changed file is
    named when the base branch changed it too, under the name it has in the
    pull request or the one it had before. Every changed file is named when
    that cannot be told: `tested`, the commit CI merged into, is not known,
    or GitHub's list of what changed is as long as it lets one be.
    """
    changed = [file for file in files if file["has_diff"]]
    if tested:
        # Three dots: what changed on the way from the commit the two share
        # to the commit CI merged into.
        on_base: list[str] = json.loads(
            gh(
                f"repos/{repo}/compare/{pull['head']}...{tested}",
                "--jq",
                "[.files[].filename]",
            ),
        )
        if len(on_base) < MAX_COMPARED_FILES:
            changed = [
                file
                for file in changed
                if {file["name"]["base"], file["name"]["head"]} & set(on_base)
            ]
    return [file["name"]["head"] for file in changed]


def figures(
    repo: str,
    pull: PullRequest,
    base: str | None,
) -> tuple[Compare, list[Breakdown], list[Breakdown], str]:
    """Read Codecov's comparison for the head commit, by component and flag too.

    It is compared with `base`, the commit on the default branch that CI
    merged it into. Without that commit, or without a report for it, what is
    left is Codecov's own comparison for the pull request, which starts from
    the commit the branch forked at. Every file changed on the default branch
    since then would show there as coverage the pull request moved, so its
    figures from before are dropped and the comment gives only the current
    ones. The last item returned says why they were dropped, and is empty
    when they were not.
    """

    def read(query: str) -> tuple[Compare, list[Breakdown], list[Breakdown]]:
        compare, components, flags = (
            json.loads(codecov(repo, f"compare/{part}?{query}"), parse_float=Decimal)
            for part in ("", "components", "flags")
        )
        return compare, components, flags

    dropped = "CI is not known to have merged it into a commit on the default branch"
    if base:
        try:
            return (*read(f"base={base}&head={pull['head']}"), "")
        except urllib.error.HTTPError as error:
            # Not found is Codecov's answer for a commit it holds no report
            # for. Any other refusal is a fault, and stops the run.
            if error.code != HTTPStatus.NOT_FOUND:
                raise
        dropped = f"Codecov has no report for {base}, the commit CI merged it into"
    compare, components, flags = read(f"pullid={pull['number']}")
    compare["totals"]["base"] = None
    for file in compare["files"]:
        file["totals"]["base"] = None
    for row in [*components, *flags]:
        row["base_report_totals"] = None
    return compare, components, flags, dropped


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
    target.add_argument(
        "--pull",
        type=int,
        help="pull request number: print its comment and post nothing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --sha, print the comment and post nothing",
    )
    args = parser.parse_args()
    repo: str = args.repo
    found: PullRequest

    if args.pull is None:
        # Codecov's check does not name its pull request, and GitHub's lookup
        # of a commit's pull requests misses those from forks, so match the
        # commit against the head of every open one.
        opened: list[PullRequest] = [
            json.loads(line)
            for line in gh(
                f"repos/{repo}/pulls?state=open&per_page=100",
                "--paginate",
                "--jq",
                f".[] | {PULL} | @json",
            ).split("\n")
            if line
        ]
        matching = [one for one in opened if one["head"] == args.sha]
        if not matching:
            print(f"No open pull request has {args.sha} as its head; nothing to do.")
            return
        found = matching[0]
    else:
        found = json.loads(gh(f"repos/{repo}/pulls/{args.pull}", "--jq", PULL))
    pull, sha = found["number"], found["head"]

    tested = merged_into(repo, found)
    # Coverage before the pull request is the report of the commit CI merged
    # it into, and only on the default branch is that report the commit's
    # own. A stacked pull request is merged into the head of another one,
    # whose report is for that one's test merge.
    before = tested if found["base"] == found["default_branch"] else None
    compare, components, flags, dropped = figures(repo, found, before)
    if compare["head_commit"] != sha:
        print(f"Codecov has not compared {sha} yet; nothing to do.")
        return
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
    if dropped:
        print(
            f"Left out the figures from before the pull request: {dropped}.",
            file=sys.stderr,
        )
    shifted = shifted_files(repo, found, tested, compare["files"])
    comment = render(repo, pull, judged[0], compare, components, flags, shifted)
    # A run by pull request number is a person's. It only ever prints: a
    # comment posted under their name would not be found again and edited.
    if args.dry_run or args.pull is not None:
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
