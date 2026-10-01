"""Tests for building the command line from form values."""

from __future__ import annotations

import pytest

from cgt_calc.web.command import build_command
from cgt_calc.web.forms import build_sections

SECTIONS = build_sections(strict=True)


def test_command_follows_the_form_order() -> None:
    """Options come out in the order the parser declares them."""
    command = build_command(
        SECTIONS,
        text={"year": "2024"},
        checked={"verbose", "no_pdflatex"},
        uploaded={"raw_file": "raw.csv", "trading212_dir": "trading212"},
    )
    assert command.argv == (
        "--year",
        "2024",
        "--trading212-dir",
        "trading212",
        "--raw-file",
        "raw.csv",
        "--no-pdflatex",
        "--verbose",
    )
    assert command.display == (
        "cgt-calc --year 2024 --trading212-dir trading212 --raw-file raw.csv "
        "--no-pdflatex --verbose"
    )


def test_blank_and_unknown_values_are_left_out() -> None:
    """Empty text boxes add nothing, and only known options are used."""
    command = build_command(
        SECTIONS,
        text={"year": "  ", "period_from": "", "unknown": "x"},
        checked={"unknown"},
        uploaded={},
    )
    assert command.argv == ()


def test_text_starting_with_dash_cannot_add_options() -> None:
    """A value like ``--output`` stays the value of its own option."""
    command = build_command(
        SECTIONS,
        text={"interest_fund_tickers": "--output=/tmp/x"},
        checked=set(),
        uploaded={},
    )
    assert command.argv == ("--interest-fund-tickers=--output=/tmp/x",)


def test_output_option_gets_a_path_inside_the_run() -> None:
    """The person only switches the dump on; the path is chosen for them."""
    command = build_command(
        SECTIONS, text={}, checked={"dump_transactions"}, uploaded={}
    )
    assert command.argv == ("--dump-transactions", "out/dump-transactions.csv")


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("plain.csv", "plain.csv"),
        ("my file.csv", "'my file.csv'"),
        ("it's.csv", "'it'\"'\"'s.csv'"),
    ],
)
def test_display_quotes_like_a_shell(name: str, shown: str) -> None:
    """The shown command can be pasted into a terminal as it is."""
    command = build_command(
        SECTIONS, text={}, checked=set(), uploaded={"raw_file": name}
    )
    assert command.display == f"cgt-calc --raw-file {shown}"
