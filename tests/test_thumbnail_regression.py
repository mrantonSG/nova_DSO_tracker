"""Regression: thumbnail generation after the atomic-write switch.

`write_file_atomic` writes to a `tempfile.mkstemp(prefix=".tmp_")` path, which
has no extension. Pillow infers an image's format from the path extension, so
`Image.save(tmp)` without an explicit `format=` raised
"unknown file extension: " and every thumbnail (session upload, session
self-heal, project upload, project self-heal) silently failed.

The fix keeps the target's extension on the temp name. These tests drive the
four real paths and open the produced thumbnail with Pillow to prove it is a
valid image of the right format.
"""
import io
import random

import pytest
from PIL import Image

from nova import app
from nova.journal_rows import session_image_url
from nova.models import DbUser, Project


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """One upload root for every module that bound UPLOAD_FOLDER at import."""
    root = tmp_path / "uploads"
    root.mkdir()
    for target in ("nova.blueprints.journal.UPLOAD_FOLDER",
                   "nova.journal_rows.UPLOAD_FOLDER",
                   "nova.blueprints.core.UPLOAD_FOLDER",
                   "nova.blueprints.projects.UPLOAD_FOLDER",
                   "nova.UPLOAD_FOLDER"):
        monkeypatch.setattr(target, str(root))
    return root


def _image_bytes(fmt="JPEG", size=(200, 200)):
    """A real image whose encoded bytes clear the self-heal 1024-byte floor."""
    im = Image.new("RGB", size)
    px = im.load()
    rng = random.Random(0xC0FFEE)
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = (rng.getrandbits(8), rng.getrandbits(8), rng.getrandbits(8))
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    data = buf.getvalue()
    assert len(data) > 1024, f"{fmt} image too small for the self-heal floor: {len(data)}"
    return data


def _assert_valid_image(path, expected_format, size=(200, 200)):
    assert path.exists(), f"thumbnail not created: {path}"
    with Image.open(path) as im:
        im.load()
        assert im.format == expected_format, f"{path}: format is {im.format}, expected {expected_format}"
        assert im.size == size


def test_session_thumbnail_generated_on_upload(client, db_session, upload_dir):
    resp = client.post("/journal/add", data={
        "form_action": "save_draft",
        "session_date": "2026-01-10",
        "target_object_id": "M42",
        "session_image": (io.BytesIO(_image_bytes("JPEG")), "photo.jpg"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    session_id = resp.get_json()["session_id"]

    assert (upload_dir / "default" / f"{session_id}.jpg").exists()
    _assert_valid_image(upload_dir / "default" / f"thumb_{session_id}.jpg", "JPEG")


def test_session_thumbnail_self_heal(upload_dir):
    user_dir = upload_dir / "default"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "7.jpg").write_bytes(_image_bytes("JPEG"))

    with app.test_request_context("/"):
        url = session_image_url("default", "7.jpg")

    assert "thumb_7.jpg" in url
    _assert_valid_image(user_dir / "thumb_7.jpg", "JPEG")


def test_project_thumbnail_generated_on_upload(client, db_session, upload_dir):
    user = db_session.query(DbUser).filter_by(username="default").one()
    project = Project(id="a" * 32, user_id=user.id, name="Proj", status="In Progress")
    db_session.add(project)
    db_session.commit()

    resp = client.post(f"/project/{project.id}", data={
        "name": "Proj",
        "status": "In Progress",
        "target_object_id": "M42",
        "final_image": (io.BytesIO(_image_bytes("JPEG")), "final.jpg"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302

    _assert_valid_image(upload_dir / "default" / f"thumb_project_{project.id}.jpg", "JPEG")


def test_project_thumbnail_self_heal(client, db_session, upload_dir):
    user = db_session.query(DbUser).filter_by(username="default").one()
    project_id = "b" * 32
    project = Project(id=project_id, user_id=user.id, name="Proj2",
                      status="In Progress", final_image_file=f"project_{project_id}.jpg")
    db_session.add(project)
    db_session.commit()

    user_dir = upload_dir / "default"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / f"project_{project_id}.jpg").write_bytes(_image_bytes("JPEG"))

    resp = client.get("/")
    assert resp.status_code == 200

    _assert_valid_image(user_dir / f"thumb_project_{project_id}.jpg", "JPEG")


def test_session_thumbnail_png_format(client, db_session, upload_dir):
    resp = client.post("/journal/add", data={
        "form_action": "save_draft",
        "session_date": "2026-01-10",
        "target_object_id": "M42",
        "session_image": (io.BytesIO(_image_bytes("PNG")), "photo.png"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    session_id = resp.get_json()["session_id"]

    _assert_valid_image(upload_dir / "default" / f"thumb_{session_id}.png", "PNG")
