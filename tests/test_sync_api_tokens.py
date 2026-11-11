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

import json
import logging
import os
import pathlib
import re
import shutil
import subprocess

import pytest

from nova.models import DbUser, SyncState, SyncToken
from nova.sync_time import utc_now
from nova.sync_transport import hash_token, new_token

NODE = shutil.which("node")

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


# =============================================================================
# 6. NOVA_SYNC_CLIENT off: the section is gone and every client route 404s
# =============================================================================

def test_nova_sync_client_off_404s_client_routes_and_hides_the_section(hub, monkeypatch):
    monkeypatch.setattr("nova.blueprints.sync_tokens.NOVA_SYNC_CLIENT", False)

    for route in ("/sync/connect", "/sync/download", "/sync/sync",
                  "/sync/confirm", "/sync/abort"):
        assert hub.client.post(route, data={}).status_code == 404
    assert hub.client.get("/sync/job").status_code == 404

    body = hub.client.get("/sync").data.decode()
    assert "Sync with a server" not in body
    assert "Use this Nova instance as a server" in body


# =============================================================================
# 7. One user cannot see, confirm or abort another user's job (rule 10)
# =============================================================================

def test_one_user_cannot_see_confirm_or_abort_another_users_job(
        multi_user_client, monkeypatch, tmp_path):
    import nova.sync_job as sj
    client, ids = multi_user_client
    user_a, user_b = ids["user_a_id"], ids["user_b_id"]

    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))
    sj.save_job(user_b, {
        "job_id": "b", "start_token": "b", "user_id": user_b, "kind": "download",
        "phase": "awaiting_confirmation", "progress": 0.9,
        "phase_label": "awaiting_confirmation",
        "summary": {"kind": "download", "requires_replace_typed": False,
                    "server_counts": {}, "local_counts": {},
                    "files": {"incoming": 0, "bytes": 0, "replaced": 0},
                    "unsynced_local_changes": 0},
        "result": None, "error": None,
        "started_at": "2026-01-01T00:00:00", "heartbeat_at": "2026-01-01T00:00:00",
        "pid": os.getpid(),
    })

    # A's job poll reports exactly "not running" — nothing of B's summary.
    assert client.get("/sync/job").get_json() == {"running": False}

    # A's abort and confirm leave B's job file untouched.
    client.post("/sync/abort")
    assert sj.load_job(user_b) is not None
    client.post("/sync/confirm", data={"replace_confirmed": "REPLACE"})
    assert sj.load_job(user_b) is not None

    # Job files and backups are per user.
    assert sj.job_dir(user_b) != sj.job_dir(user_a)


# =============================================================================
# 8. The typed REPLACE keeps Apply disabled until the text matches (rule 11)
# =============================================================================

_REPLACE_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [scriptFile, outFile] = process.argv.slice(2);

function el(id) {
  return { id, dataset: {}, value: '', disabled: false, listeners: {},
           addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); } };
}
const byId = {};
const document = { getElementById: (id) => byId[id] || null };
byId['sync-replace-input'] = el('sync-replace-input');
byId['sync-replace-input'].dataset.expected = 'REPLACE';
byId['sync-confirm-form'] = el('sync-confirm-form');
byId['sync-apply-btn'] = el('sync-apply-btn');

const ctx = { console, document, window: {}, setTimeout, clearTimeout };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(scriptFile, 'utf8'), ctx, { filename: 'inline.js' });

