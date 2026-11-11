"""Instance sync — local job runner (step 6).

A sync/download runs in a daemon thread, never inside a request: gunicorn caps
requests at 30 s and runs two workers, so the work cannot ride one request. The
job's state lives on disk under ``instance/sync_jobs/<user_id>/``, so any worker
can serve a status poll and a restart never loses the summary the user already
approved.

Two-phase. Download and Sync now pause in ``awaiting_confirmation`` with the
summary and the fetched artifacts (snapshot / push payload) on disk. Confirm
runs phase 2 from those artifacts after re-checking the guard (the local
counter rev is unchanged, final deletions <= approved, no new mass delete);
Abort drops the artifacts and cancels the hub's push session.

Safety. The per-user ``sync_lock`` is held for the whole job (non-blocking), the
DB apply is a single transaction, and file placement is idempotent. A worker
that dies leaves a stale heartbeat; the next poll marks the job failed — never
half-applied, never auto-resumed. The next manual Sync now completes any pending
files through the engine's watermark rules.

Liveness. A running worker keeps its heartbeat fresh from a small ticker thread
of its own, so a long step (the backup, one large file, the apply) can never
make a live job read as interrupted. The ticker dies with the process, which is
exactly when the heartbeat should go stale. It also stops by itself when the
worker has made no progress for HEARTBEAT_PROGRESS_LIMIT_SECONDS: a worker that
hangs (a peer that never answers) then reads as interrupted like a dead one,
instead of blocking every later sync until the process is restarted.

There is deliberately no Abort for a running job. A Python thread cannot be
killed, so an abort could only ask the worker to stop at its next step - which
a hung worker never reaches - and a worker stopped between the hub's push and
this computer's pull, or holding the account's lock, is exactly the state the
interrupted-job path already handles on the next Sync now.

Healing. Every completed Sync now and Download ends with a healing pass, and
its one test is whether a row is unchanged since the last sync (its sync_rev is
at or below the push watermark):
  - a file this computer's record names but its disk lacks is fetched from the
    hub by (table, key, slot) when the row is unchanged; a row that changed
    here and lost its bytes is reported as lost and never filled with the
    hub's older file;
  - a file the hub reported missing is uploaded through /repair when this
    computer's row is unchanged and still has the bytes.
Whatever is still missing after that is counted in the result ("N files could
not be restored") and no longer holds a watermark - except a file the hub still
owes from this push and this computer can supply: that one holds the push
watermark, so the record is offered again and the hub asks for the bytes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

from sqlalchemy import select

from nova.config import BACKUP_DIR, CACHE_DIR, UPLOAD_FOLDER
from nova.helpers import (LOGS_DIR, get_db, release_file_lock,
                          try_acquire_file_lock, write_file_atomic)
from nova.models import (DB_PATH, INSTANCE_PATH, DbUser, JournalSession,
                         SyncState, UiPref, UserCustomFilter)
from nova.sync_client import SyncClient, SyncTransportError
from nova.sync_engine import (
    KEY_CHANGED_AT, REFUSAL_BAD_FILE_REF, REFUSAL_FILE_EXISTS,
    REFUSAL_MASS_DELETE, REFUSAL_NOT_PAIRED, REFUSAL_PLAN_STALE,
    REFUSAL_SERVER_UNTOUCHED, REFUSAL_SYNC_RUNNING,
    REPLACED_DIRNAME, ROLE_NOTE_IMAGE, STAGING_DIRNAME, TABLE_MODEL, UNIQUE_KEY,
    _HEX64, _PARENT_TABLES, _role_ext_ok, _row_by_key,
    CommitReport, FileRef, MergeResult, Refusal, StagedFile, SyncRefused,
    apply_download, apply_merge, check_sync_preconditions, collect_files,
    commit_staged_files,
    counter_rev, discard_staged, export_changes, file_slot_role, files_needed,
    file_source_path, missing_local_slots, move_replaced_files, natural_key,
    parse_datetime, plan_download, plan_merge, pushed_watermark,
    record_pull_applied,
    record_push_accepted, shift, stage_file, staged_index, staging_dir, sync_lock,
    to_iso,
)
from nova.sync_transport import SYNC_UPLOAD_DIRNAME
from nova.sync_time import utc_now

logger = logging.getLogger(__name__)

# --- Phase names (opaque; the UI translates them) -----------------------------

PHASE_QUEUED = "queued"
PHASE_PLANNING = "planning"
PHASE_AWAITING = "awaiting_confirmation"
PHASE_BACKUP = "backup"
PHASE_UPLOAD = "upload"
PHASE_DOWNLOAD_FILES = "download_files"
PHASE_APPLYING = "applying"
PHASE_DONE = "done"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"

_RUNNING_PHASES = {PHASE_QUEUED, PHASE_PLANNING, PHASE_BACKUP, PHASE_UPLOAD,
                   PHASE_DOWNLOAD_FILES, PHASE_APPLYING}
_TERMINAL_PHASES = {PHASE_DONE, PHASE_FAILED, PHASE_CANCELLED}

# --- Lifetimes ----------------------------------------------------------------

# An unconfirmed job expires after this long (matches the hub's push-session
# TTL: a confirmed phase 2 must be able to resume the session the plan opened).
JOB_EXPIRE_SECONDS = 30 * 60
# A running job whose heartbeat is older than this is treated as dead.
HEARTBEAT_STALE_SECONDS = 120
# Heartbeat is re-stamped at most this often, to bound per-chunk disk writes.
HEARTBEAT_THROTTLE_SECONDS = 10
# How often a running worker's ticker re-stamps the heartbeat on its own. Well
# under HEARTBEAT_STALE_SECONDS, so several beats may be missed before a live
# job could read as dead.
HEARTBEAT_INTERVAL_SECONDS = 20
# A worker that has not reached _touch() or moved a byte for this long is taken
# to be hung: its ticker stops, the heartbeat goes stale, the job reads as
# interrupted and a new one may start.
HEARTBEAT_PROGRESS_LIMIT_SECONDS = 600
# How many records changed on both sides the summary names before "and N more".
CONFLICT_LINES = 5
# Local backups kept per user (SQLite online backup + hard-link snapshot).
BACKUP_KEEP = 5
# plan_stale / sync_running are retried this many times before reporting.
PLAN_STALE_RETRIES = 3
# A completion toast older than this is consumed without being shown.
TOAST_TTL_SECONDS = 600


class _Superseded(Exception):
    """The worker's job was replaced by a newer start; stop quietly."""


