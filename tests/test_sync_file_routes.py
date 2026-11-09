"""Corrections A and D at the route level, through the Flask test client.

A photo, a project image and a log keep their name when they are replaced: a
session photo is "<session id>.<ext>" whichever bytes it holds, a project image
is "project_<project id>.<ext>", a log is "<session id>_<original name>". The
column therefore does not change and before_flush sees an unmodified row, so
the write path has to call mark_file_changed() by hand (correction A) or the
new bytes never travel. Every write to a live name must also drop the stale
thumbnail first (correction D).

These tests drive the real routes; tests/test_sync_files.py covers the engine
with hand-built payloads.
"""
import os
import zipfile
from datetime import date
from io import BytesIO

import pytest

from nova.helpers import log_file_path
from nova.models import INSTANCE_PATH, DbUser, JournalSession, Project, SyncCounter

PNG = b"\x89PNG\r\n\x1a\n" + b"first-photo" * 8
PNG_ALT = b"\x89PNG\r\n\x1a\n" + b"replacement-photo" * 8
STALE_THUMB = b"stale-thumbnail-bytes"

# The three log types: (route field, uploaded name, the column it lands in).
LOGS = [
    pytest.param("asiair_log", "Autorun_Log.txt", "asiair_log_content", id="asiair"),
    pytest.param("phd2_log", "PHD2_GuideLog.txt", "phd2_log_content", id="phd2"),
    pytest.param("nina_log", "NINA_log.log", "nina_log_content", id="nina"),
]


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """One upload root for the journal, project and app-factory write paths."""
    root = tmp_path / "uploads"
    root.mkdir()
    for target in ("nova.blueprints.journal.UPLOAD_FOLDER",
                   "nova.blueprints.projects.UPLOAD_FOLDER",
                   "nova.UPLOAD_FOLDER"):
        monkeypatch.setattr(target, str(root))
    return root


@pytest.fixture
def user_dir(uploads):
    directory = uploads / "UserA"
    directory.mkdir()
    return directory


@pytest.fixture
def world(multi_user_client, db_session):
    client, ids = multi_user_client

    class W:
        pass

    w = W()
    w.client, w.db, w.uid = client, db_session, ids["user_a_id"]
    return w


def _rev(db, uid):
    db.expire_all()
    row = db.query(SyncCounter).filter_by(user_id=uid).one_or_none()
    return row.rev if row else 0


def _session(db, uid, **fields):
    fields.setdefault("date_utc", date(2026, 1, 10))
    fields.setdefault("external_id", "ext-1")
    row = JournalSession(user_id=uid, **fields)
    db.add(row)
    db.commit()
    db.expire_all()
    return row


def _put(path, data):
    path.write_bytes(data)
    return path


def _post(w, url, data=None, files=None):
    resp = w.client.post(url, data=dict(data or {}, **(files or {})),
                         content_type="multipart/form-data")
    assert resp.status_code == 302, resp.status_code
    w.db.expire_all()
    return resp


# =============================================================================
# A — a file change with no column change still stamps the record
#
# The edit form rewrites every column from the request, so a plain edit POST
# stamps the row whether or not mark_file_changed ran. To make these tests bite,
# each one first posts the form once so the row already holds the form's own
# values; the second post then differs only in the file on disk.
# =============================================================================

SESSION_FORM = {"session_date": "2026-01-10"}


def _prime_session(w, session_id, data=None, files=None):
    """Settle the row on the form's own values, so a re-post changes nothing."""
    _post(w, f"/journal/edit/{session_id}", data=dict(data or SESSION_FORM),
          files=files)
    w.db.expire_all()


def _prime_project(w, project_id, data=None, files=None):
    _post(w, f"/project/{project_id}", data=dict(data or PROJECT_FORM),
          files=files)
    w.db.expire_all()


PROJECT_FORM = {"name": "Veil", "status": "In Progress"}


def test_a_photo_replaced_under_the_same_name_stamps_the_session(world, user_dir):
    """The name is "<id>.<ext>" either way, so only mark_file_changed can stamp it."""
    session = _session(world.db, world.uid)
    name = f"{session.id}.png"
    _prime_session(world, session.id,
                   files={"session_image": (BytesIO(PNG), "photo.png")})
    assert world.db.get(JournalSession, session.id).session_image_file == name
    _put(user_dir / f"thumb_{name}", STALE_THUMB)
    before = _rev(world.db, world.uid)

    _post(world, f"/journal/edit/{session.id}", data=SESSION_FORM,
          files={"session_image": (BytesIO(PNG_ALT), "whatever.png")})

    world.db.expire_all()
    session = world.db.get(JournalSession, session.id)
    assert session.session_image_file == name          # the column did not move
    assert (user_dir / name).read_bytes() == PNG_ALT   # but the bytes did
    assert session.sync_rev is not None and session.sync_rev > before


