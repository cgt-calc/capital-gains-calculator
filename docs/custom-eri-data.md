# Custom ERI data

Use a custom file to supply excess reported income (ERI) data for an ETF or investment fund you held
when the [bundled data](offshore-funds.md#bundled-data) does not cover its exact ISIN and every
reporting period you need.

## ERI_RAW format

Create a CSV with this header and one row for each share class and reporting period:

```csv
ISIN,Fund Reporting Period End Date,Currency,Excess of reporting income over distribution
IE00B42WWV65,30/06/2024,GBP,0.0066
```

The row is only an example of the format. Replace it with values from the fund's report:

- **ISIN:** the ISIN for the exact share class
- **Fund Reporting Period End Date:** the period end date in `DD/MM/YYYY` format
- **Currency:** the three-letter currency code shown in the report
- **Excess of reporting income over distribution:** the ERI amount per unit, without multiplying it
    by the number of units you held. If the report states the excess as nil, enter 0: cgt-calc then
    knows the period has a report. If the report leaves it blank, it gives no figure; do not
    enter 0.

Name each of the four columns once, in any order; letter case and spaces around a name do not
matter. Do not add other columns, and give each row's values in the order of its header.

Add `--eri-raw-file` to your normal cgt-calc command. For example, with a Schwab transaction file:

```shell
cgt-calc --year 2024 schwab_transactions.csv --eri-raw-file eri_raw.csv
```

Pass the ERI file with this option, not as an input like a broker export: cgt-calc does not read ERI
data passed that way, and stops with a message naming the option.

Schwab rows carry no ISIN, so the ERI is applied only when the mapping gives the fund's ISIN the
symbol Schwab shows. The bundled mapping does that for very few funds: a US fund it lists under its
US symbol, such as `CHIQ`, and a fund based outside the US whose US over-the-counter symbol it
lists. For any other fund, add the row to your mapping file; see
[Funds based outside the US](brokers/schwab.md#funds-based-outside-the-us).

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

If the report you need is not linked there, look in BlackRock's document library:
<https://www.blackrock.com/uk/education/library?userType=individual&materialType=tax+information>

BlackRock groups its reports by fund range, such as BGIF, BGF and BSF. Choose the range named in the
fund documents.

iShares UK publishes annual Reportable Income reports in its document library:
<https://www.ishares.com/uk/professionals/en/library?materialType=tax+information>

To find a report for a particular ETF, open its product page on ishares.com and look under
**Literature → Tax Information**. Reports may cover several ETFs belonging to one fund company, such
as iShares II plc. Within the report, find the exact ISIN and reporting period you need.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** same name column
- **Excess of reporting income over distribution:** Excess of reporting income per unit column

### Xtrackers

DWS UK publishes annual Reportable Income reports here:
<https://etf.dws.com/en-gb/information/etf-documents/reportings/>

Choose the report for the fund company named in your fund documents: Xtrackers, Xtrackers II or
Xtrackers IE.

The column names changed in 2025. For reports on periods ending in 2025 or later:

- **ISIN:** ISIN CODE column
- **Fund Reporting Period End Date:** End date in the REPORTING PERIOD column for your share class
- **Currency:** CLASS CURRENCY column
- **Excess of reporting income over distribution:** EXCESS OF REPORTED INCOME PER UNIT OVER
    DISTRIBUTIONS IN RESPECT OF THE REPORTING PERIOD column, not the DISTRIBUTION(S) PER UNIT column

If that column is empty for your share class, the report gives no figure. Do not enter 0; ask DWS
for the figure.

For earlier reports:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Period Ended date at the top of the PDF
- **Currency:** Share class currency column (Currency of the following amounts in Xtrackers II and
    Xtrackers IE reports)
- **Excess of reporting income over distribution:** Excess reported income per share column (Per
    unit excess reportable income over distributions in Xtrackers II and Xtrackers IE reports)

### JPMorgan

JPMorgan publishes one Report to Investors a year for all the funds of JPMorgan ETFs (Ireland) ICAV.
The report for 2025 is at
<https://am.jpmorgan.com/content/dam/jpm-am-aem/emea/regional/en/supplemental/notice-to-shareholders/jpm-etf-ireland-icav-2025-report-to-investors.pdf>,
and the one for 2020 at
<https://am.jpmorgan.com/content/dam/jpm-am-aem/emea/ie/en/regulatory/reportable-incomes/jpm-etfs-uk-reportable-income-2020-en.pdf>.

From the 2021 report on, each share class has a row:

- **ISIN:** ISIN or ISIN/SEDOL column
- **Fund Reporting Period End Date:** the "To" date under Reporting Period
- **Currency:** your share class's currency: the Share Class Currency column, or in the 2025 report
    the currency named in the Share class column, such as EUR in "Class EUR Hedged (acc)". Do not
    use the Currency of calculation column, which is the fund's currency.
- **Excess of reporting income over distribution:** Excess of reportable income per unit column

The 2020 report lists the share classes in columns instead, and its excess of reportable income per
unit is "Nil" when there is none. It does not say which currency its figures are in. cgt-calc has no
2020 figure for a share class whose currency differs from its Currency of calculation, unless the
figure is Nil; ask JPMorgan which currency the figure is in before entering it.

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

### SPDR

SSGA publishes the annual Reportable Income reports of SSGA SPDR ETFs Europe I plc and SSGA SPDR
ETFs Europe II plc on its announcements page:
<https://ssga.com/uk/en_gb/intermediary/resources/documents/announcements>

Choose the report for the company named in your fund documents. For the years to 31 March 2020 to
2023, use the revised reports SSGA published in 2024, with "Revised" in the title: SSGA restated the
figures of some share classes.

The table comes after the letter:

- **ISIN:** ISIN / Identifier column (ISIN in reports up to 2019)
- **Fund Reporting Period End Date:** the date in the table's title, after "Reporting Period End" or
    "Reporting Period Ended". If a footnote says your fund terminated during the year, use that
    date.
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

### HSBC

HSBC publishes one Reportable Income report a year for all the funds of HSBC ETFs plc. Download it
from this link, changing the year at the end to the year the reporting period ends:
<https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/ie00b42tw061/gb/en/reportable%20income%202025>

Read the first table, which has an **Excess reported income per share** column. The tables after it
list the distributions paid, which are not ERI. Reports up to 2017 also have a **Reporting period
start date** column; use the end date.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Reporting period end date column, for your share class
- **Currency:** same name column
- **Excess of reporting income over distribution:** Excess reported income per share column

Some share classes HMRC lists for HSBC ETFs plc, mostly currency-hedged ones, are not in the report.
If yours is missing, ask HSBC for its figures.

Funds of HSBC Global Funds ICAV have a separate report, with an added **Share class / Series**
column. To download it, replace the ISIN in the link with your fund's ISIN. If a year's report does
not download, ask HSBC for it.

## Contributing data back

If you compile ERI data for any fund, please contribute it so other holders of the same fund can
reuse it. See [Contributing ERI data](development/eri-data.md).
