# Sharesight

cgt-calc reads two Sharesight reports: **All Trades** for acquisitions and disposals, and **Taxable
Income** for dividends and tax deducted from them. This is useful when Sharesight already combines
activity from several brokers into one GBP portfolio.

Use a Sharesight portfolio whose base currency is GBP. The importer treats local income and the
portfolio value of foreign-exchange trades as GBP.

## Export the reports

### All Trades

Export all trades from the portfolio's first transaction. Earlier acquisitions can still affect
disposals in the tax year you are reporting; see [Before you start](../usage.md#before-you-start).

1. Open **Tax** and select **All Trades Report**.
2. Set the date range to **Since inception**.
3. Select **Do not group (Holdings)**.
4. Export the report to a spreadsheet or Google Drive. Do not use the PDF export.
5. Open the exported spreadsheet and save or download the sheet containing the All Trades table as a
    CSV file.

See Sharesight's current
[All Trades Report instructions](https://help.sharesight.com/uk/trades_report/) for the report
controls.

### Taxable Income

1. Open the portfolio's **Taxable Income Report**.
2. Set the date range to the tax year you are calculating.
3. Export the report to a spreadsheet or Google Drive. Do not use the PDF export.
4. Open the exported spreadsheet and save or download the report sheet as a CSV file.

Sharesight recommends checking its income figures against your dividend statements because the
report relies on third-party data. Its
[Taxable Income Report instructions](https://help.sharesight.com/uk/taxable_income/) explain the
available periods and exports.

## Prepare the directory

Keep both CSV files directly in one directory, for example:

```text
sharesight/
├── All Trades Report.csv
└── Taxable Income Report.csv
```

Use the filenames shown above when saving the CSV files. cgt-calc does not read the original `.xlsx`
files or search subdirectories.

cgt-calc reads every CSV in the directory whose name starts with `All Trades Report` or
`Taxable Income Report`. An All Trades report exported **Since inception** repeats the trades of
every earlier export, so move an older All Trades file out of the directory when you add a new one.
cgt-calc stops when two files hold the same transaction; see
[Two files in the directory hold this transaction](#two-files-in-the-directory-hold-this-transaction).

Do not rename or delete columns when converting the worksheets. In addition to the legacy headings,
cgt-calc accepts aliases including `Market Code`, `Qty`, `Instrument Currency` and `Exch. Rate`. You
can compare the legacy structure with the
[sanitised example reports](https://github.com/cgt-calc/capital-gains-calculator/tree/main/tests/sharesight/data/inputs).

## Generate the report

For the 2024/25 tax year, run:

```shell
cgt-calc --year 2024 --sharesight-dir sharesight/ --no-balance-check
```

`--year 2024` means 6 April 2024 to 5 April 2025. `--no-balance-check` is needed because these two
reports do not contain the complete cash activity required to reconcile a broker balance. Follow
[Generate and Review a Report](../usage.md) to find and check the output.

## Supported activity

The Sharesight importer currently handles:

| Report         | Included activity                                                                                               |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| All Trades     | `Buy` and `Sell` rows, including Sharesight foreign-exchange rows, and equity grants entered as described below |
| Taxable Income | Local and foreign dividend payments, including `Tax Deducted` and `Foreign Tax Deducted` amounts                |

### Equity grants

cgt-calc identifies an equity grant through the trade comment. To record a vest:

1. Record it as a `Buy` in Sharesight.
2. Enter the market value per share on the vest date as the price.
3. Include `Stock Activity` anywhere in the trade comment.

cgt-calc then treats the row as stock activity rather than a cash purchase. A `Sell` row carrying
that marker is rejected. If the CSV has no `Comments` column, cgt-calc warns that it cannot detect
these markers.

## Known limitations

- [`Opening Balance`](https://help.sharesight.com/opening-balance/) trades are not supported.
    Sharesight uses them when the original buy and sell records are unavailable, but cgt-calc needs
    the original acquisition dates and costs for UK share matching.
- `Split`, `Consolidation` and `Bonus` rows are not supported. They stop the import with an unknown
    action error. Do not delete such a row on its own to make the calculation run: later quantities
    and gains would be wrong. For a split or consolidation, remove the row in a working copy only
    together with a RAW [`STOCK_SPLIT` row](raw.md#share-reorganisations) stating the change to your
    whole pooled holding.
- Cash deposits, withdrawals and balances are not imported, which is why `--no-balance-check` is
    required.
- Taxable Income sections other than local and foreign dividend payments are not imported.
- If a newer All Trades report gives a non-zero brokerage charge in a currency different from the
    trade currency (GBP for foreign-exchange rows), the import stops. That fee cannot yet be
    represented exactly from this report without risking an incorrect allowable cost.
- Overlapping reports are not merged. cgt-calc stops when two files hold a transaction that reads
    the same in both. It cannot detect a transaction that the two files state differently, such as a
    trade whose quantity or comment you edited in Sharesight between two exports, and counts that
    one twice. Do not keep an older export of a report beside a newer one.

## Troubleshooting

### No transactions are detected

Check that you converted the spreadsheet worksheet to CSV: `.xlsx` files are not read. Confirm that
both files are directly inside the directory passed to `--sharesight-dir`, and that their filenames
start with `All Trades Report` and `Taxable Income Report`. Filenames are matched
case-insensitively; additional text after `Report` and uppercase `.CSV` extensions are accepted.

### `Missing expected columns`

cgt-calc found the report's header row but not every column it needs. This happens when headings
were edited during conversion, or when Sharesight has changed the report format.

First upgrade cgt-calc using the same method you used to install it and try again. If the error
remains, open a [GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new)
with:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- the CSV header and a sanitised failing row.

Do not upload an unredacted report: it can contain holdings and other sensitive financial
information.

### `Unknown action`

Compare the named action with [Known limitations](#known-limitations). Do not remove the row unless
you replace it with an equivalent supported transaction whose UK tax treatment you have verified.

### `Two files in the directory hold this transaction`

cgt-calc found the same transaction in two CSV files and stopped, because reading both would count
it twice. The error shows the transaction once for each file, with the file and the row it was read
from. It reports the first one it finds; the two files can share others.

Open both files and compare them:

- **One file is a copy of the other**, such as a report downloaded twice. Move one of them out of
    the directory.
- **Both are All Trades reports for the same portfolio.** Each export starts at the portfolio's
    first trade, so the newer one already holds the trades of the older one as Sharesight records
    them now. Move the older file out of the directory.
- **Both are Taxable Income reports whose date ranges overlap.** Keep both files until you have
    exported reports whose date ranges do not overlap, then use those instead.
- **The two rows are separate transactions that only look the same.** Both must be counted. Do not
    delete either row, and do not run cgt-calc on the two files separately: either would leave
    transactions out of the calculation. Work on copies of the two files and move one of the two
    rows to the line directly below the other, so that both are in the same file. cgt-calc counts
    every row of a list, including rows that match. A row placed anywhere else can fall outside the
    list it belongs to and be left out.

### The portfolio or dividend totals look wrong

Check that the All Trades export starts at the portfolio's first transaction, the Taxable Income
export covers the tax year being calculated, and both came from the same GBP portfolio. Compare
dividends and deducted tax with your broker statements, and review the terminal's final portfolio
for missing or excessive holdings before relying on the tax report.
