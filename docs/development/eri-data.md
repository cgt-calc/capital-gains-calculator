# Contributing ERI Data

Sharing compiled Excess Reported Income data saves significant time for other users holding the same
funds. Where each provider publishes its reports — and how their columns map to the ERI_RAW format —
is described in [Providing custom ERI data](../custom-eri-data.md#where-providers-publish-reports).

## Importing provider reports

First [set up the development environment](contributing.md#getting-started). From the repository
directory, run the
[`import_eri_reports.py`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/scripts/import_eri_reports.py)
script with the path to a downloaded report or a folder of reports:

```shell
uv run python scripts/import_eri_reports.py /path/to/reports
```

Replace `/path/to/reports` with your file or folder. The script recognises each report by its
filename and imports the data into the matching resource CSV under
[`cgt_calc/resources/eri/`](https://github.com/cgt-calc/capital-gains-calculator/tree/main/cgt_calc/resources/eri).
Vanguard, BlackRock, iShares, Xtrackers, Invesco and Legal & General UCITS ETF plc reports are
supported this way. The script does not update ISIN translations; add any needed mappings as
described below.

Check the messages for each file. **No ERI importer found** means the file was skipped; **found no
transactions** means no rows were imported from it. The final **Import complete!** message alone
does not confirm that every report was imported. Review the resource changes with
`git diff -- cgt_calc/resources/eri/` and compare the added ISINs, dates, currencies and amounts
with the provider's report.

If a supported report is skipped, check its filename first. Rename the file to match the filename
pattern in that provider's module under
[`cgt_calc/parsers/eri/importer/`](https://github.com/cgt-calc/capital-gains-calculator/tree/main/cgt_calc/parsers/eri/importer),
then run the script again. The script cannot read older Vanguard PDF reports, or an Invesco or
Xtrackers report that is still skipped once its name matches the pattern; add their data manually as
described below.

A recognised report can also stop the script with an error, such as **Cannot process ERI columns**
when its column headings differ from the ones the importer reads. Nothing is imported from that file
or from any file after it in the folder. Move the file out of the folder, run the script again for
the other reports, and add its data manually as described below.

Amundi reports, and Legal & General reports other than the UCITS ETF plc one, have no automatic
importer; add their data manually as described below.

## Manual additions and ISIN translations

To add data by hand, append the rows at the bottom of the provider's resource CSV under
[`cgt_calc/resources/eri/`](https://github.com/cgt-calc/capital-gains-calculator/tree/main/cgt_calc/resources/eri).
If a fund needs new ticker mappings, follow the
[ISIN translation instructions](../extra-data-and-options.md#isin-to-ticker-translation) to check or
add them in your own translation file (default `out/isin_translation.csv`). Copy the verified
mappings into
[`initial_isin_translation.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv).

## Pull request checklist

- Updated resource CSV(s) under `cgt_calc/resources/eri/`
- Any new rows in `cgt_calc/resources/initial_isin_translation.csv`
- Updated bundled-data list in [Offshore funds (ERI)](../offshore-funds.md#bundled-data)
