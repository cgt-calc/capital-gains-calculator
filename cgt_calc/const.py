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

from .model import ActionType, Isin, TaxTreaty

# =============================================================================
# Allowances
# =============================================================================

# Capital Gains Tax annual exempt amount (tax-free allowance)
# https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances#tax-free-allowances-for-capital-gains-tax
# 2008 to 2013 are from the Treasury's annual orders (SI 2008/708, 2009/824,
# 2010/923, 2011/899, 2013/662) and, for 2012, TCGA 1992 s3(2) as amended by
# FA 2012 s34, which froze the amount that SI 2012/881 had raised.
CAPITAL_GAIN_ALLOWANCES: Final[dict[int, int]] = {
    2008: 9600,
    2009: 10100,
    2010: 10100,
    2011: 10600,
    2012: 10600,
    2013: 10900,
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
    2026: 3000,
}

# Capital Gains Tax rates on an individual's shares, as (first disposal date,
# basic rate %, higher rate %). Each pair applies until the next one starts.
# One rate for everyone from 6 April 2008, a second above the basic rate band
# from 23 June 2010, both cut from 6 April 2016 and raised from 30 October 2024.
# https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg10246
# https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances
CAPITAL_GAINS_TAX_RATES: Final[tuple[tuple[datetime.date, int, int], ...]] = (
    (datetime.date(2008, 4, 6), 18, 18),
    (datetime.date(2010, 6, 23), 18, 28),
    (datetime.date(2016, 4, 6), 10, 20),
    (datetime.date(2024, 10, 30), 18, 24),
)

# Income Tax basic rate limit: the taxable income below which a gain is taxed
# at the basic rate, from 2010/11, the first year with a higher rate. Scottish
# and Welsh taxpayers use the same limit (CG21204).
# From HMRC's Rates of Income Tax table:
# https://www.gov.uk/government/statistics/rates-of-income-statistics
BASIC_RATE_LIMITS: Final[dict[int, int]] = {
    2010: 37400,
    2011: 35000,
    2012: 34370,
    2013: 32010,
    2014: 31865,
    2015: 31785,
    2016: 32000,
    2017: 33500,
    2018: 34500,
    2019: 37500,
    2020: 37500,
    2021: 37700,
    2022: 37700,
    2023: 37700,
    2024: 37700,
    2025: 37700,
    2026: 37700,
}

# Income Tax Personal Allowance: the standard amount, before it is reduced for
# income above £100,000. At that income a gain is taxed at the higher rate
# whatever the allowance, so the reduction never changes the rate.
# From HMRC's Income Tax personal allowances and reliefs table:
# https://www.gov.uk/government/statistics/income-tax-personal-allowances-and-reliefs
PERSONAL_ALLOWANCES: Final[dict[int, int]] = {
    2010: 6475,
    2011: 7475,
    2012: 8105,
    2013: 9440,
    2014: 10000,
    2015: 10600,
    2016: 11000,
    2017: 11500,
    2018: 11850,
    2019: 12500,
    2020: 12500,
    2021: 12570,
    2022: 12570,
    2023: 12570,
    2024: 12570,
    2025: 12570,
    2026: 12570,
}

# Dividend Tax annual allowance
# https://www.gov.uk/tax-on-dividends
# ITA 2007 s13A: £5,000 from 2016/17, £2,000 from 2018/19 (F(No. 2)A 2017 s8).
DIVIDEND_ALLOWANCES: Final[dict[int, int]] = {
    2016: 5000,
    2017: 5000,
    2018: 2000,
    2019: 2000,
    2020: 2000,
    2021: 2000,
    2022: 2000,
    2023: 1000,
    2024: 500,
    2025: 500,
    2026: 500,
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
# The earliest tax year `--year` accepts: the first in which every disposal
# is matched under the share pooling rules (CG51550). History may reach
# further back.
EARLIEST_TAX_YEAR: Final = 2008

# The first tax year with a dividend allowance. Before it most dividends
# carried a tax credit, so the amount received is not the amount taxed, and
# the report does not work the taxable amount out.
FIRST_DIVIDEND_ALLOWANCE_YEAR: Final = 2016

# From 6 April 2008 an individual's shares of one class are pooled at cost
# whenever they were acquired, and shares held on 6 April 1982 enter at their
# 31 March 1982 value. Disposals before 2008 followed other rules (LIFO,
# indexation, taper) that decide which shares were left.
# See: https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51550
POOLING_RULES_START_DATE: Final = datetime.date(2008, 4, 6)
REBASING_DATE: Final = datetime.date(1982, 4, 6)

# Actions refused before POOLING_RULES_START_DATE: each disposes of shares or
# changes a pool's cost in a way the pre-2008 rules could treat differently.
# Every other action only adds shares at cost, restates a holding, or moves
# cash or income, which the pool treats the same before and after 2008.
PRE_POOLING_REFUSED_ACTIONS: Final = frozenset(
    {
        ActionType.SELL,
        ActionType.CASH_MERGER,
        ActionType.FULL_REDEMPTION,
        ActionType.GIFT,
        ActionType.GIFT_UNCONNECTED,
        ActionType.UNCLASSIFIED_GIFT,
        ActionType.TRANSFER_TO_SPOUSE,
        # The no gain/no loss cost included the spouse's indexation allowance.
        ActionType.TRANSFER_FROM_SPOUSE,
        ActionType.FEE,
        # The reporting fund regime began in 2009, so such a row is a mistake.
        ActionType.EXCESS_REPORTED_INCOME,
        ActionType.OPTION_GRANT,
        ActionType.OPTION_CLOSE,
        ActionType.OPTION_EXPIRY,
        ActionType.OPTION_ASSIGNMENT,
    }
)

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

# Exchange-specific tickers for one security, mapped to the ticker the report
# uses. Trading 212 lists the Xetra line of a US share under its German code,
# so one holding arrives under two names and pools, matches and prices as two.
# Keyed by ISIN as well as ticker: a ticker code belongs to an exchange rather
# than to a security, so `NVD` is NVDA only under US67066G1040. These are
# listings that trade side by side, not renames over time, which are in
# `ticker_renames.csv`. Add a pair only once both listings are confirmed.
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

# Bundled USD share prices for a few vests, used when no prices file is passed
SHARE_PRICES_RESOURCE: Final = "share_prices.csv"

# Package resource listing ticker changes, with dates and ISINs.
TICKER_RENAMES_RESOURCE: Final = "ticker_renames.csv"

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
