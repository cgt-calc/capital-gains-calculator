# Vanguard

cgt-calc reads a CSV copy of a Vanguard UK **Client Transactions Listing**. Use the worksheet for a
taxable
[General Account](https://www.vanguardinvestor.co.uk/investing-explained/general-investment-account)
only. Do not include a Stocks and Shares ISA, Junior ISA or Personal Pension: the CSV does not give
cgt-calc an account type it can use to exclude tax-advantaged activity.

The importer does not read the Excel workbook directly. Keep that original workbook for your
records, but save the complete General Account worksheet as a CSV for cgt-calc.

## Export the complete history

Export enough history to establish the cost of every investment owned or sold during the tax year.
The safest range starts with the account's first transaction and ends today, or at least 30 days
after the end of the tax year you are calculating, because later acquisitions can affect UK share
matching. See [Before you start](../usage.md#before-you-start).

1. Sign in to Vanguard UK and open **Documents**, then the **Report Generator**.
2. Request a **Client Transactions Listing** for the required period.
3. Download the completed workbook. If it contains several account worksheets, select only the
    taxable General Account.
4. Save the entire worksheet as a comma-separated, UTF-8 CSV. Keep both the **Cash Transactions**
    and **Investment Transactions** tables, including their original headings.

Do not substitute an annual statement or Consolidated Tax Certificate. Vanguard says its
[Consolidated Tax Certificate](https://www.vanguardinvestor.co.uk/need-help/answer/what-is-a-consolidated-tax-certificate)
summarises dividends and tax deducted at source, but does not provide a Capital Gains Tax
calculation.

You can compare the expected worksheet structure with the
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/vanguard/data/cash_investment_report.csv).
The filename does not matter. Both comma- and tab-separated files are accepted, including the byte
order mark written by the **CSV UTF-8** format in Excel.

### Why both tables matter

The Cash Transactions table is the primary record when both tables are present. Its `Amount` is the
cash movement used by the balance check. Ordinary `Bought` and `Sold` cash rows already state their
quantities. The important exception is wording such as:

```text
Selling of account investments for payment of Account Fee for the period ...
```

That cash row omits the investment and quantity, while the matching Investment Transactions row
contains them. The importer joins the rows only when `Details` and `TransactionDetails` contain
exactly the same text and both either reverse that activity or neither does. Where several
investment rows qualify, it picks the one nearest the cash row's own date and skips the join
entirely if none qualifies. It copies the quantity and derives a unit price from the cash amount,
but it does not copy the investment symbol and still classifies the result as a cash movement rather
than a disposal.

Keep the complete, unchanged worksheet so that enrichment is not lost and ticker-renaming events
remain available. See [Automatic sales to pay fees](#automatic-sales-to-pay-fees).

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --vanguard-file vanguard.csv
```

`--year 2025` means 6 April 2025 to 5 April 2026. Pass the CSV, not the original Excel workbook.
Follow [Generate and Review a Report](../usage.md) to find and check the output.

Run with the balance check enabled. Do not add `--no-balance-check` merely to bypass an error; first
reconcile the file and any unsupported activity as described under
[Troubleshooting](#troubleshooting).

## Supported activity

The importer recognises these forms in the `Details` or `TransactionDetails` column:

| Exported text or pattern                                           | How cgt-calc handles it                             |
| ------------------------------------------------------------------ | --------------------------------------------------- |
| `Bought <quantity> <investment> (<ticker>)`                        | A GBP purchase                                      |
| `Sold <quantity> <investment> (<ticker>)`                          | A GBP disposal                                      |
| `DIV: <ticker>.<market>... @ <currency> <rate>`                    | Dividend income using the GBP cash amount           |
| `Reversal of` a dividend, interest or cash movement                | The same activity, using the exported signed amount |
| `Cash Account Interest`                                            | GBP interest income                                 |
| Supported deposit, transfer and payment phrases                    | GBP cash movements used by the balance check        |
| Text containing `Account fee`, `Account Fee` or `ETF dealing fee`  | GBP cash movements only                             |
| `NameChange: <OLD> replaced with <NEW>` in Investment Transactions | A rename between uppercase ticker codes             |

For ordinary buys and sells in a full worksheet, the cash table supplies the total amount and the
exported text supplies the quantity and symbol. cgt-calc treats the final text in parentheses as the
symbol; if there is no text in parentheses, it uses the complete investment name. This is a simple
text rule, not validation that the value is an exchange ticker. The importer records Vanguard
account amounts in GBP and does not expose a separate dealing-fee field. For a cash-table trade, it
derives the unit price from the total cash amount and quantity, so any dealing charge already
included in that trade's `Amount` is folded into the derived price.

Deposit and withdrawal wording is recognised through phrases such as `Regular Deposit`,
`Cash transfer`, `Deposit via`, `Deposit for` and `Payment by`. Capitalisation and punctuation can
matter. Most other text stops the import with `Unknown action`. `Reversal of` is stripped only when
what follows is a dividend, interest or a cash movement, which the exported signed amount alone
reverses: a reversed dividend is exported as a debit and a reversed fee as a credit. A reversed
purchase or disposal stops the import rather than being read as a fresh trade in the wrong
direction.

By contrast, a separate account-fee or ETF-dealing-fee row only changes the tracked cash balance.
cgt-calc does not assign that row to a purchase or disposal as an allowable cost.

## Tax information outside the transaction listing

The transaction listing is not a complete tax certificate. In particular, it does not supply Excess
Reported Income (ERI). See the
[Vanguard section of the custom ERI data guide](../custom-eri-data.md#vanguard) for Vanguard's
published reports and the distinction between its traditional funds and ETFs.

Follow the cgt-calc [offshore funds guide](../offshore-funds.md) and check that the bundled ERI data
covers the fund and reporting period you need. Vanguard rows do not contain an ISIN, so cgt-calc can
attach ERI only when the parsed symbol maps to an ISIN. The bundled translations contain exchange
tickers, but not Vanguard's complete fund names.

For a holding whose parsed symbol is a fund name, create an ISIN translation CSV. The symbol must
match the parsed holding name shown in the cgt-calc report, not the complete `Details` cell. For
example, after confirming that the share class really is the
[Vanguard Emerging Markets Stock Index Fund GBP Accumulation share class](https://www.vanguard.co.uk/professional/product/fund/equity/9140/emerging-markets-stock-index-fund-acc),
the mapping would be:

```csv
ISIN,symbol
IE00B50MZ724,VANEMPA,Emerging Markets Stock Index Fund - Accumulation
```

The bundled translations already associate that ISIN with `VANEMPA`. A row in the user file for an
existing ISIN replaces its bundled symbol set, so retain every verified alias on the same row. Do
not copy that ISIN for another share class. Pass your verified mapping when generating the report:

```shell
cgt-calc --year 2025 --vanguard-file vanguard.csv \
  --isin-translation-file vanguard-isins.csv
```

This option selects a read/write cache, not a read-only input. cgt-calc may create or rewrite the
file when it learns a mapping from a transaction or a successful Open FIGI lookup. Keep a separate
copy of manually curated data if you need an immutable record; see
[ISIN to ticker translation](../extra-data-and-options.md#isin-to-ticker-translation).

A missing mapping produces a warning naming each affected Vanguard symbol. Check that warning and
the report rather than assuming that bundled ERI data was matched. Some bond-fund distributions are
taxed as interest rather than dividends; see
[Bond-fund income](../extra-data-and-options.md#bond-fund-income).

## Known limitations

- When a file contains both tables, ordinary transactions come from Cash Transactions. An unmatched
    Investment Transactions row is not imported as an additional transaction; ticker renames are the
    exception. Compare both tables for missing activity.
- Automatic sales of investments to cover an account fee are currently classified as cash movements,
    not disposals. They do not reduce the calculated holding or generate a gain or loss.
- Account fees and ETF dealing fees are not assigned to a purchase or disposal as allowable costs.
- Corporate actions other than the supported `NameChange` pair are not mapped. A split, merger,
    conversion, transfer of investments or other unfamiliar row can stop the import or be absent
    from the cash table. A split or consolidation can be recorded as a RAW
    [`STOCK_SPLIT` row](raw.md#share-reorganisations) stating the change to your whole pooled
    holding; remove the unmapped row, if the export has one, in a working copy only alongside it.
- `NameChange` supports uppercase ticker-style codes, with an optional dot suffix, rather than fund
    names. A row such as `NameChange: U.S. Equity Index Fund replaced with ...` stops with
    `Unknown action`.
- The dividend reader expects Vanguard's `DIV: ... @ ...` text layout. Changed dividend wording or
    another income type is not mapped merely because it appears in the workbook.
- A reversed purchase or disposal stops the import. It has to be reconciled with the original trade,
    which the row does not identify, so cgt-calc refuses it instead of guessing.
- The importer treats every transaction amount as GBP. Foreign-currency dividend text has not been
    validated and is not converted.
- The final text in parentheses is always treated as the ticker. For example, an investment ending
    in `(Accumulation)` is assigned the symbol `Accumulation`, which can silently combine different
    funds in one holding.
- A row cgt-calc cannot place stops the import instead of being skipped, and the error names the
    file line. That covers a row with the wrong number of fields, a header that does not match the
    section title above it, a conflicting header or section title, a transaction row above the first
    header or after a `Balance` or `Cost` summary, and stray text inside a table.
- Cosmetic rows are still ignored: trailing empty columns, a repeated page title or header, a
    `Page 1 of 2` label in any column, and a text footer below the last row of its table.
- A legacy cash-only table can be calculated, but an investment-only table is rejected because its
    unsigned `Cost` values do not provide usable cash movements. Use the complete worksheet with its
    Cash Transactions table.

## Troubleshooting

### `Vanguard CSV file is empty` or a file-format error

Pass the CSV saved from the General Account worksheet, not the `.xlsx` workbook, a PDF statement or
the Consolidated Tax Certificate. Excel workbooks are rejected before parsing; save the General
Account worksheet as CSV. Keep the headings unchanged. A usable export must contain the
`Date,Details,Amount,Balance` Cash Transactions header; retain the
`Date,InvestmentName,TransactionDetails,Quantity,Price,Cost` Investment Transactions table as well.

The parser accepts UTF-8 with or without a byte order mark and detects comma- or tab-separated table
headers throughout a full worksheet. A semicolon-delimited or another non-UTF-8 file is rejected. Do
not manually concatenate rows from different account worksheets. Cosmetic trailing empty columns do
not need to be preserved.

### `Unknown action`

The error names the unrecognised `Details` or `TransactionDetails` text. Compare it with the
original workbook and determine whether it is a trade, income, transfer, fee or corporate action
before changing anything.

Keep the original export unchanged. A name-based `NameChange` row is unsupported even though a
ticker-based rename is recognised. If the row is another real transaction not listed under
[Supported activity](#supported-activity), first upgrade cgt-calc using the same method you used to
install it. If it still fails, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with your cgt-calc
version, the complete error and a sanitised copy of the row.

A row with the wrong number of fields stops the import too, with a different message naming its
physical file line; check the source workbook as described below. A reversed purchase or disposal
has its own message, described next.

### A reversed purchase or disposal

`Reversal of Bought ...` and `Reversal of Sold ...` stop the import. Reversing a dividend, interest
or a cash movement only negates one exported amount, so cgt-calc reads those. Reversing a trade
instead has to be reconciled with the original trade, and the row does not say which purchase or
disposal that is, so the share matching cannot be worked out from it.

Keep the export unchanged, and please report the row with your cgt-calc version and a sanitised copy
in a [GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new). No export
containing a reversed trade has been examined, so what the row means is not established: it may
cancel a trade that never stood, or record something that is itself a disposal. A real example is
what would let cgt-calc handle it for you instead of stopping.

Do not simply delete the rows to get the import to run. Deleting the reversal alone leaves a trade
in the pool that may have been cancelled; deleting it together with the original removes activity
that may be chargeable, and the original may not even be in the export if your period starts after
it. Establish the correct treatment for the event first, then record it through the
[RAW format](raw.md) or calculate it separately, and retain the original workbook as evidence.

### Automatic sales to pay fees

If Vanguard sold units to pay an account fee, compare the Cash Transactions row with the matching
Investment Transactions row and your statement. Exact matching text enriches the cash movement with
the Investment Transactions quantity and a derived price, but the symbol remains empty and no
disposal of those units is recorded. Editing either text cell can prevent that enrichment.

Do not rely on the calculated gain or closing quantity until you have added the verified disposal
through another supported export or the [RAW format](raw.md), or calculated that event separately.
Make sure the disposal appears exactly once and retain the original workbook as evidence.

### Transactions are missing without an error

Compare the activity and row counts in both CSV tables with the original workbook. A row whose field
count differs from its table header, a header that does not match the section title above it, a
conflicting header or section title, and a transaction-shaped row above the first header or after a
summary now stop the import naming the physical file line, rather than being skipped. Stray text
inside a table stops the import for the same reason: a transaction that lost its separators arrives
as a single cell and must not pass as a footer.

A reversed purchase or disposal no longer imports silently either; see
[A reversed purchase or disposal](#a-reversed-purchase-or-disposal). Keep the original export
unchanged and report an unchanged Vanguard row that behaves unexpectedly in a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new).

### `Reached a negative balance` or `Tried to sell`

Check that the CSV is from the General Account and covers the complete required history, including
the deposits that funded purchases and the earlier purchase behind every sale. Also compare the Cash
and Investment Transactions tables for transfers, name changes and automatic fee sales.

Use `--no-balance-check` only after establishing why the cash history cannot reconcile and checking
its completeness another way.

### A quantity, ticker, fee or dividend looks wrong

Use the complete, unchanged worksheet CSV. Compare the terminal section headed “Portfolio at the end
of … tax year” with the Vanguard statement for 5 April. Check each disposal quantity and total
against both transaction tables, and reconcile dividends with the Consolidated Tax Certificate and
any required ERI. If an investment name ends in parentheses, confirm that the text is a real ticker;
cgt-calc otherwise uses it as the symbol and can combine unrelated funds.

Do not upload an unredacted workbook or CSV to GitHub: it can contain account names, holdings,
balances and other sensitive financial information.
