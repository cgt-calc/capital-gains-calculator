"""Tests for version resolution."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version
import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.version import (
    DISTRIBUTION_NAME,
    PLACEHOLDER_VERSION,
    RELEASE_TAG_GLOB,
    _source_repo_dir,
    get_version,
)

if TYPE_CHECKING:
    from pathlib import Path

GITHUB_URL = "https://github.com/cgt-calc/capital-gains-calculator"
GITHUB_COMMIT = "0ed8e395a60bd2ff31e7f8f2c0e58c71317b513d"

GIT_DESCRIBE_COMMAND = [
    "git",
    "describe",
    "--tags",
    "--always",
    "--dirty",
    "--match",
    RELEASE_TAG_GLOB,
]


class FakeGit:
    """Replacement for subprocess.run that replays a canned git outcome."""

    def __init__(
        self,
        stdout: str = "",
        returncode: int = 0,
        error: Exception | None = None,
    ) -> None:
        """Store the outcome to replay and prepare the call log."""
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(
        self, args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Record the call and replay the canned outcome."""
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def _install_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    version: str = PLACEHOLDER_VERSION,
    direct_url: bytes | None = None,
) -> None:
    """Make cgt-calc look installed into a fresh environment under tmp_path.

    The dist-info goes first on sys.path, so importlib.metadata finds it before
    the development install, and the package seems to sit beside it, outside
    any checkout. direct_url is the content of direct_url.json, if there is one.
    """
    site_packages = tmp_path / "site-packages"
    dist_info = site_packages / f"cgt_calc-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {DISTRIBUTION_NAME}\nVersion: {version}\n",
        encoding="utf-8",
    )
    if direct_url is not None:
        (dist_info / "direct_url.json").write_bytes(direct_url)
    monkeypatch.syspath_prepend(site_packages)
    monkeypatch.setattr(
        "cgt_calc.version.__file__", str(site_packages / "cgt_calc" / "version.py")
    )


def _patch_version_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git: FakeGit,
    metadata_version: str = PLACEHOLDER_VERSION,
    repo_dir: Path | None = None,
) -> None:
    """Point version resolution at the given metadata, checkout and git."""
    _install_metadata(monkeypatch, tmp_path, version=metadata_version)
    monkeypatch.setattr("cgt_calc.version._source_repo_dir", lambda: repo_dir)
    monkeypatch.setattr("cgt_calc.version.subprocess.run", git)


def _print_version(capsys: pytest.CaptureFixture[str]) -> str:
    """Run `cgt-calc --version` and return what it printed."""
    with pytest.raises(SystemExit) as exc_info:
        create_parser().parse_args(["--version"])
    assert exc_info.value.code == 0
    return capsys.readouterr().out


def _vcs_direct_url(vcs_info: object) -> bytes:
    """Return a direct_url.json for a GitHub install with the given vcs_info."""
    return json.dumps({"url": GITHUB_URL, "vcs_info": vcs_info}).encode()


def test_version_names_the_commit_of_a_github_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that a GitHub install prints the commit it was installed from.

    The metadata is what uv, pip and pipx record for an install from
    git+https://github.com/cgt-calc/capital-gains-calculator. Its version is
    the placeholder, so the installation docs promise 0.0.0 with g and the
    commit's first seven characters, 0ed8e39, as the local part.
    """
    _install_metadata(
        monkeypatch,
        tmp_path,
        direct_url=_vcs_direct_url({"commit_id": GITHUB_COMMIT, "vcs": "git"}),
    )

    assert _print_version(capsys) == "cgt-calc 0.0.0+g0ed8e39\n"


@pytest.mark.parametrize(
    "direct_url",
    [
        # Only installs from a direct URL get a direct_url.json (PEP 610).
        pytest.param(None, id="no-file"),
        # As uv, pip and pipx record a local directory. Wheel and archive
        # installs record archive_info instead, and also have no vcs_info.
        pytest.param(
            json.dumps({"dir_info": {}, "url": "file:///home/user/cgt"}).encode(),
            id="local-directory",
        ),
        # A GitHub install's file without its closing brace.
        pytest.param(
            _vcs_direct_url({"commit_id": GITHUB_COMMIT, "vcs": "git"})[:-1],
            id="cut-off",
        ),
        pytest.param(bytes([0xFF]), id="not-utf-8"),
        pytest.param(b"[]", id="not-an-object"),
        pytest.param(_vcs_direct_url("git"), id="vcs-info-not-an-object"),
        pytest.param(
            _vcs_direct_url({"commit_id": GITHUB_COMMIT, "vcs": "hg"}), id="not-git"
        ),
        pytest.param(
            _vcs_direct_url({"commit_id": 395, "vcs": "git"}),
            id="commit-not-a-string",
        ),
        pytest.param(
            _vcs_direct_url({"commit_id": "main", "vcs": "git"}),
            id="commit-not-a-hash",
        ),
    ],
)
def test_version_stays_placeholder_without_an_installed_git_commit(
    direct_url: bytes | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that an install without a usable git commit prints the placeholder."""
    _install_metadata(monkeypatch, tmp_path, direct_url=direct_url)

    assert _print_version(capsys) == "cgt-calc 0.0.0\n"


