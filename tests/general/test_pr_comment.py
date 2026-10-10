"""Tests for the one comment the project's bot keeps on a pull request."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from scripts import pr_comment

REPO = "cgt-calc/capital-gains-calculator"
BOT = "cgtbot[bot]"
ACTIONS = "github-actions[bot]"
# The docs-preview workflow's marker, and the coverage-comment workflow's.
PREVIEW = "<!-- cgt-calc-docs-preview -->"
COVERAGE = "<!-- cgt-calc-coverage -->"


class FakeComments:
    """Stand in for the gh CLI holding the bot's token, on one pull request."""

    def __init__(
        self,
        repo: str,
        pull: int,
        comments: list[tuple[int, str, str]],
        *,
        refuses: str | None = None,
    ) -> None:
        """Hold the pull request's comments: each one's id, author and text.

        `refuses` is the method of the calls that GitHub turns down.
        """
        self.listing = f"repos/{repo}/issues/{pull}/comments?per_page=100"
        self.comments = comments
        self.refuses = refuses
        self.writes: list[tuple[str, str, str]] = []

    def __call__(self, *args: str, stdin: str | None = None) -> str:
        """Answer one call the way `gh api` would, and record each write.

        A call that sends a body is a POST unless it names another method. A
        body is sent only when the call names it as a field read from
        standard input, and a deletion sends none. The listing is answered
        only when every page is asked for, and comes back one JSON document a
        line, with characters outside ASCII left as they are, which is how jq
        writes them.
        """
        path = next(arg for arg in args if arg.startswith("repos/"))
        body = stdin if args[-2:] == ("--field", "body=@-") else None
        unnamed = "GET" if body is None else "POST"
        method = args[1] if args[0] == "--method" else unnamed
        if method == self.refuses:
            raise subprocess.CalledProcessError(1, ["gh", "api", *args])
        if method in {"POST", "PATCH"} and body is not None:
            self.writes.append((method, path, body))
            return ""
        if method == "DELETE" and stdin is None:
            self.writes.append((method, path, ""))
            return ""
        if method == "GET" and path == self.listing and "--paginate" in args:
            return "".join(
                f"{json.dumps(row, ensure_ascii=False)}\n" for row in self.comments
            )
        raise AssertionError(args)


# What the docs-preview workflow asks for: one text for a first comment, the
# same with the time added for a comment that is already there, and a third
# when a later push leaves the docs as they are live. The script reads none of
# them past the marker.
FIRST = f"{PREVIEW}\nDocs preview: https://pr-7.cgt-calc.pages.dev, from b653e4c"
EDITED = f"{FIRST}, updated 2026-10-10 18:05 BST"
UNCHANGED = f"{PREVIEW}\nNo rendered changes, checked b653e4c"
LINK = ["--create", FIRST, "--update", EDITED]
NO_CHANGE = ["--update", UNCHANGED]
# Comments that are not the docs preview's to manage. A person's that starts
# with the marker; one of GitHub Actions' and one of the bot's that only
# mention it later; and the coverage comment, from each of the two.
OTHERS = [
    (21, "someone", f"{PREVIEW}\nquoted by a person"),
    (22, ACTIONS, f"The preview is below. {PREVIEW}"),
    (23, BOT, f"The preview is below. {PREVIEW}"),
    (24, ACTIONS, f"{COVERAGE}\nfigures"),
    (25, BOT, f"{COVERAGE}\nfigures"),
]
OWN = (30, BOT, f"{PREVIEW}\nan earlier preview")
# Two runs racing could each post a comment.
OWN_COPY = (33, BOT, f"{PREVIEW}\na second copy")
OLD = (31, ACTIONS, f"{PREVIEW}\nfrom before the bot wrote these")
OLD_COPY = (32, ACTIONS, f"{PREVIEW}\na second copy")
NEW = f"repos/{REPO}/issues/7/comments"
OF_OWN = f"repos/{REPO}/issues/comments/30"
OF_OLD = f"repos/{REPO}/issues/comments/31"
OF_OLD_COPY = f"repos/{REPO}/issues/comments/32"
NOTHING_TO_UPDATE = "The pull request has no comment to update.\n"


