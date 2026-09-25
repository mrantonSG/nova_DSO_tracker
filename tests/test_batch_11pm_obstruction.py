from modules.astro_calculations import interpolate_horizon, ra_dec_to_alt_az
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
    monkeypatch.setattr("nova.blueprints.api.get_utc_time_for_local_11pm", lambda tz_name: FIXED_11PM_UTC)

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
