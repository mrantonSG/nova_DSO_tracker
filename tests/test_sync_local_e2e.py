"""Instance sync, step 6 — the local side end to end.

Two apps in one process, no network: the local is the Flask app (``nova.app``)
paired to a hub, and the hub is a second Flask app running only the sync API
blueprint over its own in-memory database. ``SyncClient`` is routed to the hub's
test client through a fake requests-style session, so every byte really crosses
the local/hub boundary. The job is driven exactly as the routes do — start_job /
confirm_job / abort_job — and the background thread is joined by polling the
on-disk job status.
"""

import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

import nova
from nova.models import (
    Base, DbUser, JournalSession, Location, AstroObject, Project,
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


def test_download_multi_image_notes(harness):
    """A hub note with several images downloads all of them, not just the first."""
    uid = harness["local_user"].id
    instance = harness["instance_path"]

    proj_images = {"np1.png": PNG, "np2.png": PNG + b"-2", "np3.png": PNG + b"-3"}
    obj_images = {"no1.png": PNG + b"-4", "no2.png": PNG + b"-5"}

    def seed(db):
        proj = Project(id="hub-proj", user_id=999, name="Veil",
                       status="In Progress")
        proj.description_notes = "".join(
            f'<img src="/uploads/hubuser/{n}">' for n in proj_images)
        db.add(proj)
        obj = AstroObject(user_id=999, object_name="HUB-NOTE",
                          ra_hours=1.0, dec_deg=2.0)
        obj.shared_notes = "".join(
            f'<img src="/uploads/hubuser/{n}">' for n in obj_images)
        db.add(obj)
        db.commit()
    _hub_query(harness, seed)
    for name, data in {**proj_images, **obj_images}.items():
        _write_upload(instance, "hubuser", name, data)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    # Every image landed under the local naming and the links were rewritten.
    local_uploads = os.path.join(instance, "uploads", "default")
    got = {}
    for name in os.listdir(local_uploads):
        with open(os.path.join(local_uploads, name), "rb") as handle:
            got[hashlib.sha256(handle.read()).hexdigest()] = name
    for data in list(proj_images.values()) + list(obj_images.values()):
        assert hashlib.sha256(data).hexdigest() in got

    proj = _local_query(harness, lambda db: db.query(Project).filter_by(
        user_id=uid, id="hub-proj").one())
    assert "/uploads/hubuser/" not in proj.description_notes
    assert proj.description_notes.count("/uploads/default/note_img_") == 3

    obj = _local_query(harness, lambda db: db.query(AstroObject).filter_by(
        user_id=uid, object_name="HUB-NOTE").one())
    assert "/uploads/hubuser/" not in obj.shared_notes
    assert obj.shared_notes.count("/uploads/default/note_img_") == 2


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


def test_push_multi_image_notes(harness):
    """A local note with several images pushes all of them to the hub."""
    uid = harness["local_user"].id
    instance = harness["instance_path"]
    _mark_downloaded(harness)

    images = {"lp1.png": PNG, "lp2.png": PNG + b"-2", "lp3.png": PNG + b"-3"}

    def seed(db):
        proj = Project(id="local-proj", user_id=uid, name="Veil",
                       status="In Progress")
        proj.description_notes = "".join(
            f'<img src="/uploads/default/{n}">' for n in images)
        db.add(proj)
        db.commit()
    _local_query(harness, seed)
    for name, data in images.items():
        _write_upload(instance, "default", name, data)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    hub_proj = _hub_query(harness, lambda db: db.query(Project).filter_by(
        user_id=999, id="local-proj").one())
    assert hub_proj.description_notes.count("/uploads/hubuser/note_img_") == 3

    hub_uploads = os.path.join(instance, "uploads", "hubuser")
    got = {}
    for name in os.listdir(hub_uploads):
        with open(os.path.join(hub_uploads, name), "rb") as handle:
            got[hashlib.sha256(handle.read()).hexdigest()] = name
    for data in images.values():
        assert hashlib.sha256(data).hexdigest() in got


def test_empty_sync_skips_confirmation_and_updates_watermarks(harness):
    """A sync with nothing to push or pull completes without a confirm step."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)

    start_job(harness["app"], uid, "sync")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed", "awaiting_confirmation"))
    assert st["phase"] == "done", st
    assert st["result"]["empty"] is True

    # The watermarks still advance as if the (empty) sync had run today.
    state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert state.last_sync_at is not None


def test_empty_sync_cursor_is_phase1_hub_rev_and_later_hub_change_is_pulled(harness, monkeypatch):
    """The empty path stores the hub rev phase 1 saw, so a later hub change is pulled."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)

    # Give the hub a non-zero rev and bring the local level with it.
    _hub(harness, _obj(999, "HUB-1"))
    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    # Record the rev of every /changes payload the job receives.
    seen = []
    original = SyncClient.changes

    def recording_changes(self, *args, **kwargs):
        payload = original(self, *args, **kwargs)
        seen.append(int(payload.get("rev") or 0))
        return payload
    monkeypatch.setattr(SyncClient, "changes", recording_changes)

    start_job(harness["app"], uid, "sync")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed", "awaiting_confirmation"))
    assert st["phase"] == "done", st
    assert st["result"]["empty"] is True
    assert len(seen) == 1                        # phase 1 only; no second fetch
    hub_rev = _hub_query(harness, lambda db: counter_rev(db, 999))
    assert seen[0] == hub_rev > 0
    state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert state.server_cursor == seen[0]

    # A hub change after the empty sync is pulled by the next one.
    _hub(harness, _obj(999, "HUB-2", ra=7.0))
    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["down"]["created"] >= 1
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    hub2 = _local_query(harness, lambda db: db.query(AstroObject).filter_by(
        user_id=uid, object_name="HUB-2").one())
    assert hub2.ra_hours == 7.0


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


def test_failed_job_keeps_its_progress(harness):
    """A failed job reports the progress it reached, never a full 1.0."""
    uid = harness["local_user"].id
    _hub(harness, _obj(999, "HUB-ONLY"))

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st

    # A local edit lands between the summary and the confirm: phase 2 re-checks
    # the counter and refuses, so the job fails without moving the cursor.
    _local_query(harness, lambda db: db.add(_obj(uid, "SNEAKY")))
    harness["local_db"].commit()

    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st
    assert st["error"]["code"] == "plan_stale", st
    assert st["progress"] < 1.0, st


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

    # A new hub record makes this sync non-empty, so it pauses for a summary
    # instead of completing instantly (an empty sync skips the confirm step).
    _hub(harness, _obj(999, "OBJ-NEW"))

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

    # A new hub record makes this sync non-empty, so it pauses for a summary
    # instead of completing instantly (an empty sync skips the confirm step).
    _hub(harness, _obj(999, "OBJ-NEW"))

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["mass_delete"] is False

    # Deleting the original 40 (but not OBJ-NEW) turns the confirm's re-plan
    # into a mass delete the summary never showed.
    def delete_originals(db):
        for o in db.query(AstroObject).filter_by(user_id=999).all():
            if o.object_name != "OBJ-NEW":
                db.delete(o)
        db.commit()
    _hub_query(harness, delete_originals)

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


# =============================================================================
# 13. Shared digests across roles, per-file placement failure, combination
# =============================================================================

def _sha256_of_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _file_digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _upload_digest_map(instance, username):
    root = os.path.join(instance, "uploads", username)
    if not os.path.isdir(root):
        return {}
    out = {}
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            out[_file_digest(path)] = name
    return out


def test_download_shared_digest_across_roles(harness):
    """One digest served as a session photo, a project image and a note image
    lands under three distinct names/columns — the manifest's single role field
    must never override the slot's role."""
    uid = harness["local_user"].id
    instance = harness["instance_path"]
    shared = PNG + b"-shared-roles"
    digest = _sha256_of_bytes(shared)

    def seed(db):
        proj = Project(id="hub-proj", user_id=999, name="Veil", status="In Progress",
                       final_image_file="shared.jpg")
        proj.description_notes = '<img src="/uploads/hubuser/shared.jpg">'
        db.add(proj)
        db.add(JournalSession(user_id=999, external_id="hub-sess",
                              date_utc=date(2026, 1, 2), session_image_file="shared.jpg"))
        db.commit()
    _hub_query(harness, seed)
    _write_upload(instance, "hubuser", "shared.jpg", shared)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert st["result"]["pending_files"] == 0

    proj = _local_query(harness, lambda db: db.query(Project).filter_by(
        user_id=uid, id="hub-proj").one())
    sess = _local_query(harness, lambda db: db.query(JournalSession).filter_by(
        user_id=uid, external_id="hub-sess").one())

    uploads = os.path.join(instance, "uploads", "default")
    assert proj.final_image_file and proj.final_image_file.startswith("project_")
    assert sess.session_image_file and sess.session_image_file.endswith(".jpg")
    assert _file_digest(os.path.join(uploads, proj.final_image_file)) == digest
    assert _file_digest(os.path.join(uploads, sess.session_image_file)) == digest

    # The note image localized to its digest-derived name, same bytes.
    assert "/uploads/hubuser/" not in proj.description_notes
    note_name = f"note_img_{digest[:12]}.jpg"
    assert note_name in os.listdir(uploads)
    assert _file_digest(os.path.join(uploads, note_name)) == digest


def test_push_shared_digest_across_roles(harness):
    """The same bytes pushed up land on the hub under the right columns/names."""
    uid = harness["local_user"].id
    instance = harness["instance_path"]
    _mark_downloaded(harness)
    shared = PNG + b"-shared-push"
    digest = _sha256_of_bytes(shared)

    def seed(db):
        proj = Project(id="local-proj", user_id=uid, name="Veil", status="In Progress",
                       final_image_file="shared.jpg")
        proj.description_notes = '<img src="/uploads/default/shared.jpg">'
        db.add(proj)
        db.add(JournalSession(user_id=uid, external_id="local-sess",
                              date_utc=date(2026, 1, 2), session_image_file="shared.jpg"))
        db.commit()
    _local_query(harness, seed)
    _write_upload(instance, "default", "shared.jpg", shared)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    hub_proj = _hub_query(harness, lambda db: db.query(Project).filter_by(
        user_id=999, id="local-proj").one())
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession).filter_by(
        user_id=999, external_id="local-sess").one())
    assert hub_proj.final_image_file
    assert hub_sess.session_image_file
    hub_uploads = os.path.join(instance, "uploads", "hubuser")
    assert _file_digest(os.path.join(hub_uploads, hub_proj.final_image_file)) == digest
    assert _file_digest(os.path.join(hub_uploads, hub_sess.session_image_file)) == digest
    assert f"note_img_{digest[:12]}.jpg" in os.listdir(hub_uploads)