def _mkdirp(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def _remove_quiet(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# --- Job state on disk --------------------------------------------------------

def jobs_root(instance_path: str = None) -> str:
    return os.path.join(instance_path or INSTANCE_PATH, "sync_jobs")


def job_dir(user_id: int, instance_path: str = None) -> str:
    return os.path.join(jobs_root(instance_path), str(int(user_id)))


def job_path(user_id: int, instance_path: str = None) -> str:
    return os.path.join(job_dir(user_id, instance_path), "job.json")


def load_job(user_id: int, instance_path: str = None) -> dict:
    try:
        with open(job_path(user_id, instance_path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_job(user_id: int, job: dict, instance_path: str = None) -> None:
    path = job_path(user_id, instance_path)
    _mkdirp(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        _remove_quiet(tmp)


def clear_job(user_id: int, instance_path: str = None) -> None:
    shutil.rmtree(job_dir(user_id, instance_path), ignore_errors=True)


def _save_artifact(user_id: int, name: str, data, instance_path: str = None) -> None:
    _mkdirp(job_dir(user_id, instance_path))
    write_file_atomic(os.path.join(job_dir(user_id, instance_path), name),
                      lambda p: json.dump(data, open(p, "w", encoding="utf-8"), default=str))


def _load_artifact(user_id: int, name: str, instance_path: str = None):
    try:
        with open(os.path.join(job_dir(user_id, instance_path), name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_stale(job: dict) -> bool:
    if job.get("phase") in _TERMINAL_PHASES:
        return False
    if job.get("phase") == PHASE_AWAITING:
        return False  # waiting for the user, not a running worker
    from nova.sync_engine import parse_datetime
    last = parse_datetime(job.get("heartbeat_at"))
    if last is None:
        return True
    return (utc_now() - last).total_seconds() > HEARTBEAT_STALE_SECONDS


def _is_expired(job: dict) -> bool:
    if job.get("phase") != PHASE_AWAITING:
        return False
    from nova.sync_engine import parse_datetime
    started = parse_datetime(job.get("heartbeat_at") or job.get("started_at"))
    if started is None:
        return True
    return (utc_now() - started).total_seconds() > JOB_EXPIRE_SECONDS


# job.json is read-modified-written by the worker and by its heartbeat ticker,
# two threads of one process. Without this an update from one could be undone
# by the other's stale copy.
_JOB_LOCK = threading.RLock()

# When each running worker last made progress, by (user id, start token), on
# the monotonic clock. In memory only: it is this process's worker threads the
# ticker watches.
_PROGRESS: dict = {}


def _note_progress(user_id: int, start_token: str) -> None:
    """The worker is alive and moving: a step finished, or bytes moved."""
    _PROGRESS[(int(user_id), start_token)] = time.monotonic()


def _touch(user_id: int, start_token: str, **updates) -> dict:
    """Update the job iff this worker still owns it, re-stamping the heartbeat."""
    with _JOB_LOCK:
        job = load_job(user_id)
        if job is None or job.get("start_token") != start_token:
            raise _Superseded()
        job.update(updates)
        now = utc_now()
        last = parse_datetime(job.get("heartbeat_at")) or now
        if (now - last).total_seconds() >= HEARTBEAT_THROTTLE_SECONDS:
            job["heartbeat_at"] = to_iso(now)
        job["pid"] = os.getpid()
        save_job(user_id, job)
        _note_progress(user_id, start_token)
        return job


def _beat(user_id: int, start_token: str) -> bool:
    """Re-stamp the heartbeat of a job this worker still owns. False when the
    job is gone, superseded or finished, i.e. when there is nothing to keep
    alive."""
    with _JOB_LOCK:
        job = load_job(user_id)
        if (job is None or job.get("start_token") != start_token
                or job.get("phase") not in _RUNNING_PHASES):
            return False
        job["heartbeat_at"] = to_iso(utc_now())
        save_job(user_id, job)
        return True


class _Heartbeat:
    """Keep a running job's heartbeat fresh for as long as the block runs.

    The worker itself only reaches _touch() between steps, and one step (the
    backup of a large database, a 50 MB image on a slow link, the apply) can
    outlast HEARTBEAT_STALE_SECONDS. This ticker is a daemon thread of the same
    process: it beats while the worker lives and stops with it, so a stale
    heartbeat still means what it always meant — the worker is gone.

    Gone includes hung. The ticker only vouches for a worker that is making
    progress (_note_progress: every _touch, every chunk moved). After
    HEARTBEAT_PROGRESS_LIMIT_SECONDS without any it stops beating for good, and
    the job reads as interrupted two minutes later.
    """

    def __init__(self, user_id: int, start_token: str):
        self._user_id = user_id
        self._token = start_token
        self._interval = HEARTBEAT_INTERVAL_SECONDS
        self._limit = HEARTBEAT_PROGRESS_LIMIT_SECONDS
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"sync-heartbeat-{user_id}")

    def _stalled(self) -> bool:
        last = _PROGRESS.get((int(self._user_id), self._token))
        return last is not None and time.monotonic() - last > self._limit

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self._stalled():
                logger.warning("sync: job for user %s made no progress for %ss; "
                               "its heartbeat stops", self._user_id, int(self._limit))
                return
            try:
                _beat(self._user_id, self._token)
            except Exception:
                logger.debug("sync heartbeat failed", exc_info=True)

    def __enter__(self):
        _note_progress(self._user_id, self._token)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5)
        _PROGRESS.pop((int(self._user_id), self._token), None)
        return False


def _mark_failed(user_id: int, start_token: str, refusal: Refusal) -> None:
    try:
        _touch(user_id, start_token, phase=PHASE_FAILED,
               phase_label=PHASE_FAILED,
               error={"code": refusal.code, "detail": dict(refusal.detail or {})})
    except _Superseded:
        pass


# --- Starting / confirming / aborting -----------------------------------------

def _new_job(user_id: int, kind: str, allow_mass_delete: bool = False) -> dict:
    return {
        "job_id": uuid.uuid4().hex,
        "start_token": uuid.uuid4().hex,
        "user_id": int(user_id),
        "kind": kind,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "phase_label": PHASE_QUEUED,
        "summary": None,
        "result": None,
        "error": None,
        "allow_mass_delete": bool(allow_mass_delete),
        "started_at": to_iso(utc_now()),
        "heartbeat_at": to_iso(utc_now()),
        "pid": os.getpid(),
    }


def start_job(app, user_id: int, kind: str) -> dict:
    """Begin phase 1 (plan) for `kind` in {"download", "sync"} in a daemon thread."""
    existing = load_job(user_id)
    if existing and existing.get("phase") in _RUNNING_PHASES and not _is_stale(existing):
        raise SyncRefused(Refusal(REFUSAL_SYNC_RUNNING, {"user_id": user_id}))
    if existing and existing.get("phase") == PHASE_AWAITING and not _is_expired(existing):
        raise SyncRefused(Refusal(REFUSAL_SYNC_RUNNING, {"user_id": user_id}))
    if existing and existing.get("phase") == PHASE_AWAITING and _is_expired(existing):
        clear_job(user_id)

    job = _new_job(user_id, kind)
    save_job(user_id, job)
    token = job["start_token"]
    thread = threading.Thread(target=_run_phase1, args=(app, user_id, kind, token),
                              name=f"sync-{kind}-{user_id}", daemon=True)
    thread.start()
    return job


def confirm_job(app, user_id: int, *, allow_mass_delete: bool = False,
                allow_push_mass_delete: bool = None,
                allow_pull_mass_delete: bool = None,
                replace_confirmed: str = None) -> dict:
    """Begin phase 2 from the stored artifacts, after the guards are checked.

    The two mass-delete approvals are separate. Each side's brake is released
    only when THAT side asked for it in the summary the user saw and the user
    approved THAT side: an approval for the pull never reaches the push, and
    the other way round. ``allow_mass_delete`` is the one-switch form (approve
    whichever side asked); the per-side flags, when given, take its place.

    A job is confirmed once. The confirm moves it out of awaiting_confirmation
    under a cross-process lock before the worker starts, so a second confirm —
    a double click, a re-POST, the other gunicorn worker — finds nothing to
    confirm and is refused. It never replaces the token a live worker holds.
    """
    guard = try_acquire_file_lock(os.path.join(_job_lock_dir(), f"confirm_{int(user_id)}"))
    if guard is None:
        raise SyncRefused(Refusal(REFUSAL_SYNC_RUNNING, {"user_id": user_id}))
    try:
        with _JOB_LOCK:
            job = load_job(user_id)
            if job is None or job.get("phase") != PHASE_AWAITING:
                raise SyncRefused(Refusal(REFUSAL_SYNC_RUNNING, {"user_id": user_id}))
            summary = job.get("summary") or {}
            kind = job.get("kind")
            if (kind == "download" and summary.get("requires_replace_typed")
                    and replace_confirmed != "REPLACE"):
                raise SyncRefused(Refusal("replace_required", {}))

            push_asked, pull_asked = _mass_delete_sides(summary)
            approve_push = allow_mass_delete if allow_push_mass_delete is None \
                else allow_push_mass_delete
            approve_pull = allow_mass_delete if allow_pull_mass_delete is None \
                else allow_pull_mass_delete
            allow_push = bool(push_asked and approve_push)
            allow_pull = bool(pull_asked and approve_pull)
            if kind == "sync" and push_asked and not allow_push:
                raise SyncRefused(Refusal(REFUSAL_MASS_DELETE, dict(
                    summary.get("push_mass_delete_detail")
                    or summary.get("mass_delete_detail") or {})))
            if kind == "sync" and pull_asked and not allow_pull:
                raise SyncRefused(Refusal(REFUSAL_MASS_DELETE, dict(
                    summary.get("pull_mass_delete_detail")
                    or summary.get("mass_delete_detail") or {})))

            job["start_token"] = uuid.uuid4().hex
            job["allow_push_mass_delete"] = allow_push
            job["allow_pull_mass_delete"] = allow_pull
            job["allow_mass_delete"] = bool(allow_push or allow_pull)
            # Confirmed: no longer awaiting. A second confirm stops above.
            job["phase"] = PHASE_QUEUED
            job["phase_label"] = PHASE_QUEUED
            job["heartbeat_at"] = to_iso(utc_now())
            job["pid"] = os.getpid()
            save_job(user_id, job)
            token = job["start_token"]
    finally:
        release_file_lock(guard)
    thread = threading.Thread(target=_run_phase2,
                              args=(app, user_id, kind, token, allow_push, allow_pull),
                              name=f"sync-phase2-{kind}-{user_id}", daemon=True)
    thread.start()
    return job


def _job_lock_dir() -> str:
    root = jobs_root()
    _mkdirp(root)
    return root


def _mass_delete_sides(summary: dict) -> tuple:
    """(push asked, pull asked): which side's plan hit the mass-delete brake."""
    if "push_mass_delete" in summary or "pull_mass_delete" in summary:
        return bool(summary.get("push_mass_delete")), bool(summary.get("pull_mass_delete"))
    # A summary written before the split names one flag for both.
    asked = bool(summary.get("mass_delete"))
    return asked, asked


def abort_job(app, user_id: int) -> None:
    """Drop an awaiting job's artifacts and cancel the hub push session."""
    job = load_job(user_id)
    if job is None:
        return
    kind = job.get("kind")
    clear_job(user_id)
    if kind != "sync":
        return
    try:
        with app.app_context():
            db = get_db()
            state = db.scalars(select(SyncState).where(SyncState.user_id == user_id)).first()
            if state is not None and state.server_url and state.token:
                SyncClient(state.server_url, state.token).push_cancel()
    except Exception:
        logger.warning("push_cancel failed during abort", exc_info=True)


def consume_toast(user_id: int, start_token: str) -> None:
    """Mark a finished job's completion toast as shown, so it flashes only once.

    `start_token` is the token of the job the caller loaded. Like _touch(), the
    job is re-read and left alone when a newer start replaced it.
    """
    job = load_job(user_id)
    if job is None or job.get("start_token") != start_token:
        return
    if job.get("toast_pending"):
        job["toast_pending"] = False
        save_job(user_id, job)


# --- Worker entry points ------------------------------------------------------

def _run_phase1(app, user_id: int, kind: str, start_token: str) -> None:
    with app.app_context():
        db = get_db()
        user = db.get(DbUser, int(user_id))
        if user is None:
            _mark_failed(user_id, start_token, Refusal(REFUSAL_NOT_PAIRED, {"user_id": user_id}))
            return
        try:
            with sync_lock(user_id), _Heartbeat(user_id, start_token):
                if kind == "download":
                    _phase1_download(db, user, start_token)
                else:
                    _phase1_sync(db, user, start_token)
        except SyncRefused as exc:
            _mark_failed(user_id, start_token, exc.refusal)
        except SyncTransportError as exc:
            _mark_failed(user_id, start_token, Refusal("network_error", {"detail": str(exc)}))
        except _Superseded:
            pass
        except Exception:
            logger.exception("sync phase 1 (%s) failed for user %s", kind, user_id)
            _mark_failed(user_id, start_token, Refusal("internal_error", {}))


def _run_phase2(app, user_id: int, kind: str, start_token: str,
                allow_push_mass_delete: bool = False,
                allow_pull_mass_delete: bool = False) -> None:
    with app.app_context():
        db = get_db()
        user = db.get(DbUser, int(user_id))
        if user is None:
            _mark_failed(user_id, start_token, Refusal(REFUSAL_NOT_PAIRED, {"user_id": user_id}))
            return
        try:
            with sync_lock(user_id), _Heartbeat(user_id, start_token):
                if kind == "download":
                    _phase2_download(db, user, start_token)
                else:
                    _phase2_sync(db, user, start_token,
                                 allow_push_mass_delete=allow_push_mass_delete,
                                 allow_pull_mass_delete=allow_pull_mass_delete)
        except SyncRefused as exc:
            _mark_failed(user_id, start_token, exc.refusal)
        except SyncTransportError as exc:
            _mark_failed(user_id, start_token, Refusal("network_error", {"detail": str(exc)}))
        except _Superseded:
            pass
        except Exception:
            logger.exception("sync phase 2 (%s) failed for user %s", kind, user_id)
            _mark_failed(user_id, start_token, Refusal("internal_error", {}))


def _client_for(db, user) -> SyncClient:
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None or not state.server_url or not state.token:
        raise SyncRefused(Refusal(REFUSAL_NOT_PAIRED, {"user_id": user.id}))
    return SyncClient(state.server_url, state.token)


# --- Download -----------------------------------------------------------------

def _local_has_data(plan) -> bool:
    return sum(int(v) for v in getattr(plan, "wiping", {}).values()) > 0


def _first_download(db, user) -> bool:
    """True when this pairing has never downloaded (nothing pushed, no last_sync_at)."""
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    return (state is None) or ((state.last_sync_at is None) and not (state.local_pushed_rev or 0))


def _may_skip_deletes(deleted) -> bool:
    """True when a plan deletes rows in a reference-checked table.

    Those deletes can still be skipped at apply time ("edit beats delete" /
    "still referenced"), so the planned count is an upper bound and the summary
    says "up to N deleted".
    """
    return any(table in _PARENT_TABLES and count for table, count in (deleted or {}).items())


def _unsynced_local_count(db, user) -> int:
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    since = int(state.local_pushed_rev or 0) if state else 0
    payload = export_changes(db, user, since_rev=since, side="local", with_files=False)
    return sum(len(v) for v in payload.get("records", {}).values()) + len(payload.get("tombstones") or [])


def _phase1_download(db, user, start_token: str) -> None:
    uid = user.id
    _touch(uid, start_token, phase=PHASE_PLANNING, progress=0.05, phase_label=PHASE_PLANNING)
    client = _client_for(db, user)
    hello = client.hello()
    if hello.get("untouched"):
        raise SyncRefused(Refusal(REFUSAL_SERVER_UNTOUCHED, {"user_id": uid}))
    snapshot = client.snapshot()
    plan = plan_download(db, user, snapshot, offset_seconds=hello.get("_clock_offset_seconds", 0.0))
    if plan.refused:
        raise SyncRefused(plan.refusals[0])

    _save_artifact(uid, "snapshot.json", snapshot)
    summary = {
        "kind": "download",
        "server_rev": plan.server_rev,
        "server_counts": dict(plan.inserting),
        "local_counts": dict(plan.wiping),
        "files": plan.summary()["files"],
        "unsynced_local_changes": _unsynced_local_count(db, user),
        "first_download": _first_download(db, user),
        "deletions": 0,
        "counter_rev": counter_rev(db, uid),
        "offset_seconds": hello.get("_clock_offset_seconds", 0.0),
        "requires_replace_typed": _local_has_data(plan),
    }
    _touch(uid, start_token, phase=PHASE_AWAITING, progress=0.9, summary=summary,
           phase_label=PHASE_AWAITING)


def _phase2_download(db, user, start_token: str) -> None:
    uid = user.id
    job = _touch(uid, start_token, phase=PHASE_BACKUP, progress=0.0, phase_label=PHASE_BACKUP)
    summary = job.get("summary") or {}
    if counter_rev(db, uid) != summary.get("counter_rev"):
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"user_id": uid}))

    snapshot = _load_artifact(uid, "snapshot.json")
    plan = plan_download(db, user, snapshot, offset_seconds=summary.get("offset_seconds", 0.0))
    if plan.refused:
        raise SyncRefused(plan.refusals[0])

    backup_local(db, user)
    _touch(uid, start_token, phase=PHASE_BACKUP, progress=0.08, phase_label=PHASE_BACKUP)
    client = _client_for(db, user)
    needed = files_needed(plan, user)
    _touch(uid, start_token, phase=PHASE_DOWNLOAD_FILES, progress=0.1, phase_label=PHASE_DOWNLOAD_FILES)
    _fetch_files(client, user, needed, uid, start_token)

    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.8, phase_label=PHASE_APPLYING)
    result = apply_download(db, plan, user=user)
    db.commit()
    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.85, phase_label=PHASE_APPLYING)
    move_replaced_files(db, plan, user)
    db.commit()
    staged = _staged_for(plan, user)
    commit_staged_files(db, result, staged, user=user)
    db.commit()

    # Healing pass, then the watermark is released whatever is still missing:
    # the download is complete, and a file that cannot be restored is reported
    # rather than left holding the cursor at zero for ever.
    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.92, phase_label=PHASE_APPLYING)
    unrestored = len(_heal_local(db, user, client,
                                 _local_heal_targets(db, user, result),
                                 tick=lambda: _note_progress(uid, start_token)))
    _release_cursor(db, uid, plan.server_rev)

    _touch(uid, start_token, phase=PHASE_DONE, progress=1.0, phase_label=PHASE_DONE,
           result={"down": dict(result.created),
                   "replaced_files": result.replaced_files,
                   "pending_files": unrestored,
                   "unrestored_files": unrestored,
                   "blocked": _blocked(result.blocked, "local")},
           toast_pending=True, toast_at=to_iso(utc_now()))


