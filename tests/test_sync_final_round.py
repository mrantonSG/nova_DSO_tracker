"""Final fix round for the audit of 5e9223c..HEAD.

M1  a browser upload reaches its live name only after its commit
M2  placement guards are taken before the window they protect
M3  the way out of a record held because the hub lost its file
L1  /repair checks the sender's stamp
L3  counting edge cases
L4  the start-of-sync sweep of left-over files
plus a commit that fails after a placement (settle, repair, pull, download)
and skip-identical placements.

The race tests are deterministic interleavings: a hook runs the other side's
step at exactly the moment the race needs. Symbols new in this round are
imported inside the tests that need them.
"""
import hashlib
import io
import os
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import nova
from nova.models import DbUser, JournalSession, Project, SyncState
from nova.sync_engine import (
    FileRef, MergeResult, ROLE_SESSION_PHOTO, commit_staged_files, counter_rev,
    finish_placements, placement_guard, stage_file, to_iso,
)
from nova.sync_tracking import mark_file_changed

from test_sync_local_e2e import (  # noqa: F401  (harness is a fixture)
    BIG, HUB_URL, PNG, _CommitFailsOnce, _FakeSession, _TestSyncClient,
    _download_one_photo, _file_digest, _hub, _hub_dies_before_placing,
    _hub_owed, _hub_photo_digests, _hub_query, _hub_staging, _local_photo_digests,
    _local_query, _mark_downloaded, _obj, _patch_sync_dirs, _run_phase1,
    _session, _sha256_of_bytes, _stamp, _sync_to_done, _wait, _write_upload,
    harness,
)
from nova.sync_job import confirm_job, load_job, save_job

REPAIR = "/api/sync/v1/repair"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _upload_temps(directory) -> list:
    return [n for n in os.listdir(directory) if ".upload-" in n]


def _write_atomic(path, data):
    from nova.helpers import write_file_atomic
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_file_atomic(path, lambda p: open(p, "wb").write(data))


def _hub_browser_upload(harness, external_id, data, ext="png"):
    """A browser upload on the hub, start to finish: bytes, column, stamp,
    commit. Runs on the session the current hub request is using."""
    db = harness["hub_session"]()
    row = db.query(JournalSession).filter_by(user_id=999, external_id=external_id).one()
    name = f"{row.id}.{ext}"
    _write_atomic(os.path.join(harness["instance_path"], "uploads", "hubuser", name), data)
    row.session_image_file = name
    mark_file_changed(row, "session_image_file")
    db.commit()


# =============================================================================
# M1 - a browser upload and a sync placement for the same row
# =============================================================================

@pytest.fixture
def edit_world(su_client_logged_in, db_session, monkeypatch, tmp_path):
    """A session with a photo on disk, the journal routes and the engine both
    pointed at the same per-test uploads directory."""
    instance = _patch_sync_dirs(monkeypatch, tmp_path)
    uploads = os.path.join(str(instance), "uploads")
    monkeypatch.setattr("nova.blueprints.journal.UPLOAD_FOLDER", uploads)
    user = db_session.query(DbUser).filter_by(username="default").one()
    row = JournalSession(user_id=user.id, external_id="ext-1", date_utc=date(2026, 1, 10))
    db_session.add(row)
    db_session.commit()
    name = f"{row.id}.png"
    row.session_image_file = name
    db_session.commit()
    live = os.path.join(uploads, "default", name)
    old = PNG + b"-old"
    _write_atomic(live, old)
    return SimpleNamespace(client=su_client_logged_in, db=db_session, user=user,
                           row_id=row.id, live=live, old=old)


def _post_photo(w, data):
    return w.client.post(f"/journal/edit/{w.row_id}",
                         data={"form_action": "save_draft",
                               "session_image": (io.BytesIO(data), "new.png")},
                         content_type="multipart/form-data")


