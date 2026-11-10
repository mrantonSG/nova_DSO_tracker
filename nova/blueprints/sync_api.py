"""Instance sync — hub-side HTTP API (step 5).

Token-only. Every request carries a bearer token in the Authorization header,
there is no session cookie, CSRF does not apply, and every query is scoped to
the token's user — never to g.db_user, which a gate-exempt request does not
set. The view layer merges nothing: it hands the engine its own payloads and
answers with the engine's own refusal codes as JSON. No user-facing text is
produced here; the client in step 6 translates codes.

The push is three requests (plan → upload chunks → apply) because a large file
cannot ride in one request inside gunicorn's 30 s timeout. Between them the
state lives on disk (nova.sync_transport), and the apply re-plans from the
stored payload and compares fingerprints, so a push whose account moved during
the upload is refused as plan_stale instead of overwriting the newer edit.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta
from functools import wraps
from typing import Optional

from flask import Blueprint, Response, g, jsonify, request, send_file
from sqlalchemy import select

from nova.config import APP_VERSION
from nova.helpers import get_db, shared_log_paths
from nova.sync_engine import (
    MAX_SYNC_FILE_BYTES, FileRef, MergeResult, PLAN_ORDER, REFUSAL_BAD_FILE_REF,
    REFUSAL_BAD_FORMAT, REFUSAL_FILE_TOO_LARGE, REFUSAL_FILE_VERIFY,
    REFUSAL_PLAN_STALE, REFUSAL_SERVER_UNTOUCHED, ROLE_NOTE_IMAGE, Refusal,
    StagedFile, SyncRefused, TABLE_MODEL, _FILE_SLOTS, _HEX64, _ref_summary,
    _role_ext_ok, _row_by_key, account_marker_rev, apply_merge, collect_files,
    commit_staged_files, counter_rev, export_changes, export_files,
    file_cap, file_source_path, files_needed, missing_local_files, plan_merge,
    record_to_payload, row_key, server_untouched, staged_index, sync_lock, to_iso,
)
from nova.sync_time import utc_now
from nova.sync_transport import (
    AUTH_HEADER, CHUNK_BYTES, CODE_PROTOCOL_MISMATCH, MAX_CHUNK_BYTES,
    MAX_JSON_BYTES, PROTOCOL_HEADER, PROTOCOL_VERSION,
    PUSH_SESSION_TTL_SECONDS, SNAPSHOT_DEFAULT_LIMIT, SNAPSHOT_MAX_LIMIT,
    authenticate, clear_push_session, drop_uploads, finalize_upload,
    hub_offset_from_client, load_push_session, refusal_payload,
    save_push_session, snapshot_account_yaml,
    status_for, sweep_uploads, touch_last_used, upload_received, write_chunk,
)

sync_api_bp = Blueprint('sync_api', __name__)


# --- Response helpers ---------------------------------------------------------

def _ok(data: dict) -> Response:
    body = {"protocol": PROTOCOL_VERSION}
    body.update(data)
    return jsonify(body)


def _refuse(refusals, status: Optional[int] = None) -> Response:
    if isinstance(refusals, Refusal):
        refusals = [refusals]
    if status is None:
        status = status_for(refusals[0])
    return jsonify(refusal_payload(refusals)), status


@sync_api_bp.errorhandler(SyncRefused)
def _engine_refused(exc):
    """A refusal the engine raises rather than returns still answers with its
    own code: sync_lock() raises sync_running when another worker holds the
    account, and that is a 409 the client can retry, not a 500."""
    return _refuse(exc.refusal)


def _bad(reason: str, detail: Optional[dict] = None) -> Response:
    payload = {"reason": reason}
    payload.update(detail or {})
    return _refuse(Refusal(REFUSAL_BAD_FORMAT, payload), 400)


def _json_body():
    """The request's JSON body, or a refusal. Bounds the size itself: the app's
    MAX_CONTENT_LENGTH answers an oversized body with an HTML 413."""
    length = request.content_length or 0
    if length > MAX_JSON_BYTES:
        return None, Refusal(REFUSAL_BAD_FORMAT, {"reason": "payload_too_large",
                                                  "limit": MAX_JSON_BYTES})
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, Refusal(REFUSAL_BAD_FORMAT, {"reason": "not_json"})
    return body, None


def _int_header(name: str) -> Optional[int]:
    raw = request.headers.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _result_summary(result) -> dict:
    """A MergeResult as JSON. The push response is what the pusher's cursor
    follows, so the fields it needs are the ones that hold it back."""
    return {
        "created": dict(result.created),
        "updated": dict(result.updated),
        "deleted": dict(result.deleted),
        "resurrected": [r._asdict() for r in result.resurrected],
        "skipped_at_apply": [r._asdict() for r in result.skipped_at_apply],
        "r_verbatim": result.r_verbatim,
        "r_changed": result.r_changed,
        "push_revs": list(result.push_revs),
        "deferred": [list(entry) for entry in result.deferred],
        "pending_files": [list(entry) for entry in result.pending_files],
        "placed_files": [list(entry) for entry in result.placed_files],
        "skipped_files": [_ref_summary(r) for r in result.skipped_files],
        "orphaned_files": [_ref_summary(r) for r in result.orphaned_files],
    }


# --- Authentication -----------------------------------------------------------

def _sync_auth(view):
    """Protocol check, then the bearer token. Both before the view runs.

    The protocol is checked first so a client built for a different version is
    told so without a database read, and so no endpoint can forget the check.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        sent = request.headers.get(PROTOCOL_HEADER, "")
        try:
            version = int(sent)
        except (TypeError, ValueError):
            version = None
        if version != PROTOCOL_VERSION:
            return _refuse(Refusal(CODE_PROTOCOL_MISMATCH,
                                   {"supported": PROTOCOL_VERSION,
                                    "found": sent or None}), 400)

        db = get_db()
        token, user, refusal = authenticate(db, request.headers.get(AUTH_HEADER))
        if refusal is not None:
            return _refuse(refusal)
        touch_last_used(db, token)
        g.sync_token = token
        g.sync_user = user
        return view(*args, **kwargs)

    return wrapper


