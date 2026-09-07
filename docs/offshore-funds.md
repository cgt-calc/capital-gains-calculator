# Offshore Funds (ERI)

Use this page if you hold a fund based outside the UK. Not every non-UK fund is an offshore fund for
UK tax purposes, so check HMRC's
[offshore funds guidance](https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds)
first.

For a reporting fund, excess reported income (ERI) is the income per unit above the amount paid to
investors. It can be taxable even though you did not receive it in cash. It can apply to both
accumulating and distributing share classes.

## What to check

1. Find the exact share class and ISIN in your statement or the fund documents.
2. Confirm that share class had reporting fund status for every period you held it. Match the ISIN
    against HMRC's
    [list of approved offshore reporting funds](https://www.gov.uk/government/publications/approved-offshore-reporting-funds),
    not the fund name alone. Ask the fund manager if its status is unclear.
3. Check that cgt-calc has the ERI per unit for every relevant reporting period. Start with the
    bundled data below. If anything is missing, use the fund's report and the
    [custom ERI guide](custom-eri-data.md).
4. Check how the income should be reported. cgt-calc treats ERI and cash distributions as dividend
    income by default. For a bond fund, pass its ticker to `--interest-fund-tickers` to report both
    as interest. cgt-calc cannot handle property, miscellaneous or mixed fund income; calculate that
    income outside the tool and do not use its income totals. See HMRC's
    [income classification](https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds).

## Bundled data

cgt-calc currently bundles:

- [Vanguard Funds plc 2018-2025 and Vanguard Investment Series plc 2021](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)
- [BlackRock Funds 2019-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/blackrock_eri.csv)
- [iShares Funds 2018-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/ishares_eri.csv)
- [Invesco Funds 2018-2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/invesco_eri.csv)
- [Xtrackers Funds 2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/xtrackers_eri.csv)
- [Amundi CSH2 2015-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/amundi_csh2_eri.csv)

Open the relevant file and check that it contains the exact ISIN and every reporting period you
need. cgt-calc cannot detect a missing ERI row. If supplied ERI cannot be matched to the holding,
check the ISIN-to-ticker mapping below. Do not assume that missing or unmatched data means zero ERI.

Bundled ERI data is indexed by ISIN. If your broker does not supply one, cgt-calc uses its
ISIN-to-ticker mapping to match the holding. To check or override the mapping, see
[ISIN to ticker translation](extra-data-and-options.md#isin-to-ticker-translation).

## Unsupported functionality

cgt-calc does not support:

- Disposing of a holding in an offshore fund that was non-reporting at any time while you owned it.
    A gain may be an offshore income gain subject to Income Tax, while a loss may still be an
    allowable capital loss. HMRC explains the
    [gain and loss treatment](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13410)
    and
    [funds that changed status](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13412).
    cgt-calc does not detect reporting status, elections or exceptions and applies ordinary CGT
    rules. Work out the disposal outside cgt-calc before using the report totals.
- Full equalisation adjustments. Taxable fund income (cash distributions and/or ERI) and the
    acquisition cost used for CGT can be reduced. cgt-calc does not make these adjustments; use the
    fund's report and HMRC's
    [income guidance](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13328) and
    [CGT guidance](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13372) to make both
    adjustments outside the tool.