def test_exception_placing_one_file_is_healed_before_the_run_ends(harness, monkeypatch):
    """A placement crash is counted pending, not fatal: the rest place, and the
    healing pass at the end of the same run fetches the file again and places
    it — so the download completes with nothing left over."""
    uid = harness["local_user"].id
    instance = harness["instance_path"]
    _hub(harness,
         _session(999, "sess-a", image="a.jpg"),
         _session(999, "sess-b", image="b.jpg"))
    _write_upload(instance, "hubuser", "a.jpg", PNG)
    _write_upload(instance, "hubuser", "b.jpg", PNG + b"-b")

    import nova.sync_engine as sync_engine
    real_place_one = sync_engine._place_one
    fired = {"n": 0}

    def flaky(db, user, model, ref, staged, instance_path=None, **kwargs):
        fired["n"] += 1
        if fired["n"] == 1:
            raise RuntimeError("simulated placement failure")
        return real_place_one(db, user, model, ref, staged,
                              instance_path=instance_path, **kwargs)
    monkeypatch.setattr("nova.sync_engine._place_one", flaky)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert fired["n"] >= 3                       # failed once, healed, and the other
    assert st["result"]["unrestored_files"] == 0
    assert st["result"]["pending_files"] == 0

    state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert state.server_cursor > 0
    assert state.last_sync_at is not None
    placed = {n: _file_digest(os.path.join(instance, "uploads", "default", n))
              for n in os.listdir(os.path.join(instance, "uploads", "default"))}
    assert sum(1 for d in placed.values() if d in (_sha256_of_bytes(PNG),
                                                   _sha256_of_bytes(PNG + b"-b"))) == 2


