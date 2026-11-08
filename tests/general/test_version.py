"""Tests for the version a build of cgt-calc gets and the version it reports."""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from cgt_calc.args_parser import create_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
# The project files that decide which version a build gets.
VERSION_SETTINGS = ("pyproject.toml", ".gitattributes", ".git_archival.txt")


def _git(repo: Path, *args: str) -> str:
    """Run git in repo as a fixed author, without signing, and return its output."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=cgt-calc tests",
            "-c",
            "user.email=tests@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _project_copy(path: Path) -> Path:
    """Copy cgt-calc's version settings to path, without any Git history."""
    path.mkdir()
    for name in VERSION_SETTINGS:
        shutil.copy(REPO_ROOT / name, path / name)
    return path


def _released_repo(tmp_path: Path) -> Path:
    """Return a repository with cgt-calc's version settings, released as v3.0.0."""
    repo = _project_copy(tmp_path / "repo")
    _git(repo, "init", "--quiet")
    _git(repo, "add", "--all")
    _git(repo, "commit", "--quiet", "--message", "Release")
    _git(repo, "tag", "v3.0.0")
    return repo


def _commit(repo: Path, message: str) -> str:
    """Add an empty commit and return its abbreviated hash, as git describe names it."""
    _git(repo, "commit", "--quiet", "--allow-empty", "--message", message)
    return _git(repo, "rev-parse", "--short", "HEAD")


def _build_version(
    project: Path, given: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Ask the build backend for the version a build of project gets.

    Any version override in the environment is dropped, so the result does
    not depend on how the tests are run; given sets the one Docker uses.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("SETUPTOOLS_SCM_", "VCS_VERSIONING_"))
    }
    if given is not None:
        env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CGT_CALC"] = given
    return subprocess.run(
        [sys.executable, "-m", "hatchling", "version"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _built_version(project: Path) -> str:
    """Return the version a build of project gets, failing on a build error."""
    result = _build_version(project)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_release_tag_builds_as_its_release(tmp_path: Path) -> None:
    """Test that the tagged commit builds as exactly the tagged release."""
    repo = _released_repo(tmp_path)

    assert _built_version(repo) == "3.0.0"


def test_commits_after_a_release_build_as_post_releases(tmp_path: Path) -> None:
    """Test that commits after a release build as post-releases of it.

    Two commits after v3.0.0 make 3.0.0.post2, and the local part names the
    commit, as Docker edge images and checkouts have been versioned. The
    default .dev scheme would make 3.0.1.dev2 instead.
    """
    repo = _released_repo(tmp_path)
    _commit(repo, "First change")
    commit = _commit(repo, "Second change")

    assert _built_version(repo) == f"3.0.0.post2+g{commit}"


def test_tags_that_name_no_release_are_passed_over(tmp_path: Path) -> None:
    """Test that a later tag other than a release tag does not set the version.

    A backup tag such as a rebase leaves behind has digits in it, so Git's
    default describe would pick it over v3.0.0.
    """
    repo = _released_repo(tmp_path)
    commit = _commit(repo, "Change")
    _git(repo, "tag", "backup/attempt9")

    assert _built_version(repo) == f"3.0.0.post1+g{commit}"


def test_a_source_archive_builds_as_its_commit(tmp_path: Path) -> None:
    """Test that a git archive, such as a GitHub download, keeps the version.

    The archive has no Git history, only the commit and release that git
    archive writes into .git_archival.txt. The backup tag checks that this
    file also names only release tags.
    """
    repo = _released_repo(tmp_path)
    _commit(repo, "First change")
    commit = _commit(repo, "Second change")
    _git(repo, "tag", "backup/attempt9")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    extracted = tmp_path / "archive"
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(extracted, filter="data")

    assert _built_version(extracted) == f"3.0.0.post2+g{commit}"


def test_a_build_with_no_version_source_fails(tmp_path: Path) -> None:
    """Test that a build that cannot find its version stops.

    A copy of the files without Git history or a filled-in archive file has
    nothing to read the version from. It must not build as 0.0.0.
    """
    project = _project_copy(tmp_path / "copy")

    result = _build_version(project)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "unable to detect version" in result.stderr


def test_a_build_without_git_uses_the_version_it_is_given(tmp_path: Path) -> None:
    """Test that the version Docker passes in becomes the build's version."""
    project = _project_copy(tmp_path / "copy")

    result = _build_version(project, given="3.0.0.post13+g327ebae")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3.0.0.post13+g327ebae"


def test_version_prints_the_installed_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that --version prints the version the package was installed with."""
    site_packages = tmp_path / "site-packages"
    dist_info = site_packages / "cgt_calc-3.0.0.post13+g327ebae.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: cgt-calc\nVersion: 3.0.0.post13+g327ebae\n",
        encoding="utf-8",
    )
    # First on sys.path, so it is found before the development install.
    monkeypatch.syspath_prepend(site_packages)

    with pytest.raises(SystemExit) as exc_info:
        create_parser().parse_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == "cgt-calc 3.0.0.post13+g327ebae\n"
