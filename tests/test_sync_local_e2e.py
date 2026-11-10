"""Instance sync, step 6 — the local side end to end.

Two apps in one process, no network: the local is the Flask app (``nova.app``)
paired to a hub, and the hub is a second Flask app running only the sync API
blueprint over its own in-memory database. ``SyncClient`` is routed to the hub's
test client through a fake requests-style session, so every byte really crosses
the local/hub boundary. The job is driven exactly as the routes do — start_job /
confirm_job / abort_job — and the background thread is joined by polling the
on-disk job status.
"""

import json
import os
import time
from datetime import date, datetime

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

import nova
from nova.models import (
    Base, DbUser, JournalSession, Location, AstroObject,
    SyncState, SyncToken,
)
from nova.sync_client import SyncClient, SyncTransportError
from nova.sync_engine import SyncRefused, Refusal, counter_rev, start_pairing
from nova.sync_job import (
    abort_job, clear_job, confirm_job, job_status, load_job, save_job, start_job,
)
from nova.sync_time import utc_now
from nova.sync_transport import hash_token, load_push_session, new_token
from nova.blueprints.sync_api import sync_api_bp

HUB_URL = "https://hub.invalid"
PNG = b"\x89PNG\r\n\x1a\n" + b"hub-photo-bytes" * 16
BIG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40  # ~10 KB, single chunk


# --- No-network transport -----------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, data, headers=None):
        self.status_code = status_code
        self._data = data or b""
        self.headers = headers or {}

    def json(self):
        return json.loads(self._data.decode("utf-8") or "{}")

    def iter_content(self, chunk_size=1024):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]


class _FakeSession:
    """Forwards SyncClient's requests.post to the hub's Flask test client."""

    def __init__(self, hub_app):
        self._app = hub_app

    def post(self, url, **kwargs):
        from urllib.parse import urlparse
        path = urlparse(url).path
        headers = dict(kwargs.get("headers") or {})
        data = None
        content_type = None
        if kwargs.get("data") is not None:
            data = kwargs["data"]
            content_type = "application/octet-stream"
        elif kwargs.get("json") is not None:
            data = json.dumps(kwargs["json"])
            content_type = "application/json"
        resp = self._app.test_client().post(path, data=data, headers=headers,
                                            content_type=content_type)
        return _FakeResponse(resp.status_code, resp.data, dict(resp.headers))


class _TestSyncClient(SyncClient):
    """Small pages, so a download exercises the keyset pager every time."""

    def snapshot(self, page_limit=2, max_restarts=3):
        return super().snapshot(page_limit=page_limit, max_restarts=max_restarts)


# --- Harness ------------------------------------------------------------------

def _patch_sync_dirs(monkeypatch, tmp_path):
    """Point every sync filesystem root at a fresh per-test directory.

    Each sync module binds INSTANCE_PATH / UPLOAD_FOLDER / BACKUP_DIR / CACHE_DIR
    from config at import time, so isolating a test means patching every
    binding. Everything the local side and the hub write — jobs, staging,
    replaced files, the upload accumulator, the push session and the pre-push
    snapshots — then lands under tmp_path and disappears with the test instead
    of lingering in the shared session instance.
    """
    instance = tmp_path / "instance"
    uploads = instance / "uploads"
    backups = instance / "backups"
    cache = instance / "cache"
    for mod, attr, value in (
        ("nova.sync_job", "INSTANCE_PATH", instance),
        ("nova.sync_job", "BACKUP_DIR", backups),
        ("nova.sync_job", "UPLOAD_FOLDER", uploads),
        ("nova.sync_job", "CACHE_DIR", cache),
        ("nova.sync_engine", "INSTANCE_PATH", instance),
        ("nova.sync_engine", "UPLOAD_FOLDER", uploads),
        ("nova.sync_transport", "INSTANCE_PATH", instance),
        ("nova.sync_transport", "BACKUP_DIR", backups),
        ("nova.sync_transport", "CACHE_DIR", cache),
    ):
        monkeypatch.setattr(f"{mod}.{attr}", str(value))
    return instance


