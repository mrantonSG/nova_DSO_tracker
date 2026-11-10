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
from nova.helpers import LOGS_DIR, get_db, write_file_atomic
from nova.models import DB_PATH, INSTANCE_PATH, DbUser, SyncState
from nova.sync_client import SyncClient, SyncTransportError
from nova.sync_engine import (
    REFUSAL_MASS_DELETE, REFUSAL_NOT_PAIRED, REFUSAL_PLAN_STALE,
    REFUSAL_SERVER_UNTOUCHED, REFUSAL_SYNC_RUNNING,
    REPLACED_DIRNAME, STAGING_DIRNAME,
    CommitReport, FileRef, Refusal, StagedFile, SyncRefused,
    apply_download, apply_merge, check_sync_preconditions, commit_staged_files,
    counter_rev, discard_staged, export_changes, file_slot_role, files_needed,
    file_source_path, move_replaced_files, plan_download, plan_merge,
    record_pull_applied,
    record_push_accepted, stage_file, staged_index, staging_dir, sync_lock, to_iso,
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
# Local backups kept per user (SQLite online backup + hard-link snapshot).
BACKUP_KEEP = 5
# plan_stale / sync_running are retried this many times before reporting.
PLAN_STALE_RETRIES = 3


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


def _touch(user_id: int, start_token: str, **updates) -> dict:
    """Update the job iff this worker still owns it, re-stamping the heartbeat."""
    job = load_job(user_id)
    if job is None or job.get("start_token") != start_token:
        raise _Superseded()
    job.update(updates)
    from nova.sync_engine import parse_datetime
    now = utc_now()
    last = parse_datetime(job.get("heartbeat_at")) or now
    if (now - last).total_seconds() >= HEARTBEAT_THROTTLE_SECONDS:
        job["heartbeat_at"] = to_iso(now)
    job["pid"] = os.getpid()
    save_job(user_id, job)
    return job


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
                replace_confirmed: str = None) -> dict:
    """Begin phase 2 from the stored artifacts, after the guards are checked."""
    job = load_job(user_id)
    if job is None or job.get("phase") != PHASE_AWAITING:
        raise SyncRefused(Refusal(REFUSAL_SYNC_RUNNING, {"user_id": user_id}))
    summary = job.get("summary") or {}
    kind = job.get("kind")
    if kind == "download" and summary.get("requires_replace_typed") and replace_confirmed != "REPLACE":
        raise SyncRefused(Refusal("replace_required", {}))
    if kind == "sync" and summary.get("mass_delete") and not allow_mass_delete:
        raise SyncRefused(Refusal(REFUSAL_MASS_DELETE, summary.get("mass_delete_detail") or {}))

    job["start_token"] = uuid.uuid4().hex
    job["allow_mass_delete"] = bool(allow_mass_delete)
    save_job(user_id, job)
    token = job["start_token"]
    thread = threading.Thread(target=_run_phase2, args=(app, user_id, kind, token, bool(allow_mass_delete)),
                              name=f"sync-phase2-{kind}-{user_id}", daemon=True)
    thread.start()
    return job


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


# --- Worker entry points ------------------------------------------------------

def _run_phase1(app, user_id: int, kind: str, start_token: str) -> None:
    with app.app_context():
        db = get_db()
        user = db.get(DbUser, int(user_id))
        if user is None:
            _mark_failed(user_id, start_token, Refusal(REFUSAL_NOT_PAIRED, {"user_id": user_id}))
            return
        try:
            with sync_lock(user_id):
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


def _run_phase2(app, user_id: int, kind: str, start_token: str, allow_mass_delete: bool) -> None:
    with app.app_context():
        db = get_db()
        user = db.get(DbUser, int(user_id))
        if user is None:
            _mark_failed(user_id, start_token, Refusal(REFUSAL_NOT_PAIRED, {"user_id": user_id}))
            return
        try:
            with sync_lock(user_id):
                if kind == "download":
                    _phase2_download(db, user, start_token)
                else:
                    _phase2_sync(db, user, start_token, allow_mass_delete)
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
    client = _client_for(db, user)
    needed = files_needed(plan, user)
    _touch(uid, start_token, phase=PHASE_DOWNLOAD_FILES, progress=0.1, phase_label=PHASE_DOWNLOAD_FILES)
    _fetch_files(client, user, needed, uid, start_token)

    _touch(uid, start_token, phase=PHASE_APPLYING, progress=0.8, phase_label=PHASE_APPLYING)
    result = apply_download(db, plan, user=user)
    db.commit()
    move_replaced_files(db, plan, user)
    db.commit()
    staged = _staged_for(plan, user)
    commit_staged_files(db, result, staged, user=user)
    db.commit()

    _touch(uid, start_token, phase=PHASE_DONE, progress=1.0, phase_label=PHASE_DONE,
           result={"down": dict(result.created),
                   "replaced_files": result.replaced_files,
                   "pending_files": len(result.pending_files or [])})


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
    mass_delete_detail = push_mass_delete_detail
    if pull_mass_delete:
        for r in pull_plan.refusals:
            if getattr(r, "code", "") == REFUSAL_MASS_DELETE:
                mass_delete_detail = dict(r.detail or {})
                break

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
    summary = {
        "kind": "sync",
        "push": push_summary,
        "pull": pull_plan.summary(),
        "up": {"created": _sum_counts(push_summary.get("created")),
               "updated": _sum_counts(push_summary.get("updated")),
               "deleted": up_deleted},
        "down": {"created": _sum_counts(pull_plan.created),
                 "updated": _sum_counts(pull_plan.updated),
                 "deleted": _sum_counts(pull_plan.deleted)},
        "deletions": _sum_counts(pull_plan.deleted),
        "mass_delete": push_mass_delete or pull_mass_delete,
        "mass_delete_detail": mass_delete_detail,
        "counter_rev": counter_rev(db, uid),
        "rev_at_export": rev_at_export,
        "offset_seconds": offset,
        "session_id": push_session.get("session_id") if push_session else None,
        "fingerprint": push_session.get("fingerprint") if push_session else None,
        "files_needed": push_session.get("files_needed") if push_session else [],
        "hub_missing": push_session.get("hub_missing") if push_session else [],
        "files": {"up": len((push_session.get("files_needed") or []) if push_session else []),
                  "down": pull_plan.summary()["files"]["needed"],
                  "down_bytes": pull_plan.summary()["files"]["bytes"]},
    }
    _touch(uid, start_token, phase=PHASE_AWAITING, progress=0.9, summary=summary,
           phase_label=PHASE_AWAITING)


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


