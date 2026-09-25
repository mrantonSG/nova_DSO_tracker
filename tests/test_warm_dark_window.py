from datetime import datetime, timedelta

import pytz

import nova
from nova import warm_main_cache, nightly_curves_cache
from modules.astro_calculations import (calculate_observable_duration_vectorized, calculate_sun_events_cached,
                                        interpolate_horizon, ra_dec_to_alt_az)

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


# Dec -60 at 47.8N culminates at 90 - |47.8 - (-60)| = -17.8 deg -> never reaches ALT_THRESHOLD
NEVER_RISES_NAME, NEVER_RISES_RA_H, NEVER_RISES_DEC = "Never Rises", 12.0, -60.0


def _cache_key(obj_name):
    return (f"{USERNAME}_{obj_name.lower().replace(' ', '_')}_{LOCAL_DATE}"
            f"_{LAT:.4f}_{LON:.4f}_{ALT_THRESHOLD}_{SAMPLING}")


def test_warm_main_cache_skips_geometrically_impossible(monkeypatch):
    monkeypatch.setattr(nova, "datetime", _FixedDatetime)
    monkeypatch.setattr("threading.Thread", _no_threads)
    nightly_curves_cache.clear()

    user_config = {
        "locations": {LOC_NAME: {"lat": LAT, "lon": LON, "timezone": TZ,
                                 "altitude_threshold": ALT_THRESHOLD}},
        "objects": [
            {"Object": NEVER_RISES_NAME, "RA": NEVER_RISES_RA_H, "DEC": NEVER_RISES_DEC, "enabled": True},
            {"Object": OBJ_NAME, "RA": OBJ_RA_H, "DEC": OBJ_DEC, "enabled": True},
        ],
    }

    try:
        warm_main_cache(USERNAME, LOC_NAME, user_config, SAMPLING, trigger_outlook=False)

        never_key = _cache_key(NEVER_RISES_NAME)
        assert never_key not in nightly_curves_cache, (
            f"never-rising object must not be cached (matches get_desktop_data_batch), "
            f"got: {nightly_curves_cache.get(never_key)!r}")
        assert _cache_key(OBJ_NAME) in nightly_curves_cache, "NGC 188 was not cached"
    finally:
        nightly_curves_cache.clear()


# 2026-09-20 23:00 Europe/Vienna (CEST, UTC+2) -> 21:00 UTC, the night of LOCAL_DATE
FIXED_11PM_UTC = "2026-09-20T21:00:00"


def test_warm_main_cache_11pm_obstruction_matches_interpolate_horizon(monkeypatch):
    monkeypatch.setattr(nova, "datetime", _FixedDatetime)
    monkeypatch.setattr(nova, "get_utc_time_for_local_11pm", lambda tz_name: FIXED_11PM_UTC)
    monkeypatch.setattr("threading.Thread", _no_threads)
    nightly_curves_cache.clear()

    alt_11pm, az_11pm = ra_dec_to_alt_az(OBJ_RA_H, OBJ_DEC, LAT, LON, FIXED_11PM_UTC)
    # Duplicate azimuth exactly at az_11pm. A key=p[0] sort is stable, so the order is kept:
    # interpolate_horizon takes the first point (80), np.interp took the last (20).
    horizon_mask = [[az_11pm, 80.0], [az_11pm, 20.0]]

    required = interpolate_horizon(az_11pm, sorted(horizon_mask, key=lambda p: p[0]), ALT_THRESHOLD)
    expected = bool(ALT_THRESHOLD <= alt_11pm < required)
    # Guard: the scenario must discriminate (old np.interp profile gives required=20 -> False)
    assert expected is True, f"scenario not discriminating: alt_11pm={alt_11pm}, required={required}"

    user_config = {
        "locations": {LOC_NAME: {"lat": LAT, "lon": LON, "timezone": TZ,
                                 "altitude_threshold": ALT_THRESHOLD,
                                 "horizon_mask": horizon_mask}},
        "objects": [{"Object": OBJ_NAME, "RA": OBJ_RA_H, "DEC": OBJ_DEC, "enabled": True}],
    }

    try:
        warm_main_cache(USERNAME, LOC_NAME, user_config, SAMPLING, trigger_outlook=False)

        cached = nightly_curves_cache[_cache_key(OBJ_NAME)]
        assert cached["is_obstructed_at_11pm"] == expected, (
            f"is_obstructed_at_11pm: cached={cached['is_obstructed_at_11pm']!r} "
            f"expected={expected!r} (interpolate_horizon, required={required}, alt_11pm={alt_11pm:.2f})")
    finally:
        nightly_curves_cache.clear()
