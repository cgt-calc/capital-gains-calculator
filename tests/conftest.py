"""Shared test configuration."""

import os

import pytest

# The shared helpers in tests/utils.py assert on byte-exact output, and only
# rewritten asserts print the diff that says what changed.
pytest.register_assert_rewrite("tests.utils")

# Force plain CLI output regardless of the environment running the tests:
# expected-output fixtures are byte-exact and stderr assertions match on the
# "WARNING:"/"ERROR:" prefixes, so colour and emoji must never leak in.
os.environ["NO_COLOR"] = "1"
os.environ.pop("FORCE_COLOR", None)