# --- Sync now -----------------------------------------------------------------

def _first_non_mass_delete(refusals):
    for r in refusals or []:
        if getattr(r, "code", "") != REFUSAL_MASS_DELETE:
            return r
    return None


def _sum_counts(mapping) -> int:
    return sum(int(v) for v in (mapping or {}).values())


def _backoff(attempt: int) -> float:
    # Full jitter on a small exponential base, capped.
    return min(1.0 + 2.0 ** attempt, 8.0) * (0.5 + 0.5 * (attempt % 2))


def _retry_transient(fn, attempts: int = PLAN_STALE_RETRIES):
    """Retry transient failures and plan_stale/sync_running; surface the rest.

    Every other 409 code (needs_download, clock_skew, mass_delete,
    server_untouched, local_replaced, remote_replaced) is deterministic and is
    raised immediately.
    """
    last: Exception = None
    for i in range(attempts):
        try:
            return fn()
        except SyncRefused as exc:
            if exc.refusal.code not in (REFUSAL_SYNC_RUNNING, REFUSAL_PLAN_STALE):
                raise
            last = exc
        except SyncTransportError as exc:
            last = exc
        if i < attempts - 1:
            time.sleep(_backoff(i))
    raise last


def _sync_plan_empty(summary) -> bool:
    """True when a sync plan has nothing to apply in either direction."""
    up = summary.get("up") or {}
    down = summary.get("down") or {}
    files = summary.get("files") or {}
    return (
        int(up.get("created") or 0) == 0
        and int(up.get("updated") or 0) == 0
        and int(up.get("deleted") or 0) == 0
        and int(down.get("created") or 0) == 0
        and int(down.get("updated") or 0) == 0
        and int(down.get("deleted") or 0) == 0
        and int(files.get("up") or 0) == 0
        and int(files.get("down") or 0) == 0
        # A file can need placing without needing transfer (it is already
        # staged from a run that died): that is still work, so not empty.
        and int(files.get("up_place") or 0) == 0
        and int(files.get("down_place") or 0) == 0
    )


