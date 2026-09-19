"""Constants."""

from __future__ import annotations

import datetime
from decimal import Decimal
from enum import Enum
import os
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

from .model import Isin, TaxTreaty

# =============================================================================
# Allowances
# =============================================================================

# Capital Gains Tax annual exempt amount (tax-free allowance)
# https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances#tax-free-allowances-for-capital-gains-tax
CAPITAL_GAIN_ALLOWANCES: Final[dict[int, int]] = {
    2014: 11000,
    2015: 11100,
    2016: 11100,
    2017: 11300,
    2018: 11700,
    2019: 12000,
    2020: 12300,
    2021: 12300,
    2022: 12300,
    2023: 6000,
    2024: 3000,
    2025: 3000,
}

# Dividend Tax annual allowance
# https://www.gov.uk/tax-on-dividends
DIVIDEND_ALLOWANCES: Final[dict[int, int]] = {
    2019: 2000,
    2020: 2000,
    2021: 2000,
    2022: 2000,
    2023: 1000,
    2024: 500,
    2025: 500,
}


# =============================================================================
# Double taxation
# =============================================================================

# Country and treaty rates per country of the income's source, keyed by the
# ISO 3166-1 alpha-2 code that an ISIN is prefixed with.
# https://www.gov.uk/hmrc-internal-manuals/double-taxation-relief
DIVIDEND_DOUBLE_TAXATION_RULES: Final[dict[str, TaxTreaty]] = {
    "US": TaxTreaty("USA", Decimal("0.15"), Decimal("0.15")),
    "PL": TaxTreaty("Poland", Decimal("0.19"), Decimal("0.1")),
}

# Fallback for transactions with no ISIN: guess the source country from the
# currency the dividend was paid in. This is only a guess — a broker reporting
# in the account's base currency breaks it — so it is used only as a last
# resort, and it keeps the behaviour brokers relied on before ISINs were used.
DIVIDEND_CURRENCY_TO_COUNTRY: Final[dict[str, str]] = {
    "USD": "US",
    "PLN": "PL",
}

# ISIN is prefixed with the ISO 3166-1 alpha-2 code of the issuing country.
ISIN_COUNTRY_CODE_LENGTH: Final = 2


# =============================================================================
# General constants
# =============================================================================


class RuntimeMode(Enum):
    """Runtime mode, used to differentiate testing behaviours."""

    # Default
    PROD = 1
    # pytest
    TEST = 2
    # pytest within pre-commit hook
    TEST_STRICT = 3


CGT_MODE: Final = (
    RuntimeMode.TEST_STRICT
    if os.environ.get("CGT_TEST_MODE_STRICT", "0") == "1"
    else RuntimeMode.TEST
    if os.environ.get("CGT_TEST_MODE", "0") == "1"
    else RuntimeMode.PROD
)
INTERNAL_START_DATE: Final = datetime.date(2010, 1, 1)

# Bed and Breakfast rule: HMRC requires matching disposals with acquisitions
# within 30 days following the disposal to prevent tax avoidance.
# See: https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560
BED_AND_BREAKFAST_DAYS: Final = 30

# How far back from a withholding its dividend may lie. A broker posts the
# tax with the payment or in the weeks after it, and a correction of one
# later still, so the search reaches well back. Beyond this the tax is left
# out of the report rather than attributed to a payment it may not belong to.
DIVIDEND_TAX_MATCH_DAYS: Final = 30

# How far forward it may lie, for a broker that posts the tax just ahead of
# the payment. This is deliberately short: the further it reaches, the more
# often a monthly holding's next payment is a candidate alongside the last
# one, and a withholding two payments could claim is left out rather than
# assigned to either.
DIVIDEND_TAX_LEAD_DAYS: Final = 5

UK_CURRENCY: Final = "GBP"

# Tax dates are UK calendar days, so timestamped transactions are read
# in UK time (GMT in winter, BST in summer) and not in UTC.
UK_TIMEZONE: Final = ZoneInfo("Europe/London")

ERI_TAX_DATE_DELTA: Final = relativedelta(months=6)

TICKER_RENAMES: Final[dict[str, str]] = {
    "FB": "META",
}

# Exchange-specific tickers for one security, mapped to the ticker the report
# uses. Trading 212 lists the Xetra line of a US share under its German code,
# so one holding arrives under two names and pools, matches and prices as two.
# Keyed by ISIN as well as ticker, and deliberately not in TICKER_RENAMES: a
# ticker code belongs to an exchange rather than to a security, so `NVD` is
# NVDA only under US67066G1040, and TICKER_RENAMES is applied at parse time,
# before any ISIN is known. Add a pair only once both listings are confirmed.
ISIN_TICKER_ALIASES: Final[dict[tuple[Isin, str], str]] = {
    (Isin("US67066G1040"), "NVD"): "NVDA",
    (Isin("US11135F1012"), "1YD"): "AVGO",
    (Isin("DE0007030009"), "RHMd"): "RHM",
}

# For ActionType.RENAME: set symbol=new_ticker, description=f"{RENAME_DESCRIPTION_PREFIX}{old_ticker}"
RENAME_DESCRIPTION_PREFIX: Final = "renamed from "


# =============================================================================
# Resource files
# =============================================================================

assert __package__ is not None
PACKAGE_NAME: Final = __package__

# LaTeX template for calculations report
LATEX_TEMPLATE_RESOURCE: Final = "template.tex.j2"

# Initial vesting and spin-off prices
INITIAL_PRICES_RESOURCE: Final = "initial_prices.csv"

# ISIN initial translation file
INITIAL_ISIN_TRANSLATION_RESOURCE: Final = "initial_isin_translation.csv"

# ERI data folder
ERI_RESOURCE_FOLDER: Final = "eri"

# Most recent transactions shown when the balance check fails
BALANCE_CHECK_CONTEXT_ROWS: Final = 10

# Acquisition dates listed when a same-day sale and transfer to spouse clash
MAX_CONTENDED_DATES_SHOWN: Final = 3


# =============================================================================
# Default output paths
# =============================================================================

DEFAULT_OUTPUT_FOLDER: Final = Path("out")

# Generated PDF report
DEFAULT_REPORT_PATH: Final = DEFAULT_OUTPUT_FOLDER / "calculations.pdf"

# Monthly exchange rates from HMRC
DEFAULT_EXCHANGE_RATES_FILE: Final = DEFAULT_OUTPUT_FOLDER / "exchange_rates.csv"

# Spin-offs output file
DEFAULT_SPIN_OFF_FILE: Final = DEFAULT_OUTPUT_FOLDER / "spin_offs.csv"

# ISIN to ticker translation file
DEFAULT_ISIN_TRANSLATION_FILE: Final = DEFAULT_OUTPUT_FOLDER / "isin_translation.csv"
