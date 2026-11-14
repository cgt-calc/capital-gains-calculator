"""Shared test configuration."""

from collections.abc import Iterator
import os
from pathlib import Path
import sys

import pytest

# The shared helpers in tests/utils.py assert on byte-exact output, and only
# rewritten asserts print the diff that says what changed.
pytest.register_assert_rewrite("tests.utils")

# Force plain CLI output regardless of the environment running the tests:
# expected-output fixtures are byte-exact and stderr assertions match on the
# "WARNING:"/"ERROR:" prefixes, so colour and emoji must never leak in.
os.environ["NO_COLOR"] = "1"
os.environ.pop("FORCE_COLOR", None)


@pytest.fixture(autouse=True)
def _no_leaked_file_descriptors() -> Iterator[None]:
    """Fail a test that leaves a file, pipe, socket or thread's event loop open.

    One pytest worker runs thousands of tests, so a few descriptors left by
    each soon reach 1024, and select() cannot watch a descriptor numbered 1024
    or more. Windows has no /dev/fd.
    """
    if sys.platform == "win32":
        yield
        return
    before = len(list(Path("/dev/fd").iterdir()))
    yield
    if (leaked := len(list(Path("/dev/fd").iterdir())) - before) > 0:
        pytest.fail(f"The test left {leaked} file descriptor(s) open.", pytrace=False)
