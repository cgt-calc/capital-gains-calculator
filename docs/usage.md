# Generate a report

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

An option that takes a value can be given only once. cgt-calc stops if you repeat one, because the
option holds a single value and the first would otherwise go unread.

See the [broker instructions](brokers/index.md) for the correct option and export format for each
source. If your broker is not listed, you can convert its transactions to the generic
[RAW format](brokers/raw.md).

## Find the results

cgt-calc prints a summary to the terminal and, by default, writes the detailed PDF report to
`out/calculations.pdf`.

The PDF has a **Capital Gains Events** section, an **Interest and Dividends Events** section when
there is income to show, and **Overall** summaries at the end. Interest is grouped monthly per
broker, even if your broker pays it daily.

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

The check stops the run if a cash balance is negative at the end of a day. It combines accounts from
the same broker in the same currency. It cannot detect a balance that goes negative and recovers
within the day; check your broker's cash history if you need to verify that.

A successful run means cgt-calc could parse and calculate the supplied transactions. It does not
prove that the supplied history was complete or that every part of your tax position is supported.

The PDF shows the matching rules applied to each disposal. A sale or other disposal is matched first
with shares bought or received on the same day (**SAME DAY**), then with shares bought or received
in the following 30 days (**BED AND BREAKFAST**), then with older shares in your pooled holding
(**SECTION 104**). Shares that arrive through a company reorganisation, such as a spin-off, are not
matched under the same-day or 30-day rules: they join your Section 104 holding at their share of the
original cost. Check any unexpected matches against HMRC's
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

For a full tax year with a taxable gain, the terminal also shows estimates of the tax on it. They
are not your final tax bill. See [Tax estimates](tax-estimates.md) for how to read them,
[what they leave out](tax-estimates.md#what-the-estimates-leave-out) and how to
[estimate the tax from your income](tax-estimates.md#estimate-the-tax-from-your-income) with
`--income`.

Keep the original exports, supporting statements, command used, warnings and generated report with
the calculation. Follow HMRC's
[Capital Gains Tax record-keeping guidance](https://www.gov.uk/capital-gains-tax/records) for the
required records and retention period.

Run `cgt-calc --help` for the complete list of available options. Use `--verbose` when you need more
detail while investigating a warning or error, and
[`--dump-transactions`](#inspect-parsed-transactions) to see the transactions the calculation
started from.

## Inspect parsed transactions

Use `--dump-transactions` to save the parsed transactions as CSV before calculation. Use it to
compare exports or check what cgt-calc read from a broker file:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --dump-transactions parsed-transactions.csv
```

cgt-calc confirms the saved file on **stderr**, then continues with the calculation:

```text
Saved 123 parsed transactions to parsed-transactions.csv
```

This confirms that the CSV is complete, not that the calculation succeeded. If calculation fails,
the CSV remains available and the command exits with an error.

### What the file contains

The file has a header and one row per loaded transaction, including dates outside the report's
`--year` or `--from`/`--to` period. Rows follow the order supplied to the calculation, which may not
be chronological. Ticker names and quantities can differ from the final report.

The dump removes no duplicates beyond those handled by your broker's parser. Follow your
[broker's instructions](brokers/index.md); do not assume overlapping exports are safe.

Numbers are exact rather than rounded for display. `price`, `fees` and `amount` are in the row’s
`currency`, and fees charged in another currency stay in `foreign_fees` under their own currency.
`market` names the market a `symbol` belongs to when cgt-calc knows it, which is `US` for every
Charles Schwab row. It is empty otherwise.

Columns holding several named values, such as `foreign_fees` and `source`, use JSON to keep them in
one cell: `{"EUR":"2.50"}` is a fee of 2.50 euros. Outside JSON, unset values and empty text both
appear as empty cells.

Excess reported income (ERI) records loaded for funds you hold appear with the action
`EXCESS_REPORTED_INCOME` and an `isin` in place of a `symbol`. They carry only a per-unit `price`,
so `quantity` and `amount` stay empty until the calculation works them out. See the
[offshore funds guide](offshore-funds.md).

The dump is not [RAW format](brokers/raw.md) and cannot be used with `--raw-file`. Its `source`
column includes input paths and, where recorded, row numbers. Check these before sharing the file;
see the [privacy notes](privacy.md).

### Where it is written

Choose a new filename in an existing directory, separate from the report and cache paths. cgt-calc
refuses existing files, missing directories and paths the run would write later. If writing fails,
the error names the path; remove any partly written file or choose another name before retrying.

The usual input checks and `--output`, `--no-report` and `--no-pdflatex` options still apply.

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
full-year report's [tax figures](tax-estimates.md#tax-at-the-basic-and-higher-rate) already apply
both sets of rates. Its dividend and interest figures are not annual totals either: they include
only income received inside the period, and the dividend section still deducts the full-year
dividend allowance.

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