def test_combination_mixed_scenarios(harness):
    """A deterministic mix — shared digests, multi-image notes, a removed slot,
    a missing-on-disk file, an over-cap file and an own log — downloads and
    syncs back with zero refusals and every transferable file in place."""
    uid = harness["local_user"].id
    instance = harness["instance_path"]
    shared = b"SHARED-COMBINATION"
    shared_digest = _sha256_of_bytes(shared)
    note_a = b"note-a"
    note_b = b"note-b"
    log_bytes = b"asiair log line\n" * 20
    log_digest = _sha256_of_bytes(log_bytes)

    def seed(db):
        proj = Project(id="combo-proj", user_id=999, name="Combo", status="In Progress",
                       final_image_file="shared.jpg")
        proj.description_notes = '<img src="/uploads/hubuser/shared.jpg">'
        db.add(proj)
        db.add(JournalSession(user_id=999, external_id="combo-shared",
                              date_utc=date(2026, 1, 2), session_image_file="shared.jpg"))
        proj2 = Project(id="combo-notes", user_id=999, name="Notes", status="In Progress")
        proj2.description_notes = ('<img src="/uploads/hubuser/n1.png">'
                                   '<img src="/uploads/hubuser/n2.png">')
        db.add(proj2)
        db.add(JournalSession(user_id=999, external_id="combo-removed",
                              date_utc=date(2026, 1, 2), session_image_file=""))
        db.add(JournalSession(user_id=999, external_id="combo-missing",
                              date_utc=date(2026, 1, 2), session_image_file="nope.jpg"))
        db.add(JournalSession(user_id=999, external_id="combo-log",
                              date_utc=date(2026, 1, 2),
                              asiair_log_content="instance/logs/asiair/999_combo_log.txt"))
        db.add(JournalSession(user_id=999, external_id="combo-big",
                              date_utc=date(2026, 1, 2), session_image_file="big.jpg"))
        db.commit()

    _hub_query(harness, seed)
    _write_upload(instance, "hubuser", "shared.jpg", shared)
    _write_upload(instance, "hubuser", "n1.png", note_a)
    _write_upload(instance, "hubuser", "n2.png", note_b)
    _write_upload(instance, "hubuser", "big.jpg", b"\x00" * (5 * 1024 * 1024 + 1))
    os.makedirs(os.path.join(instance, "logs", "asiair"), exist_ok=True)
    with open(os.path.join(instance, "logs", "asiair", "999_combo_log.txt"), "wb") as f:
        f.write(log_bytes)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert st["result"]["pending_files"] == 0

    # Every transferable file placed with the right digest.
    got = _upload_digest_map(instance, "default")
    for data in (shared, note_a, note_b):
        assert _sha256_of_bytes(data) in got
    logs_root = os.path.join(instance, "logs", "asiair")
    assert log_digest in {_file_digest(os.path.join(logs_root, n))
                          for n in os.listdir(logs_root)
                          if os.path.isfile(os.path.join(logs_root, n))}
    # The removed slot, missing file and over-cap file carried no bytes.
    assert not any(n.endswith("big.jpg") for n in os.listdir(os.path.join(instance, "uploads", "default")))

    # Sync back both ways: with nothing left to transfer this is an empty sync
    # that completes without a confirm step, and both sides stay equal.
    start_job(harness["app"], uid, "sync")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed", "awaiting_confirmation"))
    assert st["phase"] == "done", st
    assert st["result"]["empty"] is True

    local_projects = _local_query(harness, lambda db: db.query(Project).filter_by(user_id=uid).count())
    hub_projects = _hub_query(harness, lambda db: db.query(Project).filter_by(user_id=999).count())
    assert local_projects == hub_projects == 2


# =============================================================================
# 20. A network drop after the record commit heals, whatever the clocks do
# =============================================================================
#
# The offset is measured afresh on every run. These tests script it, so the run
# that heals always sees a DIFFERENT offset from the run that lost the file, in
# the direction that used to leave the file missing for good.

def _script_offsets(monkeypatch, *offsets):
    """Make each hello() measure the next scripted offset (the last one repeats)."""
    left = list(offsets)

    def measured(_server_time, _t0, _t3):
        return left.pop(0) if len(left) > 1 else left[0]
    monkeypatch.setattr("nova.sync_client.measure_clock_offset", measured)


def _local_photo_digests(harness):
    root = os.path.join(harness["instance_path"], "uploads", "default")
    if not os.path.isdir(root):
        return set()
    return {_file_digest(os.path.join(root, n)) for n in os.listdir(root)
            if os.path.isfile(os.path.join(root, n))}


def _hub_photo_digests(harness):
    root = os.path.join(harness["instance_path"], "uploads", "hubuser")
    if not os.path.isdir(root):
        return set()
    return {_file_digest(os.path.join(root, n)) for n in os.listdir(root)
            if os.path.isfile(os.path.join(root, n))}


def _sync_to_done(harness, **confirm):
    uid = harness["local_user"].id
    start_job(harness["app"], uid, "sync")
    st = _wait(uid, lambda s: s.get("phase") in ("awaiting_confirmation", "done", "failed"))
    if st["phase"] == "awaiting_confirmation":
        confirm_job(harness["app"], uid, **confirm)
        st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    return st


@pytest.mark.parametrize("first,second", [(0.4, -0.4), (-0.4, 0.4), (0.0, -2.0)])
def test_a_drop_during_the_pulls_file_fetch_heals_on_the_next_sync(
        harness, monkeypatch, first, second):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", BIG)
    _script_offsets(monkeypatch, first, second)

    orig = _TestSyncClient.fetch_file_iter
    state = {"fail": True}

    def flaky(self, ref, resume_from=0, chunk=1024 * 1024):
        if state["fail"]:
            raise SyncTransportError("simulated drop after the record commit")
        return orig(self, ref, resume_from=resume_from, chunk=chunk)
    monkeypatch.setattr(_TestSyncClient, "fetch_file_iter", flaky)

    st = _sync_to_done(harness)
    assert st["phase"] == "failed" and st["error"]["code"] == "network_error", st
    # The record is committed, its photo is not here, and the cursor is held.
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    assert not landed.session_image_file
    assert _sha256_of_bytes(BIG) not in _local_photo_digests(harness)
    rev_before = _local_query(harness, lambda db: counter_rev(db, uid))
    stamp_before = landed.sync_changed_at

    state["fail"] = False
    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert st["result"]["down"] == {"created": 0, "updated": 0, "deleted": 0}

    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    assert landed.session_image_file
    assert _sha256_of_bytes(BIG) in _local_photo_digests(harness)
    assert landed.sync_changed_at == stamp_before          # file only: no record write
    assert _local_query(harness, lambda db: counter_rev(db, uid)) == rev_before


def _hub_dies_before_placing(harness, monkeypatch):
    """One Sync now during which the hub commits the pushed records and dies
    before it places their files. No retry inside the run: the next request the
    hub sees is the next Sync now. Returns the failed job's status."""
    import nova.blueprints.sync_api as api
    import nova.sync_job as job

    def died(*_args, **_kwargs):
        raise SyncTransportError("simulated hub death after the record commit")

    with monkeypatch.context() as patch:
        patch.setattr(api, "commit_staged_files", died)
        patch.setattr(job, "_retry_transient", lambda fn, attempts=1: fn())
        return _sync_to_done(harness)


def _hub_owed(harness):
    from types import SimpleNamespace
    from nova.sync_engine import load_owed_files
    return load_owed_files(SimpleNamespace(id=999))


def _hub_staging(harness):
    return os.path.join(harness["instance_path"], "sync_staging", "999")


