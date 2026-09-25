"""A null global altitude_threshold (e.g. a YAML import without the key) with no
location override must resolve to 20 on every per-location site, not None.

Deliberately does not import resolve_altitude_threshold, so these tests run
(and fail) against the pre-helper code.
"""

import json
import time
import types

import nova
from nova import get_db
from nova.config import nightly_curves_cache
from nova.models import DbUser, Location, UiPref

LOC_NAME = "Default Test Loc"  # created by the conftest client fixture (lat 50, lon 10, UTC), no override


def _set_null_global(username="default"):
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one()
    prefs = db.query(UiPref).filter_by(user_id=user.id).first()
    blob = json.loads(prefs.json_blob) if prefs and prefs.json_blob else {}
    blob["altitude_threshold"] = None
    if prefs:
        prefs.json_blob = json.dumps(blob)
    else:
        db.add(UiPref(user_id=user.id, json_blob=json.dumps(blob)))
    loc = db.query(Location).filter_by(user_id=user.id, name=LOC_NAME).one()
    assert loc.altitude_threshold is None  # no override, so the global path is exercised
    db.commit()


def _m42_keys():
    return [k for k in list(nightly_curves_cache.keys()) if k.startswith("default_m42_")]


def test_batch_null_global_resolves_to_20(client):
    _set_null_global()
    nightly_curves_cache.clear()  # force the cache-miss path

    response = client.get('/api/get_desktop_data_batch', query_string={"location": LOC_NAME})
    assert response.status_code == 200
    item = next(r for r in response.get_json()["results"] if r.get("Object") == "M42")
    assert item["error"] is False, item

    keys = _m42_keys()
    assert len(keys) == 1 and "_20_" in keys[0], keys


def test_object_data_null_global_resolves_to_20(client):
    _set_null_global()
    nightly_curves_cache.clear()

    response = client.get('/api/get_object_data/M42', query_string={"location": LOC_NAME})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["error"] is False

    keys = _m42_keys()
    assert len(keys) == 1 and "_20_" in keys[0], keys


def test_warm_signature_null_global_resolves_to_20(db_session, monkeypatch):
    config = {
        "altitude_threshold": None,
        "default_location": "Home",
        "locations": {"Home": {"lat": 50.0, "lon": 10.0, "timezone": "UTC",
                               "active": True, "altitude_threshold": None}},
        "objects": [{"Object": "M1", "enabled": True}],
    }
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    monkeypatch.setattr(nova, "warm_main_cache", lambda *a, **k: None)
    monkeypatch.setattr(nova, "build_user_config_from_db", lambda username: config)
    monkeypatch.setattr(nova, "time", types.SimpleNamespace(time=time.time, sleep=lambda s: None))
    monkeypatch.setattr(nova, "_last_warmed", {})
    monkeypatch.setattr(nova, "_recent_visitors", {})

    nova.warm_default_locations()

    # signature = (location_name, local_date, sampling_interval, altitude_threshold, lat, lon, enabled_count)
    assert nova._last_warmed["default"][3] == 20, nova._last_warmed
