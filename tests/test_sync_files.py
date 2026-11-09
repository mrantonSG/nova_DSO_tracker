"""Instance sync — file transfer (step 4).

Two installations in one process, as in test_sync_engine.py, but each with its
own instance directory: a file really crosses a boundary instead of being handed
to the same path twice. The transport (step 5) is not written yet, so these
tests drive the four file entry points directly, in the order it will:

    export -> plan -> [fetch bytes with stage_file] -> apply -> commit files

What each block covers is named in its section header. The letters name the
corrections the design was accepted with (A stamping, B content, C move-aside,
D thumbnails, E caps, F own-dir-only, G validation).

The tests reach the files through each user's own instance directory by passing
`instance_path` to the file entry points. Production passes nothing: the
configured INSTANCE_PATH is used.
"""
import hashlib
import json
import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from nova.models import (
    AstroObject, Base, DbUser, JournalSession, Project, SyncState,
)
from nova.sync_engine import (
    FILE_TOKEN, FILE_TOKEN_MISSING, KEY_FILES, PAYLOAD_FORMAT,
    REFUSAL_BAD_FILE_REF, REFUSAL_DANGLING_FILE, REFUSAL_FILE_TOKEN_COLUMN,
    REFUSAL_FILE_TOO_LARGE, REFUSAL_NO_DISK_SPACE,
    ROLE_NOTE_IMAGE, ROLE_PROJECT_IMAGE, ROLE_SESSION_LOG, ROLE_SESSION_PHOTO,
    SKIP_FOREIGN, SKIP_MISSING, SKIP_TOO_LARGE,
    SyncRefused, apply_download, apply_merge, canonicalize_text,
    collect_files, commit_staged_files, disk_space_refusal, export_changes,
    export_snapshot, export_files, files_needed, file_source_path,
    local_name_for, localize_text, missing_local_files, move_replaced_files,
    note_image_name, plan_download, plan_merge, stage_file, staged_index,
)
from nova.sync_time import utc_now
from nova.sync_tracking import mark_file_changed, register_sync_tracking

HUB_URL = "https://hub.invalid"

PNG = b"\x89PNG\r\n\x1a\n" + b"hub-photo-bytes" * 8
PNG_ALT = b"\x89PNG\r\n\x1a\n" + b"different-bytes" * 8
JPG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 16


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Two installations, each with its own instance directory -------------------

class Node:
    """One installation: its own database, session, user and instance dir."""

    def __init__(self, name, instance_path):
        self.name = name
        self.instance_path = str(instance_path)
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

    # --- disk ---

    def put(self, filename: str, data: bytes) -> str:
        """Write a file into this node's uploads directory."""
        path = os.path.join(self.uploads, filename)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def upload_path(self, filename: str) -> str:
        return os.path.join(self.uploads, filename)

    def log_path(self, log_type: str, filename: str) -> str:
        directory = os.path.join(self.instance_path, "logs", log_type)
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, filename)

    def put_log(self, log_type: str, filename: str, data: bytes) -> str:
        path = self.log_path(log_type, filename)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def has(self, filename: str) -> bool:
        return os.path.isfile(self.upload_path(filename))

    def read(self, filename: str) -> bytes:
        with open(self.upload_path(filename), "rb") as handle:
            return handle.read()

    def replaced_dir(self) -> str:
        return os.path.join(self.instance_path, "replaced", str(self.uid))

    def replaced_names(self) -> list:
        """The basenames moved aside, under whatever timestamp directory."""
        found = []
        for _base, _dirs, files in os.walk(self.replaced_dir()):
            found.extend(files)
        return sorted(found)

    def replaced_paths(self) -> list:
        """The full paths moved aside, timestamp directory included."""
        found = []
        for base, _dirs, files in os.walk(self.replaced_dir()):
            found.extend(os.path.join(base, name) for name in files)
        return sorted(found)

    # --- db ---

    @property
    def uid(self):
        return self.user.id

    def commit(self):
        self.db.commit()
        self.db.expire_all()

    def state(self):
        self.db.expire_all()
        return self.db.query(SyncState).filter_by(user_id=self.uid).one_or_none()

    def rows(self, model):
        self.db.expire_all()
        return self.db.query(model).filter_by(user_id=self.uid).all()

    def one(self, model, **match):
        self.db.expire_all()
        return self.db.query(model).filter_by(user_id=self.uid, **match).one_or_none()

    def close(self):
        try:
            self.db.rollback()
        except Exception:
            pass
        self.Session.remove()
        self.engine.dispose()