const input = byId['sync-replace-input'];
const apply = byId['sync-apply-btn'];
const initial = apply.disabled;
input.value = 'REPLACE';
(input.listeners['input'] || []).forEach((fn) => fn());
fs.writeFileSync(outFile, JSON.stringify({ initialDisabled: initial, afterMatchDisabled: apply.disabled }));
"""


@pytest.fixture(scope="module")
def run_replace(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("syncjs")
    driver = tmp / "driver.cjs"
    driver.write_text(_REPLACE_DRIVER, encoding="utf-8")

    def run(script):
        sf = tmp / "inline.js"
        sf.write_text(script, encoding="utf-8")
        out = tmp / "out.json"
        proc = subprocess.run([NODE, str(driver), str(sf), str(out)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(out.read_text(encoding="utf-8"))

    return run


def _seed_download_job(hub, db_session, monkeypatch, tmp_path):
    """An awaiting download job that requires the typed REPLACE, for the page."""
    import nova.sync_job as sj
    from nova.sync_engine import to_iso
    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))
    now = to_iso(utc_now())
    sj.save_job(hub.user.id, {
        "job_id": "x", "start_token": "x", "user_id": hub.user.id,
        "kind": "download", "phase": "awaiting_confirmation", "progress": 0.9,
        "phase_label": "awaiting_confirmation",
        "summary": {"kind": "download", "requires_replace_typed": True,
                    "server_counts": {"astro_objects": 5},
                    "local_counts": {"astro_objects": 1},
                    "unsynced_local_changes": 0,
                    "files": {"incoming": 1, "bytes": 1024, "replaced": 1}},
        "result": None, "error": None,
        "started_at": now, "heartbeat_at": now,
        "pid": os.getpid(),
    })


def test_the_typed_replace_starts_apply_disabled(hub, db_session, monkeypatch,
                                                 tmp_path, run_replace):
    _seed_download_job(hub, db_session, monkeypatch, tmp_path)
    html = hub.client.get("/sync").data.decode()

    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    script = next(s for s in scripts if "sync-replace-input" in s)
    assert 'data-expected="REPLACE"' in html

    result = run_replace(script)
    assert result["initialDisabled"] is True
    assert result["afterMatchDisabled"] is False


# =============================================================================
# 9. "Last synced" is a local-time, app-language instant (rule 12)
# =============================================================================

_LAST_SYNCED_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [scriptFile, outFile] = process.argv.slice(2);

let captured = null;
const byId = {
  'sync-last-synced': {
    _text: '',
    getAttribute(name) { return name === 'datetime' ? '2026-11-10T18:11:00Z' : null; },
    set textContent(v) { this._text = v; },
  },
};
const document = {
  getElementById: (id) => byId[id] || null,
  documentElement: { lang: 'de' },
};
const Intl = {
  DateTimeFormat: function (locale, opts) {
    captured = { locale, opts };
    return { format: function () { return 'LOCALIZED'; } };
  },
};

const ctx = { document, Intl };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(scriptFile, 'utf8'), ctx, { filename: 'inline.js' });

fs.writeFileSync(outFile, JSON.stringify({
  locale: captured ? captured.locale : null,
  dateStyle: captured ? captured.opts.dateStyle : null,
  timeStyle: captured ? captured.opts.timeStyle : null,
  textContent: byId['sync-last-synced']._text,
}));
"""


def _seed_paired_synced(hub, db_session):
    """A paired client that has completed a download (so "Last synced" renders)."""
    user = db_session.query(DbUser).filter_by(username="default").one()
    state = db_session.query(SyncState).filter_by(user_id=user.id).first()
    if state is None:
        state = SyncState(user_id=user.id)
        db_session.add(state)
    state.server_url = "https://example.com"
    state.server_account = "hub"
    state.local_pushed_rev = 5
    state.last_sync_at = utc_now()
    db_session.commit()
    return user


def test_last_synced_time_uses_iso_z_and_formats_in_app_locale(hub, db_session, tmp_path):
    if NODE is None:
        pytest.skip("node is not installed")

    _seed_paired_synced(hub, db_session)
    html = hub.client.get("/sync").data.decode()

    # The <time> element carries the UTC instant as ISO-8601 with a trailing Z.
    m = re.search(r'<time id="sync-last-synced" datetime="([^"]+)"', html)
    assert m, "no <time id=\"sync-last-synced\"> element rendered"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", m.group(1)), m.group(1)

    # The script formats with the page's <html lang>, not the browser default.
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    script = next(s for s in scripts if "sync-last-synced" in s)
    assert "Intl.DateTimeFormat" in script
    assert "document.documentElement.lang" in script

    driver = tmp_path / "driver.cjs"
    driver.write_text(_LAST_SYNCED_DRIVER, encoding="utf-8")
    sf = tmp_path / "inline.js"
    sf.write_text(script, encoding="utf-8")
    out = tmp_path / "out.json"
    proc = subprocess.run([NODE, str(driver), str(sf), str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["locale"] == "de"
    assert result["dateStyle"] == "medium"
    assert result["timeStyle"] == "short"
    assert result["textContent"] == "LOCALIZED"


# =============================================================================
# 10. Completion toast: flashed once, not on every later page load
# =============================================================================

def _seed_done_job(hub, monkeypatch, tmp_path, *, summary, result, toast_at=None):
    import nova.sync_job as sj
    from nova.sync_engine import to_iso
    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))
    now = to_iso(utc_now())
    sj.save_job(hub.user.id, {
        "job_id": "x", "start_token": "x", "user_id": hub.user.id,
        "kind": summary["kind"], "phase": "done", "progress": 1.0,
        "phase_label": "done",
        "summary": summary, "result": result, "error": None,
        "toast_pending": True, "toast_at": toast_at or now,
        "started_at": now, "heartbeat_at": now,
        "pid": os.getpid(),
    })


