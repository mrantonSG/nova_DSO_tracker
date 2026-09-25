"""Behaviour tests for Outlook serving, the default-location warm-up loop and
the before_request hooks that feed it.

Nothing here touches the real instance/cache/: Outlook paths come from tmp_path
via a monkeypatched outlook_cache_file. No real threads are started and
time.sleep is never called for real.
"""

import json
import time
import types

import pytest

import nova
import nova.blueprints.core as core
from nova import app, DbUser
from nova.helpers import _HAS_FCNTL, try_acquire_file_lock, release_file_lock


LOCATION = "Default Test Loc"  # created by su_client_logged_in


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AlwaysStarting(dict):
    """cache_worker_status stand-in that reports every key as "starting"."""

    def get(self, key, default=None):
        return "starting"


class _ForbiddenThread:
    def __init__(self, *args, **kwargs):
        pytest.fail("threading.Thread must not be constructed when a fresh cache file exists")


def _warm_config(n_objects=1):
    """Small config with one active default location."""
    return {
        "default_location": "Home",
        "locations": {
            "Home": {"lat": 50.0, "lon": 10.0, "timezone": "UTC", "active": True},
        },
        "objects": [{"Object": f"M{i + 1}", "enabled": True} for i in range(n_objects)],
    }


@pytest.fixture
def warm_env(db_session, monkeypatch):
    """Isolates warm_default_locations: records warm_main_cache calls, stubs
    time.sleep, and gives _last_warmed / _recent_visitors fresh dicts.

    db_session already swaps nova.SessionLocal for a scoped_session whose
    remove() is a no-op, so the SessionLocal.remove() calls inside the loop
    do not tear down the test session.
    """
    calls = []
    configs = {}

    def fake_warm_main_cache(username, location_name, user_config, sampling_interval):
        calls.append(username)

    monkeypatch.setattr(nova, "warm_main_cache", fake_warm_main_cache)
    monkeypatch.setattr(nova, "build_user_config_from_db", lambda username: configs[username])
    # Replace only nova's reference to the time module, not time.sleep globally
    monkeypatch.setattr(nova, "time", types.SimpleNamespace(time=time.time, sleep=lambda s: None))
    monkeypatch.setattr(nova, "_last_warmed", {})
    monkeypatch.setattr(nova, "_recent_visitors", {})

    return types.SimpleNamespace(calls=calls, configs=configs, session=db_session)


# ---------------------------------------------------------------------------
# /get_outlook_data
# ---------------------------------------------------------------------------

def test_outlook_fresh_file_wins_over_worker_status(su_client_logged_in, monkeypatch, tmp_path):
    opportunity = {"object_name": "M42", "date": "2026-10-01", "score": 90, "has_framing": False}
    cache_file = tmp_path / "outlook.json"
    cache_file.write_text(json.dumps({"metadata": {}, "opportunities": [opportunity]}))

    monkeypatch.setattr(core, "outlook_cache_file", lambda *a, **k: str(cache_file))
    monkeypatch.setattr(core, "cache_worker_status", _AlwaysStarting())
    monkeypatch.setattr(core, "threading", types.SimpleNamespace(Thread=_ForbiddenThread))

    resp = su_client_logged_in.get(f"/get_outlook_data?location={LOCATION}")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "complete"
    assert data["results"] == [opportunity]


def test_outlook_status_is_starting_before_thread_start(su_client_logged_in, monkeypatch, tmp_path):
    status = {}
    seen_at_start = []

    class RecordingThread:
        def __init__(self, target=None, args=(), kwargs=None, **extra):
            self.target = target

        def start(self):
            # Snapshot the status dict at the moment start() is called; never run target
            seen_at_start.append(dict(status))

    monkeypatch.setattr(core, "outlook_cache_file", lambda *a, **k: str(tmp_path / "missing.json"))
    monkeypatch.setattr(core, "cache_worker_status", status)
    monkeypatch.setattr(core, "threading", types.SimpleNamespace(Thread=RecordingThread))

    resp = su_client_logged_in.get(f"/get_outlook_data?location={LOCATION}")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "starting"
    assert len(seen_at_start) == 1
    snapshot = seen_at_start[0]
    assert len(snapshot) == 1
    (status_key, value), = snapshot.items()
    assert status_key.endswith(f"_{LOCATION}")
    assert value == "starting"


