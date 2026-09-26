"""Incremental Outlook updates on pin/unpin, notes edits and object edits.

The per-object calculation (compute_outlook_object_opportunities) is replaced
by a recording stub, so every test counts exactly which objects were
calculated. Threads are run inline; cache files live in the per-test
isolated cache dir from conftest.py.
"""

import json
import os
import time
import types

import pytest

import nova
import nova.blueprints.core as core
from nova import AstroObject, DbUser, Location
from nova.config import astro_context_cache
from nova.helpers import outlook_cache_file

LOCATION = "Default Test Loc"  # created by su_client_logged_in (lat 50, lon 10, UTC)
SECOND = "Second Loc"


class _RecordingThread:
    """Records the worker call instead of running it."""
    calls = []

    def __init__(self, target=None, args=(), kwargs=None, **extra):
        self.target = target
        self.args = args

    def start(self):
        _RecordingThread.calls.append((self.target, self.args))


@pytest.fixture
def env(su_client_logged_in, db_session, monkeypatch):
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.add(Location(user_id=user.id, name=SECOND, lat=40, lon=-3, timezone="UTC", active=True))
    db_session.add(AstroObject(user_id=user.id, object_name="Zen", common_name="Zenith",
                               ra_hours=6.0, dec_deg=50.0, active_project=True, project_name="old notes"))
    db_session.commit()
    astro_context_cache.clear()

    calculated = []

    def fake_object_opportunities(obj_entry, objects_map, framed_objects, inputs, local_tz,
                                  dates_to_check, sampling_interval, status_key):
        name = obj_entry["Object"]
        calculated.append((name, inputs["lat"]))
        return [{
            "object_name": name, "common_name": name, "has_framing": name in framed_objects,
            "date": dates_to_check[i].isoformat(), "score": 90.0, "rating": "★★★★★", "rating_num": 5,
            "max_alt": 70.0, "obs_dur": 300, "moon_illumination": 1.0,
            "project": obj_entry.get("Project", "none"), "type": "N/A", "constellation": "N/A",
            "magnitude": "N/A", "size": "N/A", "sb": "N/A",
        } for i in (0, 3)]

    status = {}
    monkeypatch.setattr(nova, "compute_outlook_object_opportunities", fake_object_opportunities)
    monkeypatch.setattr(nova, "cache_worker_status", status)
    monkeypatch.setattr(core, "cache_worker_status", status)
    # Trigger threads run inline
    monkeypatch.setattr(nova, "threading", types.SimpleNamespace(
        Thread=lambda target=None, args=(), **k: types.SimpleNamespace(start=lambda: target(*args))))
    _RecordingThread.calls = []
    monkeypatch.setattr(core, "threading", types.SimpleNamespace(Thread=_RecordingThread))
    return types.SimpleNamespace(user=user, session=db_session, client=su_client_logged_in,
                                 calculated=calculated)


def _set_object(env, name, **fields):
    obj = env.session.query(AstroObject).filter_by(user_id=env.user.id, object_name=name).one()
    for key, value in fields.items():
        setattr(obj, key, value)
    env.session.commit()
    astro_context_cache.clear()


def _trigger(env):
    with nova.app.test_request_context():
        nova.g.db_user = env.user
        nova.trigger_outlook_update_for_user("default")


def _filename(env, location=LOCATION):
    with nova.app.app_context():
        return outlook_cache_file(env.user.id, location, nova.build_user_config_from_db("default"))


def _read(env, location=LOCATION):
    with open(_filename(env, location)) as f:
        return json.load(f)


def _names(data):
    return {o["object_name"] for o in data["opportunities"]}


def _full_build(env):
    """Trigger with no files: a full update per location, then a clean record."""
    _trigger(env)
    assert env.calculated, "full build did not calculate anything"
    env.calculated.clear()


# ---------------------------------------------------------------------------
# Filename
# ---------------------------------------------------------------------------

def test_filename_ignores_pins_and_notes(env):
    before = _filename(env)
    _set_object(env, "M42", active_project=True)
    assert _filename(env) == before
    _set_object(env, "Zen", active_project=False)
    assert _filename(env) == before
    _set_object(env, "M42", project_name="new notes")
    assert _filename(env) == before


# ---------------------------------------------------------------------------
# Trigger (pin / unpin)
# ---------------------------------------------------------------------------

def test_pin_calculates_only_the_new_object_per_location(env):
    _full_build(env)
    _set_object(env, "M42", active_project=True)

    _trigger(env)

    # Once per active location, and only M42
    assert sorted(env.calculated) == [("M42", 40), ("M42", 50)]
    for loc in (LOCATION, SECOND):
        data = _read(env, loc)
        assert _names(data) == {"Zen", "M42"}
        assert set(data["metadata"]["objects"]) == {"Zen", "M42"}
        dates = [o["date"] for o in data["opportunities"]]
        assert dates == sorted(dates)


def test_unpin_drops_entries_without_calculating(env):
    _set_object(env, "M42", active_project=True)
    _full_build(env)
    assert _names(_read(env)) == {"Zen", "M42"}

    _set_object(env, "M42", active_project=False)
    _trigger(env)

    assert env.calculated == []
    for loc in (LOCATION, SECOND):
        data = _read(env, loc)
        assert _names(data) == {"Zen"}
        assert set(data["metadata"]["objects"]) == {"Zen"}