def test_completion_toast_flashes_once_after_done(hub, monkeypatch, tmp_path):
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 1, "down": 2, "down_bytes": 2048}},
                   result={"up": {"created": 1, "updated": 1, "deleted": 0},
                           "down": {"created": 2, "updated": 0, "deleted": 0}})

    first = hub.client.get("/sync").data.decode()
    assert "Sync complete: 2 sent, 2 received · 3 files" in first

    # A later load of the same finished job does not re-emit the toast.
    second = hub.client.get("/sync").data.decode()
    assert "Sync complete:" not in second


def test_download_and_empty_completion_toast_messages(hub, monkeypatch, tmp_path):
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "download",
                            "files": {"incoming": 3, "bytes": 4096, "replaced": 0}},
                   result={"down": {"astro_objects": 2, "projects": 1},
                           "pending_files": 0})
    body = hub.client.get("/sync").data.decode()
    assert "Download complete: 3 records, 3 files" in body

    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 0, "down": 0, "down_bytes": 0}},
                   result={"up": {"created": 0, "updated": 0, "deleted": 0},
                           "down": {"created": 0, "updated": 0, "deleted": 0},
                           "empty": True})
    body = hub.client.get("/sync").data.decode()
    assert "Everything is up to date" in body


def test_stale_completion_toast_is_consumed_without_flashing(hub, monkeypatch, tmp_path):
    import nova.sync_job as sj
    from datetime import timedelta
    from nova.sync_engine import to_iso
    old = to_iso(utc_now() - timedelta(seconds=sj.TOAST_TTL_SECONDS + 60))
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 0, "down": 0, "down_bytes": 0}},
                   result={"up": {"created": 1, "updated": 0, "deleted": 0},
                           "down": {"created": 0, "updated": 0, "deleted": 0}},
                   toast_at=old)

    body = hub.client.get("/sync").data.decode()
    assert "Sync complete:" not in body
    # Consumed, not merely suppressed: the flag is cleared on disk.
    assert sj.load_job(hub.user.id)["toast_pending"] is False


def test_consume_toast_with_stale_start_token_leaves_newer_job(hub, monkeypatch, tmp_path):
    import nova.sync_job as sj
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 0, "down": 0, "down_bytes": 0}},
                   result={"up": {"created": 0, "updated": 0, "deleted": 0},
                           "down": {"created": 0, "updated": 0, "deleted": 0}})
    # A newer job replaced the one the caller loaded (token "x").
    newer = dict(sj.load_job(hub.user.id), job_id="y", start_token="y")
    sj.save_job(hub.user.id, newer)
    with open(sj.job_path(hub.user.id), "rb") as f:
        before = f.read()

    sj.consume_toast(hub.user.id, "x")

    with open(sj.job_path(hub.user.id), "rb") as f:
        assert f.read() == before
    assert sj.load_job(hub.user.id)["toast_pending"] is True


# =============================================================================
# 11. Summary rows: body font, and the files line's zero case
# =============================================================================

def test_sync_summary_rows_use_body_font_and_tabular_figures():
    css = (REPO / "static" / "css" / "config_form.css").read_text()
    block = re.search(r"\.sync-summary-row\s*\{[^}]*\}", css).group(0)
    assert "var(--font-mono" not in block
    assert "font-variant-numeric: tabular-nums" in block


