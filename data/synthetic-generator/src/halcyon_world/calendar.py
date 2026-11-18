"""Business-day calendars per country. Deliberately small: weekends plus fixed holidays."""

from datetime import date, timedelta

# Fixed-date holidays only, enough to make "business day" differ from "weekday".
_HOLIDAYS: dict[str, set[tuple[int, int]]] = {
    "DE": {(1, 1), (5, 1), (10, 3), (12, 25), (12, 26)},
    "GB": {(1, 1), (12, 25), (12, 26)},
}


def is_business_day(country: str, day: date) -> bool:
    return day.weekday() < 5 and (day.month, day.day) not in _HOLIDAYS.get(country, set())


def next_business_day(country: str, day: date) -> date:
    while not is_business_day(country, day):
        day += timedelta(days=1)
    return day


def days(start: date, end: date) -> list[date]:
    """Every date in the half-open interval [start, end)."""
    return [start + timedelta(days=i) for i in range((end - start).days)]
