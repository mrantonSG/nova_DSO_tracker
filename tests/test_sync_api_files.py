"""Instance sync, step 5 — the push flow, the paged snapshot, file fetches,
repair, the pre-push backup and purge.

A push is three requests because a file cannot ride in one: plan opens a
session, chunks fill the accumulator (resumably), apply re-plans from the
payload stored on disk and commits. Everything that has to survive between the
requests is therefore tested across separate client objects, which is what a
second Gunicorn worker or a restarted client looks like from here.

The hub in these tests is the Flask app itself; the sender is a separate
in-memory installation with its own instance directory, so a payload and its
bytes really cross a boundary.
"""

import json
import os
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

import nova
from nova.config import BACKUP_DIR, CACHE_DIR, UPLOAD_FOLDER
from nova.helpers import purge_user_app_data
from nova.models import (
    INSTANCE_PATH, Base, DbUser, JournalSession, Location, Project, SyncToken,
)
from nova.sync_engine import export_changes
from nova.sync_time import utc_now
from nova.sync_transport import (
    CHUNK_BYTES, PROTOCOL_VERSION, hash_token, load_push_session, new_token,
    push_session_path, snapshot_account_yaml, backup_dirs,
)

PLAN = "/api/sync/v1/push/plan"
FILE = "/api/sync/v1/push/file"
APPLY = "/api/sync/v1/push/apply"
CANCEL = "/api/sync/v1/push/cancel"
SNAPSHOT = "/api/sync/v1/snapshot"
FETCH = "/api/sync/v1/file"
REPAIR = "/api/sync/v1/repair"
CHANGES = "/api/sync/v1/changes"

PNG = b"\x89PNG\r\n\x1a\n" + b"project-image-bytes" * 8
BIG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 10000 + b"tail"   # ~2.5 MB


# --- The sender: another installation in this process --------------------------

class Sender:
    """A separate Nova installation: its own engine, user and instance dir."""

    def __init__(self, name, root):
        self.name = name
        self.instance_path = str(root)
        self.uploads = os.path.join(self.instance_path, "uploads", name)
        os.makedirs(self.uploads, exist_ok=True)
        self.engine = create_engine(
            "sqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = scoped_session(
            sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        )
        self.db = self.Session()
        self.user = DbUser(username=name)
        self.db.add(self.user)
        self.db.commit()

    @property
    def uid(self):
        return self.user.id

    def commit(self):
        self.db.commit()
        self.db.expire_all()

    def put(self, filename, data):
        with open(os.path.join(self.uploads, filename), "wb") as handle:
            handle.write(data)

    def payload(self, side="local"):
        """What this installation would POST for a push."""
        return export_changes(self.db, self.user, 0, side=side,
                              instance_path=self.instance_path)

    def close(self):
        self.db.rollback()
        self.Session.remove()
        self.engine.dispose()


# --- The hub: the Flask app ----------------------------------------------------

class Hub:
    def __init__(self, client, db, user, token):
        self.client = client
        self.db = db
        self.user = user
        self.uid = user.id
        self.token = token