def run(
    texts: list[str],
    github: FakeComments,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the script for pull request 7 as the docs-preview workflow does."""
    monkeypatch.setattr(pr_comment, "gh", github)
    monkeypatch.setenv("COMMENT_AUTHOR", BOT)
    monkeypatch.setattr(
        "sys.argv",
        ["pr_comment.py", "--repo", REPO, "--pull", "7", "--marker", PREVIEW, *texts],
    )
    pr_comment.main()


@pytest.mark.parametrize(
    ("comments", "texts", "writes", "said"),
    [
        pytest.param(
            OTHERS,
            LINK,
            [("POST", NEW, FIRST)],
            "",
            id="a first comment is posted",
        ),
        pytest.param(
            [*OTHERS, OWN, OWN_COPY],
            LINK,
            [("PATCH", OF_OWN, EDITED)],
            "",
            id="the bot's earlier comment is edited in place",
        ),
        pytest.param(
            [*OTHERS, OLD],
            LINK,
            [("POST", NEW, EDITED), ("DELETE", OF_OLD, "")],
            "",
            id="the bot's comment replaces the one GitHub Actions wrote",
        ),
        pytest.param(
            [OLD, *OTHERS, OLD_COPY],
            LINK,
            [
                ("POST", NEW, EDITED),
                ("DELETE", OF_OLD, ""),
                ("DELETE", OF_OLD_COPY, ""),
            ],
            "",
            id="it replaces every copy GitHub Actions wrote",
        ),
        pytest.param(
            [*OTHERS, OLD, OWN],
            LINK,
            [("PATCH", OF_OWN, EDITED), ("DELETE", OF_OLD, "")],
            "",
            id="a comment of each is left as one, the bot's",
        ),
        pytest.param(
            OTHERS,
            NO_CHANGE,
            [],
            NOTHING_TO_UPDATE,
            id="no comment yet and no text for a first one, so none is posted",
        ),
        pytest.param(
            [*OTHERS, OWN],
            NO_CHANGE,
            [("PATCH", OF_OWN, UNCHANGED)],
            "",
            id="no text for a first one, but the bot's is brought up to date",
        ),
        pytest.param(
            [*OTHERS, OLD],
            NO_CHANGE,
            [("POST", NEW, UNCHANGED), ("DELETE", OF_OLD, "")],
            "",
            id="no text for a first one, but GitHub Actions' is still replaced",
        ),
    ],
)
def test_what_a_run_writes_to_the_pull_request(
    comments: list[tuple[int, str, str]],
    texts: list[str],
    writes: list[tuple[str, str, str]],
    said: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One run leaves the pull request with one comment, and it is the bot's.

    The comment is the bot's own that starts with the marker, the older of
    two. Where GitHub Actions wrote it instead, the bot posts the text for a
    comment that is already there, since for the reader it is, and only then
    deletes every copy of GitHub Actions'. Nobody else's comment is edited or
    deleted, whatever it starts with, and neither is one of the bot's or of
    GitHub Actions' that starts with anything else.
    """
    github = FakeComments(REPO, 7, comments)

    run(texts, github, monkeypatch)

    assert github.writes == writes
    assert capsys.readouterr().out == said


@pytest.mark.parametrize(
    ("comments", "refuses", "error", "writes"),
    [
        pytest.param(
            [*OTHERS, OLD],
            "POST",
            r"issues/7/comments', '--field'.*exit status 1",
            [],
            id="the bot's comment cannot be posted",
        ),
        pytest.param(
            [*OTHERS, OLD, OWN],
            "PATCH",
            r"'PATCH', .*issues/comments/30'.*exit status 1",
            [],
            id="the bot's comment cannot be edited",
        ),
        pytest.param(
            [*OTHERS, OLD, OLD_COPY],
            "DELETE",
            r"'DELETE', .*issues/comments/31'.*exit status 1",
            [("POST", NEW, EDITED)],
            id="GitHub Actions' comment cannot be deleted",
        ),
    ],
)
def test_refusal_from_github_stops_the_run(
    comments: list[tuple[int, str, str]],
    refuses: str,
    error: str,
    writes: list[tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub Actions' comment goes only after the bot's is written.

    When the bot's comment cannot be written, the run fails with nothing
    deleted, so the pull request keeps the comment it had. When a deletion is
    refused, the run fails at that one, where it is seen: the bot's comment is
    up, and the next run edits it and tries the deletions again.
    """
    github = FakeComments(REPO, 7, comments, refuses=refuses)

    with pytest.raises(subprocess.CalledProcessError, match=error):
        run(LINK, github, monkeypatch)

    assert github.writes == writes


def gh_exiting(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[list[str], dict[str, str] | None, object]]:
    """Put a gh that exits with `status` where the real one is run.

    The workflow's token is in GH_TOKEN and the bot's in COMMENT_TOKEN, as in
    a workflow. Each call is noted: the command, its environment and what it
    was sent. As with a real process, a failure raises only when asked to.
    """
    ran: list[tuple[list[str], dict[str, str] | None, object]] = []

    def run_gh(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = False,
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        ran.append((command, env, options["input"]))
        if check and status:
            raise subprocess.CalledProcessError(status, command)
        return subprocess.CompletedProcess(command, status, stdout="answer")

    monkeypatch.setenv("GH_TOKEN", "the workflow's")
    monkeypatch.setenv("COMMENT_TOKEN", "the bot's")
    monkeypatch.setattr(subprocess, "run", run_gh)
    return ran


def test_github_is_called_with_the_bots_token_not_the_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comment written with the workflow's token would be GitHub Actions'.

    gh takes its token from GH_TOKEN, which is where a workflow keeps its own
    for what it reads, so the bot's goes there for the call. The rest of the
    environment is passed on: gh is found on PATH.
    """
    ran = gh_exiting(0, monkeypatch)

    assert pr_comment.gh(NEW, "--field", "body=@-", stdin=FIRST) == "answer"

    assert ran == [
        (
            ["gh", "api", NEW, "--field", "body=@-"],
            {**os.environ, "GH_TOKEN": "the bot's"},
            FIRST,
        ),
    ]


def test_call_that_gh_reports_as_failed_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that GitHub refuses is an error, not an empty answer.

    It is what stops a run before anything is deleted.
    """
    gh_exiting(1, monkeypatch)

    with pytest.raises(subprocess.CalledProcessError, match="exit status 1"):
        pr_comment.gh(NEW, "--field", "body=@-", stdin=FIRST)