# --- Summary helpers: conflicts and blocked records -----------------------------

def _display_name(model: type, record: dict) -> str:
    """What the user calls a record, for the summary's conflict line."""
    if model is JournalSession:
        parts = [str(record.get("date_utc") or "").strip(),
                 str(record.get("object_name") or "").strip()]
        name = " ".join(p for p in parts if p)
    elif model is UserCustomFilter:
        name = str(record.get("filter_label") or record.get("filter_key") or "").strip()
    else:
        column = UNIQUE_KEY.get(model) or "name"
        name = str(record.get(column) or record.get("name") or "").strip()
    return name or natural_key(model, record)


def _both_sides_changed(push_payload: dict, pull_payload: dict, offset: float) -> list:
    """Records changed on BOTH sides since the last sync, and whose copy stays.

    The push payload is everything this computer changed above its watermark,
    the pull payload everything the hub changed above the cursor; a record in
    both, with different content, was edited on both sides. Which copy stays is
    the engine's own rule, and it is one rule for the record and its files: the
    push is merged first and the hub never heals, so the newer stamp wins there
    (this computer's moved into the hub's clock by the measured offset) and a
    tie goes to the hub. That holds for a record whose two copies differ only
    in a file: when the hub keeps its own, the pull that follows places the
    hub's file here, because the row is by then unchanged since the push; when
    the hub takes this computer's, there is nothing left to pull. ui_prefs is
    left out — its two blobs are merged key by key, so neither side loses.
    """
    out = []
    pulled = pull_payload.get("records") or {}
    for table, rows in (push_payload.get("records") or {}).items():
        model = TABLE_MODEL.get(table)
        if model is None or model is UiPref:
            continue
        theirs = {natural_key(model, r): r for r in pulled.get(table) or []
                  if isinstance(r, dict)}
        for mine in rows or []:
            if not isinstance(mine, dict):
                continue
            other = theirs.get(natural_key(model, mine))
            if other is None:
                continue
            if ({k: v for k, v in mine.items() if k != KEY_CHANGED_AT}
                    == {k: v for k, v in other.items() if k != KEY_CHANGED_AT}):
                continue
            try:
                local_at = parse_datetime(mine.get(KEY_CHANGED_AT))
                hub_at = shift(parse_datetime(other.get(KEY_CHANGED_AT)), offset)
            except (TypeError, ValueError):
                continue
            kept = "local" if (local_at and hub_at and local_at > hub_at) else "server"
            out.append({"table": table, "name": _display_name(model, mine), "kept": kept})
    return out


def _blocked(entries, side: str) -> list:
    """Blocked records as the page shows them: who refused (`side`) rides along."""
    out = []
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append({"table": entry.get("table"), "key": entry.get("key"),
                        "name": entry.get("name") or entry.get("key") or "",
                        "reason": entry.get("reason"), "side": side})
    return out


# --- Healing pass ----------------------------------------------------------------

