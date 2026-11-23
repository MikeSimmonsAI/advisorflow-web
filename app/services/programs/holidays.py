"""Days the team doesn't follow up on (owner, Nov 22 2026: "after the
holidays ... Thanksgiving ... Christmas ... New Year's").

Thanksgiving Thursday and the Friday after, Christmas Eve, Christmas Day,
New Year's Eve, New Year's Day - and Sundays (nothing goes out on Sundays).
"""
from datetime import date, timedelta


def thanksgiving(year: int) -> date:
    d = date(year, 11, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(weeks=3)


def is_holiday(d: date) -> bool:
    tg = thanksgiving(d.year)
    return (d in (tg, tg + timedelta(days=1))
            or (d.month, d.day) in ((12, 24), (12, 25), (12, 31), (1, 1)))


def next_working_day(d: date) -> date:
    """d itself, or the next day that is neither a holiday nor a Sunday."""
    for _ in range(14):
        if not is_holiday(d) and d.isoweekday() != 7:
            return d
        d += timedelta(days=1)
    return d