@pytest.mark.parametrize("first,second", [(0.4, -0.4), (-0.4, 0.4), (0.0, 0.0)])
def test_a_hub_crash_between_commit_and_placement_is_finished_by_the_hub(
        harness, monkeypatch, first, second):
    """The hub commits the pushed records and dies before it places their
    files. It noted what it owed before the commit, so the next sync request
    finds the staged bytes and places them - by any pair of clock offsets, with
    no record rewritten. The row gets a new rev so other computers are offered
    the record again."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    _script_offsets(monkeypatch, first, second)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)

    st = _hub_dies_before_placing(harness, monkeypatch)
    assert st["phase"] == "failed" and st["error"]["code"] == "network_error", st
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="local-sess").one())
    assert not hub_sess.session_image_file                  # record there, file not
    assert _sha256_of_bytes(BIG) not in _hub_photo_digests(harness)
    assert [e["sha256"] for e in _hub_owed(harness)] == [_sha256_of_bytes(BIG)]
    hub_stamp = hub_sess.sync_changed_at
    hub_rev = _hub_query(harness, lambda db: counter_rev(db, 999))
    assert _local_query(harness, lambda db: db.query(SyncState)
                        .filter_by(user_id=uid).one()).local_pushed_rev == 0

    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0

    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="local-sess").one())
    assert hub_sess.session_image_file
    assert _sha256_of_bytes(BIG) in _hub_photo_digests(harness)
    assert _hub_owed(harness) == []
    assert hub_sess.sync_changed_at == hub_stamp            # the record was not rewritten
    assert hub_sess.sync_rev > hub_rev                      # offered to the others again
    assert _local_query(harness, lambda db: db.query(SyncState)
                        .filter_by(user_id=uid).one()).local_pushed_rev > 0


@pytest.mark.parametrize("first,second", [(0.4, -0.4), (-0.4, 0.4), (0.0, 0.0)])
def test_staged_bytes_the_hub_lost_are_uploaded_again(harness, monkeypatch, first, second):
    """As above, and the hub's staged copy is gone as well. The hub cannot
    finish alone, so its next push plan asks for the file again; the client
    uploads it and the hub places it."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    _script_offsets(monkeypatch, first, second)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)

    st = _hub_dies_before_placing(harness, monkeypatch)
    assert st["phase"] == "failed", st
    staging = _hub_staging(harness)
    staged = [n for n in os.listdir(staging) if n.startswith(_sha256_of_bytes(BIG))]
    assert staged
    for name in staged:
        os.remove(os.path.join(staging, name))               # the staged bytes are lost

    uploaded = []
    orig = _TestSyncClient.push_file_chunk

    def spy(self, session_id, sha256, offset, chunk):
        uploaded.append(sha256)
        return orig(self, session_id, sha256, offset, chunk)
    monkeypatch.setattr(_TestSyncClient, "push_file_chunk", spy)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["files"]["up"] == 1                 # the hub asked for it
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))

    assert st["phase"] == "done", st
    assert set(uploaded) == {_sha256_of_bytes(BIG)}
    assert st["result"]["unrestored_files"] == 0
    assert _sha256_of_bytes(BIG) in _hub_photo_digests(harness)
    assert _hub_owed(harness) == []
    assert _local_query(harness, lambda db: db.query(SyncState)
                        .filter_by(user_id=uid).one()).local_pushed_rev > 0


def test_an_owed_file_is_dropped_when_a_newer_edit_supersedes_it(harness, monkeypatch):
    """The hub owes a photo; before the next sync the user replaces that photo.
    The newer push rewrites the hub's row, the old entry no longer matches it
    and is discarded, and the hub ends up with the newer photo only."""
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)
    st = _hub_dies_before_placing(harness, monkeypatch)
    assert st["phase"] == "failed", st
    for name in os.listdir(_hub_staging(harness)):
        if name.startswith(_sha256_of_bytes(BIG)):
            os.remove(os.path.join(_hub_staging(harness), name))

    newer = BIG + b"-replaced"
    _write_upload(harness["instance_path"], "default", "photo.jpg", newer)
    from nova.sync_tracking import mark_file_changed
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="local-sess").one())
    mark_file_changed(row, "session_image_file")
    harness["local_db"].commit()
    _stamp(harness["local_db"], JournalSession, row.id, utc_now() + timedelta(hours=1))

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    digests = _hub_photo_digests(harness)
    assert _sha256_of_bytes(newer) in digests and _sha256_of_bytes(BIG) not in digests
    assert _hub_owed(harness) == []


@pytest.mark.parametrize("first,second", [(0.4, -0.4), (-0.4, 0.4), (-3.0, -3.0)])
def test_a_drop_after_the_downloads_record_commit_heals_on_the_next_sync(
        harness, monkeypatch, first, second):
    uid = harness["local_user"].id
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", BIG)
    _script_offsets(monkeypatch, first, second)

    import nova.sync_job as job
    real_commit = job.commit_staged_files
    state = {"fail": True}

    def dropped(*args, **kwargs):
        if state["fail"]:
            raise SyncTransportError("simulated death after the download's commit")
        return real_commit(*args, **kwargs)
    monkeypatch.setattr(job, "commit_staged_files", dropped)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "failed", st
    sync_state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert sync_state.server_cursor == 0 and sync_state.last_sync_at is None
    assert sync_state.local_pushed_rev > 0                   # the records did commit
    assert _sha256_of_bytes(BIG) not in _local_photo_digests(harness)
    # A staged copy must not be what saves the day: the bytes are fetched again.
    import shutil
    shutil.rmtree(os.path.join(harness["instance_path"], "sync_staging", str(uid)),
                  ignore_errors=True)

    state["fail"] = False
    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert _sha256_of_bytes(BIG) in _local_photo_digests(harness)
    sync_state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert sync_state.server_cursor > 0 and sync_state.last_sync_at is not None


# =============================================================================
# 21. The healing pass
# =============================================================================

def _download_one_photo(harness, data=BIG):
    """Both sides end up holding one session and its photo."""
    uid = harness["local_user"].id
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", data)
    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert _sha256_of_bytes(data) in _local_photo_digests(harness)


def test_a_file_the_hub_lost_is_restored_through_repair(harness):
    """The hub's record names a photo its disk no longer has. Nothing changed,
    so no plan mentions it — the healing pass of an otherwise empty sync
    uploads it through /repair. The record keeps its stamp, and the row gets a
    new rev so a computer that pulled it while the file was gone is offered it
    again."""
    uid = harness["local_user"].id
    _download_one_photo(harness)
    hub_path = os.path.join(harness["instance_path"], "uploads", "hubuser", "hub.jpg")
    os.remove(hub_path)
    hub_rev = _hub_query(harness, lambda db: counter_rev(db, 999))
    hub_stamp = _hub_query(harness, lambda db: db.query(JournalSession)
                           .filter_by(user_id=999, external_id="hub-sess").one()
                           .sync_changed_at)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert _sha256_of_bytes(BIG) in _hub_photo_digests(harness)
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="hub-sess").one())
    hub_file = os.path.join(harness["instance_path"], "uploads", "hubuser",
                            hub_sess.session_image_file)
    assert _file_digest(hub_file) == _sha256_of_bytes(BIG)
    assert _hub_query(harness, lambda db: counter_rev(db, 999)) == hub_rev + 1
    assert hub_sess.sync_rev == hub_rev + 1                 # offered again
    assert hub_sess.sync_changed_at == hub_stamp            # the record did not change


def test_a_file_this_computer_lost_is_fetched_by_record_and_slot(harness):
    uid = harness["local_user"].id
    _download_one_photo(harness)
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    local_path = os.path.join(harness["instance_path"], "uploads", "default",
                              landed.session_image_file)
    os.remove(local_path)
    rev_before = _local_query(harness, lambda db: counter_rev(db, uid))

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert _file_digest(local_path) == _sha256_of_bytes(BIG)
    assert _local_query(harness, lambda db: counter_rev(db, uid)) == rev_before


