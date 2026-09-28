"""
Cross-worker purge sync: purge_user_app_data writes CACHE_DIR/purged_users.json,
and nova.apply_purge_note (a before_request hook in multi-user mode) drops each
newly listed user's in-memory caches in the worker that runs it.

"Another worker" is simulated by writing the note directly, without busting
anything locally. CACHE_DIR is per-test (conftest isolated_cache_dir).
"""
import json
import os

import pytest

import nova
from nova.config import (
    astro_context_cache, nightly_curves_cache, observable_objects_cache, cache_worker_status,
)
from nova.helpers import (
    purge_note_path, read_purge_note, append_purge_note, purge_user_app_data,
    get_user_log_string,
)
from nova.models import DbUser

X_ID, X_NAME = 7, "xavier"
Y_ID, Y_NAME = 70, "yvonne"


@pytest.fixture(autouse=True)
def fresh_worker(monkeypatch):
    """Each test starts as a worker that has never checked the note."""
    monkeypatch.setattr(nova, "_purge_note_stamp", None)
    monkeypatch.setattr(nova, "_purge_note_applied", set())
    yield
    for uid, name in ((X_ID, X_NAME), (Y_ID, Y_NAME)):
        nova._drop_purged_user_caches(uid, name)


def _status_key(uid, name):
    return f"({get_user_log_string(uid, name)})_Home"


def _fill(uid, name):
    astro_context_cache[uid] = {"user": name}
    nightly_curves_cache[f"{name}_m42_2026-09-28_Home"] = [1]
    observable_objects_cache[f"obs_objects:{name}:Home:2026-09-28"] = [1]
    cache_worker_status[_status_key(uid, name)] = "complete"
    nova._last_warmed[name] = ("Home",)
    nova._recent_visitors[name] = 1.0


def _present(uid, name):
    return {
        "astro_context": uid in astro_context_cache,
        "nightly_curves": f"{name}_m42_2026-09-28_Home" in nightly_curves_cache,
        "observable_objects": f"obs_objects:{name}:Home:2026-09-28" in observable_objects_cache,
        "worker_status": _status_key(uid, name) in cache_worker_status,
        "last_warmed": name in nova._last_warmed,
        "recent_visitors": name in nova._recent_visitors,
    }


ALL = {k: True for k in ("astro_context", "nightly_curves", "observable_objects",
                         "worker_status", "last_warmed", "recent_visitors")}
NONE = {k: False for k in ALL}


def _write_note(entries):
    """Write the note the way another worker would (atomic replace, no local bust)."""
    path = purge_note_path()
    tmp = path + ".test.tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f)
    os.replace(tmp, path)


def _entry(uid, name, at="2026-09-28T10:00:00+00:00"):
    return {"user_id": uid, "username": name, "purged_at": at}


def test_note_entry_drops_only_that_users_caches():
    nova.apply_purge_note()  # first check: no note yet
    _fill(X_ID, X_NAME)
    _fill(Y_ID, Y_NAME)

    _write_note([_entry(X_ID, X_NAME)])
    nova.apply_purge_note()

    assert _present(X_ID, X_NAME) == NONE
    assert _present(Y_ID, Y_NAME) == ALL


def test_unchanged_note_does_nothing(monkeypatch):
    nova.apply_purge_note()
    _write_note([_entry(X_ID, X_NAME)])
    nova.apply_purge_note()

    # A new account reusing the name fills caches again; the old entry must not touch them
    _fill(X_ID, X_NAME)
    calls = []
    monkeypatch.setattr(nova, "read_purge_note", lambda: calls.append(1) or [])
    nova.apply_purge_note()

    assert calls == []  # stat only, note not re-read
    assert _present(X_ID, X_NAME) == ALL


def test_fresh_worker_marks_existing_entries_applied():
    _write_note([_entry(X_ID, X_NAME), _entry(None, "ghost")])
    _fill(X_ID, X_NAME)  # e.g. a new account with the same id/name, cached after the purge

    nova.apply_purge_note()

    assert _present(X_ID, X_NAME) == ALL
    assert nova._purge_note_applied == {(X_ID, X_NAME, "2026-09-28T10:00:00+00:00"),
                                        (None, "ghost", "2026-09-28T10:00:00+00:00")}

    # Later entries are still applied
    _fill(Y_ID, Y_NAME)
    _write_note([_entry(X_ID, X_NAME), _entry(None, "ghost"), _entry(Y_ID, Y_NAME)])
    nova.apply_purge_note()
    assert _present(X_ID, X_NAME) == ALL
    assert _present(Y_ID, Y_NAME) == NONE


def test_entry_without_user_id_busts_username_caches_only():
    nova.apply_purge_note()
    _fill(X_ID, X_NAME)
    _write_note([_entry(None, X_NAME)])
    nova.apply_purge_note()

    assert _present(X_ID, X_NAME) == {**NONE, "astro_context": True, "worker_status": True}


@pytest.fixture
def purge_env(db_session, tmp_path, monkeypatch):
    import nova.auth
    for attr in ("UPLOAD_FOLDER", "CONFIG_DIR", "ASIAIR_LOGS_DIR", "PHD2_LOGS_DIR", "NINA_LOGS_DIR"):
        path = tmp_path / attr.lower()
        path.mkdir()
        monkeypatch.setattr(f"nova.helpers.{attr}", str(path))
    monkeypatch.setattr("nova.helpers.ADMIN_USERS", set())
    monkeypatch.setattr(nova.auth, "db", None)  # login already gone from users.db
    db_session.add(DbUser(id=X_ID, username=X_NAME))
    db_session.commit()
    return db_session


def test_dry_run_does_not_append(purge_env):
    summary = purge_user_app_data(X_NAME, dry_run=True)
    assert summary["refused"] is None
    assert not os.path.exists(purge_note_path())


def test_real_purge_appends_entry(purge_env):
    summary = purge_user_app_data(X_NAME, dry_run=False)
    assert summary["refused"] is None

    entries = read_purge_note()
    assert len(entries) == 1
    assert entries[0]["user_id"] == X_ID
    assert entries[0]["username"] == X_NAME
    assert isinstance(entries[0]["purged_at"], str)


def test_note_keeps_last_500_entries(monkeypatch):
    _write_note([_entry(i, f"u{i}") for i in range(500)])
    append_purge_note(X_ID, X_NAME)

    entries = read_purge_note()
    assert len(entries) == 500
    assert entries[0]["username"] == "u1"
    assert entries[-1]["username"] == X_NAME


def test_missing_note_is_harmless():
    assert not os.path.exists(purge_note_path())
    nova.apply_purge_note()
    nova.apply_purge_note()
    assert nova._purge_note_stamp == ()


def test_corrupt_note_is_harmless_and_recoverable():
    nova.apply_purge_note()
    with open(purge_note_path(), "w") as f:
        f.write("{not json")

    nova.apply_purge_note()  # warns, does not raise

    # The next purge starts a new note, which is applied normally
    _fill(X_ID, X_NAME)
    append_purge_note(X_ID, X_NAME)
    nova.apply_purge_note()
    assert _present(X_ID, X_NAME) == NONE


def test_request_continues_with_corrupt_note(multi_user_client):
    client, _ids = multi_user_client
    with open(purge_note_path(), "w") as f:
        f.write("[{broken")

    resp = client.get("/")
    assert resp.status_code in (200, 302)
