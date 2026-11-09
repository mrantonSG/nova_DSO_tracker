"""Instance sync — hub-side transport (step 5).

Everything the hub needs that is not the merge engine: bearer-token
authentication, the on-disk push session that carries one push between
requests, the chunked upload accumulator, the pre-push account snapshot, and
the refusal shapes the sync API answers with.

No in-memory state between requests. Two Gunicorn workers serve the same
account, so anything a request leaves behind for the next one lives on disk
under the instance directory, and per-user mutual exclusion comes from the
engine's sync_lock() — an OS advisory lock, so it holds across processes and
across a worker restart.

The transport codes below are the only vocabulary this layer adds; everything
else is the engine's. They are transport-level, not merge-level, because a
mismatched protocol version or a bad token never reaches the engine at all.
Like every engine refusal they carry no user-facing text: the client in step 6
translates them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
from typing import Optional

from nova.config import BACKUP_DIR, CACHE_DIR
from nova.helpers import _FileLock
from nova.models import INSTANCE_PATH, DbUser, SyncToken
from nova.sync_engine import Refusal, parse_datetime
from nova.sync_time import utc_now

logger = logging.getLogger(__name__)

# --- Protocol ----------------------------------------------------------------

# The transport protocol version, independent of the payload format the engine
# stamps into an envelope (PAYLOAD_FORMAT). Every request and every response
# carries it; a mismatch is refused before anything else happens.
PROTOCOL_VERSION = 1
PROTOCOL_HEADER = "X-Nova-Sync-Protocol"
AUTH_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "

# --- Transport refusal codes --------------------------------------------------

CODE_PROTOCOL_MISMATCH = "protocol_mismatch"
CODE_TOKEN_INVALID = "token_invalid"
CODE_TOKEN_REVOKED = "token_revoked"
CODE_GUEST_ACCOUNT = "guest_account"

TRANSPORT_CODES = (CODE_PROTOCOL_MISMATCH, CODE_TOKEN_INVALID,
                   CODE_TOKEN_REVOKED, CODE_GUEST_ACCOUNT)

# The account a sync token may never belong to: it is shared, and its rows are
# seeded demo data.
GUEST_USERNAME = "guest_user"

# --- Size limits --------------------------------------------------------------

# A JSON body (a push payload, a snapshot page) may be this large. The app's
# MAX_CONTENT_LENGTH is 500 MB and answers an over-size body with HTML, so the
# sync API bounds its own requests and answers with a code.
MAX_JSON_BYTES = 16 * 1024 * 1024
# One upload chunk. The ceiling is gunicorn's 30 s timeout: the timeout covers
# the whole request, so the chunk size is what bounds how long a request takes
# on a slow uplink.
CHUNK_BYTES = 1024 * 1024
MAX_CHUNK_BYTES = 2 * 1024 * 1024

SNAPSHOT_DEFAULT_LIMIT = 500
SNAPSHOT_MAX_LIMIT = 2000

# --- On-disk state lifetimes --------------------------------------------------

PUSH_SESSION_TTL_SECONDS = 30 * 60
UPLOAD_TTL_SECONDS = 30 * 60
PUSH_BACKUP_KEEP = 5

# instance/… — the accumulator is deliberately OUTSIDE the engine's staging
# directory: staged_index() parses every name there as "<sha256>.<ext>", so a
# half-written accumulator file would be mis-read as a finished file for its
# digest. Same filesystem, so the final move into staging is an atomic rename.
SYNC_UPLOAD_DIRNAME = "sync_uploads"
SYNC_BACKUP_DIRNAME = "sync"

# last_used_at is informational, so it is written at most this often: a push
# makes many requests and every one of them would otherwise be a write on the
# same row from two workers.
LAST_USED_THROTTLE_SECONDS = 60

# --- Tokens -------------------------------------------------------------------

def new_token() -> str:
    """A fresh bearer token: 256 bits, URL-safe, shown once."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """The only form of a token this server stores or looks up by."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(db, header_value: Optional[str]):
    """Resolve an Authorization header to (SyncToken, DbUser) or a refusal.

    One code for "absent" and "unknown", so the endpoint is not an oracle for
    which hashes exist. A revoked token and a guest account are named
    separately: both tell the caller something it can act on, and neither
    reveals a token.

    The plaintext token is never stored, logged or compared: the lookup is by
    digest.
    """
    if not header_value or not header_value.startswith(BEARER_PREFIX):
        return None, None, Refusal(CODE_TOKEN_INVALID, {})
    token = header_value[len(BEARER_PREFIX):].strip()
    if not token:
        return None, None, Refusal(CODE_TOKEN_INVALID, {})
    row = db.query(SyncToken).filter_by(token_hash=hash_token(token)).first()
    if row is None:
        return None, None, Refusal(CODE_TOKEN_INVALID, {})
    if row.revoked_at is not None:
        return None, None, Refusal(CODE_TOKEN_REVOKED, {"token_id": row.id})
    user = db.query(DbUser).filter_by(id=row.user_id).first()
    if user is None:
        # A token can outlive its user row: user_id carries no FK.
        return None, None, Refusal(CODE_TOKEN_INVALID, {})
    if (user.username or "") == GUEST_USERNAME:
        return None, None, Refusal(CODE_GUEST_ACCOUNT, {})
    # The token's row id, never the token: a log line is a place a credential
    # leaks, so nothing on this path formats the plaintext.
    logger.debug("sync auth: token %s for user %s", row.id, user.username)
    return row, user, None


