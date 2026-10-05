# Generate and Review a Report

## Before you start

Most users need:

- **cgt-calc installed.** Follow the [installation guide](installation.md) before continuing.
- **LaTeX installed if you want a PDF report.** It is not needed for a terminal-only report using
    `--no-report`.
- **A complete transaction history from every relevant account.** Follow the instructions for each
    [supported broker](brokers/index.md). Earlier purchases or employer-share awards can establish
    the cost of shares sold later. Purchases in the 30 days after the report ends can be matched to
    a sale or other disposal inside the report. Exporting from the date the account was opened
    through at least 30 days after the report ends is the safest option. A history reaching back
    before 6 April 2008 may need one change by hand; see
    [shares held before April 2008](extra-data-and-options.md#shares-held-before-april-2008).

Depending on your investments, you may also need:

- **Extra income data for some funds outside the UK.** This can apply whether the fund pays income
    to you or reinvests it. Check the [offshore funds guide](offshore-funds.md).
- **Transfers to or from a spouse or civil partner, recorded by hand.** No broker export marks
    these. Add them in a small RAW file using the
    [`TRANSFER_TO_SPOUSE` or `TRANSFER_FROM_SPOUSE`](brokers/raw.md#transfers-to-a-spouse-or-civil-partner)
    action and pass it alongside your export.

Do not include the same transaction in more than one export. cgt-calc can validate the transactions
it receives, but it cannot detect every missing or duplicated export.

## Choose the tax year

Pass the first year of the UK tax year to `--year`. For example, `--year 2024` means the 2024/25 tax
year, from 6 April 2024 to 5 April 2025.

If you omit `--year`, cgt-calc uses the most recently completed UK tax year.

The earliest tax year cgt-calc reports is 2008/09, the first in which every sale is matched under
the share pooling rules that still apply
([CG51550](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51550)). For years before
2016/17 the report shows no dividend allowance and no taxable dividend figure: there was no
allowance then, and most dividends carried a tax credit, so the amount you received is not the
amount taxed. If you need the taxable amount for those years, work it out from your dividend
vouchers and HMRC's guidance on the tax credits on
[UK dividends](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim5100) and
[foreign dividends](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim5102).

## Generate the report

Pass the export using the option for your broker. For example, with a Charles Schwab export:

```shell
cgt-calc --year 2024 --schwab-file schwab_transactions.csv
```

You can combine inputs from different brokers in one calculation:

```shell
cgt-calc --year 2024 \
  --schwab-file schwab_transactions.csv \
  --trading212-dir trading212/ \
  --mssb-dir morgan_stanley/
```

See the [broker instructions](brokers/index.md) for the correct option and export format for each
source. If your broker is not listed, you can convert its transactions to the generic
[RAW format](brokers/raw.md).

## Find the results

cgt-calc prints a summary to the terminal and, by default, writes the detailed PDF report to
`out/calculations.pdf`.

Use `--output` to choose a different PDF location:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --output reports/2024-25.pdf
```

The summary is written to **stdout** and progress messages and warnings are written to **stderr**.
This means you can save the text summary without mixing in progress messages:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv > report.txt
```

Redirecting stdout does not change where the PDF is saved.

Use `--no-report` instead of `--output` to print the terminal summary without generating a report.
This creates neither a PDF nor LaTeX source and does not require `pdflatex`:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --no-report
```

To save the LaTeX source without creating a PDF, use `--no-pdflatex`. The source follows the
`--output` path with a `.tex` extension (by default, `out/calculations.tex`). For example,
`--output reports/2024-25.pdf` writes `reports/2024-25.tex`.

## Inspect parsed transactions

Use `--dump-transactions` followed by a filename to save the transactions cgt-calc read from your
exports to a CSV file, then carry on with the normal calculation:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --dump-transactions parsed-transactions.csv
```

This helps when you want to compare two exports of the same account, or see what a broker file
actually produced before reading the report.

cgt-calc saves the file before any calculation starts, and confirms it on **stderr**:

```text
Saved 123 parsed transactions to parsed-transactions.csv
```

That message means the file is complete. It is not the message that ends a run, which begins
`Done!`, so a saved file does not show that the calculation succeeded. If the calculation fails, the
file is kept for you to inspect and the command still exits with an error.

### What the file contains

One row per transaction, under a header naming every column. The rows are in the order the
calculation reads them, which is not always the order they happened: some brokers export the newest
row first, and cgt-calc deliberately places some of a day’s rows before others.

Every transaction cgt-calc loaded is exported, including dates outside the reporting period.
`--year`, `--from` and `--to` still choose the period the report covers.

Because the calculation has not run yet, ticker names and quantities can differ from the final
report. The file reflects whatever duplicate handling the parser for your broker supports and
removes nothing itself, so follow the [instructions for your broker](brokers/index.md) and do not
assume overlapping exports are safe.

Numbers are exact rather than rounded for display. `price`, `fees` and `amount` are in the row’s
`currency`, and fees charged in another currency stay in `foreign_fees` under their own currency.

Columns holding several named values, such as `foreign_fees` and `source`, use JSON, which keeps
names and values together in one cell: `{"EUR":"2.50"}` is a fee of 2.50 euros. An empty column
means the value was unset or empty text, which look the same outside JSON.

Excess reported income (ERI) records loaded for funds you hold appear with the action
`EXCESS_REPORTED_INCOME` and an `isin` in place of a `symbol`. They carry only a per-unit `price`,
so `quantity` and `amount` stay empty until the calculation works them out. See the
[offshore funds guide](offshore-funds.md).

The file is a snapshot for inspection, not an input format: its columns are not the seven columns of
the [RAW format](brokers/raw.md), and it cannot be passed back with `--raw-file`. `source` names the
input file each transaction came from, and includes a row number when the parser records one, so the
export can contain paths from your own machine. Read it before you send it to anyone, and see the
[privacy notes](privacy.md).

### Where it is written

`--dump-transactions` always creates a new file. cgt-calc refuses to overwrite an existing file,
refuses a path whose directory does not exist, and refuses a path the same run would write later,
such as the report or one of the cache files. If writing fails partway through, it stops with an
error naming the path; remove the partly written file, or choose another name, before trying again.

Nothing else about the run changes. `--output`, `--no-report` and `--no-pdflatex` still do what they
normally do, and the usual checks on your input still apply.

## Check the result

Before relying on the figures:

1. Read every warning printed while the calculator runs.
2. Check that the portfolio section agrees with your records on the end date in the heading (5 April
    for a full-year report). A holding under a ticker the company no longer uses can mean cgt-calc
    does not know its [ticker rename](extra-data-and-options.md#ticker-renames), or that a merger or
    takeover is missing from your history.
3. Compare **Number of disposals** with your records. Check that **Disposal proceeds** agrees with
    sale amounts on your broker statements and any values used for other disposals.
4. Check that dividends and interest are present when you expect them.
5. Confirm that every relevant account was included once, without overlapping exports.
6. Check that unusual events such as share splits, spin-offs and offshore fund income were handled.

Avoid `--no-balance-check` unless the relevant [broker guide](brokers/index.md) recommends it or you
understand why the check cannot succeed. Disabling it removes one of the checks that can reveal
incomplete transaction history.

For each broker and currency, the balance check compares the cash balance with zero after the last
row of each day, rather than after every row. Exports disagree about the order of a day's rows, and
several write the newest first, so a purchase can appear before the deposit that funded it. Balances
are kept per broker and currency rather than per account, so everything you export from one broker
in one currency shares a single balance.

cgt-calc cannot detect a balance that falls below zero within a day and recovers by the end of it.
If you need to verify intraday funding, check the cash history in your broker's own records for that
day.

A successful run means cgt-calc could parse and calculate the supplied transactions. It does not
prove that the supplied history was complete or that every part of your tax position is supported.

The PDF shows the matching rules applied to each disposal: **SAME DAY**, **BED AND BREAKFAST** and
**SECTION 104**. Check any unexpected matches against HMRC's
[guidance for shares and Capital Gains Tax](https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet).

## Use the figures

The cgt-calc report covers only the supported transactions supplied to the tool. It is not a
complete tax return. Before filing, include gains and losses from other assets, unused losses from
earlier years, and any claims or reliefs.

Use HMRC's guidance to
[check whether the gains must be reported](https://www.gov.uk/capital-gains-tax/work-out-need-to-pay).
If completing Self Assessment, use the
[Capital gains summary form and notes](https://www.gov.uk/government/publications/self-assessment-capital-gains-summary-sa108)
for the relevant tax year. cgt-calc does not work out your final tax bill, map its output to return
boxes or submit a return.

Keep the original exports, supporting statements, command used, warnings and generated report with
the calculation. Follow HMRC's
[Capital Gains Tax record-keeping guidance](https://www.gov.uk/capital-gains-tax/records) for the
required records and retention period.

Run `cgt-calc --help` for the complete list of available options. Use `--verbose` when you need more
detail while investigating a warning or error, and
[`--dump-transactions`](#inspect-parsed-transactions) to see the transactions the calculation
started from.

## Tax at the basic and higher rate

When a full tax year has a **Taxable gain**, the terminal shows the Capital Gains Tax on it twice,
as **Tax at basic rate** and **Tax at higher rate**. The PDF report does not include these figures.

cgt-calc does not know your income, so it cannot tell which rate you pay unless you
[give your income](#estimate-the-tax-from-your-income). The notes under the two figures give the
rates and the Income Tax basic rate limit for that tax year, which is £37,700 for 2025/26:

- **Tax at basic rate** is your tax if your taxable income for that year plus the taxable gain is
    £37,700 or less.
- **Tax at higher rate** is your tax if your taxable income for that year is £37,700 or more. This
    includes additional rate taxpayers.
- If your taxable income is under £37,700 but the taxable gain takes you over it, you pay the basic
    rate on the part of it that fits under £37,700 and the higher rate on the rest. Your tax is then
    between the two figures.

For example, in 2025/26 the rates are 18% and 24%. With a taxable gain of £12,000 the terminal shows
£2,160 and £2,880. If your taxable income is £30,000:

1. £7,700 of the £37,700 is still unused.
2. The first £7,700 of the gain is taxed at 18%, which is £1,386.
3. The other £4,300 is taxed at 24%, which is £1,032.
4. Your tax is £2,418.

Taxable income is your income after the Personal Allowance and other Income Tax reliefs. Follow
HMRC's steps to [work out which rate you pay](https://www.gov.uk/capital-gains-tax/rates). Two
things to know about the limit
([CG21204](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg21204)):

- Gift Aid donations and pension contributions that get tax relief at source raise your limit, so
    more of your gain is taxed at the basic rate. cgt-calc does not know about them.
- If you pay Scottish or Welsh Income Tax, the limit for Capital Gains Tax is still the UK one shown
    in the notes.

The rates changed during two tax years: on 23 June 2010 and on 30 October 2024. For 2010/11 and
2024/25, cgt-calc taxes each gain at the rates for the day of the sale or other disposal. It deducts
the year's losses and the annual exempt amount from the gains taxed at the highest rate first,
whenever in the year the loss arose. This gives the lowest tax, and you may set them against your
gains in whichever way benefits you most
([CG10246](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg10246)).

In 2010/11, gains before 23 June 2010 are taxed at 18% whatever your income and do not count towards
the limit
([Finance (No. 2) Act 2010, Schedule 1, paragraph 18](https://www.legislation.gov.uk/ukpga/2010/31/schedule/1)).
When part of your taxable gain was made before that date, the notes say how much of it was made from
23 June 2010; compare only that amount with the limit. If none of it was made from that date, the
terminal shows one figure, **Tax at 18%**. It shows the same for 2008/09 and 2009/10, when everyone
paid 18%.

The figures are estimates to help you plan, not your final tax bill, and the last note in the
terminal says so. They cover only the **Taxable gain** in the report, at the rates for shares, so
they leave out:

- gains and losses that are not in the files you supplied, including those on other assets such as
    property, which can be taxed at different rates
- losses brought forward from earlier years
- Business Asset Disposal Relief (formerly Entrepreneurs' Relief) and Investors' Relief
- losses on gifts to connected people, shown as **Losses on gifts**, which can only reduce gains on
    disposals to the [same person](brokers/raw.md#gifts-to-anyone-else)

The figures also tax a gain on an
[offshore fund that was ever non-reporting](offshore-funds.md#unsupported-functionality) as a
capital gain, although it can be taxed as income. Work out that disposal outside cgt-calc.

No tax figures are shown when the taxable gain is zero or when you use `--from` and `--to`, even for
a period that covers the whole tax year.

## Estimate the tax from your income

Add `--income` to get one figure in place of the two. Pass your income for the tax year before the
Personal Allowance, without the dividends and interest in the files you supply. For most employees
that is the pay shown on the P60:

```shell
cgt-calc --year 2025 --schwab-file schwab_transactions.csv --income 45000
```

cgt-calc then:

1. Adds the dividends and interest in the report to the income you gave.
2. Deducts the standard Personal Allowance for the year, which is £12,570 for 2025/26.
3. Works out how much of the basic rate limit that taxable income leaves unused.
4. Taxes that much of the gain at the basic rate and the rest at the higher rate.

The terminal shows **Estimated tax** in place of the two figures, with notes that give each step so
that you can check it:

```text
  Taxable gain:        £12,000.00
  Estimated tax:        £2,653.80
Income £45,000.00, plus £1,200.00 dividends and £300.00 interest from these files, less the £12,570 Personal Allowance: taxable income £33,930.00, which leaves £3,770.00 of the £37,700 basic rate limit unused.
Of the taxable gain, £3,770.00 is taxed at 18% and £8,230.00 at 24%.
```

In the figure you pass, include:

- pay from every job, taxable benefits, pensions, rental profit and any other taxable income
- dividends and interest from accounts that are not in the files you supply

Leave out income that is not taxed, such as interest and dividends in an ISA.

cgt-calc cannot know the following, so allow for them in the figure you pass:

- **A Personal Allowance that is not the standard one.** Subtract any Blind Person's Allowance. Add
    any part of your allowance that you transferred with Marriage Allowance. If you received
    Marriage Allowance, change nothing: it reduces your tax, not your taxable income. Before 2016/17
    some older people had a higher age-related allowance; subtract the extra.
- **Gift Aid donations and pension contributions that get tax relief at source.** They raise your
    basic rate limit. Subtract their gross amount: what you paid plus the basic rate tax relief
    added to it. If that takes the figure below your Personal Allowance, the estimate can be too
    high, because cgt-calc cannot count the rest.
- **Other Income Tax reliefs.** Subtract anything else that reduces your taxable income, such as a
    trading loss you claim against your income.
- **Dividends and interest exported after tax.** cgt-calc adds them as the report shows them. If
    your broker's export gives an amount after tax was taken off, as Revolut does for
    [every dividend](brokers/revolut.md#dividends-and-withholding-tax) and Trading 212 does in
    [some cases](brokers/trading212.md#known-limitations), add that tax unless you have put it into
    the report.
- **Dividends before 6 April 2016.** cgt-calc does not add them, because the amount taxed is not the
    amount received (see [Choose the tax year](#choose-the-tax-year)). Add their taxable amount
    yourself. The terminal reminds you when the report has such dividends.

The Personal Allowance shrinks when income is over £100,000. That does not change the estimate: at
that income all of the gain is taxed at the higher rate. For the same reason, when your income
leaves none of the limit unused, the notes give the allowance as "£12,570 at most".

For 2010/11 and 2024/25, when the rates changed during the year, cgt-calc sets the losses, the
annual exempt amount and the unused part of the limit against the gains where they save the most
tax. HMRC confirms you may use them
[in the most beneficial way](https://www.gov.uk/government/publications/changes-to-the-rates-of-capital-gains-tax/capital-gains-tax-rates-of-tax).
The estimate is the tax for the whole year. It is not the adjustment figure that the 2024/25 Self
Assessment return asks for.

`--income` needs a full tax year, so it cannot be combined with `--from` and `--to`. Write it in
whole pounds, such as `45000`. For 2008/09 and 2009/10 it changes nothing: everyone paid 18%
whatever their income, so the terminal still shows one figure, **Tax at 18%**. The same goes for
2010/11 when all of your taxable gain was made before 23 June 2010. The estimate leaves out the same
things as the two figures: see
[Tax at the basic and higher rate](#tax-at-the-basic-and-higher-rate).

## Report part of a tax year (advanced)

Use `--from` and `--to` instead of `--year` to report a period within one UK tax year. For example,
the following reports sales and other disposals on or after 30 October 2024 for the
[HMRC 2024/25 Capital Gains Tax adjustment](https://www.gov.uk/guidance/work-out-your-capital-gains-tax-adjustment-for-the-2024-to-2025-tax-year):

```shell
cgt-calc --from 2024-10-30 --to 2025-04-05 --schwab-file schwab_transactions.csv
```

2010/11 has a similar split: gains on or after 23 June 2010 could be charged at 28% rather than 18%
([CG21000](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg21000)). Use
`--from 2010-04-06 --to 2010-06-22` and `--from 2010-06-23 --to 2011-04-05` to report the two parts.

cgt-calc still reads earlier transactions from the supplied history to establish the cost of the
holding, but only reports the selected period. A purchase after the end date can still be matched to
a sale or other disposal in the report under the 30-day rule if it is in the supplied history.

A period report does not calculate the HMRC adjustment or divide the year's tax-free allowance for
capital gains between periods. Use the **Gain** and **Loss** figures with the full-year report and
HMRC guidance; do not treat its **Taxable gain** as your annual figure. It shows no tax figures; the
full-year report's [tax figures](#tax-at-the-basic-and-higher-rate) already apply both sets of
rates. Its dividend and interest figures are not annual totals either: they include only income
received inside the period, and the dividend section still deducts the full-year dividend allowance.

## Read a file from standard input

Pass `-` in place of a file path to read that file from standard input. Another program can then
supply the file without saving it first, such as a script that converts your broker's export to the
[RAW format](brokers/raw.md):

```shell
python convert_export.py broker_export.xlsx | cgt-calc --year 2024 --raw-file -
```

`-` works with every broker option that takes one file: `--schwab-file`, `--schwab-award-file`,
`--freetrade-file`, `--interactive-brokers-file`, `--revolut-file`, `--vanguard-file`, `--raw-file`
and `--eri-raw-file`. Options that take a directory need a path, and so do `--prices-file`,
`--exchange-rates-file`, `--isin-translation-file` and `--spin-offs-file`. The output options,
`--output` and `--dump-transactions`, need a path as well: cgt-calc does not write the report or the
parsed transactions to standard output.

Reading from standard input changes a few things:

- **Only one option can use it in a run.** If you give `-` to two options, cgt-calc stops before
    reading anything and names them. Pass the other files by path.
- **cgt-calc cannot ask you a question.** If a spin-off needs a source mapping that is not already
    in the spin-offs file, cgt-calc stops and tells you which row to add. See
    [Spin-off source mappings](extra-data-and-options.md#spin-off-source-mappings).
- **Check that the program supplying the input finished successfully.** If it stops early, cgt-calc
    may still produce a report from incomplete data. If you save its output to a file first, pass
    that file to cgt-calc only after the program succeeds.
- **The input must be UTF-8.** The program supplying it must write UTF-8, whatever your system's
    default encoding is.

## Terminal appearance

Colours and emoji are used automatically when the terminal supports them. The standard
[`NO_COLOR`](https://no-color.org/) and `FORCE_COLOR` conventions are respected, plus `NO_EMOJI` to
keep colours but drop emoji.
