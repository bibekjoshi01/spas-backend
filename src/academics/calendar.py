"""
The academic year, laid out as months of days.

Bikram Sambat months do not line up with Gregorian ones — a BS month runs 29 to
32 days and starts partway through an AD month — so a calendar that only
relabelled a Gregorian grid would show every Nepali month as a fragment. This
module builds the grid itself, in whichever system was asked for, and the
frontend draws what it is given.

Conversion lives here and nowhere else on purpose. The BS calendar is a
published table rather than a formula, and independent implementations of it
disagree: the table shipped with `nepali-datetime` and the one in the popular
npm package part company from BS 2084 onward. Two tables would mean a holiday
saved on one screen landing on a different day on another, so there is one —
this one — and the client is told the answer rather than working it out.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import nepali_datetime as nd
from django.utils import timezone

from .constants import CalendarSystem, Weekday

#: The BS years the shipped conversion table covers.
MIN_BS_YEAR = nd.MINYEAR
MAX_BS_YEAR = nd.MAXYEAR

#: Gregorian years reachable from that table, kept a year inside each edge so
#: every month of a listed year converts without falling off the end.
MIN_AD_YEAR = nd.date(MIN_BS_YEAR, 1, 1).to_datetime_date().year + 1
MAX_AD_YEAR = nd.date(MAX_BS_YEAR, 12, 30).to_datetime_date().year - 1

_BS_MONTHS_EN = (
    "Baishakh",
    "Jestha",
    "Ashadh",
    "Shrawan",
    "Bhadra",
    "Ashwin",
    "Kartik",
    "Mangsir",
    "Poush",
    "Magh",
    "Falgun",
    "Chaitra",
)
_AD_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_NEPALI_DIGITS = nd._DIGIT_NP  # the package exposes no public alias


def to_nepali_digits(value: int | str) -> str:
    """`12` -> `१२`, for the numerals a Nepali reader expects on the date."""
    return "".join(_NEPALI_DIGITS[int(ch)] if ch.isdigit() else ch for ch in str(value))


def days_in_bs_month(year: int, month: int) -> int:
    """
    How many days that Bikram Sambat month holds.

    Derived from the package's cumulative day table rather than probing dates,
    so a month that ends on the 32nd is as cheap to ask about as one that ends
    on the 29th.
    """
    # Index 0 of the shipped row is a `-1` sentinel, not a zero offset, so the
    # first month counts from zero rather than from it.
    cumulative = nd._CALENDAR[year]  # no public accessor exists
    previous = 0 if month == 1 else cumulative[month - 1]
    return int(cumulative[month] - previous)


@dataclass(frozen=True)
class CalendarDay:
    """One cell of the grid, addressed by the AD date everything is stored against."""

    date: datetime.date
    day: int
    day_label: str
    weekday: int
    is_weekend: bool


@dataclass(frozen=True)
class CalendarMonth:
    """One month card: its name, and the days that fall inside it."""

    index: int
    name: str
    name_nepali: str
    days: list[CalendarDay] = field(default_factory=list)


def _weekday(value: datetime.date) -> int:
    """`date.isoweekday()` — the numbering `Weekday` already uses."""
    return value.isoweekday()


def _bs_months(year: int, weekend: frozenset[int]) -> list[CalendarMonth]:
    months = []
    for month in range(1, 13):
        days = []
        for day in range(1, days_in_bs_month(year, month) + 1):
            ad = nd.date(year, month, day).to_datetime_date()
            weekday = _weekday(ad)
            days.append(
                CalendarDay(
                    date=ad,
                    day=day,
                    day_label=to_nepali_digits(day),
                    weekday=weekday,
                    is_weekend=weekday in weekend,
                )
            )
        months.append(
            CalendarMonth(
                index=month,
                name=_BS_MONTHS_EN[month - 1],
                name_nepali=nd._MONTHNAMES_NP[month],
                days=days,
            )
        )
    return months


def _ad_months(year: int, weekend: frozenset[int]) -> list[CalendarMonth]:
    months = []
    for month in range(1, 13):
        start = datetime.date(year, month, 1)
        end = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
        days = []
        current = start
        while current < end:
            weekday = _weekday(current)
            days.append(
                CalendarDay(
                    date=current,
                    day=current.day,
                    day_label=str(current.day),
                    weekday=weekday,
                    is_weekend=weekday in weekend,
                )
            )
            current += datetime.timedelta(days=1)
        months.append(
            CalendarMonth(
                index=month,
                name=_AD_MONTHS_EN[month - 1],
                name_nepali=_AD_MONTHS_EN[month - 1],
                days=days,
            )
        )
    return months


def year_bounds(system: str) -> tuple[int, int]:
    """The first and last year this system can be asked for."""
    if system == CalendarSystem.BS.value:
        return MIN_BS_YEAR, MAX_BS_YEAR
    return MIN_AD_YEAR, MAX_AD_YEAR


def current_year(system: str) -> int:
    """Today's year in the requested system."""
    today = timezone.localdate()
    if system == CalendarSystem.BS.value:
        return int(nd.date.from_datetime_date(today).year)
    return today.year


def build_year(system: str, year: int, weekend_days: list[int]) -> list[CalendarMonth]:
    """
    The twelve months of one year, in the requested system.

    Every day carries the AD date it is stored against, so a caller can attach
    entries without converting anything itself.
    """
    weekend = frozenset(int(day) for day in weekend_days if day in Weekday.values)
    if system == CalendarSystem.BS.value:
        return _bs_months(year, weekend)
    return _ad_months(year, weekend)


def to_bs_string(value: datetime.date) -> str:
    """The Gregorian date written as Bikram Sambat, e.g. `2082-05-20`."""
    bs = nd.date.from_datetime_date(value)
    return f"{bs.year:04d}-{bs.month:02d}-{bs.day:02d}"
