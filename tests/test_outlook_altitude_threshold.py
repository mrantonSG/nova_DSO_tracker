"""Outlook altitude threshold when the location overrides the global one.

The Outlook's filename (outlook_cache_file) and its contents
(update_outlook_cache) must both use the location's override:
  (a) configs differing only in the location's threshold give different files
  (c) the reader and the writer compute the same filename for such a location
No thread is started: worker targets are captured instead of run.
"""

import json
import types

import pytest

import nova
import nova.blueprints.core as core
from nova import DbUser, Location, UiPref
from nova.config import astro_context_cache
from nova.helpers import outlook_cache_file

LOCATION = "Default Test Loc"  # created by su_client_logged_in (lat 50, lon 10, UTC)
SIM_DATE = "2027-01-10"


class _RecordingThread:
    """Records the worker call instead of running it."""
    calls = []

    def __init__(self, target=None, args=(), kwargs=None, **extra):
        self.target = target
        self.args = args

    def start(self):
        _RecordingThread.calls.append((self.target, self.args))


@pytest.fixture
def override_user(su_client_logged_in, db_session, monkeypatch):
    user = db_session.query(DbUser).filter_by(username="default").one()
    location = db_session.query(Location).filter_by(user_id=user.id, name=LOCATION).one()
    location.altitude_threshold = 35
    prefs = {"altitude_threshold": 20, "default_location": LOCATION}
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    db_session.commit()
    astro_context_cache.clear()

    _RecordingThread.calls = []
    monkeypatch.setattr(core, "cache_worker_status", {})
    monkeypatch.setattr(core, "threading", types.SimpleNamespace(Thread=_RecordingThread))
    return types.SimpleNamespace(user=user, location=location, client=su_client_logged_in)


def test_filename_differs_by_location_threshold(override_user):
    env = override_user

    def cfg(loc_threshold):
        return {"altitude_threshold": 20, "locations": {LOCATION: {
            "db_id": env.location.id, "lat": 50, "lon": 10, "timezone": "UTC",
            "horizon_mask": [], "altitude_threshold": loc_threshold,
        }}}

    with nova.app.app_context():
        names = {outlook_cache_file(env.user.id, LOCATION, cfg(t), SIM_DATE) for t in (None, 35, 40)}
    assert len(names) == 3


def test_reader_and_writer_filenames_match_with_override(override_user, monkeypatch):
    env = override_user

    resp = env.client.get(f"/get_outlook_data?location={LOCATION}")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "starting"
    assert len(_RecordingThread.calls) == 1
    reader_filename = _RecordingThread.calls[0][1][2]

    # trigger_outlook_update_for_user (project toggles) builds its config from the DB
    written = []
    monkeypatch.setattr(nova, "update_outlook_cache",
                        lambda uid, key, fname, loc, cfg, interval, sim: written.append(fname))
    monkeypatch.setattr(nova, "threading", types.SimpleNamespace(
        Thread=lambda target=None, args=(), **k: types.SimpleNamespace(start=lambda: target(*args))))
    with nova.app.test_request_context():
        nova.g.db_user = env.user
        nova.trigger_outlook_update_for_user("default")
    assert written == [reader_filename]