def test_m1_a_sync_placement_racing_a_browser_upload_never_wins(edit_world, monkeypatch):
    """The browser has written its new photo and not yet committed when a sync
    placement for the same row (decided on the row as it was) runs to the end.
    The browser then commits. The live file must be the browser's bytes - the
    committed row is the browser's."""
    import nova.blueprints.journal as journal
    w = edit_world
    synced, browser = PNG + b"-from-the-hub", PNG + b"-from-the-browser"
    real_mark = journal.mark_file_changed
    placed = []

    def sync_places_now():
        other = Session(bind=w.db.get_bind())
        user = other.get(DbUser, w.user.id)
        ref = FileRef(role=ROLE_SESSION_PHOTO, table="journal_sessions", key="ext-1",
                      slot="session_image_file", sha256=_digest(synced),
                      size=len(synced), ext="png")
        staged = stage_file(user, ref, io.BytesIO(synced))
        guard = placement_guard(other, user, [ref])
        report = commit_staged_files(other, MergeResult(files_expected=[ref]), [staged],
                                     user=user, guard=guard)
        other.commit()
        finish_placements(report, user=user)
        placed.extend(report.placed)

    def racing(row, column):
        if column == "session_image_file" and not placed:
            sync_places_now()                 # the browser wrote, has not committed
        return real_mark(row, column)
    monkeypatch.setattr(journal, "mark_file_changed", racing)

    resp = _post_photo(w, browser)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert placed, "the sync placement did not run inside the race window"
    w.db.expire_all()
    row = w.db.get(JournalSession, w.row_id)
    assert os.path.join(os.path.dirname(w.live), row.session_image_file) == w.live
    assert _file_digest(w.live) == _digest(browser)
    assert _upload_temps(os.path.dirname(w.live)) == []


def test_m1_a_browser_commit_that_fails_leaves_no_temp_and_the_live_file_alone(
        edit_world, monkeypatch):
    w = edit_world
    real_commit = w.db.commit

    def failing_commit():
        if any(isinstance(o, JournalSession) for o in w.db.dirty):
            raise RuntimeError("simulated commit failure")
        return real_commit()
    monkeypatch.setattr(w.db, "commit", failing_commit)

    resp = _post_photo(w, PNG + b"-never-committed")

    assert resp.status_code == 500
    assert _file_digest(w.live) == _digest(w.old)
    assert _upload_temps(os.path.dirname(w.live)) == []


# =============================================================================
# M2 - guards taken before the window
# =============================================================================

def test_m2_a_browser_upload_during_the_settle_hashing_wins(harness, monkeypatch):
    """The hub owes a photo for a row. While the settle hashes the staged
    copies, a browser upload on the hub replaces the row's photo and commits.
    The owed (older) file must not land: the browser's photo stays the row's."""
    import nova.sync_engine as engine
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)
    st = _hub_dies_before_placing(harness, monkeypatch)
    assert st["phase"] == "failed", st
    assert [e["sha256"] for e in _hub_owed(harness)] == [_sha256_of_bytes(BIG)]

    browser = PNG + b"-browser-on-the-hub"
    real_index = engine.staged_index
    fired = []

    def hashing(user, *, verify=False, instance_path=None):
        found = real_index(user, verify=verify, instance_path=instance_path)
        if verify and getattr(user, "id", None) == 999 and not fired:
            fired.append(1)
            _hub_browser_upload(harness, "local-sess", browser)
        return found
    monkeypatch.setattr(engine, "staged_index", hashing)

    _sync_to_done(harness)

    assert fired
    row = _hub_query(harness, lambda db: db.query(JournalSession)
                     .filter_by(user_id=999, external_id="local-sess").one())
    live = os.path.join(harness["instance_path"], "uploads", "hubuser", row.session_image_file)
    assert _file_digest(live) == _sha256_of_bytes(browser)
    assert _hub_owed(harness) == []


def test_m2_a_browser_upload_between_the_download_apply_and_its_placement_wins(
        harness, monkeypatch):
    """A download commits its records; before its files are placed the user
    uploads a photo for one of them. The download's older file must not land."""
    import nova.sync_job as job
    uid = harness["local_user"].id
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", BIG)
    browser = PNG + b"-browser-during-the-download"
    real_move = job.move_replaced_files
    fired = []

    def browser_then_move(db, plan, user, **kwargs):
        if not fired:
            fired.append(1)
            row = db.query(JournalSession).filter_by(user_id=uid, external_id="hub-sess").one()
            name = f"{row.id}.jpg"                     # the very name the download places
            _write_atomic(os.path.join(harness["instance_path"], "uploads", "default", name),
                          browser)
            row.session_image_file = name
            mark_file_changed(row, "session_image_file")
            db.commit()
        return real_move(db, plan, user, **kwargs)
    monkeypatch.setattr(job, "move_replaced_files", browser_then_move)

    st = _run_phase1(harness, "download")
    assert st["phase"] == "awaiting_confirmation", st
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))

    assert fired and st["phase"] == "done", st
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    live = os.path.join(harness["instance_path"], "uploads", "default", row.session_image_file)
    assert _file_digest(live) == _sha256_of_bytes(browser)