@pytest.fixture(autouse=True)
def _tracking():
    """The listener is process-global; install it for these tests too."""
    register_sync_tracking()


@pytest.fixture
def hub(tmp_path):
    node = Node("hub", tmp_path / "hub")
    yield node
    node.close()


@pytest.fixture
def local(tmp_path):
    node = Node("local", tmp_path / "local")
    yield node
    node.close()


# --- Builders ------------------------------------------------------------------

def add_session(node, external_id, **fields):
    node.db.add(JournalSession(
        user_id=node.uid, external_id=external_id,
        date_utc=datetime(2026, 1, 2).date(), **fields))
    node.commit()
    return node.one(JournalSession, external_id=external_id)


def add_project(node, project_id, name="Veil"):
    node.db.add(Project(id=project_id, user_id=node.uid, name=name,
                        status="In Progress"))
    node.commit()
    return node.one(Project, id=project_id)


def add_object(node, name):
    node.db.add(AstroObject(user_id=node.uid, object_name=name, ra_hours=1.0,
                            dec_deg=2.0))
    node.commit()
    return node.one(AstroObject, object_name=name)


# --- Transport stand-ins -------------------------------------------------------

def transfer(sender, receiver, refs) -> list:
    """Fetch the bytes of `refs` from `sender` and stage them on `receiver`.

    This is the step-5 transfer, written out: for each ref the plan needs, find
    the file on the sender by the sender's own naming scheme and stage it on the
    receiver. The plan is what says which files are needed, so a note image
    (named only by a token in the record's text) is included too.
    """
    staged = []
    for ref in refs:
        if ref.missing or not ref.sha256:
            continue
        source = file_source_path(ref, sender.user,
                                  instance_path=sender.instance_path)
        if not source:
            continue
        with open(source, "rb") as handle:
            staged.append(stage_file(receiver.user, ref, handle,
                                     declared_size=ref.size,
                                     instance_path=receiver.instance_path))
    return staged


def _ref_from(*, table, digest, slot, descriptor, role):
    from nova.sync_engine import FileRef
    return FileRef(role=role, table=table, key="", slot=slot, sha256=digest,
                   size=int(descriptor.get("size") or 0),
                   ext=str(descriptor.get("ext") or ""),
                   name=str(descriptor.get("name") or ""))


def push(local, hub):
    """Sync now, local -> hub, files included."""
    payload = export_changes(local.db, local.user,
                             local.state().local_pushed_rev or 0, side="local",
                             instance_path=local.instance_path)
    plan = plan_merge(hub.db, hub.user, payload, side="server",
                      instance_path=hub.instance_path)
    if plan.refused:
        raise SyncRefused(plan.refusals[0])
    staged = transfer(local, hub, plan.files)
    result = apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    commit_staged_files(hub.db, result, staged, user=hub.user,
                        instance_path=hub.instance_path)
    hub.commit()
    return result