def test_a_changed_row_that_lost_its_bytes_is_reported_not_filled(harness):
    """This computer edited the session since the last sync, and its photo is
    gone from disk. The hub still has its (older) copy, but the row changed
    here, so the healing pass must not fetch it: the file is reported as lost
    and the slot is left as it is."""
    uid = harness["local_user"].id
    _download_one_photo(harness)
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    local_path = os.path.join(harness["instance_path"], "uploads", "default",
                              landed.session_image_file)
    landed.notes = "edited here after the download"
    harness["local_db"].commit()
    os.remove(local_path)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 1             # reported
    assert not os.path.exists(local_path)                    # never filled
    hub_file = os.path.join(harness["instance_path"], "uploads", "hubuser", "hub.jpg")
    assert _file_digest(hub_file) == _sha256_of_bytes(BIG)   # the hub's copy is untouched
    assert _hub_query(harness, lambda db: db.query(JournalSession).filter_by(
        user_id=999, external_id="hub-sess").one()).notes == "edited here after the download"

    # The next sync finds the row unchanged since the last one: now the hub's
    # file IS the current one, and it is fetched.
    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert _file_digest(local_path) == _sha256_of_bytes(BIG)


def test_a_file_the_hub_lost_is_not_repaired_from_a_row_changed_here(harness, monkeypatch):
    """/repair is only for a row that is the same record on both sides. A row
    this computer changed since the last sync sends its file with the record,
    through the merge; the healing pass does not upload it."""
    uid = harness["local_user"].id
    _download_one_photo(harness)
    os.remove(os.path.join(harness["instance_path"], "uploads", "hubuser", "hub.jpg"))
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    landed.notes = "edited here"
    harness["local_db"].commit()
    repairs = []
    orig = _TestSyncClient.repair_chunk

    def spy(self, *args, **kwargs):
        repairs.append(args)
        return orig(self, *args, **kwargs)
    monkeypatch.setattr(_TestSyncClient, "repair_chunk", spy)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert repairs == []
    # The push carried the record and its file: the hub has it again, as a
    # record change with a rev.
    assert _sha256_of_bytes(BIG) in _hub_photo_digests(harness)


def test_a_file_gone_on_both_sides_is_reported_and_holds_no_watermark(harness):
    uid = harness["local_user"].id
    _download_one_photo(harness)
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    os.remove(os.path.join(harness["instance_path"], "uploads", "default",
                           landed.session_image_file))
    os.remove(os.path.join(harness["instance_path"], "uploads", "hubuser", "hub.jpg"))
    # A real change as well, so this is a full two-phase sync, not an empty one.
    _hub(harness, _obj(999, "M 77"))
    before = _local_query(harness, lambda db: db.query(SyncState)
                          .filter_by(user_id=uid).one().server_cursor)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    # One file, missing on both sides, is one file — not one per side.
    assert st["result"]["unrestored_files"] == 1
    sync_state = _local_query(harness, lambda db: db.query(SyncState).filter_by(user_id=uid).one())
    assert sync_state.server_cursor > before                 # the cursor moved on
    assert sync_state.last_sync_at is not None
    assert _local_query(harness, lambda db: db.query(AstroObject)
                        .filter_by(user_id=uid, object_name="M 77").count()) == 1


# =============================================================================
# 22. Mass-delete approvals are separate
# =============================================================================

def _spy_brakes(monkeypatch):
    """Record the allow_mass_delete each side's plan was built with in phase 2."""
    seen = {"push": [], "pull": []}
    orig_push = _TestSyncClient.push_plan

    def push_plan(self, payload, clock_offset_seconds, allow_mass_delete=False):
        seen["push"].append(bool(allow_mass_delete))
        return orig_push(self, payload, clock_offset_seconds,
                         allow_mass_delete=allow_mass_delete)
    monkeypatch.setattr(_TestSyncClient, "push_plan", push_plan)

    import nova.sync_job as job
    orig_plan = job.plan_merge

    def plan_merge(db, user, payload, **kwargs):
        seen["pull"].append(bool(kwargs.get("allow_mass_delete")))
        return orig_plan(db, user, payload, **kwargs)
    monkeypatch.setattr(job, "plan_merge", plan_merge)
    return seen


def test_a_pull_side_approval_never_opens_the_push_brake(harness, monkeypatch):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 40)
    _hub_delete_all_objects(harness)                        # the pull is the mass delete
    _local_query(harness, lambda db: db.add(_obj(uid, "LOCAL-NEW")))   # and a push exists
    harness["local_db"].commit()

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["pull_mass_delete"] is True
    assert st["summary"]["push_mass_delete"] is False

    seen = _spy_brakes(monkeypatch)
    confirm_job(harness["app"], uid, allow_mass_delete=True)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    assert seen["push"] and not any(seen["push"])           # the hub never saw it
    assert seen["pull"] == [True]
    job = load_job(uid)
    assert job["allow_pull_mass_delete"] is True
    assert job["allow_push_mass_delete"] is False


def test_a_push_side_approval_never_opens_the_pull_brake(harness, monkeypatch):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 40)

    def delete_local(db):
        for o in db.query(AstroObject).filter_by(user_id=uid).all():
            db.delete(o)
        db.commit()
    _local_query(harness, delete_local)                     # the push is the mass delete
    _hub(harness, _obj(999, "HUB-NEW"))                     # and a pull exists

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["push_mass_delete"] is True
    assert st["summary"]["pull_mass_delete"] is False

    seen = _spy_brakes(monkeypatch)
    confirm_job(harness["app"], uid, allow_mass_delete=True)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    assert seen["push"] == [True]
    assert seen["pull"] == [False]                          # the local plan never saw it


def test_approving_the_wrong_side_is_not_an_approval(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 40)
    _hub_delete_all_objects(harness)

    st = _run_phase1(harness, "sync")
    assert st["summary"]["pull_mass_delete"] is True

    with pytest.raises(SyncRefused) as exc:
        confirm_job(harness["app"], uid, allow_push_mass_delete=True,
                    allow_pull_mass_delete=False)
    assert exc.value.refusal.code == "mass_delete"
    assert job_status(uid)["phase"] == "awaiting_confirmation"
    assert _local_query(harness, lambda db: db.query(AstroObject)
                        .filter_by(user_id=uid).count()) == 40

    confirm_job(harness["app"], uid, allow_pull_mass_delete=True)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st


# =============================================================================
# 23. Job liveness
# =============================================================================

def test_a_long_step_never_makes_a_live_job_read_interrupted(harness, monkeypatch):
    """The backup outlasts the stale threshold several times over. The worker's
    own ticker keeps the heartbeat fresh, so no poll ever reports the job dead."""
    import nova.sync_job as job
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _obj(999, "M 1"))
    monkeypatch.setattr(job, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(job, "HEARTBEAT_STALE_SECONDS", 0.4)
    real_backup = job.backup_local

    def slow_backup(db, user):
        time.sleep(1.5)
        return real_backup(db, user)
    monkeypatch.setattr(job, "backup_local", slow_backup)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)

    seen = []
    deadline = time.time() + 20
    while time.time() < deadline:
        status = job_status(uid)
        seen.append((status.get("phase"), (status.get("error") or {}).get("code")))
        if status.get("phase") in ("done", "failed"):
            break
        time.sleep(0.05)

    assert seen[-1] == ("done", None), seen[-5:]
    assert ("failed", "interrupted") not in seen
    assert sum(1 for phase, _ in seen if phase == "backup") > 10     # polled all through it


