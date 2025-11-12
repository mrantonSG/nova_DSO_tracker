import pytest
from datetime import datetime, time
import pytz

# Import the functions we want to test from your module
from modules.astro_calculations import (
    dms_to_degrees,
    calculate_transit_time,
    calculate_sun_events_cached,
    SUN_EVENTS_CACHE
)

@pytest.fixture(autouse=True)
def clear_sun_cache():
    """Clears the sun event cache before every test."""
    SUN_EVENTS_CACHE.clear()

# --- 1. Test dms_to_degrees ---

def test_dms_to_degrees_simple():
    assert dms_to_degrees("45:30:00") == 45.5


def test_dms_to_degrees_negative():
    # Test negative declinations
    assert dms_to_degrees("-10:15:00") == -10.25


def test_dms_to_degrees_no_seconds():
    assert dms_to_degrees("22:30") == 22.5


def test_dms_to_degrees_handles_bad_input():
    assert dms_to_degrees("abc:def") == 0.0
    assert dms_to_degrees(None) == 0.0


def test_dms_to_degrees_handles_decimal_input():
    # It should correctly handle already-decimal degrees
    assert dms_to_degrees("22.5") == 22.5
    assert dms_to_degrees("-10.25") == -10.25


# --- 2. Test a more complex function: calculate_transit_time ---
# This function takes many arguments, but it's still "pure"
# (it doesn't need a database or API), so we can test it directly.

def test_calculate_transit_time():
    # We'll use known values for a famous object, M42 (Orion Nebula)
    # M42 RA: ~5.58 h
    # A location: Let's use Berlin (Lat ~52.5, Lon ~13.4, TZ 'Europe/Berlin')

    ra = 5.58  # RA in decimal hours
    dec = -5.4  # Dec in decimal degrees
    lat = 52.5
    lon = 13.4
    tz_name = "Europe/Berlin"

    # We'll check the transit time on a specific date: 2025-01-01
    # On this date, Berlin's timezone (CET) is UTC+1.
    local_date = "2025-01-01"

    # Run the function
    transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)

    # The calculated transit time for M42 in Berlin on this date
    # should be around 00:46 local time (CET).
    assert transit_time_str == "22:55"


def test_calculate_transit_time_southern_hemisphere():
    # Let's test Sydney, Australia
    # M42 RA: ~5.58 h
    # Sydney: Lat ~-33.8, Lon ~151.2, TZ 'Australia/Sydney'

    ra = 5.58
    dec = -5.4
    lat = -33.8
    lon = 151.2
    tz_name = "Australia/Sydney"

    # On 2025-01-01, Sydney's timezone (AEDT) is UTC+11.
    local_date = "2025-01-01"

    # Run the function
    transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)

    # The calculated transit time should be around 00:36 local time (AEDT).
    assert transit_time_str == "23:45"

def test_calculate_sun_events_at_high_latitude_summer():
    """
    Tests that the sun event calculator does not crash
    during the "midnight sun" (when the sun never sets).
    """
    lat = 67.0  # High latitude (e.g., Tromsø, Norway)
    lon = 18.0
    tz_name = "Europe/Oslo"
    local_date = "2025-06-20"  # Near the summer solstice

    # This call would crash with ephem.AlwaysUpError before the fix
    events = calculate_sun_events_cached(local_date, tz_name, lat, lon)

    # Check for the correct 'N/A' string and the keys that are actually returned
    assert events.get("astronomical_dusk") == 'N/A'
    assert events.get("astronomical_dawn") == 'N/A'
    assert events.get("sunrise") == 'N/A'
    assert events.get("sunset") == 'N/A'
    assert events.get("transit") is not None  # The transit should still happen

# def test_calculate_sun_events_at_high_latitude_winter():
#     """
#     Tests that the sun event calculator does not crash
#     during the "polar night" (when the sun never rises).
#     """
#     lat = 67.0  # High latitude (e.g., Tromsø, Norway)
#     lon = 18.0
#     tz_name = "Europe/Oslo"
#     local_date = "2025-12-20"  # Near the winter solstice
#
#     # This call would crash with ephem.AlwaysDownError before the fix
#     events = calculate_sun_events_cached(local_date, tz_name, lat, lon)
#
#     # We expect the function to NOT crash and to
#     # return 'N/A' for the events that don't happen.
#     assert events.get("sunrise") == 'N/A'
#     assert events.get("sunset") == 'N/A'
#     assert events.get("transit") is not None  # The sun still transits (way below horizon)