from datetime import datetime, timedelta

import pytz

import nova
from nova import warm_main_cache, nightly_curves_cache
from modules.astro_calculations import calculate_observable_duration_vectorized, calculate_sun_events_cached

USERNAME = "default"
LOC_NAME = "Test Loc"
LAT, LON, TZ = 47.8, 16.1, "Europe/Vienna"
LOCAL_DATE = "2026-09-20"
ALT_THRESHOLD = 20
SAMPLING = 15
HORIZON_MASK = None  # Test location defines no horizon mask
# NGC 188: RA 00h48m (12.1 deg = 0.807 h), Dec +85.25 -> circumpolar at 47.8N
OBJ_NAME, OBJ_RA_H, OBJ_DEC = "NGC 188", 12.1 / 15.0, 85.25


class _FixedDatetime(datetime):
    """now() lands on 2026-09-21 03:00 local, so now - 12h gives local_date 2026-09-20."""

    @classmethod
    def now(cls, tz=None):
        fixed = pytz.timezone(TZ).localize(datetime(2026, 9, 21, 3, 0))
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


def _no_threads(*args, **kwargs):
    raise AssertionError("warm_main_cache must not spawn threads with trigger_outlook=False")


def _dark_window_minutes():
    sun_events = calculate_sun_events_cached(LOCAL_DATE, TZ, LAT, LON)
    local_tz = pytz.timezone(TZ)
    date_obj = datetime.strptime(LOCAL_DATE, "%Y-%m-%d")
    dusk_dt = local_tz.localize(datetime.combine(
        date_obj, datetime.strptime(sun_events["astronomical_dusk"], "%H:%M").time()))
    dawn_dt = local_tz.localize(datetime.combine(
        date_obj, datetime.strptime(sun_events["astronomical_dawn"], "%H:%M").time()))
    if dawn_dt <= dusk_dt:
        dawn_dt += timedelta(days=1)
    return (dawn_dt - dusk_dt).total_seconds() / 60, dusk_dt, dawn_dt


def test_warm_main_cache_uses_dark_window(monkeypatch):
    monkeypatch.setattr(nova, "datetime", _FixedDatetime)
    monkeypatch.setattr("threading.Thread", _no_threads)
    nightly_curves_cache.clear()

    user_config = {
        "locations": {LOC_NAME: {"lat": LAT, "lon": LON, "timezone": TZ,
                                 "altitude_threshold": ALT_THRESHOLD}},
        "objects": [{"Object": OBJ_NAME, "RA": OBJ_RA_H, "DEC": OBJ_DEC, "enabled": True}],
    }

    try:
        warm_main_cache(USERNAME, LOC_NAME, user_config, SAMPLING, trigger_outlook=False)

        cache_key = (f"{USERNAME}_{OBJ_NAME.lower().replace(' ', '_')}_{LOCAL_DATE}"
                     f"_{LAT:.4f}_{LON:.4f}_{ALT_THRESHOLD}_{SAMPLING}")
        assert cache_key in nightly_curves_cache, "warm_main_cache did not populate the cache entry"
        cached = nightly_curves_cache[cache_key]

        # 1. Exact equality with the dashboard batch route (api.py) calculation + conversion
        obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
            OBJ_RA_H, OBJ_DEC, LAT, LON, LOCAL_DATE, TZ, ALT_THRESHOLD, SAMPLING, HORIZON_MASK)
        expected_minutes = int(obs_duration.total_seconds() / 60) if obs_duration else 0
        expected_max_alt = round(max_alt, 1) if max_alt is not None else "N/A"

        assert cached["obs_duration_minutes"] == expected_minutes, (
            f"obs_duration_minutes: cached={cached['obs_duration_minutes']!r} "
            f"expected={expected_minutes!r} (batch route)")
        assert cached["max_altitude"] == expected_max_alt, (
            f"max_altitude: cached={cached['max_altitude']!r} "
            f"expected={expected_max_alt!r} (batch route)")

        # 2. Physical bound: observable time cannot exceed the astronomical dark window
        dark_window_minutes, dusk_dt, dawn_dt = _dark_window_minutes()
        assert cached["obs_duration_minutes"] <= dark_window_minutes + SAMPLING, (
            f"obs_duration_minutes={cached['obs_duration_minutes']} exceeds dark window "
            f"{dark_window_minutes:.0f} min ({dusk_dt:%Y-%m-%d %H:%M} -> {dawn_dt:%Y-%m-%d %H:%M}) "
            f"+ {SAMPLING} min sampling tolerance")
    finally:
        nightly_curves_cache.clear()