def test_sync_files_line_shows_none_to_transfer_when_zero(hub, monkeypatch, tmp_path):
    import nova.sync_job as sj
    from nova.sync_engine import to_iso
    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))
    now = to_iso(utc_now())
    sj.save_job(hub.user.id, {
        "job_id": "x", "start_token": "x", "user_id": hub.user.id,
        "kind": "sync", "phase": "awaiting_confirmation", "progress": 0.9,
        "phase_label": "awaiting_confirmation",
        "summary": {"kind": "sync",
                    "up": {"created": 0, "updated": 0, "deleted": 0,
                           "may_skip_deleted": False},
                    "down": {"created": 0, "updated": 0, "deleted": 0,
                             "may_skip_deleted": False},
                    "files": {"up": 0, "down": 0, "down_bytes": 0},
                    "mass_delete": False},
        "result": None, "error": None,
        "started_at": now, "heartbeat_at": now,
        "pid": os.getpid(),
    })

    body = hub.client.get("/sync").data.decode()
    assert "none to transfer" in body
    assert "to upload" not in body


# =============================================================================
# 12. CSRF: every session-authenticated POST of the sync blueprint
# =============================================================================
#
# conftest switches the check off for the suite (csrf_checks_off); these tests
# switch it back on. Single-user mode is the important one: it has no login, so
# without the token ANY page could post here.

CSRF_IN_FORM = re.compile(r'name="csrf_token" value="([^"]+)"')

SYNC_POST_ROUTES = (
    "/sync/token/create", "/sync/token/revoke", "/sync/connect",
    "/sync/disconnect", "/sync/download", "/sync/sync", "/sync/confirm",
    "/sync/abort",
)


@pytest.fixture
def csrf_on(monkeypatch):
    import nova
    monkeypatch.setitem(nova.app.config, "WTF_CSRF_ENABLED", True)


def _csrf(client):
    """The token the sync page was rendered with, for this client's session."""
    return CSRF_IN_FORM.findall(client.get("/sync").data.decode())[0]


def _pair(db, user_id):
    db.add(SyncState(user_id=user_id, server_url="https://hub.invalid",
                     token="t", server_account="hubuser", paired_at=utc_now(),
                     last_sync_at=utc_now()))
    db.commit()


def test_the_sync_blueprint_has_exactly_these_post_routes():
    """A new POST route added to the blueprint must be added to the list the
    CSRF tests walk, or it would go untested."""
    import nova
    found = sorted(rule.rule for rule in nova.app.url_map.iter_rules()
                   if rule.endpoint.startswith("sync.") and "POST" in rule.methods)
    assert found == sorted(SYNC_POST_ROUTES)


@pytest.mark.parametrize("route", SYNC_POST_ROUTES)
def test_a_post_without_a_csrf_token_is_refused_in_single_user_mode(hub, csrf_on, route):
    token_row = _token_for(hub.db, hub.user.id)
    hub.db.commit()
    _pair(hub.db, hub.user.id)
    nonce = _nonce(hub.client)

    resp = hub.client.post(route, data={
        "nonce": nonce, "label": "MacBook",
        "token_id": str(_tokens(hub.db, hub.user.id)[0].id),
        "server_url": "https://hub.invalid", "token": "t",
        "replace_confirmed": "REPLACE"})

    assert resp.status_code == 400
    # Nothing happened: no token minted or revoked, still paired, no job.
    rows = _tokens(hub.db, hub.user.id)
    assert len(rows) == 1 and rows[0].revoked_at is None
    hub.db.expire_all()
    assert hub.db.query(SyncState).filter_by(user_id=hub.user.id).count() == 1
    import nova.sync_job as sj
    assert sj.load_job(hub.user.id) is None
    assert token_row is not None


@pytest.mark.parametrize("route", SYNC_POST_ROUTES)
def test_a_post_with_a_wrong_csrf_token_is_refused(hub, csrf_on, route):
    resp = hub.client.post(route, data={"csrf_token": "not-the-token"})
    assert resp.status_code == 400


def test_a_refused_post_renders_the_page_again_with_a_fresh_token(hub, csrf_on):
    resp = hub.client.post("/sync/token/revoke", data={"token_id": "1"})
    body = resp.data.decode()
    assert resp.status_code == 400
    assert "This form expired. Please try again." in body
    assert CSRF_IN_FORM.findall(body)