@pytest.fixture
def harness(db_session, monkeypatch, tmp_path):
    instance = _patch_sync_dirs(monkeypatch, tmp_path)
    user = nova.get_or_create_db_user(db_session, "default")
    db_session.commit()

    hub_engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                               connect_args={"check_same_thread": False})
    Base.metadata.create_all(hub_engine)
    HubSession = scoped_session(sessionmaker(bind=hub_engine, autocommit=False,
                                             autoflush=False))
    monkeypatch.setattr('nova.blueprints.sync_api.get_db', lambda: HubSession())

    hub_app = Flask("hub")
    hub_app.config["TESTING"] = True
    hub_app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
    hub_app.register_blueprint(sync_api_bp)

    # Distinct user id (999) so the two installations' per-user staging dirs,
    # lock files, backups and push sessions never collide under the instance dir.
    hdb = HubSession()
    hub_user = DbUser(id=999, username="hubuser")
    hdb.add(hub_user)
    hdb.commit()
    token = new_token()
    hdb.add(SyncToken(user_id=999, token_hash=hash_token(token), label="test",
                      created_at=utc_now()))
    hdb.commit()
    HubSession.remove()

    fake_session = _FakeSession(hub_app)

    def make_client(url, tok):
        return _TestSyncClient(url, tok, session=fake_session)

    monkeypatch.setattr('nova.sync_job.SyncClient', make_client)
    monkeypatch.setattr('nova.blueprints.sync_tokens.SyncClient', make_client)

    start_pairing(db_session, user, server_url=HUB_URL, token=token, account="hubuser")
    db_session.commit()

    return {"app": nova.app, "local_user": user, "local_db": db_session,
            "hub_user": hub_user, "hub_session": HubSession, "hub_app": hub_app,
            "token": token, "instance_path": str(instance)}


def _hub(harness, *rows):
    """Add rows to the hub (auto-stamped by the sync tracking listener)."""
    db = harness["hub_session"]()
    for row in rows:
        db.add(row)
    db.commit()
    harness["hub_session"].remove()


def _hub_query(harness, fn):
    db = harness["hub_session"]()
    try:
        return fn(db)
    finally:
        harness["hub_session"].remove()


def _local_query(harness, fn):
    harness["local_db"].expire_all()
    return fn(harness["local_db"])


def _obj(user_id, name, ra=1.0, dec=2.0):
    return AstroObject(user_id=user_id, object_name=name, ra_hours=ra, dec_deg=dec)


def _session(user_id, external_id, image=None):
    return JournalSession(user_id=user_id, external_id=external_id,
                          date_utc=date(2026, 1, 2), session_image_file=image)


def _write_upload(instance_path, username, filename, data):
    d = os.path.join(instance_path, "uploads", username)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, filename), "wb") as f:
        f.write(data)


def _mark_downloaded(harness):
    db = harness["local_db"]
    db.query(SyncState).filter_by(user_id=harness["local_user"].id).update(
        {"last_sync_at": utc_now()})
    db.commit()


def _wait(uid, predicate, timeout=20.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = job_status(uid)
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job did not reach state; last={last}")


def _phase(uid):
    return (job_status(uid) or {}).get("phase")


def _run_phase1(harness, kind):
    start_job(harness["app"], harness["local_user"].id, kind)
    return _wait(harness["local_user"].id,
                 lambda st: st.get("phase") in ("awaiting_confirmation", "failed"))


def _stamp(db, model, row_id, moment):
    db.execute(text(f"UPDATE {model.__tablename__} "
                    f"SET sync_changed_at = :m WHERE id = :i;"),
               {"m": moment.strftime("%Y-%m-%d %H:%M:%S.%f"), "i": row_id})
    db.commit()
    db.expire_all()


# =============================================================================
# 1. Download end to end, with files across several snapshot pages
# =============================================================================

def test_download_end_to_end_with_files(harness):
    uid = harness["local_user"].id
    # Local has data that must be moved aside.
    _write_upload(harness["instance_path"], "default", "old.jpg", b"local-old-bytes")
    _local_query(harness, lambda db: db.add(_session(uid, "old-sess", image="old.jpg")) or None)
    harness["local_db"].commit()

    # Hub has several objects (several pages) + a session with a photo.
    _hub(harness,
         _obj(999, "M 1"), _obj(999, "M 2"), _obj(999, "M 3"), _obj(999, "M 4"),
         _obj(999, "M 5"),
         _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", PNG)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    summary = st["summary"]
    assert summary["kind"] == "download"
    assert summary["requires_replace_typed"] is True
    assert summary["server_counts"]["astro_objects"] == 5
    assert summary["local_counts"].get("astro_objects", 0) == 0
    assert summary["files"]["incoming"] == 1            # hub.jpg
    assert summary["files"]["replaced"] >= 1            # old.jpg moved aside

    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    # Local now matches the hub.
    names = sorted(o.object_name for o in
                   _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid).all()))
    assert names == ["M 1", "M 2", "M 3", "M 4", "M 5"]
    assert _local_query(harness, lambda db: db.query(JournalSession).filter_by(user_id=uid).one()).external_id == "hub-sess"

    # The photo landed (local naming), the displaced file was moved aside.
    uploads = os.path.join(harness["instance_path"], "uploads", "default")
    placed = [n for n in os.listdir(uploads) if os.path.isfile(os.path.join(uploads, n))]
    assert "old.jpg" not in placed
    assert any(open(os.path.join(uploads, n), "rb").read() == PNG for n in placed)

    replaced_root = os.path.join(harness["instance_path"], "replaced", str(uid))
    assert os.path.isdir(replaced_root)

    # Backup dir written (app.db + uploads/ + logs/).
    backups = os.path.join(harness["instance_path"], "backups", "sync", str(uid))
    dirs = [d for d in os.listdir(backups) if os.path.isdir(os.path.join(backups, d))]
    assert len(dirs) == 1
    assert os.path.exists(os.path.join(backups, dirs[0], "app.db"))
    assert os.path.exists(os.path.join(backups, dirs[0], "uploads"))