@pytest.fixture
def hub(su_client_logged_in, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    plaintext = new_token()
    db_session.add(SyncToken(user_id=user.id, token_hash=hash_token(plaintext),
                             label="test", created_at=utc_now()))
    db_session.commit()
    return Hub(su_client_logged_in, db_session, user, plaintext)


@pytest.fixture
def sender(tmp_path):
    node = Sender("sender", tmp_path / "sender")
    yield node
    node.close()


# --- Request helpers -----------------------------------------------------------

def _api(hub, path, token=None, body=None, headers=None, raw=None, client=None):
    sent = {"X-Nova-Sync-Protocol": str(PROTOCOL_VERSION)}
    sent.update(headers or {})
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    target = client or hub.client
    if raw is not None:
        return target.post(path, data=raw, headers=sent,
                           content_type="application/octet-stream")
    return target.post(path, json=body if body is not None else {}, headers=sent)


def _codes(response):
    return [r["code"] for r in response.get_json()["refusals"]]


def _plan(hub, payload, offset=0, client=None, **extra):
    body = {"payload": payload, "clock_offset_seconds": offset}
    body.update(extra)
    return _api(hub, PLAN, token=hub.token, body=body, client=client)


def _apply(hub, session_id, fingerprint, client=None):
    return _api(hub, APPLY, token=hub.token,
                body={"session_id": session_id, "fingerprint": fingerprint},
                client=client)


def _upload(hub, session_id, digest, data, offset=0, client=None):
    """Send the file in chunks, following the server's received count."""
    while offset < len(data):
        part = data[offset:offset + CHUNK_BYTES]
        resp = _api(hub, FILE, token=hub.token, raw=part,
                    headers={"X-Nova-Session": session_id,
                             "X-Nova-Sha256": digest,
                             "X-Nova-Offset": str(offset)}, client=client)
        assert resp.status_code == 200, resp.get_json()
        offset = resp.get_json()["received"]
    return resp


def _import_digest(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _project_with_image(sender, project_id="p1", data=PNG, name="Veil"):
    sender.db.add(Project(id=project_id, user_id=sender.uid, name=name,
                          status="In Progress"))
    sender.commit()
    filename = f"project_{project_id}.png"
    sender.put(filename, data)
    row = sender.db.query(Project).filter_by(id=project_id).one()
    row.final_image_file = filename
    sender.commit()
    return row


def _session_with_photo(hub, external_id="s1", data=PNG):
    row = JournalSession(user_id=hub.uid, external_id=external_id,
                         date_utc=date(2026, 1, 10))
    hub.db.add(row)
    hub.db.commit()
    hub.db.expire_all()
    name = f"{row.id}.png"
    os.makedirs(os.path.join(UPLOAD_FOLDER, "default"), exist_ok=True)
    with open(os.path.join(UPLOAD_FOLDER, "default", name), "wb") as handle:
        handle.write(data)
    row.session_image_file = name
    hub.db.commit()
    return row


# =============================================================================
# 1. A push with a file, across three requests
# =============================================================================

def test_a_push_carries_a_project_image_in_chunks(hub, sender):
    _project_with_image(sender, "p1", BIG)
    payload = sender.payload()
    digest = _import_digest(BIG)

    planned = _plan(hub, payload)
    assert planned.status_code == 200, planned.get_json()
    plan_body = planned.get_json()
    session_id = plan_body["session_id"]
    assert plan_body["fingerprint"]
    assert [ref[3] for ref in plan_body["files_needed"]] == [digest]
    assert plan_body["plan"]["created"].get("projects") == 1
    assert plan_body["plan"]["files"]["bytes"] == len(BIG)

    # The session is on disk, not in a worker's memory.
    stored = load_push_session(hub.uid)
    assert stored["session_id"] == session_id
    assert stored["fingerprint"] == plan_body["fingerprint"]
    assert stored["file_refs"][0]["sha256"] == digest
    assert stored["file_refs"][0]["role"] == "project_image"

    # A brand-new client object is the next request: nothing is remembered
    # in the process between them.
    with nova.app.test_client() as fresh:
        _upload(hub, session_id, digest, BIG, client=fresh)
        applied = _apply(hub, session_id, plan_body["fingerprint"], client=fresh)

    assert applied.status_code == 200, applied.get_json()
    result = applied.get_json()["result"]
    assert result["placed_files"], result
    assert result["pending_files"] == []

    hub.db.expire_all()
    arrived = hub.db.query(Project).filter_by(id="p1").one()
    assert arrived.final_image_file == "project_p1.png"
    with open(os.path.join(UPLOAD_FOLDER, "default", "project_p1.png"),
              "rb") as handle:
        assert handle.read() == BIG

    # The apply consumes the session: a second one is refused as stale.
    again = _apply(hub, session_id, plan_body["fingerprint"])
    assert again.status_code == 409
    assert _codes(again) == ["plan_stale"]


def test_an_upload_resumes_at_the_offset_the_hub_reports(hub, sender):
    _project_with_image(sender, "p1", BIG)
    digest = _import_digest(BIG)
    body = _plan(hub, sender.payload()).get_json()
    session_id = body["session_id"]

    # First chunk, then the connection dies.
    first = _api(hub, FILE, token=hub.token, raw=BIG[:CHUNK_BYTES],
                 headers={"X-Nova-Session": session_id,
                          "X-Nova-Sha256": digest, "X-Nova-Offset": "0"})
    assert first.get_json()["received"] == CHUNK_BYTES

    # The client comes back and asks how much the hub holds.
    probe = _api(hub, FILE, token=hub.token,
                 headers={"X-Nova-Session": session_id,
                          "X-Nova-Sha256": digest, "X-Nova-Offset": "-1"})
    assert probe.status_code == 200
    assert probe.get_json()["received"] == CHUNK_BYTES
    assert probe.get_json()["expected"] == len(BIG)
    assert probe.get_json()["complete"] is False

    _upload(hub, session_id, digest, BIG, offset=CHUNK_BYTES)
    applied = _apply(hub, session_id, body["fingerprint"])
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()["result"]["placed_files"]


def test_a_retried_chunk_is_written_once_not_twice(hub, sender):
    """A dropped response makes the client resend the same chunk; the file must
    not grow by a chunk each retry."""
    _project_with_image(sender, "p1", BIG)
    digest = _import_digest(BIG)
    body = _plan(hub, sender.payload()).get_json()
    session_id = body["session_id"]

    for _ in range(3):
        resp = _api(hub, FILE, token=hub.token, raw=BIG[:CHUNK_BYTES],
                    headers={"X-Nova-Session": session_id,
                             "X-Nova-Sha256": digest, "X-Nova-Offset": "0"})
        assert resp.get_json()["received"] == CHUNK_BYTES

    _upload(hub, session_id, digest, BIG, offset=CHUNK_BYTES)
    assert _apply(hub, session_id, body["fingerprint"]).status_code == 200


def test_a_gap_in_the_upload_is_refused(hub, sender):
    _project_with_image(sender, "p1", BIG)
    digest = _import_digest(BIG)
    body = _plan(hub, sender.payload()).get_json()

    resp = _api(hub, FILE, token=hub.token, raw=BIG[CHUNK_BYTES:2 * CHUNK_BYTES],
                headers={"X-Nova-Session": body["session_id"],
                         "X-Nova-Sha256": digest,
                         "X-Nova-Offset": str(CHUNK_BYTES)})
    assert resp.status_code == 400
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "offset_gap"


def test_bytes_that_do_not_match_the_declared_digest_are_refused(hub, sender):
    _project_with_image(sender, "p1", PNG)
    digest = _import_digest(PNG)
    body = _plan(hub, sender.payload()).get_json()

    # The digest is verified when the last byte arrives, so the garbage must be
    # the declared length: a short body is an incomplete upload, not a bad one.
    resp = _api(hub, FILE, token=hub.token, raw=b"x" * len(PNG),
                headers={"X-Nova-Session": body["session_id"],
                         "X-Nova-Sha256": digest, "X-Nova-Offset": "0"})
    assert resp.status_code == 400
    assert _codes(resp) == ["file_verification_failed"]

    # ...and nothing reached staging, where the name is the digest.
    staged = os.path.join(INSTANCE_PATH, "sync_staging", str(hub.uid))
    assert not os.path.exists(os.path.join(staged, f"{digest}.png"))


def test_a_chunk_for_a_digest_the_plan_did_not_name_is_refused(hub, sender):
    _project_with_image(sender, "p1", PNG)
    body = _plan(hub, sender.payload()).get_json()
    other = _import_digest(b"unrelated bytes")
    resp = _api(hub, FILE, token=hub.token, raw=b"x",
                headers={"X-Nova-Session": body["session_id"],
                         "X-Nova-Sha256": other, "X-Nova-Offset": "0"})
    assert resp.status_code == 409
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "not_in_plan"


def test_a_chunk_for_a_foreign_session_is_refused(hub, sender):
    _project_with_image(sender, "p1", PNG)
    body = _plan(hub, sender.payload()).get_json()
    resp = _api(hub, FILE, token=hub.token, raw=b"x",
                headers={"X-Nova-Session": "someone-else",
                         "X-Nova-Sha256": _import_digest(PNG),
                         "X-Nova-Offset": "0"})
    assert resp.status_code == 409
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "no_session"
    assert load_push_session(hub.uid)["session_id"] == body["session_id"]


def test_a_cancelled_push_drops_the_session_and_the_accumulator(hub, sender):
    _project_with_image(sender, "p1", BIG)
    body = _plan(hub, sender.payload()).get_json()
    digest = _import_digest(BIG)
    _api(hub, FILE, token=hub.token, raw=BIG[:CHUNK_BYTES],
         headers={"X-Nova-Session": body["session_id"],
                  "X-Nova-Sha256": digest, "X-Nova-Offset": "0"})
    assert os.path.isdir(os.path.join(INSTANCE_PATH, "sync_uploads", str(hub.uid)))

    cancelled = _api(hub, CANCEL, token=hub.token, body={})
    assert cancelled.get_json()["cancelled"] is True
    assert not os.path.exists(push_session_path(hub.uid))
    assert not os.path.isdir(os.path.join(INSTANCE_PATH, "sync_uploads", str(hub.uid)))

    # Cancelling nothing is not an error.
    assert _api(hub, CANCEL, token=hub.token, body={}).get_json()["cancelled"] is False


# =============================================================================
# 2. plan_stale: the account moved during the upload
# =============================================================================

def _location_payload(sender, name="Home"):
    sender.db.add(Location(user_id=sender.uid, name=name, lat=48.0, lon=16.0,
                           timezone="UTC"))
    sender.commit()
    return sender.payload()


def test_an_apply_is_refused_when_the_hub_moved_since_the_plan(hub, sender):
    payload = _location_payload(sender)

    body = _plan(hub, payload).get_json()

    # Someone edits the same record on the hub during the upload.
    hub.db.add(Location(user_id=hub.uid, name="Home", lat=49.0, lon=16.0,
                        timezone="UTC"))
    hub.db.commit()

    resp = _apply(hub, body["session_id"], body["fingerprint"])
    assert resp.status_code == 409
    assert _codes(resp) == ["plan_stale"]
    assert not os.path.exists(push_session_path(hub.uid))


def test_an_apply_with_the_wrong_fingerprint_is_refused(hub, sender):
    body = _plan(hub, _location_payload(sender)).get_json()
    resp = _apply(hub, body["session_id"], "not-the-fingerprint")
    assert resp.status_code == 409
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "fingerprint"


def test_an_apply_for_an_unknown_session_is_refused(hub, sender):
    resp = _apply(hub, "nope", "nope")
    assert resp.status_code == 409
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "session"


def test_a_push_that_writes_nothing_takes_no_backup(hub, sender):
    """Data safety rule 1: a push that decides nothing writes no snapshot."""
    before = len(backup_dirs(hub.uid))
    body = _plan(hub, sender.payload()).get_json()      # an empty account
    assert body["plan"]["created"] == {}
    assert _apply(hub, body["session_id"], body["fingerprint"]).status_code == 200
    assert len(backup_dirs(hub.uid)) == before


# =============================================================================
# 3. The pre-push backup
# =============================================================================

def test_a_writing_push_snapshots_the_account_before_it_applies(hub, sender):
    # BACKUP_DIR is shared by the whole test session, so the assertion is a
    # delta: this push adds exactly one snapshot and no more.
    before = len(backup_dirs(hub.uid))
    body = _plan(hub, _location_payload(sender)).get_json()

    assert _apply(hub, body["session_id"], body["fingerprint"]).status_code == 200

    dirs = backup_dirs(hub.uid)
    assert len(dirs) == before + 1
    written = sorted(os.listdir(dirs[0]))
    assert written == ["config_default.yaml", "journal_default.yaml",
                       "rigs_default.yaml"]


def test_the_backup_keeps_only_the_newest_five(hub):
    for _ in range(7):
        assert snapshot_account_yaml(hub.user) is not None
    dirs = backup_dirs(hub.uid)
    assert len(dirs) == 5
    assert dirs == sorted(dirs)


def test_a_failed_backup_does_not_stop_the_push(hub, sender, monkeypatch, caplog):
    """A backup that cannot be written is logged, not fatal: the push is the
    user's intent and the merge engine's rev discipline still holds."""
    import logging
    import nova.migration as migration

    def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(migration, "export_user_to_yaml", broken)
    before = len(backup_dirs(hub.uid))
    body = _plan(hub, _location_payload(sender)).get_json()
    with caplog.at_level(logging.WARNING, logger="nova.sync_transport"):
        resp = _apply(hub, body["session_id"], body["fingerprint"])
    assert resp.status_code == 200
    # No half-written snapshot is left behind, and the failure is visible
    # rather than silent.
    assert len(backup_dirs(hub.uid)) == before
    assert any("snapshot failed" in record.getMessage()
               for record in caplog.records)


# =============================================================================
# 4. The snapshot pager
# =============================================================================

def _locations(hub, count, prefix="Loc"):
    for index in range(count):
        hub.db.add(Location(user_id=hub.uid, name=f"{prefix}{index}",
                            lat=48.0, lon=16.0, timezone="UTC"))
    hub.db.commit()


def _walk_snapshot(hub, table, limit):
    after, pages = 0, []
    while True:
        resp = _api(hub, SNAPSHOT, token=hub.token,
                    body={"table": table, "after_id": after, "limit": limit})
        assert resp.status_code == 200, resp.get_json()
        page = resp.get_json()
        pages.append(page)
        after = page["next_after_id"]
        if page["done"]:
            return pages


def test_the_snapshot_pages_by_key_and_covers_every_row(hub):
    _locations(hub, 5)
    pages = _walk_snapshot(hub, "locations", 2)

    assert [len(p["records"]) for p in pages] == [2, 2, 2]
    assert [p["done"] for p in pages] == [False, False, True]
    # Records carry their natural key, not the row id; the cursor is what
    # proves the walk moved forward.
    names = [r["name"] for page in pages for r in page["records"]]
    assert sorted(names) == sorted(["Default Test Loc"] +
                                   [f"Loc{index}" for index in range(5)])
    cursors = [p["next_after_id"] for p in pages]
    assert cursors == sorted(cursors)
    assert len(set(cursors)) == len(cursors)


def test_a_snapshot_page_carries_the_rev_and_the_table_list(hub):
    page = _walk_snapshot(hub, "locations", 100)[0]
    assert page["protocol"] == PROTOCOL_VERSION
    assert page["table"] == "locations"
    assert page["server_rev"] >= 1
    assert "locations" in page["tables"]
    assert page["records"]
    assert page["records"][0]["record_uid"]


def test_a_snapshot_of_an_untouched_account_is_refused(hub, db_session):
    """A hub holding nothing but template data must not be downloaded over a
    real local: there is nothing there to download."""
    fresh = DbUser(username="fresh")
    db_session.add(fresh)
    db_session.commit()
    token = new_token()
    db_session.add(SyncToken(user_id=fresh.id, token_hash=hash_token(token),
                             label="fresh", created_at=utc_now()))
    db_session.commit()

    resp = _api(hub, SNAPSHOT, token=token, body={"table": "locations"})
    assert resp.status_code == 409
    assert _codes(resp) == ["server_untouched"]


def test_a_snapshot_page_lists_the_files_its_records_reference(hub):
    _session_with_photo(hub, "s1")
    pages = _walk_snapshot(hub, "journal_sessions", 50)
    manifest = pages[0]["files"]
    assert manifest
    # The manifest is keyed by digest; the entry describes the file for the
    # client's token validation.
    digest, entry = next(iter(manifest.items()))
    assert digest == _import_digest(PNG)
    assert entry["role"] == "session_photo"
    assert entry["size"] == len(PNG)
    assert entry["ext"] == "png"
    # A display name only: the client never builds a path from it.
    assert os.sep not in entry["name"] and entry["name"]


# =============================================================================
# 5. Fetching one file
# =============================================================================

def test_a_file_is_fetched_by_record_and_slot(hub):
    session = _session_with_photo(hub, "s1")
    resp = _api(hub, FETCH, token=hub.token,
                body={"table": "journal_sessions", "key": "s1",
                      "slot": "session_image_file"})
    assert resp.status_code == 200
    assert resp.data == PNG
    assert resp.headers["X-Nova-Sha256"] == _import_digest(PNG)
    assert resp.headers["X-Nova-Sync-Protocol"] == str(PROTOCOL_VERSION)
    assert session.session_image_file


def test_a_file_fetch_with_the_wrong_digest_is_refused(hub):
    _session_with_photo(hub, "s1")
    resp = _api(hub, FETCH, token=hub.token,
                body={"table": "journal_sessions", "key": "s1",
                      "slot": "session_image_file",
                      "sha256": _import_digest(b"something else")})
    assert resp.status_code == 404
    assert _codes(resp) == ["bad_file_reference"]


def test_each_note_image_in_one_record_is_fetched_by_its_digest(hub):
    """A note with several images serves each by its own digest, not the first."""
    hub.db.add(Project(id="np1", user_id=hub.uid, name="Veil",
                       status="In Progress"))
    hub.db.commit()
    os.makedirs(os.path.join(UPLOAD_FOLDER, "default"), exist_ok=True)

    images = {}
    for i, data in enumerate((PNG, PNG + b"-2", PNG + b"-3")):
        name = f"note_img_{i}.png"
        with open(os.path.join(UPLOAD_FOLDER, "default", name), "wb") as handle:
            handle.write(data)
        images[_import_digest(data)] = data

    hub.db.expire_all()
    row = hub.db.query(Project).filter_by(id="np1").one()
    row.description_notes = "".join(
        f'<img src="/uploads/default/note_img_{i}.png">' for i in range(3))
    hub.db.commit()

    for digest, data in images.items():
        resp = _api(hub, FETCH, token=hub.token,
                    body={"table": "projects", "key": "np1",
                          "slot": "note_img", "sha256": digest})
        assert resp.status_code == 200, resp.get_json()
        assert resp.data == data
        assert resp.headers["X-Nova-Sha256"] == digest

    # A digest the record does not reference is still refused.
    resp = _api(hub, FETCH, token=hub.token,
                body={"table": "projects", "key": "np1",
                      "slot": "note_img", "sha256": _import_digest(b"nope")})
    assert resp.status_code == 404
    assert _codes(resp) == ["bad_file_reference"]


def test_another_accounts_file_cannot_be_named(hub, db_session):
    other = DbUser(username="Other")
    db_session.add(other)
    db_session.commit()
    row = JournalSession(user_id=other.id, external_id="theirs",
                         date_utc=date(2026, 1, 10),
                         session_image_file="theirs.png")
    db_session.add(row)
    db_session.commit()

    resp = _api(hub, FETCH, token=hub.token,
                body={"table": "journal_sessions", "key": "theirs",
                      "slot": "session_image_file"})
    assert resp.status_code == 404
    assert _codes(resp) == ["bad_file_reference"]


def test_a_file_fetch_names_a_record_not_a_path(hub):
    _session_with_photo(hub, "s1")
    for body in ({"table": "journal_sessions", "key": "s1", "slot": "nope"},
                 {"table": "journal_sessions", "key": "s1", "slot": ""},
                 {"table": "secrets", "key": "s1", "slot": "session_image_file"},
                 {"table": "journal_sessions", "key": "../../etc/passwd",
                  "slot": "session_image_file"}):
        resp = _api(hub, FETCH, token=hub.token, body=body)
        assert resp.status_code == 404, body
        assert _codes(resp) == ["bad_file_reference"], body


# =============================================================================
# 6. Repair: the hub is missing a file its own records name
# =============================================================================

def test_repair_places_a_missing_file_into_an_existing_record(hub):
    row = JournalSession(user_id=hub.uid, external_id="s1",
                         date_utc=date(2026, 1, 10))
    hub.db.add(row)
    hub.db.commit()
    hub.db.expire_all()
    name = f"{row.id}.png"
    row.session_image_file = name
    hub.db.commit()
    # ...but the bytes are not on the hub's disk. The uploads directory is the
    # session-wide one and row ids repeat across tests, so an earlier test's
    # file of the same name is cleared: a repair only fills a missing file.
    _forget_upload(name)

    headers = {"X-Nova-Table": "journal_sessions", "X-Nova-Key": "s1",
               "X-Nova-Slot": "session_image_file",
               "X-Nova-Sha256": _import_digest(PNG),
               "X-Nova-Ext": "png", "X-Nova-Total": str(len(PNG)),
               "X-Nova-Offset": "0"}
    resp = _api(hub, REPAIR, token=hub.token, raw=PNG, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["complete"] is True
    assert resp.get_json()["placed"], resp.get_json()

    with open(os.path.join(UPLOAD_FOLDER, "default", name), "rb") as handle:
        assert handle.read() == PNG


def test_a_repair_is_scoped_to_a_record_the_account_owns(hub, db_session):
    other = DbUser(username="Other")
    db_session.add(other)
    db_session.commit()
    db_session.add(JournalSession(user_id=other.id, external_id="theirs",
                                  date_utc=date(2026, 1, 10),
                                  session_image_file="theirs.png"))
    db_session.commit()

    headers = {"X-Nova-Table": "journal_sessions", "X-Nova-Key": "theirs",
               "X-Nova-Slot": "session_image_file",
               "X-Nova-Sha256": _import_digest(PNG),
               "X-Nova-Ext": "png", "X-Nova-Total": str(len(PNG)),
               "X-Nova-Offset": "0"}
    resp = _api(hub, REPAIR, token=hub.token, raw=PNG, headers=headers)
    assert resp.status_code == 404
    assert _codes(resp) == ["bad_file_reference"]


def test_a_repair_cannot_choose_a_role_or_an_extension(hub):
    _session_with_photo(hub, "s1")
    headers = {"X-Nova-Table": "journal_sessions", "X-Nova-Key": "s1",
               "X-Nova-Slot": "session_image_file",
               "X-Nova-Sha256": _import_digest(PNG),
               "X-Nova-Ext": "exe", "X-Nova-Total": str(len(PNG)),
               "X-Nova-Offset": "0"}
    resp = _api(hub, REPAIR, token=hub.token, raw=PNG, headers=headers)
    assert resp.status_code == 404
    assert _codes(resp) == ["bad_file_reference"]


def _forget_upload(name):
    path = os.path.join(UPLOAD_FOLDER, "default", name)
    if os.path.exists(path):
        os.remove(path)


def _repair_headers(key="s1", slot="session_image_file", data=PNG, ext="png",
                    offset="0", digest=None):
    return {"X-Nova-Table": "journal_sessions", "X-Nova-Key": key,
            "X-Nova-Slot": slot,
            "X-Nova-Sha256": _import_digest(data) if digest is None else digest,
            "X-Nova-Ext": ext, "X-Nova-Total": str(len(data)),
            "X-Nova-Offset": offset}


def test_a_repair_never_overwrites_a_file_the_hub_holds(hub):
    """The hub has the photo. A repair carrying other bytes is refused with
    file_exists and the hub's bytes are untouched."""
    row = _session_with_photo(hub, "s1", data=PNG)
    other = PNG + b"-other-bytes"

    resp = _api(hub, REPAIR, token=hub.token, raw=other,
                headers=_repair_headers(data=other))

    assert resp.status_code == 409
    assert _codes(resp) == ["file_exists"]
    with open(os.path.join(UPLOAD_FOLDER, "default", row.session_image_file), "rb") as handle:
        assert handle.read() == PNG


def test_a_repair_with_the_same_bytes_is_refused_too(hub):
    _session_with_photo(hub, "s1", data=PNG)
    resp = _api(hub, REPAIR, token=hub.token, raw=PNG, headers=_repair_headers())
    assert resp.status_code == 409
    assert _codes(resp) == ["file_exists"]


def test_a_repair_resume_is_refused_for_a_file_the_hub_holds(hub):
    """Offset -1 (how much do you hold?) answers file_exists as well: a client
    learns the file is there before it sends a byte."""
    _session_with_photo(hub, "s1", data=PNG)
    resp = _api(hub, REPAIR, token=hub.token, raw=b"",
                headers=_repair_headers(offset="-1"))
    assert resp.status_code == 409
    assert _codes(resp) == ["file_exists"]


@pytest.mark.parametrize("digest", [
    "", "abc", "g" * 64, "A" * 63, "../../../../etc/passwd",
    "0" * 60 + "/../x",
])
@pytest.mark.parametrize("offset", ["0", "-1"])
def test_a_repair_with_a_bad_digest_is_refused_before_anything_reads_it(
        hub, monkeypatch, digest, offset):
    """The digest becomes part of a path (the accumulator's name), so it is
    checked to be 64 hex digits first — on an upload and on the resume path."""
    row = JournalSession(user_id=hub.uid, external_id="s1",
                         date_utc=date(2026, 1, 10), session_image_file="gone.png")
    hub.db.add(row)
    hub.db.commit()
    touched = []
    import nova.blueprints.sync_api as api
    monkeypatch.setattr(api, "upload_received",
                        lambda *a, **k: touched.append(a) or 0)
    monkeypatch.setattr(api, "write_chunk",
                        lambda *a, **k: touched.append(a) or 0)

    resp = _api(hub, REPAIR, token=hub.token, raw=PNG,
                headers=_repair_headers(digest=digest, offset=offset))

    assert resp.status_code == 400
    assert _codes(resp) == ["bad_file_reference"]
    assert resp.get_json()["refusals"][0]["detail"] == {"reason": "sha256"}
    assert touched == []


def test_a_repair_does_not_put_back_a_photo_the_hub_removed(hub):
    row = JournalSession(user_id=hub.uid, external_id="s1",
                         date_utc=date(2026, 1, 10), session_image_file="")
    hub.db.add(row)
    hub.db.commit()

    resp = _api(hub, REPAIR, token=hub.token, raw=PNG, headers=_repair_headers())

    assert _codes(resp) == ["bad_file_reference"]
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "slot_removed"
    hub.db.expire_all()
    assert hub.db.query(JournalSession).filter_by(external_id="s1").one().session_image_file == ""


@pytest.mark.parametrize("offset", ["0", "-1"])
def test_a_repair_is_refused_for_a_slot_that_names_no_file(hub, offset):
    """The record has no photo. A repair restores a file the record names and
    the disk lost; it never attaches one to a record that has none - on the
    upload and on the resume path alike."""
    row = JournalSession(user_id=hub.uid, external_id="s1", date_utc=date(2026, 1, 10))
    hub.db.add(row)
    hub.db.commit()
    hub.db.expire_all()
    _forget_upload(f"{row.id}.png")
    from nova.sync_engine import counter_rev
    rev = counter_rev(hub.db, hub.uid)

    resp = _api(hub, REPAIR, token=hub.token, raw=PNG,
                headers=_repair_headers(offset=offset))

    assert _codes(resp) == ["bad_file_reference"]
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "slot_empty"
    hub.db.expire_all()
    assert hub.db.query(JournalSession).filter_by(external_id="s1").one().session_image_file is None
    assert not os.path.exists(os.path.join(UPLOAD_FOLDER, "default", f"{row.id}.png"))
    assert counter_rev(hub.db, hub.uid) == rev


def test_a_repair_never_writes_over_a_file_at_its_target_path(hub):
    """The column names 1.jpg, which is gone. The request says "png", so the
    placement would write 1.png - and a file is already there. Refused: a
    repair fills a gap, it never writes over anything on disk."""
    row = JournalSession(user_id=hub.uid, external_id="s1", date_utc=date(2026, 1, 10))
    hub.db.add(row)
    hub.db.commit()
    hub.db.expire_all()
    row.session_image_file = f"{row.id}.jpg"
    hub.db.commit()
    _forget_upload(f"{row.id}.jpg")
    keep = os.path.join(UPLOAD_FOLDER, "default", f"{row.id}.png")
    os.makedirs(os.path.dirname(keep), exist_ok=True)
    with open(keep, "wb") as handle:
        handle.write(b"bytes already on disk")
    other = PNG + b"-repair"

    resp = _api(hub, REPAIR, token=hub.token, raw=other,
                headers=_repair_headers(data=other, ext="png"))

    assert resp.status_code == 409
    assert _codes(resp) == ["file_exists"]
    with open(keep, "rb") as handle:
        assert handle.read() == b"bytes already on disk"
    hub.db.expire_all()
    assert (hub.db.query(JournalSession).filter_by(external_id="s1").one()
            .session_image_file == f"{row.id}.jpg")
    _forget_upload(f"{row.id}.png")


def _note_session(hub, digest, ext="png", named=True):
    from nova.sync_engine import note_image_name
    name = note_image_name(digest, ext)
    notes = f'<p>x</p><img src="/uploads/default/{name}">' if named else "<p>no image</p>"
    row = JournalSession(user_id=hub.uid, external_id="s1", date_utc=date(2026, 1, 10),
                         notes=notes)
    hub.db.add(row)
    hub.db.commit()
    _forget_upload(name)
    return name


def _note_headers(data):
    return _repair_headers(slot="note_img", data=data)


def test_a_note_image_repair_is_refused_when_the_text_does_not_name_it(hub):
    data = PNG + b"-note-unnamed"
    name = _note_session(hub, _import_digest(data), named=False)

    resp = _api(hub, REPAIR, token=hub.token, raw=data, headers=_note_headers(data))

    assert _codes(resp) == ["bad_file_reference"]
    assert resp.get_json()["refusals"][0]["detail"]["reason"] == "not_referenced"
    assert not os.path.exists(os.path.join(UPLOAD_FOLDER, "default", name))


def test_a_note_image_the_text_names_is_restored_and_never_overwritten(hub):
    data = PNG + b"-note-named"
    name = _note_session(hub, _import_digest(data))
    path = os.path.join(UPLOAD_FOLDER, "default", name)

    resp = _api(hub, REPAIR, token=hub.token, raw=data, headers=_note_headers(data))
    assert resp.status_code == 200, resp.get_json()
    with open(path, "rb") as handle:
        assert handle.read() == data

    again = _api(hub, REPAIR, token=hub.token, raw=data, headers=_note_headers(data))
    assert again.status_code == 409 and _codes(again) == ["file_exists"]
    _forget_upload(name)


def test_a_repair_resume_reports_what_is_held_for_a_missing_file(hub):
    row = JournalSession(user_id=hub.uid, external_id="s1",
                         date_utc=date(2026, 1, 10))
    hub.db.add(row)
    hub.db.commit()
    hub.db.expire_all()
    row.session_image_file = f"{row.id}.png"
    hub.db.commit()
    _forget_upload(f"{row.id}.png")

    resp = _api(hub, REPAIR, token=hub.token, raw=b"",
                headers=_repair_headers(offset="-1"))

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["complete"] is False


def test_a_file_fetch_by_slot_names_the_digest_type_and_size(hub):
    """The healing pass asks by record and slot with no digest of its own; the
    answer's headers are all it has to verify and place the file."""
    _session_with_photo(hub, "s1", data=PNG)
    resp = _api(hub, FETCH, token=hub.token,
                body={"table": "journal_sessions", "key": "s1",
                      "slot": "session_image_file"})
    assert resp.status_code == 200
    assert resp.data == PNG
    assert resp.headers["X-Nova-Sha256"] == _import_digest(PNG)
    assert resp.headers["X-Nova-Ext"] == "png"
    assert resp.headers["X-Nova-Size"] == str(len(PNG))


# =============================================================================
# 7. Purge leaves no sync state behind
# =============================================================================

def test_purge_removes_the_sync_directories_and_the_push_session(db_session,
                                                                 monkeypatch):
    """A deleted login must leave no sync state: no accumulator, no snapshot,
    no push session. Purge refuses the protected system users, so this uses an
    ordinary account."""
    import nova.auth
    monkeypatch.setattr(nova.auth, "db", None)      # no users.db to consult

    doomed = DbUser(username="syncvanish")
    db_session.add(doomed)
    db_session.commit()
    uid = doomed.id

    upload_dir = os.path.join(INSTANCE_PATH, "sync_uploads", str(uid))
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, "part.part"), "wb") as handle:
        handle.write(b"half a file")

    assert snapshot_account_yaml(doomed) is not None
    backup_root = os.path.join(BACKUP_DIR, "sync", str(uid))
    assert os.path.isdir(backup_root)

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(push_session_path(uid), "w") as handle:
        json.dump({"session_id": "x"}, handle)

    someone_else = os.path.join(INSTANCE_PATH, "sync_uploads", "999")
    os.makedirs(someone_else, exist_ok=True)

    summary = purge_user_app_data("syncvanish", dry_run=False)
    assert summary["refused"] is None, summary

    assert not os.path.exists(upload_dir)
    assert not os.path.exists(backup_root)
    assert not os.path.exists(push_session_path(uid))
    # Purge is scoped: another account's accumulator survives.
    assert os.path.isdir(someone_else)


# =============================================================================
# 8. The clock sign: the hub negates the offset the client measured
# =============================================================================

def _stamp(db, model, row_id, moment):
    """Set sync_changed_at behind the listener's back."""
    from sqlalchemy import text
    db.execute(text(f"UPDATE {model.__tablename__} "
                    f"SET sync_changed_at = :m WHERE id = :i"),
               {"m": moment.strftime("%Y-%m-%d %H:%M:%S.%f"), "i": row_id})
    db.commit()
    db.expire_all()


def _conflicting_location(hub, sender, client_offset, client_edit_delta):
    """Both sides edit one location; the client's clock is off by
    `client_offset` seconds (its clock minus the hub's) and its edit happened
    `client_edit_delta` seconds after the hub's in true time.

    Returns the latitude the hub holds after the push: 10.0 is the client's
    edit, 20.0 the hub's.
    """
    moment = utc_now()

    sender.db.add(Location(user_id=sender.uid, name="Home", lat=10.0, lon=16.0,
                           timezone="UTC"))
    sender.commit()
    # A location is keyed by its record_uid, so the hub's copy is the same
    # record only if it carries the same one.
    key = sender.payload()["records"]["locations"][0]["record_uid"]
    sender_row = sender.db.query(Location).filter_by(name="Home").one()
    _stamp(sender.db, Location, sender_row.id,
           moment + timedelta(seconds=client_edit_delta + client_offset))

    hub.db.add(Location(user_id=hub.uid, name="Home", lat=20.0, lon=16.0,
                        timezone="UTC", record_uid=key))
    hub.db.commit()
    hub_row = hub.db.query(Location).filter_by(name="Home").one()
    _stamp(hub.db, Location, hub_row.id, moment)

    body = _plan(hub, sender.payload(), offset=client_offset).get_json()
    assert _apply(hub, body["session_id"], body["fingerprint"]).status_code == 200
    hub.db.expire_all()
    return hub.db.query(Location).filter_by(name="Home").one().lat


def test_a_fast_client_clock_does_not_hide_a_newer_hub_edit(hub, sender):
    """The client's clock runs 200 s fast and its edit is 100 s older than the
    hub's. Its stamp reads 100 s *ahead* of the hub's, so only the negated
    offset (which moves it back to true time) lets the hub's edit win."""
    assert _conflicting_location(hub, sender, client_offset=200,
                                 client_edit_delta=-100) == 20.0


def test_a_slow_client_clock_does_not_lose_a_newer_client_edit(hub, sender):
    """The client's clock runs 200 s slow and its edit is 100 s newer than the
    hub's. Its stamp reads 100 s *behind* the hub's, so only the negated offset
    (which moves it forward to true time) lets the client's edit win."""
    assert _conflicting_location(hub, sender, client_offset=-200,
                                 client_edit_delta=100) == 10.0


def test_the_negation_is_what_makes_the_newer_edit_win(hub, sender, monkeypatch):
    """Mutation check: with the sign removed, the fast-clock case keeps the
    client's older edit. The two tests above are therefore not passing by
    accident."""
    import nova.blueprints.sync_api as api
    monkeypatch.setattr(api, "hub_offset_from_client", lambda offset: float(offset))
    assert _conflicting_location(hub, sender, client_offset=200,
                                 client_edit_delta=-100) == 10.0