def test_a_hung_worker_stops_its_heartbeat_and_reads_interrupted(harness, monkeypatch):
    """The worker is alive but stuck: it reaches no step and moves no byte. The
    ticker vouches only for a worker that makes progress, so after the limit it
    stops, the heartbeat goes stale, and the job reads interrupted - a new Sync
    now is possible without restarting the process."""
    import threading
    import nova.sync_job as job
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _obj(999, "M 1"))
    monkeypatch.setattr(job, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(job, "HEARTBEAT_STALE_SECONDS", 0.4)
    monkeypatch.setattr(job, "HEARTBEAT_PROGRESS_LIMIT_SECONDS", 0.5)
    release = threading.Event()
    real_backup = job.backup_local

    def hung_backup(db, user):
        release.wait(timeout=15)                 # no _touch, no bytes
        return real_backup(db, user)
    monkeypatch.setattr(job, "backup_local", hung_backup)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    try:
        # Alive at first (the ticker beats through the stale threshold)...
        time.sleep(0.45)
        assert job_status(uid).get("running") is True
        # ...and interrupted once it has made no progress for the limit.
        st = _wait(uid, lambda s: s.get("phase") == "failed", timeout=10)
        assert st["error"]["code"] == "interrupted", st
    finally:
        release.set()
    _wait(uid, lambda s: s.get("phase") in ("done", "failed"))


def test_progress_keeps_a_slow_worker_alive_past_the_limit(harness, monkeypatch):
    """Slow is not hung: a step that keeps moving bytes (here, noting progress)
    outlasts the limit several times and never reads interrupted."""
    import nova.sync_job as job
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _obj(999, "M 1"))
    monkeypatch.setattr(job, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(job, "HEARTBEAT_STALE_SECONDS", 0.4)
    monkeypatch.setattr(job, "HEARTBEAT_PROGRESS_LIMIT_SECONDS", 0.3)
    real_backup = job.backup_local

    def slow_backup(db, user):
        token = job.load_job(user.id)["start_token"]
        for _ in range(15):
            time.sleep(0.1)
            job._note_progress(user.id, token)
        return real_backup(db, user)
    monkeypatch.setattr(job, "backup_local", slow_backup)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    seen = []
    deadline = time.time() + 20
    while time.time() < deadline:
        status = job_status(uid)
        seen.append((status.get("phase"), (status.get("error") or {}).get("code")))
        if status.get("phase") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert seen[-1] == ("done", None), seen[-5:]
    assert ("failed", "interrupted") not in seen


def test_a_job_whose_worker_really_died_still_reads_interrupted(harness, monkeypatch):
    """The ticker lives in the worker's process. With no worker there is no
    beat, and the stale heartbeat still means the job is dead."""
    import nova.sync_job as job
    uid = harness["local_user"].id
    monkeypatch.setattr(job, "HEARTBEAT_STALE_SECONDS", 0.2)
    dead = job._new_job(uid, "sync")
    dead["phase"] = "applying"
    save_job(uid, dead)
    time.sleep(0.4)
    status = job_status(uid)
    assert status["phase"] == "failed" and status["error"]["code"] == "interrupted"


def test_a_second_confirm_is_refused_and_never_supersedes_the_worker(harness, monkeypatch):
    import nova.sync_job as job
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _obj(999, "M 1"))
    real_backup = job.backup_local

    def slow_backup(db, user):
        time.sleep(0.6)                                      # the worker is alive and busy
        return real_backup(db, user)
    monkeypatch.setattr(job, "backup_local", slow_backup)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid)
    token = load_job(uid)["start_token"]
    assert load_job(uid)["phase"] != "awaiting_confirmation"   # confirmed at once

    for _ in range(3):                                       # a double click, a re-POST
        with pytest.raises(SyncRefused) as exc:
            confirm_job(harness["app"], uid)
        assert exc.value.refusal.code == "sync_running"
        assert load_job(uid)["start_token"] == token         # the worker keeps its job

    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert _local_query(harness, lambda db: db.query(AstroObject)
                        .filter_by(user_id=uid, object_name="M 1").count()) == 1


def test_two_confirms_at_once_start_one_worker(harness, monkeypatch):
    import threading
    import nova.sync_job as job
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _hub(harness, _obj(999, "M 1"))
    started = []
    real_phase2 = job._run_phase2

    def counting(*args, **kwargs):
        started.append(args[3])
        return real_phase2(*args, **kwargs)
    monkeypatch.setattr(job, "_run_phase2", counting)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st

    outcomes = []

    def confirm():
        try:
            confirm_job(harness["app"], uid)
            outcomes.append("started")
        except SyncRefused as exc:
            outcomes.append(exc.refusal.code)
    threads = [threading.Thread(target=confirm) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert sorted(outcomes) == ["started"] + ["sync_running"] * 5
    assert len(started) == 1


# =============================================================================
# 24. The summary names records changed on both sides
# =============================================================================

def _rename_common(harness, names, *, hub_wins):
    """Edit the same objects on both sides; the later stamp decides who wins."""
    from datetime import timedelta
    uid = harness["local_user"].id
    now = utc_now()

    def edit_hub(db):
        for name in names:
            row = db.query(AstroObject).filter_by(user_id=999, object_name=name).one()
            row.common_name = f"{name} (server)"
            db.commit()
            _stamp(db, AstroObject, row.id,
                   now + timedelta(minutes=10 if hub_wins else 1))
    _hub_query(harness, edit_hub)

    db = harness["local_db"]
    for name in names:
        row = db.query(AstroObject).filter_by(user_id=uid, object_name=name).one()
        row.common_name = f"{name} (this computer)"
        db.commit()
        _stamp(db, AstroObject, row.id, now + timedelta(minutes=1 if hub_wins else 10))


def test_the_summary_lists_records_changed_on_both_sides(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 8)
    _rename_common(harness, ["OBJ0", "OBJ1"], hub_wins=True)
    _rename_common(harness, ["OBJ2"], hub_wins=False)
    # Changed on one side only: not a conflict.
    _hub_query(harness, lambda db: (
        setattr(db.query(AstroObject).filter_by(user_id=999, object_name="OBJ3").one(),
                "common_name", "only the server"), db.commit()))

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    summary = st["summary"]

    assert sorted((c["name"], c["kept"]) for c in summary["conflicts"]) == [
        ("OBJ0", "server"), ("OBJ1", "server"), ("OBJ2", "local")]
    assert summary["conflicts_more"] == 0

    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    # The line told the truth about which copy stays, on both sides.
    for name, expected in (("OBJ0", "OBJ0 (server)"), ("OBJ2", "OBJ2 (this computer)")):
        assert _local_query(harness, lambda db: db.query(AstroObject).filter_by(
            user_id=uid, object_name=name).one().common_name) == expected
        assert _hub_query(harness, lambda db: db.query(AstroObject).filter_by(
            user_id=999, object_name=name).one().common_name) == expected


@pytest.mark.parametrize("local_ahead,kept", [(-15, "server"), (15, "local")])
def test_the_conflict_line_is_right_for_a_record_that_differs_only_in_its_file(
        harness, local_ahead, kept):
    """Both sides replaced the same session's photo, fifteen seconds apart.
    There is no window in which that is "one write": the newer stamp wins, the
    line says which, and both sides end up holding that photo."""
    from nova.sync_tracking import mark_file_changed
    uid = harness["local_user"].id
    _download_one_photo(harness)
    base = utc_now().replace(microsecond=0) + timedelta(minutes=5)
    hub_photo, local_photo = BIG + b"-hub-version", BIG + b"-local-version"

    hdb = harness["hub_session"]()
    hub_row = hdb.query(JournalSession).filter_by(user_id=999, external_id="hub-sess").one()
    _write_upload(harness["instance_path"], "hubuser", hub_row.session_image_file, hub_photo)
    mark_file_changed(hub_row, "session_image_file")
    hdb.commit()
    _stamp(hdb, JournalSession, hub_row.id, base)
    harness["hub_session"].remove()

    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    _write_upload(harness["instance_path"], "default", row.session_image_file, local_photo)
    mark_file_changed(row, "session_image_file")
    harness["local_db"].commit()
    _stamp(harness["local_db"], JournalSession, row.id, base + timedelta(seconds=local_ahead))

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert [c["kept"] for c in st["summary"]["conflicts"]] == [kept]
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st

    winner = _sha256_of_bytes(hub_photo if kept == "server" else local_photo)
    hub_name = _hub_query(harness, lambda db: db.query(JournalSession).filter_by(
        user_id=999, external_id="hub-sess").one().session_image_file)
    local_name = _local_query(harness, lambda db: db.query(JournalSession).filter_by(
        user_id=uid, external_id="hub-sess").one().session_image_file)
    uploads = os.path.join(harness["instance_path"], "uploads")
    assert _file_digest(os.path.join(uploads, "hubuser", hub_name)) == winner
    assert _file_digest(os.path.join(uploads, "default", local_name)) == winner


def test_the_summary_names_five_conflicts_and_counts_the_rest(harness):
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 8)
    _rename_common(harness, [f"OBJ{i}" for i in range(7)], hub_wins=True)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert len(st["summary"]["conflicts"]) == 5
    assert st["summary"]["conflicts_more"] == 2


