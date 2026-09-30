# Hargreaves Lansdown

cgt-calc reads one or more **Transaction Summary** CSV files from the Hargreaves Lansdown Tax
Centre. Every buy and sell in those files must have its original PDF contract note in the same
directory: the CSV supplies the cash value, while the PDF supplies the ticker, ISIN, quantity, unit
price and dealing charge.

Use reports for a taxable **Fund and Share Account** only. Do not include an ISA, Lifetime ISA or
SIPP: the parser does not identify the account type or exclude tax-advantaged accounts.

## Export the complete history

Export enough history to establish the cost of every investment owned or sold during the tax year.
The safest option is to start with the account's first transaction and end today, or at least 30
days after the end of the tax year you are calculating. If you generate several reports, use
non-overlapping periods so that the same transaction is not imported twice.

1. Sign in to the HL website and open the Tax Centre, currently under **Accounts**.
2. Generate the required Transaction Summary and download the CSV offered with the completed report.
3. For every buy or sell in the CSV, download the linked PDF contract note. Contract notes are also
    available from **Transaction History**. The HL instructions for
    [share and ETF trades](https://www.hl.co.uk/help/buying-selling-investments/shares-investment-trusts-etfs/buy-shares)
    and [fund trades](https://www.hl.co.uk/help/buying-selling-investments/funds/buy-funds) confirm
    where completed trades and their notes appear.
4. Put all the Transaction Summary CSV files and their contract-note PDFs in one directory. Do not
    put unrelated CSV files there; cgt-calc attempts to parse every CSV in the directory.

Each trade has a reference such as `B302087054` or `S302087055`. Its PDF filename must begin with
that complete reference followed by an underscore, for example:

```text
B302087054_BOUGHT_Vanguard.pdf
S302087055_SOLD_Vanguard.pdf
```

Matching is case-insensitive, including the `.csv` and `.pdf` extensions. Keep exactly one matching
PDF for each reference. If HL downloaded it under another name, retain the original for your records
and rename a copy; do not edit or convert the PDF itself.

You can compare the CSV layout with the
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/hl/data/inputs/hl-transaction-summary.csv).
The tests create synthetic contract notes rather than publishing real account PDFs.

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --hl-dir hargreaves_lansdown/
```

`--year 2025` means 6 April 2025 to 5 April 2026. Pass the directory, not an individual CSV or PDF.
Follow [Generate and Review a Report](../usage.md) to find and check the output.

Run with the balance check enabled. Do not add `--no-balance-check` merely to bypass an error; first
reconcile the report and any unsupported rows as described under
[Troubleshooting](#troubleshooting).

## Supported activity

The parser uses the CSV's `Reference` column to recognise these rows:

| Reference       | How cgt-calc handles it                                                                     |
| --------------- | ------------------------------------------------------------------------------------------- |
| `B` plus digits | A purchase; takes the trade date and cash value from the CSV and execution details from PDF |
| `S` plus digits | A disposal; takes the trade date and cash value from the CSV and execution details from PDF |
| `INTEREST`      | GBP interest income using the value from the CSV                                            |
| `FPC`           | A GBP cash movement used by the balance check                                               |
| `MANAGE FEE`    | A GBP cash movement used by the balance check; `n/a` is treated as zero                     |
| blank           | Ignored as a report summary or footer row                                                   |

For a purchase or disposal, cgt-calc reads the ticker, ISIN, quantity, price and `Dealing charge`
from the matching PDF. cgt-calc assumes the extracted unit price is in pence and divides it by 100
before recording it in GBP. This has been validated only with the tested ETF contract-note layout;
fund contract notes have not been validated. The CSV's `Value (£)` remains the transaction's total
cash movement.

`MANAGE FEE` only changes the tracked cash balance; cgt-calc does not add it to the allowable cost
of an investment.

Only the literal references above are supported. Letter case and surrounding spaces do not matter,
but any other non-blank reference stops the import with `Unknown transaction type`.

## Known limitations

- Dividends, tax deductions, transfers of investments and corporate actions are not mapped. A
    non-blank row for any of them stops the import rather than being silently discarded.
- Every recognised buy or sell requires a matching PDF. The
    [HL service terms](https://www.hl.co.uk/__data/assets/pdf_file/0015/37122/Online-Ts-and-Cs.pdf)
    say that it does not issue contract notes for some fund transactions, including certain regular
    investments, automatic reinvestment and sales made to cover fees. Those trades cannot be
    imported from the Transaction Summary alone.
- The PDF reader expects the text-based HL contract-note layout and extracts only the field labelled
    `Dealing charge`. Scanned PDFs, changed layouts, foreign-currency trades, stamp duty or other
    charges have not been validated and can make the trade fail its amount checks.
- The parser does not inspect the investment type. It has been tested with an ordinary GBP-priced
    ETF; do not rely on this importer for gilts, bonds, derivatives or another instrument whose UK
    tax treatment differs.
- Every CSV in the directory is parsed and overlapping reports are not deduplicated. Keep unrelated
    CSVs elsewhere and make each reporting period occur exactly once.
- The action and trade date extracted from a PDF are not cross-checked with the CSV. The filename
    reference is the link, so verify that each reference points to the correct note.

## Troubleshooting

### `Cannot find contract note pdf for transaction ...`

Find the named reference in the CSV and download its contract note from HL. Check that the PDF is in
the same directory and that its filename follows `<reference>_*.pdf`. Keep only one matching PDF for
that reference.

If HL did not issue a contract note for the trade, cgt-calc cannot reconstruct its ticker, quantity,
price and fees from the CSV alone. Do not invent a PDF or copy another trade's note.

### `Unknown transaction type`

The error includes the unsupported reference and description. Identify the activity before changing
anything; it may be income, a transfer or a corporate action that affects the calculation.

Keep the original export unchanged. For activity whose complete details and UK tax treatment you can
independently verify, make a separate working copy without the unsupported row and add one
equivalent transaction through another supported export or the [RAW format](raw.md). Make sure the
activity appears exactly once. Otherwise, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with your cgt-calc
version, the complete error and a sanitised copy of the row.

### `Could not find the 'Trade date' header` or `CSV header mismatch`

Use the CSV downloaded from the Tax Centre rather than a statement, tax certificate or spreadsheet
created by converting a PDF. Keep its headings unchanged. cgt-calc skips the identifying preamble
above the `Trade date` row, but requires the exported transaction columns below it.

### `Reached a negative balance` or `Tried to sell`

Check that the non-overlapping CSVs cover the complete required history, including the cash movement
that funded every purchase and the earlier purchase behind every sale. Then account for every
unsupported row rather than deleting it.

Use `--no-balance-check` only after you have established why the cash history cannot reconcile and
have checked its completeness another way.

### A trade or portfolio quantity looks wrong

Use an unchanged, text-based contract note and confirm its filename matches the CSV reference.
Compare the terminal section headed “Portfolio at the end of … tax year” with the HL account record
for 5 April. Also compare each imported quantity, unit price, total value and dealing charge with
its contract note before relying on the calculation.

Do not upload an unredacted CSV or PDF to GitHub: these files contain client numbers, transaction
references, holdings and other sensitive financial information.
