"""Tests for the form description built from the command line parser."""

from __future__ import annotations

import argparse

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.web.forms import (
    MANAGED_DESTS,
    Field,
    FieldKind,
    UnsupportedOptionError,
    build_sections,
    flatten,
)


@pytest.fixture(scope="module")
def fields() -> dict[str, Field]:
    """Every offered field by its option name."""
    return {field.flag: field for field in flatten(build_sections(strict=True))}


@pytest.mark.parametrize(
    ("flag", "kind"),
    [
        ("--year", FieldKind.YEAR),
        ("--from", FieldKind.DATE),
        ("--trading212-dir", FieldKind.FILES),
        ("--raw-file", FieldKind.FILE),
        ("--initial-prices-file", FieldKind.FILE),
        ("--exchange-rates-file", FieldKind.FILE),
        ("--no-balance-check", FieldKind.FLAG),
        ("--unrealized-gains", FieldKind.FLAG),
        ("--interest-fund-tickers", FieldKind.TEXT),
        ("--dump-transactions", FieldKind.OUTPUT_FILE),
        ("--verbose", FieldKind.FLAG),
    ],
)
def test_option_kind(fields: dict[str, Field], flag: str, kind: FieldKind) -> None:
    """Each validator maps to the matching input."""
    assert fields[flag].kind is kind


@pytest.mark.parametrize(
    "flag",
    [
        "--output",
        "--help",
        "--version",
        "--print-completion",
        # Deprecated spellings of options that are offered under a new name.
        "--raw",
        "--schwab",
        "--report",
    ],
)
def test_hidden_options(fields: dict[str, Field], flag: str) -> None:
    """Options the interface manages or that are deprecated are not offered."""
    assert flag not in fields


def test_every_visible_option_is_offered() -> None:
    """A new option in the parser must show up in the form, not vanish."""
    parser = create_parser()
    offered = {field.dest for field in flatten(build_sections(parser, strict=True))}
    expected = {
        action.dest
        for action in parser._actions  # noqa: SLF001
        if action.help != argparse.SUPPRESS
        and action.dest
        not in MANAGED_DESTS | {"help", "print_completion", argparse.SUPPRESS}
    }
    assert expected <= offered


def test_dests_are_unique() -> None:
    """The form field name is the option name, so it cannot repeat."""
    dests = [field.dest for field in flatten(build_sections(strict=True))]
    assert len(dests) == len(set(dests))


def test_help_placeholders_are_expanded(fields: dict[str, Field]) -> None:
    """The text shown is what --help shows, with defaults filled in."""
    assert "%(" not in "".join(field.help for field in fields.values())
    assert "out/exchange_rates.csv" in fields["--exchange-rates-file"].help


def test_sections_follow_argument_groups() -> None:
    """Titles come from the parser, so the form reads like --help."""
    titles = [section.title for section in build_sections(strict=True)]
    assert titles[0] == "Tax year"
    assert "Broker inputs" in titles


def test_directory_name(fields: dict[str, Field]) -> None:
    """A directory option's uploads land in a folder named after the broker."""
    assert fields["--trading212-dir"].directory_name == "trading212"


def test_unknown_option_shape_is_rejected() -> None:
    """An option the form cannot express fails loudly, not silently."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--many", nargs="+", type=str)
    with pytest.raises(UnsupportedOptionError, match="--many"):
        build_sections(parser, strict=True)


def test_unknown_option_shape_is_skipped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Outside the tests, the interface still starts and says what it left out."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--many", nargs="+", type=str)
    parser.add_argument("--verbose", action="store_true")
    fields = flatten(build_sections(parser))
    assert [f.flag for f in fields] == ["--verbose"]
    assert "--many" in caplog.text


@pytest.mark.parametrize("default", [argparse.SUPPRESS, None])
def test_completion_option_from_shtab_is_hidden(default: str | None) -> None:
    """Shtab's --print-completion offers shells and prints a script, whatever its default."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--print-completion",
        choices=["bash", "zsh", "tcsh"],
        default=default,
        help="print shell completion script",
    )
    parser.add_argument("--verbose", action="store_true")
    fields = flatten(build_sections(parser, strict=True))
    assert [f.flag for f in fields] == ["--verbose"]
