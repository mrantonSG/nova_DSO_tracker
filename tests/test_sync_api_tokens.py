"""Instance sync, step 5 — the token page, and what a token must never leak.

The token page is the session-authenticated half of sync: a user mints bearer
tokens here that other instances present to /api/sync/v1/*. Three properties
matter more than the layout, and each has its own block below:

  * the plaintext is shown exactly once, straight out of the create response —
    never through the session, so a reload cannot show it again;
  * it appears in no log record and in no other response;
  * revoking a token is scoped to its owner and takes effect at the API.

The page is also checked against the two design constraints that are easy to
break by accident: it links only stylesheets that already exist, and its own
markup hardcodes no colour (so dark mode cannot be broken by this page).
"""

import logging
import pathlib
import re

import pytest

from nova.models import DbUser, SyncToken
from nova.sync_time import utc_now
from nova.sync_transport import hash_token, new_token

TEMPLATE = (pathlib.Path(__file__).resolve().parents[1]
            / "templates" / "sync_tokens.html")
TOKEN_IN_BODY = re.compile(r'id="sync-new-token"[^>]*>([^<]+)<')

REPO = pathlib.Path(__file__).resolve().parents[1]
# Every stylesheet a page can inherit from, base.html's chain plus the two this
# page links itself.
STYLESHEETS = [
    REPO / "static" / "css" / name for name in
    ("tokens.css", "base.css", "style.css", "nova-theme.css",
     "config_form.css", "gallery_section.css")
]


# --- Fixtures and helpers ------------------------------------------------------

@pytest.fixture
def hub(su_client_logged_in, db_session):
    """The app as the hub: a logged-in client and the account it belongs to."""
    class Hub:
        pass

    h = Hub()
    h.client = su_client_logged_in
    h.db = db_session
    h.user = db_session.query(DbUser).filter_by(username="default").one()
    return h


def _api(hub, path, token=None, body=None, headers=None, raw=None):
    sent = {"X-Nova-Sync-Protocol": "1"}
    sent.update(headers or {})
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    if raw is not None:
        return hub.client.post(path, data=raw, headers=sent,
                               content_type="application/octet-stream")
    return hub.client.post(path, json=body if body is not None else {},
                           headers=sent)


NONCE_IN_FORM = re.compile(r'name="nonce" value="([^"]+)"')


def _nonce(client):
    """The one-time value the create form was rendered with."""
    return NONCE_IN_FORM.findall(client.get("/sync").data.decode())[0]


def _create(client, label="MacBook", nonce=None):
    return client.post("/sync/token/create",
                       data={"label": label, "nonce": nonce or _nonce(client)})


def _shown_token(response):
    return TOKEN_IN_BODY.findall(response.data.decode())[0].strip()


def _tokens(db, user_id):
    db.expire_all()
    return db.query(SyncToken).filter_by(user_id=user_id).all()


def _token_for(db, user_id, label="seeded"):
    """A token row inserted straight into the database, bypassing the page."""
    plaintext = new_token()
    db.add(SyncToken(user_id=user_id, token_hash=hash_token(plaintext),
                     label=label, created_at=utc_now()))
    db.commit()
    return plaintext


def _as_guest(nova, monkeypatch, db_session):
    """A multi-user client that entered through /guest."""
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", False)
    with nova.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["guest_mode"] = True
        return client


# =============================================================================
# 1. The plaintext is shown once, and only once
# =============================================================================