# =============================================================================
# M3 - a record held because the hub lost a file nobody else has
# =============================================================================

def _held_record(harness, hub_shift=timedelta(seconds=-30)):
    """The hub loses the file of a record another computer changed; this
    computer holds the record (it has bytes there it cannot vouch for).
    `hub_shift` moves that computer's stamp: by default an older write, so a
    choice made here is the newer change."""
    uid = harness["local_user"].id
    old_photo = BIG
    _download_one_photo(harness, data=old_photo)
    instance = harness["instance_path"]
    _write_upload(instance, "hubuser", "hub.jpg", PNG + b"-p-replaced-the-photo")

    def p_push(db):
        row = db.query(JournalSession).filter_by(user_id=999, external_id="hub-sess").one()
        mark_file_changed(row, "session_image_file")
        db.commit()
        # Another write than the one this computer holds (well over the 1 s
        # same-write tolerance), and older than anything done here from now
        # on, so a choice made here is the newer change.
        _stamp(db, JournalSession, row.id, row.sync_changed_at + hub_shift)
    _hub_query(harness, p_push)
    os.remove(os.path.join(instance, "uploads", "hubuser", "hub.jpg"))

    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    assert [h["key"] for h in st["result"]["held_files"]] == ["hub-sess"]
    return uid, old_photo


def test_m3_using_this_computers_copy_clears_the_hold_on_the_next_sync(harness):
    from nova.sync_job import HELD_USE_LOCAL, held_choices, resolve_held_file
    uid, old_photo = _held_record(harness)
    db, user = harness["local_db"], harness["local_user"]
    assert [c["local_copy"] for c in held_choices(db, user)] == [True]

    assert resolve_held_file(db, user, "journal_sessions", "hub-sess",
                             "session_image_file", HELD_USE_LOCAL) == "done"
    assert held_choices(db, user) == []                    # no longer offered

    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    assert st["result"]["held_files"] == []                # the toast stops
    assert _sha256_of_bytes(old_photo) in _hub_photo_digests(harness)
    cursor = _local_query(harness, lambda db: db.query(SyncState)
                          .filter_by(user_id=uid).one()).server_cursor
    assert cursor == _hub_query(harness, lambda db: counter_rev(db, 999))

    st = _sync_to_done(harness)
    assert st["result"].get("held_files") in ([], None)


def test_m3_a_copy_the_server_does_not_take_is_reported_not_dropped_silently(harness):
    """The hub's record is the newer write, so the push of this computer's
    copy loses under newest-wins. The run says so in plain words, and the
    choice is still offered."""
    from nova.blueprints.sync_tokens import held_file_message
    from nova.sync_job import HELD_USE_LOCAL, held_choices, resolve_held_file
    _held_record(harness, hub_shift=timedelta(hours=1))
    db, user = harness["local_db"], harness["local_user"]
    assert resolve_held_file(db, user, "journal_sessions", "hub-sess",
                             "session_image_file", HELD_USE_LOCAL) == "done"

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    [held] = st["result"]["held_files"]
    assert held["key"] == "hub-sess" and held.get("sent") is True
    with harness["app"].test_request_context():
        message = held_file_message(held)
    assert "copy was not used" in message and "newer version" in message
    assert _hub_photo_digests(harness) == set()            # nothing landed on the hub
    assert [c["sent"] for c in held_choices(db, user)] == [True]


