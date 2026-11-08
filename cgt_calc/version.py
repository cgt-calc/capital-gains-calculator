"""Resolve the version of the running package."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import re
import subprocess

# Version kept in pyproject.toml for unreleased source trees. Releases and
# Docker images overwrite it with `uv version <tag>` at build time, so seeing it
# at run time means the package was installed straight from a checkout or a
# git URL.
PLACEHOLDER_VERSION = "0.0.0"

GIT_DESCRIBE_TIMEOUT_SECONDS = 5

# Releases are tagged v<version>. Describing against anything else, such as a
# backup tag left behind by a rebase, would name a version that does not exist.
RELEASE_TAG_GLOB = "v[0-9]*"

DISTRIBUTION_NAME = "cgt-calc"

# Commits since the latest reachable tag, e.g. "v2.0.0-157-g3473d67".
COMMITS_SINCE_TAG_RE = re.compile(
    r"^(?P<tag>.+)-(?P<distance>\d+)-g(?P<commit>[0-9a-f]+)$"
)
# Abbreviated commit hash, which is all `--always` can print without any tags.
COMMIT_ONLY_RE = re.compile(r"^[0-9a-f]{7,40}$")
# The version a release tag names, with the v stripped off.
RELEASE_TAG_RE = re.compile(r"^v?(?P<release>\d[^\s]*)$")

# Where installers record the URL a package came from (PEP 610).
DIRECT_URL_FILE = "direct_url.json"
# The length git describe gives commits in a fresh clone of this repository,
# so a GitHub install names its commit the way a checkout or Docker edge does.
INSTALLED_COMMIT_LENGTH = 7


def get_version() -> str:
    """Return the version of the running package.

    Released and Docker builds carry a stamped version in their metadata. A
    source checkout or a GitHub install carries the placeholder instead, so
    name the commit it came from to pin down the exact code.
    """
    version = importlib.metadata.version(DISTRIBUTION_NAME)
    if version != PLACEHOLDER_VERSION:
        return version
    return _describe_source_tree() or _describe_git_install() or PLACEHOLDER_VERSION


def _describe_git_install() -> str | None:
    """Return a PEP 440 version naming the commit of a git URL install, if any.

    pip, pipx and uv record the commit they installed from a git URL in the
    distribution's `direct_url.json`. Any other install, or a file that does not
    hold a git commit, leaves the placeholder standing.
    """
    distribution = importlib.metadata.distribution(DISTRIBUTION_NAME)
    try:
        text = distribution.read_text(DIRECT_URL_FILE)
        direct_url = None if text is None else json.loads(text)
    except ValueError:
        # A damaged file: not UTF-8, or not JSON.
        return None
    if not isinstance(direct_url, dict):
        # No file, or JSON of another shape.
        return None
    vcs_info = direct_url.get("vcs_info")
    # Directory and archive installs record dir_info or archive_info instead.
    if not isinstance(vcs_info, dict) or vcs_info.get("vcs") != "git":
        return None
    commit = vcs_info.get("commit_id")
    if not isinstance(commit, str) or not COMMIT_ONLY_RE.fullmatch(commit):
        return None
    return f"{PLACEHOLDER_VERSION}+g{commit[:INSTALLED_COMMIT_LENGTH]}"


def _describe_source_tree() -> str | None:
    """Return a PEP 440 version describing the checkout, if there is one."""
    package_dir = _source_repo_dir()
    if package_dir is None:
        return None
    described = _run_git_describe(package_dir)
    if described is None:
        return None
    return _to_pep440(described)


def _source_repo_dir() -> Path | None:
    """Return the package directory when it sits inside a git checkout.

    Without this check, a non-editable install below an unrelated repository
    would end up reporting that repository's version.
    """
    package_dir = Path(__file__).resolve().parent
    # A worktree has a .git file rather than a directory, hence exists().
    if not (package_dir.parent / ".git").exists():
        return None
    return package_dir


def _run_git_describe(package_dir: Path) -> str | None:
    """Describe the checkout with git, or return None if that is not possible."""
    try:
        result = subprocess.run(
            [
                "git",
                "describe",
                "--tags",
                "--always",
                "--dirty",
                "--match",
                RELEASE_TAG_GLOB,
            ],
            cwd=package_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_DESCRIBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        # No git binary, an unreadable directory, or a hung call.
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _to_pep440(described: str) -> str:
    """Convert `git describe` output to a PEP 440 version.

    This follows the same scheme as the Docker workflow, which stamps edge
    images from a describe of its own, plus the dirty marker that only a
    working checkout can produce. Any tag git offers that does not name a
    release is discarded rather than passed on as a version.
    """
    dirty = described.endswith("-dirty")
    if dirty:
        described = described.removesuffix("-dirty")

    # Without a release to count from, the commit alone identifies the build.
    release = PLACEHOLDER_VERSION
    local_parts = []
    match = COMMITS_SINCE_TAG_RE.match(described)
    if match:
        tagged_release = _release_from_tag(match["tag"])
        if tagged_release is not None:
            release = f"{tagged_release}.post{match['distance']}"
        local_parts.append(f"g{match['commit']}")
    elif COMMIT_ONLY_RE.match(described):
        local_parts.append(f"g{described}")
    else:
        release = _release_from_tag(described) or PLACEHOLDER_VERSION

    if dirty:
        local_parts.append("dirty")
    if not local_parts:
        return release
    return f"{release}+{'.'.join(local_parts)}"


def _release_from_tag(tag: str) -> str | None:
    """Return the version a release tag names, or None for any other tag.

    Git is asked for release tags only, so this guards against the tag glob
    and this module drifting apart.
    """
    match = RELEASE_TAG_RE.match(tag)
    if match is None:
        return None
    return match["release"]
