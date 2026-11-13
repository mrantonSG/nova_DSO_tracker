"""Path-containment hardening tests.

Covers the new resolve_under_root helper and the three call sites it was added
to: the ZIP journal-photo import, the Trix editor image upload, and the
generic-message change on update_project_active (CodeQL #172).
"""

import io
import os
import stat
import uuid
import zipfile

import pytest

from nova.helpers import resolve_under_root


# --- resolve_under_root -------------------------------------------------------

def test_resolve_under_root_joins_inside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    p = resolve_under_root(str(root), "a", "b.txt")
    assert p == os.path.realpath(root / "a" / "b.txt")


def test_resolve_under_root_rejects_dotdot(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert resolve_under_root(str(root), "..", "x") == ""
    assert resolve_under_root(str(root), "a", "..", "..", "x") == ""


def test_resolve_under_root_rejects_absolute(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert resolve_under_root(str(root), "/etc/passwd") == ""


def test_resolve_under_root_percent_encoding_stays_literal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    p = resolve_under_root(str(root), "%2e%2e%2f")
    # "%2e%2e%2f" is not decoded, so it must remain a literal file name inside root.
    assert p == os.path.realpath(root / "%2e%2e%2f")


def test_resolve_under_root_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    assert resolve_under_root(str(root), "link.txt") == ""


def test_resolve_under_root_nested_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.txt").write_text("secret")
    (root / "link1").symlink_to(outside)
    (root / "link2").symlink_to(root / "link1")
    assert resolve_under_root(str(root), "link2", "target.txt") == ""


def test_resolve_under_root_backslashes_never_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real_root = os.path.realpath(root)
    for part in ("..\\..\\x.jpg", "C:\\evil"):
        p = resolve_under_root(str(root), part)
        # Contained or rejected — never outside root.
        if p:
            assert os.path.commonpath([p, real_root]) == real_root


def test_resolve_under_root_rejects_nul_byte(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert resolve_under_root(str(root), "a\x00b") == ""


def test_resolve_under_root_root_itself(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert resolve_under_root(str(root)) == ""
    assert resolve_under_root(str(root), allow_root=True) == os.path.realpath(root)


# --- ZIP journal-photo import ------------------------------------------------

def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def _zip_with_symlink(name, target):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(name)
        info.create_system = 3  # Unix
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, target)
    buf.seek(0)
    return buf


def test_zip_import_traversal_is_contained(su_client_logged_in):
    from nova.config import UPLOAD_FOLDER

    token = uuid.uuid4().hex
    good_name = f"good_{token}.jpg"
    user_dir = os.path.join(UPLOAD_FOLDER, "default")
    os.makedirs(user_dir, exist_ok=True)

    buf = _zip([
        (good_name, b"good-bytes"),
        ("../../evil.jpg", b"evil"),
        ("..\\..\\evil2.jpg", b"evil2"),
        ("/evil3.jpg", b"evil3"),
    ])
    resp = su_client_logged_in.post(
        "/import_journal_photos",
        data={"file": (buf, "photos.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302

    # The valid member is imported, flat, inside the user's upload dir.
    assert os.path.exists(os.path.join(user_dir, good_name))

    # Nothing escapes user_dir: the traversal members are flattened inside it or
    # skipped, but never written to UPLOAD_FOLDER's parent or grandparent.
    parent = os.path.dirname(user_dir)            # UPLOAD_FOLDER
    grandparent = os.path.dirname(parent)         # INSTANCE_PATH
    for evil in ("evil.jpg", "evil2.jpg", "evil3.jpg"):
        assert not os.path.exists(os.path.join(parent, evil))
        assert not os.path.exists(os.path.join(grandparent, evil))


def test_zip_import_symlink_member_is_skipped(su_client_logged_in):
    from nova.config import UPLOAD_FOLDER

    token = uuid.uuid4().hex
    user_dir = os.path.join(UPLOAD_FOLDER, "default")
    os.makedirs(user_dir, exist_ok=True)

    buf = _zip_with_symlink(f"link_{token}.jpg", "/etc/passwd")
    resp = su_client_logged_in.post(
        "/import_journal_photos",
        data={"file": (buf, "photos.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    # A symlink member must not be written as a file.
    assert not os.path.exists(os.path.join(user_dir, f"link_{token}.jpg"))


# --- Trix editor image upload ------------------------------------------------

def test_editor_image_rejects_non_whitelisted_extension(su_client_logged_in):
    resp = su_client_logged_in.post(
        "/upload_editor_image",
        data={"file": (io.BytesIO(b"x"), "evil.exe")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_editor_image_rejects_traversal_name(su_client_logged_in):
    resp = su_client_logged_in.post(
        "/upload_editor_image",
        data={"file": (io.BytesIO(b"x"), "x.jpg/../../..")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# --- update_project_active hides the exception (CodeQL #172) -----------------

def test_update_project_active_returns_generic_error(su_client_logged_in, monkeypatch):
    import nova.blueprints.core as core

    def boom(*args, **kwargs):
        raise RuntimeError("SECRET_TABLE_DETAILS")

    monkeypatch.setattr(core, "invalidate_object_caches", boom)

    resp = su_client_logged_in.post(
        "/update_project_active",
        json={"object": "M42", "active": True},
    )
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["error"] == "An internal error occurred."
    # The exception text must never reach the client.
    assert "SECRET_TABLE_DETAILS" not in resp.get_data(as_text=True)