def test_m3_the_page_offers_the_choice_and_the_route_checks_it(
        su_client_logged_in, db_session, monkeypatch, tmp_path):
    """The page offers "use this computer's copy" for a held slot whose file
    is here; the route takes no other choice, and nothing while a sync holds
    the lock."""
    from nova.sync_engine import start_pairing, sync_lock
    _patch_sync_dirs(monkeypatch, tmp_path)
    user = db_session.query(DbUser).filter_by(username="default").one()
    start_pairing(db_session, user, server_url=HUB_URL, token="t", account="hubuser")
    state = db_session.query(SyncState).filter_by(user_id=user.id).one()
    state.last_sync_at = state.paired_at
    row = JournalSession(user_id=user.id, external_id="held-1", date_utc=date(2026, 1, 10))
    db_session.add(row)
    db_session.commit()
    row.session_image_file = f"{row.id}.png"
    db_session.commit()
    _write_atomic(os.path.join(str(tmp_path), "instance", "uploads", "default",
                               row.session_image_file), PNG)
    now = to_iso(nova.sync_job.utc_now())
    save_job(user.id, {"job_id": "x", "start_token": "x", "user_id": user.id,
                       "kind": "sync", "phase": "done", "progress": 1.0,
                       "summary": {"kind": "sync"}, "error": None,
                       "result": {"held_files": [{"table": "journal_sessions",
                                                  "key": "held-1", "name": "held",
                                                  "slots": ["session_image_file"]}]},
                       "toast_pending": False, "started_at": now, "heartbeat_at": now})
    client = su_client_logged_in
    page = client.get("/sync").get_data(as_text=True)
    assert 'data-held-choice="use_local"' in page
    rev = row.sync_rev

    def post(choice):
        return client.post("/sync/held", data={"table": "journal_sessions", "key": "held-1",
                                               "slot": "session_image_file", "choice": choice})

    assert post("bogus").status_code == 400                # one choice only
    with sync_lock(user.id):
        assert post("use_local").status_code == 302        # refused: a sync holds the lock
    db_session.expire_all()
    assert db_session.get(JournalSession, row.id).sync_rev == rev

    assert post("use_local").status_code == 302
    db_session.expire_all()
    assert db_session.get(JournalSession, row.id).sync_rev > rev
    assert load_job(user.id)["result"]["held_files"] == []


# =============================================================================
# L1 - /repair checks the stamp
# =============================================================================

def _hub_row_moved_on(harness):
    """After a download, the hub's row becomes another write (a computer's
    newer record) and its file is missing; this computer's row is unchanged."""
    _download_one_photo(harness)
    instance = harness["instance_path"]

    def newer(db):
        row = db.query(JournalSession).filter_by(user_id=999, external_id="hub-sess").one()
        _stamp(db, JournalSession, row.id, row.sync_changed_at + timedelta(seconds=30))
    _hub_query(harness, newer)
    os.remove(os.path.join(instance, "uploads", "hubuser", "hub.jpg"))


def test_l1_a_repair_with_a_stale_stamp_is_refused_and_the_file_stays_owed(harness):
    from nova.sync_engine import _save_owed_files
    _hub_row_moved_on(harness)
    uid = harness["local_user"].id
    hub_row = _hub_query(harness, lambda db: db.query(JournalSession)
                         .filter_by(user_id=999, external_id="hub-sess").one())
    owed = [{"role": ROLE_SESSION_PHOTO, "table": "journal_sessions", "key": "hub-sess",
             "slot": "session_image_file", "sha256": _digest(b"the newer photo"),
             "ext": "jpg", "size": 15, "name": "", "rev": int(hub_row.sync_rev),
             "stamp": to_iso(hub_row.sync_changed_at)}]
    _save_owed_files(SimpleNamespace(id=999), owed)
    mine = _local_query(harness, lambda db: db.query(JournalSession)
                        .filter_by(user_id=uid, external_id="hub-sess").one())

    client = _TestSyncClient(HUB_URL, harness["token"], session=_FakeSession(harness["hub_app"]))
    headers = client._repair_headers("journal_sessions", "hub-sess", "session_image_file",
                                     _sha256_of_bytes(BIG), "jpg", len(BIG), 0)
    headers["X-Nova-Stamp"] = to_iso(mine.sync_changed_at)
    from nova.sync_engine import SyncRefused
    with pytest.raises(SyncRefused) as refused:
        client._json(REPAIR, None, extra_headers=headers, data=BIG)

    assert refused.value.refusal.code == "stamp_mismatch"
    assert _hub_photo_digests(harness) == set()
    assert _hub_owed(harness) == owed


def test_l1_a_refused_repair_is_skipped_not_counted(harness):
    _hub_row_moved_on(harness)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["result"]["unrestored_files"] == 0           # skipped, tried next sync
    assert _hub_photo_digests(harness) == set()            # the older bytes did not land


# =============================================================================
# L3 - counting
# =============================================================================