# =============================================================================
# 2. Sync now, both directions, with files, a deletion and a conflict
# =============================================================================

def test_sync_now_both_directions(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)

    # Local: an object to push up + a session photo.
    _local_query(harness, lambda db: db.add(_obj(uid, "LOCAL", ra=5.0, dec=-10.0)))
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="local.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "local.jpg", PNG)

    # Hub: an object to pull down + a "SHARED" object that both will edit.
    _hub(harness, _obj(999, "HUB", ra=9.0), _obj(999, "SHARED", ra=1.0))

    # First sync: both sides converge on LOCAL, HUB, SHARED.
    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    assert _hub_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=999, object_name="LOCAL").one()).ra_hours == 5.0
    assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid, object_name="HUB").one()).ra_hours == 9.0
    # The local's photo reached the hub.
    hub_photo = _hub_query(harness, lambda db: db.query(JournalSession).filter_by(user_id=999, external_id="local-sess").one())
    assert hub_photo.session_image_file  # a name was written

    # Watermarks moved.
    state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert state.server_cursor > 0
    assert state.local_pushed_rev > 0

    # Conflict: both edit SHARED, hub later (wins), and hub deletes HUB.
    shared_local = _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid, object_name="SHARED").one())
    shared_local.ra_hours = 2.0
    harness["local_db"].commit()

    def hub_edit(db):
        shared = db.query(AstroObject).filter_by(user_id=999, object_name="SHARED").one()
        shared.ra_hours = 3.0
        hub_obj = db.query(AstroObject).filter_by(user_id=999, object_name="HUB").one()
        db.delete(hub_obj)
        db.commit()
    _hub_query(harness, hub_edit)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    # Newer hub edit won the conflict; the hub deletion reached the local.
    assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid, object_name="SHARED").one()).ra_hours == 3.0
    assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid, object_name="HUB").one_or_none()) is None


# =============================================================================
# 3. Apply guard: local edit after summary -> stop and re-ask
# =============================================================================

def test_apply_guard_local_edit_between_summary_and_confirm(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _obj(999, "HUB-ONLY"))

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st

    # A local edit slips in after the summary was shown.
    _local_query(harness, lambda db: db.add(_obj(uid, "SNEAKY")))
    harness["local_db"].commit()

    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st
    assert st["error"]["code"] == "plan_stale", st
    # Nothing from the hub was applied.
    assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid, object_name="HUB-ONLY").one_or_none()) is None


# =============================================================================
# 4. Abort: artifacts deleted, push session cancelled, nothing applied
# =============================================================================

def test_abort_clears_artifacts_and_cancels_push(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_obj(uid, "LOCAL")))
    harness["local_db"].commit()

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert load_push_session(999) is not None   # the plan opened a push session

    abort_job(harness["app"], uid)

    assert load_job(uid) is None                # job + artifacts cleared
    assert load_push_session(999) is None       # push session cancelled on the hub
    # Nothing was pushed or pulled.
    assert _hub_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=999, object_name="LOCAL").one_or_none()) is None


# =============================================================================
# 5. Expiry: an unconfirmed job past 30 min is swept and cannot be confirmed
# =============================================================================

