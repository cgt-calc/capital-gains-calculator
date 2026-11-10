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

## Years you were not UK resident

ERI is taxed on its fund distribution date, six months after the reporting period ends. If you were
not UK resident on that date, the ERI isn't taxed in the UK, and so it isn't added to the cost of
your units either ([regulation 99(3)](https://www.legislation.gov.uk/uksi/2009/3001/regulation/99)
of the Offshore Funds (Tax) Regulations 2009 and
[section 368](https://www.legislation.gov.uk/ukpga/2005/5/section/368) of the Income Tax (Trading
and Other Income) Act 2005).

To leave this ERI out, list the periods you were not UK resident as `START:END`, with both dates
included. Leave `END` empty for a period that hasn't ended. For a split year, start the period on
the first day of the overseas part. For example, abroad for two tax years and again since April
2025:

```shell
cgt-calc --non-uk-resident-periods 2019-04-06:2021-04-05,2025-04-06: ...
```

Every ERI distributed in one of these periods is then left out, both as income and as a cost. The
option is only used for ERI: cgt-calc doesn't apply any other residence rules, such as those for
gains on disposals.

## Bundled data

cgt-calc currently bundles:

- [Vanguard Funds plc 2018-2025 and Vanguard Investment Series plc 2021-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)
- [BlackRock Funds 2019-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/blackrock_eri.csv)
- [iShares Funds 2018-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/ishares_eri.csv)
- [Invesco Funds 2018-2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/invesco_eri.csv)
- [Xtrackers Funds 2024-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/xtrackers_eri.csv)
- [Amundi Smart Overnight Return (CSH2) 2015-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/amundi_eri.csv),
    entered by hand from KPMG Reporting Funds
- [Legal & General UCITS ETF plc 2023-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/legal_and_general_eri.csv)

Open the relevant file and check that it contains the exact ISIN and every reporting period you
need. cgt-calc cannot detect a missing ERI row. If supplied ERI cannot be matched to the holding,
check the ISIN-to-ticker mapping below. Do not assume that missing or unmatched data means zero ERI.

Bundled ERI data is indexed by ISIN. If your broker does not supply one, cgt-calc uses its
ISIN-to-ticker mapping to match the holding. To check or override the mapping, see
[ISIN to ticker translation](extra-data-and-options.md#isin-to-ticker-translation).

Charles Schwab rows carry no ISIN, and Schwab shows a fund based outside the US under a US
over-the-counter symbol, not under the London tickers the bundled mapping gives it. So a Schwab
holding is matched only when the mapping gives the fund's ISIN the symbol Schwab shows. The bundled
mapping does that for very few funds: a US fund it lists under its US symbol, such as `CHIQ`, and a
fund based outside the US whose over-the-counter symbol it lists. For any other fund, add the row;
see [Funds based outside the US](brokers/schwab.md#funds-based-outside-the-us).

## Exchange rate

cgt-calc converts ERI in a foreign currency at HMRC's rate for the fund distribution date, when the
ERI counts as income. The same pound figure is reported as income and added to the cost of your
units ([regulation 99(3)](https://www.legislation.gov.uk/uksi/2009/3001/regulation/99) of the
Offshore Funds (Tax) Regulations 2009).

Until the distribution date comes, cgt-calc uses the rate for the end of the reporting period
instead, and warns you. It switches to the distribution date's rate on that date, not when HMRC
publishes the rate for that month, which can be earlier. The PDF report marks each ERI figure that
uses the period-end rate as estimated. Both reports say when the allowable costs or the dividend or
interest totals include one. Run cgt-calc again after the distribution date for the final figures.

### Disposals before the distribution date

If you dispose of units after the end of a reporting period and before its distribution date, the
ERI for those units is treated, for capital gains tax, as received just before the disposal
([regulation 99(5)](https://www.legislation.gov.uk/uksi/2009/3001/regulation/99)). Neither the
regulations nor HMRC's guidance say whether the cost added for them should still use the rate for
the distribution date, or the rate for the disposal date instead.

cgt-calc assumes the distribution date: the cost added is the same pound figure as the income. This
is cgt-calc's assumption, not HMRC's stated position. cgt-calc warns about each such disposal and
notes it in the PDF report. The two readings differ by how far the exchange rate moved between the
disposal and the distribution date, which can make your gain higher or lower.

If you take the other view, work out the difference for each such disposal and adjust its allowable
cost yourself, or ask a tax adviser:

1. Find the ERI, in the fund's currency, that the disposal's cost includes. Only the units it takes
    from your holding carry ERI: the Section 104 line in the PDF report that has the note. Units
    matched with a purchase on the same day or within 30 days do not. At the end of the reporting
    period, your holding's cost took on the ERI per unit (from the bundled ERI file or the fund's
    report) times the units you held. Each disposal takes the same fraction of the ERI still in that
    cost as it takes of the holding's units: taking half the units takes half of it.
2. Convert that amount at HMRC's rate for the month of the disposal, and again at HMRC's rate for
    the month of the distribution date.
3. Add the first pound figure less the second to the disposal's allowable cost. If the first is
    smaller, this reduces the cost.

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
