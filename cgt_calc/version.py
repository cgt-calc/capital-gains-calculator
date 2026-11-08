"""Report the version of the running package."""

from __future__ import annotations

import importlib.metadata

DISTRIBUTION_NAME = "cgt-calc"


def get_version() -> str:
    """Return the version the package was built with.

    The build takes it from the latest release tag, as configured under
    [tool.hatch.version] in pyproject.toml.
    """
    return importlib.metadata.version(DISTRIBUTION_NAME)