def test_a_photo_uploaded_on_add_stamps_the_session(world, user_dir):
    """A new row is stamped by the insert path; the upload must land all the same."""
    before = _rev(world.db, world.uid)

    _post(world, "/journal/add",
          data={"session_date": "2026-01-10", "target_object_id": "M 42"},
          files={"session_image": (BytesIO(PNG), "photo.png")})

    session = world.db.query(JournalSession).filter_by(user_id=world.uid).one()
    assert session.session_image_file == f"{session.id}.png"
    assert (user_dir / session.session_image_file).read_bytes() == PNG
    assert session.sync_rev is not None and session.sync_rev > before


@pytest.mark.parametrize("field,filename,column", LOGS)
def test_a_log_uploaded_on_add_stamps_the_session(world, field, filename, column):
    before = _rev(world.db, world.uid)

    _post(world, "/journal/add",
          data={"session_date": "2026-01-10", "target_object_id": "M 42"},
          files={field: (BytesIO(b"log body"), filename)})

    session = world.db.query(JournalSession).filter_by(user_id=world.uid).one()
    assert getattr(session, column) is not None
    assert os.path.isfile(log_file_path(getattr(session, column)))
    assert session.sync_rev is not None and session.sync_rev > before


@pytest.mark.parametrize("field,filename,column", LOGS)
def test_a_log_replaced_under_the_same_name_stamps_the_session(world, field,
                                                               filename, column):
    """The stored path is "<id>_<name>", so new bytes leave the column alone."""
    session = _session(world.db, world.uid)
    _prime_session(world, session.id, files={field: (BytesIO(b"first body"), filename)})
    stored = getattr(world.db.get(JournalSession, session.id), column)
    assert stored is not None
    before = _rev(world.db, world.uid)

    _post(world, f"/journal/edit/{session.id}", data=SESSION_FORM,
          files={field: (BytesIO(b"second body"), filename)})

    world.db.expire_all()
    session = world.db.get(JournalSession, session.id)
    assert getattr(session, column) == stored              # the column did not move
    with open(log_file_path(stored), encoding="utf-8") as handle:
        assert handle.read() == "second body"              # but the bytes did
    assert session.sync_rev is not None and session.sync_rev > before


def test_a_project_image_replaced_under_the_same_name_stamps_the_project(world, user_dir):
    """The name is "project_<id>.<ext>" either way (correction A)."""
    project = Project(id="p" * 32, user_id=world.uid, name="Veil",
                      status="In Progress")
    world.db.add(project)
    world.db.commit()
    name = f"project_{project.id}.png"
    _prime_project(world, project.id,
                   files={"final_image": (BytesIO(PNG), "photo.png")})
    assert world.db.get(Project, project.id).final_image_file == name
    _put(user_dir / f"thumb_{name}", STALE_THUMB)
    before = _rev(world.db, world.uid)

    _post(world, f"/project/{project.id}", data=PROJECT_FORM,
          files={"final_image": (BytesIO(PNG_ALT), "whatever.png")})

    project = world.db.get(Project, project.id)
    assert project.final_image_file == name
    assert (user_dir / name).read_bytes() == PNG_ALT
    assert project.sync_rev is not None and project.sync_rev > before


@pytest.fixture
def zip_import(tmp_path, monkeypatch, world):
    """The import route's upload root, holding the logged-in user's directory.

    The route takes its username from the session, so the root needs that one
    child and nothing else. tools.py imported SINGLE_USER_MODE by value, so it
    still reads whatever the test .env held at import time; the client here is
    multi-user, and the patch is what puts the file under UserA/.
    """
    root = tmp_path / "uploads"
    (root / "UserA").mkdir(parents=True)
    monkeypatch.setattr("nova.blueprints.tools.UPLOAD_FOLDER", str(root))
    monkeypatch.setattr("nova.blueprints.tools.SINGLE_USER_MODE", False)
    return root / "UserA"


def _zip_with(name, data):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
    buffer.seek(0)
    return buffer


def _import_zip(world, name, data):
    resp = world.client.post(
        "/import_journal_photos",
        data={"file": (_zip_with(name, data), "photos.zip")})
    assert resp.status_code == 302
    world.db.expire_all()
    return resp


