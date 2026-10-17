"""Read-only inputs for the desktop dashboard and graph view.

/api/desktop_inputs must hand the browser everything it needs to compute with
static/js/nova_astro.js, and must never change what /api/mobile_up_now_inputs
returns. It takes an optional ?location= limited to the caller's own locations.
"""

import json

from nova import app, get_db
from nova.config import astro_context_cache
from nova.models import AstroObject, DbUser, Location, UiPref

LOC_NAME = "Default Test Loc"  # created by the conftest client fixture (lat 50, lon 10, UTC)

SG_SECTORS = [
    {"az_deg": 0.0, "az_label": "N", "min_alt_deg": 24.3,
     "sqm_at_horizon": 17.611, "sqm_at_min_alt": 19.226},
    {"az_deg": 180.0, "az_label": "S", "min_alt_deg": 38.4,
     "sqm_at_horizon": 17.17, "sqm_at_min_alt": 19.226},
]

MOBILE_OBJECT_KEYS = {
    "Object", "Common Name", "ActiveProject", "has_framing",
    "Type", "Constellation", "ra", "dec",
}


def _loc_id(name=LOC_NAME, username="default"):
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one()
    return db.query(Location).filter_by(user_id=user.id, name=name).one().id


def _set_prefs(blob, username="default"):
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one()
    prefs = db.query(UiPref).filter_by(user_id=user.id).first()
    merged = json.loads(prefs.json_blob) if prefs and prefs.json_blob else {}
    merged.update(blob)
    if prefs:
        prefs.json_blob = json.dumps(merged)
    else:
        db.add(UiPref(user_id=user.id, json_blob=json.dumps(merged)))
    db.commit()


def _write_skyglow(tmp_path, monkeypatch, loc_id, sectors=SG_SECTORS):
    sg_dir = tmp_path / "skyglow"
    sg_dir.mkdir(exist_ok=True)
    (sg_dir / f"{loc_id}.json").write_text(json.dumps({"skyglow_horizon": sectors}))
    monkeypatch.setattr(app, "instance_path", str(tmp_path))


# --- single-user happy path --------------------------------------------------------------

def test_desktop_inputs_returns_the_documented_shape(client):
    response = client.get('/api/desktop_inputs')

    assert response.status_code == 200
    data = response.get_json()
    assert set(data.keys()) == {"location", "objects", "settings", "skyglow"}

    assert set(data["location"].keys()) == {
        "name", "lat", "lon", "timezone",
        "altitude_threshold", "sampling_interval", "horizon_mask",
    }
    assert data["location"]["name"] == LOC_NAME
    assert isinstance(data["location"]["horizon_mask"], list)

    # All of the user's objects, each carrying the enabled flag.
    assert any(o["Object"] == "M42" for o in data["objects"])
    for item in data["objects"]:
        assert set(item.keys()) == MOBILE_OBJECT_KEYS | {"enabled"}
        # No calculation ran (raw inputs only).
        assert "Altitude Current" not in item

    assert set(data["settings"].keys()) == {
        "hide_invisible", "calc_invisible",
        "min_observable_minutes", "max_moon_illumination", "min_angular_separation",
        "skyglow_flags",
    }


def test_desktop_inputs_settings_reflect_ui_prefs(client, monkeypatch):
    monkeypatch.delenv("NASA_EARTHDATA_TOKEN", raising=False)
    _set_prefs({
        "hide_invisible": False,
        "calc_invisible": True,
        "imaging_criteria": {
            "min_observable_minutes": 90,
            "max_moon_illumination": 45,
            "min_angular_separation": 12,
        },
    })

    settings = client.get('/api/desktop_inputs').get_json()["settings"]

    assert settings == {
        "hide_invisible": False,
        "calc_invisible": True,
        "min_observable_minutes": 90,
        "max_moon_illumination": 45,
        "min_angular_separation": 12,
        "skyglow_flags": False,
    }


def test_desktop_inputs_without_a_location_returns_the_null_shape(client, db_session):
    """A user with no location at all: 200, null location, no objects, but the
    settings are still present (they are user-level, not location-level)."""
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.query(Location).filter_by(user_id=user.id).delete()
    db_session.commit()
    astro_context_cache.clear()

    response = client.get('/api/desktop_inputs')

    assert response.status_code == 200
    data = response.get_json()
    assert data["location"] is None
    assert data["objects"] == []
    assert data["skyglow"] is None
    assert set(data["settings"].keys()) == {
        "hide_invisible", "calc_invisible",
        "min_observable_minutes", "max_moon_illumination", "min_angular_separation",
        "skyglow_flags",
    }


def test_desktop_inputs_settings_defaults(client):
    # No UiPref row at all: the documented defaults, not None.
    settings = client.get('/api/desktop_inputs').get_json()["settings"]

    assert settings["hide_invisible"] is True
    assert settings["calc_invisible"] is False
    assert settings["min_observable_minutes"] == 60
    assert settings["max_moon_illumination"] == 20
    assert settings["min_angular_separation"] == 30