def _stage_from_hub(client, user, table: str, key: str, slot: str,
                    sha256: str = "", ext_hint: str = ""):
    """Fetch one file from the hub by (table, key, slot) into staging.

    The digest and extension come from the hub's response, not from this side:
    the file is gone here, so there is nothing local to hash, and the hub may
    hold it under another extension. Returns a StagedFile, or None when the
    hub's answer cannot be used (no digest, a type the slot does not allow, an
    over-cap or corrupt stream).
    """
    info, chunks = client.fetch_slot(table, key, slot, sha256)
    digest = str(info.get("sha256") or "").lower()
    ext = str(info.get("ext") or ext_hint or "").lower()
    role = file_slot_role(table, slot) or ROLE_NOTE_IMAGE
    if (not _HEX64.fullmatch(digest) or not _role_ext_ok(role, ext)
            or (sha256 and digest != sha256)):
        return None
    try:
        size = int(info.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    ref = FileRef(role=role, table=table, key=key, slot=slot, sha256=digest,
                  size=size, ext=ext)
    staged = stage_file(user, ref, _IterStream(chunks))
    return None if staged.skipped else staged


def _row_unchanged(db, user, table: str, key: str, watermark: int):
    """(row, unchanged): the local row a key names, and whether it is as the
    last sync left it (sync_rev at or below the push watermark)."""
    model = TABLE_MODEL.get(table)
    row = _row_by_key(db, model, user.id, key) if model is not None else None
    if row is None:
        return None, False
    return row, int(getattr(row, "sync_rev", None) or 0) <= int(watermark)


def _local_heal_targets(db, user, result=None, watermark=None) -> list:
    """(table, key, slot, sha256, ext hint, fetch) of every file a heal looks at.

    Two sources. Placements this run could not finish: the plan decided on that
    exact digest, so fetching it is the rest of this run's own apply. And FILE
    slots whose file is simply not on this disk: those are fetched only when
    the row is unchanged since the last sync - then the hub's file is the
    current one. A row that changed here and lost its bytes gets fetch=False:
    it is reported as lost, never filled with the hub's older file.
    One entry per (table, key, slot); a known digest wins over an unknown one.

    `watermark` is the push watermark "the last sync" left. A Sync now passes
    the value it started with: its own push moves the watermark past rows that
    were changed here, and those must still count as changed - the hub took
    the record, not the bytes this disk no longer has.
    """
    targets: dict[tuple, tuple] = {}
    for entry in getattr(result, "pending_files", None) or []:
        try:
            table, key, slot, sha256 = entry[0], entry[1], entry[2], entry[3]
        except (IndexError, TypeError):
            continue
        ident = (table, key, slot, sha256 if slot == "note_img" else "")
        targets[ident] = (table, key, slot, sha256 or "", "", True)
    if watermark is None:
        watermark = pushed_watermark(db, user.id)
    for ref in missing_local_slots(db, user):
        ident = (ref.table, ref.key, ref.slot, "")
        if ident in targets:
            continue
        _row, unchanged = _row_unchanged(db, user, ref.table, ref.key, watermark)
        targets[ident] = (ref.table, ref.key, ref.slot, "", ref.ext, unchanged)
    return list(targets.values())


def _heal_local(db, user, client, targets, tick=None) -> set:
    """Fetch and place each target. Returns the (table, key, slot) of every one
    that could NOT be restored.

    Placement is commit_staged_files(), the same idempotent step a sync uses: it
    writes the bytes and the column and never bumps a rev — a file name is this
    installation's own business. One file failing never stops the others, and
    nothing here fails the job: a file that cannot be restored is counted, and
    so is one that must not be (fetch=False, see _local_heal_targets).
    """
    failed: set = set()
    for table, key, slot, sha256, ext_hint, fetch in targets:
        if tick is not None:
            tick()
        if not fetch:
            failed.add((table, key, slot))
            continue
        try:
            staged = _stage_from_hub(client, user, table, key, slot, sha256, ext_hint)
            if staged is None:
                failed.add((table, key, slot))
                continue
            report = commit_staged_files(
                db, MergeResult(files_expected=[staged.ref]), [staged], user=user)
            db.commit()
            if not report.placed:
                failed.add((table, key, slot))
        except (SyncRefused, SyncTransportError, OSError):
            db.rollback()
            failed.add((table, key, slot))
        except Exception:
            db.rollback()
            logger.exception("sync: healing %s/%s/%s failed", table, key, slot)
            failed.add((table, key, slot))
    return failed


def _local_file_ref(db, user, table: str, key: str, slot: str, sha256: str = ""):
    """(ref, path) of the file this computer's row holds for a slot, or
    (None, "") when the row or the bytes are not here. With `sha256` the file
    must be exactly that one."""
    model = TABLE_MODEL.get(table)
    row = _row_by_key(db, model, user.id, key) if model is not None else None
    if row is None:
        return None, ""
    refs = collect_files(db, user, rows={model: [row]})
    ref = next((r for r in refs
                if r.slot == slot and not r.missing and r.sha256
                and (not sha256 or r.sha256 == sha256)), None)
    path = file_source_path(ref, user) if ref is not None else ""
    return (ref, path) if path else (None, "")


def _repair_upload(client, ref: FileRef, path: str) -> None:
    """Send one file to the hub's /repair, resuming where a dropped try stopped."""
    received = int(client.repair_resume(ref.table, ref.key, ref.slot, ref.sha256,
                                        ref.ext, ref.size).get("received") or 0)
    if received > int(ref.size or 0):
        received = 0
    with open(path, "rb") as handle:
        handle.seek(received)
        offset = received
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            client.repair_chunk(ref.table, ref.key, ref.slot, ref.sha256,
                                ref.ext, ref.size, offset, chunk)
            offset += len(chunk)


def _heal_hub(db, user, client, targets, tick=None, watermark=None) -> set:
    """Upload each file the hub lost and this computer has. Returns the
    (table, key, slot) of every one that could NOT be restored.

    `targets` are (table, key, slot, sha256) from the hub's `hub_missing` list:
    files its records name and its disk lacks. One is uploaded only when this
    computer's row is unchanged since the last sync - then the two records are
    the same record and this file is the one the hub lost. A row that changed
    here is left alone: its file travels with the record on the next push,
    under the merge's rules. A record this computer no longer holds is skipped
    silently; so is a file the hub turns out to have (file_exists) or refuses
    as not repairable. A file missing on both sides, or an upload that fails,
    counts. `watermark` is as in _local_heal_targets: the one this run started
    with.
    """
    failed: set = set()
    seen: set = set()
    if watermark is None:
        watermark = pushed_watermark(db, user.id)
    for entry in targets or []:
        try:
            table, key, slot, sha256 = entry[0], entry[1], entry[2], entry[3] or ""
        except (IndexError, TypeError):
            continue
        ident = (table, key, slot, sha256 if slot == "note_img" else "")
        if ident in seen:
            continue
        seen.add(ident)
        if tick is not None:
            tick()
        row, unchanged = _row_unchanged(db, user, table, key, watermark)
        if row is None or not unchanged:
            continue
        try:
            ref, path = _local_file_ref(
                db, user, table, key, slot, sha256 if slot == "note_img" else "")
            if not path:
                failed.add((table, key, slot))         # gone here too: nobody has it
                continue
            _repair_upload(client, ref, path)
        except SyncRefused as exc:
            if exc.refusal.code not in (REFUSAL_FILE_EXISTS, REFUSAL_BAD_FILE_REF):
                failed.add((table, key, slot))
        except (SyncTransportError, OSError):
            failed.add((table, key, slot))
        except Exception:
            logger.exception("sync: repairing %s/%s/%s on the hub failed", table, key, slot)
            failed.add((table, key, slot))
    return failed


def _release_cursor(db, uid: int, server_rev) -> None:
    """Mark a finished run complete: the cursor at the rev it consumed.

    Called after the healing pass. Until then a file that did not land holds
    the cursor (so a run that FAILS re-offers the record and the engine heals
    it); once the run has completed and healing has had its try, what is still
    missing is reported instead of holding the cursor for ever.
    """
    state = db.scalars(select(SyncState).where(SyncState.user_id == uid)).first()
    if state is None:
        return
    if server_rev is not None:
        state.server_cursor = max(int(state.server_cursor or 0), int(server_rev))
    state.last_sync_at = utc_now()
    db.commit()


def _complete_empty_sync(db, uid, rev_at_export, server_rev) -> None:
    """Advance the watermarks and last_sync_at for a sync with nothing to apply."""
    state = db.scalars(select(SyncState).where(SyncState.user_id == uid)).first()
    if state is None:
        return
    state.local_pushed_rev = max(state.local_pushed_rev or 0, int(rev_at_export))
    state.server_cursor = int(server_rev)
    state.last_sync_at = utc_now()
    db.commit()


def _phase1_sync(db, user, start_token: str) -> None:
    uid = user.id
    _touch(uid, start_token, phase=PHASE_PLANNING, progress=0.05, phase_label=PHASE_PLANNING)
    refusal = check_sync_preconditions(db, user, side="local")
    if refusal is not None:
        raise SyncRefused(refusal)

    client = _client_for(db, user)
    hello = client.hello()
    offset = hello.get("_clock_offset_seconds", 0.0)
    state = db.scalars(select(SyncState).where(SyncState.user_id == uid)).first()

    rev_at_export = counter_rev(db, uid)
    push_payload = export_changes(db, user, since_rev=int(state.local_pushed_rev or 0), side="local")

    # Push plan on the hub. A mass-delete refusal here is the brake, not an
    # error: it is surfaced as a confirm and phase 2 re-plans with the user's
    # explicit approval.
    push_session = None
    push_mass_delete = False
    push_mass_delete_detail = {}
    try:
        push_resp = client.push_plan(push_payload, offset, allow_mass_delete=False)
        push_session = push_resp
    except SyncRefused as exc:
        if exc.refusal.code == REFUSAL_MASS_DELETE:
            push_mass_delete = True
            push_mass_delete_detail = dict(exc.refusal.detail or {})
        else:
            raise

    # Provisional pull plan (exclude_revs empty: it overstates the pull by at
    # most the push_revs, which only remove rows the push just sent).
    pull_payload = client.changes(since_rev=int(state.server_cursor or 0),
                                  exclude_revs=[], with_files=True)
    pull_plan = plan_merge(db, user, pull_payload, side="local", offset_seconds=offset,
                           allow_mass_delete=False)
    non_mass = _first_non_mass_delete(pull_plan.refusals)
    if non_mass is not None:
        raise SyncRefused(non_mass)

    pull_mass_delete = any(getattr(r, "code", "") == REFUSAL_MASS_DELETE for r in pull_plan.refusals)
    pull_mass_delete_detail = {}
    mass_delete_detail = push_mass_delete_detail
    if pull_mass_delete:
        for r in pull_plan.refusals:
            if getattr(r, "code", "") == REFUSAL_MASS_DELETE:
                pull_mass_delete_detail = dict(r.detail or {})
                mass_delete_detail = pull_mass_delete_detail
                break
    conflicts = _both_sides_changed(push_payload, pull_payload, offset)

    _save_artifact(uid, "push_payload.json", push_payload)
    _save_artifact(uid, "pull_payload.json", pull_payload)

    push_summary = push_session.get("plan") if push_session else {}
    # When the hub refused the push plan as a mass delete there is no plan
    # summary to read counts from (push_session is None): take the deletion
    # count from the refusal's own detail so the user confirms the real number,
    # not zero.
    up_deleted = _sum_counts(push_summary.get("deleted"))
    if push_mass_delete:
        up_deleted = int((push_mass_delete_detail or {}).get("deletions") or 0)
    server_rev = int(pull_payload.get("rev") or 0)
    summary = {
        "kind": "sync",
        "push": push_summary,
        "pull": pull_plan.summary(),
        "up": {"created": _sum_counts(push_summary.get("created")),
               "updated": _sum_counts(push_summary.get("updated")),
               "deleted": up_deleted,
               "may_skip_deleted": _may_skip_deletes(push_summary.get("deleted"))},
        "down": {"created": _sum_counts(pull_plan.created),
                 "updated": _sum_counts(pull_plan.updated),
                 "deleted": _sum_counts(pull_plan.deleted),
                 "may_skip_deleted": _may_skip_deletes(pull_plan.deleted)},
        "deletions": _sum_counts(pull_plan.deleted),
        "mass_delete": push_mass_delete or pull_mass_delete,
        "mass_delete_detail": mass_delete_detail,
        # Each side's brake on its own: an approval for one never opens the other.
        "push_mass_delete": push_mass_delete,
        "push_mass_delete_detail": push_mass_delete_detail,
        "pull_mass_delete": pull_mass_delete,
        "pull_mass_delete_detail": pull_mass_delete_detail,
        "conflicts": conflicts[:CONFLICT_LINES],
        "conflicts_more": max(0, len(conflicts) - CONFLICT_LINES),
        "blocked": (_blocked(push_summary.get("blocked"), "server")
                    + _blocked(pull_plan.blocked, "local")),
        "counter_rev": counter_rev(db, uid),
        "rev_at_export": rev_at_export,
        "offset_seconds": offset,
        "session_id": push_session.get("session_id") if push_session else None,
        "fingerprint": push_session.get("fingerprint") if push_session else None,
        "files_needed": push_session.get("files_needed") if push_session else [],
        "hub_missing": push_session.get("hub_missing") if push_session else [],
        "files": {"up": len(_upload_refs(
                      db, user, (push_session.get("files_needed") or []) if push_session else [],
                      push_payload.get("files") or {})),
                  "up_place": int((push_session.get("files_to_place") or 0) if push_session else 0),
                  "down_place": len(pull_plan.placements or []),
                  "down": pull_plan.summary()["files"]["needed"],
                  "down_bytes": pull_plan.summary()["files"]["bytes"]},
    }
    if _sync_plan_empty(summary):
        watermark = pushed_watermark(db, uid)      # as the last sync left it
        _complete_empty_sync(db, uid, rev_at_export, server_rev)
        # Nothing to merge is still a completed sync, so it heals too: a file
        # lost out of band shows up in no plan, only here.
        def tick():
            _note_progress(uid, start_token)
        unrestored = len(
            _heal_hub(db, user, client, summary["hub_missing"], tick=tick,
                      watermark=watermark)
            | _heal_local(db, user, client,
                          _local_heal_targets(db, user, watermark=watermark),
                          tick=tick))
        _touch(uid, start_token, phase=PHASE_DONE, progress=1.0, phase_label=PHASE_DONE,
               result={"up": {"created": 0, "updated": 0, "deleted": 0},
                       "down": {"created": 0, "updated": 0, "deleted": 0},
                       "files": 0, "empty": True,
                       "pending_files": unrestored,
                       "unrestored_files": unrestored,
                       "blocked": summary["blocked"]},
               summary=summary,
               toast_pending=True, toast_at=to_iso(utc_now()))
        return

    _touch(uid, start_token, phase=PHASE_AWAITING, progress=0.9, summary=summary,
           phase_label=PHASE_AWAITING)


def _upload_refs(db, user, files_needed, manifest) -> list:
    """The files the hub asked for that this computer can send.

    Most are described by the push payload's own manifest. One the hub still
    owes from an earlier push (its staged bytes were lost) may belong to a
    record this payload does not carry: that one is resolved through the local
    row, and only when the row still holds exactly that digest.
    """
    refs = []
    for ref in _refs_from_needed(files_needed, manifest):
        if ref.sha256 in (manifest or {}):
            if file_source_path(ref, user):
                refs.append(ref)
            continue
        local, path = _local_file_ref(db, user, ref.table, ref.key, ref.slot, ref.sha256)
        if path:
            refs.append(local)
    return refs


def _refs_from_needed(files_needed, manifest):
    refs = []
    for entry in files_needed or []:
        try:
            table, key, slot, sha256 = entry[0], entry[1], entry[2], entry[3]
        except (IndexError, TypeError):
            continue
        desc = (manifest or {}).get(sha256) or {}
        # The role comes from the slot, never the manifest's single `role` field:
        # one digest may serve several slots/roles, and the manifest records only
        # the first. The descriptor's role is the fallback for note images, whose
        # slot "note_img" has no FILE column.
        role = file_slot_role(table, slot) or desc.get("role") or ""
        refs.append(FileRef(role=role, table=table, key=key,
                            slot=slot, sha256=sha256, size=int(desc.get("size") or 0),
                            ext=desc.get("ext") or "", name=desc.get("name") or ""))
    return refs


def _phase2_sync(db, user, start_token: str, allow_mass_delete: bool = False, *,
                 allow_push_mass_delete: bool = None,
                 allow_pull_mass_delete: bool = None) -> None:
    uid = user.id
    job = _touch(uid, start_token, phase=PHASE_BACKUP, progress=0.0, phase_label=PHASE_BACKUP)
    summary = job.get("summary") or {}
    if counter_rev(db, uid) != summary.get("counter_rev"):
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"user_id": uid}))

    # Two approvals, never one: each side's brake opens only for the side that
    # asked AND was approved. The pull's approval must not reach the hub's plan.
    push_asked, pull_asked = _mass_delete_sides(summary)
    allow_push = bool(push_asked and (allow_mass_delete if allow_push_mass_delete is None
                                      else allow_push_mass_delete))
    allow_pull = bool(pull_asked and (allow_mass_delete if allow_pull_mass_delete is None
                                      else allow_pull_mass_delete))

    backup_local(db, user)
    _touch(uid, start_token, phase=PHASE_BACKUP, progress=0.08, phase_label=PHASE_BACKUP)
    client = _client_for(db, user)
    offset = summary.get("offset_seconds", 0.0)
    rev_at_export = summary.get("rev_at_export", 0)
    server_cursor = _state_cursor(db, uid)
    # The push watermark as the last sync left it: the healing pass at the end
    # asks "unchanged since the last sync", not "since this run's own push".
    watermark = pushed_watermark(db, uid)

    # --- push (plan → upload → apply, re-planned on plan_stale/sync_running) ---
    planned: dict = {}

    def _push():
        push_payload = _load_artifact(uid, "push_payload.json") or export_changes(
            db, user, since_rev=int(rev_at_export), side="local")
        push_resp = client.push_plan(push_payload, offset, allow_mass_delete=allow_push)
        planned.clear()
        planned.update(push_resp)
        refs = _upload_refs(db, user, push_resp.get("files_needed") or [],
                            push_payload.get("files") or {})
        _upload_files(client, user, refs, uid, start_token, push_resp.get("session_id"))
        _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.6, phase_label=PHASE_APPLYING)
        return client.push_apply(push_resp.get("session_id"), push_resp.get("fingerprint"))

    hub_result = _retry_transient(_push)
    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.65, phase_label=PHASE_APPLYING)
    push_revs = hub_result.get("result", {}).get("push_revs") or []
    deferred = hub_result.get("result", {}).get("deferred") or []
    pending_files = hub_result.get("result", {}).get("pending_files") or []
    hub_blocked = _blocked(hub_result.get("result", {}).get("blocked"), "server")

    # A file the hub could not place holds the watermark for now. The hub keeps
    # its own note of what it owes and finishes the placement at the start of
    # the next sync request, asking for the bytes again if it lost them.
    record_push_accepted(db, user, rev_at_export, deferred=deferred,
                         commit=CommitReport(pending=pending_files))
    db.commit()

    # --- pull (fetch → plan → apply), re-fetched on plan_stale/sync_running ---
    pull_payload = _retry_transient(
        lambda: client.changes(since_rev=server_cursor, exclude_revs=push_revs, with_files=True))
    pull_plan = plan_merge(db, user, pull_payload, side="local", offset_seconds=offset,
                           allow_mass_delete=allow_pull)
    if pull_plan.refused:
        non_mass = _first_non_mass_delete(pull_plan.refusals)
        if non_mass is not None:
            raise SyncRefused(non_mass)
        raise SyncRefused(Refusal(REFUSAL_MASS_DELETE, summary.get("mass_delete_detail") or {}))

    final_deletions = _sum_counts(pull_plan.deleted)
    if final_deletions > int(summary.get("deletions", 0)):
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"reason": "more_deletions"}))

    rev_at_export_pull = counter_rev(db, uid)
    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.7, phase_label=PHASE_APPLYING)
    result = apply_merge(db, pull_plan, user=user)
    server_rev = int(pull_payload.get("rev") or 0)
    # The pull's cursor also advances only once its files are placed, so
    # commit_staged_files carries this rev the same way a download carries
    # snapshot_rev.
    result.server_rev = server_rev
    record_pull_applied(db, user, result, server_rev, rev_at_export_pull)
    db.commit()
    _touch(uid, start_token, phase=PHASE_DOWNLOAD_FILES, progress=0.75,
           phase_label=PHASE_DOWNLOAD_FILES)

    # A drop in here fails the job with the records committed and the cursor
    # held; the next Sync now is offered the same records again and the merge
    # places their files whatever the two clocks say.
    needed = files_needed(pull_plan, user)
    _fetch_files(client, user, needed, uid, start_token)
    staged = _staged_for(pull_plan, user)
    commit_staged_files(db, result, staged, user=user)
    db.commit()

    # --- healing pass, then the watermarks are released -------------------------
    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.92, phase_label=PHASE_APPLYING)
    def tick():
        _note_progress(uid, start_token)

    # A set, so a file gone on both sides is counted once, not once per side.
    lost = _heal_hub(db, user, client, list(planned.get("hub_missing") or []),
                     tick=tick, watermark=watermark)
    lost |= _heal_local(db, user, client,
                        _local_heal_targets(db, user, result, watermark=watermark),
                        tick=tick)
    # What the hub still owes from this push and this computer can supply: it
    # holds the push watermark, so the next Sync now offers the record again
    # and the hub asks for the bytes. Anything else still missing is reported
    # and holds no watermark. Deferred RECORDS still do.
    owed = []
    for entry in pending_files:
        try:
            table, key, slot, sha256 = entry[0], entry[1], entry[2], entry[3]
        except (IndexError, TypeError):
            continue
        if _local_file_ref(db, user, table, key, slot, sha256)[1]:
            owed.append((table, key, slot, sha256))
            lost.add((table, key, slot))
    unrestored = len(lost)
    record_push_accepted(db, user, rev_at_export, deferred=deferred,
                         commit=CommitReport(pending=owed))
    db.commit()
    if not result.deferred:
        _release_cursor(db, uid, server_rev)

    _touch(uid, start_token, phase=PHASE_DONE, progress=1.0, phase_label=PHASE_DONE,
           result={"up": {"created": _sum_counts(hub_result.get("result", {}).get("created")),
                          "updated": _sum_counts(hub_result.get("result", {}).get("updated")),
                          "deleted": _sum_counts(hub_result.get("result", {}).get("deleted"))},
                   "down": {"created": _sum_counts(result.created),
                            "updated": _sum_counts(result.updated),
                            "deleted": _sum_counts(result.deleted)},
                   "pending_files": unrestored,
                   "unrestored_files": unrestored,
                   "blocked": hub_blocked + _blocked(result.blocked, "local")},
           toast_pending=True, toast_at=to_iso(utc_now()))