def test_importing_a_zip_over_a_session_photo_stamps_the_session(world, zip_import):
    """Restoring a backup photo over a live one must move the rev.

    The photo keeps its "<session id>.<ext>" name, so no column changes and only
    the route's own stamp can send the new bytes.
    """
    session = _session(world.db, world.uid)
    name = f"{session.id}.png"
    photo = zip_import / name
    photo.write_bytes(PNG)
    world.db.get(JournalSession, session.id).session_image_file = name
    world.db.commit()
    (zip_import / f"thumb_{name}").write_bytes(STALE_THUMB)
    before = _rev(world.db, world.uid)

    _import_zip(world, f"uploads/whatever/{name}", PNG_ALT)

    session = world.db.get(JournalSession, session.id)
    assert session.session_image_file == name       # the column did not move
    assert photo.read_bytes() == PNG_ALT            # but the bytes did
    assert session.sync_rev is not None and session.sync_rev > before
    assert not (zip_import / f"thumb_{name}").exists()


def test_importing_a_zip_over_a_project_image_stamps_the_project(world, zip_import):
    project = Project(id="z" * 32, user_id=world.uid, name="Veil",
                      status="In Progress")
    world.db.add(project)
    world.db.commit()
    name = f"project_{project.id}.png"
    photo = zip_import / name
    photo.write_bytes(PNG)
    world.db.get(Project, project.id).final_image_file = name
    world.db.commit()
    before = _rev(world.db, world.uid)

    _import_zip(world, f"uploads/whatever/{name}", PNG_ALT)

    project = world.db.get(Project, project.id)
    assert project.final_image_file == name
    assert photo.read_bytes() == PNG_ALT
    assert project.sync_rev is not None and project.sync_rev > before


def test_importing_a_zip_with_an_unrelated_photo_stamps_nothing(world, zip_import):
    """A file no record names is just a file: no stamp, no rev."""
    before = _rev(world.db, world.uid)
    _import_zip(world, "loose.png", PNG)
    assert (zip_import / "loose.png").read_bytes() == PNG
    assert _rev(world.db, world.uid) == before


def test_the_mobile_quick_entry_stamps_the_new_session(world):
    """Mobile has no file upload; the row it creates is stamped all the same."""
    before = _rev(world.db, world.uid)

    _post(world, "/m/journal/new",
          data={"session_date": "2026-01-10", "target_object_id": "M 42"})

    session = world.db.query(JournalSession).filter_by(user_id=world.uid).one()
    assert session.sync_rev is not None and session.sync_rev > before


def test_the_mobile_quick_entry_ignores_an_uploaded_photo(world, user_dir):
    """No mobile route reads request.files, so no file and no file column."""
    _post(world, "/m/journal/new",
          data={"session_date": "2026-01-10", "target_object_id": "M 42"},
          files={"session_image": (BytesIO(PNG), "photo.png")})

    session = world.db.query(JournalSession).filter_by(user_id=world.uid).one()
    assert session.session_image_file is None
    assert list(user_dir.iterdir()) == []


# =============================================================================
# D — a write to a live name takes the stale thumbnail with it
# =============================================================================

def test_replacing_a_session_photo_leaves_no_stale_thumbnail(world, user_dir):
    session = _session(world.db, world.uid)
    name = f"{session.id}.png"
    _prime_session(world, session.id,
                   files={"session_image": (BytesIO(PNG), "photo.png")})
    _put(user_dir / f"thumb_{name}", STALE_THUMB)

    _post(world, f"/journal/edit/{session.id}", data=SESSION_FORM,
          files={"session_image": (BytesIO(PNG_ALT), "whatever.png")})

    # The bytes are not a decodable image, so no fresh thumbnail is written;
    # what matters is that the one describing the old bytes is gone.
    assert not (user_dir / f"thumb_{name}").exists()


def test_replacing_a_project_image_leaves_no_stale_thumbnail(world, user_dir):
    project = Project(id="q" * 32, user_id=world.uid, name="Veil",
                      status="In Progress")
    world.db.add(project)
    world.db.commit()
    name = f"project_{project.id}.png"
    _prime_project(world, project.id,
                   files={"final_image": (BytesIO(PNG), "photo.png")})
    _put(user_dir / f"thumb_{name}", STALE_THUMB)

    _post(world, f"/project/{project.id}", data=PROJECT_FORM,
          files={"final_image": (BytesIO(PNG_ALT), "whatever.png")})

    assert not (user_dir / f"thumb_{name}").exists()


def test_deleting_a_session_takes_its_photo_and_thumbnail(world, user_dir):
    session = _session(world.db, world.uid)
    name = f"{session.id}.png"
    _put(user_dir / name, PNG)
    _put(user_dir / f"thumb_{name}", STALE_THUMB)
    session.session_image_file = name
    world.db.commit()

    resp = world.client.post(f"/journal/delete/{session.id}")
    assert resp.status_code == 302

    assert not (user_dir / name).exists()
    assert not (user_dir / f"thumb_{name}").exists()