def pull(local, hub):
    """Sync now, hub -> local, files included."""
    payload = export_changes(hub.db, hub.user, local.state().server_cursor or 0,
                             side="server", instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    if plan.refused:
        raise SyncRefused(plan.refusals[0])
    staged = transfer(hub, local, plan.files)
    result = apply_merge(local.db, plan, user=local.user)
    local.commit()
    commit_staged_files(local.db, result, staged, user=local.user,
                        instance_path=local.instance_path)
    local.commit()
    return result


def download(local, hub):
    """Download from server: wipe, write, move the displaced files aside."""
    snapshot = export_snapshot(hub.db, hub.user, side="server",
                                       instance_path=hub.instance_path)
    plan = plan_download(local.db, local.user, snapshot,
                         instance_path=local.instance_path)
    if plan.refused:
        raise SyncRefused(plan.refusals[0])
    staged = transfer(hub, local, plan.files)
    result = apply_download(local.db, plan, user=local.user)
    local.commit()
    move_replaced_files(local.db, plan, local.user,
                        instance_path=local.instance_path)
    commit_staged_files(local.db, result, staged, user=local.user,
                        instance_path=local.instance_path)
    local.commit()
    return result


def seed_pairing(local, hub):
    from nova.sync_engine import start_pairing
    start_pairing(local.db, local.user, server_url=HUB_URL, account=hub.name)
    local.db.query(SyncState).filter_by(user_id=local.uid).update(
        {"last_sync_at": utc_now()})
    local.commit()


# =============================================================================
# 1. A session photo travels (the base case)
# =============================================================================

def test_a_session_photo_reaches_the_other_side(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    pull(local, hub)

    landed = local.one(JournalSession, external_id="s1")
    assert landed.session_image_file == f"{landed.id}.png"
    assert local.read(landed.session_image_file) == PNG


def test_a_photo_the_receiver_already_holds_is_not_refetched(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert plan.files == []      # equal content is a no-op, not a transfer


def test_a_project_image_uses_the_local_project_name(hub, local):
    project = add_project(hub, "p1")
    hub.put(f"project_{project.id}.png", PNG)
    project.final_image_file = f"project_{project.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    pull(local, hub)

    landed = local.one(Project, id="p1")
    assert landed.final_image_file == "project_p1.png"
    assert local.read(landed.final_image_file) == PNG


# =============================================================================
# 2. Correction B: a file is content
# =============================================================================

def test_a_photo_replaced_under_the_same_name_still_travels(hub, local):
    """The hub keeps the name, the bytes change: the local must still pull."""
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)
    local_before = local.one(JournalSession, external_id="s1")
    assert local.read(local_before.session_image_file) == PNG

    # Replace the photo on the hub. Same filename, different bytes.
    hub.put(f"{session.id}.png", PNG_ALT)
    mark_file_changed(session, "session_image_file")
    hub.commit()

    pull(local, hub)

    landed = local.one(JournalSession, external_id="s1")
    assert local.read(landed.session_image_file) == PNG_ALT


def test_equal_text_but_a_different_file_is_a_change(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    # The local's own copy goes missing: the text still matches, the content
    # does not, so the record is a change and the file is fetched again.
    landed = local.one(JournalSession, external_id="s1")
    os.remove(local.upload_path(landed.session_image_file))
    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert [r.sha256 for r in plan.files] == [sha(PNG)]


def test_a_note_round_trip_is_a_no_op_in_both_directions(hub, local):
    """A note's image link survives a round trip without re-transferring."""
    obj = add_object(hub, "M 31")
    hub.put("note_img_aaaaaaaaaaaa.png", PNG)
    obj.shared_notes = ('<p>see <img src="/uploads/hub/note_img_aaaaaaaaaaaa.png">'
                        '</p>')
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    local_obj = local.one(AstroObject, object_name="M 31")
    assert FILE_TOKEN not in local_obj.shared_notes
    assert "/uploads/local/" in local_obj.shared_notes
    local_name = local_obj.shared_notes.split("/uploads/local/")[1].split('"')[0]
    assert local_name == note_image_name(sha(PNG), "png")
    assert local.read(local_name) == PNG

    # Push it back: the hub's own note is unchanged, so nothing moves.
    result = push(local, hub)
    assert result.updated.get("astro_objects") is None


def test_a_note_image_carries_its_bytes_and_the_link_follows(hub, local):
    obj = add_object(hub, "M 31")
    hub.put("note_img_bbbbbbbbbbbb.jpg", JPG)
    obj.shared_notes = (
        '<p>x</p><img src="/uploads/hub/note_img_bbbbbbbbbbbb.jpg">'
        '<a href="/uploads/hub/note_img_bbbbbbbbbbbb.jpg">y</a>'
        '&quot;/uploads/hub/note_img_bbbbbbbbbbbb.jpg&quot;')
    hub.commit()
    seed_pairing(local, hub)

    pull(local, hub)

    landed = local.one(AstroObject, object_name="M 31")
    name = note_image_name(sha(JPG), "jpg")
    assert landed.shared_notes.count(f"/uploads/local/{name}") == 3
    assert local.read(name) == JPG


# =============================================================================
# 3. Correction A: a replaced file stamps the record
# =============================================================================

def test_flag_modified_stamps_a_loaded_file_column(hub):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()

    rev_before = session.sync_rev
    assert mark_file_changed(session, "session_image_file") is True
    hub.commit()

    assert session.sync_rev != rev_before


def test_mark_file_changed_refuses_a_slot_the_model_does_not_have(hub):
    session = add_session(hub, "s1")
    assert mark_file_changed(session, "final_image_file") is False
    assert mark_file_changed(None, "session_image_file") is False


def test_a_replaced_photo_stamps_the_row_even_though_no_column_moved(hub, local):
    """The route's own write path: save the bytes, stamp, and the rev moves."""
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    session = hub.one(JournalSession, external_id="s1")
    rev_before = session.sync_rev
    hub.put(f"{session.id}.png", PNG_ALT)     # the route's file.save()
    mark_file_changed(session, "session_image_file")
    hub.commit()

    assert session.sync_rev != rev_before


def test_a_log_replaced_under_the_same_name_stamps_the_row(hub):
    session = add_session(hub, "s1")
    path = hub.put_log("asiair", f"{session.id}_Autorun_Log.txt", b"first")
    session.asiair_log_content = os.path.join("instance", "logs", "asiair",
                                              os.path.basename(path))
    hub.commit()

    rev_before = session.sync_rev
    with open(path, "wb") as handle:
        handle.write(b"second")
    mark_file_changed(session, "asiair_log_content")
    hub.commit()

    assert session.sync_rev != rev_before


# =============================================================================
# 4. Correction D: a replaced photo leaves no stale thumbnail
# =============================================================================

def test_a_replacement_lands_without_a_stale_thumbnail(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    landed = local.one(JournalSession, external_id="s1")
    local.put(f"thumb_{landed.session_image_file}", b"stale-thumb")

    hub.put(f"{session.id}.png", PNG_ALT)
    mark_file_changed(session, "session_image_file")
    hub.commit()
    pull(local, hub)

    assert not local.has(f"thumb_{landed.session_image_file}")


def test_remove_thumbnail_is_quiet_when_there_is_none(tmp_path):
    from nova.helpers import remove_thumbnail, thumb_path_for
    target = tmp_path / "1.png"
    target.write_bytes(PNG)
    assert remove_thumbnail(str(target)) is False
    assert thumb_path_for(str(target)).endswith("thumb_1.png")
    (tmp_path / "thumb_1.png").write_bytes(b"x")
    assert remove_thumbnail(str(target)) is True
    assert not (tmp_path / "thumb_1.png").exists()


# =============================================================================
# 5. Correction E: caps are reported, never queued
# =============================================================================

def test_an_over_cap_photo_is_skipped_and_reported(hub, local):
    session = add_session(hub, "s1")
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024 + 1)
    hub.put(f"{session.id}.png", big)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    assert payload["files"] == {}
    assert payload["skipped_files"][0]["reason"] == SKIP_TOO_LARGE

    # The record still syncs; the file is not queued as pending.
    result = pull(local, hub)
    assert result.pending_files == []
    assert local.one(JournalSession, external_id="s1") is not None


def test_a_photo_missing_on_the_sender_is_skipped(hub, local):
    session = add_session(hub, "s1")
    session.session_image_file = "1.png"      # named, but not on disk
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    assert payload["skipped_files"][0]["reason"] == SKIP_MISSING
    result = pull(local, hub)
    assert result.pending_files == []


def test_an_over_cap_stream_is_truncated_not_stored(hub, local):
    """A hostile transfer cannot fill the disk: the cap is enforced on the read."""
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    descriptor = payload["files"][sha(PNG)]
    ref = _ref_from(table="journal_sessions", digest=sha(PNG),
                    slot="session_image_file", descriptor=descriptor,
                    role=ROLE_SESSION_PHOTO)
    # Declare a small size but send a stream that never ends.
    staged = stage_file(local.user, ref, _EndlessStream(),
                        declared_size=10, instance_path=local.instance_path)
    assert staged.skipped and staged.reason == SKIP_TOO_LARGE


class _EndlessStream:
    def read(self, _n=-1):
        return b"x" * (1024 * 1024)


def test_a_file_over_its_cap_is_refused_in_a_manifest(hub, local):
    session = add_session(hub, "s1")
    hub.commit()
    payload = {
        "format": PAYLOAD_FORMAT, "account": "hub", "side": "server",
        "rev": 1, "snapshot": False, "records": {}, "tombstones": [],
        "files": {sha(PNG): {"role": ROLE_SESSION_PHOTO,
                             "size": 5 * 1024 * 1024 + 1, "ext": "png",
                             "name": "1.png"}},
    }
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert any(r.code == REFUSAL_FILE_TOO_LARGE for r in plan.refusals)


# =============================================================================
# 6. Correction F: only this user's own files ever travel
# =============================================================================

def test_a_link_to_another_users_directory_is_a_missing_token(hub, local):
    obj = add_object(hub, "M 31")
    other = os.path.join(hub.instance_path, "uploads", "someone-else")
    os.makedirs(other, exist_ok=True)
    with open(os.path.join(other, "note_img_cccccccccccc.png"), "wb") as handle:
        handle.write(PNG)
    obj.shared_notes = '<img src="/uploads/someone-else/note_img_cccccccccccc.png">'
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    text = payload["records"]["astro_objects"][0]["shared_notes"]
    assert FILE_TOKEN_MISSING in text
    assert sha(PNG) not in json.dumps(payload.get("files") or {})


def test_a_log_owned_by_another_session_is_skipped(hub, local):
    session = add_session(hub, "s1")
    hub.put_log("asiair", "9999_Someone_Elses.txt", b"not mine")
    session.asiair_log_content = os.path.join("instance", "logs", "asiair",
                                              "9999_Someone_Elses.txt")
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    assert payload["files"] == {}
    # Not this account's file: the reason says so, rather than being folded
    # into a generic "missing" (correction F).
    assert payload["skipped_files"][0]["reason"] == SKIP_FOREIGN


def test_canonicalize_only_reads_this_users_directory(hub):
    other = os.path.join(hub.instance_path, "uploads", "someone-else")
    os.makedirs(other, exist_ok=True)
    with open(os.path.join(other, "note_img_dddddddddddd.png"), "wb") as handle:
        handle.write(PNG)
    text, refs = canonicalize_text(
        hub.db, hub.user, AstroObject, "shared_notes",
        '<img src="/uploads/someone-else/note_img_dddddddddddd.png">', "M 31")
    assert refs == []
    assert FILE_TOKEN_MISSING + "note_img_dddddddddddd.png" in text


# =============================================================================
# 7. Correction G: validation
# =============================================================================

def test_a_token_with_no_manifest_entry_is_refused(hub, local):
    add_object(local, "M 31")
    payload = {
        "format": PAYLOAD_FORMAT, "account": "hub", "side": "server",
        "rev": 1, "snapshot": False, "tombstones": [], "files": {},
        "records": {"astro_objects": [{
            "record_uid": "uid-1", "object_name": "M 31",
            "sync_changed_at": "2026-01-01T00:00:00.000000",
            "shared_notes": f"<p>{FILE_TOKEN}{sha(PNG)}.png</p>",
        }]},
    }
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert any(r.code == REFUSAL_DANGLING_FILE for r in plan.refusals)


def test_a_token_in_a_non_text_column_is_refused(hub, local):
    add_object(local, "M 31")
    payload = {
        "format": PAYLOAD_FORMAT, "account": "hub", "side": "server",
        "rev": 1, "snapshot": False, "tombstones": [],
        "files": {sha(PNG): {"role": ROLE_NOTE_IMAGE, "size": len(PNG),
                             "ext": "png", "name": "x.png"}},
        "records": {"astro_objects": [{
            "record_uid": "uid-1", "object_name": f"{FILE_TOKEN}{sha(PNG)}.png",
            "sync_changed_at": "2026-01-01T00:00:00.000000",
        }]},
    }
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert any(r.code == REFUSAL_FILE_TOKEN_COLUMN for r in plan.refusals)


def test_a_manifest_entry_with_a_bad_digest_is_refused(hub, local):
    payload = {
        "format": PAYLOAD_FORMAT, "account": "hub", "side": "server",
        "rev": 1, "snapshot": False, "records": {}, "tombstones": [],
        "files": {"not-a-digest": {"role": ROLE_NOTE_IMAGE, "size": 1,
                                   "ext": "png", "name": "x.png"}},
    }
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert any(r.code == REFUSAL_BAD_FILE_REF for r in plan.refusals)


# =============================================================================
# 8. Correction C: a download moves files aside, never deletes
# =============================================================================

def test_a_download_moves_the_displaced_files_aside(hub, local):
    add_object(hub, "M 31")
    seed_pairing(local, hub)

    session = add_session(local, "old")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()
    old_name = f"{session.id}.png"

    download(local, hub)

    assert not local.has(old_name)
    assert local.replaced_names() == [old_name]
    [moved] = local.replaced_paths()
    with open(moved, "rb") as handle:
        assert handle.read() == PNG


def test_a_download_never_moves_a_file_the_new_records_still_name(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)
    landed = local.one(JournalSession, external_id="s1")
    name = landed.session_image_file

    # A second download: the same session and the same bytes come back under
    # the same local id, so the file stays exactly where it is.
    download(local, hub)

    assert local.has(name)
    assert local.replaced_names() == []


def test_a_download_moves_a_log_owned_by_a_wiped_session(hub, local):
    add_object(hub, "M 31")
    seed_pairing(local, hub)
    session = add_session(local, "old")
    name = f"{session.id}_Autorun_Log.txt"
    local.put_log("asiair", name, b"old log")
    session.asiair_log_content = os.path.join("instance", "logs", "asiair", name)
    local.commit()

    download(local, hub)

    assert not os.path.isfile(local.log_path("asiair", name))
    assert local.replaced_names() == [name]


def test_a_download_leaves_another_users_log_alone(hub, local):
    """Only this account's session ids own a log file (correction F)."""
    add_object(hub, "M 31")
    seed_pairing(local, hub)
    local.put_log("asiair", "9999_Someone_Elses.txt", b"not mine")

    download(local, hub)

    assert os.path.isfile(local.log_path("asiair", "9999_Someone_Elses.txt"))
    assert local.replaced_names() == []


def test_the_move_aside_is_idempotent(hub, local):
    add_object(hub, "M 31")
    seed_pairing(local, hub)
    session = add_session(local, "old")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()
    name = f"{session.id}.png"

    snapshot = export_snapshot(hub.db, hub.user, side="server",
                                       instance_path=hub.instance_path)
    plan = plan_download(local.db, local.user, snapshot,
                         instance_path=local.instance_path)
    apply_download(local.db, plan, user=local.user)
    local.commit()
    first = move_replaced_files(local.db, plan, local.user,
                                instance_path=local.instance_path)
    second = move_replaced_files(local.db, plan, local.user,
                                 instance_path=local.instance_path)

    assert len(first.placed) == 1
    assert second.placed == []
    assert local.replaced_names() == [name]


def test_a_crash_between_commit_and_move_leaves_the_file_where_it_was(hub, local):
    """The row still names the old file, so the file must not have moved."""
    add_object(hub, "M 31")
    seed_pairing(local, hub)
    session = add_session(local, "old")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()
    name = f"{session.id}.png"

    snapshot = export_snapshot(hub.db, hub.user, side="server",
                                       instance_path=hub.instance_path)
    plan = plan_download(local.db, local.user, snapshot,
                         instance_path=local.instance_path)
    apply_download(local.db, plan, user=local.user)
    # No commit, no move: the transaction is abandoned.
    local.db.rollback()

    assert local.has(name)


def test_a_download_reports_the_bytes_it_will_move(hub, local):
    add_object(hub, "M 31")
    seed_pairing(local, hub)
    session = add_session(local, "old")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()

    plan = plan_download(local.db, local.user,
                         export_snapshot(hub.db, hub.user, side="server",
                                       instance_path=hub.instance_path),
                         instance_path=local.instance_path)
    summary = plan.summary()
    assert summary["files"]["replaced"] == 1
    assert summary["files"]["replaced_bytes"] == len(PNG)


def test_a_download_with_no_disk_space_is_refused(hub, local, monkeypatch):
    add_object(hub, "M 31")
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    import nova.sync_engine as engine
    monkeypatch.setattr(engine, "_free_bytes", lambda _path: 1)
    plan = plan_download(local.db, local.user,
                         export_snapshot(hub.db, hub.user, side="server",
                                       instance_path=hub.instance_path),
                         instance_path=local.instance_path)
    assert any(r.code == REFUSAL_NO_DISK_SPACE for r in plan.refusals)


# =============================================================================
# 9. Failed and partial transfers
# =============================================================================

def test_a_file_that_did_not_arrive_is_pending(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    result = apply_merge(local.db, plan, user=local.user)
    local.commit()
    # The transfer was skipped entirely: nothing staged, so nothing placed.
    report = commit_staged_files(local.db, result, [], user=local.user,
                                 instance_path=local.instance_path)
    assert len(report.pending) == 1
    assert report.placed == []


def test_a_corrupted_staged_file_is_discarded_and_pending(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    staged = transfer(hub, local, plan.files)
    # Corrupt the staged copy after it was verified.
    with open(staged[0].path, "wb") as handle:
        handle.write(b"corrupted")
    result = apply_merge(local.db, plan, user=local.user)
    local.commit()
    report = commit_staged_files(local.db, result, staged, user=local.user,
                                 instance_path=local.instance_path)

    assert len(report.pending) == 1
    assert not os.path.exists(staged[0].path)


def test_a_missing_local_file_is_reported_for_repair(hub, local):
    session = add_session(local, "s1")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()

    assert missing_local_files(local.db, local.user,
                               instance_path=local.instance_path) == []
    os.remove(local.upload_path(f"{session.id}.png"))
    found = missing_local_files(local.db, local.user,
                                instance_path=local.instance_path)
    assert [(r.table, r.key, r.slot) for r in found] == [
        ("journal_sessions", "s1", "session_image_file")]


def test_a_staged_file_survives_a_retry(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    staged = transfer(hub, local, plan.files)

    # The retry asks for what it does not have; it already has this one.
    assert files_needed(plan, local.user, instance_path=local.instance_path) == []
    assert staged_index(local.user, verify=True,
                        instance_path=local.instance_path)[sha(PNG)] == staged[0].path


def test_the_same_file_on_two_records_is_fetched_once(hub, local):
    first = add_session(hub, "s1")
    second = add_session(hub, "s2")
    hub.put(f"{first.id}.png", PNG)
    hub.put(f"{second.id}.png", PNG)
    first.session_image_file = f"{first.id}.png"
    second.session_image_file = f"{second.id}.png"
    hub.commit()
    seed_pairing(local, hub)

    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert [r.sha256 for r in plan.files] == [sha(PNG)]

    pull(local, hub)
    for external_id in ("s1", "s2"):
        landed = local.one(JournalSession, external_id=external_id)
        assert local.read(landed.session_image_file) == PNG


# =============================================================================
# 10. Paths and names
# =============================================================================

def test_the_receiver_never_uses_the_senders_name(hub, local):
    session = add_session(hub, "s1")
    hub.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    landed = local.one(JournalSession, external_id="s1")
    assert landed.session_image_file == f"{landed.id}.png"


def test_a_path_traversal_in_a_name_is_refused(hub, local):
    session = add_session(hub, "s1")
    hub.commit()
    payload = {
        "format": PAYLOAD_FORMAT, "account": "hub", "side": "server",
        "rev": 1, "snapshot": False, "tombstones": [],
        "files": {sha(PNG): {"role": ROLE_SESSION_PHOTO, "size": len(PNG),
                             "ext": "png", "name": "../../etc/passwd"}},
        "records": {"journal_sessions": [{
            "external_id": "s1", "date_utc": "2026-01-02",
            "sync_changed_at": "2026-01-01T00:00:00.000000",
            KEY_FILES: {"session_image_file": sha(PNG)},
        }]},
    }
    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    if plan.refused:
        return                       # refused outright is fine too
    staged = transfer(hub, local, plan.files)
    result = apply_merge(local.db, plan, user=local.user)
    local.commit()
    commit_staged_files(local.db, result, staged, user=local.user,
                        instance_path=local.instance_path)
    landed = local.one(JournalSession, external_id="s1")
    assert landed.session_image_file == f"{landed.id}.png"
    assert not os.path.exists(os.path.join(local.instance_path, "etc", "passwd"))


def test_local_name_for_follows_the_readers_scheme(hub):
    session = add_session(hub, "s1")
    from nova.sync_engine import FileRef
    photo = FileRef(role=ROLE_SESSION_PHOTO, table="journal_sessions", key="s1",
                    slot="session_image_file", sha256=sha(PNG), ext="png",
                    name="whatever.png")
    assert local_name_for(JournalSession, session, photo) == f"{session.id}.png"
    project = FileRef(role=ROLE_PROJECT_IMAGE, table="projects", key="p1",
                      slot="final_image_file", sha256=sha(PNG), ext="png",
                      name="x.png")
    assert local_name_for(Project, None, project) == "project_p1.png"


def test_localize_text_puts_back_a_missing_token_as_a_url(hub):
    text = localize_text(AstroObject, "shared_notes",
                         f"<p>{FILE_TOKEN_MISSING}gone.png</p>", {}, "local")
    assert text == "<p>/uploads/local/gone.png</p>"


def test_a_note_image_that_cannot_travel_is_skipped_not_pending(hub, local):
    """The record still syncs; the image is reported, never pending.

    An image the sender cannot send is exported as a name-only
    nova-file-missing: token and listed in `skipped_files` (correction E).
    A `nova-file:` token left without a manifest entry is a different thing
    entirely and is refused (correction G, the test above).
    """
    obj = add_object(hub, "M 31")
    hub.put("note_img_eeeeeeeeeeee.png", PNG)
    obj.shared_notes = '<img src="/uploads/hub/note_img_eeeeeeeeeeee.png">'
    hub.commit()
    seed_pairing(local, hub)

    # The bytes vanish from the sender after the row was written.
    os.remove(hub.upload_path("note_img_eeeeeeeeeeee.png"))
    payload = export_changes(hub.db, hub.user, 0, side="server",
                             instance_path=hub.instance_path)
    assert payload["files"] == {}
    assert FILE_TOKEN_MISSING in payload["records"]["astro_objects"][0]["shared_notes"]
    assert [s["slot"] for s in payload["skipped_files"]] == ["note_img"]

    plan = plan_merge(local.db, local.user, payload, side="local",
                      instance_path=local.instance_path)
    assert plan.refused is False
    assert plan.files == []

    result = pull(local, hub)
    assert result.pending_files == []
    landed = local.one(AstroObject, object_name="M 31")
    assert landed is not None
    # The link is still a valid URL, just one that 404s.
    assert "/uploads/local/note_img_eeeeeeeeeeee.png" in landed.shared_notes


# =============================================================================
# 11. The cache a new log invalidates
# =============================================================================

def test_an_arriving_log_nulls_the_analysis_cache(hub, local):
    session = add_session(hub, "s1")
    name = f"{session.id}_Autorun_Log.txt"
    hub.put_log("asiair", name, b"hub log")
    session.asiair_log_content = os.path.join("instance", "logs", "asiair", name)
    hub.commit()
    seed_pairing(local, hub)
    pull(local, hub)

    landed = local.one(JournalSession, external_id="s1")
    assert landed.log_analysis_cache is None
    assert landed.asiair_log_content.endswith(f"{landed.id}_{name.split('_', 1)[1]}")


# =============================================================================
# 12. Cross-user isolation
# =============================================================================

def test_one_users_files_never_land_in_anothers_directory(tmp_path):
    """Two accounts on one installation keep their uploads apart."""
    first = Node("first", tmp_path / "inst")
    second = Node("second", tmp_path / "inst")
    try:
        session = add_session(first, "s1")
        first.put(f"{session.id}.png", PNG)
        session.session_image_file = f"{session.id}.png"
        first.commit()

        # The sender's own ref, resolved against the sender's own directory.
        refs = collect_files(first.db, first.user,
                         instance_path=first.instance_path)
        assert [r.role for r in refs] == [ROLE_SESSION_PHOTO]
        source = file_source_path(refs[0], first.user,
                                  instance_path=first.instance_path)
        assert source == first.upload_path(f"{session.id}.png")

        with open(source, "rb") as handle:
            staged = stage_file(second.user, refs[0], handle,
                                instance_path=second.instance_path)

        assert staged.path.startswith(
            os.path.join(second.instance_path, "sync_staging",
                         str(second.uid)))
        assert "first" not in staged.path
        # Staging wrote nothing into either account's uploads directory, and in
        # particular nothing landed in the sender's.
        assert os.listdir(first.uploads)
        assert not os.path.exists(os.path.join(second.instance_path, "uploads",
                                               "second", os.path.basename(staged.path)))
        assert not os.path.exists(os.path.join(second.instance_path, "uploads",
                                               "first", os.path.basename(staged.path)))
    finally:
        first.close()
        second.close()


def test_collect_files_only_walks_this_users_rows(hub, local):
    add_object(hub, "M 31")
    add_object(local, "M 99")
    session = add_session(local, "s9")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()

    refs = collect_files(local.db, local.user,
                         instance_path=local.instance_path)
    assert [r.key for r in refs] == ["s9"]


def test_export_files_describes_a_manifest_by_digest(hub, local):
    session = add_session(local, "s1")
    local.put(f"{session.id}.png", PNG)
    session.session_image_file = f"{session.id}.png"
    local.commit()

    refs = collect_files(local.db, local.user,
                         instance_path=local.instance_path)
    manifest = export_files(local.db, local.user, refs)
    assert manifest[sha(PNG)]["role"] == ROLE_SESSION_PHOTO
    assert manifest[sha(PNG)]["ext"] == "png"