def _state_cursor(db, uid: int) -> int:
    state = db.scalars(select(SyncState).where(SyncState.user_id == uid)).first()
    return int(state.server_cursor or 0) if state else 0


# --- File transfer helpers ----------------------------------------------------

class _IterStream:
    """Adapt an iterator of bytes to the ``.read(n)`` shape stage_file wants."""

    def __init__(self, iterator):
        self._it = iterator
        self._buf = b""

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        if n < 0:
            out, self._buf = self._buf, b""
            return out
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


def _fetch_files(client, user, refs, uid, start_token) -> None:
    total = max(len(refs), 1)
    for i, ref in enumerate(refs):
        _fetch_file(client, user, ref, tick=lambda: _note_progress(uid, start_token))
        _touch(uid, start_token, progress=0.1 + 0.7 * (i + 1) / total,
               phase_label=PHASE_DOWNLOAD_FILES)


def _fetch_file(client, user, ref, tick=None) -> None:
    """Fetch one file into staging, resuming a partial ``.part`` via Range.

    A dropped download leaves a partial ``<sha>.part``; the next attempt re-uses
    it and asks the hub for the remainder with a Range header, so a retry does
    not re-download bytes it already holds. The digest is verified over the
    whole stream before the file is renamed into place.
    """
    final = os.path.join(staging_dir(user), f"{ref.sha256}.{ref.ext}")
    if os.path.exists(final):
        return
    part = final + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    digest = hashlib.sha256()
    if have:
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    with open(part, "ab") as out:
        for chunk in client.fetch_file_iter(ref, resume_from=have):
            out.write(chunk)
            digest.update(chunk)
            if tick is not None:
                tick()          # bytes are moving: the worker is not hung
    if digest.hexdigest() != ref.sha256:
        discard_staged(StagedFile(ref=ref, path=part))
        raise SyncTransportError(f"digest mismatch downloading {ref.sha256}")
    os.replace(part, final)