def test_expired_unconfirmed_job_is_swept(harness):
    uid = harness["local_user"].id
    _hub(harness, _obj(999, "M 1"))

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation"

    # Age the job beyond the expiry.
    job = load_job(uid)
    job["heartbeat_at"] = "2000-01-01T00:00:00"
    job["started_at"] = "2000-01-01T00:00:00"
    save_job(uid, job)

    status = job_status(uid)
    assert status.get("expired") is True
    assert load_job(uid) is None                # swept

    with pytest.raises(SyncRefused) as caught:
        confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    assert caught.value.refusal.code == "sync_running"


# =============================================================================
# 9. Concurrent second start -> sync_running
# =============================================================================

def test_concurrent_start_is_refused(harness):
    uid = harness["local_user"].id
    _hub(harness, _obj(999, "M 1"))
    _run_phase1(harness, "download")            # now awaiting_confirmation

    with pytest.raises(SyncRefused) as caught:
        start_job(harness["app"], uid, "download")
    assert caught.value.refusal.code == "sync_running"


# =============================================================================
# 6. plan_stale between push/plan and push/apply -> automatic re-plan, bounded
# =============================================================================

def test_plan_stale_retries_then_succeeds(harness, monkeypatch):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_obj(uid, "LOCAL")))
    harness["local_db"].commit()

    calls = {"n": 0}
    orig = _TestSyncClient.push_apply

    def flaky(self, session_id, fingerprint):
        calls["n"] += 1
        if calls["n"] == 1:
            # The hub account moves between the plan and the apply: the first
            # apply re-plans and refuses plan_stale, the retry re-plans clean.
            _hub(harness, _obj(999, "INTRUDER"))
        return orig(self, session_id, fingerprint)

    monkeypatch.setattr(_TestSyncClient, "push_apply", flaky)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert calls["n"] >= 2                                    # plan_stale, then retry
    assert _hub_query(harness, lambda db: db.query(AstroObject)
                      .filter_by(user_id=999, object_name="LOCAL").one_or_none()) is not None


# =============================================================================
# 7. Dead worker: interrupted at every phase, never half-applied
# =============================================================================

def test_dead_worker_reported_interrupted_at_every_phase(harness):
    uid = harness["local_user"].id
    for phase in ("queued", "planning", "backup", "upload", "download_files", "applying"):
        save_job(uid, {"job_id": "x", "start_token": "x", "user_id": uid, "kind": "sync",
                       "phase": phase, "progress": 0.5, "phase_label": phase,
                       "summary": None, "result": None, "error": None,
                       "started_at": "2000-01-01T00:00:00",
                       "heartbeat_at": "2000-01-01T00:00:00", "pid": os.getpid()})
        st = job_status(uid)
        assert st["phase"] == "failed", (phase, st)
        assert st["running"] is False, (phase, st)
        assert st["error"]["code"] == "interrupted", (phase, st)
    clear_job(uid)


def test_dead_worker_never_half_applies(harness, monkeypatch):
    uid = harness["local_user"].id
    _local_query(harness, lambda db: db.add(_obj(uid, "LOCAL-KEEP")))
    harness["local_db"].commit()
    _hub(harness, _obj(999, "HUB-1"))

    def boom(*a, **k):
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr("nova.sync_job.apply_download", boom)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st

    # DB untouched: the local record is still there and none of the hub's
    # records were applied.
    assert _local_query(harness, lambda db: db.query(AstroObject)
                        .filter_by(user_id=uid, object_name="LOCAL-KEEP").one_or_none()) is not None
    assert _local_query(harness, lambda db: db.query(AstroObject)
                        .filter_by(user_id=uid, object_name="HUB-1").one_or_none()) is None


# =============================================================================
# 8. Interrupted chunk upload and Range download resume end to end
# =============================================================================

def test_interrupted_upload_resumes(harness, monkeypatch):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)

    calls = {"n": 0}
    orig = _TestSyncClient.push_file_chunk

    def flaky(self, session_id, sha256, offset, chunk):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SyncTransportError("simulated dropped chunk")
        return orig(self, session_id, sha256, offset, chunk)

    monkeypatch.setattr(_TestSyncClient, "push_file_chunk", flaky)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert calls["n"] >= 2                                    # dropped chunk, then retry
    # The photo arrived on the hub.
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="local-sess").one())
    assert hub_sess.session_image_file