# --- Handshake ----------------------------------------------------------------

@sync_api_bp.route('/api/sync/v1/hello', methods=['POST'])
@_sync_auth
def hello() -> Response:
    """Who am I, what time is it here, and where does this account stand.

    server_time is the reference the client measures its clock offset against;
    untouched and seeded are what the client needs to pick a direction before
    it exports anything (a hub holding nothing but template data refuses to be
    asked for a snapshot, and a local that never downloaded may not push).
    """
    db = get_db()
    user = g.sync_user
    token = g.sync_token
    return _ok({
        "server_time": to_iso(utc_now()),
        "app_version": APP_VERSION,
        "account": {"user_id": user.id, "username": user.username},
        "server_rev": counter_rev(db, user.id),
        "untouched": server_untouched(db, user.id),
        "seeded": account_marker_rev(db, user.id, "seeded") > 0,
        "token": {"label": token.label,
                  "created_at": to_iso(token.created_at),
                  "last_used_at": to_iso(token.last_used_at)},
        "limits": {"payload_bytes": MAX_JSON_BYTES,
                   "chunk_bytes": CHUNK_BYTES,
                   "snapshot_page": SNAPSHOT_DEFAULT_LIMIT,
                   "file_caps": dict(MAX_SYNC_FILE_BYTES)},
    })


# --- Pull: changes and snapshot ----------------------------------------------

@sync_api_bp.route('/api/sync/v1/changes', methods=['POST'])
@_sync_auth
def changes() -> Response:
    """Everything this account changed above a cursor, as an engine payload.

    The payload is handed over untouched: the client feeds it straight to
    plan_merge. exclude_revs is how a pusher avoids receiving back what it just
    sent (the hub's push response names those revs).
    """
    body, refusal = _json_body()
    if refusal is not None:
        return _refuse(refusal)

    since = body.get("since_rev", 0)
    if isinstance(since, bool) or not isinstance(since, int) or since < 0:
        return _bad("since_rev")
    exclude = body.get("exclude_revs") or []
    if not isinstance(exclude, list) or not all(
            isinstance(rev, int) and not isinstance(rev, bool) for rev in exclude):
        return _bad("exclude_revs")

    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        payload = export_changes(db, user, since, side="server",
                                 exclude_revs=exclude,
                                 with_files=bool(body.get("with_files", True)))
    return _ok({"payload": payload})