def _send_chunk(client, session_id, sha256, offset, chunk, attempts=3) -> dict:
    """Send one chunk, retrying transient failures at the same offset.

    The hub's write_chunk rewinds when the offset is at or below what it already
    holds, so a retried chunk is idempotent — a torn write is overwritten, not
    appended to.
    """
    last: Exception = None
    for i in range(attempts):
        try:
            return client.push_file_chunk(session_id, sha256, offset, chunk)
        except SyncTransportError as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(_backoff(i))
    raise last


def _upload_files(client, user, refs, uid, start_token, session_id) -> None:
    total = max(len(refs), 1)
    for i, ref in enumerate(refs):
        path = file_source_path(ref, user)
        if not path:
            continue
        received = int(client.push_file_resume(session_id, ref.sha256).get("received") or 0)
        with open(path, "rb") as f:
            f.seek(received)
            offset = received
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                _send_chunk(client, session_id, ref.sha256, offset, chunk)
                offset += len(chunk)
                _note_progress(uid, start_token)
        _touch(uid, start_token, phase=PHASE_UPLOAD, progress=0.1 + 0.5 * (i + 1) / total,
               phase_label=PHASE_UPLOAD)


def _staged_for(plan, user) -> list:
    index = staged_index(user, verify=True)
    refs = getattr(plan, "placements", None) or getattr(plan, "files", []) or []
    return [StagedFile(ref=ref, path=index[ref.sha256], local_name=ref.name)
            for ref in refs if ref.sha256 and not ref.missing and ref.sha256 in index]


