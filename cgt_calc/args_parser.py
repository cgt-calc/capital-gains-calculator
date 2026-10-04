"""Parse command line arguments."""

from __future__ import annotations

import argparse
import datetime

import shtab

from .args_validators import (
    STDIN_PATH,
    DeprecatedAction,
    VersionAction,
    date_type,
    existing_file_or_stdin_type,
    existing_file_type,
    income_type,
    optional_cache_file_type,
    output_path_type,
    set_completer,
    ticker_list_type,
    year_type,
)
from .const import (
    DEFAULT_EXCHANGE_RATES_FILE,
    DEFAULT_ISIN_TRANSLATION_FILE,
    DEFAULT_REPORT_PATH,
    DEFAULT_SPIN_OFF_FILE,
    EARLIEST_TAX_YEAR,
)
from .dates import get_tax_year_end, get_tax_year_for_date, get_tax_year_start
from .parsers.broker_registry import BrokerRegistry


def get_last_elapsed_tax_year() -> int:
    """Get last ended tax year."""
    now = datetime.datetime.now()
    if now.date() >= datetime.date(now.year, 4, 6):
        return now.year - 1
    return now.year - 2


def create_parser() -> argparse.ArgumentParser:
    """Create ArgumentParser."""
    parser = argparse.ArgumentParser(
        description="Calculate UK capital gains from broker transactions and generate a PDF report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        allow_abbrev=False,
        epilog="""
Environment variables:
  NO_COLOR              disable coloured output
  FORCE_COLOR           use colours even when output is not a terminal
  NO_EMOJI              keep colours but drop emoji

Documentation: https://cgt-calc.uk/
""",
    )

    # Tax Year
    year_group = parser.add_argument_group("Tax year")
    year_group.add_argument(
        "--year",
        type=year_type,
        metavar="YYYY",
        default=None,
        help="first year of the UK tax year to report, e.g. 2024 for 2024/25 "
        f"(default: {get_last_elapsed_tax_year()}, the last completed tax year)",
    )
    year_group.add_argument(
        "--from",
        dest="period_from",
        type=date_type,
        metavar="YYYY-MM-DD",
        help="first day of a period within one tax year, to report only part of "
        "it, e.g. from the rate change on 2024-10-30 (requires --to, incompatible "
        "with --year)",
    )
    year_group.add_argument(
        "--to",
        dest="period_to",
        type=date_type,
        metavar="YYYY-MM-DD",
        help="last day of the period to report (requires --from)",
    )
    year_group.add_argument(
        "--income",
        type=income_type,
        metavar="POUNDS",
        help="your income for the tax year before the Personal Allowance, e.g. the "
        "pay on your P60, without the interest and, from 2016/17, the dividends "
        "in the files you supply; gives one Capital Gains Tax estimate instead of "
        "two (incompatible with --from and --to)",
    )

    # Broker Inputs
    broker_group = parser.add_argument_group("Broker inputs")
    BrokerRegistry.register_all_arguments(broker_group)

    # Additional Data Files
    data_group = parser.add_argument_group("Additional data files")
    set_completer(
        data_group.add_argument(
            "--prices-file",
            type=existing_file_type,
            metavar="PATH",
            help="share prices in CSV format, for vests without a price and for "
            "spin-offs; used instead of the bundled prices",
        ),
        shtab.FILE,
    )
    data_group.add_argument(
        "--initial-prices-file",
        "--initial-prices",
        action=DeprecatedAction,
        dest="prices_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    set_completer(
        data_group.add_argument(
            "--exchange-rates-file",
            type=optional_cache_file_type,
            metavar="PATH",
            default=DEFAULT_EXCHANGE_RATES_FILE,
            help="cache of HMRC monthly exchange rates in CSV format, downloaded "
            "as needed (default: %(default)s)",
        ),
        shtab.FILE,
    )
    set_completer(
        data_group.add_argument(
            "--isin-translation-file",
            type=optional_cache_file_type,
            default=DEFAULT_ISIN_TRANSLATION_FILE,
            metavar="PATH",
            help="cache of ISIN to ticker lookups in CSV format, updated as needed "
            "(default: %(default)s)",
        ),
        shtab.FILE,
    )
    set_completer(
        data_group.add_argument(
            "--spin-offs-file",
            type=optional_cache_file_type,
            metavar="PATH",
            default=DEFAULT_SPIN_OFF_FILE,
            help="old ticker each spin-off came from, in CSV format; an interactive "
            "run asks for a missing one and saves the answer here "
            "(default: %(default)s)",
        ),
        shtab.FILE,
    )

    # Calculation Options
    calc_group = parser.add_argument_group("Calculation options")
    calc_group.add_argument(
        "--no-balance-check",
        dest="balance_check",
        action="store_false",
        default=True,
        help="do not stop when a broker's cash balance falls below zero, which "
        "usually means transactions are missing from the export",
    )
    calc_group.add_argument(
        "--autoconvert-currency",
        action="store_true",
        default=False,
        help=(
            "accept the same dividend, or a dividend and its withholding tax, "
            "reported in GBP and in one foreign currency, converting the foreign "
            "amounts to GBP; two different foreign currencies are still an error"
        ),
    )
    calc_group.add_argument(
        "--unrealized-gains",
        dest="calc_unrealized_gains",
        action="store_true",
        default=False,
        help="estimate the gain or loss on the holdings in the report's ending "
        "portfolio if sold at today's price, fetched from Yahoo Finance",
    )
    calc_group.add_argument(
        "--interest-fund-tickers",
        type=ticker_list_type,
        metavar="TICKER[,TICKER...]",
        default=[],
        help="tickers of offshore bond funds, including ETFs, whose income is "
        "reported as foreign interest rather than dividends",
    )
    calc_group.add_argument(
        "--cgt-exempt-tickers",
        type=ticker_list_type,
        metavar="TICKER[,TICKER...]",
        default=[],
        help="advanced: treat disposals of these tickers as exempt from Capital "
        "Gains Tax (TCGA 1992 s115), disregarding both gains and losses; "
        "cgt-calc cannot check the exemption; not for a qualifying corporate "
        "bond carrying a deferred gain or for a deeply discounted security; "
        "coupon and accrued interest stay taxable",
    )

    # Output Options
    output_group = parser.add_argument_group("Output")

    output_mutex = output_group.add_mutually_exclusive_group()
    set_completer(
        output_mutex.add_argument(
            "-o",
            "--output",
            type=output_path_type,
            metavar="PATH",
            default=DEFAULT_REPORT_PATH,
            help="where to save the PDF report (default: %(default)s)",
        ),
        shtab.FILE,
    )
    output_mutex.add_argument(
        "--report",
        action=DeprecatedAction,
        dest="output",
        type=output_path_type,
        default=DEFAULT_REPORT_PATH,
        help=argparse.SUPPRESS,
    )
    output_mutex.add_argument(
        "--no-report",
        action="store_true",
        help="print the summary only, without creating a PDF or LaTeX source",
    )
    output_group.add_argument(
        "--no-pdflatex",
        action="store_true",
        help="save the report's LaTeX source instead of creating a PDF",
    )
    set_completer(
        output_group.add_argument(
            "--dump-transactions",
            type=output_path_type,
            metavar="PATH",
            default=None,
            help="write parsed transactions as CSV to a new file, then continue calculating",
        ),
        shtab.FILE,
    )

    # General Options
    general_group = parser.add_argument_group("General")
    general_group.add_argument(
        "-h",
        "--help",
        action="help",
        help="show this help message and exit",
    )
    general_group.add_argument(
        "--version",
        action=VersionAction,
        nargs=0,
        dest=argparse.SUPPRESS,
        default=argparse.SUPPRESS,
        help="show version and exit",
    )
    shtab.add_argument_to(
        general_group,
        "--print-completion",
        help="print a tab completion script for the given shell and exit",
    )
    general_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show debug messages, and a full traceback if the calculation fails",
    )
    return parser


