"""Instance sync — token page and local-side routes (steps 5 + 6).

The one session-authenticated part of sync. Two sections share this page:

* "Use this Nova as a server" — mint/revoke the bearer tokens other instances
  use to reach this server. The sync API itself (nova.blueprints.sync_api)
  never reads a session.
* "Sync with a server" — pair this installation with a hub, download, and sync.
  The heavy work runs in a background job (nova.sync_job); these routes start,
  confirm, abort and poll it.

The plaintext token is rendered straight into the create response and never
stored, logged or flashed: a flash message would put it in the session cookie.

Creating is guarded by a one-time nonce, because a browser reload re-POSTs the
form. The nonce is issued by the GET that renders the form and consumed by the
POST that uses it, so a reload, a double click or a back-and-resubmit finds
nothing to consume and creates nothing.

Every POST here is CSRF-protected (Flask-WTF), in single- and multi-user mode
alike. Single-user mode has no login at all, so without the token any page the
user visits could post to this blueprint: revoke a token, pair this installation
with another server, or confirm a download that replaces the local data. The
token-only API (nova.blueprints.sync_api) is a different blueprint and stays
exempt — it carries a bearer token, never a session.
"""

from __future__ import annotations

import secrets

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from flask_babel import gettext as _, ngettext
from flask_wtf.csrf import CSRFError
from functools import wraps

from nova.config import NOVA_SYNC_CLIENT
from nova.helpers import get_db
from nova.models import SyncState, SyncToken
from nova.sync_client import (DEFAULT_SERVER_URL, SyncClient, SyncTransportError,
                              validate_server_url)
from nova.sync_engine import (SyncRefused, download_status, parse_datetime,
                              start_pairing)
from nova.sync_job import (TOAST_TTL_SECONDS, abort_job, confirm_job, consume_toast,
                           job_status, load_job, start_job)
from nova.sync_time import utc_now
from nova.sync_transport import GUEST_USERNAME, hash_token, new_token

sync_bp = Blueprint('sync', __name__)

LABEL_MAX = 100


@sync_bp.before_request
def _csrf_protect_sync():
    """Refuse a POST that does not carry this session's CSRF token.

    Not gated on the user mode (unlike the admin blueprint): single-user mode is
    the one where a cross-site POST needs no cookie at all. The form field
    ``csrf_token`` and the ``X-CSRFToken`` header are both accepted.
    """
    if request.method == "POST" and current_app.config.get("WTF_CSRF_ENABLED", True):
        from nova import csrf
        csrf.protect()


@sync_bp.errorhandler(CSRFError)
def _csrf_refused(_exc):
    """A missing, wrong or expired CSRF token: nothing was done.

    The page is rendered again (with a fresh token in every form) under a 400,
    so a form that simply sat open too long can be retried straight away.
    """
    flash(_("This form expired. Please try again."), 'error')
    return _render(), 400

# The open nonces live in the session as a small list, not a single value: two
# tabs each hold their own form, and the second tab's submit must not be
# refused because the first one was used. Capped so a client that keeps
# reloading the page cannot grow the cookie without bound.
NONCE_SESSION_KEY = 'sync_create_nonces'
NONCE_FIELD = 'nonce'
MAX_OPEN_NONCES = 5


def _current_user():
    """The signed-in app user, or None. Guests are refused by the POSTs."""
    return getattr(g, 'db_user', None)


def _issue_nonce():
    """A fresh one-time value for the create form, kept in the session."""
    nonces = list(session.get(NONCE_SESSION_KEY) or [])
    nonce = secrets.token_urlsafe(16)
    nonces.append(nonce)
    session[NONCE_SESSION_KEY] = nonces[-MAX_OPEN_NONCES:]
    return nonce


def _consume_nonce(sent):
    """Remove `sent` from the open set. True only if it was there."""
    nonces = list(session.get(NONCE_SESSION_KEY) or [])
    if not sent or sent not in nonces:
        return False
    nonces.remove(sent)
    session[NONCE_SESSION_KEY] = nonces
    return True


