"""Check whether Dependabot metadata and auto-merge can run for this PR."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def main() -> None:
    """Skip blocked stack entries and commits that metadata would reject."""
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    pull = event["pull_request"]
    eligible = True

    if stack := pull.get("stack"):
        data = json.loads(
            subprocess.check_output(
                [
                    "gh",
                    "api",
                    f"repos/{os.environ['GITHUB_REPOSITORY']}/stacks/{stack['number']}",
                ],
                text=True,
            )
        )
        first = next(
            (pr["number"] for pr in data["pull_requests"] if pr["merged_at"] is None),
            None,
        )
        eligible = first == pull["number"]
        if not eligible:
            print("Skipping auto-merge until the pull requests below this one merge.")

    if eligible:
        commits = json.loads(
            subprocess.check_output(
                ["gh", "api", f"{pull['url']}/commits?per_page=1"], text=True
            )
        )
        first_commit = commits[0]
        eligible = (
            first_commit["author"] is not None
            and first_commit["author"]["login"] == "dependabot[bot]"
            and first_commit["commit"]["verification"]["verified"]
        )
        if not eligible:
            print(
                "Skipping auto-merge: the first commit is not verified Dependabot code."
            )

    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"eligible={str(eligible).lower()}\n")


if __name__ == "__main__":
    main()
