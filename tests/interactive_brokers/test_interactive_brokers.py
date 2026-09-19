"""Test Interactive Brokers parser."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
from cgt_calc.parsers.interactive_brokers import InteractiveBrokersParser
from tests.utils import build_cmd, report_path, stderr_alerts


class TestInteractiveBrokers:
    """Test Interactive Brokers parser."""

    base_header = (
        """
Statement,Header,Field Name,Field Value
Statement,Data,Title,Transaction History
Statement,Data,Period,"January 1, 2024 - December 31, 2025"
Statement,Data,WhenGenerated,"2025-12-31, 23:59:59 EST"
Summary,Header,Field Name,Field Value
Summary,Data,Base Currency,GBP
Summary,Data,Starting Cash,0.0
Summary,Data,Change,200.00
Summary,Data,Ending Cash,200.00
Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Gross Amount ,Commission,Net Amount
""".strip()
        + "\n"
    )

    base_header_with_foreign_currency = (
        """
Statement,Header,Field Name,Field Value
Statement,Data,Title,Transaction History
Statement,Data,Period,"January 1, 2024 - December 31, 2025"
Statement,Data,WhenGenerated,"2025-12-31, 23:59:59 EST"
Summary,Header,Field Name,Field Value
Summary,Data,Base Currency,GBP
Summary,Data,Starting Cash,0.0
Summary,Data,Change,200.00
Summary,Data,Ending Cash,200.00
Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount,Exchange Rate
""".strip()
        + "\n"
    )

    def test_single_buy(self, tmp_path: Path) -> None:
        """Test parsing a single buy transaction."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-10-13,U***08040,ISHARES NASDAQ 100 USD ACC,Buy,CNX1,1.0,1060.3,-1060.3,-3.0,-1063.3\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        txn = transactions[0]
        expected = BrokerTransaction(
            date=date(2025, 10, 13),
            action=ActionType.BUY,
            symbol="CNX1",
            description="ISHARES NASDAQ 100 USD ACC",
            quantity=Decimal("1.0"),
            price=Decimal("1060.3"),
            fees=Decimal("3.0"),
            amount=Decimal("-1063.3"),
            currency=CurrencyCode("GBP"),
            broker="Interactive Brokers",
            isin=None,
        )

        assert txn.date == expected.date
        assert txn.action == expected.action
        assert txn.symbol == expected.symbol
        assert txn.description == expected.description
        assert txn.quantity == expected.quantity
        assert txn.price == expected.price
        assert txn.fees == expected.fees
        assert txn.amount == expected.amount
        assert txn.currency == expected.currency
        assert txn.broker == expected.broker

    def test_isin_is_read_from_the_description(self, tmp_path: Path) -> None:
        """Dividend rows name the security as TICKER(ISIN); trades do not."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-10-01,U***08040,"
            "VT(US9220427424) Cash Dividend USD 0.3272 per Share (Ordinary Dividend),"
            "Payment in Lieu,VT,-,-,1.0,-,1.0\n"
            + "Transaction History,Data,2025-10-13,U***08040,"
            "ISHARES NASDAQ 100 USD ACC,Buy,CNX1,1.0,1060.3,-1060.3,-3.0,-1063.3\n"
        )

        dividend, trade = InteractiveBrokersParser().load_from_file(csv_file)

        assert dividend.isin == "US9220427424"
        assert trade.isin is None

    def test_invalid_isin_in_the_description_is_ignored(self, tmp_path: Path) -> None:
        """A malformed code must not be passed off as an ISIN."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-10-01,U***08040,"
            "VT(US9220427429) Cash Dividend USD 0.3272 per Share (Ordinary Dividend),"
            "Payment in Lieu,VT,-,-,1.0,-,1.0\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert transactions[0].isin is None

    def test_buy_foreign_currency_price(self, tmp_path: Path) -> None:
        """Test that a buy with a foreign-currency price is converted to GBP correctly.

        In IBKR exports the Gross/Net Amount and Commission are always in the account's
        base currency (GBP), while Price can be in the instrument's trading currency.
        The parser must convert Price to GBP using the Exchange Rate so that the
        internal consistency check (quantity x price + fees ≈ |amount|) passes.

        Transaction: Buy 217 IWDE at EUR 67.71, exchange rate 0.88542 EUR→GBP.
        GBP gross = 217 x 67.71 x 0.88542 = 13009.5380394
        GBP commission = 6.5047690197
        GBP net = 13016.0428084197
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2023-02-10,U***9143,ISHARES MSCI WORLD EUR-H,Buy,IWDE,217,67.71,EUR,-13009.5380394,-6.5047690197,-13016.0428084197,0.88542\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        txn = transactions[0]
        expected_price_gbp = Decimal("67.71") * Decimal("0.88542")
        assert txn.date == date(2023, 2, 10)
        assert txn.action == ActionType.BUY
        assert txn.symbol == "IWDE"
        assert txn.quantity == Decimal(217)
        assert txn.price == expected_price_gbp
        assert txn.fees == Decimal("6.5047690197")
        assert txn.amount == Decimal("-13016.0428084197")
        assert txn.currency == "GBP"

    def test_run_with_interactive_brokers_file(
        self, request: pytest.FixtureRequest
    ) -> None:
        """Runs the script and verifies it doesn't fail."""
        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            "tests/interactive_brokers/data/test_basic.csv",
            "--output",
            report_path(request),
        )
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
        expected_file = (
            Path("tests") / "interactive_brokers" / "data" / "expected_output.txt"
        )
        expected = expected_file.read_text(encoding="utf-8")
        cmd_str = " ".join([param or "''" for param in cmd])
        assert result.stdout == expected, (
            "Run with example files generated unexpected outputs, "
            "if you added new features update the test with:\n"
            f"{cmd_str} > {expected_file}"
        )

    def test_ordinary_dividend_and_its_withholding(self, tmp_path: Path) -> None:
        """An ordinary dividend is income, and the US tax on it is withheld.

        The parser knew "Payment in Lieu", which arrives instead of a dividend
        when the holding is out on loan, but not the ordinary dividend that
        every holder of a paying security receives. Anyone with one hit
        "Unknown type: 'Dividend'" on their first real statement.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-11-03,U***00000,VT(US9220427424) Cash Dividend USD 0.50 per Share,Dividend,VT,-,-,20.0,-,20.0\n"
            + "Transaction History,Data,2025-11-03,U***00000,VT(US9220427424) Cash Dividend - US Tax,Foreign Tax Withholding,VT,-,-,-3.0,-,-3.0\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert [t.action for t in transactions] == [
            ActionType.DIVIDEND,
            ActionType.DIVIDEND_TAX,
        ]
        assert transactions[0].amount == Decimal("20.0")
        assert transactions[0].symbol == "VT"
        assert transactions[1].amount == Decimal("-3.0")

    def test_fx_translation_adjusts_the_balance_only(self, tmp_path: Path) -> None:
        """Revaluing a currency balance is not a disposal.

        IBKR reports "FX Translations P&L" as an Adjustment. It moves the cash
        balance and creates no taxable event: exchange movement on a personal
        currency balance is outside CGT. The Schwab parser treats its own
        Adjustment the same way.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-11-30,U***00000,FX Translations P&L,Adjustment,-,-,-,0.55,-,0.55\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        assert transactions[0].action == ActionType.ADJUSTMENT
        assert transactions[0].amount == Decimal("0.55")
        # No symbol, no quantity and no price, so it cannot become an
        # acquisition or a disposal however it is grouped downstream. IBKR
        # writes "-" in all three, and only the numeric columns read that as
        # absent on their own, so the symbol is pinned here too.
        assert transactions[0].symbol is None
        assert transactions[0].quantity is None
        assert transactions[0].price is None

    def test_forex_trade_component_is_an_adjustment_not_a_fee(
        self, tmp_path: Path
    ) -> None:
        """A currency conversion is not a charge against a holding.

        IBKR files the base-currency net of an FX trade under the currency
        pair as a "Forex Trade Component". Its Net Amount is positive when the
        conversion leaves more base currency behind and negative when it
        leaves less, and neither direction is a fee: a positive one is refused
        outright, and a negative one opens a pooled cost for a currency pair.
        "Other Fee", which really is a charge on a holding, keeps its meaning.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-10-01,U***00000,"
            "Net Amount in Base from Forex Trade: 800 GBP.USD,"
            "Forex Trade Component,GBP.USD,800,1.32,0.16,-0.02,0.14\n"
            + "Transaction History,Data,2025-10-02,U***00000,"
            "Net Amount in Base from Forex Trade: -800 GBP.USD,"
            "Forex Trade Component,GBP.USD,-800,1.32,-0.18,-,-0.18\n"
            + "Transaction History,Data,2025-10-03,U***00000,"
            "CNX1 ADR Fee,Other Fee,CNX1,-,-,-1.5,-,-1.5\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert [t.action for t in transactions] == [
            ActionType.ADJUSTMENT,
            ActionType.ADJUSTMENT,
            ActionType.FEE,
        ]
        assert [t.amount for t in transactions] == [
            Decimal("0.14"),
            Decimal("-0.18"),
            Decimal("-1.5"),
        ]
        # Neither direction keeps anything that could be read as a security,
        # and the description still names the pair the movement came from.
        for component in transactions[:2]:
            assert component.symbol is None
            assert component.quantity is None
            assert component.price is None
            assert component.fees == Decimal(0)
            assert "GBP.USD" in component.description
        assert transactions[2].symbol == "CNX1"

    def test_forex_trade_component_moves_the_base_currency_balance(
        self, tmp_path: Path
    ) -> None:
        """Net Amount is in the base currency whatever priced the leg.

        In the extended format a currency-pair row can name a Price Currency,
        and the price is converted to GBP only when the row also carries an
        Exchange Rate. Without one the price stays foreign, and taking that as
        the transaction currency would file a GBP Net Amount against a USD
        balance instead.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2025-10-02,U***00000,"
            "Net Amount in Base from Forex Trade: 800 GBP.USD,"
            "Forex Trade Component,GBP.USD,800,1.32,USD,0.18,-,0.18,-\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        assert transactions[0].action == ActionType.ADJUSTMENT
        assert transactions[0].currency == CurrencyCode("GBP")
        assert transactions[0].amount == Decimal("0.18")

    @pytest.mark.parametrize("exchange_rate", ["-", "1.32"])
    def test_priced_row_without_a_price_records_the_base_currency_amount(
        self, tmp_path: Path, exchange_rate: str
    ) -> None:
        """Price Currency describes the price, not the Net Amount.

        A dividend row can name the currency the security trades in while
        stating no price of its own, with or without an Exchange Rate. Net
        Amount is in the account's base currency either way, so the
        transaction is in GBP and nothing is left to convert.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2025-10-02,U***00000,"
            "VT(US9220427424) Cash Dividend,Dividend,VT,-,-,USD,100.0,-,100.0,"
            f"{exchange_rate}\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        assert transactions[0].currency == CurrencyCode("GBP")
        assert transactions[0].amount == Decimal("100.0")
        assert transactions[0].price is None

    def test_dividend_priced_in_a_foreign_currency_is_not_converted(
        self, tmp_path: Path
    ) -> None:
        """A base-currency dividend must not be reported in the price currency.

        Taking Price Currency as the transaction currency filed a GBP 100
        dividend as USD 100, moved a USD balance the account never held and
        converted the proceeds down to about GBP 73.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2025-01-01,U***00000,"
            "Electronic Fund Transfer,Deposit,-,-,-,-,1000.0,-,1000.0,-\n"
            + "Transaction History,Data,2025-10-02,U***00000,"
            "VT(US9220427424) Cash Dividend,Dividend,VT,-,-,USD,100.0,-,100.0,-\n"
        )

        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            str(csv_file),
            "--output",
            str(tmp_path / "out"),
        )
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
        assert "(USD)" not in result.stdout
        assert "Final balance\n  Interactive Brokers: 1100.00 (GBP)" in result.stdout
        assert "VT: 100.00 (GBP)" in result.stdout
        assert re.search(r"Proceeds:\s+£100\.00", result.stdout)

    def test_foreign_price_without_an_exchange_rate_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A price nothing can convert cannot be checked against the amount.

        The amount columns are in GBP, so a EUR price with no Exchange Rate
        would fail the quantity x price + fees check anyway. Say why instead
        of guessing a rate or dropping the price.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2023-02-10,U***9143,"
            "ISHARES MSCI WORLD EUR-H,Buy,IWDE,217,67.71,EUR,"
            "-13009.5380394,-6.5047690197,-13016.0428084197,-\n"
        )

        with pytest.raises(ParsingError) as excinfo:
            InteractiveBrokersParser().load_from_file(csv_file)

        assert "Price is in EUR" in str(excinfo.value)
        assert "Exchange Rate" in str(excinfo.value)

    def test_foreign_price_without_an_exchange_rate_column_is_refused(
        self, tmp_path: Path
    ) -> None:
        """Price Currency and Exchange Rate are independent optional columns.

        A header can carry the first and not the second. Reading the missing
        column as a 1:1 rate would pass a EUR price off as a GBP one, so such
        a row is refused just like one whose Exchange Rate is empty.
        """
        header = self.base_header_with_foreign_currency.replace(
            ",Exchange Rate\n", "\n"
        )
        assert "Exchange Rate" not in header
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            header + "Transaction History,Data,2023-02-10,U***9143,"
            "ISHARES MSCI WORLD EUR-H,Buy,IWDE,217,67.71,EUR,"
            "-13009.5380394,-6.5047690197,-13016.0428084197\n"
        )

        with pytest.raises(ParsingError) as excinfo:
            InteractiveBrokersParser().load_from_file(csv_file)

        assert "Price is in EUR" in str(excinfo.value)
        assert "Exchange Rate" in str(excinfo.value)

    def test_foreign_priced_fee_without_an_exchange_rate_is_not_refused(
        self, tmp_path: Path
    ) -> None:
        """Only a Buy or Sell reads the price back to check it against the amount.

        add_management_fee never reads a fee row's price, so a Price Currency
        with no Exchange Rate to convert it is left as is rather than refusing
        a row the calculation does not check it on.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2025-10-03,U***00000,"
            "CNX1 ADR Fee,Other Fee,CNX1,-,1.5,USD,-1.5,-,-1.5,-\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        assert transactions[0].action == ActionType.FEE
        assert transactions[0].currency == CurrencyCode("GBP")
        assert transactions[0].amount == Decimal("-1.5")
        assert transactions[0].price == Decimal("1.5")

    def test_forex_trade_component_leaves_no_fee_in_the_report(
        self, tmp_path: Path
    ) -> None:
        """A currency pair must not reach the report as a holding with a cost.

        The pooled cost a fee opens is invisible in the printed summary while
        the quantity is zero, so the rendered report is what gives it away: on
        the fee path this file produces a "Management fee for GBP.USD" section
        for a symbol that is not a security.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-01-01,U***00000,"
            "Electronic Fund Transfer,Deposit,-,-,-,1000.0,-,1000.0\n"
            + "Transaction History,Data,2025-10-02,U***00000,"
            "Net Amount in Base from Forex Trade: -800 GBP.USD,"
            "Forex Trade Component,GBP.USD,-800,1.32,-0.18,-,-0.18\n"
        )

        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            str(csv_file),
            "--output",
            str(tmp_path / "out"),
            keep_tex=True,
        )
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
        assert "Final balance\n  Interactive Brokers: 999.82 (GBP)" in result.stdout
        assert "GBP.USD" not in (tmp_path / "out.tex").read_text(encoding="utf-8")

    def test_same_day_round_trip_buys_before_its_own_sell(self, tmp_path: Path) -> None:
        """A security bought and sold on one day must fill its pool first.

        Sorting every sell before every buy funds the day's purchases, but it
        empties the pool of a security whose buy and sell are both on that
        day, and the disposal is then refused as "not owned". The round
        trip's own buy has to lead; other sells still come before it. Where
        the sale falls in the day does not change the gain, which TCGA 1992
        s105 works out by matching the day's acquisitions and disposals with
        each other.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-01-01,U***00000,Electronic Fund Transfer,Deposit,-,-,-,5000.0,-,5000.0\n"
            # Listed sell-first, and bought and sold on the same day.
            + "Transaction History,Data,2025-06-02,U***00000,AAA STOCK,Sell,AAA,-10.0,110.0,1100.0,-,1100.0\n"
            + "Transaction History,Data,2025-06-02,U***00000,AAA STOCK,Buy,AAA,10.0,100.0,-1000.0,-,-1000.0\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert [(t.action, t.symbol) for t in transactions[1:]] == [
            (ActionType.BUY, "AAA"),
            (ActionType.SELL, "AAA"),
        ]

    def test_buy_before_same_day_sell_does_not_go_negative(
        self, tmp_path: Path
    ) -> None:
        """A same-day buy listed before the sell funding it must not go negative.

        The file lists the buy of AAA before the sell of BBB on the same day,
        even though the cash to pay for the buy only exists once the sell is
        accounted for. If the transactions were processed in file order the
        cash balance would briefly go negative and trip the balance check.
        Sorting sells before buys on the same day (post_process_transactions)
        avoids that, and the amounts are chosen so the buy exactly consumes
        the sell's proceeds, leaving a final cash balance of zero.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-01-01,U***00000,Electronic Fund Transfer,Deposit,-,-,-,1000.0,-,1000.0\n"
            + "Transaction History,Data,2025-01-01,U***00000,BBB STOCK,Buy,BBB,10.0,100.0,-1000.0,-,-1000.0\n"
            # Same day, buy listed before the sell that funds it.
            + "Transaction History,Data,2025-06-02,U***00000,AAA STOCK,Buy,AAA,10.0,100.0,-1000.0,-,-1000.0\n"
            + "Transaction History,Data,2025-06-02,U***00000,BBB STOCK,Sell,BBB,-10.0,100.0,1000.0,-,1000.0\n"
        )

        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            str(csv_file),
            "--output",
            str(tmp_path / "out"),
        )
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
        assert "Final balance\n  Interactive Brokers: 0.00 (GBP)" in result.stdout

    def test_withholding_before_its_dividend_does_not_go_negative(
        self, tmp_path: Path
    ) -> None:
        """IBKR lists tax withheld at source before the dividend it came from.

        A statement that does not reach back to the opening of the account
        starts at a zero balance, so in file order the withholding is the first
        row and takes the balance negative before the dividend that covers it
        arrives on the same day.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-06-24,U***00000,"
            "VT(US9220427424) Cash Dividend - US Tax,Foreign Tax Withholding,"
            "VT,-,-,-15.0,-,-15.0\n" + "Transaction History,Data,2025-06-24,U***00000,"
            "VT(US9220427424) Cash Dividend USD 0.5 per Share,Dividend,"
            "VT,-,-,100.0,-,100.0\n"
        )

        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            str(csv_file),
            "--output",
            str(tmp_path / "out"),
        )
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
