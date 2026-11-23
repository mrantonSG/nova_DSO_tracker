"""transit_local_datetime() in nova/ai/routes.py places a transit 'HH:MM' on the right
calendar day of the observing night.

calculate_transit_time() reports the upper transit in the window local noon to noon, so
a transit after midnight belongs to the day after the night's date. Before the fix the
AI pre-filter combined it with the night's own date, a day early, and measured the Moon
separation 24 hours off.
"""
from datetime import datetime

import pytest
import pytz

from nova.ai.routes import transit_local_datetime

NY = "America/New_York"


@pytest.mark.parametrize("hhmm, expected_day", [
    ("12:00", "2026-11-22"),   # the window starts at local noon
    ("20:08", "2026-11-22"),   # evening: same calendar day
    ("23:59", "2026-11-22"),
    ("00:00", "2026-11-23"),   # after midnight: the next calendar day
    ("00:27", "2026-11-23"),
    ("08:06", "2026-11-23"),
    ("11:59", "2026-11-23"),   # the window ends at the next local noon
])
def test_transit_lands_in_the_observing_night(hhmm, expected_day):
    got = transit_local_datetime("2026-11-22", hhmm, NY)
    assert got.strftime("%Y-%m-%d %H:%M") == f"{expected_day} {hhmm}"
    assert got.tzinfo is not None and got.utcoffset() == pytz.timezone(NY).utcoffset(
        datetime.strptime(f"{expected_day} {hhmm}", "%Y-%m-%d %H:%M"))


def test_after_midnight_transit_across_a_month_and_year_end():
    got = transit_local_datetime("2026-12-31", "02:15", "Europe/Vienna")
    assert got.strftime("%Y-%m-%d %H:%M %z") == "2027-01-01 02:15 +0100"


def test_after_midnight_transit_on_the_night_clocks_go_back():
    """Europe/Vienna leaves CEST at 03:00 on 2026-10-25: 05:00 that morning is CET."""
    got = transit_local_datetime("2026-10-24", "05:00", "Europe/Vienna")
    assert got.strftime("%Y-%m-%d %H:%M %z") == "2026-10-25 05:00 +0100"
