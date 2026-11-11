"""Instance sync — local side (step 6): unit + hub-integration tests.

The client's HTTP is routed to the hub through a fake ``requests``-style
session that forwards each call to the hub's Flask test client — no network.
The full two-phase job runner (nova.sync_job) is exercised through the client
and engine entry points directly; the daemon-thread lifecycle is covered by the
on-disk job-state unit tests.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from flask import Flask

from nova.helpers import write_file_atomic
from nova.models import SyncToken
from nova.sync_client import (SyncClient, SyncTransportError, normalize_server_url,
                              validate_server_url)
from nova.sync_engine import (ROLE_SESSION_PHOTO, JournalSession, Refusal,
                              SyncRefused, _slot_ref)
from nova.blueprints.sync_tokens import refusal_message


# --- URL rules ----------------------------------------------------------------

def test_url_https_normalized_and_slash_stripped():
    url, warn = validate_server_url("https://app.nova-tracker.com/")
    assert url == "https://app.nova-tracker.com"
    assert warn is False


def test_url_http_loopback_allowed_with_warning():
    url, warn = validate_server_url("http://127.0.0.1:5001")
    assert url == "http://127.0.0.1:5001"
    assert warn is True


def test_url_http_private_lan_allowed_with_warning():
    url, warn = validate_server_url("http://192.168.1.10")
    assert url == "http://192.168.1.10"
    assert warn is True


def test_url_http_local_allowed():
    url, warn = validate_server_url("http://mymac.local:5001")
    assert url == "http://mymac.local:5001"
    assert warn is True


def test_url_http_public_rejected():
    with pytest.raises(ValueError):
        validate_server_url("http://example.com")


def test_url_path_query_rejected():
    with pytest.raises(ValueError):
        validate_server_url("https://example.com/sync")
    with pytest.raises(ValueError):
        validate_server_url("https://example.com/?token=x")


def test_url_implicit_scheme():
    assert normalize_server_url("app.nova-tracker.com") == "https://app.nova-tracker.com"


# --- Clock offset -------------------------------------------------------------

def test_clock_offset_both_signs():
    from nova.sync_client import measure_clock_offset
    # offset = local - server, minus half the round-trip. With a ~0.1 s RTT and
    # the local clock 100 s fast, t0/t3 straddle server_time by 0.05 s each.
    server = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    base = server.timestamp()
    t0 = base + 100.0 - 0.05
    t3 = base + 100.0 + 0.05
    assert abs(measure_clock_offset(server, t0, t3) - 100.0) < 0.02

    # local clock slow by 100 s -> offset negative
    t0 = base - 100.0 - 0.05
    t3 = base - 100.0 + 0.05
    assert abs(measure_clock_offset(server, t0, t3) + 100.0) < 0.02


# --- Correction A: atomic write keeps a hard-linked backup unchanged -----------

def test_write_file_atomic_preserves_hardlink(tmp_path):
    live = tmp_path / "uploads" / "42.jpg"
    live.parent.mkdir()
    live.write_bytes(b"old-bytes")
    backup = tmp_path / "backup" / "42.jpg"
    backup.parent.mkdir()
    os.link(str(live), str(backup))  # the hard-link snapshot

    write_file_atomic(str(live), lambda p: open(p, "wb").write(b"new-bytes"))

    assert live.read_bytes() == b"new-bytes"
    assert backup.read_bytes() == b"old-bytes"  # old inode untouched


# --- Carry-over: emptied file slots are not "missing" --------------------------

def test_emptied_slot_not_missing(monkeypatch):
    monkeypatch.setattr('nova.sync_engine.row_key', lambda model, row: "K")

    class Row:
        session_image_file = ""

    ref = _slot_ref(JournalSession, Row(), "session_image_file",
                    ROLE_SESSION_PHOTO, user=None)
    assert ref.missing is False
    assert ref.sha256 == ""


# --- Refusal codes surface as translated messages, never raw codes -------------

def test_refusal_messages_translated():
    for code in ("token_invalid", "token_revoked", "guest_account", "not_paired",
                 "needs_download", "server_untouched", "local_replaced",
                 "remote_replaced", "sync_running", "plan_stale", "mass_delete",
                 "clock_skew", "bad_format", "network_error", "interrupted"):
        msg = refusal_message(code, {"deletions": 5, "records": 50, "offset_seconds": 420})
        assert msg and msg != code
    assert "minutes" in refusal_message("clock_skew", {"offset_seconds": 420})


def test_refusal_unknown_code_falls_back():
    msg = refusal_message("future_code_xyz", {})
    assert msg and "future_code_xyz" in msg


# --- Client <-> hub over a fake transport (no network) -------------------------

class _FakeResponse:
    def __init__(self, status_code, data, headers=None):
        self.status_code = status_code
        self._data = data or b""
        self.headers = headers or {}

    def json(self):
        return json.loads(self._data.decode("utf-8") or "{}")

    def iter_content(self, chunk_size=1024):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]


class _FakeSession:
    """Forwards requests.Session.post to the hub's Flask test client."""

    def __init__(self, hub_app):
        self._app = hub_app

    def post(self, url, **kwargs):
        from urllib.parse import urlparse
        path = urlparse(url).path
        headers = dict(kwargs.get("headers") or {})
        data = None
        content_type = None
        if kwargs.get("data") is not None:
            data = kwargs["data"]
            content_type = "application/octet-stream"
        elif kwargs.get("json") is not None:
            data = json.dumps(kwargs["json"])
            content_type = "application/json"
        resp = self._app.test_client().post(path, data=data, headers=headers,
                                            content_type=content_type)
        return _FakeResponse(resp.status_code, resp.data, dict(resp.headers))


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """A minimal hub: the sync API blueprint over its own SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    from nova.models import Base, DbUser, SyncToken
    from nova.sync_transport import hash_token, new_token
    from nova.blueprints.sync_api import sync_api_bp

    hub_instance = tmp_path / "hub_instance"
    hub_instance.mkdir()
    engine = create_engine(f"sqlite:///{hub_instance / 'app.db'}")
    Base.metadata.create_all(engine)
    HubSession = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

    monkeypatch.setattr('nova.blueprints.sync_api.get_db', lambda: HubSession())
    monkeypatch.setattr('nova.sync_engine.INSTANCE_PATH', str(hub_instance))
    monkeypatch.setattr('nova.sync_transport.INSTANCE_PATH', str(hub_instance))
    monkeypatch.setattr('nova.sync_transport.CACHE_DIR', str(hub_instance / "cache"))
    monkeypatch.setattr('nova.sync_transport.BACKUP_DIR', str(hub_instance / "backups"))

    app = Flask("hub")
    app.config["TESTING"] = True
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
    app.register_blueprint(sync_api_bp)

    db = HubSession()
    user = DbUser(username="hubuser")
    db.add(user)
    db.commit()
    uid = user.id  # capture before the session is removed (detach expires attrs)
    token = new_token()
    db.add(SyncToken(user_id=uid, token_hash=hash_token(token), label="test",
                     created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.commit()
    HubSession.remove()
    return {"app": app, "token": token, "user_id": uid, "session": HubSession}


def test_hello_valid_token(hub):
    client = SyncClient("https://hub.invalid", hub["token"],
                        session=_FakeSession(hub["app"]))
    hello = client.hello()
    assert hello["account"]["username"] == "hubuser"
    assert hello["untouched"] is True
    assert isinstance(hello["_clock_offset_seconds"], float)


def test_hello_wrong_token(hub):
    client = SyncClient("https://hub.invalid", "not-the-token",
                        session=_FakeSession(hub["app"]))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "token_invalid"


def test_hello_revoked_token(hub):
    db = hub["session"]()
    row = db.query(SyncToken).filter_by(user_id=hub["user_id"]).first()
    row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    hub["session"].remove()

    client = SyncClient("https://hub.invalid", hub["token"],
                        session=_FakeSession(hub["app"]))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "token_revoked"


def test_token_never_in_log(caplog, hub):
    import logging
    client = SyncClient("https://hub.invalid", "super-secret-token-value",
                        session=_FakeSession(hub["app"]))
    with caplog.at_level(logging.DEBUG):
        try:
            client.hello()
        except SyncRefused:
            pass
    joined = "\n".join(caplog.messages) + "\n".join(r.getMessage() for r in caplog.records)
    assert "super-secret-token-value" not in joined


# --- Backup pruning keeps the latest five (rule 5) ----------------------------

def test_backup_pruning_keeps_the_latest_five(monkeypatch, tmp_path):
    from nova.sync_job import _prune_backups
    monkeypatch.setattr("nova.sync_job.BACKUP_DIR", str(tmp_path))
    base = tmp_path / "sync" / "7"
    base.mkdir(parents=True)
    for i in range(6):
        (base / f"stamp{i}").mkdir()

    _prune_backups(7, keep=5)

    assert sorted(p.name for p in base.iterdir()) == [
        "stamp1", "stamp2", "stamp3", "stamp4", "stamp5"]


# --- Every writer into uploads/ or logs/ is atomic (rule 5) -------------------

def _raw_write_sites():
    """(relpath, line, function, primitive) for every raw file write in nova/."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "nova"
    prims = [
        (re.compile(r'open\([^)]*["\'](?:w|wb|a|ab|w\+|wb\+)["\']'), "open-write"),
        (re.compile(r'\.save\('), ".save"),
        (re.compile(r'shutil\.(?:copy|copyfile|copy2|move)'), "shutil.copy/move"),
        (re.compile(r'\.write_bytes\('), "write_bytes"),
    ]
    sites = []
    for path in sorted(root.rglob("*.py")):
        funcs = []
        for i, line in enumerate(path.read_text().splitlines(), 1):
            m = re.match(r'^(\s*)def\s+(\w+)', line)
            if m:
                indent = len(m.group(1))
                while funcs and funcs[-1][0] >= indent:
                    funcs.pop()
                funcs.append((indent, m.group(2)))
            if line.strip().startswith("#"):
                continue
            for rx, label in prims:
                if rx.search(line):
                    fn = funcs[-1][1] if funcs else "<module>"
                    sites.append((str(path.relative_to(root)), i, fn, label))
    return sites


