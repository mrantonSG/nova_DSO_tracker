# (All your existing imports)
import pytest
import math
import numpy as np
from datetime import datetime, time, timedelta
import pytz

from modules.astro_calculations import (
    dms_to_degrees,
    calculate_transit_time,
    calculate_sun_events_cached,
    SUN_EVENTS_CACHE,
    calculate_observable_duration_vectorized,
    hms_to_hours,
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
    # ... (setup)
    obs_duration, max_alt, obs_from, obs_to = calculate_observable_duration_vectorized(
        ra=2.5, dec=89.0, lat=52.5, lon=13.4,
        local_date="2025-01-01", tz_name="Europe/Berlin",
        altitude_threshold=30, sampling_interval_minutes=15
    )

    # ... (assert duration, e.g., assert obs_duration.total_seconds() == 44100.0)

    # --- START OF TEST FIX ---
    # From/To should NOT be None. They should be the start
    # and end of the astronomical night.
    assert obs_from is not None
    assert obs_to is not None

    # Check that the times correspond to dusk and dawn
    assert obs_from.hour == 18  # Astronomical dusk on this date
    assert obs_to.hour == 6  # Astronomical dawn on this date
    # --- END OF TEST FIX ---


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

@pytest.mark.parametrize(
    "input_ra, expected_hours",
    [
        # --- plain decimal HOURS should pass through unchanged ---
        ("12.5", 12.5),
        (12.5, 12.5),
        (np.float64(12.5), 12.5),

        # --- HMS strings (space or colon separated) ---
        ("12 30 00", 12.5),
        ("12:30:00", 12.5),
        ("12 30 36", 12 + 30/60 + 36/3600),

        # --- decimal DEGREES should auto-convert to hours ---
        # this is the bug you hit with NGC4490
        ("187.6437", 187.6437 / 15.0),
        (187.6437, 187.6437 / 15.0),
        (np.float64(187.6437), 187.6437 / 15.0),

        # near-boundary sanity checks
        ("359.999", 359.999 / 15.0),  # degrees -> ~23.9999h
        ("24.0001", 24.0001 / 15.0),  # >24 means degrees per your heuristic
    ],
)
def test_hms_to_hours_accepts_hours_hms_and_degrees(input_ra, expected_hours):
    out = hms_to_hours(input_ra)
    assert math.isclose(out, expected_hours, rel_tol=0, abs_tol=1e-6)


def test_hms_to_hours_never_returns_over_24_for_decimal_inputs():
    """
    Guard rail: any purely-decimal RA should end up in [0, 24),
    because if it's bigger it must have been degrees or a bug.
    """
    decimals = ["0", "12.3", "23.999", "187.6", "350.0"]
    for val in decimals:
        out = hms_to_hours(val)
        assert 0.0 <= out < 24.0


@pytest.mark.parametrize(
    "input_dec, expected_deg",
    [
        # decimal degrees pass through
        ("41.6405", 41.6405),
        (-41.6405, -41.6405),

        # DMS variants
        ("+41 38 26", 41 + 38/60 + 26/3600),
        ("-41 38 26", -(41 + 38/60 + 26/3600)),
        ("41:38:26", 41 + 38/60 + 26/3600),
        ("-41:38:26", -(41 + 38/60 + 26/3600)),
    ],
)
def test_dms_to_degrees_parsing(input_dec, expected_deg):
    out = dms_to_degrees(input_dec)
    assert math.isclose(out, expected_deg, rel_tol=0, abs_tol=1e-6)



# ------------------------------
# Helpers for round-trip tests
# (tests only — not production)
# ------------------------------
def hours_to_hms_str(hours: float) -> str:
    """Convert decimal hours to 'HH MM SS.s' test string."""
    hours = hours % 24.0
    h = int(hours)
    m_float = (hours - h) * 60.0
    m = int(m_float)
    s = (m_float - m) * 60.0
    return f"{h:02d} {m:02d} {s:06.3f}"


def deg_to_dms_str(deg: float) -> str:
    """Convert decimal degrees to '±DD MM SS.s' test string."""
    sign = "-" if deg < 0 else "+"
    deg_abs = abs(deg)
    d = int(deg_abs)
    m_float = (deg_abs - d) * 60.0
    m = int(m_float)
    s = (m_float - m) * 60.0
    return f"{sign}{d:02d} {m:02d} {s:06.3f}"


# ------------------------------
# RA unit/consistency guard rails
# ------------------------------
@pytest.mark.parametrize("ra_deg", [0.0, 15.0, 120.0, 187.6437, 359.999])
def test_ra_degree_inputs_convert_to_hours_and_stay_in_range(ra_deg):
    """
    If the input is >24, it's degrees and must be converted.
    If the input is <=24, it's decimal hours and must be taken as-is.
    """
    out = hms_to_hours(str(ra_deg))

    # Always within the valid range for hours
    assert 0.0 <= out < 24.0

    if ra_deg > 24.0:
        # degrees → hours
        assert math.isclose(out, ra_deg / 15.0, abs_tol=1e-6)
    else:
        # <=24 → treat as hours
        assert math.isclose(out, ra_deg, abs_tol=1e-6)


def test_ra_decimal_hours_leave_unchanged_and_in_range():
    """
    Pure decimal inputs <= 24 are treated as hours.
    """
    for ra_h in [0.0, 1.2345, 12.5, 23.999]:
        out = hms_to_hours(str(ra_h))
        assert math.isclose(out, ra_h, abs_tol=1e-6)
        assert 0.0 <= out < 24.0


def test_ra_dec_pair_typical_simbad_case():
    """
    Typical SIMBAD case that caused your bug:
    RA as decimal degrees, DEC as decimal degrees.
    RA must be normalized to hours; DEC stays degrees.
    """
    ra_in = "187.6437"   # degrees
    dec_in = "41.6405"   # degrees

    ra_h = hms_to_hours(ra_in)
    dec_d = dms_to_degrees(dec_in)

    assert math.isclose(ra_h, 187.6437 / 15.0, abs_tol=1e-6)
    assert math.isclose(dec_d, 41.6405, abs_tol=1e-6)


# ------------------------------
# Round-trip formatting tests
# ------------------------------
@pytest.mark.parametrize("ra_h", [0.0, 0.001, 1.5, 12.34567, 23.9999])
def test_ra_round_trip_hours_to_hms_and_back(ra_h):
    """
    If we format hours as HMS and parse back, we should recover the same value.
    """
    hms = hours_to_hms_str(ra_h)
    out = hms_to_hours(hms)
    assert math.isclose(out, ra_h % 24.0, abs_tol=1e-4)  # small tolerance due to formatting


@pytest.mark.parametrize("dec_d", [-89.999, -41.6405, -0.1, 0.0, 12.5, 41.6405, 89.999])
def test_dec_round_trip_deg_to_dms_and_back(dec_d):
    """
    Same idea for DEC: degrees -> DMS string -> degrees
    """
    dms = deg_to_dms_str(dec_d)
    out = dms_to_degrees(dms)
    assert math.isclose(out, dec_d, abs_tol=1e-4)


# ------------------------------
# DEC edge / bounds tests
# ------------------------------
@pytest.mark.parametrize(
    "dec_str, expected",
    [
        ("+90 00 00", 90.0),
        ("-90 00 00", -90.0),
        ("+89:59:59.9", 89.9999722222),
        ("-89:59:59.9", -89.9999722222),
        ("0 0 0", 0.0),
    ]
)
def test_dec_edge_cases_parse_correctly(dec_str, expected):
    out = dms_to_degrees(dec_str)
    assert math.isclose(out, expected, abs_tol=1e-4)
    assert -90.0 <= out <= 90.0


def test_dec_bad_inputs_never_crash_and_return_zero():
    for bad in ["abc", "abc:def", "++--", "12:xx:yy", ""]:
        assert dms_to_degrees(bad) == 0.0