# --- Refusal-code -> user message --------------------------------------------

def refusal_message(code: str, detail: dict = None) -> str:
    """A translated, user-understandable message for a refusal code.

    Every engine and transport code, plus the client's own (network_error,
    internal_error, interrupted, replace_required). Unknown codes fall back to a
    generic message that still names the code, so a forward server cannot crash
    the page.
    """
    detail = detail or {}
    if code == "protocol_mismatch":
        return _("This app and the server speak different sync versions. Update one of them.")
    if code == "server_no_sync":
        return _("This server doesn't support sync — it needs a newer Nova version.")
    if code == "file_exists":
        return _("The server already has this file.")
    if code == "token_invalid":
        return _("The sync token was rejected.")
    if code == "token_revoked":
        return _("This token has been revoked.")
    if code == "guest_account":
        return _("The guest account can't sync.")
    if code == "not_paired":
        return _("Not connected to a server.")
    if code == "needs_download":
        return _("Download from the server first.")
    if code == "server_untouched":
        return _("This server has no data yet — import your YAML files there first.")
    if code == "local_replaced":
        return _("Local data was replaced from a YAML import.")
    if code == "remote_replaced":
        return _("The server's data was replaced from a YAML import.")
    if code == "sync_running":
        return _("A sync is already running.")
    if code == "plan_stale":
        return _("The data changed during sync — retrying.")
    if code == "mass_delete":
        return _("This sync would delete %(deletions)s of %(records)s records.") % {
            "deletions": detail.get("deletions", "?"), "records": detail.get("records", "?")}
    if code == "clock_skew":
        minutes = int(round(abs(float(detail.get("offset_seconds") or 0)) / 60.0))
        return _("Your computer's clock is off by %(minutes)s minutes.") % {"minutes": minutes}
    if code == "bad_format":
        return _("The server sent an unreadable response.")
    if code in ("unknown_table", "unknown_column", "unclassified_column"):
        return _("This server uses data shapes this app doesn't know.")
    if code in ("missing_natural_key", "missing_timestamp"):
        return _("The server sent a record this app can't import.")
    if code == "session_without_external_id":
        return _("A session record can't be matched.")
    if code in ("dangling_file_token", "file_token_in_non_text_column", "bad_file_reference"):
        return _("A file reference in the sync is invalid.")
    if code == "file_over_cap":
        return _("A file is too large to sync.")
    if code == "file_verification_failed":
        return _("A file failed its checksum.")
    if code == "insufficient_disk_space":
        return _("Not enough disk space.")
    if code == "network_error":
        return _("Could not reach the server.")
    if code == "internal_error":
        return _("The sync failed unexpectedly.")
    if code == "interrupted":
        return _("The last sync was interrupted. Run Sync now to finish.")
    if code == "replace_required":
        return _("Type REPLACE to continue.")
    return _("Sync failed (%(code)s).") % {"code": code}


def blocked_message(entry: dict) -> str:
    """The translated line for one record a sync left out for good.

    Today that is a project whose id another account already uses, on the
    server (a push) or on this computer (a pull or a download). The message
    names the project, so the user knows which one did not travel.
    """
    name = (entry or {}).get("name") or (entry or {}).get("key") or "?"
    if (entry or {}).get("side") == "local":
        return _('Project "%(name)s" was not synced: its ID is already used by '
                 'another account on this computer.') % {"name": name}
    return _('Project "%(name)s" was not synced: its ID is already used by '
             'another account on the server.') % {"name": name}


def conflict_message(entry: dict) -> str:
    """One "changed on both sides" line: the record and whose copy stays."""
    name = (entry or {}).get("name") or "?"
    if (entry or {}).get("kept") == "local":
        return _("%(name)s — this computer's version kept") % {"name": name}
    return _("%(name)s — server version kept") % {"name": name}