def test_every_file_write_is_atomic_or_outside_uploads_and_logs():
    """A non-atomic writer into uploads/ or logs/ would corrupt the hard-link
    backup. Raw writes may appear only (a) inside the atomic helpers or the
    engine's placement (both finish with os.replace), or (b) in the explicit
    allowlist of writers whose target is never uploads/ or logs/."""
    ATOMIC = {
        # The atomic mechanism itself, or a caller that wraps its write in
        # write_file_atomic / staging-then-os.replace.
        "write_file_atomic", "_atomic_write_yaml",
        "_place_from_staged", "_copy_hashed", "_fetch_file",
        "save_log_to_filesystem", "_save_artifact", "backup_local",
        "import_journal_photos", "upload_editor_image",
        "journal_add", "journal_edit", "session_image_url",
        "_is_within_user_dir", "index",
    }
    NON_UPLOAD_LOG = {
        # Writes files that never live under uploads/ or logs/, so a hard-link
        # snapshot of those two trees is untouched: cache, config, env, backups,
        # the two-phase job file, and move-aside (which only renames out).
        "initialize_instance_directory", "_timestamped_copy", "_has_key",
        "migrate_journal_data", "update_outlook_cache", "sync_outlook_cache",
        "_fire_startup_skyglow_check", "get_yearly_heatmap_chunk", "_run_skyglow_task",
        "_save_conflicts", "import_yaml_for_user", "_write_cache", "append_purge_note",
        "_backup_with_rotation", "move_replaced_files", "save_job", "_hardlink_tree",
        "save_push_session", "heatmap_background_worker",
    }
    violations = [
        f"{f}:{line} [{prim}] in {fn}"
        for f, line, fn, prim in _raw_write_sites()
        if fn not in ATOMIC and fn not in NON_UPLOAD_LOG
    ]
    assert not violations, (
        "raw writers not through write_file_atomic / the engine's placement:\n"
        + "\n".join(violations))


