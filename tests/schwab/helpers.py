"""Helpers for the Schwab tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cgt_calc.args_parser import create_parser
from cgt_calc.parsers.schwab import SchwabParser

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction


def load_via_cli(**flags: str) -> list[BrokerTransaction]:
    """Load Schwab input through the CLI wiring rather than the parser directly.

    ``load_from_args`` registers ``--schwab-dir``, classifies the award file
    and fills in ``awards_prices``, so bypassing it would test none of that.
    Each keyword is a flag: ``schwab_award_file=path`` is
    ``--schwab-award-file path``.
    """
    argv = ["--year", "2023"]
    for flag, value in flags.items():
        argv += [f"--{flag.replace('_', '-')}", value]
    return SchwabParser.load_from_args(create_parser().parse_args(argv))