def test_creating_and_revoking_work_with_the_csrf_token(hub, csrf_on):
    page = hub.client.get("/sync").data.decode()
    token = CSRF_IN_FORM.findall(page)[0]
    nonce = NONCE_IN_FORM.findall(page)[0]

    created = hub.client.post("/sync/token/create", data={
        "csrf_token": token, "nonce": nonce, "label": "MacBook"})
    assert created.status_code == 200
    rows = _tokens(hub.db, hub.user.id)
    assert [r.label for r in rows] == ["MacBook"]

    revoked = hub.client.post("/sync/token/revoke", data={
        "csrf_token": _csrf(hub.client), "token_id": str(rows[0].id)})
    assert revoked.status_code == 302
    assert _tokens(hub.db, hub.user.id)[0].revoked_at is not None


def test_the_client_routes_work_with_the_csrf_token(hub, csrf_on, monkeypatch, tmp_path):
    import nova.sync_job as sj
    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))
    started = []
    monkeypatch.setattr("nova.blueprints.sync_tokens.start_job",
                        lambda app, uid, kind: started.append(kind))
    _pair(hub.db, hub.user.id)

    for route, kind in (("/sync/download", "download"), ("/sync/sync", "sync")):
        resp = hub.client.post(route, data={"csrf_token": _csrf(hub.client)})
        assert resp.status_code == 302
        assert started[-1] == kind
    assert hub.client.post("/sync/abort",
                           data={"csrf_token": _csrf(hub.client)}).status_code == 302
    assert hub.client.post("/sync/confirm",
                           data={"csrf_token": _csrf(hub.client)}).status_code == 302
    assert hub.client.post("/sync/disconnect",
                           data={"csrf_token": _csrf(hub.client)}).status_code == 302
    hub.db.expire_all()
    assert hub.db.query(SyncState).filter_by(user_id=hub.user.id).count() == 0


def test_the_csrf_token_is_accepted_as_a_header(hub, csrf_on):
    _pair(hub.db, hub.user.id)
    resp = hub.client.post("/sync/disconnect",
                           headers={"X-CSRFToken": _csrf(hub.client)})
    assert resp.status_code == 302
    hub.db.expire_all()
    assert hub.db.query(SyncState).filter_by(user_id=hub.user.id).count() == 0


@pytest.mark.parametrize("route", SYNC_POST_ROUTES)
def test_a_post_without_a_csrf_token_is_refused_in_multi_user_mode(
        multi_user_client, csrf_on, route):
    client, _ids = multi_user_client
    assert client.post(route, data={"label": "x", "token_id": "1"}).status_code == 400


def test_a_post_with_the_csrf_token_works_in_multi_user_mode(multi_user_client, csrf_on):
    client, ids = multi_user_client
    page = client.get("/sync").data.decode()
    resp = client.post("/sync/token/create", data={
        "csrf_token": CSRF_IN_FORM.findall(page)[0],
        "nonce": NONCE_IN_FORM.findall(page)[0], "label": "Observatory"})
    assert resp.status_code == 200
    assert "Copy this token now" in resp.data.decode()


def test_the_token_only_sync_api_stays_exempt_from_csrf(hub, csrf_on):
    plaintext = _token_for(hub.db, hub.user.id)
    hub.db.commit()
    resp = _api(hub, "/api/sync/v1/hello", token=plaintext)
    assert resp.status_code == 200, resp.get_json()


