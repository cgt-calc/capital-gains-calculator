# Freetrade

cgt-calc reads the CSV export of a Freetrade Activity Feed. Use activity from a taxable General
Investment Account (GIA) only. Do not include activity from an ISA or self-invested personal pension
in this calculation; the CSV has no account-type column that cgt-calc could use to separate it
later.

Freetrade warns that the in-app CSV can omit provider transfers, takeovers and other corporate
actions that may matter for tax. Read [Check for missing activity](#check-for-missing-activity)
before relying on the result.

## Export your activity

1. Open the Freetrade app and make sure you are viewing your GIA.
2. Open **Activity**.
3. Select the download button at the top right of the screen.
4. Confirm the download by selecting **All Activity**.
5. Export the CSV file to your device.

The official
[Freetrade export instructions](https://help.freetrade.io/en/articles/6627908-how-do-i-download-a-csv-export-of-my-activity-feed)
show the current controls. **All Activity** includes the history since the account was opened, which
is the safest range for UK share matching; see [Before you start](../usage.md#before-you-start).

Use the Activity Feed CSV, not a monthly statement PDF or a yearly tax statement. Keep the exported
columns unchanged. You can compare the layout with this
[sanitised example export](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/freetrade/data/transactions.csv).

## Generate the report

For the 2024/25 tax year, run:

```shell
cgt-calc --year 2024 --freetrade-file freetrade.csv
```

The filename does not matter. `--year 2024` means 6 April 2024 to 5 April 2025. Follow
[Generate and Review a Report](../usage.md) to find and check the output.

## Supported activity

The importer recognises these literal values from the CSV's `Type` column:

| Type                 | How cgt-calc handles it                                                              |
| -------------------- | ------------------------------------------------------------------------------------ |
| `ORDER`              | `BUY` and `SELL` share or fund orders, including stamp duty and the Freetrade FX fee |
| `FREESHARE_ORDER`    | A `BUY` of the awarded shares with zero acquisition cost                             |
| `DIVIDEND`           | Gross dividend income and any `Dividend Withheld Tax Amount` as tax at source        |
| `INTEREST_FROM_CASH` | Interest income                                                                      |
| `TOP_UP`             | Cash added to the broker balance                                                     |
| `WITHDRAWAL`         | Cash removed from the broker balance                                                 |

Freetrade classifies
[monthly statements as non-transactions](https://help.freetrade.io/en/articles/6627913-in-the-csv-download-of-my-activity-feed-what-items-are-considered-transactions)
and exposes
[yearly tax statements through Activity](https://help.freetrade.io/en/articles/6627915-can-i-rely-on-the-csv-download-of-my-activity-feed-to-complete-my-tax-return).
Their `MONTHLY_STATEMENT` and `TAX_CERTIFICATE` rows link to documents rather than recording
transactions, so cgt-calc ignores them.

For trades in a foreign instrument currency, cgt-calc uses the price and total already expressed in
the GBP account currency. It records the exported stamp duty and FX fee as costs. Foreign dividend
amounts and their tax at source are converted to GBP using the exported `Base FX Rate`.

### Dates and time zones

Freetrade timestamps every transaction in UTC. cgt-calc converts each one to UK time, GMT in winter
and BST in summer, before taking the date. The tax year boundary and the same-day and 30-day
matching rules all run on UK calendar days, and the boundary always falls inside BST, so a
transaction stamped after 23:00 UTC on 5 April belongs to the following tax year.

### Check for missing activity

Freetrade says its Activity Feed CSV is
[not sufficient by itself for every tax return](https://help.freetrade.io/en/articles/6627915-can-i-rely-on-the-csv-download-of-my-activity-feed-to-complete-my-tax-return).
In particular, provider transfers are not included and some corporate actions, such as a takeover,
may be absent.

If you transferred holdings into or out of Freetrade, or any holding was affected by a takeover,
merger, split or other reorganisation:

1. Compare the CSV with your contract notes, messages and statements.
2. Ask Freetrade support for an
    [activity statement that includes corporate actions](https://help.freetrade.io/en/articles/6416976-what-s-an-activity-statement-and-how-can-i-request-one).
3. Supply any missing transactions through another supported broker export or the
    [RAW format](raw.md), after verifying the dates, costs and UK tax treatment.

The additional Freetrade statement is a source for finding missing activity; cgt-calc does not
promise to import its corporate-action rows directly.

## Known limitations

- Only the transaction types listed above are mapped. Another value stops the import; do not delete
    a financial transaction merely to make the calculation run.
- Current exports add `Stock Split ...` columns, but `STOCK_SPLIT` rows are not yet mapped. The
    columns are accepted so ordinary rows can still be imported; an actual split row stops with
    `Unknown type`. Remove it in a working copy only together with a RAW
    [`STOCK_SPLIT` row](raw.md#share-reorganisations) stating the change to your whole pooled
    holding. Delete the line in a text editor, not a spreadsheet; see
    [`This row has ... columns, not ...`](#this-row-has-columns-not).
- The importer supports a GBP account currency only. Changing the currency text in the CSV would not
    convert its amounts.
- The export has no asset-class column, and the importer does not use one. Every `ORDER` is
    processed as a share or fund acquisition or disposal; do not rely on this path for gilts,
    Treasury bills or another instrument whose tax treatment differs.
- A `FREESHARE_ORDER` is assigned zero acquisition cost. Check whether that treatment is appropriate
    for the way you received the award before relying on its eventual gain.

## Troubleshooting

### `Unknown type`

The Activity Feed can contain queued orders and other items as well as executed transactions.
cgt-calc ignores the two document types described under [Supported activity](#supported-activity).
Identify any other named row in Freetrade before deciding what to do with it:

- a queued order should be replaced by its executed contract note if it later executed, or removed,
    in a text editor, if it was cancelled; and
- a transfer or corporate action may need the extra records described in
    [Check for missing activity](#check-for-missing-activity).

If the row is an executed transaction not covered by [Supported activity](#supported-activity),
first upgrade cgt-calc using the same method you used to install it. If it still fails, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the complete
error and a sanitised copy of the row.

### `Missing columns` or `Unknown columns`

Make sure the file is an unchanged Activity Feed CSV rather than a statement, dividend-only export
or a spreadsheet converted from PDF. cgt-calc accepts both the older and current names for the two
total-amount columns, plus the additional stock-split columns in current exports.

If an unchanged export still fails after upgrading cgt-calc, open a GitHub issue containing:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- the CSV header and a sanitised failing row.

Do not upload an unredacted file: the export contains order identifiers, holdings and other
sensitive financial information.

### `This row has ... columns, not ...`

A row has more or fewer cells than the header line. This can happen after a CSV file is edited or
saved by another program: a spreadsheet program can drop the empty cells at the end of a row when it
saves a CSV file. Download the export again and use it unchanged. If a fresh export still fails,
report it as described under
[`Missing columns` or `Unknown columns`](#missing-columns-or-unknown-columns), with the header and a
sanitised failing row. If you have to remove a row, delete the whole line in a text editor.

The message quotes the whole row. Remove the order identifier and anything else you would not
publish before sharing it.

### `Reached a negative balance`

Check that **All Activity** was selected and that the file contains the top ups, withdrawals and
sales that funded later purchases. A provider transfer can also leave the in-app CSV incomplete;
Freetrade explicitly excludes these from the export.

Do not add a made-up top up or use `--no-balance-check` just to silence the error. Establish the
missing cash or holdings from the original provider and Freetrade records first. Use
`--no-balance-check` only after you understand why the history cannot reconcile and have checked its
completeness another way.

### The portfolio, fees or dividends look wrong

Check the terminal section headed “Portfolio at the end of … tax year” against your statement for 5
April. Compare trade totals, stamp duty, FX fees, gross dividends and withholding with the contract
notes and dividend confirmations. If a real exported row disagrees with the report, open a GitHub
issue with sanitised values from that row.