def test_l3_the_apply_counts_the_records_its_plan_counted_by_files_alone(harness, monkeypatch):
    """The hub owes a pushed photo and cannot place it at plan time, so the
    re-offered record is counted as changed by its file alone; the apply's
    settle places it. The apply counts from the plan's own list (kept in the
    push session), whatever its re-plan concludes."""
    import flask
    import nova.blueprints.sync_api as api
    import nova.sync_engine as engine
    uid = harness["local_user"].id
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)
    assert _hub_dies_before_placing(harness, monkeypatch)["phase"] == "failed"

    real_place = engine._place_one

    def placed_only_at_apply(*args, **kwargs):
        if flask.has_request_context() and flask.request.path != "/api/sync/v1/push/apply":
            return "pending"                               # the plan-time settle fails
        return real_place(*args, **kwargs)
    monkeypatch.setattr(engine, "_place_one", placed_only_at_apply)

    real_plan = api.plan_merge

    def replanned(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        if flask.request.path == "/api/sync/v1/push/apply":
            plan.file_only = set()                         # the re-plan sees it otherwise
        return plan
    monkeypatch.setattr(api, "plan_merge", replanned)

    st = _run_phase1(harness, "sync")
    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["up"]["updated"] == 1
    confirm_job(harness["app"], uid)
    st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))

    assert st["phase"] == "done", st
    assert st["result"]["up"]["updated"] == st["summary"]["up"]["updated"] == 1
    assert _sha256_of_bytes(BIG) in _hub_photo_digests(harness)   # placed at apply


@pytest.mark.parametrize("fetch,expect", [(True, (1, "2026-01-01 00:00:00.000000")),
                                          (False, None)])
def test_l3_a_heal_target_whose_row_was_deleted_is_not_unrestored(harness, fetch, expect):
    import nova.sync_job as job
    targets = [("journal_sessions", "deleted-meanwhile", "session_image_file",
                "", "jpg", fetch, expect)]
    assert job._heal_local(harness["local_db"], harness["local_user"], None, targets) == set()


def test_l3_the_pre_sync_summary_counts_held_records(harness):
    from nova.blueprints.sync_tokens import _sync_view
    uid, _old = _held_record(harness)
    _hub(harness, _obj(999, "M 1"))                        # something to confirm

    st = _run_phase1(harness, "sync")

    assert st["phase"] == "awaiting_confirmation", st
    assert st["summary"]["held"] == 1
    with harness["app"].test_request_context():
        view = _sync_view(harness["local_db"], harness["local_user"])
    assert "1 record will be held" in view["held_line"]


def _photo_placement(db, uid, tmp_path, monkeypatch):
    """A session row and a staged photo for it, with the engine's roots in tmp."""
    instance = _patch_sync_dirs(monkeypatch, tmp_path)
    user = db.get(DbUser, uid)
    row = JournalSession(user_id=uid, external_id="ext-q1", date_utc=date(2026, 1, 10))
    db.add(row)
    db.commit()
    ref = FileRef(role=ROLE_SESSION_PHOTO, table="journal_sessions", key="ext-q1",
                  slot="session_image_file", sha256=_digest(PNG), size=len(PNG), ext="png")
    staged = stage_file(user, ref, io.BytesIO(PNG))
    return SimpleNamespace(instance=str(instance), user=user, row=row, ref=ref, staged=staged)


def test_l4_a_placement_with_no_safe_path_stays_pending_and_keeps_its_copy(
        db_session, monkeypatch, tmp_path):
    """The live name is a symlink leading out of the user's upload folder: the
    placement refuses it. The file is still current, so it stays pending (the
    cursor holds) and its staged copy is kept for the next try."""
    user = nova.get_or_create_db_user(db_session, "default")
    db_session.commit()
    w = _photo_placement(db_session, user.id, tmp_path, monkeypatch)
    root = os.path.join(w.instance, "uploads", "default")
    os.makedirs(root, exist_ok=True)
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"not yours")
    os.symlink(str(outside), os.path.join(root, f"{w.row.id}.png"))

    report = commit_staged_files(db_session, MergeResult(files_expected=[w.ref]), [w.staged],
                                 user=w.user, guard=placement_guard(db_session, w.user, [w.ref]))

    assert report.pending == [("journal_sessions", "ext-q1", "session_image_file", w.ref.sha256)]
    assert report.discard == []
    assert outside.read_bytes() == b"not yours"


def test_l4_a_placement_whose_row_was_deleted_drops_its_staged_copy(
        db_session, monkeypatch, tmp_path):
    user = nova.get_or_create_db_user(db_session, "default")
    db_session.commit()
    w = _photo_placement(db_session, user.id, tmp_path, monkeypatch)
    db_session.delete(w.row)
    db_session.commit()

    report = commit_staged_files(db_session, MergeResult(files_expected=[w.ref]), [w.staged],
                                 user=w.user)
    db_session.commit()
    finish_placements(report, user=w.user)

    assert report.pending == [] and [r.key for r in report.skipped] == ["ext-q1"]
    assert not os.path.exists(w.staged.path)


# =============================================================================
# L4 - the start-of-sync sweep
# =============================================================================

