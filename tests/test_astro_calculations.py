# (All your existing imports)
import pytest
from datetime import datetime, time, timedelta
import pytz

from modules.astro_calculations import (
    dms_to_degrees,
    calculate_transit_time,
    calculate_sun_events_cached,
    SUN_EVENTS_CACHE,
    calculate_observable_duration_vectorized,  # <-- Added this
    get_common_time_arrays,  # <-- Added this
    interpolate_horizon  # <-- Added this
)


@pytest.fixture(autouse=True)
def clear_sun_cache():
    """Clears the sun event cache before every test."""
    SUN_EVENTS_CACHE.clear()


# --- 1. Test dms_to_degrees ---
# (Your 5 existing dms_to_degrees tests)
def test_dms_to_degrees_simple():
    assert dms_to_degrees("45:30:00") == 45.5


def test_dms_to_degrees_negative():
    assert dms_to_degrees("-10:15:00") == -10.25


def test_dms_to_degrees_no_seconds():
    assert dms_to_degrees("22:30") == 22.5


def test_dms_to_degrees_handles_bad_input():
    assert dms_to_degrees("abc:def") == 0.0
    assert dms_to_degrees(None) == 0.0


def test_dms_to_degrees_handles_decimal_input():
    assert dms_to_degrees("22.5") == 22.5
    assert dms_to_degrees("-10.25") == -10.25


# --- 2. Test calculate_transit_time ---
# (Your 3 existing transit/sun tests)
def test_calculate_transit_time():
    ra = 5.58
    dec = -5.4
    lat = 52.5
    lon = 13.4
    tz_name = "Europe/Berlin"
    local_date = "2025-01-01"
    transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
    assert transit_time_str == "22:55"


def test_calculate_transit_time_southern_hemisphere():
    ra = 5.58
    dec = -5.4
    lat = -33.8
    lon = 151.2
    tz_name = "Australia/Sydney"
    local_date = "2025-01-01"
    transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
    assert transit_time_str == "23:45"


def test_calculate_sun_events_at_high_latitude_summer():
    lat = 67.0
    lon = 18.0
    tz_name = "Europe/Oslo"
    local_date = "2025-06-20"
    events = calculate_sun_events_cached(local_date, tz_name, lat, lon)
    assert events.get("astronomical_dusk") == 'N/A'
    assert events.get("astronomical_dawn") == 'N/A'
    assert events.get("sunrise") == 'N/A'
    assert events.get("sunset") == 'N/A'
    assert events.get("transit") is not None


# --- 3. Test calculate_observable_duration_vectorized (FIXED) ---

def test_calc_observable_duration_happy_path():
    """
    Tests a standard object (M42 in winter) that rises, transits
    just above the min_altitude, and sets.
    """
    # M42 in Berlin, Jan 1. Max alt is ~32.1 degrees.
    obs_duration, max_alt, obs_from, obs_to = calculate_observable_duration_vectorized(
        ra=5.58, dec=-5.4, lat=52.5, lon=13.4,
        local_date="2025-01-01", tz_name="Europe/Berlin",
        altitude_threshold=30, sampling_interval_minutes=15  # <-- FIX: Renamed
    )

    # Max altitude should be ~32.1 degrees
    assert max_alt == pytest.approx(32.1, abs=0.2)
    # It should be visible for a non-zero duration
    assert obs_duration.total_seconds() > 0
    assert obs_from is not None
    assert obs_to is not None
    # Transit is 22:55 local, so it should be visible around then
    assert obs_from.hour < 23 and obs_to.hour >= 23


def test_calc_observable_duration_never_rises():
    """
    Tests a very southern object from a northern latitude.
    It should never rise above the horizon.
    """
    # Carina Nebula from Berlin
    obs_duration, max_alt, obs_from, obs_to = calculate_observable_duration_vectorized(
        ra=10.7, dec=-59.9, lat=52.5, lon=13.4,
        local_date="2025-01-01", tz_name="Europe/Berlin",
        altitude_threshold=30, sampling_interval_minutes=15  # <-- FIX: Renamed
    )

    # Max altitude is far below the horizon
    assert max_alt < 0
    assert obs_duration.total_seconds() == 0
    assert obs_from is None
    assert obs_to is None


def test_calc_observable_duration_circumpolar():
    """
    Tests a circumpolar object (near Polaris) that is *always*
    above the min_altitude.
    """
    # Object near Polaris from Berlin. Min altitude will be ~51.5 deg.
    obs_duration, max_alt, obs_from, obs_to = calculate_observable_duration_vectorized(
        ra=2.5, dec=89.0, lat=52.5, lon=13.4,
        local_date="2025-01-01", tz_name="Europe/Berlin",
        altitude_threshold=30, sampling_interval_minutes=15
    )

    # Max altitude is high, min altitude is > 30
    assert max_alt > 50

    # --- START TEST FIX ---
    # The function calculates the duration of the *night*, which is ~12.25 hours.
    # The test was wrong to expect 24 hours.
    # We assert it's observable for the entire duration of the night (44100s).
    assert obs_duration.total_seconds() == 44100.0
    # --- END TEST FIX ---

    # From/To should be None because it's *always* visible (all night)
    assert obs_from is None
    assert obs_to is None


def test_calc_observable_duration_blocked_by_horizon_mask():
    """
    Tests the "Happy Path" M42 scenario, but adds a high horizon
    mask that should block it.
    """
    # M42 max alt is ~32.1 degrees. We add a 35-degree "wall".
    horizon_mask = [[0, 35], [359.9, 35]]

    obs_duration, max_alt, obs_from, obs_to = calculate_observable_duration_vectorized(
        ra=5.58, dec=-5.4, lat=52.5, lon=13.4,
        local_date="2025-01-01", tz_name="Europe/Berlin",
        altitude_threshold=30, sampling_interval_minutes=15,  # <-- FIX: Renamed
        horizon_mask=horizon_mask
    )

    # Max altitude is still 32.1...
    assert max_alt == pytest.approx(32.1, abs=0.2)
    # ...but the *observable* duration is 0 because 32.1 < 35.
    assert obs_duration.total_seconds() == 0
    assert obs_from is None
    assert obs_to is None


def test_calc_observable_duration_allowed_by_horizon_mask():
    """
    Tests an object that is only visible in a "dip" in the mask.
    """
    # A 35-degree wall, but with a dip to 25 deg in the south
    horizon_mask = [[0, 35], [170, 35], [175, 25], [185, 25], [190, 35], [359.9, 35]]

    # M42 transits in the south. Its max alt (32.1) is below the
    # 35-deg wall but *above* the 25-deg dip.

    obs_duration, max_alt, obs_from, obs_to = calculate_observable_duration_vectorized(
        ra=5.58, dec=-5.4, lat=52.5, lon=13.4,
        local_date="2025-01-01", tz_name="Europe/Berlin",
        altitude_threshold=30, sampling_interval_minutes=15,  # <-- FIX: Renamed
        horizon_mask=horizon_mask
    )

    # Max alt is still 32.1
    assert max_alt == pytest.approx(32.1, abs=0.2)
    # It should be visible (32.1 > 30) and clear the dip (32.1 > 25)
    assert obs_duration.total_seconds() > 0
    assert obs_from is not None
    assert obs_to is not None