def test_moved_object_is_recalculated(env):
    _full_build(env)
    _set_object(env, "Zen", ra_hours=7.0)

    _trigger(env)

    assert sorted(env.calculated) == [("Zen", 40), ("Zen", 50)]
    assert _read(env)["metadata"]["objects"]["Zen"] == [7.0, 50.0]
    assert len(_read(env)["opportunities"]) == 2  # old entries replaced, not duplicated


def test_sync_plan_matches_names_case_insensitively():
    data = {"metadata": {"objects": {"m42": [5.58, -5.4], "Zen": [6.0, 50.0]}}}
    current, drop, calculate = nova.outlook_sync_plan(
        data, [{"Object": "M42", "RA": 5.58, "DEC": -5.4}])
    assert drop == {"zen"}
    assert calculate == []
    assert current == {"M42": [5.58, -5.4]}


# ---------------------------------------------------------------------------
# /get_outlook_data
# ---------------------------------------------------------------------------

def test_notes_edit_refreshes_project_without_calculating(env):
    _full_build(env)

    resp = env.client.post("/update_project", json={"object": "Zen", "project": "new notes"})
    assert resp.get_json()["status"] == "success"

    resp = env.client.get(f"/get_outlook_data?location={LOCATION}")
    data = resp.get_json()
    assert data["status"] == "complete"
    assert data["results"] and {o["project"] for o in data["results"]} == {"new notes"}
    assert env.calculated == []
    assert _RecordingThread.calls == []
    # Written back to the file as well
    assert {o["project"] for o in _read(env)["opportunities"]} == {"new notes"}


def _update_object(env, name, is_active):
    obj = env.session.query(AstroObject).filter_by(user_id=env.user.id, object_name=name).one()
    resp = env.client.post("/api/update_object", json={
        "object_id": name, "name": obj.common_name, "ra": obj.ra_hours, "dec": obj.dec_deg,
        "constellation": obj.constellation, "type": obj.type, "magnitude": obj.magnitude,
        "size": obj.size, "sb": obj.sb, "is_active": is_active, "project_notes": obj.project_name,
    })
    assert resp.get_json()["status"] == "success"


def test_update_object_changes_reach_outlook(env):
    _set_object(env, "M42", active_project=True)
    _full_build(env)

    # Unpin through /api/update_object: served inline, no calculation
    _update_object(env, "M42", False)
    data = env.client.get(f"/get_outlook_data?location={LOCATION}").get_json()
    assert data["status"] == "complete"
    assert {o["object_name"] for o in data["results"]} == {"Zen"}
    assert env.calculated == []

    # Pin through /api/update_object: a background sync, like the missing-file path
    _update_object(env, "M42", True)
    data = env.client.get(f"/get_outlook_data?location={LOCATION}").get_json()
    assert data["status"] == "starting"
    assert len(_RecordingThread.calls) == 1
    target, args = _RecordingThread.calls[0]
    assert target is nova.sync_outlook_cache
    target(*args)
    assert env.calculated == [("M42", 50)]

    data = env.client.get(f"/get_outlook_data?location={LOCATION}").get_json()
    assert data["status"] == "complete"
    assert {o["object_name"] for o in data["results"]} == {"Zen", "M42"}


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def test_incremental_update_keeps_file_age(env):
    _full_build(env)
    path = _filename(env)
    old_mtime = time.time() - 20 * 3600
    os.utime(path, (old_mtime, old_mtime))

    _set_object(env, "M42", active_project=True)
    _trigger(env)

    assert ("M42", 50) in env.calculated
    assert "M42" in _names(_read(env))
    assert abs(os.path.getmtime(path) - old_mtime) < 1


def test_file_older_than_a_day_gets_full_update(env):
    _full_build(env)
    path = _filename(env)
    old_mtime = time.time() - 25 * 3600
    os.utime(path, (old_mtime, old_mtime))

    _set_object(env, "M42", active_project=True)
    _trigger(env)

    # Stale file at LOCATION: every active object recalculated there
    assert sorted(n for n, lat in env.calculated if lat == 50) == ["M42", "Zen"]
    assert os.path.getmtime(path) > time.time() - 60


def test_inline_sync_keeps_the_request_session(env, monkeypatch):
    """The inline sync in /get_outlook_data must not tear down the request's DB session."""
    from sqlalchemy import inspect
    from sqlalchemy.orm import scoped_session

    _set_object(env, "M42", active_project=True)
    _full_build(env)
    _set_object(env, "M42", active_project=False)  # removal only: inline path

    # db_session makes remove() a no-op; restore a real one and record calls
    removed = []

    def real_remove():
        removed.append(True)
        scoped_session.remove(nova.SessionLocal)

    monkeypatch.setattr(nova.SessionLocal, "remove", real_remove)

    # Inspect g.db_user in the same request, right after the view returns
    seen = {}
    view = nova.app.view_functions["core.get_outlook_data"]

    def checked_view(*args, **kwargs):
        # The test client tears down the previous request's context before this one
        removed.clear()
        result = view(*args, **kwargs)
        seen["removed"] = list(removed)
        seen["detached"] = inspect(nova.g.db_user).detached
        seen["username"] = nova.g.db_user.username
        return result

    monkeypatch.setitem(nova.app.view_functions, "core.get_outlook_data", checked_view)

    data = env.client.get(f"/get_outlook_data?location={LOCATION}").get_json()
    assert data["status"] == "complete"
    assert {o["object_name"] for o in data["results"]} == {"Zen"}
    assert seen == {"removed": [], "detached": False, "username": "default"}
