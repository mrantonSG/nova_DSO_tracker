from datetime import datetime, timedelta

import pytz

from modules.astro_calculations import (get_utc_time_for_local_11pm_on,
                                        interpolate_horizon, ra_dec_to_alt_az)
from nova import get_db
from nova.config import nightly_curves_cache
from nova.models import HorizonPoint, Location

LOC_NAME = "Default Test Loc"          # created by the conftest client fixture (lat 50, lon 10, UTC)
LAT, LON = 50, 10
OBJ_RA_H, OBJ_DEC = 5.58, -5.4          # M42, created by the conftest client fixture
ALT_THRESHOLD = 20.0
# Location tz is UTC, so local 11 PM == 23:00 UTC
FIXED_11PM_UTC = "2026-01-15T23:00:00"


def test_batch_11pm_obstruction_ignores_one_point_mask(client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.api.get_utc_time_for_local_11pm_on",
                        lambda local_date, tz_name: FIXED_11PM_UTC)

    alt_11pm, az_11pm = ra_dec_to_alt_az(OBJ_RA_H, OBJ_DEC, LAT, LON, FIXED_11PM_UTC)
    horizon_mask = [[float(az_11pm), 80.0]]

    # Guard: the scenario must discriminate. The old `if horizon_mask:` guard interpolates the
    # single point (required=80 at exactly az_11pm) -> obstructed; W4's len>1 guard skips it.
    old_required = interpolate_horizon(az_11pm, sorted(horizon_mask, key=lambda p: p[0]), ALT_THRESHOLD)
    old_rule = bool(ALT_THRESHOLD <= alt_11pm < old_required)
    expected = False  # W4: horizon_mask and len(horizon_mask) > 1 -> not evaluated
    assert old_rule is True and old_rule != expected, (
        f"scenario not discriminating: alt_11pm={alt_11pm}, az_11pm={az_11pm}, old_required={old_required}")

    db = get_db()
    loc = db.query(Location).filter_by(name=LOC_NAME).one()
    loc.altitude_threshold = ALT_THRESHOLD
    db.add(HorizonPoint(location_id=loc.id, az_deg=float(az_11pm), alt_min_deg=80.0))
    db.commit()
    nightly_curves_cache.clear()  # force the cache-miss path

    response = client.get('/api/get_desktop_data_batch', query_string={"location": LOC_NAME})
    assert response.status_code == 200
    item = next(r for r in response.get_json()["results"] if r.get("Object") == "M42")

    assert item["error"] is False
    assert item["Azimuth 11PM"] == f"{az_11pm:.2f}", "patched 11 PM time not used"
    assert item["is_obstructed_at_11pm"] is expected, (
        f"is_obstructed_at_11pm={item['is_obstructed_at_11pm']!r}, expected={expected!r} "
        f"(W4 rule; old rule gave {old_rule}, alt_11pm={alt_11pm:.2f}, required={old_required})")


# Real "today" at 20:00 UTC: after noon, so the batch's observing night is sim_date itself
_TODAY_UTC = datetime.now(pytz.utc).date()
SIM_DATE = (_TODAY_UTC + timedelta(days=182)).strftime('%Y-%m-%d')


class _AfterNoonDatetime(datetime):
    """now() lands on today 20:00 UTC, so the sim_date observing night doesn't roll back a day."""

    @classmethod
    def now(cls, tz=None):
        fixed = pytz.utc.localize(datetime.combine(_TODAY_UTC, datetime.min.time()).replace(hour=20))
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


def test_batch_11pm_follows_sim_date(client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.api.datetime", _AfterNoonDatetime)

    # Location tz is UTC, so 23:00 local on SIM_DATE == SIM_DATE 23:00 UTC
    expected_alt, _ = ra_dec_to_alt_az(OBJ_RA_H, OBJ_DEC, LAT, LON, get_utc_time_for_local_11pm_on(SIM_DATE, "UTC"))
    # Reproduces the removed clock-based 11 PM for the guard: next 23:00 UTC from the real clock
    now_utc = datetime.now(pytz.utc)
    clock_11pm = now_utc.replace(hour=23, minute=0, second=0, microsecond=0)
    if now_utc >= clock_11pm:
        clock_11pm += timedelta(days=1)
    clock_alt, _ = ra_dec_to_alt_az(OBJ_RA_H, OBJ_DEC, LAT, LON, clock_11pm.strftime('%Y-%m-%dT%H:%M:%S'))
    # Guard: the clock-based 11 PM (old behaviour) must give a different value, so the test discriminates
    assert f"{expected_alt:.2f}" != f"{clock_alt:.2f}", (
        f"scenario not discriminating: sim={expected_alt:.2f}, clock={clock_alt:.2f}")

    nightly_curves_cache.clear()  # force the cache-miss path

    response = client.get('/api/get_desktop_data_batch',
                          query_string={"location": LOC_NAME, "sim_date": SIM_DATE})
    assert response.status_code == 200
    item = next(r for r in response.get_json()["results"] if r.get("Object") == "M42")

    assert item["error"] is False
    assert item["Altitude 11PM"] == f"{expected_alt:.2f}", (
        f"Altitude 11PM={item['Altitude 11PM']!r}, expected {expected_alt:.2f} (23:00 on {SIM_DATE}); "
        f"clock-based 11 PM gives {clock_alt:.2f}")