@sync_api_bp.route('/api/sync/v1/snapshot', methods=['POST'])
@_sync_auth
def snapshot() -> Response:
    """One page of the whole account, for a first download or a repair.

    Paging is transport-level: plan_download takes a whole snapshot, so the
    client concatenates the pages of every table into one object. Each page
    carries the file manifest for its own records, which is what the engine's
    file-token validation needs, and the current rev, so a page set taken
    across a change can be recognised and thrown away.
    """
    body, refusal = _json_body()
    if refusal is not None:
        return _refuse(refusal)

    table = body.get("table")
    model = TABLE_MODEL.get(table) if isinstance(table, str) else None
    if model is None:
        return _bad("table", {"table": table})
    try:
        after_id = int(body.get("after_id", 0))
        limit = int(body.get("limit", SNAPSHOT_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return _bad("after_id")
    limit = max(1, min(limit, SNAPSHOT_MAX_LIMIT))

    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        if server_untouched(db, user.id):
            return _refuse(Refusal(REFUSAL_SERVER_UNTOUCHED,
                                   {"seeded_rev": account_marker_rev(db, user.id, "seeded")}))
        # One row beyond the page decides `done`. A page that is exactly full
        # cannot tell "the table ends here" from "there is another page", and
        # the alternative is sending the client a page it did not ask for
        # purely to learn it is empty — one wasted request per table on the hub.
        page = _page_rows(db, model, user.id, after_id, limit + 1)
        done = len(page) <= limit
        rows = page[:limit]
        shared = shared_log_paths(db)
        records = [record_to_payload(model, row, user=user, shared_paths=shared)
                   for row in rows]
        refs = collect_files(db, user, rows={model: rows}, shared_paths=shared)
        manifest = export_files(db, user, refs)
        skipped = [_ref_summary(r) for r in refs if r.missing]
        rev = counter_rev(db, user.id)
    return _ok({
        "server_rev": rev,
        "table": table,
        "records": records,
        "files": manifest,
        "skipped_files": skipped,
        "next_after_id": (rows[-1].id if rows else after_id),
        "done": done,
        "tables": [m.__tablename__ for m in PLAN_ORDER],
    })


def _page_rows(db, model, user_id: int, after_id: int, limit: int) -> list:
    """A keyset page of one table, ordered by primary key.

    Keyset rather than offset paging: a row inserted while a client walks the
    snapshot must not shift the page boundaries.
    """
    return list(db.scalars(
        select(model).where(model.user_id == user_id, model.id > after_id)
        .order_by(model.id).limit(limit)))


def _file_response(path: str) -> Response:
    """Serve `path`, honouring a single byte Range even though this is POST.

    Werkzeug's ``send_file(conditional=True)`` only applies Range to GET/HEAD,
    and every sync endpoint is POST (token auth). The client resumes a dropped
    download with a ``Range: bytes=N-`` header, so it is handled here by hand:
    one open-ended or closed range is answered 206, anything else falls back to
    the full file.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    rng = request.range
    ranges = list(getattr(rng, "ranges", []) or []) if rng is not None else []
    if getattr(rng, "units", "") == "bytes" and len(ranges) == 1:
        start, end = ranges[0]
        start = 0 if start is None else int(start)
        end = size - 1 if end is None else int(end)
        if start >= size:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        end = max(start, min(end, size - 1))
        length = end - start + 1
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(length)
        return Response(data, status=206,
                        headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                 "Content-Length": str(length),
                                 "Accept-Ranges": "bytes"})
    return send_file(path, conditional=True)


# --- Pull: one file -----------------------------------------------------------

@sync_api_bp.route('/api/sync/v1/file', methods=['POST'])
@_sync_auth
def fetch_file() -> Response:
    """The bytes of one file, by the record that references it.

    The request names a record and a slot, never a path: the row is loaded
    scoped to the token's user, its current value is re-derived here, and the
    file is served from the path that value resolves to. A digest the record
    does not reference is refused, so one account cannot name another's bytes
    even with a correct digest.
    """
    body, refusal = _json_body()
    if refusal is not None:
        return _refuse(refusal)

    table = body.get("table")
    model = TABLE_MODEL.get(table) if isinstance(table, str) else None
    key = body.get("key")
    slot = body.get("slot")
    digest = body.get("sha256") or ""
    if model is None or not isinstance(key, str) or not key or not isinstance(slot, str):
        return _refuse(Refusal(REFUSAL_BAD_FILE_REF, {"reason": "not_a_record"}), 404)

    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        row = _row_by_key(db, model, user.id, key)
        if row is None:
            return _refuse(Refusal(REFUSAL_BAD_FILE_REF,
                                   {"table": table, "key": key}), 404)
        refs = collect_files(db, user, rows={model: [row]})
        ref = next((r for r in refs
                    if r.slot == slot and not r.missing
                    and (not digest or r.sha256 == digest)), None)
        if ref is None or (digest and digest != ref.sha256):
            return _refuse(Refusal(REFUSAL_BAD_FILE_REF,
                                   {"table": table, "key": key, "slot": slot}), 404)
        path = file_source_path(ref, user)
        if not path:
            return _refuse(Refusal(REFUSAL_BAD_FILE_REF,
                                   {"table": table, "key": key, "slot": slot,
                                    "reason": "not_on_disk"}), 404)
        response = _file_response(path)
        response.headers["X-Nova-Sha256"] = ref.sha256
    response.headers[PROTOCOL_HEADER] = str(PROTOCOL_VERSION)
    return response


# --- Push: stage 1, plan ------------------------------------------------------

@sync_api_bp.route('/api/sync/v1/push/plan', methods=['POST'])
@_sync_auth
def push_plan() -> Response:
    """Plan a push and open a session for its uploads.

    The clock offset the client sends is measured on the client (its clock
    minus the hub's); from the hub's side the peer is the client, so it is
    negated here before the engine sees it. The plan's fingerprint is stored
    with the payload: the apply re-plans and compares, which is what refuses a
    push whose account moved during the upload.
    """
    body, refusal = _json_body()
    if refusal is not None:
        return _refuse(refusal)

    payload = body.get("payload")
    if not isinstance(payload, dict):
        return _bad("payload")
    if "clock_offset_seconds" not in body:
        return _bad("clock_offset_seconds")
    try:
        client_offset = float(body["clock_offset_seconds"])
    except (TypeError, ValueError):
        return _bad("clock_offset_seconds")
    if client_offset != client_offset or client_offset in (float("inf"), float("-inf")):
        return _bad("clock_offset_seconds")

    db = get_db()
    user = g.sync_user
    hub_offset = hub_offset_from_client(client_offset)
    with sync_lock(user.id):
        sweep_uploads(user.id)
        plan = plan_merge(db, user, payload, side="server",
                          offset_seconds=hub_offset,
                          allow_mass_delete=bool(body.get("allow_mass_delete", False)))
        if plan.refused:
            return _refuse(plan.refusals)

        refs = list(plan.placements or plan.files)
        needed = files_needed(plan, user)
        session_id = uuid.uuid4().hex
        session = {
            "protocol": PROTOCOL_VERSION,
            "session_id": session_id,
            "token_id": g.sync_token.id,
            "user_id": user.id,
            "created_at": to_iso(utc_now()),
            "expires_at": to_iso(utc_now() + _ttl()),
            "hub_offset_seconds": hub_offset,
            "allow_mass_delete": bool(body.get("allow_mass_delete", False)),
            "fingerprint": plan.fingerprint,
            "payload": payload,
            # The upload allowlist: a digest the plan does not name is refused,
            # and the role/ext of each are decided here, never by the request.
            "file_refs": [{"role": r.role, "table": r.table, "key": r.key,
                           "slot": r.slot, "sha256": r.sha256, "ext": r.ext,
                           "size": int(r.size or 0), "name": r.name}
                          for r in refs if r.sha256 and not r.missing],
            "files_needed": [[r.table, r.key, r.slot, r.sha256] for r in needed],
        }
        save_push_session(user.id, session)
        hub_missing = missing_local_files(db, user)

    return _ok({
        "session_id": session_id,
        "expires_at": session["expires_at"],
        "plan": plan.summary(),
        "fingerprint": plan.fingerprint,
        "files_needed": session["files_needed"],
        "hub_missing": [[r.table, r.key, r.slot, r.sha256] for r in hub_missing],
    })


def _ttl():
    return timedelta(seconds=PUSH_SESSION_TTL_SECONDS)


# --- Push: stage 2, the file chunks ------------------------------------------

@sync_api_bp.route('/api/sync/v1/push/file', methods=['POST'])
@_sync_auth
def push_file() -> Response:
    """One chunk of one file the open session's plan asked for.

    Role, extension and total size come from the session's own allowlist, not
    from the request, so the request contributes a digest, an offset and bytes.
    Offset -1 asks how much is already held, which is how a client resumes
    after a dropped request.
    """
    session_id = request.headers.get("X-Nova-Session", "")
    digest = (request.headers.get("X-Nova-Sha256") or "").lower()
    offset = _int_header("X-Nova-Offset")

    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        session = load_push_session(user.id)
        if session is None or session.get("session_id") != session_id:
            return _refuse(Refusal(REFUSAL_BAD_FILE_REF, {"reason": "no_session"}), 409)
        entry = next((e for e in session.get("file_refs", [])
                      if e.get("sha256") == digest), None)
        if entry is None:
            return _refuse(Refusal(REFUSAL_BAD_FILE_REF,
                                   {"reason": "not_in_plan", "sha256": digest}), 409)
        if offset == -1:
            return _ok({"received": upload_received(user.id, digest, entry["ext"]),
                        "expected": entry["size"] or None, "complete": False})
        result, refusal = _receive_chunk(
            user, role=entry["role"], ext=entry["ext"], sha256=digest,
            total=entry["size"] or None, offset=offset)
        if refusal is not None:
            return _refuse(refusal)

    return _ok(result)


def _receive_chunk(user, *, role: str, ext: str, sha256: str,
                   total: Optional[int], offset: Optional[int],
                   slot: str = "", table: str = "", key: str = ""):
    """Validate and append one chunk. Returns (body, refusal).

    Every check the engine makes about a file's identity is made here too, and
    before a byte is written: the digest must be a digest, the extension must
    belong to the role, and the declared total must fit the role's cap.
    """
    cap = file_cap(role)
    if not _HEX64.fullmatch(sha256 or ""):
        return None, Refusal(REFUSAL_BAD_FILE_REF, {"reason": "sha256", "sha256": sha256})
    if not _role_ext_ok(role, ext):
        return None, Refusal(REFUSAL_BAD_FILE_REF, {"role": role, "ext": ext})
    if total is None:
        return None, Refusal(REFUSAL_BAD_FILE_REF, {"reason": "total"})
    if int(total) > cap:
        return None, Refusal(REFUSAL_FILE_TOO_LARGE,
                             {"total": int(total), "cap": cap, "role": role})
    if offset is None or offset < 0:
        return None, Refusal(REFUSAL_BAD_FILE_REF, {"reason": "offset"})

    data, refusal = _read_chunk()
    if refusal is not None:
        return None, refusal
    chunk_sha = (request.headers.get("X-Nova-Chunk-Sha256") or "").lower()
    if chunk_sha and hashlib.sha256(data).hexdigest() != chunk_sha:
        return None, Refusal(REFUSAL_FILE_VERIFY, {"reason": "chunk"})

    try:
        received = write_chunk(user.id, sha256, ext, int(offset), data)
    except ValueError:
        return None, Refusal(REFUSAL_BAD_FILE_REF, {"reason": "offset_gap",
                                                    "received": upload_received(user.id, sha256, ext)})
    if received > int(total):
        return None, Refusal(REFUSAL_BAD_FILE_REF, {"reason": "too_much",
                                                    "received": received, "total": int(total)})
    if received == int(total):
        if not finalize_upload(user.id, sha256, ext):
            return None, Refusal(REFUSAL_FILE_VERIFY,
                                 {"sha256": sha256, "reason": "digest"})
        return {"received": received, "expected": int(total), "complete": True}, None
    return {"received": received, "expected": int(total), "complete": False}, None


def _read_chunk():
    length = request.content_length or 0
    if length <= 0:
        return b"", Refusal(REFUSAL_BAD_FORMAT, {"reason": "empty_chunk"})
    if length > MAX_CHUNK_BYTES:
        return b"", Refusal(REFUSAL_BAD_FORMAT, {"reason": "chunk_too_large",
                                                 "limit": MAX_CHUNK_BYTES})
    data = request.get_data(cache=False, as_text=False)
    if len(data) != length:
        return b"", Refusal(REFUSAL_BAD_FORMAT, {"reason": "short_chunk"})
    return data, None


# --- Push: stage 3, apply -----------------------------------------------------

@sync_api_bp.route('/api/sync/v1/push/apply', methods=['POST'])
@_sync_auth
def push_apply() -> Response:
    """Apply the session's stored payload, then place its staged files.

    The payload is re-planned rather than replayed, because the engine's plan
    describes rows that may have moved since the upload started. The fresh
    fingerprint is compared with the one taken at plan time: equal means the
    account is untouched since, and the difference is reported as plan_stale
    rather than absorbed.
    """
    body, refusal = _json_body()
    if refusal is not None:
        return _refuse(refusal)
    session_id = body.get("session_id")
    fingerprint = body.get("fingerprint")
    if not isinstance(session_id, str) or not isinstance(fingerprint, str):
        return _bad("session_id")

    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        session = load_push_session(user.id)
        if session is None or session.get("session_id") != session_id:
            return _refuse(Refusal(REFUSAL_PLAN_STALE, {"reason": "session"}), 409)
        if session.get("fingerprint") != fingerprint:
            return _refuse(Refusal(REFUSAL_PLAN_STALE, {"reason": "fingerprint"}), 409)

        plan = plan_merge(db, user, session["payload"], side="server",
                          offset_seconds=session.get("hub_offset_seconds", 0.0),
                          allow_mass_delete=session.get("allow_mass_delete", False))
        if plan.refused:
            clear_push_session(user.id)
            return _refuse(plan.refusals)
        if plan.fingerprint != session["fingerprint"]:
            clear_push_session(user.id)
            return _refuse(Refusal(REFUSAL_PLAN_STALE, {"reason": "changed"}), 409)

        # Data safety rule 1: the account as it stands, before this push
        # touches it. A push that decides nothing about a row writes none.
        if plan.has_verbatim or plan.has_changed:
            snapshot_account_yaml(user)

        try:
            result = apply_merge(db, plan, user=user)
            db.commit()
        except SyncRefused as exc:
            db.rollback()
            clear_push_session(user.id)
            return _refuse(exc.refusal)

        staged = _staged_for(db, user, plan)
        commit_staged_files(db, result, staged, user=user)
        db.commit()
        clear_push_session(user.id)

    return _ok({"plan": plan.summary(), "result": _result_summary(result)})


def _staged_for(db, user, plan) -> list:
    """The staged copies this plan's placements need, as StagedFile objects."""
    index = staged_index(user, verify=True)
    return [StagedFile(ref=ref, path=index[ref.sha256], local_name=ref.name)
            for ref in (plan.placements or plan.files)
            if ref.sha256 and not ref.missing and ref.sha256 in index]


@sync_api_bp.route('/api/sync/v1/push/cancel', methods=['POST'])
@_sync_auth
def push_cancel() -> Response:
    """Give up on a push. Idempotent: cancelling nothing succeeds.

    Staged files are left alone: they are keyed by digest, so the next attempt
    reuses them instead of uploading them again.
    """
    body, refusal = _json_body()
    if refusal is not None:
        return _refuse(refusal)
    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        session = load_push_session(user.id)
        cancelled = bool(session)
        clear_push_session(user.id)
        drop_uploads(user.id)
    return _ok({"cancelled": cancelled})


# --- Repair: the hub is missing a file its own records name -------------------

@sync_api_bp.route('/api/sync/v1/repair', methods=['POST'])
@_sync_auth
def repair() -> Response:
    """Upload a file this hub's records reference but its disk lacks.

    The mirror of `hub_missing` in the push response. No push session: this is
    a single file placed into a row that already exists, and the placement
    writes no rev — a file name is instance-local, so placing one is not a
    change to sync.
    """
    table = request.headers.get("X-Nova-Table", "")
    key = request.headers.get("X-Nova-Key", "")
    slot = request.headers.get("X-Nova-Slot", "")
    digest = (request.headers.get("X-Nova-Sha256") or "").lower()
    ext = (request.headers.get("X-Nova-Ext") or "").lower()
    offset = _int_header("X-Nova-Offset")
    total = _int_header("X-Nova-Total")

    model = TABLE_MODEL.get(table)
    if model is None or not key or not slot:
        return _refuse(Refusal(REFUSAL_BAD_FILE_REF, {"reason": "not_a_record"}), 404)
    role = _repair_role(model, slot, ext)
    if role is None:
        return _refuse(Refusal(REFUSAL_BAD_FILE_REF,
                               {"table": table, "slot": slot}), 404)

    db = get_db()
    user = g.sync_user
    with sync_lock(user.id):
        row = _row_by_key(db, model, user.id, key)
        if row is None:
            return _refuse(Refusal(REFUSAL_BAD_FILE_REF,
                                   {"table": table, "key": key}), 404)
        if offset == -1:
            return _ok({"received": upload_received(user.id, digest, ext),
                        "expected": total, "complete": False})
        result, refusal = _receive_chunk(user, role=role, ext=ext, sha256=digest,
                                         total=total, offset=offset)
        if refusal is not None:
            return _refuse(refusal)
        if result["complete"]:
            ref = _repair_ref(model, row, slot, role, digest, ext, int(total))
            staged = _staged_for_repair(user, ref)
            if staged is None:
                return _refuse(Refusal(REFUSAL_FILE_VERIFY, {"sha256": digest}))
            placement = MergeResult(files_expected=[ref])
            commit_staged_files(db, placement, [staged], user=user)
            db.commit()
            result["placed"] = [list(entry) for entry in placement.placed_files]
            result["pending"] = [list(entry) for entry in placement.pending_files]
    return _ok(result)


def _repair_role(model: type, slot: str, ext: str) -> Optional[str]:
    """The role a repair may write into one slot, or None when it may not.

    Derived from the model and the slot, never from the request: a repair can
    only fill a column the record actually has, or the one rich-text image
    slot, and only with a file whose extension the role allows.
    """
    slots = _FILE_SLOTS.get(model, {})
    if slot in slots:
        role = slots[slot][0]
    elif slot == "note_img" and model in _FILE_SLOTS:
        role = ROLE_NOTE_IMAGE
    else:
        return None
    return role if _role_ext_ok(role, ext) else None


def _repair_ref(model, row, slot, role, digest, ext, size):
    return FileRef(role=role, table=model.__tablename__, key=row_key(model, row),
                   slot=slot, sha256=digest, size=int(size or 0), ext=ext)


def _staged_for_repair(user, ref) -> Optional[StagedFile]:
    path = staged_index(user, verify=True).get(ref.sha256)
    if not path:
        return None
    return StagedFile(ref=ref, path=path, local_name=ref.name)
