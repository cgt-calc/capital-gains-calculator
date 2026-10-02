# Extra Data and Options

Most users can generate a report from a supported broker export without these files or options.
Start with your [broker guide](brokers/index.md). Return here only if cgt-calc asks for more
information or one of the tax settings below applies to your investments.

## Automatic data fetching

cgt-calc creates and updates these local files itself. You do not need to create or select them for
a normal calculation.

### Exchange rates

cgt-calc converts other currencies to pounds at HMRC's monthly exchange rates.

- **February 2015 to April 2016:** the rates come with cgt-calc. In those months HMRC also
    [changed some rates during the month](https://www.hmrc.gov.uk/softwaredevelopers/2016-exrates.htm),
    and each change is used from the day it applied. For example, the US dollar rate for January
    2016 was 1.5003 until the 26th and 1.4144 from the 27th.
- **May 2016 onwards:** cgt-calc downloads each month's rates, from
    [HMRC's legacy service](https://www.hmrc.gov.uk/softwaredevelopers/2020-exrates.html) up to 2020
    and from the [UK Trade Tariff API](https://www.trade-tariff.service.gov.uk/exchange_rates) for
    2021 onwards. It saves them to `out/exchange_rates.csv`.

For February 2015 to April 2016, a row in that file is used only for a currency HMRC did not list.
If the file gives a different rate from HMRC's for one of those dates, cgt-calc uses HMRC's rate and
prints a warning naming the row. A file written by an earlier version of cgt-calc can hold such
rows, because it saved the rate from before HMRC's change. The warning is repeated on every run
until you remove the row.

No rates before February 2015 come with cgt-calc or can be downloaded. For an earlier transaction in
another currency, add a row to the file yourself: the transaction's date in the `month` column, the
currency code, and the rate as units of that currency per £1. If the file does not exist yet, start
it with the header line `month,currency,rate`.

HMRC's rates for those months are kept in the UK Government Web Archive:

- **April 2002 to 2007:** open
    [HMRC's exchange rates for 2007](https://webarchive.nationalarchives.gov.uk/ukgwa/20110202145605/http://customs.hmrc.gov.uk/channelsPortalWebApp/channelsPortalWebApp.portal?_nfpb=true&_pageLabel=pageImport_RatesCodesTools&id=EXRATES_2007&columns=1)
    and change `EXRATES_2007` in the address to the year you need. Each month's page offers the
    table as a PDF download.
- **2008 to 2014:** open
    [HMRC's exchange rates for 2009](https://webarchive.nationalarchives.gov.uk/ukgwa/20141203171558/http://customs.hmrc.gov.uk/channelsPortalWebApp/channelsPortalWebApp.portal?_nfpb=true&_pageLabel=pageImport_RatesCodesTools&id=EXRATES_2009&columns=1)
    and choose “Rates of Exchange for Customs and VAT purposes” for the month. For another year,
    change `EXRATES_2009` in the address to that year.
- **January 2015:**
    [HMRC exchange rates for 2015: monthly](https://webarchive.nationalarchives.gov.uk/ukgwa/20231016190054/https://www.gov.uk/government/publications/hmrc-exchange-rates-for-2015-monthly).

Use the month's rate, unless the table shows a “Date of change” and “New rate” for that currency:
from that date on, use the new rate. Each transaction needs its own row. For example, June 2009
lists Dollar (USD) at 1.5649 with no change, so a purchase on 15 June 2009 needs the row
`2009-06-15,USD,1.5649`.

Use `--exchange-rates-file` to select another file in the same format. An empty value disables the
cache. Keep the completed file with the report to preserve the exchange rates used. cgt-calc may add
missing months to the selected file, so retain the version used for the final report. Rates that
come with cgt-calc are not written to the file. Keeping it does not preserve other fetched data,
such as Yahoo Finance prices.

### ISIN to ticker translation

When an ERI row identifies a fund only by its ISIN, cgt-calc maps it to ticker symbols. It checks
existing mappings first and queries the [Open FIGI API](https://www.openfigi.com/api/overview) only
if needed. It saves new mappings in `out/isin_translation.csv` by default; `--isin-translation-file`
selects another path.

cgt-calc can create or rewrite this file after a successful lookup. Tickers read from your broker
transactions are used for the run but never written to the file: cached, they would be read back as
reference data and could contradict a later run. It starts with the bundled
[`initial_isin_translation.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv).
If you edit the cache, a row for an existing ISIN replaces its bundled symbols. Put every verified
ticker for that ISIN on the same row.

## When extra information is needed

### Missing share prices

cgt-calc sometimes needs a share's price on a date that your broker files do not give:

- **A vest without a price.** A vest or other stock-plan acquisition needs a price per share to
    establish its cost. Broker files usually provide it, and cgt-calc includes some historical USD
    values in
    [`share_prices.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/share_prices.csv).
    If cgt-calc stops with a **No share price** error, give the missing price in the currency of the
    transaction it is for.
- **A spin-off.** cgt-calc splits the old holding's cost between the old and the new holding by
    their market values on the date of the spin-off row, using each holding's closing price that day
    from Yahoo Finance, as traded rather than adjusted for later dividends and splits. If it stops
    with a **No market data found** error, for example because Yahoo lists the ticker differently
    from your broker, give the closing prices of both holdings on that date as they traded then, not
    the split-adjusted figures on Yahoo's history page. The file has no currency column, so write
    both prices in the same currency; for London shares, do not mix pence and pounds. Prices you
    give are used instead of Yahoo's, and giving only one of the two stops the run.

Put the prices in a CSV file in the same format as `share_prices.csv`, under the tickers in your
broker files, not Yahoo's, and pass it with `--prices-file`. For a spin-off of `NEWCO` from `ACME`
on 1 April 2024:

```csv
date,symbol,price
"Apr 01, 2024",ACME,94.00
"Apr 01, 2024",NEWCO,17.50
```

Your file replaces the bundled prices rather than adding to them, so copy in any bundled rows you
still need. A row under a ticker the company has since changed, such as `FB`, counts for its current
ticker, `META`, while the old ticker was still the company's: an `FB` row dated from 26 June 2025
prices the security that has traded as `FB` since then, not Meta. If two rows give the same share
different prices on one day, cgt-calc stops and asks you to remove one.

### Spin-off source mappings

A spin-off row may name the new holding without naming the old holding it came from. cgt-calc needs
that link to divide the existing pooled cost between them. During an interactive run, it asks for
the old ticker and saves the answer to `out/spin_offs.csv`.

If the run cannot ask you for the old ticker, add the required `dst,src` row to a mapping file, pass
that file with `--spin-offs-file`, and rerun. An empty value disables the cache, so cgt-calc does
not read or save spin-off mappings.

### Estimate unrealized gains

Use `--unrealized-gains` to add current-price estimates to the portfolio and summary printed in the
terminal. For each holding left at the report's end date, cgt-calc uses the latest usable price
returned by Yahoo Finance, converts it to GBP and subtracts its Section 104 pooled cost. If no
usable price is returned, the holding is shown as `unknown`, omitted from the numeric **Unrealized
gains** total and covered by a warning. Check unknown or unexpected estimates against your broker.

This is a portfolio estimate, not the gain from an actual sale. It does not include selling costs or
change the report's capital gains, losses or taxable figures. For an older report, the ending
portfolio may not be what you hold today. See
[Privacy and data security](privacy.md#privacy-and-data-security) for details of the Yahoo lookup.

### Bond-fund income

Some offshore bond funds have income that must be reported as interest rather than dividends. After
checking the fund's classification, pass its ticker to `--interest-fund-tickers`. Use a
comma-separated list for several tickers. This setting applies to both cash distributions and ERI;
see the [offshore-fund checklist](offshore-funds.md#what-to-check). It reports the income as foreign
interest, so do not use it for a UK fund. If cgt-calc reports a UK bond fund distribution as a
dividend, adjust the income figures outside cgt-calc.

### Income reported in more than one currency

Two brokers reporting the same dividend in different currencies, or a dividend and its withholding
tax in different currencies, stop the calculation with **Cannot combine amounts in different
currencies** or **Dividend and withholding tax currencies do not match**.

If the rows really are the same dividend and use GBP plus one foreign currency, pass
`--autoconvert-currency`:

```shell
cgt-calc --year 2024 --revolut-file revolut.csv --trading212-dir trading212/ --autoconvert-currency
```

cgt-calc then reports them as one figure, converting the foreign amounts at the HMRC rate for the
date of the dividend. Withholding posted in a later month is converted at that same rate, not the
rate for the month it posted in. Amounts already in GBP are used as they are.

The option is off by default, and it cannot tell whether two rows with the same ticker and date
describe the same security and payment. Compare the ISIN and payment details on each broker's
statement before enabling it. Two different non-GBP currencies remain an error: the option does not
convert between foreign currencies. If your broker cannot provide a verified amount in one currency,
report that dividend outside cgt-calc.

Where an ISIN is available, cgt-calc uses it to determine the dividend's source country. Without an
ISIN, it falls back to the currency of the dividend itself, never that of the tax. For a dividend
your broker reports already converted to GBP, cgt-calc therefore cannot determine a treaty
automatically: it warns that the source country is unknown and leaves the relief out of the report.

A payment currency does not prove where the paying company is resident, especially when a broker
converts payments into your account currency.
[HMRC's dividend treaty guidance](https://www.gov.uk/hmrc-internal-manuals/international-manual/intm164020)
bases treaty treatment on the residence of the company paying the dividend. Check that residence and
the tax deducted on your broker's tax voucher. If the fallback is wrong or missing, add a verified
mapping under [ISIN to ticker translation](#isin-to-ticker-translation); if you cannot, calculate
the relief outside cgt-calc rather than relying on the treaty figure in the report.

### Shares held before April 2008

Since 6 April 2008, all your shares in one company are pooled at what they cost, whenever you bought
them ([CG51550](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51550)). cgt-calc
applies these rules to your whole history, so purchases from before 2008 count towards the cost of
shares you sell now. Two kinds of earlier history are refused, with an error naming the transaction:

- **A sale, gift or other disposal before 6 April 2008.** Earlier disposals followed different
    rules, which decided which shares you still held, and cgt-calc does not implement them. Replace
    that holding's rows before 6 April 2008 with one `BUY` in a [RAW file](brokers/raw.md) dated 5
    April 2008, for the shares you still held and their total cost from your records of the time.
    Enter the cost in sterling, with the currency `GBP`: a cost in another currency would be
    converted at the 5 April 2008 rate, but each purchase's cost converts at the rate on the day it
    was made ([CG78310](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg78310)). Use
    the cost without indexation: an old pool statement may show an indexed figure beside it. A `BUY`
    with no deposit to pay for it fails the cash balance check, so add a `TRANSFER` row for the same
    amount on the same date, or run with `--no-balance-check`.
- **Anything before 6 April 1982.** Shares held on that date are pooled at their 31 March 1982
    market value, which cgt-calc cannot know.

A transaction in another currency before February 2015 also needs its rate added by hand; see
[Exchange rates](#exchange-rates).

### CGT-exempt instruments (advanced)

Most users should not use `--cgt-exempt-tickers`. Pass a comma-separated list only when you have
established that each instrument is exempt under
[TCGA 1992 s115](https://www.legislation.gov.uk/ukpga/1992/12/section/115).

- This is your own unverified assertion. The calculator does not identify or validate gilts or
    [qualifying corporate bonds (QCBs)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg53702),
    and QCB status cannot be inferred from a ticker or name.
- The override disregards both gains and losses on disposals. Do not use it where a disposal can
    still produce a charge: disposing of a QCB acquired on a takeover or reorganisation can bring a
    [deferred gain](https://www.gov.uk/government/publications/share-reorganisations-company-takeovers-and-capital-gains-tax-hs285-self-assessment-helpsheet)
    back into charge, and the profit on a
    [deeply discounted security](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim3010)
    is taxable as income. Neither is calculated here.
- The per-disposal breakdown in the PDF applies the ordinary same-day, 30-day and Section 104 share
    identification rules. Those are not the identification rules HMRC gives for gilts, QCBs and
    other relevant securities, so read the breakdown as workings for the quantities only. It has no
    effect on the figures reported, because the gain or loss is disregarded.
- Coupon and accrued interest remain taxable income. The calculator does not implement the
    [Accrued Income Scheme](https://www.gov.uk/government/publications/accrued-income-scheme-hs343-self-assessment-helpsheet).
- The importers for [Freetrade](brokers/freetrade.md),
    [Hargreaves Lansdown](brokers/hargreaves-lansdown.md) and
    [Interactive Brokers](brokers/interactive-brokers.md) are documented as unvalidated for bonds
    and gilts. This override does not validate them either.
- The override changes the capital gains calculation only. A coupon is reported in whichever box its
    broker rows put it in. `--interest-fund-tickers` reports as foreign interest, so it is not a fix
    for a UK gilt coupon. Check the interest and dividend figures against your own records.
- Matching uses the ticker at the disposal, so an instrument renamed part-way through the history
    has to be listed under both names.
- A gift of a listed instrument is reported as an exempt disposal and does not also appear in the
    **Gifts at market value** section.
- Listing an instrument does not lift the restrictions on disposing of it in more than one way on a
    single day. A sale and a gift to a connected person on the same day are still refused, even
    though the loss they cannot apportion would be disregarded.