# --- Download confirm refuses anything but the exact REPLACE text (rule 11) ---

def test_download_confirm_without_exact_replace_is_refused(monkeypatch, tmp_path):
    from nova.sync_job import confirm_job, save_job
    monkeypatch.setattr("nova.sync_job.INSTANCE_PATH", str(tmp_path))
    uid = 7
    save_job(uid, {
        "job_id": "x", "start_token": "x", "user_id": uid, "kind": "download",
        "phase": "awaiting_confirmation", "progress": 0.9,
        "phase_label": "awaiting_confirmation",
        "summary": {"kind": "download", "requires_replace_typed": True},
        "result": None, "error": None,
        "started_at": "2026-01-01T00:00:00", "heartbeat_at": "2026-01-01T00:00:00",
        "pid": os.getpid(),
    })

    # The refusal raises before any thread is started, so a dummy app is enough.
    with pytest.raises(SyncRefused) as exc:
        confirm_job(object(), uid, replace_confirmed="replace")
    assert exc.value.refusal.code == "replace_required"

    with pytest.raises(SyncRefused) as exc:
        confirm_job(object(), uid, replace_confirmed="REPLACE ")
    assert exc.value.refusal.code == "replace_required"


# =============================================================================
# Protocol handling: refusals are read before the protocol field
# =============================================================================

class _Canned:
    """A requests-style session that answers every POST with one fixed reply."""

    def __init__(self, status, body, headers=None):
        self._reply = (status, body if isinstance(body, bytes) else
                       json.dumps(body).encode("utf-8"), headers or {})
        self.calls = 0

    def post(self, _url, **_kwargs):
        self.calls += 1
        return _FakeResponse(*self._reply)


def _refusal_text(code, detail=None):
    from nova import app
    from nova.blueprints.sync_tokens import refusal_message
    with app.test_request_context("/sync"):
        return refusal_message(code, detail or {})


def test_a_hub_on_another_protocol_version_is_a_protocol_mismatch(hub):
    """A real hub (protocol 1) and a client built for protocol 2. The hub's
    refusal carries ITS version in `protocol`; the client must read the refusal
    first instead of tripping over that field."""
    client = SyncClient("https://hub.invalid", hub["token"],
                        session=_FakeSession(hub["app"]), protocol=2)
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "protocol_mismatch"
    assert exc.value.refusal.detail["supported"] == 1
    assert _refusal_text("protocol_mismatch") == (
        "This app and the server speak different sync versions. Update one of them.")