def test_interrupted_download_resumes_via_range(harness, monkeypatch):
    uid = harness["local_user"].id
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", BIG)

    state = {"fail": True, "resume_from": []}
    orig = _TestSyncClient.fetch_file_iter

    def flaky(self, ref, resume_from=0, chunk=1024 * 1024):
        state["resume_from"].append(resume_from)
        if state["fail"] and resume_from == 0:
            def gen():
                yield BIG[:100]
                raise SyncTransportError("simulated dropped download")
            return gen()
        return orig(self, ref, resume_from=resume_from, chunk=chunk)

    monkeypatch.setattr(_TestSyncClient, "fetch_file_iter", flaky)

    # First download drops after 100 bytes -> the .part holds 100 bytes.
    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st

    # Next manual download resumes the partial file via a Range header.
    state["fail"] = False
    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    assert state["resume_from"][-1] == 100                     # Range resume used
    uploads = os.path.join(harness["instance_path"], "uploads", "default")
    assert any(open(os.path.join(uploads, n), "rb").read() == BIG for n in os.listdir(uploads))


# =============================================================================
# 10. Push mass-delete summary reports the refusal's count
# =============================================================================

def test_push_mass_delete_summary_reports_the_refusal_count(harness, monkeypatch):
    uid = harness["local_user"].id
    _mark_downloaded(harness)

    def refuse_push(self, payload, clock_offset_seconds, allow_mass_delete=False):
        raise SyncRefused(Refusal("mass_delete",
                                  {"deletions": 42, "records": 50, "limit": 0.10}))
    monkeypatch.setattr(_TestSyncClient, "push_plan", refuse_push)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    summary = st["summary"]
    assert summary["mass_delete"] is True
    assert summary["up"]["deleted"] == 42
    assert summary["mass_delete_detail"]["deletions"] == 42


# =============================================================================
# 11. Apply guard (rule 1): deletions, mass delete, and explicit confirm
# =============================================================================

def _sync_shared_objects(harness, count, *, prefix="OBJ"):
    """Seed `count` objects on the hub and pull them to the local in one sync."""
    uid = harness["local_user"].id
    _hub(harness, *[_obj(999, f"{prefix}{i}") for i in range(count)])
    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st


def _hub_delete_all_objects(harness):
    def fn(db):
        for o in db.query(AstroObject).filter_by(user_id=999).all():
            db.delete(o)
        db.commit()
    _hub_query(harness, fn)


def test_apply_guard_extra_hub_deletions_stop_the_sync(harness):
    """More deletions than the summary showed -> plan_stale, nothing applied."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 3)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["deletions"] == 0

    # A hub deletion slips in after the summary was shown.
    _hub_query(harness, lambda db: (
        db.delete(db.query(AstroObject).filter_by(user_id=999, object_name="OBJ0").one()),
        db.commit()))

    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st
    assert st["error"]["code"] == "plan_stale", st


def test_apply_guard_new_mass_delete_at_confirm_stops(harness):
    """A mass delete that appears only at confirm (not in the summary) stops."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 40)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["mass_delete"] is False

    _hub_delete_all_objects(harness)   # now the pull is a mass delete, never shown

    confirm_job(harness["app"], uid)   # no allow_mass_delete
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st
    assert st["error"]["code"] == "mass_delete", st


def test_pull_mass_delete_applied_only_after_explicit_confirm(harness):
    """The mass delete is refused without approval, then applied with it."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 40)

    _hub_delete_all_objects(harness)   # phase 1 shows the mass delete

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["mass_delete"] is True

    with pytest.raises(SyncRefused) as exc:
        confirm_job(harness["app"], uid)   # no explicit approval
    assert exc.value.refusal.code == "mass_delete"
    assert job_status(uid)["phase"] == "awaiting_confirmation"
    assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid).count()) == 40

    confirm_job(harness["app"], uid, allow_mass_delete=True)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(user_id=uid).count()) == 0


# =============================================================================
# 12. Pull excludes the push revs (rule 2)
# =============================================================================

def test_pull_excludes_the_push_revs(harness, monkeypatch):
    """The job passes push_revs as exclude_revs — a [] would fail this."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_obj(uid, "LOCAL")))
    harness["local_db"].commit()
    _hub(harness, _obj(999, "HUB"))

    seen = []
    orig = _TestSyncClient.changes

    def spy(self, since_rev, exclude_revs=None, with_files=True):
        seen.append(list(exclude_revs or []))
        return orig(self, since_rev, exclude_revs=exclude_revs, with_files=with_files)
    monkeypatch.setattr(_TestSyncClient, "changes", spy)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    assert len(seen) >= 2            # phase 1 (provisional) + phase 2 (real)
    assert seen[0] == []             # the provisional pull excludes nothing
    assert seen[-1] != []            # the real pull excludes the push revs
