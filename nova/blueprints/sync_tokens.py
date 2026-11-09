"""Instance sync — token page (step 5).

The one session-authenticated part of sync: the page where a user mints the
bearer tokens that other Nova instances use to reach this server. The API
itself (nova.blueprints.sync_api) never reads a session.

The plaintext token is rendered straight into the create response and never
stored, logged or flashed: a flash message would put it in the session cookie,
and the page would then be able to show it again.

Creating is guarded by a one-time nonce, because a browser reload re-POSTs the
form. The nonce is issued by the GET that renders the form and consumed by the
POST that uses it, so a reload, a double click or a back-and-resubmit finds
nothing to consume and creates nothing. The nonce is not a secret — it only
has to stop an accidental second submit, and the client can read it from the
form it was given.
"""

from __future__ import annotations

import secrets

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from flask_babel import gettext as _

from nova.helpers import get_db
from nova.models import SyncToken
from nova.sync_time import utc_now
from nova.sync_transport import GUEST_USERNAME, hash_token, new_token

sync_bp = Blueprint('sync', __name__)

LABEL_MAX = 100

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
    """Remove `sent` from the open set. True only if it was there.

    Removing it is what makes the second POST of the same form a no-op: the
    value is gone from the session, so it can never match again.
    """
    nonces = list(session.get(NONCE_SESSION_KEY) or [])
    if not sent or sent not in nonces:
        return False
    nonces.remove(sent)
    session[NONCE_SESSION_KEY] = nonces
    return True


def _render(new_token=None):
    db = get_db()
    user = _current_user()
    tokens = []
    if user is not None:
        tokens = list(db.query(SyncToken).filter_by(user_id=user.id)
                      .order_by(SyncToken.id.desc()).all())
    # The reveal response is uncacheable because set_cache_headers() in the app
    # factory marks every non-static HTML response no-store, so a back/forward
    # navigation cannot show the plaintext again. Setting the header here would
    # be overwritten by that hook; the test pins the guarantee instead, so a
    # future change to the hook cannot silently make the token cacheable.
    return render_template('sync_tokens.html', tokens=tokens,
                           new_token=new_token, nonce=_issue_nonce())


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
        # A reload, a double click or a back-and-resubmit: the form was already
        # used. Nothing is created, and the page says why — the plaintext is
        # gone for good, so the only way forward is to revoke and start over.
        flash(_("This token was already created. For security it can't be "
                "shown again — revoke it and create a new one if you didn't "
                "copy it."), 'error')
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
