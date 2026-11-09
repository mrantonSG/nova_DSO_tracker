"""Instance sync, step 5 — the hub's HTTP API: authentication, the gate, the
lock, and the two read endpoints.

The push flow itself (plan → chunks → apply), the snapshot pager and the file
fetcher live in tests/test_sync_api_files.py.

The API is token-only, so the properties that matter here are the ones a
token-only surface can get wrong: it must be unreachable without a token, it
must not fall into the session/login/telemetry machinery, every refusal must
carry a code, and no credential may be written anywhere.
"""

import os
import subprocess
import sys
import time
from datetime import timedelta

import pytest

import nova
from nova.models import INSTANCE_PATH, DbUser, Location, SyncToken
from nova.sync_time import utc_now
from nova.sync_transport import (
    MAX_JSON_BYTES, PROTOCOL_VERSION, hash_token, new_token,
)

HELLO = "/api/sync/v1/hello"
CHANGES = "/api/sync/v1/changes"
CANCEL = "/api/sync/v1/push/cancel"
SNAPSHOT = "/api/sync/v1/snapshot"
PUSH_PLAN = "/api/sync/v1/push/plan"
PUSH_FILE = "/api/sync/v1/push/file"
PUSH_APPLY = "/api/sync/v1/push/apply"
FETCH_FILE = "/api/sync/v1/file"
REPAIR = "/api/sync/v1/repair"

ALL_SYNC_ENDPOINTS = (
    "sync_api.hello", "sync_api.changes", "sync_api.snapshot",
    "sync_api.fetch_file", "sync_api.push_plan", "sync_api.push_file",
    "sync_api.push_apply", "sync_api.push_cancel", "sync_api.repair",
)


# --- Fixtures and helpers ------------------------------------------------------

class Hub:
    """The app as the hub: a logged-in client, its account, and a token."""

    def __init__(self, client, db, user, token):
        self.client = client
        self.db = db
        self.user = user
        self.uid = user.id
        self.token = token