def reject_duplicate_stdin(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Reject the stdin marker passed to more than one option.

    Only one option can consume stdin. Whichever parser runs second sees an
    exhausted stream and fails with an error naming the wrong file, or reports
    no transactions at all.
    """
    # existing_file_or_stdin_type is exactly the set of options wired to a stdin
    # consumer, so the parser itself is the source of truth here.
    options: dict[str, str] = {}
    for action in parser._actions:  # noqa: SLF001
        if action.type is not existing_file_or_stdin_type:
            continue
        if getattr(args, action.dest, None) != STDIN_PATH:
            continue
        # Deprecated aliases share a dest, so keep the canonical flag only.
        options.setdefault(action.dest, action.option_strings[0])
    if len(options) > 1:
        named = ", ".join(sorted(options.values()))
        parser.error(
            "'-' reads from stdin and can only be given to one option, "
            f"but was given to {named}"
        )


def reject_schwab_file_and_dir(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Reject ``--schwab-file`` and ``--schwab-dir`` given together.

    Schwab is the only broker offering both, because ``--schwab-file`` predates the
    directory support and has to keep working. Loading both would mean merging
    two sources whose overlap cannot be detected: a Schwab CSV has no
    transaction id, so a row present in each is indistinguishable from a
    genuine repeat of the same trade.
    """
    if args.schwab_file and args.schwab_dir:
        parser.error(
            "--schwab-file and --schwab-dir cannot be used together. Pass the "
            "directory holding every export, or the single file."
        )


def resolve_reporting_period(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Validate --year/--from/--to and resolve the effective tax year.

    Sets args.year from the custom period when --from/--to are used,
    or to the last elapsed tax year when nothing is specified.
    """
    if args.period_from is None and args.period_to is None:
        if args.year is None:
            args.year = get_last_elapsed_tax_year()
        return
    if args.period_from is None or args.period_to is None:
        parser.error("--from and --to must be used together")
    if args.income is not None:
        parser.error(
            "--income cannot be combined with --from/--to: the tax is estimated for "
            "a full tax year"
        )
    if args.year is not None:
        parser.error("--year cannot be combined with --from/--to")
    if args.period_from > args.period_to:
        parser.error("--from must not be after --to")
    tax_year = get_tax_year_for_date(args.period_from)
    if tax_year < EARLIEST_TAX_YEAR:
        parser.error(
            f"--from must not be before {get_tax_year_start(EARLIEST_TAX_YEAR)}, "
            "the start of the earliest tax year cgt-calc supports"
        )
    if tax_year != get_tax_year_for_date(args.period_to):
        parser.error(
            "--from and --to must be within the same UK tax year, "
            f"e.g. {get_tax_year_start(tax_year)} to {get_tax_year_end(tax_year)}"
        )
    args.year = tax_year