def test_the_same_edit_on_both_sides_is_not_a_conflict(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _sync_shared_objects(harness, 2)
    _hub_query(harness, lambda db: (
        setattr(db.query(AstroObject).filter_by(user_id=999, object_name="OBJ0").one(),
                "common_name", "Crab"), db.commit()))
    row = harness["local_db"].query(AstroObject).filter_by(
        user_id=uid, object_name="OBJ0").one()
    row.common_name = "Crab"
    harness["local_db"].commit()

    start_job(harness["app"], uid, "sync")
    st = _wait(uid, lambda s: s.get("phase") in ("awaiting_confirmation", "done", "failed"))
    assert st["phase"] in ("awaiting_confirmation", "done"), st
    assert (st.get("summary") or {}).get("conflicts") in ([], None)


# =============================================================================
# 25. A project id another account owns on the hub
# =============================================================================

def test_a_project_id_taken_on_the_hub_is_named_and_the_rest_syncs(harness):
    uid = harness["local_user"].id
    _mark_downloaded(harness)

    def other_account(db):
        other = DbUser(id=500, username="someone-else")
        db.add(other)
        db.commit()
        db.add(Project(id="shared-id", user_id=500, name="Theirs", status="In Progress"))
        db.commit()
    _hub_query(harness, other_account)

    db = harness["local_db"]
    db.add(Project(id="shared-id", user_id=uid, name="Veil mosaic", status="In Progress"))
    db.add(_obj(uid, "M 42"))
    db.add(JournalSession(user_id=uid, external_id="local-sess",
                          date_utc=date(2026, 1, 2), project_id="shared-id"))
    db.commit()

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["blocked"] == [{
        "table": "projects", "key": "shared-id", "name": "Veil mosaic",
        "reason": "project_id_taken", "side": "server"}]

    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))
    assert st["phase"] == "done", st
    assert [b["name"] for b in st["result"]["blocked"]] == ["Veil mosaic"]

    # The other account's project is untouched; everything else arrived.
    theirs = _hub_query(harness, lambda db: db.query(Project).filter_by(id="shared-id").one())
    assert (theirs.user_id, theirs.name) == (500, "Theirs")
    assert _hub_query(harness, lambda db: db.query(AstroObject)
                      .filter_by(user_id=999, object_name="M 42").count()) == 1
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="local-sess").one())
    assert hub_sess.project_id is None
    # A blocked record does not hold the watermark: the next sync is a quiet one.
    assert _local_query(harness, lambda db: db.query(SyncState)
                        .filter_by(user_id=uid).one()).local_pushed_rev > 0
    st = _sync_to_done(harness)
    assert st["phase"] == "done" and st["result"].get("empty") is True, st

    from nova.blueprints.sync_tokens import blocked_message
    with harness["app"].test_request_context("/sync"):
        message = blocked_message(st_blocked := {"name": "Veil mosaic", "side": "server"})
    assert "Veil mosaic" in message and "another account on the server" in message


# =============================================================================
# Placements finish only after their commit; a newer row always wins
# =============================================================================

class _CommitFailsOnce:
    """The hub's session, with its next commit failing once `armed` is set: the
    transaction is rolled back and the commit raises, as a full disk or a lock
    timeout would."""

    def __init__(self, inner, armed):
        self._inner = inner
        self._armed = armed

    def commit(self):
        if self._armed["on"]:
            self._armed["on"] = False
            self._inner.rollback()
            raise RuntimeError("simulated commit failure after the placement")
        return self._inner.commit()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_commit_that_fails_after_the_push_placement_loses_nothing(harness, monkeypatch):
    """H1. push_apply places the pushed photo, and the commit that would make
    its column durable fails. The owed entry and the staged bytes are only
    dropped after a commit that succeeded, so both are still there, and the
    next sync request settles the placement."""
    import nova.blueprints.sync_api as api
    import nova.sync_job as job
    uid = harness["local_user"].id
    digest = _sha256_of_bytes(BIG)
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)

    armed = {"on": False, "placed": 0}
    real_commit_staged = api.commit_staged_files

    def placing(*args, **kwargs):
        report = real_commit_staged(*args, **kwargs)
        armed["placed"] += len(report.placed)
        armed["on"] = bool(report.placed)       # the very next commit fails
        return report

    hub_session = harness["hub_session"]
    with monkeypatch.context() as patch:
        patch.setattr(api, "commit_staged_files", placing)
        patch.setattr(api, "get_db", lambda: _CommitFailsOnce(hub_session(), armed))
        patch.setattr(job, "_retry_transient", lambda fn, attempts=1: fn())
        st = _sync_to_done(harness)

    assert armed["placed"] == 1 and armed["on"] is False   # placed, then the commit failed
    assert st["phase"] == "failed", st
    assert [e["sha256"] for e in _hub_owed(harness)] == [digest]
    assert any(name.startswith(digest) for name in os.listdir(_hub_staging(harness)))
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="local-sess").one())
    assert not hub_sess.session_image_file                  # the column write was lost

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    hub_sess = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="local-sess").one())
    hub_file = os.path.join(harness["instance_path"], "uploads", "hubuser",
                            hub_sess.session_image_file)
    assert _file_digest(hub_file) == digest
    assert _hub_owed(harness) == []
    assert not any(name.startswith(digest) for name in os.listdir(_hub_staging(harness)))