@pytest.fixture
def hub(su_client_logged_in, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    token = _mint(db_session, user.id)
    return Hub(su_client_logged_in, db_session, user, token)


def _mint(db, user_id, label="test"):
    plaintext = new_token()
    db.add(SyncToken(user_id=user_id, token_hash=hash_token(plaintext),
                     label=label, created_at=utc_now()))
    db.commit()
    return plaintext


def _api(hub, path, token=None, body=None, headers=None, raw=None, protocol=1):
    sent = {}
    if protocol is not None:
        sent["X-Nova-Sync-Protocol"] = str(protocol)
    sent.update(headers or {})
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    client = hub.client if isinstance(hub, Hub) else hub
    if raw is not None:
        return client.post(path, data=raw, headers=sent,
                           content_type="application/octet-stream")
    return client.post(path, json=body if body is not None else {}, headers=sent)


def _codes(response):
    return [r["code"] for r in response.get_json()["refusals"]]


def _token_row(hub, plaintext=None):
    hub.db.expire_all()
    digest = hash_token(plaintext or hub.token)
    return hub.db.query(SyncToken).filter_by(token_hash=digest).one()


def _location(db, user_id, name="Home", **extra):
    row = Location(user_id=user_id, name=name, lat=48.0, lon=16.0,
                   timezone="UTC", **extra)
    db.add(row)
    db.commit()
    db.expire_all()
    return row


def _names(payload, table="locations"):
    """Location names in a payload. An empty table is omitted, not listed."""
    return [r["name"] for r in payload["records"].get(table, [])]


# =============================================================================
# 1. The login gate
# =============================================================================

def test_every_sync_api_endpoint_is_exempt_from_the_login_gate():
    """Enumerated from the url_map, so a new endpoint cannot be forgotten."""
    registered = {rule.endpoint for rule in nova.app.url_map.iter_rules()
                  if rule.endpoint.startswith("sync_api.")}
    assert registered == set(ALL_SYNC_ENDPOINTS)
    for endpoint in registered:
        assert endpoint in nova.GATE_EXEMPT_ENDPOINTS, endpoint


def test_every_sync_api_endpoint_is_a_post_under_the_api_prefix():
    for rule in nova.app.url_map.iter_rules():
        if rule.endpoint.startswith("sync_api."):
            assert rule.rule.startswith("/api/sync/v1/"), rule.rule
            assert rule.methods == {"POST", "OPTIONS"}, rule.endpoint


def test_an_anonymous_sync_request_gets_a_refusal_not_a_login_redirect(
        mu_client_logged_out):
    """The gate would answer with an HTML redirect; the token layer must answer
    with a code, or a client cannot tell "not paired" from "not logged in"."""
    resp = _api(mu_client_logged_out, HELLO)
    assert resp.status_code == 401
    assert _codes(resp) == ["token_invalid"]
    assert resp.is_json


@pytest.fixture
def guest_client(mu_client_logged_out):
    """The multi-user client, entered through /guest. Its requests are the ones
    that would otherwise provision guest_user and count a visit, so every
    assertion below has a control leg that uses this same session."""
    with mu_client_logged_out.session_transaction() as sess:
        sess["guest_mode"] = True
    return mu_client_logged_out


def test_the_sync_api_does_not_create_a_db_user(guest_client, db_session,
                                                monkeypatch):
    """The gate's guest branch provisions guest_user; a sync request must not."""
    calls = []
    real = nova.get_or_create_db_user

    def spy(db, username, *a, **kw):
        calls.append(username)
        return real(db, username, *a, **kw)

    monkeypatch.setattr(nova, "get_or_create_db_user", spy)
    before = db_session.query(DbUser).count()

    _api(guest_client, HELLO)
    assert calls == []
    assert db_session.query(DbUser).count() == before

    # Control: the same session asking for the token page does provision.
    guest_client.get("/sync")
    assert calls == ["guest_user"]


def test_the_sync_api_is_not_counted_as_a_visit(guest_client, monkeypatch):
    monkeypatch.setattr(nova, "_recent_visitors", {})
    monkeypatch.setitem(nova.app.config, "TESTING", False)  # arm the hook

    _api(guest_client, HELLO)
    assert nova._recent_visitors == {}

    # Control: the gate's guest branch is counted, so the hook really is armed.
    guest_client.get("/sync")
    assert "guest_user" in nova._recent_visitors


def test_the_sync_api_does_not_consume_the_telemetry_bootstrap_slot(
        guest_client, monkeypatch):
    started = []
    monkeypatch.setattr(nova, "_start_telemetry_scheduler_once",
                        lambda: started.append(1))

    _api(guest_client, HELLO)
    assert started == []

    guest_client.get("/sync")       # control: a page view does start it
    assert started == [1]


def test_the_sync_api_records_no_analytics_event(hub, monkeypatch):
    import nova.blueprints.core as core_module

    events = []
    monkeypatch.setattr(core_module, "record_event", events.append)

    _api(hub, HELLO, token=hub.token)
    _api(hub, CHANGES, token=hub.token, body={"since_rev": 0})
    assert events == []

    hub.client.get("/")                      # control: the dashboard records one
    assert events == ["dashboard_load"]


# =============================================================================
# 2. Protocol and token
# =============================================================================

def test_a_missing_protocol_header_is_refused(hub):
    resp = _api(hub, HELLO, token=hub.token, protocol=None)
    assert resp.status_code == 400
    assert _codes(resp) == ["protocol_mismatch"]
    assert _token_row(hub).last_used_at is None


def test_a_foreign_protocol_version_is_refused_before_the_token_is_read(hub):
    """The order matters: a client built for another version is told so without
    a database read, and its valid token must not be touched."""
    resp = _api(hub, HELLO, token=hub.token, protocol=PROTOCOL_VERSION + 1)
    assert resp.status_code == 400
    body = resp.get_json()
    assert _codes(resp) == ["protocol_mismatch"]
    assert body["refusals"][0]["detail"] == {"supported": PROTOCOL_VERSION,
                                            "found": str(PROTOCOL_VERSION + 1)}
    assert "server_time" not in body
    assert _token_row(hub).last_used_at is None


def test_an_absent_and_an_unknown_token_are_refused_identically(hub):
    absent = _api(hub, HELLO)
    unknown = _api(hub, HELLO, token=new_token())
    assert absent.status_code == unknown.status_code == 401
    assert _codes(absent) == _codes(unknown) == ["token_invalid"]
    # Identical bodies: the endpoint must not say which hashes exist.
    assert absent.get_json() == unknown.get_json()


def test_a_malformed_authorization_header_is_refused(hub):
    for value in ("", "Bearer", "Bearer ", "Basic abc", hub.token):
        resp = _api(hub, HELLO, headers={"Authorization": value})
        assert resp.status_code == 401, value
        assert _codes(resp) == ["token_invalid"], value


def test_the_guest_accounts_token_is_refused(db_session):
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    token = _mint(db_session, guest.id, "guest token")

    with nova.app.test_client() as client:
        resp = _api(client, HELLO, token=token)
    assert resp.status_code == 403
    assert _codes(resp) == ["guest_account"]


def test_a_token_that_outlives_its_user_is_refused(hub):
    """user_id carries no FK, so a deleted account can leave a live token."""
    ghost = DbUser(username="ghost")
    hub.db.add(ghost)
    hub.db.commit()
    token = _mint(hub.db, ghost.id, "orphan")

    hub.db.delete(hub.db.query(DbUser).filter_by(id=ghost.id).one())
    hub.db.commit()

    resp = _api(hub, HELLO, token=token)
    assert resp.status_code == 401
    assert _codes(resp) == ["token_invalid"]


def test_last_used_is_stamped_once_and_then_throttled(hub):
    assert _token_row(hub).last_used_at is None

    _api(hub, HELLO, token=hub.token)
    first = _token_row(hub).last_used_at
    assert first is not None

    _api(hub, HELLO, token=hub.token)
    assert _token_row(hub).last_used_at == first

    # ...but the window is a window: an old stamp is refreshed.
    row = _token_row(hub)
    row.last_used_at = utc_now() - timedelta(seconds=61)
    hub.db.commit()
    _api(hub, HELLO, token=hub.token)
    assert _token_row(hub).last_used_at > utc_now() - timedelta(seconds=30)


def test_the_refusal_payload_carries_the_protocol_version(hub):
    for resp in (_api(hub, HELLO), _api(hub, HELLO, token=hub.token,
                                        protocol=99)):
        assert resp.get_json()["protocol"] == PROTOCOL_VERSION


# =============================================================================
# 3. The per-account lock, across processes
# =============================================================================

HOLD_LOCK = """
import fcntl, os, sys
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
sys.stdout.write("locked\\n")
sys.stdout.flush()
sys.stdin.read()
"""


@pytest.fixture
def lock_path(hub):
    return os.path.join(INSTANCE_PATH, f"sync_user_{hub.uid}.lock")


def test_a_sync_request_is_refused_while_another_process_holds_the_lock(
        hub, lock_path):
    """Two Gunicorn workers serving one account: the second must be told to
    retry, not allowed to merge on top of the first."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, lock_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        resp = _api(hub, CHANGES, token=hub.token, body={"since_rev": 0})
        assert resp.status_code == 409
        assert _codes(resp) == ["sync_running"]
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)

    after = _api(hub, CHANGES, token=hub.token, body={"since_rev": 0})
    assert after.status_code == 200


def test_the_lock_is_released_when_the_view_returns(hub, lock_path):
    """Every path out of a locking view leaves the account lockable again."""
    _api(hub, CHANGES, token=hub.token, body={"since_rev": 0})        # answered
    _api(hub, CHANGES, token=hub.token, body={"since_rev": -1})       # refused

    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, lock_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)


# =============================================================================
# 4. Payload validation, before anything touches the database
# =============================================================================

def test_an_oversized_json_body_is_refused_with_413(hub):
    body = b'{"pad":"' + b"x" * MAX_JSON_BYTES + b'"}'
    resp = hub.client.post(
        CHANGES, data=body, content_type="application/json",
        headers={"Authorization": f"Bearer {hub.token}",
                 "X-Nova-Sync-Protocol": "1"},
    )
    assert resp.status_code == 413
    assert _codes(resp) == ["bad_format"]
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "payload_too_large"


def test_a_body_that_is_not_json_is_refused(hub):
    resp = hub.client.post(
        CHANGES, data=b"not json", content_type="application/json",
        headers={"Authorization": f"Bearer {hub.token}",
                 "X-Nova-Sync-Protocol": "1"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "not_json"


@pytest.mark.parametrize("field,value", [
    ("since_rev", -1),
    ("since_rev", "soon"),
    ("since_rev", True),
    ("exclude_revs", "all"),
    ("exclude_revs", [1, "2"]),
])
def test_a_bad_cursor_is_refused(hub, field, value):
    resp = _api(hub, CHANGES, token=hub.token, body={field: value})
    assert resp.status_code == 400
    assert _codes(resp) == ["bad_format"]
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == field


def test_an_unknown_snapshot_table_is_refused(hub):
    resp = _api(hub, SNAPSHOT, token=hub.token, body={"table": "secrets"})
    assert resp.status_code == 400
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "table"


# =============================================================================
# 5. hello
# =============================================================================

def test_hello_reports_the_account_the_clock_and_the_limits(hub):
    _location(hub.db, hub.uid)
    body = _api(hub, HELLO, token=hub.token).get_json()

    assert body["protocol"] == PROTOCOL_VERSION
    assert body["account"] == {"user_id": hub.uid, "username": "default"}
    assert body["app_version"]
    assert body["server_time"]
    assert body["token"]["label"] == "test"
    assert body["token"]["last_used_at"]
    assert body["limits"]["payload_bytes"] == MAX_JSON_BYTES
    assert body["limits"]["file_caps"]
    assert isinstance(body["server_rev"], int)
    assert isinstance(body["untouched"], bool)
    assert isinstance(body["seeded"], bool)


def test_hello_needs_no_session_at_all(mu_client_logged_out, db_session):
    """The same call works for a client that has never logged in."""
    remote = DbUser(username="RemoteUser")
    db_session.add(remote)
    db_session.commit()
    token = _mint(db_session, remote.id, "remote")

    resp = _api(mu_client_logged_out, HELLO, token=token)
    assert resp.status_code == 200
    assert resp.get_json()["account"]["username"] == "RemoteUser"


# =============================================================================
# 6. changes
# =============================================================================

def test_changes_returns_an_engine_payload_for_the_token_account(hub):
    _location(hub.db, hub.uid, "Home")
    mine = _api(hub, CHANGES, token=hub.token,
                body={"since_rev": 0}).get_json()
    assert mine["protocol"] == PROTOCOL_VERSION
    payload = mine["payload"]
    assert payload["side"] == "server"
    names = [r["name"] for r in payload["records"]["locations"]]
    assert "Home" in names
    assert payload["rev"] >= 1


def test_changes_never_shows_another_accounts_rows(hub):
    other = DbUser(username="Other")
    hub.db.add(other)
    hub.db.commit()
    _location(hub.db, other.id, "NotMine")
    _location(hub.db, hub.uid, "Mine")

    payload = _api(hub, CHANGES, token=hub.token,
                   body={"since_rev": 0}).get_json()["payload"]
    names = [r["name"] for r in payload["records"]["locations"]]
    assert "Mine" in names
    assert "NotMine" not in names


def test_changes_honours_the_cursor(hub):
    row = _location(hub.db, hub.uid, "Home")
    payload = _api(hub, CHANGES, token=hub.token,
                   body={"since_rev": row.sync_rev}).get_json()["payload"]
    assert "Home" not in _names(payload)


def test_changes_hides_the_revs_a_pusher_sent(hub):
    """exclude_revs is the no-echo rule: a pusher must not receive back what it
    has just pushed, or it would plan its own edits onto itself."""
    row = _location(hub.db, hub.uid, "Home")
    echoed = _api(hub, CHANGES, token=hub.token,
                  body={"since_rev": 0,
                        "exclude_revs": [row.sync_rev]}).get_json()["payload"]
    assert "Home" not in _names(echoed)

    again = _api(hub, CHANGES, token=hub.token,
                 body={"since_rev": 0}).get_json()["payload"]
    assert "Home" in _names(again)


def test_changes_carries_the_accounts_existing_rows_from_a_zero_cursor(hub):
    """A fresh client starts at 0, so the first call is a full delta."""
    payload = _api(hub, CHANGES, token=hub.token,
                   body={"since_rev": 0}).get_json()["payload"]
    names = [r["name"] for r in payload["records"]["locations"]]
    assert "Default Test Loc" in names
    assert payload["rev"] >= 1
    assert payload["snapshot"] is False


# =============================================================================
# 7. Sentry
# =============================================================================

def _synthetic_event(**request):
    base = {
        "headers": {"Authorization": "Bearer s3cret", "Cookie": "sid=abc",
                    "X-Nova-Sync-Protocol": "1", "User-Agent": "nova"},
        "url": "https://hub.example/api/sync/v1/changes?token=s3cret",
        "query_string": "token=s3cret",
        "data": {"token": "s3cret", "since_rev": 0},
    }
    base.update(request)
    return {"event_id": "abc", "message": "boom", "request": base}


def test_the_scrubber_removes_every_credential_from_a_request_event():
    from nova.sync_transport import scrub_sentry_event

    event = _synthetic_event()
    clean = scrub_sentry_event(event, None)

    assert "Authorization" not in clean["request"]["headers"]
    assert "Cookie" not in clean["request"]["headers"]
    assert "X-Nova-Sync-Protocol" not in clean["request"]["headers"]
    assert clean["request"]["headers"] == {"User-Agent": "nova"}
    assert "query_string" not in clean["request"]
    assert "token" not in clean["request"]["data"]
    assert clean["request"]["url"] == "https://hub.example/api/sync/v1/changes"
    assert "s3cret" not in repr(clean)


def test_the_scrubber_only_ever_removes_fields():
    """A scrubber that could write would be a new way to leak."""
    from nova.sync_transport import scrub_sentry_event

    event = _synthetic_event()
    original = {
        "top": dict(event),
        "request": dict(event["request"]),
        "headers": dict(event["request"]["headers"]),
        "data": dict(event["request"]["data"]),
    }
    clean = scrub_sentry_event(event, None)

    assert set(clean) <= set(original["top"])
    assert set(clean["request"]) <= set(original["request"])
    assert set(clean["request"]["headers"]) <= set(original["headers"])
    assert set(clean["request"]["data"]) <= set(original["data"])
    # Every surviving value is the very object that went in, unmodified.
    assert clean["message"] == original["top"]["message"]
    assert clean["request"]["headers"]["User-Agent"] is \
        original["headers"]["User-Agent"]
    assert clean["request"]["data"] == {"since_rev": 0}
    # A string that changed can only have been shortened.
    assert original["request"]["url"].startswith(clean["request"]["url"])



def test_the_scrubber_keeps_a_query_string_outside_the_sync_api():
    """A query string can legitimately carry a filter elsewhere."""
    from nova.sync_transport import scrub_sentry_event

    event = _synthetic_event(url="https://hub.example/api/chart?object=M42",
                             query_string="object=M42")
    clean = scrub_sentry_event(event, None)
    assert clean["request"]["query_string"] == "object=M42"
    assert clean["request"]["url"].endswith("object=M42")


def test_the_scrubber_handles_headers_as_a_list_and_a_missing_request():
    from nova.sync_transport import scrub_sentry_event

    event = {"request": {"headers": [["Authorization", "Bearer s3cret"],
                                     ["User-Agent", "nova"]]}}
    clean = scrub_sentry_event(event, None)
    assert clean["request"]["headers"] == [["User-Agent", "nova"]]

    assert scrub_sentry_event({"message": "no request"}, None) == {"message": "no request"}
    assert scrub_sentry_event(None, None) is None


def test_the_scrubber_is_installed_as_the_sentry_before_send_hook():
    """A hook nothing calls changes nothing, so the wiring is checked directly.

    The app only initialises Sentry when a DSN is configured at import time, so
    this reads the app factory rather than standing one up: it asserts the one
    line that matters, that init() is handed the hook.
    """
    with open(nova.__file__, encoding="utf-8") as handle:
        text = handle.read()
    assert "before_send=scrub_sentry_event" in text
    assert "from nova.sync_transport import scrub_sentry_event" in text
    # ...and the name really is importable under that spelling.
    from nova.sync_transport import scrub_sentry_event as hook
    assert callable(hook)