def hub_offset_from_client(client_offset: float) -> float:
    """The offset the hub's plan_merge needs, from the one the client measured.

    The client measures "my clock minus the hub's". shift() adds an offset to
    the *peer's* timestamps, and from the hub's side the peer is the client, so
    the sign flips: a client whose clock runs fast reports a positive offset,
    and the hub must subtract that much from the client's stamps before
    comparing them with its own.
    """
    return -float(client_offset)


def touch_last_used(db, token) -> None:
    """Record token use, at most once per LAST_USED_THROTTLE_SECONDS."""
    now = utc_now()
    if token.last_used_at is not None:
        try:
            if (now - token.last_used_at).total_seconds() < LAST_USED_THROTTLE_SECONDS:
                return
        except TypeError:
            pass
    token.last_used_at = now
    db.commit()


# --- Push session -------------------------------------------------------------
#
# One push is three requests (plan → upload → apply) and two workers serve
# them, so the plan's payload, its fingerprint and its clock offset live in a
# JSON file per user. One slot per user: a second plan supersedes the first,
# and the superseded session's apply is refused as plan_stale. That is the
# deliberate choice — two pushes to one account are two writers on the same
# rows, and the engine's rev discipline assumes they are serialised.

def push_session_path(user_id: int) -> str:
    return os.path.join(CACHE_DIR, f"sync_push_{int(user_id)}.json")


