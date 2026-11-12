# Generate a report

## Before you start

Most users need:

- **cgt-calc installed.** Follow the [installation guide](installation.md) before continuing.
- **A complete transaction history from every relevant account.** Follow the instructions for each
    [supported broker](brokers/index.md). Earlier purchases or employer-share awards can establish
    the cost of shares sold later. Purchases in the 30 days after the report ends can be matched to
    a sale or other disposal inside the report. Exporting from the date the account was opened
    through at least 30 days after the report ends is the safest option. A history reaching back
    before 6 April 2008 may need one change by hand; see
    [shares held before April 2008](extra-data-and-options.md#shares-held-before-april-2008).

Depending on your investments, you may also need:

- **Income kept by a UK accumulation fund, recorded by hand.** An accumulation share class, usually
    with “Acc” or “Accumulation” in its name, adds its income to the value of your units instead of
    paying it to you. That income is taxable, and it counts as an amount spent on your units.
    Neither Vanguard nor Hargreaves Lansdown pays it into your account, and cgt-calc does not count
    it from their exports. Add it in a small RAW file, as described in
    [Income reinvested in accumulation units](brokers/raw.md#income-reinvested-in-accumulation-units),
    and pass it alongside your export. Other brokers have not been checked. With another broker,
    first run cgt-calc without the rows for this income: if the PDF lists a dividend from the fund
    for that distribution under **Interest and Dividends Events**, cgt-calc already counts that
    income, so do not add them.
- **Extra income data for some funds outside the UK.** This can apply whether the fund pays income
    to you or reinvests it. Check the [offshore funds guide](offshore-funds.md).
- **Transfers to or from a spouse or civil partner, recorded by hand.** No broker export marks
    these. Add them in a small RAW file using the
    [`TRANSFER_TO_SPOUSE` or `TRANSFER_FROM_SPOUSE`](brokers/raw.md#transfers-to-a-spouse-or-civil-partner)
    action and pass it alongside your export.

Put every row you record by hand in one RAW file: cgt-calc reads only one, and a second RAW file
stops the run.

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

Pass your broker's export, or the folder that holds its exports. cgt-calc recognises the broker from
each file's contents and prints what it found. For example, with a Charles Schwab export:

```shell
cgt-calc --year 2024 schwab_transactions.csv
```

```text
Recognised schwab_transactions.csv as Charles Schwab transaction history
```

You can combine exports from different brokers in one calculation:

```shell
cgt-calc --year 2024 schwab_transactions.csv trading212/ morgan_stanley/
```

Each broker also has an option that reads its export without recognising it first, such as
`--schwab-file schwab_transactions.csv`. Scripts that use these options keep working. An option that
takes a value can be given only once. cgt-calc stops if you repeat one, because the option holds a
single value and the first would otherwise go unread.

See the [broker instructions](brokers/index.md) for the export format and option for each source. If
your broker is not listed, you can convert its transactions to the generic
[RAW format](brokers/raw.md).

## How cgt-calc recognises each input

cgt-calc reads the column headings of each file you pass, or the top-level field of a JSON file. It
never goes by the filename, so a renamed export or a second download saved as
`Withdrawals Report (1).csv` is still recognised.

A folder stands for the `.csv` and `.json` files directly inside it. Folders inside it are not
searched, and other files, such as PDFs and spreadsheets, are not inputs. Hargreaves Lansdown
contract notes are still read: the Transaction Summary finds each one in its own folder.

cgt-calc prints one line for each file before reading any transaction, for example:

```text
Recognised trading212/from_2024-04-06_to_2025-04-05.csv as a Trading 212 export
Skipped morgan_stanley/Withdrawal Wire Report.csv: a Morgan Stanley report cgt-calc does not read
```

All the files recognised as exports of one broker are read together, as one account, in the same way
as the broker's folder option reads them. Its checks for overlapping exports and repeated
transactions apply as they do with that option. Freetrade, Interactive Brokers, Revolut, Vanguard
and RAW files are read one per run, as their options read one file, and so are the Charles Schwab
Equity Awards export and each of the two Morgan Stanley reports.

A complete Charles Schwab Equity Awards transaction export is read as an account of its own, as with
`--schwab-award-file`. cgt-calc does not check it against the Charles Schwab transaction history, so
a transaction recorded in both files is counted twice. The run warns when you pass both; see
[Combining a main history with a complete export](brokers/schwab.md#combining-a-main-history-with-a-complete-export).

cgt-calc stops before reading any transaction, and lists every problem, when an input:

- **is not recognised.** The file is from a report or broker cgt-calc does not read, its column
    headings were changed, or it has no heading row. To see which headings the reader expected, pass
    the file with its broker's option.
- **matches more than one format**, such as two exports pasted into one file. Keep one export per
    file, or pass the file with the option for the broker it came from.
- **is a file cgt-calc reads with another option**: ERI data, an ISIN translation file, a share
    prices file, the exchange rates cache or a spin-offs file. The message names the option to use.
    If the file is in a folder you pass, move it out of that folder, even when you also give it with
    its option. A transaction file written by `--dump-transactions` is named too: cgt-calc does not
    read it back.
- **is empty, a spreadsheet workbook, or text in an encoding cgt-calc does not read.** Exports must
    be UTF-8 text, except a Hargreaves Lansdown Transaction Summary, which can also be in the
    Windows-1252 encoding HL uses.
- **is a PDF that is not in the folder of a Hargreaves Lansdown Transaction Summary you passed.** A
    PDF in that folder is accepted without cgt-calc looking inside it, and read as a contract note
    if a row of the summary names it.
- **repeats a file** that another argument already gives, such as a folder and a file in it.
- **gives an input that an option already gives**, such as a Trading 212 export next to
    `--trading212-dir`. Pass every file for that input with the option, or every one without it. If
    cgt-calc reads one such file per run, as with RAW files, pass only the export that holds the
    whole history, with the option or without it. Several Charles Schwab transaction histories go
    without `--schwab-file`, which reads one file.

A file passed with an option is read as that broker's without being recognised. You can combine
options and plain inputs for different brokers, or for the two Charles Schwab inputs:

```shell
cgt-calc --year 2024 --schwab-file schwab_transactions.csv equity_awards.json
```

A filename that starts with `-` needs `--` before it, as everything after `--` is read as a file:

```shell
cgt-calc --year 2024 -- -my-export.csv
```

## Find the results

cgt-calc prints a summary to the terminal and, by default, writes the detailed PDF report to
`out/calculations.pdf`.

If the report includes excess reported income in a foreign currency from an offshore fund whose
distribution date hasn't come yet, its figures are not final and cgt-calc writes no PDF. The
terminal shows the estimated figures and the date from which to run cgt-calc again; see
[Exchange rate](offshore-funds.md#exchange-rate).

The PDF opens with the **Overall** summaries, then has a **Capital Gains Events** section and an
**Interest and Dividends Events** section when there is income to show. In the events, a table under
each acquisition or disposal shows how it was matched: one row per rule, with the pool left after
it. Interest is grouped monthly per broker, even if your broker pays it daily.

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

Use `--no-report` instead of `--output` to print the terminal summary without creating a PDF:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --no-report
```

The PDF shows holding names written with Latin, Greek or Cyrillic letters, digits, common
punctuation and currency signs. If a name in your files uses other characters, such as Chinese, Thai
or emoji, cgt-calc prints the summary and then stops without creating the PDF, naming the
characters. To get a PDF, change the name in your input file, or use `--no-report` to skip it.

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

### Labels in the PDF

The PDF shows the matching rules applied to each disposal. A sale or other disposal is matched first
with shares bought or received on the same day (**SAME DAY**), then with shares bought or received
in the following 30 days (**BED AND BREAKFAST**), then with older shares in your pooled holding
(**SECTION 104**). Shares that arrive through a company reorganisation, such as a spin-off, are not
matched under the same-day or 30-day rules: they join your Section 104 holding at their share of the
original cost. Check any unexpected matches against HMRC's
[guidance for shares and Capital Gains Tax](https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet).

An acquisition is labelled too. Shares matched to a disposal, or to a transfer to a spouse or civil
partner, in the previous 30 days read **BED AND BREAKFAST**. The rest read **SECTION 104**,
including shares matched on the same day: only the disposal or transfer reads **SAME DAY**.

Three other labels are not matching rules:

- **SPIN OFF:** the original holding's pooled cost before and after part of that cost moved to the
    spun-off shares. See
    [how the cost is split at a spin-off](extra-data-and-options.md#missing-share-prices).
- **EXCESS REPORTED INCOME:** the pooled cost of a fund holding before and after the fund's excess
    reported income was added to it. See the [offshore funds guide](offshore-funds.md).
- **OPTION:** the grant of an option you wrote, with its allowable cost and gain or loss. See
    [written equity options](brokers/schwab.md#written-equity-options).

A [transfer to a spouse or civil partner](brokers/raw.md#transfers-to-a-spouse-or-civil-partner) is
matched in the same way as a disposal, so a purchase on the same day or in the following 30 days can
make the base cost that passes to the recipient differ from the average cost of your pooled holding.
Its lines carry the same labels. Each line shows the shares matched under that rule and the base
cost they pass on. A **BED AND BREAKFAST** line also gives the date the matched shares were bought
or received. The lines add up to the base cost stated above them, apart from penny rounding.

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

The usual input checks and the `--output` and `--no-report` options still apply.

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
python convert_export.py broker_export.xlsx | cgt-calc --year 2024 -
```

As with a file, cgt-calc recognises what standard input holds. It reads an export from any broker
whose option takes one file: Charles Schwab (either export), Freetrade, Interactive Brokers,
Revolut, Vanguard and RAW. Hargreaves Lansdown, Morgan Stanley, Sharesight and Trading 212 exports
are read from files only, and so is a Charles Schwab history read together with other exports of it.

`-` also works with every broker option that takes one file: `--schwab-file`, `--schwab-award-file`,
`--freetrade-file`, `--interactive-brokers-file`, `--revolut-file`, `--vanguard-file` and
`--raw-file`. It also works with `--eri-raw-file`, for [custom ERI data](custom-eri-data.md).
Options that take a directory need a path, and so do `--prices-file`, `--exchange-rates-file`,
`--isin-translation-file` and `--spin-offs-file`. The output options, `--output` and
`--dump-transactions`, need a path as well: cgt-calc does not write the report or the parsed
transactions to standard output.

Reading from standard input changes a few things:

- **Only one input can use it in a run.** If you give `-` twice, or as an input and to an option,
    cgt-calc stops before reading anything and names them. Pass the other files by path.
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
