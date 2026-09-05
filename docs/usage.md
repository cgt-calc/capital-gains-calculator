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
    through at least 30 days after the report ends is the safest option.

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

The file is written before any calculation starts, and cgt-calc confirms it on **stderr**:

```text
Saved 123 parsed transactions to parsed-transactions.csv
```

That message means the file is complete and closed. It is not the message that ends a successful
run, which is `Done! Report generated successfully.` or, without a PDF,
`Done! Calculations complete (PDF generation skipped).`

### What the file contains

One row per transaction, under a header naming every column. The rows are in the order the
calculation reads them, which is not always the order they happened: some brokers export the newest
row first, and cgt-calc deliberately places some of a day’s rows before others.

The file shows the transactions after cgt-calc has read and prepared your broker exports, and before
the capital gains calculation. It reflects whatever duplicate handling the parser for your broker
supports; the export itself removes nothing, so follow the
[instructions for your broker](brokers/index.md) and do not assume overlapping exports are safe.

Any excess reported income (ERI) records loaded for funds you hold are included as well. They appear
with the action `EXCESS_REPORTED_INCOME` and an `isin` in place of a `symbol`, and carry only a
per-unit `price`: the `quantity` and `amount` are worked out during the calculation, so those
columns are empty here. See the [offshore funds guide](offshore-funds.md).

The calculation has not run yet. Currency conversion, its own checks, share split and other
reorganisation planning, and the tax calculation all come later, so ticker names and quantities can
differ from the ones in the final report.

Numbers are exact rather than rounded for display. `price`, `fees` and `amount` are in the row’s
`currency`. Fees charged in another currency stay in `foreign_fees` under their own currency, and
every amount recorded inside a column keeps the currency stored with it.

Some columns hold several named values at once (`foreign_fees`, `option_contract`,
`written_option_tax`, `capital_adjustments` and `source`). These use JSON, which keeps the names and
their values together in one cell, such as `{"EUR":"2.50"}` for a fee of 2.50 euros. An empty column
means the value was either not set or empty text; the two look the same. Inside a JSON column they
stay apart, as `null` and `""`.

Every transaction cgt-calc loaded is exported, including dates outside the reporting period.
`--year`, `--from` and `--to` still choose the period the report covers.

The file is a snapshot for inspection, not an input format. Its columns are not the seven columns of
the [RAW format](brokers/raw.md), and it cannot be passed back with `--raw-file`.

The `source` column identifies the input file each transaction came from, and includes a row number
when the parser records one. It can therefore contain paths from your own machine next to your
transaction details. Read it before you send it to anyone. Saving the file writes only to the name
you gave; the calculation that follows behaves as it always does, including any data it fetches, as
described in the [privacy notes](privacy.md).

### Where it is written

`--dump-transactions` requires a path and always creates a new file. cgt-calc refuses to overwrite
an existing file, refuses a path whose directory does not exist, and refuses a path the same run
would write later, such as the report or one of the cache files. In each case it reports the problem
and stops without calculating.

If writing fails partway through, cgt-calc stops with an error naming the path. Remove the partly
written file, or choose another name, before trying again.

Nothing else about the run changes. `--output`, `--no-report` and `--no-pdflatex` still do what they
normally do, and the usual checks on your input still apply. If the calculation fails after the file
was saved, the file is kept for you to inspect and the command still exits with an error. A saved
file does not show that the calculation succeeded, or that the history you supplied was complete or
valid, so check the exit status and any errors printed.

## Check the result

Before relying on the figures:

1. Read every warning printed while the calculator runs.
2. Check that the portfolio section agrees with your records on the end date in the heading (5 April
    for a full-year report).
3. Compare **Number of disposals** with your records. Check that **Disposal proceeds** agrees with
    sale amounts on your broker statements and any values used for other disposals.
4. Check that dividends and interest are present when you expect them.
5. Confirm that every relevant account was included once, without overlapping exports.
6. Check that unusual events such as share splits, spin-offs and offshore fund income were handled.

Avoid `--no-balance-check` unless the relevant broker guide recommends it or you understand why the
check cannot succeed. Disabling it removes one of the checks that can reveal incomplete transaction
history.

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
for the relevant tax year. cgt-calc does not calculate the tax payable, map its output to return
boxes or submit a return.

Keep the original exports, supporting statements, command used, warnings and generated report with
the calculation. Follow HMRC's
[Capital Gains Tax record-keeping guidance](https://www.gov.uk/capital-gains-tax/records) for the
required records and retention period.

Run `cgt-calc --help` for the complete list of available options. Use `--verbose` when you need more
detail while investigating a warning or error, and
[`--dump-transactions`](#inspect-parsed-transactions) to see the transactions the calculation
started from.

## Report part of a tax year (advanced)

Use `--from` and `--to` instead of `--year` to report a period within one UK tax year. For example,
the following reports sales and other disposals on or after 30 October 2024 for the
[HMRC 2024/25 Capital Gains Tax adjustment](https://www.gov.uk/guidance/work-out-your-capital-gains-tax-adjustment-for-the-2024-to-2025-tax-year):

```shell
cgt-calc --from 2024-10-30 --to 2025-04-05 --schwab-file schwab_transactions.csv
```

cgt-calc still reads earlier transactions from the supplied history to establish the cost of the
holding, but only reports the selected period. A purchase after the end date can still be matched to
a sale or other disposal in the report under the 30-day rule if it is in the supplied history.

A period report does not calculate the HMRC adjustment or divide the year's tax-free allowance for
capital gains between periods. Use the **Gain** and **Loss** figures with the full-year report and
HMRC guidance; do not treat its **Taxable gain** as your annual figure. Its dividend and interest
figures are not annual totals either: they include only income received inside the period, and the
dividend section still deducts the full-year dividend allowance.

## Terminal appearance

Colours and emoji are used automatically when the terminal supports them. The standard
[`NO_COLOR`](https://no-color.org/) and `FORCE_COLOR` conventions are respected, plus `NO_EMOJI` to
keep colours but drop emoji.