def test_deleting_a_projects_final_image_takes_its_thumbnail(world, user_dir):
    project = Project(id="r" * 32, user_id=world.uid, name="Veil",
                      status="In Progress")
    world.db.add(project)
    world.db.commit()
    name = f"project_{project.id}.png"
    _put(user_dir / name, PNG)
    _put(user_dir / f"thumb_{name}", STALE_THUMB)
    project.final_image_file = name
    world.db.commit()

    _post(world, f"/project/{project.id}",
          data={"name": "Veil", "status": "In Progress",
                "delete_final_image": "1"})

    assert not (user_dir / name).exists()
    assert not (user_dir / f"thumb_{name}").exists()
    world.db.expire_all()
    # Emptied, not NULL: the user removed the image, and "" is what instance
    # sync reads as an explicit removal rather than "this slot never held one".
    assert world.db.get(Project, project.id).final_image_file == ""


# =============================================================================
# E — a removal is a removal, not a slot that was never filled
#
# The removal routes empty the column rather than NULLing it. Sync reads the
# two differently: "" travels as an explicit null, so the peer clears its copy,
# while NULL says nothing at all and the peer keeps what it holds.
# =============================================================================

def test_deleting_a_session_photo_empties_the_slot_and_stamps(world, user_dir):
    session = _session(world.db, world.uid)
    name = f"{session.id}.png"
    _prime_session(world, session.id,
                   files={"session_image": (BytesIO(PNG), "photo.png")})
    assert world.db.get(JournalSession, session.id).session_image_file == name
    before = _rev(world.db, world.uid)

    _post(world, f"/journal/edit/{session.id}",
          data={"session_date": "2026-01-10", "delete_session_image": "1"})

    session = world.db.get(JournalSession, session.id)
    assert session.session_image_file == ""
    assert not (user_dir / name).exists()
    assert session.sync_rev is not None and session.sync_rev > before


@pytest.mark.parametrize("field,filename,column", LOGS)
def test_deleting_a_log_empties_the_slot_and_stamps(world, field, filename, column):
    session = _session(world.db, world.uid)
    _prime_session(world, session.id,
                   files={field: (BytesIO(b"log body"), filename)})
    assert getattr(world.db.get(JournalSession, session.id), column) is not None
    before = _rev(world.db, world.uid)

    _post(world, f"/journal/edit/{session.id}",
          data={"session_date": "2026-01-10", f"delete_{field}": "1"})

    session = world.db.get(JournalSession, session.id)
    assert getattr(session, column) == ""
    assert session.sync_rev is not None and session.sync_rev > before


def test_deleting_a_project_image_empties_the_slot_and_stamps(world, user_dir):
    project = Project(id="s" * 32, user_id=world.uid, name="Veil",
                      status="In Progress")
    world.db.add(project)
    world.db.commit()
    name = f"project_{project.id}.png"
    _prime_project(world, project.id,
                   files={"final_image": (BytesIO(PNG), "photo.png")})
    assert world.db.get(Project, project.id).final_image_file == name
    before = _rev(world.db, world.uid)

    _post(world, f"/project/{project.id}",
          data={**PROJECT_FORM, "delete_final_image": "1"})

    project = world.db.get(Project, project.id)
    assert project.final_image_file == ""
    assert not (user_dir / name).exists()
    assert project.sync_rev is not None and project.sync_rev > before


# =============================================================================
# F — a restored log is stamped for the session whose path names it
# =============================================================================

def test_importing_a_zip_restores_a_log_and_stamps_the_session(world, zip_import):
    """The stored path is the one the ZIP carries, so no column moves."""
    session = _session(world.db, world.uid)
    name = f"{session.id}_Autorun_Log.txt"
    stored = os.path.join("instance", "logs", "asiair", name)
    world.db.get(JournalSession, session.id).asiair_log_content = stored
    world.db.commit()
    path = log_file_path(stored)
    # The route restores only what is not there, and the log directory is shared
    # by every test in the run, so an earlier test's log of the same name goes.
    if os.path.exists(path):
        os.remove(path)
    before = _rev(world.db, world.uid)

    _import_zip(world, f"logs/asiair/{name}", b"restored log")

    session = world.db.get(JournalSession, session.id)
    assert session.asiair_log_content == stored
    assert os.path.isfile(path)
    assert session.sync_rev is not None and session.sync_rev > before


def test_importing_a_zip_with_an_unrelated_log_stamps_nothing(world, zip_import):
    """A log no session names is a file, not a record."""
    before = _rev(world.db, world.uid)
    _import_zip(world, "logs/asiair/999_Autorun_Log.txt", b"nobody's log")
    # It was extracted all the same: nothing was stamped, and nothing should be.
    assert os.path.isfile(os.path.join(INSTANCE_PATH, "logs", "asiair",
                                       "999_Autorun_Log.txt"))
    assert _rev(world.db, world.uid) == before
