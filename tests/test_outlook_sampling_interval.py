"""Outlook sampling interval when the stored setting is null.

An imported config without sampling_interval_minutes is saved to UiPref as
null. The Outlook sites must treat that as missing (15 min), like
load_effective_settings does:
  (a) the reader's filename matches the one the other Outlook writers use
  (b) the worker the reader starts produces opportunities, not an empty list
No thread is started: the reader's worker target is captured and run inline.
"""

import json
import types

import ephem
import pytest

import nova
import nova.blueprints.core as core
from nova import AstroObject, DbUser, Location, UiPref
from nova.config import astro_context_cache
from nova.helpers import outlook_cache_file

LOCATION = "Default Test Loc"  # created by su_client_logged_in (lat 50, lon 10, UTC)
SIM_DATE = "2027-01-10"        # long winter nights; the target is up all night


class _RecordingThread:
    """Records the worker call instead of running it."""
    calls = []

    def __init__(self, target=None, args=(), kwargs=None, **extra):
        self.target = target
        self.args = args

    def start(self):
        _RecordingThread.calls.append((self.target, self.args))


def _criteria():
    return {
        "min_observable_minutes": 60,
        "min_max_altitude": 30,
        "max_moon_illumination": 20,
        "min_angular_separation": 0,
        "search_horizon_months": 1,
    }


@pytest.fixture
def null_interval_user(su_client_logged_in, db_session, monkeypatch):
    user = db_session.query(DbUser).filter_by(username="default").one()
    prefs = {
        "altitude_threshold": 20,
        "default_location": LOCATION,
        "imaging_criteria": _criteria(),
        "sampling_interval_minutes": None,
    }
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    # Near the zenith at lat 50 and circumpolar there: observable all night
    db_session.add(AstroObject(user_id=user.id, object_name="Zenith Test", common_name="Zenith Test",
                               ra_hours=6.0, dec_deg=50.0, active_project=True, project_name="p"))
    db_session.commit()
    astro_context_cache.clear()

    _RecordingThread.calls = []
    monkeypatch.setattr(core, "cache_worker_status", {})
    monkeypatch.setattr(core, "threading", types.SimpleNamespace(Thread=_RecordingThread))
    return types.SimpleNamespace(user=user, prefs=prefs, client=su_client_logged_in, session=db_session)


def _reader_call(client, query=""):
    _RecordingThread.calls = []
    resp = client.get(f"/get_outlook_data?location={LOCATION}{query}")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "starting"
    assert len(_RecordingThread.calls) == 1
    return _RecordingThread.calls[0]


def test_reader_filename_matches_other_writers(null_interval_user, monkeypatch):
    env = null_interval_user
    _, args = _reader_call(env.client)
    reader_filename, reader_interval = args[2], args[5]

    # import_config writes from the imported YAML, where the key is simply absent
    import_cfg = {k: v for k, v in env.prefs.items() if k != "sampling_interval_minutes"}
    import_cfg["locations"] = {LOCATION: {
        "db_id": env.session.query(Location).filter_by(user_id=env.user.id, name=LOCATION).one().id,
        "lat": 50, "lon": 10, "timezone": "UTC", "horizon_mask": [],
    }}
    with nova.app.app_context():
        assert outlook_cache_file(env.user.id, LOCATION, import_cfg) == reader_filename

    # trigger_outlook_update_for_user (project toggles) builds its config from the DB
    written = []
    monkeypatch.setattr(nova, "update_outlook_cache",
                        lambda uid, key, fname, loc, cfg, interval, sim: written.append((fname, interval)))
    monkeypatch.setattr(nova, "threading", types.SimpleNamespace(
        Thread=lambda target=None, args=(), **k: types.SimpleNamespace(start=lambda: target(*args))))
    with nova.app.test_request_context():
        nova.g.db_user = env.user
        nova.trigger_outlook_update_for_user("default")
    assert (reader_filename, 15) in written
    assert reader_interval == 15


def test_reader_worker_finds_opportunities(null_interval_user, monkeypatch):
    env = null_interval_user
    target, args = _reader_call(env.client, f"&sim_date={SIM_DATE}")

    # New moon, so the score depends only on altitude and duration
    monkeypatch.setattr(ephem, "Moon", lambda *a, **k: types.SimpleNamespace(phase=0.0))

    target(*args)

    with open(args[2]) as f:
        opportunities = json.load(f)["opportunities"]
    assert opportunities, "Outlook worker produced no opportunities with a null sampling interval"
    assert {o["object_name"] for o in opportunities} == {"Zenith Test"}