def test_stamped_version_is_returned_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that a stamped metadata version is used without consulting git."""
    git = FakeGit(stdout="v2.0.0-157-g3473d67")
    _patch_version_sources(
        monkeypatch, tmp_path, git, metadata_version="2.1.0", repo_dir=tmp_path
    )

    assert get_version() == "2.1.0"
    assert git.calls == []


@pytest.mark.parametrize(
    ("described", "expected"),
    [
        ("v2.0.0", "2.0.0"),
        ("v2.0.0-dirty", "2.0.0+dirty"),
        ("v2.0.0-157-g3473d67", "2.0.0.post157+g3473d67"),
        ("v2.0.0-157-g3473d67-dirty", "2.0.0.post157+g3473d67.dirty"),
        ("v2.1.0rc1", "2.1.0rc1"),
        ("3473d67", "0.0.0+g3473d67"),
        ("3473d67-dirty", "0.0.0+g3473d67.dirty"),
        # Git is asked for release tags only, but a tag naming no release must
        # never reach the version either way.
        ("backup/tts-prerebase6", "0.0.0"),
        ("backup/tts-prerebase6-dirty", "0.0.0+dirty"),
        ("backup/tts-prerebase6-5-g3473d67", "0.0.0+g3473d67"),
    ],
)
def test_placeholder_version_is_described_by_git(
    described: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that git describe output becomes a PEP 440 version."""
    git = FakeGit(stdout=f"{described}\n")
    _patch_version_sources(monkeypatch, tmp_path, git, repo_dir=tmp_path)

    version = get_version()

    assert version == expected
    assert git.calls == [GIT_DESCRIBE_COMMAND]
    try:
        Version(version)
    except InvalidVersion as err:  # pragma: no cover - only on a regression
        pytest.fail(f"{version} is not a valid version: {err}")


@pytest.mark.parametrize(
    "git",
    [
        pytest.param(FakeGit(returncode=128), id="git-failed"),
        pytest.param(FakeGit(stdout="  \n"), id="empty-output"),
        pytest.param(FakeGit(error=FileNotFoundError("git")), id="git-missing"),
        pytest.param(FakeGit(error=PermissionError("cwd")), id="unreadable-cwd"),
        pytest.param(
            FakeGit(error=subprocess.TimeoutExpired(GIT_DESCRIBE_COMMAND, 5)),
            id="timed-out",
        ),
    ],
)
def test_placeholder_version_survives_git_failures(
    git: FakeGit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the placeholder stands when git cannot describe the checkout."""
    _patch_version_sources(monkeypatch, tmp_path, git, repo_dir=tmp_path)

    assert get_version() == PLACEHOLDER_VERSION


def test_placeholder_version_outside_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that git is not run at all when there is no checkout around."""
    git = FakeGit(stdout="v2.0.0-157-g3473d67")
    _patch_version_sources(monkeypatch, tmp_path, git, repo_dir=None)

    assert get_version() == PLACEHOLDER_VERSION
    assert git.calls == []


def test_source_repo_dir_requires_a_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that the package directory counts only inside a git checkout."""
    package_dir = tmp_path / "cgt_calc"
    package_dir.mkdir()
    monkeypatch.setattr("cgt_calc.version.__file__", str(package_dir / "version.py"))

    assert _source_repo_dir() is None

    # Worktrees keep a .git file rather than a directory.
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert _source_repo_dir() == package_dir