@pytest.fixture
def sweep_world(tmp_path):
    instance = tmp_path / "instance"
    user = SimpleNamespace(id=7, username="tester")
    uploads = instance / "uploads" / "tester"
    logs = instance / "logs" / "asiair"
    staging = instance / "sync_staging" / "7"
    for d in (uploads, logs, staging):
        d.mkdir(parents=True)
    old = time.time() - 2 * 24 * 3600

    def make(path, age=None):
        path.write_bytes(b"x")
        if age is not None:
            os.utime(path, (age, age))
        return path
    return SimpleNamespace(instance=str(instance), user=user, uploads=uploads, logs=logs,
                           staging=staging, old=old, make=make)


def test_l4_a_stale_part_goes_and_a_fresh_one_stays(sweep_world):
    from nova.sync_engine import sweep_sync_leftovers
    w = sweep_world
    stale_up = w.make(w.uploads / "5.jpg.part", w.old)
    stale_log = w.make(w.logs / "5_log.txt.part", time.time() - 2 * 3600)
    fresh = w.make(w.uploads / "6.jpg.part")

    removed = sweep_sync_leftovers(w.user, instance_path=w.instance)

    assert not stale_up.exists() and not stale_log.exists()
    assert fresh.exists()
    assert removed == {"part": 2, "staged": 0}


def test_l4_a_stale_staged_file_still_owed_is_kept(sweep_world):
    from nova.sync_engine import _save_owed_files, sweep_sync_leftovers
    w = sweep_world
    owed_digest, loose_digest = "a" * 64, "b" * 64
    _save_owed_files(w.user, [{"table": "journal_sessions", "key": "k",
                               "slot": "session_image_file", "sha256": owed_digest,
                               "rev": 1, "stamp": "x"}], instance_path=w.instance)
    owed = w.make(w.staging / f"{owed_digest}.jpg", w.old)
    owed_part = w.make(w.staging / f"{owed_digest}.jpg.part", w.old)
    loose = w.make(w.staging / f"{loose_digest}.jpg", w.old)
    young = w.make(w.staging / f"{'c' * 64}.jpg")

    removed = sweep_sync_leftovers(w.user, instance_path=w.instance)

    assert owed.exists() and owed_part.exists() and young.exists()
    assert (w.staging / "owed.json").exists()
    assert not loose.exists()
    assert removed == {"part": 0, "staged": 1}


def test_l4_a_browser_upload_temporary_is_never_touched(sweep_world):
    from nova.sync_engine import sweep_sync_leftovers
    w = sweep_world
    temps = [w.make(w.uploads / ".5.jpg.upload-0123abcd.tmp", w.old),
             w.make(w.logs / ".5_log.txt.upload-0123abcd.tmp", w.old),
             w.make(w.uploads / ".5.jpg.upload-0123abcd.part", w.old),
             w.make(w.staging / ".x.upload-0123abcd.tmp", w.old)]

    sweep_sync_leftovers(w.user, instance_path=w.instance)

    assert all(t.exists() for t in temps)


# =============================================================================
# A commit that fails after a placement loses nothing
# =============================================================================

def _row_file_ok(root, column, digest):
    """The committed row names no file yet, or a file holding `digest`."""
    return not column or _file_digest(os.path.join(root, column)) == digest


def test_commit_failure_after_a_settle_placement_loses_nothing(harness, monkeypatch):
    import nova.blueprints.sync_api as api
    import nova.sync_engine as engine
    import nova.sync_job as job
    uid = harness["local_user"].id
    digest = _sha256_of_bytes(BIG)
    hub_root = os.path.join(harness["instance_path"], "uploads", "hubuser")
    _mark_downloaded(harness)
    _local_query(harness, lambda db: db.add(_session(uid, "local-sess", image="photo.jpg")))
    harness["local_db"].commit()
    _write_upload(harness["instance_path"], "default", "photo.jpg", BIG)
    monkeypatch.setattr("nova.sync_job._backoff", lambda _i: 0.0)
    assert _hub_dies_before_placing(harness, monkeypatch)["phase"] == "failed"

    armed = {"on": False, "placed": 0}
    real_commit = engine.commit_staged_files

    def arming(*args, **kwargs):
        report = real_commit(*args, **kwargs)
        armed["placed"] += len(report.placed)
        armed["on"] = armed["on"] or bool(report.placed)
        return report
    hub_session = harness["hub_session"]
    with monkeypatch.context() as patch:
        patch.setattr(engine, "commit_staged_files", arming)
        patch.setattr(api, "get_db", lambda: _CommitFailsOnce(hub_session(), armed))
        patch.setattr(job, "_retry_transient", lambda fn, attempts=1: fn())
        st = _sync_to_done(harness)

    assert armed["placed"] and armed["on"] is False and st["phase"] == "failed", st
    assert [e["sha256"] for e in _hub_owed(harness)] == [digest]
    row = _hub_query(harness, lambda db: db.query(JournalSession)
                     .filter_by(user_id=999, external_id="local-sess").one())
    assert _row_file_ok(hub_root, row.session_image_file, digest)

    st = _sync_to_done(harness)
    assert st["phase"] == "done", st
    row = _hub_query(harness, lambda db: db.query(JournalSession)
                     .filter_by(user_id=999, external_id="local-sess").one())
    assert row.session_image_file and _row_file_ok(hub_root, row.session_image_file, digest)
    assert _hub_owed(harness) == []


