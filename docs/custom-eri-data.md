# Providing Custom ERI Data

Use a custom file only when the [bundled data](offshore-funds.md#bundled-data) does not cover the
exact ISIN and every reporting period you need.

## ERI_RAW format

Create a CSV with this exact header and one row for each share class and reporting period:

```csv
ISIN,Fund Reporting Period End Date,Currency,Excess of reporting income over distribution
IE00B42WWV65,30/06/2024,GBP,0.0066
```

The row is only an example of the format. Replace it with values from the fund's report:

- **ISIN:** the ISIN for the exact share class
- **Fund Reporting Period End Date:** the period end date in `DD/MM/YYYY` format
- **Currency:** the three-letter currency code shown in the report
- **Excess of reporting income over distribution:** the ERI amount per unit, without multiplying it
    by the number of units you held

Keep the four column names unchanged and do not add other columns.

Add `--eri-raw-file` to your normal cgt-calc command. For example, with a Schwab transaction file:

```shell
cgt-calc --year 2024 --schwab-file schwab_transactions.csv --eri-raw-file eri_raw.csv
```

## Where providers publish reports

Download the report for the exact share class and reporting period you need. The lists below show
which report values belong in each ERI_RAW column.

### Vanguard

Vanguard UK publishes annual Reportable Income reports at the bottom of this page:
<https://www.vanguardinvestor.co.uk/investing-explained/general-account-tax-information>

Use Vanguard Investment Series plc reports for traditional funds and Vanguard Funds plc reports for
ETFs. Check the fund company in your fund documents if you are unsure.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** Share Class Currency column
- **Excess of reporting income over distribution:** same name column

### iShares / BlackRock

BlackRock UK publishes annual Reportable Income reports here:
<https://www.blackrock.com/uk/solutions/adviser-resources/reporting-fund-status>

iShares UK publishes annual Reportable Income reports here:
<https://www.ishares.com/uk/individual/en/education/library?materialType=tax+information>

BlackRock groups its reports by fund range, such as BGIF, BGF and BSF. iShares groups them by fund
company. Choose the group named in your fund documents.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** same name column
- **Excess of reporting income over distribution:** Excess of reporting income per unit column

### Xtrackers

DWS UK publishes annual Reportable Income reports here:
<https://etf.dws.com/en-gb/information/etf-documents/reportings/>

Choose the report for the fund company named in your fund documents: Xtrackers, Xtrackers II or
Xtrackers IE.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Period Ended date at the top of the PDF
- **Currency:** Share class currency column
- **Excess of reporting income over distribution:** Excess reported income per share column

### Amundi

Amundi UK publishes annual Reportable Income reports here:
<https://www.amundietf.co.uk/en/individual/resources/document-library?documentType=uktaxcalculation>

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Reporting Period End Date column
- **Currency:** Currency of the following amounts column
- **Excess of reporting income over distribution:** Per unit excess reportable income over
    distributions in respect of the reporting period column

Amundi does not publish every fund there. The rest are on <https://www.kpmgreportingfunds.co.uk>,
which requires an account.

### Invesco

Invesco publishes annual Reportable Income reports in the documents section of each fund with UK
reporting status:
<https://www.invesco.com/uk/en/financial-products/etfs/invesco-uk-gilts-ucits-etf-acc.html#Documents>

- **ISIN:** ISIN / Identifier column
- **Fund Reporting Period End Date:** Stated in the report header before the main table
- **Currency:** Currency of Share Class column
- **Excess of reporting income over distribution:** Per unit excess reportable income over
    distributions in respect of the reporting period column

### Legal & General

LGIM publishes annual Reportable Income reports here:
<https://am.landg.com/en-uk/institutional/fundcentre/uk-reporting-funds/>

The columns below are those of the Legal & General UCITS ETF plc report. The Legal & General ICAV,
Legal & General SICAV and LGIM Liquidity Funds plc reports on the same page set their tables out
differently, so read the equivalent columns from the report named in your fund documents.

The ETF report splits its headings over two rows: **Reporting from** and **Reporting to** sit in a
second row under a shared **Reporting Period** heading.

- **ISIN:** ISIN/SEDOL column
- **Fund Reporting Period End Date:** Reporting to column
- **Currency:** Currency of calculation column
- **Excess of reporting income over distribution:** Excess of reportable income per unit column

## Contributing data back

If you compile ERI data for any fund, please contribute it so other holders of the same fund can
reuse it — see [Contributing ERI data](development/eri-data.md).
