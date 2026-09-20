# Interactive Brokers (IBKR)

cgt-calc reads the CSV downloaded from the **Transaction History** page in the Interactive Brokers
Client Portal. It does not read Activity Statements, Flex Queries, PDFs or Excel files.

Use an account whose base currency is GBP. The importer checks the base currency in the CSV and
rejects other currencies. Include taxable accounts only; activity inside an ISA is outside the
capital gains calculation.

## Export the transaction history

Export the complete history rather than only the tax year. Earlier acquisitions can still affect
disposals in the year being calculated, and the cash deposits at the start of the account help the
balance check detect an incomplete export.

1. Sign in to the Interactive Brokers Client Portal in a web browser.
2. Open **Performance & Reports → Transaction History**. Alternatively, open **Menu → Reporting →
    Transaction History**.
3. Select **Custom** as the period and set the start to the account's first transaction. Set the end
    to today, or at least 30 days after the end of the tax year you are calculating.
4. Clear any transaction-type or symbol filters so the export includes trades, income, fees and cash
    movements.
5. Download the table using its CSV export control.

IBKR's current
[Transaction History instructions](https://www.ibkrguides.com/clientportal/transaction-history.htm)
show the menu path, period selector, column configuration and filters.

Keep the downloaded CSV unchanged. The filename does not matter.

Do not substitute an Activity Statement or a Flex Query CSV: their sections and headings are
different even though they are also available from **Performance & Reports**.

You can compare the file's structure with the
[synthetic example CSV](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/interactive_brokers/data/test_basic.csv).

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --interactive-brokers-file U12345678.TRANSACTIONS.20240101.20260505.csv
```

`--year 2025` means 6 April 2025 to 5 April 2026. Follow [Generate and Review a Report](../usage.md)
to find and check the output.

The Transaction History export contains cash deposits and withdrawals, so a complete export should
normally pass the balance check. Do not add `--no-balance-check` simply to bypass an error; first
check the date range and filters as described in [Troubleshooting](#troubleshooting).

## Supported activity

The importer recognises these literal values from the CSV's `Transaction Type` column:

| Transaction type              | How cgt-calc handles it                                      |
| ----------------------------- | ------------------------------------------------------------ |
| `Buy`, `Sell`                 | Share or fund acquisitions and disposals, with commission    |
| `Dividend`, `Payment in Lieu` | Dividend income                                              |
| `Foreign Tax Withholding`     | Tax deducted at source; treated as dividend tax              |
| `Credit Interest`             | Interest income                                              |
| `Deposit`, `Withdrawal`       | Cash movements used by the balance check                     |
| `Other Fee`                   | A charge against a holding, added to its pooled cost         |
| `Adjustment`                  | Cash-balance adjustments such as `FX Translations P&L`       |
| `Forex Trade Component`       | The base-currency net of a currency conversion; balance only |

For a GBP-base account, IBKR reports gross amounts, commissions and net amounts in GBP, so every row
is recorded in GBP whatever the security is priced in. `Price Currency` describes the unit price
alone: when the CSV also supplies an `Exchange Rate`, cgt-calc converts a foreign-currency unit
price to GBP before calculating the acquisition or disposal.

cgt-calc does not treat a currency conversion as a disposal. It applies the `Forex Trade Component`
Net Amount to the cash balance and reports no gain or loss on the conversion itself.

IBKR descriptions for dividends and payments in lieu can put an ISIN in parentheses immediately
after the symbol. cgt-calc uses that identifier when deciding whether a supported double-taxation
treaty applies.

### Tickers and exchange listings

IBKR names a security by the ticker of the listing you traded, so one security can appear under two
tickers: the German line of Rheinmetall AG is exported as `RHMd` alongside `RHM`. cgt-calc pools,
matches and prices holdings by ticker, so it rewrites the aliases it has confirmed to the ticker of
the primary listing, and the report shows one holding rather than two. `RHMd` under `DE0007030009`
is reported as `RHM`. The rewrite is scoped to that ISIN: the same ticker code under another
security is left alone.

Trade rows carry no ISIN, only a company name, so cgt-calc takes the ticker's identity from the
dividend rows of the same export, wherever they appear in the file. A holding whose trades are never
accompanied by a dividend naming the ISIN cannot be recognised this way; add a row listing every
verified ticker for that ISIN to the `--isin-translation-file` cache, as described in
[ISIN to ticker translation](../extra-data-and-options.md#isin-to-ticker-translation).

Only confirmed pairs are rewritten. Where your exports disagree about a security, cgt-calc refuses
rather than guess: an unrecognised second ticker for one ISIN, or one ticker used for two ISINs.
Combining the wrong two holdings, or splitting one, changes the gain.

## Known limitations

- Only the transaction types listed above are mapped. Any other value stops the import with
    `Unknown type`; do not delete the row merely to make the calculation run.
- Cash `Deposit` and `Withdrawal` rows are supported, but transfers of shares or funds between
    accounts are not. Corporate actions such as splits, mergers and spin-offs are not mapped from an
    IBKR export either; record a split or consolidation as a RAW
    [`STOCK_SPLIT` row](raw.md#share-reorganisations) stating the change to your whole pooled
    holding. If the export contains the corporate-action row, it stops the import, so remove it from
    a working copy only when adding the RAW replacement.
- The importer does not read an asset-class field. It has been validated for ordinary share and fund
    trades; do not rely on it to calculate options, futures, bonds, contracts for difference or
    crypto assets.
- Every `Foreign Tax Withholding` row is treated as dividend tax. If IBKR withholds tax from credit
    interest and does not reverse it, cgt-calc records that amount against a placeholder symbol. It
    reduces the cash balance but is not reported as interest tax in the summary.
- The `Account` column is not used to keep separate ledgers. Rows for multiple taxable IBKR accounts
    in one CSV are combined under one broker balance and portfolio.

## Troubleshooting

### `Unexpected base currency`

The CSV says that the account's base currency is not GBP. Changing the text in the file would not
convert any amounts, so use a GBP-base account export instead.

### `Price is in ... but the Exchange Rate column is missing or empty`

A `Buy` or `Sell` row prices the trade in a currency other than GBP but gives no rate to convert it
with, while its gross amount, commission and net amount are in GBP. The price cannot be checked
against the amount, and a guessed rate would put a wrong cost or proceeds in the report, so the
import stops. Re-export the statement with the `Exchange Rate` column filled in for that row — a
`Buy` or `Sell` always needs a price, so clearing `Price` and `Price Currency` only trades this
error for a missing-price one. Other row types are unaffected: only a trade's price feeds the
calculation, so a dividend, fee or interest row keeps a foreign, unconverted `Price Currency` rather
than being refused over a price nothing reads.

### `Couldn't find Transaction History header`

Check that the file came from **Performance & Reports → Transaction History** and is the CSV export.
Activity Statements, Flex Queries and files converted from PDF use different layouts.

### `CSV header mismatch`

The Transaction History page lets you configure its columns, while cgt-calc requires the fields it
uses and rejects fields it does not recognise. Export the standard table without changing its
columns. `Price Currency` and `Exchange Rate` are accepted optional columns.

If an unchanged export still fails, first upgrade cgt-calc using the same method you used to install
it. If the error remains, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- the CSV header and a sanitised failing row.

Do not upload an unredacted file: the report contains account identifiers, holdings and other
sensitive financial information.

### `Unknown type`

Compare the named transaction type with [Supported activity](#supported-activity). The row may
represent an unsupported corporate action or position transfer. Do not remove it unless you replace
it with an equivalent supported transaction whose UK tax treatment you have verified.

### `Reached a negative balance`

Re-export the history from the account's first transaction and make sure no transaction-type or
symbol filter is active. A shortened or filtered export can omit the deposit, sale or dividend that
funded a later transaction.

Use `--no-balance-check` only if you have confirmed why the export cannot reconcile and have checked
its completeness another way.
