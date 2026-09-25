"""Heatmap altitude threshold when the location overrides the global one.

The route (get_yearly_heatmap_chunk) and the worker (heatmap_background_worker)
must both fingerprint and compute with the location's override:
  (a) the worker passes the override to the duration calculation
  (b) the route and the worker compute the same chunk filename
  (c) changing only the location's override changes the route's chunk filename
"""

import contextlib
import json
import time
import types
from datetime import timedelta

import pytest

import nova.blueprints.api as api
import nova.workers.heatmap as heatmap_worker
from nova import AstroObject, DbUser, Location, UiPref
from nova.config import astro_context_cache

LOCATION = "Default Test Loc"  # created by su_client_logged_in (lat 50, lon 10, UTC)
CHUNK_URL = f"/api/get_yearly_heatmap_chunk?location_name={LOCATION}&chunk_index=0"


class _StopWorker(BaseException):
    """BaseException so the worker's own `except Exception` can't swallow it."""


def _fake_duration(*a, **k):
    return timedelta(hours=2), 50.0, None, None


def _record_paths(monkeypatch, module):
    """Record every chunk path `module` builds."""
    paths = []
    real = module.heatmap_cache_path

    def recording(*a, **k):
        path = real(*a, **k)
        paths.append(path)
        return path

    monkeypatch.setattr(module, "heatmap_cache_path", recording)
    return paths


def _run_worker_once(db_session, monkeypatch, fake_duration=_fake_duration):
    """Run one worker cycle; stops at the 4h sleep (or the 60s restart path)."""
    def fake_sleep(secs):
        if secs in (60, 4 * 60 * 60):
            raise _StopWorker()

    monkeypatch.setattr(heatmap_worker, "get_db", lambda: db_session)
    monkeypatch.setattr(heatmap_worker, "calculate_observable_duration_vectorized", fake_duration)
    monkeypatch.setattr(heatmap_worker, "time", types.SimpleNamespace(sleep=fake_sleep, time=time.time))
    with pytest.raises(_StopWorker):
        heatmap_worker.heatmap_background_worker(types.SimpleNamespace(app_context=contextlib.nullcontext))


@pytest.fixture
def override_user(su_client_logged_in, db_session, isolated_cache_dir, monkeypatch):
    user = db_session.query(DbUser).filter_by(username="default").one()
    location = db_session.query(Location).filter_by(user_id=user.id, name=LOCATION).one()
    location.altitude_threshold = 35
    prefs = {"altitude_threshold": 20, "default_location": LOCATION}
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    # Near the zenith at lat 50: passes the geometric filter at any threshold used here
    db_session.add(AstroObject(user_id=user.id, object_name="Zenith Test", ra_hours=6.0,
                               dec_deg=50.0, enabled=True))
    db_session.commit()
    astro_context_cache.clear()

    monkeypatch.setattr(api, "calculate_observable_duration_vectorized", _fake_duration)
    return types.SimpleNamespace(user=user, location=location, client=su_client_logged_in,
                                 session=db_session)


def test_worker_passes_location_override_to_duration(override_user, monkeypatch):
    thresholds = []

    def recording_duration(ra, dec, lat, lon, date_str, tz, thr, step, horizon_mask=None):
        thresholds.append(thr)
        return _fake_duration()

    _run_worker_once(override_user.session, monkeypatch, recording_duration)

    assert thresholds, "calculate_observable_duration_vectorized was never called"
    assert set(thresholds) == {35}


def test_route_and_worker_share_chunk_filename(override_user, monkeypatch):
    worker_paths = _record_paths(monkeypatch, heatmap_worker)
    _run_worker_once(override_user.session, monkeypatch)
    assert len(worker_paths) == 12

    route_paths = _record_paths(monkeypatch, api)
    resp = override_user.client.get(CHUNK_URL)
    assert resp.status_code == 200
    assert len(route_paths) == 1
    assert route_paths[0] in worker_paths


def test_route_filename_changes_with_location_override(override_user, monkeypatch):
    env = override_user
    route_paths = _record_paths(monkeypatch, api)
    for threshold in (None, 35, 40):
        env.location.altitude_threshold = threshold
        env.session.commit()
        astro_context_cache.clear()
        resp = env.client.get(CHUNK_URL)
        assert resp.status_code == 200
    assert len(set(route_paths)) == 3
