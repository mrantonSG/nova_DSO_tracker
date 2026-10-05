"""
Ownership checks for uploaded images and the mobile journal project link.
In multi-user mode, user A must not be able to load user B's session or
project photos, or link a journal session to B's project. Images embedded
in shared notes (note_img_*) stay readable across users, and single-user
mode keeps its legacy folder fallbacks.
"""
from datetime import date

import pytest

from nova.models import JournalSession, Project


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr('nova.blueprints.core.UPLOAD_FOLDER', str(root))
    return root


def _put(root, username, filename, data=b"img"):
    d = root / username
    d.mkdir(exist_ok=True)
    (d / filename).write_bytes(data)


def test_uploads_other_user_session_image_404(multi_user_client, upload_root):
    client, _ = multi_user_client
    _put(upload_root, "UserB", "7.jpg", b"B session photo")
    _put(upload_root, "UserB", "thumb_7.jpg", b"B thumb")
    _put(upload_root, "UserB", "project_abc.jpg", b"B project photo")

    for name in ("7.jpg", "thumb_7.jpg", "project_abc.jpg", "note_img_/../7.jpg"):
        resp = client.get(f'/uploads/UserB/{name}')
        assert resp.status_code == 404, name
        assert b"B session photo" not in resp.data
    assert (upload_root / "UserB" / "7.jpg").read_bytes() == b"B session photo"


def test_uploads_own_session_image_served(multi_user_client, upload_root):
    client, _ = multi_user_client
    _put(upload_root, "UserA", "5.jpg", b"A session photo")

    resp = client.get('/uploads/UserA/5.jpg')

    assert resp.status_code == 200
    assert resp.data == b"A session photo"


def test_uploads_shared_note_image_from_other_user_served(multi_user_client, upload_root):
    client, _ = multi_user_client
    _put(upload_root, "UserB", "note_img_0123456789ab.jpg", b"B note image")

    resp = client.get('/uploads/UserB/note_img_0123456789ab.jpg')

    assert resp.status_code == 200
    assert resp.data == b"B note image"


def test_uploads_single_user_mode_unchanged(su_client_logged_in, upload_root):
    client = su_client_logged_in
    # Legacy URL segment (old username) whose folder still exists.
    _put(upload_root, "oldname", "7.jpg", b"legacy folder photo")
    # Legacy URL segment whose file only exists under "default".
    _put(upload_root, "default", "8.jpg", b"default folder photo")

    resp = client.get('/uploads/oldname/7.jpg')
    assert resp.status_code == 200
    assert resp.data == b"legacy folder photo"

    resp = client.get('/uploads/oldname/8.jpg')
    assert resp.status_code == 200
    assert resp.data == b"default folder photo"


def test_mobile_journal_new_rejects_foreign_project(multi_user_client, db_session):
    client, ids = multi_user_client
    project_b = Project(id="b" * 32, user_id=ids["user_b_id"], name="B Secret Project")
    db_session.add(project_b)
    db_session.commit()

    resp = client.post('/m/journal/new', data={
        'session_date': "2026-01-10",
        'target_object_id': "M42",
        'location_name': "UserA_Home",
        'project_selection': project_b.id,
    })

    assert resp.status_code == 302
    db_session.expire_all()
    session_a = db_session.query(JournalSession).filter_by(user_id=ids["user_a_id"]).one()
    assert session_a.project_id is None
    project = db_session.get(Project, "b" * 32)
    assert project.user_id == ids["user_b_id"]
    assert project.name == "B Secret Project"

    # Report page: a session row that already points at B's project (written
    # before the fix) does not show B's project name.
    old_row = JournalSession(user_id=ids["user_a_id"], object_name="M42",
                             date_utc=date(2026, 1, 11), project_id=project_b.id)
    db_session.add(old_row)
    db_session.commit()

    resp = client.get(f'/journal/report_page/{old_row.id}')

    assert resp.status_code == 200
    assert b"B Secret Project" not in resp.data
