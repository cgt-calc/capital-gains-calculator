"""Keep one comment from the project's bot on a pull request.

The coverage-comment and docs-preview workflows each keep a single comment on
a pull request and rewrite it as the pull request moves on. Such a comment is
known by the marker its text starts with and by its author: the bot whose
login is in COMMENT_AUTHOR and whose token is in COMMENT_TOKEN. That is the
project's GitHub App, so a reader can tell these comments from what GitHub
Actions itself writes.

GitHub Actions wrote them before the bot did. Once the bot's comment is
written, every comment of GitHub Actions' that starts with the same marker is
deleted, so the pull request keeps one. No other comment is touched.

    python3 scripts/pr_comment.py --repo OWNER/NAME --pull 1151 \
        --marker '<!-- marker -->' --create TEXT --update TEXT

Standard library only: a workflow runs it with the runner's Python, without
installing the project. GitHub is reached through the `gh` CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess

# Who wrote these comments before the bot took them over.
LEGACY_AUTHOR = "github-actions[bot]"


def gh(*args: str, stdin: str | None = None) -> str:
    """Call the GitHub API through the gh CLI as the bot and return what it prints.

    The workflow's own token may be in GH_TOKEN, which is where gh looks, so
    the bot's is put there for this call.
    """
    return subprocess.run(
        ["gh", "api", *args],
        input=stdin,
        stdout=subprocess.PIPE,
        check=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GH_TOKEN": os.environ["COMMENT_TOKEN"]},
    ).stdout


def publish(repo: str, pull: int, marker: str, update: str, create: str | None) -> bool:
    """Write the bot's comment on a pull request, and say whether it wrote one.

    The comment says `update` when the pull request already has it, from the
    bot or from GitHub Actions, and `create` when it has neither. Without
    `create`, a pull request that has neither is left without one. The bot
    cannot take over a comment of GitHub Actions', so it posts its own and
    then deletes that one. A write that GitHub refuses stops the run before
    anything is deleted.
    """
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
    marked = [
        (comment_id, author)
        for comment_id, author, text in comments
        if text.startswith(marker)
    ]
    bot = os.environ["COMMENT_AUTHOR"]
    own = [comment_id for comment_id, author in marked if author == bot]
    legacy = [comment_id for comment_id, author in marked if author == LEGACY_AUTHOR]
    new = f"repos/{repo}/issues/{pull}/comments"
    if own:
        # Two runs racing could each post a comment. The older one is kept up
        # to date.
        gh(
            "--method",
            "PATCH",
            f"repos/{repo}/issues/comments/{own[0]}",
            "--field",
            "body=@-",
            stdin=update,
        )
    elif legacy:
        gh(new, "--field", "body=@-", stdin=update)
    elif create is not None:
        gh(new, "--field", "body=@-", stdin=create)
    else:
        return False
    for comment_id in legacy:
        gh("--method", "DELETE", f"repos/{repo}/issues/comments/{comment_id}")
    return True


def main() -> None:
    """Write the comment that the command line describes."""
    parser = argparse.ArgumentParser(
        description="Keep one comment from the project's bot on a pull request.",
    )
    parser.add_argument("--repo", required=True, help="repository as OWNER/NAME")
    parser.add_argument("--pull", required=True, type=int, help="pull request number")
    parser.add_argument(
        "--marker",
        required=True,
        help="what the comment's text starts with",
    )
    parser.add_argument(
        "--update",
        required=True,
        help="the text for a comment the pull request already has",
    )
    parser.add_argument(
        "--create",
        help="the text for a first comment; without it, none is posted",
    )
    args = parser.parse_args()
    if not publish(args.repo, args.pull, args.marker, args.update, args.create):
        print("The pull request has no comment to update.")


if __name__ == "__main__":
    main()
