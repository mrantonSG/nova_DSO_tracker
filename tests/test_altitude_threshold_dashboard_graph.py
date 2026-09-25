"""Dashboard highlight and graph page use the location's altitude threshold.

Location override 35, global 20:
  (a) the index page exposes 35 as NOVA_INDEX.altitudeThreshold
  (b) get_imaging_opportunities passes 35 to the duration calculation
  (c) get_observable_objects via the lat/lon/tz fallback passes 35
"""

import json
import re
import types
from datetime import timedelta

import pytest

import modules.astro_calculations as astro_calculations
import nova.blueprints.core as core
from nova import AstroObject, DbUser, Location, UiPref
from nova.config import astro_context_cache, observable_objects_cache

LOCATION = "Default Test Loc"  # created by su_client_logged_in (lat 50, lon 10, UTC)
LAT_LON_TZ = "lat=50&lon=10&tz=UTC"


def _not_observable(*a, **k):
    """Fails the observable-minutes check, so nothing after it runs."""
    return timedelta(0), 0.0, None, None


@pytest.fixture
def override_user(su_client_logged_in, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    location = db_session.query(Location).filter_by(user_id=user.id, name=LOCATION).one()
    location.altitude_threshold = 35
    prefs = {
        "altitude_threshold": 20,
        "default_location": LOCATION,
        "imaging_criteria": {"search_horizon_months": 1},
    }
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    db_session.add(AstroObject(user_id=user.id, object_name="Zenith Test", ra_hours=6.0,
                               dec_deg=50.0, enabled=True, active_project=True))
    db_session.commit()
    astro_context_cache.clear()
    observable_objects_cache.clear()
    return types.SimpleNamespace(user=user, location=location, client=su_client_logged_in)


def test_index_exposes_location_threshold(override_user):
    resp = override_user.client.get("/")
    assert resp.status_code == 200
    match = re.search(r"altitudeThreshold:\s*([^,\s]+),", resp.get_data(as_text=True))
    assert match, "NOVA_INDEX.altitudeThreshold not found in the index page"
    assert float(match.group(1)) == 35


def test_imaging_opportunities_passes_location_threshold(override_user, monkeypatch):
    thresholds = []

    def recording(ra, dec, lat, lon, date_str, tz_name, altitude_threshold, sampling_interval,
                  horizon_mask=None):
        thresholds.append(altitude_threshold)
        return _not_observable()

    monkeypatch.setattr(core, "calculate_observable_duration_vectorized", recording)
    monkeypatch.setattr(core, "calculate_sun_events_cached", lambda *a, **k: {})

    resp = override_user.client.get(
        "/get_imaging_opportunities/Zenith Test?plot_lat=50&plot_lon=10&plot_tz=UTC")
    assert resp.status_code == 200
    assert thresholds, "calculate_observable_duration_vectorized was never called"
    assert set(thresholds) == {35}


def test_observable_objects_fallback_passes_location_threshold(override_user, monkeypatch):
    thresholds = []

    def recording(**kwargs):
        thresholds.append(kwargs["altitude_threshold"])
        return timedelta(hours=2), 50.0, None, None

    # The route imports it from modules.astro_calculations inside the function
    monkeypatch.setattr(astro_calculations, "calculate_observable_duration_vectorized", recording)

    resp = override_user.client.get(
        f"/api/get_observable_objects?{LAT_LON_TZ}&day=10&month=1&year=2027")
    assert resp.status_code == 200
    assert thresholds, "calculate_observable_duration_vectorized was never called"
    assert set(thresholds) == {35}