# --- Local backup (data-safety rule 1) ----------------------------------------

def backup_local(db, user) -> str:
    """Snapshot app.db (SQLite online backup) + hard-link uploads/ and logs/.

    Hard links keep the pre-sync inode alive: placement elsewhere uses atomic
    rename, so a file the sync later replaces still has its old bytes under the
    backup link. Returns the backup directory.
    """
    uid = user.id
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%f")
    root = os.path.join(BACKUP_DIR, "sync", str(uid), stamp)
    _mkdirp(root)

    tmp_db = os.path.join(root, ".app.db.tmp")
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(tmp_db)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(tmp_db, os.path.join(root, "app.db"))
    except Exception:
        logger.warning("sync: online DB backup failed for user %s", uid, exc_info=True)
        _remove_quiet(tmp_db)

    _hardlink_tree(UPLOAD_FOLDER, os.path.join(root, "uploads"))
    _hardlink_tree(LOGS_DIR, os.path.join(root, "logs"))

    write_file_atomic(os.path.join(root, "manifest.json"),
                      lambda p: json.dump({
                          "created_at": to_iso(utc_now()), "user_id": uid,
                          "app_db": os.path.exists(os.path.join(root, "app.db")),
                      }, open(p, "w", encoding="utf-8"), default=str))
    _prune_backups(uid, keep=BACKUP_KEEP)
    return root


def _hardlink_tree(src_root: str, dst_root: str) -> None:
    if not os.path.isdir(src_root):
        return
    for dirpath, _dirnames, filenames in os.walk(src_root):
        rel = os.path.relpath(dirpath, src_root)
        target_dir = dst_root if rel == "." else os.path.join(dst_root, rel)
        _mkdirp(target_dir)
        for name in filenames:
            s = os.path.join(dirpath, name)
            d = os.path.join(target_dir, name)
            try:
                os.link(s, d)
            except OSError:
                try:
                    shutil.copy2(s, d)
                except OSError:
                    pass


def _prune_backups(user_id: int, keep: int = BACKUP_KEEP) -> None:
    base = os.path.join(BACKUP_DIR, "sync", str(int(user_id)))
    try:
        dirs = sorted(os.path.join(base, n) for n in os.listdir(base)
                      if os.path.isdir(os.path.join(base, n)))
    except FileNotFoundError:
        return
    for path in (dirs[:-keep] if keep > 0 else dirs):
        shutil.rmtree(path, ignore_errors=True)


def cleanup_user_sync(user_id: int, instance_path: str = None,
                      backup_dir: str = None, cache_dir: str = None) -> list:
    """(path, base) pairs for every per-user sync artifact a purge must remove.

    The single source of truth for where the sync subsystem stores a user's
    files — the two-phase job, the staging directory, files moved aside by a
    Download from server, the push upload accumulator, the in-flight push
    session, and the pre-push snapshots. ``purge_user_app_data`` imports this
    lazily (to avoid a cycle) and removes/reports each pair itself, so its
    dry-run lists exactly the paths a real purge deletes and the two can never
    drift from the layout the job runner and engine actually use.

    ``backup_dir`` and ``cache_dir`` default to this module's constants, but the
    caller passes its own (possibly patched) bindings so the paths agree with
    ``nova.sync_transport``'s push-session location under a test override.
    """
    uid = str(int(user_id))
    root = instance_path or INSTANCE_PATH
    backup = backup_dir or BACKUP_DIR
    cache = cache_dir or CACHE_DIR
    return [
        (os.path.join(root, "sync_jobs", uid), os.path.join(root, "sync_jobs")),
        (os.path.join(root, "sync_jobs", f"confirm_{uid}.lock"),
         os.path.join(root, "sync_jobs")),
        (os.path.join(root, STAGING_DIRNAME, uid), os.path.join(root, STAGING_DIRNAME)),
        (os.path.join(root, REPLACED_DIRNAME, uid), os.path.join(root, REPLACED_DIRNAME)),
        (os.path.join(root, SYNC_UPLOAD_DIRNAME, uid),
         os.path.join(root, SYNC_UPLOAD_DIRNAME)),
        (os.path.join(backup, "sync", uid), os.path.join(backup, "sync")),
        (os.path.join(cache, f"sync_push_{uid}.json"), cache),
    ]


def job_status(user_id: int) -> dict:
    """The poll payload for one user's job, with stale/expired handling applied.

    A running job whose worker died (stale heartbeat) is reported as failed —
    never auto-resumed. An unconfirmed job past its expiry is cleared and
    reported as not running.
    """
    job = load_job(user_id)
    if job is None:
        return {"running": False}
    phase = job.get("phase")
    if phase == PHASE_AWAITING and _is_expired(job):
        clear_job(user_id)
        return {"running": False, "expired": True}
    if phase == PHASE_AWAITING:
        return {"running": False, "phase": phase, "progress": job.get("progress"),
                "summary": job.get("summary"), "result": job.get("result"),
                "error": job.get("error"),
                "toast_pending": job.get("toast_pending")}
    if phase in _RUNNING_PHASES and _is_stale(job):
        job = dict(job, phase=PHASE_FAILED,
                   error={"code": "interrupted", "detail": {}})
        phase = PHASE_FAILED
    running = phase in _RUNNING_PHASES
    return {"running": running, "phase": phase, "progress": job.get("progress"),
            "summary": job.get("summary"), "result": job.get("result"),
            "error": job.get("error"),
            "toast_pending": job.get("toast_pending")}
