# Reporting problems

Use this page when cgt-calc stops with an error you cannot resolve and you want to report it, for
example when a run ends with **Unexpected error!** and a traceback.

!!! warning "Issues are public"

    Anyone can read an issue on GitHub. Never upload an unredacted export, PDF report or error
    message: follow [What to redact](#what-to-redact) first.

## Before you report

1. [Upgrade cgt-calc](installation.md#upgrade-cgt-calc) and run the same command again. The problem
    may already be fixed.
2. If the error came while cgt-calc was reading a broker file, download a fresh export and pass it
    to cgt-calc without editing it. Convert it first only if your broker guide tells you to.
3. Read the troubleshooting section of the [guide for your broker](brokers/index.md). It may name
    the error and say what to do.

## What to include

If the error is still there, open a
[new issue on GitHub](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with:

- **Your cgt-calc version.** The advice printed after an unexpected error names it. Otherwise, run
    `cgt-calc --version`.
- **The complete error.** Copy every line from the one containing `Unexpected error!` or `ERROR:` to
    the end of the output, including the traceback if there is one. Do not shorten or summarise it.
- **The smallest redacted example that still fails,** and the command you ran it with.

Redact the error and the command as well as the example.

## Make the smallest example that still fails

A few rows that produce the error are more useful than a whole export, and safer to share. These
steps are for a file with one transaction on each row, such as a CSV.

1. Copy the file into a new folder and work on the copy. If you pass cgt-calc a folder, copy the
    whole folder: cgt-calc reads every export in the folder you give it. Keep the original export
    unchanged.
2. In the copy, keep the header and remove about half of the other rows.
3. Run the same command with the path of the copy in place of the original.
4. If you get the same error, carry on with the rows that are left. If the error has gone or is a
    different one, put those rows back and remove others.
5. Stop when removing any other row makes the error go away or change.
6. Redact what is left, then run the command once more to check that the redacted copy still gives
    the same error.

A JSON file, such as a Schwab Equity Awards export, has no rows: each transaction is one `{...}`
entry in its list of transactions. Follow the same steps, but remove whole entries and leave
everything outside the list as it is, so that the file is still valid JSON.

If you cannot cut the file down, or are not sure the result is safe to share, do not attach it. Open
the issue with your version and the redacted error, and say that you need help preparing an example.

## What to redact

Replace each of these with a made-up value of the same kind and length. Do not delete it, because a
missing value can change how the row is read. Check the lines above the column names as well as the
rows: an export can begin with your name, a client number or a portfolio name.

- **Names:** yours, and any account or portfolio name.
- **Account and transaction IDs:** account and client numbers, order IDs and transaction references.
- **Local paths:** in the error and in the command. A folder name can contain your username, so
    write `/home/alex/exports/transactions.csv` as `/home/USER/exports/transactions.csv`.

Dates, quantities, amounts and tickers show what you hold. Change any you would rather not publish,
then check that the error is still the same. The [privacy notes](privacy.md) say what an error
message can quote.

## What to keep

The example has to be read the same way as your original file, so keep:

- **The file format.** Send the kind of file you passed to cgt-calc. If your broker guide had you
    convert the export first, for example by saving a worksheet as a CSV, send the converted file:
    that is the one that fails. Edit a CSV in a text editor, not a spreadsheet: a spreadsheet
    program can change how dates and numbers are written when it saves.
- **The headers.** Keep the column names exactly as exported. Keep any lines above them too, with
    their labels and layout, and replace only the personal values they hold.
- **The row structure.** Keep every column in the same order, including empty ones, and keep the
    wording of the action or type column and the way dates and numbers are written.