def test_every_form_on_the_page_carries_the_csrf_token(hub, monkeypatch, tmp_path):
    """Rendered in each state that shows a different set of forms."""
    import nova.sync_job as sj
    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))

    def forms_and_tokens():
        body = hub.client.get("/sync").data.decode()
        # Only this page's own forms: the base layout has forms of its own.
        return (len(re.findall(r'<form\b[^>]*action="/sync', body)),
                len(CSRF_IN_FORM.findall(body)), body)

    forms, tokens, _body = forms_and_tokens()                 # unpaired
    assert forms == tokens and forms >= 3

    _token_for(hub.db, hub.user.id)
    hub.db.commit()
    _pair(hub.db, hub.user.id)
    forms, tokens, _body = forms_and_tokens()                 # paired, with a token row
    assert forms == tokens and forms >= 6

    for summary in (
        {"kind": "download", "requires_replace_typed": True, "server_counts": {},
         "local_counts": {}, "files": {"incoming": 0, "bytes": 0, "replaced": 0},
         "unsynced_local_changes": 0},
        {"kind": "sync", "up": {"created": 1, "updated": 0, "deleted": 0},
         "down": {"created": 0, "updated": 0, "deleted": 0},
         "files": {"up": 0, "down": 0, "down_bytes": 0}, "mass_delete": False},
    ):
        sj.save_job(hub.user.id, {
            "job_id": "x", "start_token": "x", "user_id": hub.user.id,
            "kind": summary["kind"], "phase": "awaiting_confirmation",
            "progress": 0.9, "phase_label": "awaiting_confirmation",
            "summary": summary, "result": None, "error": None,
            "started_at": "2026-01-01T00:00:00",
            "heartbeat_at": sj.to_iso(utc_now()), "pid": os.getpid()})
        forms, tokens, body = forms_and_tokens()              # awaiting confirmation
        assert forms == tokens
        assert 'id="sync-confirm-form"' in body


def test_the_job_poll_sends_the_csrf_header():
    source = (pathlib.Path(__file__).resolve().parents[1]
              / "templates" / "sync_tokens.html").read_text(encoding="utf-8")
    fetches = re.findall(r"fetch\([^\n]*", source)
    assert fetches
    assert all("'X-CSRFToken': {{ csrf_token() | tojson }}" in call for call in fetches)


# =============================================================================
# 13. What the summary and the completion say beyond the counts
# =============================================================================

def _seed_awaiting_sync(hub, monkeypatch, tmp_path, **extra):
    import nova.sync_job as sj
    monkeypatch.setattr(sj, "INSTANCE_PATH", str(tmp_path))
    summary = {"kind": "sync", "up": {"created": 0, "updated": 2, "deleted": 0},
               "down": {"created": 0, "updated": 2, "deleted": 0},
               "files": {"up": 0, "down": 0, "down_bytes": 0}, "mass_delete": False}
    summary.update(extra)
    sj.save_job(hub.user.id, {
        "job_id": "x", "start_token": "x", "user_id": hub.user.id, "kind": "sync",
        "phase": "awaiting_confirmation", "progress": 0.9,
        "phase_label": "awaiting_confirmation", "summary": summary,
        "result": None, "error": None, "started_at": "2026-01-01T00:00:00",
        "heartbeat_at": sj.to_iso(utc_now()), "pid": os.getpid()})


def test_the_summary_shows_which_copy_of_a_conflicting_record_stays(
        hub, monkeypatch, tmp_path):
    _pair(hub.db, hub.user.id)
    _seed_awaiting_sync(hub, monkeypatch, tmp_path, conflicts=[
        {"table": "astro_objects", "name": "M 31", "kept": "server"},
        {"table": "journal_sessions", "name": "2026-01-02 NGC 7000", "kept": "local"},
    ], conflicts_more=3)

    body = hub.client.get("/sync").data.decode()

    assert "Changed on both sides" in body
    assert "M 31 — server version kept" in body
    assert "2026-01-02 NGC 7000 — this computer&#39;s version kept" in body
    assert "and 3 more" in body


def test_a_summary_without_conflicts_shows_no_conflict_block(hub, monkeypatch, tmp_path):
    _pair(hub.db, hub.user.id)
    _seed_awaiting_sync(hub, monkeypatch, tmp_path)
    body = hub.client.get("/sync").data.decode()
    assert "Changed on both sides" not in body
    assert "version kept" not in body


def test_the_summary_names_a_project_the_server_will_not_take(hub, monkeypatch, tmp_path):
    _pair(hub.db, hub.user.id)
    _seed_awaiting_sync(hub, monkeypatch, tmp_path, blocked=[
        {"table": "projects", "key": "abc", "name": "Veil mosaic",
         "reason": "project_id_taken", "side": "server"}])
    body = hub.client.get("/sync").data.decode()
    assert ("Project &#34;Veil mosaic&#34; was not synced: its ID is already used "
            "by another account on the server.") in body


