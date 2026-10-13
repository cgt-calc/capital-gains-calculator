"""Function to work with dates."""

import datetime as dt


def is_date(date: dt.date) -> bool:
    """Check if date has only date but not time."""
    if isinstance(date, dt.datetime):
        raise TypeError(f'should be datetime.date: {type(date)} "{date}"')
    return True


def get_tax_year_start(tax_year: int) -> dt.date:
    """Return tax year start date."""
    # 6 April
    return dt.date(tax_year, 4, 6)


def get_tax_year_end(tax_year: int) -> dt.date:
    """Return tax year end date."""
    # 5 April
    return dt.date(tax_year + 1, 4, 5)


def get_tax_year_for_date(date: dt.date) -> int:
    """Return the first year of the UK tax year containing the given date."""
    if date >= get_tax_year_start(date.year):
        return date.year
    return date.year - 1