def test_desktop_inputs_marks_disabled_objects(client, db_session):
    """A disabled object is still returned, with enabled = false."""
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.add(AstroObject(user_id=user.id, object_name="NGC 7000",
                               common_name="North America Nebula",
                               ra_hours=20.98, dec_deg=44.5, enabled=False))
    db_session.commit()

    rows = {o["Object"]: o for o in client.get('/api/desktop_inputs').get_json()["objects"]}

    assert rows["M42"]["enabled"] is True
    assert rows["NGC 7000"]["enabled"] is False


# --- skyglow -----------------------------------------------------------------------------

def test_desktop_inputs_skyglow_is_null_without_a_file(client):
    assert client.get('/api/desktop_inputs').get_json()["skyglow"] is None


def test_desktop_inputs_skyglow_is_null_when_the_file_has_no_sectors(client, tmp_path, monkeypatch):
    _write_skyglow(tmp_path, monkeypatch, _loc_id(), sectors=[])
    assert client.get('/api/desktop_inputs').get_json()["skyglow"] is None


def test_desktop_inputs_skyglow_carries_only_the_floor_fields(client, tmp_path, monkeypatch):
    _write_skyglow(tmp_path, monkeypatch, _loc_id())

    skyglow = client.get('/api/desktop_inputs').get_json()["skyglow"]

    # The smallest exact form: the two fields _nearest_sg_alt() reads, one per
    # sector, so the browser gets the same floor the server computes.
    assert skyglow == [
        {"az_deg": 0.0, "min_alt_deg": 24.3},
        {"az_deg": 180.0, "min_alt_deg": 38.4},
    ]


# --- location parameter ------------------------------------------------------------------

def test_desktop_inputs_location_param_picks_that_location(client, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.add(Location(user_id=user.id, name="Second Site",
                            lat=10, lon=20, timezone="UTC"))
    db_session.commit()

    data = client.get('/api/desktop_inputs?location=Second Site').get_json()

    assert data["location"]["name"] == "Second Site"
    assert data["location"]["lat"] == 10


def test_desktop_inputs_unknown_location_is_404(client):
    response = client.get('/api/desktop_inputs?location=Nowhere')
    assert response.status_code == 404
    assert "location" not in response.get_json()


# --- multi-user isolation ----------------------------------------------------------------

def test_desktop_inputs_rejects_another_users_location(multi_user_client, db_session):
    client, ids = multi_user_client
    db_session.add(Location(user_id=ids["user_b_id"], name="B Secret Site",
                            lat=1, lon=2, timezone="UTC"))
    db_session.commit()

    # User A cannot reach User B's location by name: 404 and no location data.
    response = client.get('/api/desktop_inputs?location=B Secret Site')
    assert (response.status_code, response.get_json()) == (404, {"error": "Location not found"})

    # ...and with no name it falls back to A's own active location.
    data = client.get('/api/desktop_inputs').get_json()
    assert data["location"]["name"] == "UserA_Home"


def test_desktop_inputs_multi_user_defaults_are_per_user(multi_user_client, db_session):
    client, ids = multi_user_client
    db_session.add(UiPref(user_id=ids["user_b_id"], json_blob=json.dumps({
        "hide_invisible": False,
        "imaging_criteria": {"min_observable_minutes": 5},
    })))
    db_session.commit()

    settings = client.get('/api/desktop_inputs').get_json()["settings"]

    # A sees A's defaults, never B's row.
    assert settings["hide_invisible"] is True
    assert settings["min_observable_minutes"] == 60


# --- auth: logged out and guest, as the batch --------------------------------------------

def test_desktop_inputs_auth_matches_the_batch(mu_client_logged_out):
    """Logged out in multi-user mode: the same refusal as the batch."""
    inputs = mu_client_logged_out.get('/api/desktop_inputs')
    batch = mu_client_logged_out.get('/api/get_desktop_data_batch')

    assert inputs.status_code == 401
    assert (inputs.status_code, inputs.get_json()) == (batch.status_code, batch.get_json())


def test_desktop_inputs_serves_the_guest_user_like_the_batch(mu_client_logged_out, db_session):
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    db_session.add(Location(user_id=guest.id, name="Guest Site", lat=50, lon=10,
                            timezone="UTC", is_default=True))
    db_session.add(AstroObject(user_id=guest.id, object_name="Guest M31",
                               ra_hours=0.71, dec_deg=41.3))
    db_session.commit()
    with mu_client_logged_out.session_transaction() as sess:
        sess['guest_mode'] = True

    inputs = mu_client_logged_out.get('/api/desktop_inputs', query_string={"location": "Guest Site"})
    batch = mu_client_logged_out.get('/api/get_desktop_data_batch', query_string={"location": "Guest Site"})

    assert (inputs.status_code, batch.status_code) == (200, 200)
    data = inputs.get_json()
    assert data["location"]["name"] == "Guest Site"
    assert "Guest M31" in [o["Object"] for o in data["objects"]]


# --- the existing endpoint is untouched --------------------------------------------------

def test_mobile_up_now_inputs_keys_are_unchanged(client):
    """The shared helper must not leak new keys into the mobile payload."""
    data = client.get('/api/mobile_up_now_inputs').get_json()

    assert set(data.keys()) == {"location", "objects"}
    assert set(data["location"].keys()) == {
        "name", "lat", "lon", "timezone",
        "altitude_threshold", "sampling_interval", "horizon_mask",
    }
    for item in data["objects"]:
        assert set(item.keys()) == MOBILE_OBJECT_KEYS