def save_push_session(user_id: int, session: dict) -> None:
    """Write the session atomically, under a lock, dropping an expired one."""
    path = push_session_path(user_id)
    _mkdirp(os.path.dirname(path))
    with _FileLock(path):
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(session, f, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            _remove_quiet(tmp)


def load_push_session(user_id: int) -> Optional[dict]:
    """The live session for this user, or None. An expired one is deleted."""
    path = push_session_path(user_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(session, dict) or _expired(session.get("expires_at")):
        clear_push_session(user_id)
        return None
    return session


def clear_push_session(user_id: int) -> None:
    _remove_quiet(push_session_path(user_id))


def _expired(stamp) -> bool:
    """Whether an ISO timestamp is in the past. An absent/unreadable stamp expires."""
    if not stamp:
        return True
    try:
        moment = parse_datetime(stamp)
    except (TypeError, ValueError):
        return True
    return moment is None or moment <= utc_now()


# --- Chunked upload -----------------------------------------------------------

def upload_dir(user_id: int) -> str:
    base = os.path.join(INSTANCE_PATH, SYNC_UPLOAD_DIRNAME, str(int(user_id)))
    _mkdirp(base)
    return base


def upload_part_path(user_id: int, sha256: str, ext: str) -> str:
    return os.path.join(upload_dir(user_id), f"{sha256}.{ext}.part")


def upload_received(user_id: int, sha256: str, ext: str) -> int:
    """How many bytes of this file the hub already holds."""
    try:
        return os.path.getsize(upload_part_path(user_id, sha256, ext))
    except OSError:
        return 0


def write_chunk(user_id: int, sha256: str, ext: str, offset: int, data: bytes) -> int:
    """Write one chunk at `offset`. Returns the new byte count.

    An offset at or below what is already held rewinds first: a retried chunk
    is idempotent, and a torn write from a dropped request is overwritten
    rather than appended to. An offset beyond the current size is a gap and the
    caller must refuse it.
    """
    path = upload_part_path(user_id, sha256, ext)
    current = upload_received(user_id, sha256, ext)
    if offset > current:
        raise ValueError("offset_gap")
    mode = "r+b" if offset < current else "ab"
    if offset < current:
        with open(path, "r+b") as f:
            f.truncate(offset)
            f.seek(offset)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return offset + len(data)
    with open(path, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return current + len(data)


def finalize_upload(user_id: int, sha256: str, ext: str) -> str:
    """Verify the assembled file and move it into the engine's staging dir.

    Returns the digest on success and "" on a digest mismatch, in which case
    the accumulator is discarded: a file that does not hash to its name must
    never reach staging, where the name IS the digest.
    """
    from nova.sync_engine import STAGING_DIRNAME
    part = upload_part_path(user_id, sha256, ext)
    try:
        if _sha256_of(part) != sha256:
            _remove_quiet(part)
            return ""
    except OSError:
        return ""
    staged = os.path.join(INSTANCE_PATH, STAGING_DIRNAME, str(int(user_id)))
    _mkdirp(staged)
    final = os.path.join(staged, f"{sha256}.{ext}")
    try:
        os.replace(part, final)
    except OSError:
        return ""
    return sha256


def sweep_uploads(user_id: int, older_than: int = UPLOAD_TTL_SECONDS) -> list:
    """Drop accumulator files older than the TTL. Returns the paths removed."""
    base = os.path.join(INSTANCE_PATH, SYNC_UPLOAD_DIRNAME, str(int(user_id)))
    removed = []
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return removed
    cutoff = _epoch() - older_than
    for name in names:
        path = os.path.join(base, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed.append(path)
        except OSError:
            continue
    return removed


def drop_uploads(user_id: int) -> None:
    """Remove a user's whole accumulator directory (a cancelled push)."""
    shutil.rmtree(os.path.join(INSTANCE_PATH, SYNC_UPLOAD_DIRNAME, str(int(user_id))),
                  ignore_errors=True)


# --- Pre-push account snapshot ------------------------------------------------

def backup_root(user_id: int) -> str:
    return os.path.join(BACKUP_DIR, SYNC_BACKUP_DIRNAME, str(int(user_id)))


def backup_dirs(user_id: int) -> list:
    """This user's snapshots, oldest first. Names are UTC stamps, so sorting
    the names sorts the times."""
    base = backup_root(user_id)
    try:
        return sorted(os.path.join(base, name) for name in os.listdir(base)
                      if os.path.isdir(os.path.join(base, name)))
    except FileNotFoundError:
        return []


def prune_backups(user_id: int, keep: int = PUSH_BACKUP_KEEP) -> list:
    """Keep the newest `keep` snapshots. Returns the directories removed."""
    dirs = backup_dirs(user_id)
    removed = []
    for path in dirs[:-keep] if keep > 0 else dirs:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


def snapshot_account_yaml(user, keep: int = PUSH_BACKUP_KEEP) -> Optional[str]:
    """Write this account's YAML state before a push touches it.

    One directory per snapshot, because the exporter writes three fixed
    filenames. Returns the directory, or None when the export failed — a failed
    backup must not stop the push, but it must be visible in the log.
    """
    from nova.migration import export_user_to_yaml
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%f")
    out_dir = os.path.join(backup_root(user.id), stamp)
    try:
        ok = export_user_to_yaml(user.username, out_dir=out_dir)
    except Exception as exc:
        logger.warning("sync: pre-push YAML snapshot failed for user %s: %s",
                       user.id, exc)
        shutil.rmtree(out_dir, ignore_errors=True)
        return None
    if not ok:
        shutil.rmtree(out_dir, ignore_errors=True)
        return None
    prune_backups(user.id, keep=keep)
    return out_dir


# --- Sentry -------------------------------------------------------------------

_SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie",
                                "set-cookie"})
_SENSITIVE_BODY_KEYS = frozenset({"token", "password", "api_key", "secret"})


def _header_is_sensitive(name: str) -> bool:
    lowered = str(name).lower()
    return lowered in _SENSITIVE_HEADERS or lowered.startswith("x-nova-")


def scrub_sentry_event(event, hint=None):
    """Remove credentials from a Sentry event. Removal only.

    The SDK attaches every request header to an event and redacts Authorization
    only while send_default_pii is off — a default, not a guarantee. This hook
    makes the guarantee independent of that setting, so it must never add a
    field: a scrubber that could write would be a new way to leak.

    Sentry calls before_send for every event, not just sync ones, so the header
    and body rules are unconditional and the query-string rule is scoped to the
    sync API (a query string can legitimately carry a filter elsewhere).
    """
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if not isinstance(request, dict):
        return event

    headers = request.get("headers")
    if isinstance(headers, dict):
        for name in list(headers):
            if _header_is_sensitive(name):
                headers.pop(name, None)
    elif isinstance(headers, (list, tuple)):
        request["headers"] = [pair for pair in headers
                              if not (isinstance(pair, (list, tuple)) and pair
                                      and _header_is_sensitive(pair[0]))]

    url = str(request.get("url") or "")
    path = request.get("path") or url.split("?", 1)[0]
    if "/api/sync/" in str(path) or "/api/sync/" in url:
        # The sync API takes no query parameters, so anything in one is a
        # mistake — and the mistake most likely to be a token. Both fields go:
        # popping query_string alone would leave the same string in the URL.
        request.pop("query_string", None)
        if "?" in url:
            request["url"] = url.split("?", 1)[0]

    data = request.get("data")
    if isinstance(data, dict):
        for name in list(data):
            if str(name).lower() in _SENSITIVE_BODY_KEYS:
                data.pop(name, None)
    return event


# --- Refusal shapes -----------------------------------------------------------

# Refusal code -> HTTP status. The engine's codes are semantic, not HTTP, so
# the classification is made once, here: a payload that is wrong is a 400, a
# state conflict a client can retry is a 409, and an over-size body a 413.
HTTP_STATUS_BY_REFUSAL = {
    # the request itself is malformed
    "bad_format": 400,
    "unknown_table": 400,
    "unknown_column": 400,
    "unclassified_column": 400,
    "missing_natural_key": 400,
    "missing_timestamp": 400,
    "session_without_external_id": 400,
    "dangling_file_token": 400,
    "file_token_in_non_text_column": 400,
    "bad_file_reference": 400,
    "protocol_mismatch": 400,
    # the state moved; the client may re-plan
    "plan_stale": 409,
    "sync_running": 409,
    "needs_download": 409,
    "not_paired": 409,
    "clock_skew": 409,
    "mass_delete": 409,
    "server_untouched": 409,
    "local_replaced": 409,
    "remote_replaced": 409,
    # credentials
    CODE_TOKEN_INVALID: 401,
    CODE_TOKEN_REVOKED: 401,
    CODE_GUEST_ACCOUNT: 403,
    # files
    "file_over_cap": 413,
    "file_verification_failed": 400,
    "insufficient_disk_space": 507,
}


def status_for(refusal) -> int:
    """The HTTP status one refusal answers with."""
    detail = getattr(refusal, "detail", None) or {}
    if isinstance(detail, dict) and detail.get("reason") == "payload_too_large":
        return 413
    return HTTP_STATUS_BY_REFUSAL.get(getattr(refusal, "code", ""), 400)


def refusal_payload(refusals) -> dict:
    """One or more refusals as the API's response body."""
    if isinstance(refusals, Refusal):
        refusals = [refusals]
    return {
        "protocol": PROTOCOL_VERSION,
        "refusals": [
            {"code": r.code, "detail": dict(r.detail) if isinstance(r.detail, dict) else {"detail": r.detail}}
            for r in refusals
        ],
    }


# --- Small filesystem helpers -------------------------------------------------

def _mkdirp(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def _remove_quiet(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _epoch() -> float:
    import time
    return time.time()