def test_commit_failure_after_a_repair_placement_loses_nothing(harness, monkeypatch):
    import nova.blueprints.sync_api as api
    _download_one_photo(harness)
    hub_root = os.path.join(harness["instance_path"], "uploads", "hubuser")
    digest = _sha256_of_bytes(BIG)

    def own_name(db):
        # The hub's own naming, as its browser or a placement would have left it.
        row = db.query(JournalSession).filter_by(user_id=999, external_id="hub-sess").one()
        db.execute(text("UPDATE journal_sessions SET session_image_file = :n WHERE id = :i"),
                   {"n": f"{row.id}.jpg", "i": row.id})
        db.commit()
    _hub_query(harness, own_name)
    os.remove(os.path.join(hub_root, "hub.jpg"))

    armed = {"on": False, "placed": 0}
    real_commit = api.commit_staged_files

    def arming(*args, **kwargs):
        report = real_commit(*args, **kwargs)
        armed["placed"] += len(report.placed)
        armed["on"] = armed["on"] or bool(report.placed)
        return report
    hub_session = harness["hub_session"]
    with monkeypatch.context() as patch:
        patch.setattr(api, "commit_staged_files", arming)
        patch.setattr(api, "get_db", lambda: _CommitFailsOnce(hub_session(), armed))
        st = _sync_to_done(harness)

    assert armed["placed"] and armed["on"] is False, armed
    assert st["phase"] == "done", st
    row = _hub_query(harness, lambda db: db.query(JournalSession)
                     .filter_by(user_id=999, external_id="hub-sess").one())
    assert _row_file_ok(hub_root, row.session_image_file, digest)

    st = _sync_to_done(harness)
    assert st["phase"] == "done" and st["result"]["unrestored_files"] == 0, st
    row = _hub_query(harness, lambda db: db.query(JournalSession)
                     .filter_by(user_id=999, external_id="hub-sess").one())
    assert _file_digest(os.path.join(hub_root, row.session_image_file)) == digest


def _local_commit_fails_after_placement(monkeypatch):
    import nova.sync_job as job
    armed = {"on": False, "placed": 0}
    real_commit = job.commit_staged_files

    def arming(*args, **kwargs):
        report = real_commit(*args, **kwargs)
        armed["placed"] += len(report.placed)
        armed["on"] = armed["on"] or bool(report.placed)
        return report
    real_get_db = job.get_db
    monkeypatch.setattr(job, "commit_staged_files", arming)
    monkeypatch.setattr(job, "get_db", lambda: _CommitFailsOnce(real_get_db(), armed))
    return armed


def test_commit_failure_after_a_pull_placement_loses_nothing(harness, monkeypatch):
    uid = harness["local_user"].id
    local_root = os.path.join(harness["instance_path"], "uploads", "default")
    digest = _sha256_of_bytes(BIG)
    _mark_downloaded(harness)
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", BIG)

    with monkeypatch.context() as patch:
        armed = _local_commit_fails_after_placement(patch)
        st = _sync_to_done(harness)

    assert armed["placed"] and armed["on"] is False and st["phase"] == "failed", st
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    assert _row_file_ok(local_root, row.session_image_file, digest)

    st = _sync_to_done(harness)
    assert st["phase"] == "done" and st["result"]["unrestored_files"] == 0, st
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    assert row.session_image_file
    assert _file_digest(os.path.join(local_root, row.session_image_file)) == digest