# ---------------------------------------------------------------------------
# update_outlook_cache
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_FCNTL, reason="fcntl not available; file lock is a no-op")
def test_update_outlook_cache_skips_when_lock_held(monkeypatch, tmp_path):
    status = {}
    monkeypatch.setattr(nova, "cache_worker_status", status)
    cache_filename = str(tmp_path / "outlook_locked.json")
    status_key = "(1 | test)_Home"

    held = try_acquire_file_lock(cache_filename)
    assert held is not None
    try:
        result = nova.update_outlook_cache(
            1, status_key, cache_filename, "Home", _warm_config(), 15
        )
    finally:
        release_file_lock(held)

    assert result is None
    assert not (tmp_path / "outlook_locked.json").exists()
    assert status == {status_key: "idle"}


# ---------------------------------------------------------------------------
# warm_default_locations
# ---------------------------------------------------------------------------

def test_warm_single_user_skips_unchanged_config(warm_env, monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    warm_env.configs["default"] = _warm_config(n_objects=1)

    nova.warm_default_locations()
    assert warm_env.calls == ["default"]

    nova.warm_default_locations()
    assert warm_env.calls == ["default"]  # unchanged signature → no new warm

    warm_env.configs["default"]["objects"].append({"Object": "M99", "enabled": True})
    nova.warm_default_locations()
    assert warm_env.calls == ["default", "default"]


def test_warm_run_limit_stops_after_first_user(warm_env, monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", False)
    monkeypatch.setattr(nova.nightly_curves_cache, "_maxsize", 10)  # limit = 0.8 * 10 = 8

    for name in ("alice", "bob"):
        warm_env.session.add(DbUser(username=name, active=True))
        warm_env.configs[name] = _warm_config(n_objects=9)
        nova._recent_visitors[name] = time.time()
    warm_env.session.commit()

    nova.warm_default_locations()

    # User order comes from a set, so either one may go first — but only one
    assert len(warm_env.calls) == 1
    assert warm_env.calls[0] in ("alice", "bob")


def test_warm_multi_user_only_recent_visitors(warm_env, monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", False)

    for name in ("alice", "bob"):
        warm_env.session.add(DbUser(username=name, active=True))
        warm_env.configs[name] = _warm_config(n_objects=1)
    warm_env.session.commit()

    now = time.time()
    nova._recent_visitors["alice"] = now
    nova._recent_visitors["bob"] = now - 8 * 86400

    nova.warm_default_locations()

    assert warm_env.calls == ["alice"]
    assert "alice" in nova._recent_visitors
    assert "bob" not in nova._recent_visitors


# ---------------------------------------------------------------------------
# before_request hooks
# ---------------------------------------------------------------------------

def test_note_recent_visitor_records_only_outside_testing(monkeypatch):
    from flask import g

    visitors = {}
    monkeypatch.setattr(nova, "_recent_visitors", visitors)

    monkeypatch.setitem(app.config, "TESTING", False)
    with app.test_request_context():
        g.db_user = types.SimpleNamespace(username="alice")
        assert nova._note_recent_visitor() is None
    assert set(visitors) == {"alice"}

    visitors.clear()
    monkeypatch.setitem(app.config, "TESTING", True)
    with app.test_request_context():
        g.db_user = types.SimpleNamespace(username="alice")
        assert nova._note_recent_visitor() is None
    assert visitors == {}


def test_start_warming_once_starts_single_daemon_thread(monkeypatch):
    created = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None, **kwargs):
            self.target = target
            self.daemon = daemon
            self.started = False
            created.append(self)

        def start(self):
            self.started = True  # never run target

    monkeypatch.setitem(app.config, "TESTING", False)
    monkeypatch.setattr(nova, "_warm_started", False)
    monkeypatch.setattr(nova, "threading", types.SimpleNamespace(Thread=FakeThread))

    assert nova._start_warming_once() is None
    assert len(created) == 1
    assert created[0].daemon is True
    assert created[0].started is True
    assert created[0].target is nova._warm_loop

    assert nova._start_warming_once() is None
    assert len(created) == 1
