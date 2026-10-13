"""Model classes for ERI."""

import datetime as dt
from decimal import Decimal

from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, Isin


class ERITransaction(BrokerTransaction):
    """ERI transaction data."""

    def __init__(
        self,
        date: dt.date,
        isin: Isin,
        price: Decimal,
        currency: CurrencyCode,
    ) -> None:
        """Create an ERI transaction."""

        super().__init__(
            date=date,
            action=ActionType.EXCESS_REPORTED_INCOME,
            symbol=None,
            description="",
            quantity=None,
            price=price,
            fees=Decimal(0),
            amount=None,
            currency=currency,
            broker="N/A",
            isin=isin,
        )
