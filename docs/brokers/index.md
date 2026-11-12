# Brokers

Choose your broker below to find the export instructions and command to use. Export the complete
history from the account's first transaction when possible, not only the tax year you are
calculating. Earlier purchases or employer-share awards can establish the cost of shares sold later.

Pass each export, or the folder that holds a broker's exports, straight to cgt-calc: it recognises
the broker from the file's contents, as described in
[How cgt-calc recognises each input](../usage.md#how-cgt-calc-recognises-each-input). The option in
the table reads the file as that broker's without recognising it first.

If your shares came from an employer, check the broker guide carefully. Schwab supports only the
award-export formats described in its guide, Morgan Stanley support is limited to Alphabet's GSU
plan, and Sharesight requires equity grants to be recorded in a particular way.

| Broker                                        | CLI option                                               | Notes                                              |
| --------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------- |
| [Charles Schwab](schwab.md)                   | `--schwab-file` or `--schwab-dir`, `--schwab-award-file` | Written equity options, some Equity Awards formats |
| [Freetrade](freetrade.md)                     | `--freetrade-file`                                       | Activity CSV export                                |
| [Hargreaves Lansdown](hargreaves-lansdown.md) | `--hl-dir`                                               | Tax Centre CSVs plus contract notes                |
| [Interactive Brokers](interactive-brokers.md) | `--interactive-brokers-file`                             | Transaction history CSV                            |
| [Morgan Stanley](morgan-stanley.md)           | `--mssb-dir`                                             | Alphabet GSU reports only                          |
| [Revolut](revolut.md)                         | `--revolut-file`                                         | Invest account statement CSV                       |
| [Sharesight](sharesight.md)                   | `--sharesight-dir`                                       | Multi-broker portfolio tracker                     |
| [Trading 212](trading212.md)                  | `--trading212-dir`                                       | Folder of yearly exports                           |
| [Vanguard](vanguard.md)                       | `--vanguard-file`                                        | Client Transactions Listing CSV                    |
| [RAW format](raw.md)                          | `--raw-file`                                             | Generic fallback for other brokers                 |

## Dates and time zones

UK share matching and the tax year boundary run on UK calendar days. [Freetrade](freetrade.md),
[Revolut](revolut.md) and [Trading 212](trading212.md) timestamp each transaction in UTC, and
cgt-calc converts those to UK time before taking the date. Every other export above states a date
with no time, so cgt-calc reads that date as the broker wrote it and cannot correct one that was
recorded in another time zone.

If your broker is not listed, try the [RAW format](raw.md). Contributions for new brokers are very
welcome — see [Adding a broker](../development/adding-a-broker.md).
