# Contributing ERI data

Sharing compiled Excess Reported Income data saves significant time for other users holding the same
funds. [Custom ERI data](../custom-eri-data.md#where-providers-publish-reports) describes where each
provider publishes its reports and how their columns map to the ERI_RAW format.

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
Vanguard, BlackRock, iShares, Xtrackers, Invesco, Legal & General UCITS ETF plc and HSBC ETFs plc
reports are supported this way. The script does not update ISIN translations; add any needed
mappings as described below.

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

Amundi reports, Legal & General reports other than the UCITS ETF plc one, and HSBC Global Funds ICAV
reports have no automatic importer; add their data manually as described below.

SSGA SPDR ETFs Europe I and II plc reports are supported too. For the years to 31 March 2020 to
2023, import SSGA's revised reports: the importer refuses the originals. The scanned copy of the
Europe I plc letter for the year to 31 March 2015
(`ssga-spdr-i-investor-letter-authorised-2015.pdf`) has no text and stops the script; leave it out,
as SSGA also publishes that letter as text.

## Manual additions and ISIN translations

To add data by hand, append the rows at the bottom of the provider's resource CSV under
[`cgt_calc/resources/eri/`](https://github.com/cgt-calc/capital-gains-calculator/tree/main/cgt_calc/resources/eri).
If a fund needs new ticker mappings, follow the
[ISIN translation instructions](../extra-data-and-options.md#isin-to-ticker-translation) to check or
add them in your own translation file (default `out/isin_translation.csv`). Copy the verified
mappings into
[`initial_isin_translation.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv).

## Reporting fund dates

[`reporting_funds.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/reporting_funds.csv)
records when each fund with bundled ERI data was a reporting fund, from HMRC's
[list of approved offshore reporting funds](https://www.gov.uk/government/publications/approved-offshore-reporting-funds).
Its `start` column is the list's "Reporting Fund, with effect from" date, and `end` is its "Ceased
to be an RF on" date, left empty while the fund is still a reporting fund. Dates are written as
YYYY-MM-DD. A fund HMRC lists more than once gets one row: the earliest start, and the latest end
date only if every entry has one. A fund with any entry whose start is "To Be Confirmed" gets no
row, because its dated entries alone could give too late a start.

Regenerate the file whenever you change the bundled ERI data:

1. Download "List of reporting funds A to Z" from HMRC's page. It is an `.ods` spreadsheet, updated
    every month.
2. From the repository directory, run the script with the downloaded file:

    ```shell
    uv run python scripts/import_reporting_funds.py /path/to/Master-Weblist.ods
    ```

3. Check the **WARNING** lines. Each names a bundled fund that gets no row, because an entry for it
    in HMRC's list gives its start as "To Be Confirmed", or the list does not include it. Review the
    change with `git diff -- cgt_calc/resources/reporting_funds.csv`.
4. Run the script's tests:

    ```shell
    uv run pytest tests/eri_importers/test_import_reporting_funds.py
    ```

    `test_every_bundled_eri_fund_has_reporting_fund_dates` fails while a bundled fund other than
    `IE00BYZNF849`, whose start HMRC gives as "To Be Confirmed", has no row. Look the fund up in
    HMRC's list. If an entry for it gives its start as "To Be Confirmed", add its ISIN to the test's
    exceptions; if it is not listed, check the ISIN in the ERI data. The test also fails once HMRC
    confirms `IE00BYZNF849`'s start and the fund gets a row: then remove it from the exceptions and
    from the test's docstring.

The script stops without changing the file when a date or column in HMRC's list is not in the form
it expects. It also stops when HMRC lists a bundled fund twice with a gap between the date it ceased
to be a reporting fund and the date it became one again, as one row cannot record that. Open an
issue with the fund's ISIN and HMRC's entries for it.

HMRC publishes the list under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
The dates file contains public sector information licensed under the Open Government Licence v3.0.
[`reporting_funds_notice.txt`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/reporting_funds_notice.txt)
beside it carries this attribution into every package of cgt-calc.

## Yearly refresh

cgt-calc stops a report for a finished tax year when a fund the user held has no ERI figure for a
period whose income falls in that year
([Missing reporting periods](../offshore-funds.md#missing-reporting-periods)). It checks the data in
the installed release, so the bundled data must not lag behind the funds' reports.

Every April, before users report the tax year that has just ended:

1. Import the reports for every reporting period that ended by 5 October in that tax year. Their
    income falls in the year, and their reports were due by 5 April.
2. [Regenerate the reporting fund dates](#reporting-fund-dates) from HMRC's current list.
3. Publish a release.

Repeat the refresh in late summer for reports that came out late.

## Pull request checklist

- Updated resource CSV(s) under `cgt_calc/resources/eri/`
- Any new rows in `cgt_calc/resources/initial_isin_translation.csv`
- [Regenerated `cgt_calc/resources/reporting_funds.csv`](#reporting-fund-dates)
- Updated bundled-data list in [Offshore funds (ERI)](../offshore-funds.md#bundled-data)
