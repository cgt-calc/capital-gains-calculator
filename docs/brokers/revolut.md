# Revolut

cgt-calc reads the CSV account statement exported by Revolut Invest. One export covers the whole
history of the account, so a single file is enough; cgt-calc takes one file rather than a directory.

Export the General Investment account only. Do not include ISA activity: income and capital gains
from
[investments in an ISA do not need to be declared](https://www.gov.uk/individual-savings-accounts/how-isas-work).
The CSV does not identify the account type, so cgt-calc cannot separate the accounts later.

## Export your account statement

Follow the
[Revolut account statement instructions](https://help.revolut.com/help/wealth/stocks/getting-started-with-trading/managing-your-trading-account/trading-statements/accessing-my-trading-statements-and-reports/):

1. In Revolut, open **Invest**.
2. Select **More**, then **Documents**.
3. Choose **Stocks**, select your **General Investment** account, then **Account statement**.
4. Choose the **CSV** format rather than PDF.
5. Set the Period to **All time**.
6. Generate the CSV file.

Exporting the statement for all time is the safest option; see
[Before you start](../usage.md#before-you-start) for why transactions from earlier tax years may be
needed.

Keep the exported columns unchanged.

### Check the investments in the export

Check each holding's product type in Revolut. The CSV does not identify whether a ticker is a
company share, fund, bond or another product. cgt-calc treats every `BUY` and `SELL` row as a share
transaction and cannot detect when another tax treatment is needed.

- For an ETF or other fund, check whether it is an offshore fund and follow the
    [offshore-fund guide](../offshore-funds.md).
- cgt-calc does not identify or validate the tax treatment of bonds, ETNs, ETCs or other
    instruments. Calculate them outside cgt-calc.

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --revolut-file revolut.csv
```

The filename does not matter. `--year 2025` means 6 April 2025 to 5 April 2026. Follow the
[report guide](../usage.md) to find and check the output.

## Supported activity

The importer recognises these exact values from the CSV's `Type` column:

| Type                             | How cgt-calc handles it                                                                                    |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `BUY - MARKET`, `BUY - LIMIT`    | Share acquisitions                                                                                         |
| `SELL - MARKET`, `SELL - LIMIT`  | Share disposals                                                                                            |
| `DIVIDEND`                       | Dividend income at the amount received, after foreign tax; see [Dividends](#dividends-and-withholding-tax) |
| `DIVIDEND TAX (CORRECTION)`      | A later change to the tax on a dividend; see [Tax corrections](#tax-corrections)                           |
| `STOCK SPLIT`                    | Changes the share count without changing the existing pooled cost                                          |
| `CUSTODY FEE`                    | Cash leaving the broker balance                                                                            |
| `CASH TOP-UP`, `CASH WITHDRAWAL` | Cash added to or removed from the broker balance                                                           |

cgt-calc also ignores these two exact `Type` values because moving cash or holdings between Revolut
entities is not a disposal or acquisition:

- `TRANSFER FROM REVOLUT BANK UAB TO REVOLUT SECURITIES EUROPE UAB`
- `TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB`

The `Price per share` and `Total Amount` columns each carry a currency prefix, such as `USD 134.50`,
which must match the `Currency` column of the same row. cgt-calc recalculates the price as
`Total Amount` divided by `Quantity` instead of using the rounded price in the CSV, so the cost or
proceeds always agree with the total Revolut recorded. The statement has no separate fee column, so
anything Revolut already included in the total, rather than charging as a separate `CUSTODY FEE`
row, is part of the cost or proceeds.

The `FX Rate` column is not used. cgt-calc converts foreign currency amounts using the
[monthly exchange rates it downloads](../extra-data-and-options.md#exchange-rates), not the rate
shown by Revolut.

A `STOCK SPLIT` row states the number of shares added, not the new total holding, and its
`Total Amount` is zero. cgt-calc treats it as a share reorganisation: nothing is bought or sold, and
the pooled cost is unchanged and spread over the new share count
([HMRC CG51805](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51805)). The ratio
also adjusts share matching.

### Dates and time zones

Revolut timestamps every transaction in UTC. cgt-calc converts each one to UK time, GMT in winter
and BST in summer, before taking the date. The tax year boundary and the same-day and 30-day
matching rules all run on UK calendar days, and the boundary always falls inside BST, so a
transaction stamped at or after 23:00 UTC on 5 April belongs to the following tax year.

## Known limitations

- Only the types listed above are mapped. Any other value stops the import with `Unknown action`; do
    not delete a financial transaction merely to make the calculation run, because the missing
    activity could make the resulting holdings and gains incorrect.
- A Revolut `STOCK SPLIT` row states only the change in that account. If you also hold the ticker
    through another input, cgt-calc stops. Follow the error's instructions to provide a complete
    [`STOCK_SPLIT` row](raw.md#share-reorganisations) for the whole holding, or calculate the event
    outside cgt-calc.
- cgt-calc also stops if Revolut booked another transaction for the ticker earlier on the day of a
    split. The export does not say whether that transaction's share count is before or after the
    split, and a RAW `STOCK_SPLIT` row cannot resolve this. Work out the day's transactions and
    split outside cgt-calc.
- A custody fee reduces the broker balance but is not treated as an allowable cost against a gain.
- The statement covers the investment account only. Commodities and interest on savings products are
    not part of this export.

### Dividends and withholding tax

A `DIVIDEND` row gives the amount you received, after any foreign tax such as US tax on a US share.
The CSV does not give the tax withheld, and cgt-calc cannot work it out: the rate depends on the
company, which the CSV names only by ticker, and on the tax status Revolut holds for you. cgt-calc
records the amount received and prints a warning when the file contains a dividend.

HMRC calculates foreign income before direct foreign tax is deducted
([HMRC guidance](https://www.gov.uk/hmrc-internal-manuals/international-manual/intm165030)). The
report's dividend income is therefore too low by the tax withheld, and that tax does not appear as
tax at source.

To put the tax into the report:

1. For each dividend in the tax year, open **Invest**, then **Portfolio** → **Transactions** →
    **Dividend**. Revolut shows the withholding tax in the transaction details
    ([Revolut dividend guide](https://help.revolut.com/help/wealth/stocks/corporate-events/receiving-dividends/)).

2. Write a [RAW file](raw.md) with two rows for each dividend that had tax withheld. If cgt-calc
    warned about a dividend tax correction for the ticker, read [Tax corrections](#tax-corrections)
    first. Give both rows the ticker, the currency and the [UK date](#dates-and-time-zones) of the
    `DIVIDEND` row. Enter the tax as a positive `DIVIDEND` and as a negative `DIVIDEND_TAX`. For USD
    6.67 withheld from a QCOM dividend on 18 December 2025:

    ```csv
    date,action,symbol,quantity,price,fees,currency
    2025-12-18,DIVIDEND,QCOM,1,6.67,0,USD
    2025-12-18,DIVIDEND_TAX,QCOM,1,-6.67,0,USD
    ```

    Both rows are needed, because cgt-calc matches a RAW `DIVIDEND_TAX` only to a RAW `DIVIDEND`,
    not to the dividend in the Revolut CSV.

3. Pass both files:

    ```shell
    cgt-calc --year 2025 --revolut-file revolut.csv --raw-file revolut-dividend-tax.csv
    ```

cgt-calc adds the RAW `DIVIDEND` to the dividend in the export, which makes it the dividend before
tax, and reports the `DIVIDEND_TAX` as its tax at source. The two rows cancel, so the `Unknown`
balance that the RAW file adds to the terminal's **Final balance** list is zero. The warning is
still printed, because cgt-calc cannot tell that you have added the tax.

Check the terminal's **Dividends** list printed after **Final balance**. For each ticker, the value
after `excluding ... taxed at source` should be the total of the tax you noted in step 1, and the
amount before it should be the ticker's `DIVIDEND` rows for the tax year plus the RAW `DIVIDEND`
amounts you entered.

With the tax in the report, cgt-calc works out double taxation treaty relief as for any
[RAW dividend](raw.md#known-limitations), and the PDF report shows it beside the dividend. Unless
cgt-calc knows the ticker's ISIN, it takes the company's country from the currency. A `USD` dividend
is treated as US, which is wrong for a company outside the US that pays in dollars, so
[check the country](../extra-data-and-options.md#income-reported-in-more-than-one-currency) for
those. Where cgt-calc cannot tell the country, or the tax does not match the treaty rate, it prints
a warning and leaves the relief out. If the warning says that the double taxation treaty does not
match, first check that the RAW rows have the date of the dividend.

If you do not add the tax this way, add it to your dividend income yourself and, if you use
[`--income`](../usage.md#estimate-the-tax-from-your-income), to the figure you pass.

#### Tax corrections

A `DIVIDEND TAX (CORRECTION)` row changes the tax on a dividend after it was paid. These rows
usually come in pairs of the same amount, one negative and one positive, which cancel and change
nothing. cgt-calc counts any other correction as tax at source on the ticker's dividend of the same
day or, when there is none, on its only dividend in the 30 days before or the 5 days after. Where it
finds no such dividend, or more than one, it prints a warning and leaves the correction out of the
report.

cgt-calc warns about a correction that nothing cancels and names it. Revolut has paid a dividend in
full and taken the tax afterwards with such a correction, so the `DIVIDEND` row before it may not be
short of the tax that Revolut shows. For that dividend, the amount to enter in the two RAW rows, or
to add yourself, is the dividend before tax less the `DIVIDEND` amount in the CSV. Work out the
dividend before tax as the dividend per share that the company declared, which its investor pages
give, times the shares you held on the day before the ex-dividend date. If nothing is left, as when
the dividend was paid in full, enter no rows for it: the correction is already its tax at source.

## Troubleshooting

### `Unknown action`

Revolut might add a new transaction type. First, upgrade cgt-calc using the same method you used to
install it and try again. If the error remains, open a GitHub issue containing:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- a sanitised copy of the failing row.

Do not upload an unredacted account statement: it contains your holdings and other financial
information.

### `CSV header mismatch`

The message names the columns that are missing and any it did not expect. Make sure the file is an
unchanged CSV account statement rather than a PDF converted to a spreadsheet, and that the eight
columns are still `Date`, `Ticker`, `Type`, `Quantity`, `Price per share`, `Total Amount`,
`Currency` and `FX Rate`. Their order does not matter, as each row is read by column name.

### `missing header row`

cgt-calc warns when the first row does not look like the column names, and then assumes the columns
are in the standard order. Re-export the statement rather than relying on the guess, because a file
whose columns were reordered or removed would be read incorrectly.

### `Reached a negative balance`

Check that the period was set to **All time** so that the top ups, transfers and sales that funded
later purchases are all present. Do not add a made-up top up to silence the error; establish the
missing cash or holdings from your Revolut records first.

If the error names the broker `Unknown`, the balance belongs to a RAW file. In the
[rows that add dividend tax](#dividends-and-withholding-tax), give both rows of a dividend the same
date.

Revolut can report a cash balance of `-0.01` even when your transaction history is complete. If your
records account for every transaction and the shortfall is a hundredth of a unit or so, rerun with
`--no-balance-check`. This disables the check for every input in the run, so compare each amount in
the terminal's **Final balance** list with your broker records.

### The portfolio or dividends look wrong

Check the terminal section headed “Portfolio at the end of … tax year” against your Revolut holdings
on 5 April. Compare each dividend with the amount in the CSV, then add the tax withheld as
[described above](#dividends-and-withholding-tax). If a real exported row disagrees with the report,
open a GitHub issue with sanitised values from that row.