def test_commit_failure_after_a_download_placement_loses_nothing(harness, monkeypatch):
    uid = harness["local_user"].id
    local_root = os.path.join(harness["instance_path"], "uploads", "default")
    digest = _sha256_of_bytes(BIG)
    _hub(harness, _session(999, "hub-sess", image="hub.jpg"))
    _write_upload(harness["instance_path"], "hubuser", "hub.jpg", BIG)

    with monkeypatch.context() as patch:
        armed = _local_commit_fails_after_placement(patch)
        st = _run_phase1(harness, "download")
        assert st["phase"] == "awaiting_confirmation", st
        confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
        st = _wait(uid, lambda s: s.get("phase") in ("done", "failed"))

    assert armed["placed"] and armed["on"] is False and st["phase"] == "failed", st
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    assert _row_file_ok(local_root, row.session_image_file, digest)

    st = _sync_to_done(harness)                            # finishes the download
    assert st["phase"] == "done" and st["result"]["unrestored_files"] == 0, st
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    assert row.session_image_file
    assert _file_digest(os.path.join(local_root, row.session_image_file)) == digest


# =============================================================================
# Skip-identical: a file already here under its row is neither fetched nor placed
# =============================================================================

def _count_fetches(monkeypatch):
    calls = []
    real_iter, real_slot = _TestSyncClient.fetch_file_iter, _TestSyncClient.fetch_slot

    def fetch_iter(self, *args, **kwargs):
        calls.append("file")
        return real_iter(self, *args, **kwargs)

    def fetch_slot(self, *args, **kwargs):
        calls.append("slot")
        return real_slot(self, *args, **kwargs)
    monkeypatch.setattr(_TestSyncClient, "fetch_file_iter", fetch_iter)
    monkeypatch.setattr(_TestSyncClient, "fetch_slot", fetch_slot)
    return calls


def test_skip_identical_project_image(harness, monkeypatch):
    uid = harness["local_user"].id
    image = PNG + b"-project-image"
    _hub(harness, Project(user_id=999, id="p1", name="Veil", final_image_file="veil.png"))
    _write_upload(harness["instance_path"], "hubuser", "veil.png", image)
    st = _run_phase1(harness, "download")
    confirm_job(harness["app"], uid, replace_confirmed="REPLACE")
    assert _wait(uid, lambda s: s.get("phase") in ("done", "failed"))["phase"] == "done"
    local = _local_query(harness, lambda db: db.query(Project).filter_by(user_id=uid, id="p1").one())
    name = local.final_image_file
    assert _sha256_of_bytes(image) in _local_photo_digests(harness)

    def rename(db):
        row = db.query(Project).filter_by(user_id=999, id="p1").one()
        row.name = "Veil mosaic"
        db.commit()
    _hub_query(harness, rename)
    calls = _count_fetches(monkeypatch)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert st["summary"]["files"]["down"] == 0 and calls == []
    local = _local_query(harness, lambda db: db.query(Project).filter_by(user_id=uid, id="p1").one())
    assert local.name == "Veil mosaic" and local.final_image_file == name
    assert _file_digest(os.path.join(harness["instance_path"], "uploads", "default", name)) \
        == _sha256_of_bytes(image)


def test_skip_identical_under_another_name_and_extension(harness, monkeypatch):
    """This computer holds the same bytes under a name and extension of its
    own. A record change from the hub fetches and places nothing, and the row
    keeps its name."""
    uid = harness["local_user"].id
    _download_one_photo(harness)
    root = os.path.join(harness["instance_path"], "uploads", "default")
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    os.replace(os.path.join(root, row.session_image_file), os.path.join(root, "kept.jpeg"))
    harness["local_db"].execute(
        text("UPDATE journal_sessions SET session_image_file = 'kept.jpeg' WHERE id = :i"),
        {"i": row.id})                                     # this installation's own name
    harness["local_db"].commit()

    def edit(db):
        hub_row = db.query(JournalSession).filter_by(user_id=999, external_id="hub-sess").one()
        hub_row.notes = "seeing 2/5"
        db.commit()
    _hub_query(harness, edit)
    calls = _count_fetches(monkeypatch)

    st = _sync_to_done(harness)

    assert st["phase"] == "done", st
    assert calls == [] and st["summary"]["files"]["down"] == 0
    row = _local_query(harness, lambda db: db.query(JournalSession)
                       .filter_by(user_id=uid, external_id="hub-sess").one())
    assert row.notes == "seeing 2/5" and row.session_image_file == "kept.jpeg"
    assert sorted(os.listdir(root)) == ["kept.jpeg"]
    assert _file_digest(os.path.join(root, "kept.jpeg")) == _sha256_of_bytes(BIG)
