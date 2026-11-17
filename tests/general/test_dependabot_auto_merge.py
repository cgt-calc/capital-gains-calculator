"""Checks before reading Dependabot metadata or requesting auto-merge."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts.dependabot_auto_merge import main

if TYPE_CHECKING:
    from pathlib import Path

REPO = "example/project"
PR_URL = f"https://api.github.com/repos/{REPO}/pulls/7"


@pytest.fixture
def github_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Supply the runner's event and output files without GitHub writes."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": 7, "url": PR_URL}}), encoding="utf-8"
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    return event


@pytest.mark.parametrize(
    ("entries", "author", "verified", "expected"),
    [
        pytest.param(None, "dependabot[bot]", True, True, id="standalone"),
        pytest.param(None, "KapJI", True, False, id="human-first-commit"),
        pytest.param(None, None, True, False, id="unknown-author"),
        pytest.param(None, "dependabot[bot]", False, False, id="unsigned"),
        pytest.param(
            [{"number": 7, "merged_at": None}, {"number": 2, "merged_at": None}],
            "dependabot[bot]",
            True,
            True,
            id="bottom-of-stack",
        ),
        pytest.param(
            [{"number": 9, "merged_at": None}, {"number": 7, "merged_at": None}],
            "dependabot[bot]",
            True,
            False,
            id="unmerged-parent",
        ),
        pytest.param(
            [
                {"number": 3, "merged_at": None, "state": "closed"},
                {"number": 7, "merged_at": None},
            ],
            "dependabot[bot]",
            True,
            False,
            id="closed-parent-is-not-merged",
        ),
        pytest.param(
            [
                {"number": 3, "merged_at": "2026-11-17T12:00:00Z"},
                {"number": 7, "merged_at": None},
            ],
            "dependabot[bot]",
            True,
            True,
            id="parent-merged",
        ),
        pytest.param(
            [{"number": 7, "merged_at": "2026-11-17T12:00:00Z"}],
            "dependabot[bot]",
            True,
            False,
            id="stack-already-merged",
        ),
    ],
)
def test_eligibility_written_for_the_metadata_step(
    github_event: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: list[dict[str, int | str | None]] | None,
    author: str | None,
    verified: bool,
    expected: bool,
) -> None:
    """Only verified bot commits with no unmerged dependency reach metadata."""
    responses = {
        f"{PR_URL}/commits?per_page=1": [
            {
                "author": {"login": author} if author is not None else None,
                "commit": {"verification": {"verified": verified}},
            }
        ],
    }
    if entries is not None:
        event = json.loads(github_event.read_text(encoding="utf-8"))
        event["pull_request"]["stack"] = {"number": 42}
        github_event.write_text(json.dumps(event), encoding="utf-8")

    calls: list[str] = []

    def read(args: list[str], *, text: bool) -> str:
        assert args[:2] == ["gh", "api"]
        assert text
        calls.append(args[2])
        if args[2] == f"repos/{REPO}/stacks/42":
            return json.dumps({"pull_requests": entries})
        return json.dumps(responses[args[2]])

    monkeypatch.setattr(subprocess, "check_output", read)

    main()

    assert (tmp_path / "output").read_text(encoding="utf-8") == (
        f"eligible={str(expected).lower()}\n"
    )
    if entries is None:
        assert calls == [f"{PR_URL}/commits?per_page=1"]
    elif not expected:
        assert calls == [f"repos/{REPO}/stacks/42"]


def test_api_errors_fail_instead_of_becoming_a_skip(
    github_event: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable API must not be mistaken for ineligible commits."""

    def refused(args: list[str], *, text: bool) -> str:
        raise subprocess.CalledProcessError(1, args, stderr="API unavailable")

    monkeypatch.setattr(subprocess, "check_output", refused)

    with pytest.raises(subprocess.CalledProcessError, match="exit status 1"):
        main()

    assert not (tmp_path / "output").exists()
