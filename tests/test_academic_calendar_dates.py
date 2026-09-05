"""Conversion boundaries and continuity, without database setup."""

import datetime

import nepali_datetime as nd
import pytest

from src.academics.calendar import build_year, year_bounds


@pytest.mark.parametrize("system", ["BS", "AD"])
def test_every_supported_year_has_contiguous_convertible_days(system):
    minimum, maximum = year_bounds(system)
    previous = None
    for year in range(minimum, maximum + 1):
        months = build_year(system, year, [6])
        assert len(months) == 12
        for month in months:
            for day in month.days:
                if previous is not None:
                    assert day.date == previous + datetime.timedelta(days=1)
                bs = nd.date.from_datetime_date(day.date)
                assert bs.to_datetime_date() == day.date
                if system == "BS":
                    assert (bs.year, bs.month, bs.day) == (year, month.index, day.day)
                else:
                    assert (day.date.year, day.date.month, day.date.day) == (
                        year,
                        month.index,
                        day.day,
                    )
                assert day.is_weekend == (day.date.isoweekday() == 6)
                previous = day.date