def _phase2_sync(db, user, start_token: str, allow_mass_delete: bool) -> None:
    uid = user.id
    job = _touch(uid, start_token, phase=PHASE_BACKUP, progress=0.0, phase_label=PHASE_BACKUP)
    summary = job.get("summary") or {}
    if counter_rev(db, uid) != summary.get("counter_rev"):
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"user_id": uid}))

    backup_local(db, user)
    client = _client_for(db, user)
    offset = summary.get("offset_seconds", 0.0)
    rev_at_export = summary.get("rev_at_export", 0)
    server_cursor = _state_cursor(db, uid)

    # --- push (plan → upload → apply, re-planned on plan_stale/sync_running) ---
    def _push():
        push_payload = _load_artifact(uid, "push_payload.json") or export_changes(
            db, user, since_rev=int(rev_at_export), side="local")
        push_resp = client.push_plan(push_payload, offset, allow_mass_delete=allow_mass_delete)
        refs = _refs_from_needed(push_resp.get("files_needed") or [],
                                 push_payload.get("files") or {})
        _upload_files(client, user, refs, uid, start_token, push_resp.get("session_id"))
        return client.push_apply(push_resp.get("session_id"), push_resp.get("fingerprint"))

    hub_result = _retry_transient(_push)
    push_revs = hub_result.get("result", {}).get("push_revs") or []
    deferred = hub_result.get("result", {}).get("deferred") or []
    pending_files = hub_result.get("result", {}).get("pending_files") or []

    record_push_accepted(db, user, rev_at_export, deferred=deferred,
                         commit=CommitReport(pending=pending_files))
    db.commit()

    # --- pull (fetch → plan → apply), re-fetched on plan_stale/sync_running ---
    pull_payload = _retry_transient(
        lambda: client.changes(since_rev=server_cursor, exclude_revs=push_revs, with_files=True))
    pull_plan = plan_merge(db, user, pull_payload, side="local", offset_seconds=offset,
                           allow_mass_delete=allow_mass_delete)
    if pull_plan.refused:
        non_mass = _first_non_mass_delete(pull_plan.refusals)
        if non_mass is not None:
            raise SyncRefused(non_mass)
        raise SyncRefused(Refusal(REFUSAL_MASS_DELETE, summary.get("mass_delete_detail") or {}))

    final_deletions = _sum_counts(pull_plan.deleted)
    if final_deletions > int(summary.get("deletions", 0)):
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"reason": "more_deletions"}))

    rev_at_export_pull = counter_rev(db, uid)
    result = apply_merge(db, pull_plan, user=user)
    server_rev = int(pull_payload.get("rev") or 0)
    # The pull's cursor also advances only once its files are placed, so
    # commit_staged_files carries this rev the same way a download carries
    # snapshot_rev.
    result.server_rev = server_rev
    record_pull_applied(db, user, result, server_rev, rev_at_export_pull)
    db.commit()

    needed = files_needed(pull_plan, user)
    _fetch_files(client, user, needed, uid, start_token)
    staged = _staged_for(pull_plan, user)
    commit_staged_files(db, result, staged, user=user)
    db.commit()

    _touch(uid, start_token, phase=PHASE_DONE, progress=1.0, phase_label=PHASE_DONE,
           result={"up": {"created": _sum_counts(hub_result.get("result", {}).get("created")),
                          "updated": _sum_counts(hub_result.get("result", {}).get("updated")),
                          "deleted": _sum_counts(hub_result.get("result", {}).get("deleted"))},
                   "down": {"created": _sum_counts(result.created),
                            "updated": _sum_counts(result.updated),
                            "deleted": _sum_counts(result.deleted)},
                   "pending_files": len(result.pending_files or [])})


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
        _fetch_file(client, user, ref)
        _touch(uid, start_token, progress=0.1 + 0.7 * (i + 1) / total,
               phase_label=PHASE_DOWNLOAD_FILES)


def _fetch_file(client, user, ref) -> None:
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
                "error": job.get("error")}
    if phase in _RUNNING_PHASES and _is_stale(job):
        job = dict(job, phase=PHASE_FAILED,
                   error={"code": "interrupted", "detail": {}})
        phase = PHASE_FAILED
    running = phase in _RUNNING_PHASES
    return {"running": running, "phase": phase, "progress": job.get("progress"),
            "summary": job.get("summary"), "result": job.get("result"),
            "error": job.get("error")}