def test_a_browser_upload_during_the_local_heal_survives(harness, monkeypatch):
    """M1, local side. This computer lost a photo and the healing pass fetches
    the hub's copy. While it is being fetched the user uploads a new photo in
    the browser - same live name, a new rev. The heal re-checks the row before
    placing and its column write is conditional, so the new photo stays and
    the slot is not counted as lost."""
    from sqlalchemy.orm import object_session
    from nova.sync_tracking import mark_file_changed
    import nova.sync_job as job

    uid = harness["local_user"].id
    _download_one_photo(harness)
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    local_path = os.path.join(harness["instance_path"], "uploads", "default",
                              landed.session_image_file)
    os.remove(local_path)
    rev_before = _local_query(harness, lambda db: counter_rev(db, uid))
    new_photo = PNG + b"-uploaded-in-the-browser"
    real_stage = job._stage_from_hub
    fired = {"n": 0}

    def upload_while_fetching(client, user, table, key, slot, sha256="", ext_hint=""):
        if not fired["n"]:
            fired["n"] += 1
            db = object_session(user)
            row = db.query(JournalSession).filter_by(
                user_id=user.id, external_id="hub-sess").one()
            with open(local_path, "wb") as handle:        # journal_edit's write
                handle.write(new_photo)
            row.session_image_file = os.path.basename(local_path)
            mark_file_changed(row, "session_image_file")
            db.commit()
        return real_stage(client, user, table, key, slot, sha256, ext_hint)
    monkeypatch.setattr(job, "_stage_from_hub", upload_while_fetching)

    st = _sync_to_done(harness)

    assert fired["n"] == 1
    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert _file_digest(local_path) == _sha256_of_bytes(new_photo)
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    assert landed.sync_rev > rev_before                    # the upload's own rev
    assert not [n for n in os.listdir(os.path.dirname(local_path)) if n.endswith(".part")]


def test_a_file_the_hub_lost_reaches_a_computer_that_held_the_record(harness):
    """M2 end to end: the hub loses X, this computer (C) pulls, another
    computer (P) repairs the hub, and C receives X.

    P replaced the photo; its record reached the hub and the bytes did not
    stay. C holds the old photo under that record. The hub exports the slot as
    "missing", so C holds the record pending - it neither takes it with its
    old bytes nor offers those bytes to the hub as a repair. P's repair gives
    the row a new rev; C's next pull names X and C places it."""
    from nova.sync_tracking import mark_file_changed

    uid = harness["local_user"].id
    old_photo, new_photo = BIG, PNG + b"-p-replaced-the-photo"
    _download_one_photo(harness, data=old_photo)
    instance = harness["instance_path"]
    hub_file = os.path.join(instance, "uploads", "hubuser", "hub.jpg")

    # P's photo-only change lands on the hub as a record change...
    _write_upload(instance, "hubuser", "hub.jpg", new_photo)

    def p_push(db):
        row = db.query(JournalSession).filter_by(user_id=999, external_id="hub-sess").one()
        mark_file_changed(row, "session_image_file")
        db.commit()
        # P received the record through a sync before changing it, so its
        # write is always well over a second after the one C holds.
        _stamp(db, JournalSession, row.id, row.sync_changed_at + timedelta(seconds=30))
    _hub_query(harness, p_push)
    os.remove(hub_file)                                     # ...and the hub loses X

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert _hub_photo_digests(harness) == set()             # C did not "repair" with W
    assert st["result"]["held_files"] == [
        {"table": "journal_sessions", "key": "hub-sess", "name": "2026-01-02"}]
    assert _local_photo_digests(harness) == {_sha256_of_bytes(old_photo)}
    held_cursor = _local_query(harness, lambda db: db.query(SyncState)
                               .filter_by(user_id=uid).one()).server_cursor
    assert held_cursor < _hub_query(harness, lambda db: counter_rev(db, 999))

    # P repairs the hub with X.
    hub_rev = _hub_query(harness, lambda db: counter_rev(db, 999))
    p_client = _TestSyncClient(HUB_URL, harness["token"],
                               session=_FakeSession(harness["hub_app"]))
    answer = p_client.repair_chunk("journal_sessions", "hub-sess", "session_image_file",
                                   _sha256_of_bytes(new_photo), "jpg", len(new_photo),
                                   0, new_photo)
    assert answer["complete"] and answer["placed"], answer
    assert _hub_query(harness, lambda db: counter_rev(db, 999)) == hub_rev + 1

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert _sha256_of_bytes(new_photo) in _local_photo_digests(harness)
    landed = _local_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=uid, external_id="hub-sess").one())
    local_file = os.path.join(instance, "uploads", "default", landed.session_image_file)
    assert _file_digest(local_file) == _sha256_of_bytes(new_photo)
    assert _local_query(harness, lambda db: db.query(SyncState)
                        .filter_by(user_id=uid).one()).server_cursor \
        == _hub_query(harness, lambda db: counter_rev(db, 999))


def test_one_computer_gives_the_hub_back_a_photo_it_lost(harness, monkeypatch):
    """The single-computer case. The hub re-offers a record it already gave
    this computer (its stamp kept, a new rev), and its photo is gone: the slot
    travels as "missing". This computer's row is unchanged since the last
    sync, its content equal, and the stamps agree although the offset was
    re-measured a few ms differently: the same write. So the record is not
    held, the healing pass uploads the photo through /repair, and the next
    sync finds nothing left to do."""
    from nova.sync_tracking import next_rev

    uid = harness["local_user"].id
    _script_offsets(monkeypatch, 0.0, 0.004, -0.003)
    _download_one_photo(harness)
    hub_file = os.path.join(harness["instance_path"], "uploads", "hubuser", "hub.jpg")
    os.remove(hub_file)

    def reoffer(db):                    # a new rev, the stamp untouched
        conn = db.connection()
        rev = next_rev(conn, 999)
        conn.execute(text("UPDATE journal_sessions SET sync_rev = :r "
                          "WHERE user_id = 999 AND external_id = 'hub-sess'"), {"r": rev})
        db.commit()
    _hub_query(harness, reoffer)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert st["result"]["held_files"] == []
    assert _hub_photo_digests(harness) == {_sha256_of_bytes(BIG)}   # repaired
    hub_name = _hub_query(harness, lambda db: db.query(JournalSession)
                          .filter_by(user_id=999, external_id="hub-sess").one()
                          .session_image_file)
    assert _file_digest(os.path.join(os.path.dirname(hub_file), hub_name)) \
        == _sha256_of_bytes(BIG)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0
    assert not st["result"].get("held_files")
    assert _local_photo_digests(harness) == {_sha256_of_bytes(BIG)}
    assert _local_query(harness, lambda db: db.query(SyncState)
                        .filter_by(user_id=uid).one()).server_cursor \
        == _hub_query(harness, lambda db: counter_rev(db, 999))