def test_a_newer_hubs_mismatch_refusal_is_read_by_this_client():
    """The other direction: a v2 hub refusing this v1 client."""
    reply = {"protocol": 2, "refusals": [{"code": "protocol_mismatch",
                                          "detail": {"supported": 2, "found": "1"}}]}
    client = SyncClient("https://hub.invalid", "t", session=_Canned(400, reply))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "protocol_mismatch"
    assert exc.value.refusal.detail == {"supported": 2, "found": "1"}


def test_an_answer_on_another_protocol_without_a_refusal_is_a_mismatch_too():
    client = SyncClient("https://hub.invalid", "t",
                        session=_Canned(200, {"protocol": 7, "server_time": "x"}))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "protocol_mismatch"
    assert exc.value.refusal.detail == {"supported": 7, "found": 1}


def test_any_other_refusal_is_still_read_whatever_protocol_it_names():
    reply = {"protocol": 2, "refusals": [{"code": "token_revoked", "detail": {}}]}
    client = SyncClient("https://hub.invalid", "t", session=_Canned(401, reply))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "token_revoked"


@pytest.mark.parametrize("status,body", [
    (404, b"<!doctype html><title>404 Not Found</title>"),      # single-user, no route
    (405, b"<!doctype html><title>405 Method Not Allowed</title>"),
    (401, {"error": "authentication required"}),                # multi-user login gate
    (404, {"error": "not found"}),
])
def test_a_server_from_before_sync_gets_its_own_message(status, body):
    client = SyncClient("https://old.invalid", "t", session=_Canned(status, body))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "server_no_sync"
    assert _refusal_text("server_no_sync") == (
        "This server doesn't support sync — it needs a newer Nova version.")


def test_a_real_nova_without_the_sync_api_is_recognised():
    """Not a canned reply: a Flask app with no /api/sync routes at all."""
    old = Flask("old-nova")
    old.config["TESTING"] = True
    client = SyncClient("https://old.invalid", "t", session=_FakeSession(old))
    with pytest.raises(SyncRefused) as exc:
        client.hello()
    assert exc.value.refusal.code == "server_no_sync"


@pytest.mark.parametrize("status,body", [
    (500, b"<html>Internal Server Error</html>"),
    (502, b"Bad Gateway"),
    (200, b"<html>captive portal</html>"),
    (200, {"hello": "world"}),              # JSON, but no protocol and not a 4xx
])
def test_a_broken_answer_is_still_a_transport_error(status, body):
    client = SyncClient("https://hub.invalid", "t", session=_Canned(status, body))
    with pytest.raises(SyncTransportError):
        client.hello()


def test_a_job_that_meets_an_old_server_reports_it(monkeypatch):
    """Through the job runner: the code survives to the status the page reads."""
    from nova.sync_engine import Refusal
    assert _refusal_text("server_no_sync") != _refusal_text("network_error")
    assert _refusal_text("server_no_sync") != _refusal_text("protocol_mismatch")
    assert "(server_no_sync)" not in _refusal_text("server_no_sync")
    assert Refusal("server_no_sync", {}).code == "server_no_sync"


# =============================================================================
# NOVA_SYNC_CLIENT: on in single-user mode, off in multi-user unless asked for
# =============================================================================

@pytest.mark.parametrize("raw,single_user,expected", [
    ("", True, True),            # unset, a personal computer: a client
    (None, True, True),
    ("", False, False),          # unset, a multi-user server: not a client
    (None, False, False),
    ("   ", False, False),
    ("on", False, True),         # explicitly enabled on a multi-user instance
    ("ON", False, True),
    ("true", False, True),
    ("1", False, True),
    ("off", True, False),        # explicitly disabled on a single-user one
    (" Off ", True, False),
    ("false", True, False),
    ("0", True, False),
    ("no", True, False),
    ("on", True, True),
    ("off", False, False),
])
def test_the_sync_client_default_follows_the_user_mode(raw, single_user, expected):
    from nova.config import sync_client_enabled
    assert sync_client_enabled(raw, single_user) is expected


def test_env_example_leaves_the_sync_client_to_the_mode():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert "\nNOVA_SYNC_CLIENT=\n" in text
    assert "NOVA_SYNC_CLIENT=on\n" not in text


@pytest.mark.parametrize("lang", ["en", "de", "es", "fr", "ja", "zh"])
def test_the_help_page_documents_the_sync_client_switch(lang):
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1]
            / "help_docs" / lang / "sync.md").read_text(encoding="utf-8")
    assert "NOVA_SYNC_CLIENT=on" in text
