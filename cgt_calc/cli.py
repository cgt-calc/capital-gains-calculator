"""Capital Gain Calculator command line interface."""

from __future__ import annotations

import decimal
import logging
import sys
from typing import TYPE_CHECKING

from colorama import Fore

from . import render_latex
from .args_parser import (
    create_parser,
    reject_duplicate_stdin,
    reject_schwab_file_and_dir,
    resolve_reporting_period,
)
from .currency_converter import CurrencyConverter
from .current_price_fetcher import CurrentPriceFetcher
from .exceptions import CgtError, TransactionDumpError
from .initial_prices import InitialPrices
from .isin_converter import IsinConverter
from .logging import setup_logging, style_text
from .parsers.broker_registry import BrokerRegistry
from .spin_off_handler import SpinOffHandler
from .transaction_dumper import dump_transactions

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from .model import BrokerTransaction

LOGGER = logging.getLogger(__name__)


def _paths_written_after_dump(args: argparse.Namespace) -> Iterator[tuple[Path, str]]:
    """Yield each file the rest of this run may write, and what it is.

    The dump is saved before anything else, so a destination naming one of
    these would be silently replaced by the time the run finished. The report
    names follow render_latex.render_pdf; pdflatex leaves its own log and aux
    file next to the report and removes them again afterwards.
    """
    if not args.no_report:
        directory = args.output.parent
        jobname = args.output.stem
        yield args.output, "the report from --output"
        yield directory / f"{jobname}.tex", "the LaTeX source from --output"
        if not args.no_pdflatex:
            # pdflatex names its output after the report's stem, so an
            # --output carrying any other suffix, or none, still lands here.
            yield directory / f"{jobname}.pdf", "the PDF report from --output"
            for suffix in (".latex.log", ".log", ".aux"):
                yield (
                    directory / f"{jobname}{suffix}",
                    "a pdflatex working file from --output",
                )
    for cache, option in (
        (args.exchange_rates_file, "--exchange-rates-file"),
        (args.isin_translation_file, "--isin-translation-file"),
        (args.spin_offs_file, "--spin-offs-file"),
    ):
        if cache is not None:
            yield cache, f"the cache from {option}"


def _save_transaction_dump(
    args: argparse.Namespace, transactions: Sequence[BrokerTransaction]
) -> None:
    """Write the parsed transactions to the new CSV file the user asked for."""
    path: Path = args.dump_transactions
    destination = path.resolve()
    for other, description in _paths_written_after_dump(args):
        if other.resolve() == destination:
            raise TransactionDumpError(
                f"--dump-transactions path '{path}' is also {description}, "
                "which this run writes after saving the dump. Choose a "
                "different filename."
            )
    if not path.parent.is_dir():
        raise TransactionDumpError(
            f"--dump-transactions directory '{path.parent}' does not exist. "
            "Create it, or choose a path in a directory that does."
        )
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            dump_transactions(transactions, stream)
    except FileExistsError as err:
        raise TransactionDumpError(
            f"--dump-transactions file '{path}' already exists. Choose a new "
            "filename, or move the existing file out of the way."
        ) from err
    except OSError as err:
        raise TransactionDumpError(
            f"Could not write parsed transactions to '{path}': {err}. A partly "
            "written file may be left behind; remove it or choose another "
            "filename before retrying."
        ) from err
    count = len(transactions)
    noun = "transaction" if count == 1 else "transactions"
    LOGGER.info(
        style_text(
            f"Saved {count} parsed {noun} to {path}",
            colour=Fore.CYAN,
            emoji="💾",
            stream=sys.stderr,
        )
    )


def calculate_cgt(args: argparse.Namespace) -> None:
    """Perform all the computations."""

    # Imported here because cgt_calc.main re-exports the entry points below,
    # so a module-level import either way round would be circular.
    from .main import CapitalGainsCalculator  # noqa: PLC0415

    isin_translation_file = args.isin_translation_file

    # Read data from input files
    isin_converter = IsinConverter(isin_translation_file)
    broker_transactions = BrokerRegistry.load_all_transactions(args, isin_converter)

    if args.dump_transactions is not None:
        _save_transaction_dump(args, broker_transactions)

    currency_converter = CurrencyConverter.create(args.exchange_rates_file)
    price_fetcher = CurrentPriceFetcher(currency_converter)
    initial_prices = InitialPrices(args.initial_prices_file)
    spin_off_handler = SpinOffHandler(args.spin_offs_file)

    calculator = CapitalGainsCalculator(
        args.year,
        currency_converter,
        isin_converter,
        price_fetcher,
        spin_off_handler,
        initial_prices,
        args.interest_fund_tickers,
        cgt_exempt_tickers=args.cgt_exempt_tickers,
        balance_check=args.balance_check,
        autoconvert_currency=args.autoconvert_currency,
        calc_unrealized_gains=args.calc_unrealized_gains,
        period_start=args.period_from,
        period_end=args.period_to,
    )
    # First pass converts broker transactions to HMRC transactions,
    # collapsing all transactions with the same type within the same day.
    # It also converts prices to GBP, validates the data, plans share
    # reorganisations, and records dividends, taxes on dividends and interest
    # for the second pass to process.
    # Second pass calculates capital gain tax for the given tax year.
    report = calculator.calculate(broker_transactions)
    # The report string is newline-terminated already; avoid a trailing
    # blank line so piped output stays stable under newline normalisation.
    print(report, end="")

    # Generate PDF report.
    if not args.no_report:
        render_latex.render_pdf(
            report,
            output_path=args.output,
            skip_pdflatex=args.no_pdflatex,
        )
    done_msg = (
        "Done! Calculations complete (PDF generation skipped)."
        if args.no_pdflatex or args.no_report
        else "Done! Report generated successfully."
    )
    LOGGER.info(style_text(done_msg, colour=Fore.GREEN, emoji="🎉", stream=sys.stderr))


def main() -> int:
    """Run main function."""

    # Enable colourised logging.
    setup_logging()

    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True

    parser = create_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    reject_duplicate_stdin(parser, args)
    reject_schwab_file_and_dir(parser, args)
    resolve_reporting_period(parser, args)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        calculate_cgt(args)
    except CgtError as err:
        if args.verbose:
            LOGGER.exception("Exception:")
        else:
            # Print error without traceback
            LOGGER.error("%s", err)
        return 1
    except Exception:
        # Last-resort catch for unexpected exceptions
        LOGGER.critical("Unexpected error!")
        LOGGER.exception("Details:")
        return 1

    return 0


def init() -> None:
    """Entry point."""
    sys.exit(main())


if __name__ == "__main__":
    init()
