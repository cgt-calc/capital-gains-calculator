# Morgan Stanley at Work (Alphabet)

cgt-calc reads the `Releases Report.csv` and `Withdrawals Report.csv` files produced for an Alphabet
`GSU Class C` stock plan. This importer was built from that specific export and has not been
validated for another employer, plan or Morgan Stanley brokerage account.

Morgan Stanley currently offers several distinct stock-plan platforms. The files described here come
from **Morgan Stanley at Work (formerly StockPlan Connect)**; they are not Shareworks or E*TRADE
exports. The official [employee login page](https://www.morganstanley.com/atwork/employees/login)
identifies the three platforms separately.

## Export your complete reports

Sign in to the Morgan Stanley at Work account used for your Alphabet awards and download the
complete history from its report download page.

Place the downloaded report files in one directory. Keep at least these files unchanged:

- `Releases Report.csv`
- `Withdrawals Report.csv`

Their names are required, although matching is case-insensitive, including the `.csv` extension.
Other CSV files in the directory are ignored. In particular, cgt-calc does not read the
`Releases Net Shares Report.csv` or `Withdrawal Wire Report.csv` files found in the original example
export.

Export from the first award or transaction through today, or at least 30 days after the end of the
tax year you are calculating. Earlier vesting establishes the cost and quantity of shares sold
later, and acquisitions in the following 30 days can affect UK share matching.

You can compare the two recognised files with the
[sanitised example reports](https://github.com/cgt-calc/capital-gains-calculator/tree/main/tests/morgan_stanley/data).

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --mssb-dir morgan_stanley/
```

`--year 2025` means 6 April 2025 to 5 April 2026. Pass the directory containing the two CSV files,
not either file itself. Follow [Generate and Review a Report](../usage.md) to find and check the
output.

## Supported activity

The importer recognises only these rows:

| Report                   | Exported row                                     | How cgt-calc handles it                                                 |
| ------------------------ | ------------------------------------------------ | ----------------------------------------------------------------------- |
| `Releases Report.csv`    | `GSU Class C`, type `Release`, status `Complete` | Acquires the exported net shares at the USD price on the vest date      |
| `Releases Report.csv`    | `GSU Class C`, type `Release`, status `Staged`   | Handled in the same way as a completed release                          |
| `Withdrawals Report.csv` | `GSU Class C`, type `Sale`, status `Complete`    | Disposes of shares; derives the fee from gross value minus `Net Amount` |
| `Withdrawals Report.csv` | `Cash`, type `Sale`, status `Complete`           | Records the `Net Amount` as cash withdrawn from the account             |

For a release, cgt-calc uses `Net Share Proceeds`, not the pre-tax `Quantity`, as the number of
shares acquired. `Net Cash Proceeds` must be exactly `$0.00`. All prices and amounts are treated as
USD, and `GSU Class C` is mapped to the Alphabet Class C ticker `GOOG`.

Morgan Stanley's withdrawal report says that Alphabet sales on or before 15 July 2022 use pre-split
values, while later sales use post-split values and GSU vests are always displayed post-split.
cgt-calc therefore adjusts those earlier sales for the 20-for-1 split that Alphabet executed on 15
July 2022.

## Known limitations

- This is an Alphabet-specific equity-award importer, not general Morgan Stanley support. Any plan
    other than the literal `GSU Class C` or `Cash` value stops with `Unknown plan`.
- Autosale releases are not supported. They can put a non-zero `Net Cash Proceeds` in the releases
    report while recording the actual sale in a separate Autosale report that cgt-calc does not
    parse. The import deliberately stops rather than silently losing either the vest or sale.
- Only the report names and row values listed above are recognised. Other report files are ignored;
    another type, status, header layout or price currency stops the import.
- Dividends, interest, wires, transfers of shares, and other corporate actions are not imported from
    this report directory. Add any relevant missing activity through another supported export or the
    [RAW format](raw.md), after verifying its date, amount and UK tax treatment.
- A `Staged` release is treated as an acquisition. Confirm that it completed and agrees with your
    final award record before relying on it.

## Troubleshooting

### `No transactions detected in directory`

Check that the directory passed to `--mssb-dir` directly contains `Releases Report.csv` and
`Withdrawals Report.csv`. A parent directory, a Shareworks or E*TRADE export, or a directory
containing only the ignored reports will not load any transactions.

### `No transactions detected in file`

The named report holds its header line and no rows. cgt-calc reads it as having no transactions and
carries on with the other report. That is expected when you had no sales, or no releases, in the
period you exported. If you had some, download the report again for the complete period: the
calculation leaves them out otherwise.

### `Non-zero Net Cash Proceeds`

This is a known Autosale pattern, but the releases report alone does not provide enough information
to reconstruct both the vest and the later sale safely. This history is not currently supported.
Keep the downloaded reports unchanged: do not delete the row, change its proceeds to zero or add
guessed RAW transactions.

First upgrade cgt-calc using the same method you used to install it. If the error remains, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with your cgt-calc
version, the complete error, and sanitised copies of the failing release row and its corresponding
Autosale record.

If you can independently reconstruct and verify every Morgan Stanley acquisition, sale, fee and cash
movement, you can instead convert the complete history to the [RAW format](raw.md) and omit
`--mssb-dir`. Do not combine a partial RAW replacement with the failing report: cgt-calc will still
reject the report, and overlapping inputs can duplicate transactions.

### `Unknown plan`, `Unknown type`, `Unknown status` or `Unknown price currency`

Compare the named value with [Supported activity](#supported-activity). The export may be for
another employer or may contain an unsupported transaction. Changing the text would not make the
underlying activity equivalent to an Alphabet GSU release or sale.

First upgrade cgt-calc using the same method you used to install it. If an unchanged supported
export still fails, open a GitHub issue with your cgt-calc version, the complete error and a
sanitised copy of the failing row.

### `CSV header mismatch`

Use the unchanged `Releases Report.csv` or `Withdrawals Report.csv` from the report download. Do not
rename columns or convert a PDF or spreadsheet into CSV. If Morgan Stanley has changed an unchanged
report's header, open a GitHub issue with the header and a sanitised row.

Do not upload an unredacted report: it contains order identifiers, holdings and other sensitive
financial information.

### `Reached a negative balance` or `Tried to sell`

Re-export the complete history and confirm that every sale has its earlier release. Also include the
`Cash` withdrawal rows when present. Do not use `--no-balance-check` until you have identified why
the cash or share history is incomplete.

### The share quantities or sale prices look wrong

Check the section headed “Portfolio at the end of … tax year” against your award and brokerage
records for 5 April. Pay particular attention to 2022 sales: sales on or before 15 July should be
normalised to post-split quantities and prices. Compare each sale's proceeds and derived fee with
the confirmation before relying on the calculation.
