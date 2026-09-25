"""DST regression: the dark window must localize dawn on the next calendar day."""
from datetime import datetime, timedelta

import pytest
import pytz

from modules.astro_calculations import (
    calculate_observable_duration_vectorized,
    calculate_sun_events_cached,
)

TZ_NAME = "Europe/Vienna"
LAT, LON = 47.8, 16.1
NGC188_RA, NGC188_DEC = 0.807, 85.25  # circumpolar, >43° all night at lat 47.8
THRESHOLD = 20
SAMPLING = 15


def _count_samples_minutes(dusk_dt, dawn_dt):
    step = timedelta(minutes=SAMPLING)
    count, current = 0, dusk_dt
    while current <= dawn_dt:
        count += 1
        current += step
    return count * SAMPLING


def _expected_and_old(local_date):
    tz = pytz.timezone(TZ_NAME)
    events = calculate_sun_events_cached(local_date, TZ_NAME, LAT, LON)
    base = datetime.strptime(local_date, "%Y-%m-%d")
    dusk_t = datetime.strptime(events["astronomical_dusk"], "%H:%M").time()
    dawn_t = datetime.strptime(events["astronomical_dawn"], "%H:%M").time()

    dusk_dt = tz.localize(datetime.combine(base, dusk_t), is_dst=None)
    dawn_correct = tz.localize(datetime.combine(base + timedelta(days=1), dawn_t), is_dst=None)
    dawn_old = tz.localize(datetime.combine(base, dawn_t)) + timedelta(days=1)

    return _count_samples_minutes(dusk_dt, dawn_correct), _count_samples_minutes(dusk_dt, dawn_old)


@pytest.mark.parametrize("local_date", [
    "2026-10-24",  # clocks go back overnight (CEST -> CET)
    "2027-03-27",  # clocks go forward overnight (CET -> CEST)
])
def test_circumpolar_duration_across_dst_night(local_date):
    expected, old = _expected_and_old(local_date)
    # Guard: the test must discriminate between correct and timedelta-based dawn.
    assert expected != old

    duration, _, _, _ = calculate_observable_duration_vectorized(
        NGC188_RA, NGC188_DEC, LAT, LON, local_date, TZ_NAME, THRESHOLD, SAMPLING
    )
    assert duration.total_seconds() / 60 == expected
