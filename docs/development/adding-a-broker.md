# Adding a Broker

Start with the [development setup](contributing.md#getting-started). A broker parser converts an
export into `BrokerTransaction` values; tax matching belongs in the calculator, not the parser. The
[Freetrade parser](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/parsers/freetrade.py)
and its
[tests](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/freetrade/test_freetrade.py)
show file loading, transaction conversion and errors for unsupported rows.

1. Add a new parser class in
    [`cgt_calc/parsers/`](https://github.com/cgt-calc/capital-gains-calculator/tree/main/cgt_calc/parsers)
2. Register it in
    [`cgt_calc/parsers/broker_registry.py`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/parsers/broker_registry.py)
3. Add tests in `tests/`
4. Add a docs page under `docs/brokers/` and a nav entry in `zensical.toml`, and add the broker to
    the table in [Brokers](../brokers/index.md) and the list in the README. All three are sorted by
    broker name, with the RAW format left last
5. Submit a pull request describing your changes

## Parser checklist

- Choose a shared base from
    [`base_parsers.py`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/parsers/base_parsers.py):
    `BaseSingleFileParser` for one file, `StandardCSVParser` for a CSV with known columns, or
    `BaseDirParser` for a directory of exports. Reuse its argument registration and loading
    behaviour.
- Check transaction types, dates, identifiers, currencies, quantities, amounts and fees against the
    export. Use `Decimal` for monetary values and make the sign of purchases and sales explicit in
    your test expectations.
- Reject unknown transaction types and malformed rows with a useful `ParsingError`. Skip a row only
    when you can explain why it contains no relevant transaction, such as a link to a statement.
- Test through the parser's public loading method with small, invented exports. Cover supported
    transactions with non-zero fees, unsupported rows and invalid data. Check that errors identify
    the file and row where available. Include a CLI test showing that the registered option loads
    the broker's data.
- In the broker's documentation, explain how to download the export, which accounts and transactions
    are supported, and what users should do about unsupported cases. Follow the
    [documentation guidance](contributing.md#writing-documentation).