def test_the_separate_mass_delete_fields_follow_the_side_that_asked(
        hub, monkeypatch, tmp_path):
    _pair(hub.db, hub.user.id)
    _seed_awaiting_sync(hub, monkeypatch, tmp_path, mass_delete=True,
                        push_mass_delete=False, pull_mass_delete=True)
    body = hub.client.get("/sync").data.decode()
    assert 'data-mass-delete-push="0"' in body
    assert 'data-mass-delete-pull="1"' in body
    assert 'name="allow_mass_delete_push" value="0"' in body
    assert 'name="allow_mass_delete_pull" value="0"' in body
    assert 'name="allow_mass_delete"' not in body


def test_the_confirm_route_passes_each_approval_on_its_own(hub, monkeypatch):
    seen = {}
    monkeypatch.setattr("nova.blueprints.sync_tokens.confirm_job",
                        lambda app, uid, **kwargs: seen.update(kwargs))
    hub.client.post("/sync/confirm", data={"allow_mass_delete_pull": "1"})
    assert seen["allow_pull_mass_delete"] is True
    assert seen["allow_push_mass_delete"] is False
    # The old one-switch field is no longer an approval of anything.
    seen.clear()
    hub.client.post("/sync/confirm", data={"allow_mass_delete": "1"})
    assert seen["allow_pull_mass_delete"] is False
    assert seen["allow_push_mass_delete"] is False


def test_completion_reports_files_that_could_not_be_restored(hub, monkeypatch, tmp_path):
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 0, "down": 0, "down_bytes": 0}},
                   result={"up": {"created": 1, "updated": 0, "deleted": 0},
                           "down": {"created": 0, "updated": 0, "deleted": 0},
                           "unrestored_files": 2, "pending_files": 2})
    first = hub.client.get("/sync").data.decode()
    assert "Sync complete: 1 sent, 0 received" in first
    assert "2 files could not be restored" in first
    assert "could not be restored" not in hub.client.get("/sync").data.decode()

    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "download",
                            "files": {"incoming": 3, "bytes": 1, "replaced": 0}},
                   result={"down": {"astro_objects": 1}, "unrestored_files": 1})
    assert "1 file could not be restored" in hub.client.get("/sync").data.decode()


def test_completion_names_a_blocked_project(hub, monkeypatch, tmp_path):
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 0, "down": 0, "down_bytes": 0}},
                   result={"up": {"created": 1, "updated": 0, "deleted": 0},
                           "down": {"created": 0, "updated": 0, "deleted": 0},
                           "blocked": [{"name": "Veil mosaic", "side": "server"},
                                       {"name": "Rosette", "side": "local"}]})
    body = hub.client.get("/sync").data.decode()
    assert "Veil mosaic" in body and "another account on the server" in body
    assert "Rosette" in body and "another account on this computer" in body


def test_a_clean_completion_adds_no_warning(hub, monkeypatch, tmp_path):
    _seed_done_job(hub, monkeypatch, tmp_path,
                   summary={"kind": "sync",
                            "files": {"up": 0, "down": 0, "down_bytes": 0}},
                   result={"up": {"created": 1, "updated": 0, "deleted": 0},
                           "down": {"created": 0, "updated": 0, "deleted": 0},
                           "unrestored_files": 0, "blocked": []})
    body = hub.client.get("/sync").data.decode()
    assert "could not be restored" not in body
    assert "was not synced" not in body


def test_connecting_to_a_server_without_sync_says_so(hub, monkeypatch):
    from nova.sync_engine import Refusal, SyncRefused

    class _OldServer:
        def __init__(self, *_a, **_k):
            pass

        def hello(self):
            raise SyncRefused(Refusal("server_no_sync", {"status": 404}))
    monkeypatch.setattr("nova.blueprints.sync_tokens.SyncClient", _OldServer)

    resp = hub.client.post("/sync/connect", data={
        "server_url": "https://old.example", "token": "t"}, follow_redirects=True)

    body = resp.data.decode()
    assert "support sync — it needs a newer Nova version." in body
    assert "Could not reach the server" not in body
    hub.db.expire_all()
    assert hub.db.query(SyncState).filter_by(user_id=hub.user.id).count() == 0