def _sync_view(db, user):
    """The local-sync state the page renders, plus the current job."""
    view = {"enabled": NOVA_SYNC_CLIENT}
    if user is None or not NOVA_SYNC_CLIENT:
        return view
    state = db.query(SyncState).filter_by(user_id=user.id).first()
    view["paired"] = bool(state and state.server_url)
    view["server_account"] = state.server_account if state else None
    view["server_url"] = state.server_url if state else None
    view["paired_at"] = state.paired_at if state else None
    view["last_sync_at"] = state.last_sync_at if state else None
    status = download_status(state)
    view["needs_download"] = view["paired"] and status == "needs_download"
    view["download_incomplete"] = view["paired"] and status == "incomplete"
    job = job_status(user.id)
    view["job"] = job
    summary = (job or {}).get("summary") or {}
    view["conflict_lines"] = [conflict_message(c) for c in summary.get("conflicts") or []]
    view["conflicts_more"] = int(summary.get("conflicts_more") or 0)
    view["blocked_lines"] = [blocked_message(b) for b in summary.get("blocked") or []]
    if job and job.get("error"):
        view["error_message"] = refusal_message(job["error"]["code"], job["error"].get("detail"))
    else:
        view["error_message"] = None
    return view


def _sum_int(mapping) -> int:
    """The sum of a {key: count} mapping's values, tolerant of missing keys."""
    return sum(int(v or 0) for v in (mapping or {}).values())


def _completion_toast(job) -> tuple:
    """The one-shot (kind, message) toast for a finished job, or None.

    The message is composed here — at render/poll time, inside a request — so
    ngettext and gettext use the request locale. ``empty`` short-circuits to the
    "up to date" info toast; a download reports records + files, and a sync
    reports records sent/received (plus files when any were transferred).
    """
    summary = job.get("summary") or {}
    result = job.get("result") or {}
    if result.get("empty"):
        return "info", _("Everything is up to date")
    kind = summary.get("kind")
    if kind == "download":
        records = _sum_int(result.get("down"))
        files = int((summary.get("files") or {}).get("incoming") or 0)
        return "success", _("Download complete: %(records)s, %(files)s") % {
            "records": ngettext("%(num)s record", "%(num)s records", records),
            "files": ngettext("%(num)s file", "%(num)s files", files),
        }
    if kind == "sync":
        sent = _sum_int(result.get("up"))
        received = _sum_int(result.get("down"))
        files_summary = summary.get("files") or {}
        files = int(files_summary.get("up") or 0) + int(files_summary.get("down") or 0)
        if files:
            return "success", _("Sync complete: %(sent)s sent, %(received)s received · %(files)s") % {
                "sent": sent, "received": received,
                "files": ngettext("%(num)s file", "%(num)s files", files),
            }
        return "success", _("Sync complete: %(sent)s sent, %(received)s received") % {
            "sent": sent, "received": received,
        }
    return None


def _flash_completion_toast(user, sync_view) -> None:
    """Flash a finished job's toast once, then mark it consumed.

    The job is read from disk rather than from sync_view, because consuming
    needs the job's start_token, which the poll payload does not carry. A toast
    older than TOAST_TTL_SECONDS (or without a timestamp) is consumed silently:
    a completion the user never saw on time is not announced on a later visit.
    """
    job = load_job(user.id) if user is not None else None
    if not job or job.get("phase") != "done" or not job.get("toast_pending"):
        return
    toast_at = parse_datetime(job.get("toast_at"))
    if toast_at is None or (utc_now() - toast_at).total_seconds() > TOAST_TTL_SECONDS:
        consume_toast(user.id, job.get("start_token"))
        return
    toast = _completion_toast(job)
    if toast is None:
        return
    kind, message = toast
    flash(message, kind)
    # What the run could not do rides with its completion, once: files neither
    # side could restore, and records left out for good.
    result = job.get("result") or {}
    unrestored = int(result.get("unrestored_files") or 0)
    if unrestored:
        flash(ngettext("%(num)s file could not be restored",
                       "%(num)s files could not be restored", unrestored), 'warning')
    for entry in result.get("blocked") or []:
        flash(blocked_message(entry), 'warning')
    consume_toast(user.id, job.get("start_token"))