def test_creating_a_token_shows_the_plaintext_and_stores_only_its_hash(hub):
    resp = _create(hub.client)
    assert resp.status_code == 200

    rows = _tokens(hub.db, hub.user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.label == "MacBook"
    assert row.revoked_at is None
    assert row.last_used_at is None

    shown = _shown_token(resp)
    assert hash_token(shown) == row.token_hash
    assert len(row.token_hash) == 64
    assert shown != row.token_hash


def test_the_same_nonce_cannot_create_two_tokens(hub):
    """A reload re-POSTs the form. The second POST must create nothing."""
    nonce = _nonce(hub.client)
    first = hub.client.post("/sync/token/create",
                            data={"label": "once", "nonce": nonce})
    second = hub.client.post("/sync/token/create",
                             data={"label": "once", "nonce": nonce})

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(_tokens(hub.db, hub.user.id)) == 1
    # The second response is the page, not a second reveal.
    assert b'<code id="sync-new-token"' not in second.data
    assert b"already created" in second.data


def test_a_post_without_a_nonce_creates_nothing(hub):
    """No nonce at all is the same case as a used one: nothing to consume."""
    resp = hub.client.post("/sync/token/create", data={"label": "forged"})

    assert resp.status_code == 200
    assert _tokens(hub.db, hub.user.id) == []
    assert b"already created" in resp.data


def test_each_page_load_issues_its_own_nonce(hub):
    """Two tabs: the second tab's form must still work after the first is used."""
    first_nonce = _nonce(hub.client)
    second_nonce = _nonce(hub.client)
    assert first_nonce != second_nonce

    hub.client.post("/sync/token/create", data={"label": "tab one", "nonce": first_nonce})
    resp = hub.client.post("/sync/token/create", data={"label": "tab two", "nonce": second_nonce})

    assert b'<code id="sync-new-token"' in resp.data
    assert len(_tokens(hub.db, hub.user.id)) == 2


def test_the_reveal_response_is_not_cacheable(hub):
    """Back/forward must not be able to show the plaintext again.

    The header comes from set_cache_headers() in the app factory, which marks
    every non-static HTML response no-store. This test is what keeps that true
    for the one response that carries a secret.
    """
    resp = _create(hub.client)
    assert "no-store" in resp.headers["Cache-Control"]
    assert resp.headers["Pragma"] == "no-cache"


def test_the_plaintext_is_not_carried_in_the_session(hub):
    """Nothing in the session, or the next page load could show it again."""
    shown = _shown_token(_create(hub.client))

    with hub.client.session_transaction() as sess:
        assert shown not in str(dict(sess))

    again = hub.client.get("/sync")
    assert again.status_code == 200
    assert shown.encode() not in again.data
    # The reveal block itself is gone; only the script that copies it remains.
    assert b'<code id="sync-new-token"' not in again.data


def test_a_second_token_leaves_the_first_alone(hub):
    first = _shown_token(_create(hub.client, "Laptop"))
    second = _shown_token(_create(hub.client, "Desktop"))

    assert first != second
    assert len(_tokens(hub.db, hub.user.id)) == 2
    assert first.encode() not in _create(hub.client, "Phone").data


def test_the_page_lists_each_token_but_never_its_secret(hub):
    plaintext = _token_for(hub.db, hub.user.id, "Observatory PC")
    body = hub.client.get("/sync").data.decode()
    assert "Observatory PC" in body
    assert plaintext not in body
    assert hash_token(plaintext) not in body


# =============================================================================
# 2. Input validation
# =============================================================================

def test_a_blank_label_is_refused(hub):
    assert _create(hub.client, "   ").status_code == 302
    assert _tokens(hub.db, hub.user.id) == []


def test_a_label_longer_than_the_column_is_truncated(hub):
    _create(hub.client, "x" * 500)
    assert len(_tokens(hub.db, hub.user.id)[0].label) == 100


def test_the_create_url_is_post_only(hub):
    assert hub.client.get("/sync/token/create").status_code == 405


# =============================================================================
# 3. Revoke
# =============================================================================

def test_revoking_stamps_the_row_and_hides_its_button(hub):
    plaintext = _token_for(hub.db, hub.user.id)
    token_id = _tokens(hub.db, hub.user.id)[0].id

    assert hub.client.post("/sync/token/revoke",
                           data={"token_id": token_id}).status_code == 302

    row = _tokens(hub.db, hub.user.id)[0]
    assert row.revoked_at is not None
    assert row.token_hash == hash_token(plaintext)  # revoke keeps the row

    body = hub.client.get("/sync").data.decode()
    assert "Revoked" in body
    assert f'value="{token_id}"' not in body


def test_revoking_a_revoked_token_leaves_the_first_stamp(hub):
    _token_for(hub.db, hub.user.id)
    token_id = _tokens(hub.db, hub.user.id)[0].id
    hub.client.post("/sync/token/revoke", data={"token_id": token_id})
    first = _tokens(hub.db, hub.user.id)[0].revoked_at

    hub.client.post("/sync/token/revoke", data={"token_id": token_id})
    assert _tokens(hub.db, hub.user.id)[0].revoked_at == first


def test_a_revoked_token_is_refused_at_the_api(hub):
    plaintext = _token_for(hub.db, hub.user.id)
    token_id = _tokens(hub.db, hub.user.id)[0].id
    hub.client.post("/sync/token/revoke", data={"token_id": token_id})

    resp = _api(hub, "/api/sync/v1/hello", token=plaintext)
    assert resp.status_code == 401
    assert resp.get_json()["refusals"][0]["code"] == "token_revoked"


def test_revoking_an_unknown_token_says_so(hub):
    resp = hub.client.post("/sync/token/revoke", data={"token_id": 999999},
                           follow_redirects=True)
    assert resp.status_code == 200
    assert "no longer exists" in resp.data.decode().lower()


def test_a_revoke_cannot_touch_another_accounts_token(multi_user_client, db_session):
    client, ids = multi_user_client
    other = _token_for(db_session, ids["user_b_id"], "UserB's laptop")
    row = _tokens(db_session, ids["user_b_id"])[0]

    resp = client.post("/sync/token/revoke", data={"token_id": row.id},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert "no longer exists" in resp.data.decode().lower()

    db_session.expire_all()
    surviving = db_session.query(SyncToken).filter_by(id=row.id).one()
    assert surviving.revoked_at is None
    assert surviving.token_hash == hash_token(other)

    # ...and UserA's own page never lists it.
    assert "UserB's laptop" not in client.get("/sync").data.decode()


def test_the_guest_account_cannot_mint_a_token(db_session, monkeypatch):
    """The guest account is shared; its token would be everyone's token."""
    import nova

    client = _as_guest(nova, monkeypatch, db_session)
    resp = client.post("/sync/token/create", data={"label": "nope"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert "guest" in resp.data.decode().lower()
    assert db_session.query(SyncToken).count() == 0


def test_the_guest_account_cannot_revoke_a_token(multi_user_client, db_session):
    client, ids = multi_user_client
    _token_for(db_session, ids["user_a_id"])
    row = _tokens(db_session, ids["user_a_id"])[0]

    with client.session_transaction() as sess:
        sess.pop("_user_id", None)
        sess.pop("_fresh", None)
        sess["guest_mode"] = True
    client.post("/sync/token/revoke", data={"token_id": row.id},
                follow_redirects=True)

    db_session.expire_all()
    assert db_session.query(SyncToken).filter_by(id=row.id).one().revoked_at is None


# =============================================================================
# 4. What must never leak
# =============================================================================

def test_the_token_never_reaches_a_log_record(hub, caplog):
    """One full run at DEBUG: mint, page, API calls, revoke, page."""
    with caplog.at_level(logging.DEBUG):
        shown = _shown_token(_create(hub.client, "leaky"))
        hub.client.get("/sync")
        _api(hub, "/api/sync/v1/hello", token=shown)
        _api(hub, "/api/sync/v1/changes", token=shown, body={"since_rev": 0})
        _api(hub, "/api/sync/v1/push/cancel", token=shown, body={})
        token_id = _tokens(hub.db, hub.user.id)[0].id
        hub.client.post("/sync/token/revoke", data={"token_id": token_id})
        hub.client.get("/sync")

    assert caplog.records, "nothing captured: the sweep would prove nothing"
    for record in caplog.records:
        assert shown not in record.getMessage()
        assert shown not in str(record.args)
        assert shown not in str(getattr(record, "exc_info", "") or "")
    assert shown not in caplog.text


def test_the_token_appears_in_no_response_except_the_create_one(hub):
    shown = _shown_token(_create(hub.client, "sweep"))
    token_id = _tokens(hub.db, hub.user.id)[0].id

    responses = [
        hub.client.get("/sync"),
        _create(hub.client, "second"),
        hub.client.post("/sync/token/revoke", data={"token_id": token_id}),
        hub.client.get("/config_form"),
        hub.client.get("/"),
        _api(hub, "/api/sync/v1/hello", token=shown),
    ]
    for resp in responses:
        assert shown not in resp.data.decode(errors="ignore")
        for value in resp.headers.values():
            assert shown not in str(value)


# =============================================================================
# 5. Design constraints
# =============================================================================

def test_the_config_form_links_to_the_sync_page(hub):
    assert 'href="/sync"' in hub.client.get("/config_form").data.decode()


def test_the_page_links_the_existing_stylesheets(hub):
    body = hub.client.get("/sync").data.decode()
    assert "css/config_form.css" in body
    assert "css/gallery_section.css" in body


def test_the_template_hardcodes_no_colour_and_adds_no_style_block():
    """Every colour must come from a token, or dark mode keeps the light value.

    A `var(--token, #fallback)` fallback IS allowed, and one is required on
    border-color: the design system documents that the variable resolves
    inconsistently in some contexts. A bare colour is what is forbidden — no
    theme can override it.
    """
    source = TEMPLATE.read_text()
    stripped = re.sub(r"var\(\s*--[a-z0-9-]+\s*,\s*#[0-9a-fA-F]{3,8}\s*\)", "", source)
    # (?<!&): &#8592; is an HTML entity, not a colour.
    assert not re.search(r"(?<!&)#[0-9a-fA-F]{3,8}\b", stripped)
    assert not re.search(r"rgba?\(", source)
    assert "<style" not in source


def test_every_variable_the_template_uses_is_defined():
    """A var() no stylesheet defines renders as nothing, and looks like a bug
    in dark mode only. The page's own stylesheets plus base.html's chain are
    the complete set it can inherit from."""
    source = TEMPLATE.read_text()
    css = "".join(path.read_text() for path in STYLESHEETS)
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", source))
    assert used, "the template should be taking its colours from tokens"
    assert not sorted(name for name in used if f"{name}:" not in css)


def test_the_copy_button_is_a_ghost_and_the_submit_is_primary():
    """The two button classes the design system names, on the two actions.

    The ghost needs its .form-actions wrapper: `.column button`
    (config_form.css) is specificity 0,1,1 and would otherwise repaint it as a
    filled primary button."""
    flat = re.sub(r"\s+", " ", TEMPLATE.read_text())
    assert ('<div class="form-actions"> '
            '<button type="button" class="inline-button-ghost"') in flat
    assert '<button type="submit" class="action-button">' in flat
    assert "edit-btn" not in flat


def test_the_page_renders_the_empty_state_when_there_are_no_tokens(hub):
    assert "gallery-empty-state" in hub.client.get("/sync").data.decode()