def _render(new_token=None):
    db = get_db()
    user = _current_user()
    tokens = []
    if user is not None:
        tokens = list(db.query(SyncToken).filter_by(user_id=user.id)
                      .order_by(SyncToken.id.desc()).all())
    sync_view = _sync_view(db, user)
    _flash_completion_toast(user, sync_view)
    # The reveal response is uncacheable because set_cache_headers() in the app
    # factory marks every non-static HTML response no-store.
    return render_template('sync_tokens.html', tokens=tokens, new_token=new_token,
                           nonce=_issue_nonce(), sync_view=sync_view,
                           default_server_url=DEFAULT_SERVER_URL)


def _client_gated(view):
    """404 the connect/sync routes when NOVA_SYNC_CLIENT is off (hub installs)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not NOVA_SYNC_CLIENT:
            abort(404)
        return view(*args, **kwargs)
    return wrapper


# --- Token routes (hub side) --------------------------------------------------

@sync_bp.route('/sync')
def sync_tokens():
    return _render()


@sync_bp.route('/sync/token/create', methods=['POST'])
def create_token():
    user = _current_user()
    if user is None:
        abort(403)
    if getattr(g, 'is_guest', False) or (user.username or "") == GUEST_USERNAME:
        flash(_('Not available for the guest account.'), 'error')
        return redirect(url_for('sync.sync_tokens'))

    # Before the label check: a re-POST of a used form must say "already
    # created" whatever the browser restored into the other fields.
    if not _consume_nonce(request.form.get(NONCE_FIELD)):
        flash(_("This token was already created. For security it can't be "
                "shown again — revoke it and create a new one if you didn't "
                "copy it."), 'warning')
        return _render()

    label = (request.form.get('label') or '').strip()[:LABEL_MAX]
    if not label:
        flash(_('Token label is required.'), 'error')
        return redirect(url_for('sync.sync_tokens'))

    db = get_db()
    plaintext = new_token()
    db.add(SyncToken(user_id=user.id, token_hash=hash_token(plaintext),
                     label=label, created_at=utc_now()))
    db.commit()
    return _render(new_token=plaintext)


@sync_bp.route('/sync/token/revoke', methods=['POST'])
def revoke_token():
    user = _current_user()
    if user is None:
        abort(403)
    if getattr(g, 'is_guest', False) or (user.username or "") == GUEST_USERNAME:
        flash(_('Not available for the guest account.'), 'error')
        return redirect(url_for('sync.sync_tokens'))

    try:
        token_id = int(request.form.get('token_id') or 0)
    except (TypeError, ValueError):
        token_id = 0
    db = get_db()
    row = db.query(SyncToken).filter_by(id=token_id, user_id=user.id).first()
    if row is None:
        flash(_('That token no longer exists.'), 'error')
    elif row.revoked_at is None:
        row.revoked_at = utc_now()
        db.commit()
    return redirect(url_for('sync.sync_tokens'))


# --- Local-side routes (client side) ------------------------------------------

@sync_bp.route('/sync/connect', methods=['POST'])
@_client_gated
def connect():
    user = _current_user()
    if user is None or getattr(g, 'is_guest', False):
        abort(403)
    server_url = (request.form.get('server_url') or '').strip()
    token = (request.form.get('token') or '').strip()
    if not server_url or not token:
        flash(_('Server URL and token are required.'), 'error')
        return redirect(url_for('sync.sync_tokens'))
    try:
        normalized, insecure = validate_server_url(server_url)
    except ValueError:
        flash(_('This URL is not a valid sync server address.'), 'error')
        return redirect(url_for('sync.sync_tokens'))

    try:
        hello = SyncClient(normalized, token).hello()
    except SyncRefused as exc:
        flash(refusal_message(exc.refusal.code, exc.refusal.detail), 'error')
        return redirect(url_for('sync.sync_tokens'))
    except SyncTransportError:
        flash(_('Could not reach the server.'), 'error')
        return redirect(url_for('sync.sync_tokens'))

    account = (hello.get('account') or {}).get('username') or ''
    db = get_db()
    start_pairing(db, user, server_url=normalized, token=token, account=account)
    db.commit()
    if insecure:
        flash(_('Connected over an insecure connection — the token travels in the clear on this network.'),
              'warning')
    else:
        flash(_('Connected to %(account)s.') % {"account": account}, 'success')
    return redirect(url_for('sync.sync_tokens'))


@sync_bp.route('/sync/disconnect', methods=['POST'])
@_client_gated
def disconnect():
    user = _current_user()
    if user is None:
        abort(403)
    db = get_db()
    state = db.query(SyncState).filter_by(user_id=user.id).first()
    if state is not None:
        db.delete(state)
        db.commit()
    abort_job(current_app._get_current_object(), user.id)
    flash(_('Disconnected.'), 'success')
    return redirect(url_for('sync.sync_tokens'))


@sync_bp.route('/sync/download', methods=['POST'])
@_client_gated
def download():
    user = _current_user()
    if user is None or getattr(g, 'is_guest', False):
        abort(403)
    db = get_db()
    state = db.query(SyncState).filter_by(user_id=user.id).first()
    if state is None or not state.server_url:
        flash(_('Not connected to a server.'), 'error')
        return redirect(url_for('sync.sync_tokens'))
    try:
        start_job(current_app._get_current_object(), user.id, 'download')
    except SyncRefused as exc:
        flash(refusal_message(exc.refusal.code, exc.refusal.detail), 'error')
    return redirect(url_for('sync.sync_tokens'))


@sync_bp.route('/sync/sync', methods=['POST'])
@_client_gated
def sync_now():
    user = _current_user()
    if user is None or getattr(g, 'is_guest', False):
        abort(403)
    db = get_db()
    state = db.query(SyncState).filter_by(user_id=user.id).first()
    if state is None or not state.server_url:
        flash(_('Not connected to a server.'), 'error')
        return redirect(url_for('sync.sync_tokens'))
    try:
        start_job(current_app._get_current_object(), user.id, 'sync')
    except SyncRefused as exc:
        flash(refusal_message(exc.refusal.code, exc.refusal.detail), 'error')
    return redirect(url_for('sync.sync_tokens'))


@sync_bp.route('/sync/confirm', methods=['POST'])
@_client_gated
def confirm():
    user = _current_user()
    if user is None:
        abort(403)
    # Two approvals, one per side: the form sets each only for the side whose
    # plan asked, and confirm_job honours each only for that side.
    allow_push = request.form.get('allow_mass_delete_push') == '1'
    allow_pull = request.form.get('allow_mass_delete_pull') == '1'
    replace_confirmed = (request.form.get('replace_confirmed') or '').strip()
    try:
        confirm_job(current_app._get_current_object(), user.id,
                    allow_push_mass_delete=allow_push,
                    allow_pull_mass_delete=allow_pull,
                    replace_confirmed=replace_confirmed)
    except SyncRefused as exc:
        flash(refusal_message(exc.refusal.code, exc.refusal.detail), 'error')
    return redirect(url_for('sync.sync_tokens'))


@sync_bp.route('/sync/abort', methods=['POST'])
@_client_gated
def abort_confirm():
    user = _current_user()
    if user is None:
        abort(403)
    abort_job(current_app._get_current_object(), user.id)
    flash(_('Sync cancelled.'), 'success')
    return redirect(url_for('sync.sync_tokens'))


@sync_bp.route('/sync/job')
@_client_gated
def job():
    user = _current_user()
    if user is None:
        return jsonify({"running": False}), 403
    status = job_status(user.id)
    if status.get("error"):
        status["error_message"] = refusal_message(status["error"]["code"],
                                                  status["error"].get("detail"))
    return jsonify(status)
