"""Diff 6b part 1: the importer keeps the file's record UIDs.

Covers the four owned types (locations, components, rigs, objects) for every
entry point: the three config routes and the full import.  A file without UIDs
gives each record its previous UID back by natural key (I2), a clashing UID
fails the import (I7), and every lookup is per user (I8).
"""

import io

import yaml

from nova.migration import export_user_data, export_user_to_yaml, import_user_from_yaml
from nova.models import AstroObject, Component, DbUser, Location, Rig


def _user(db, username):
    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if u is None:
        u = DbUser(username=username)
        db.add(u)
        db.flush()
    return u


def _populate(db, username):
    """A location, an object, a telescope, a camera and a rig, adding only what
    the fixture has not already created for this user."""
    u = _user(db, username)

    def one(model, defaults, **key):
        row = db.query(model).filter_by(user_id=u.id, **key).one_or_none()
        if row is None:
            row = model(user_id=u.id, **key, **defaults)
            db.add(row)
            db.flush()
        return row

    loc = one(Location, {"lat": 1.0, "lon": 2.0, "timezone": "UTC"}, name="Home")
    obj = one(AstroObject, {"ra_hours": 1.0, "dec_deg": 2.0}, object_name="M42")
    tel = one(Component, {"aperture_mm": 80, "focal_length_mm": 480},
              kind="telescope", name="Scope")
    cam = one(Component, {"sensor_width_mm": 10, "sensor_height_mm": 8, "pixel_size_um": 3.76},
              kind="camera", name="Cam")
    rig = one(Rig, {"telescope_id": tel.id, "camera_id": cam.id}, rig_name="Main")
    db.commit()
    return u


def _uids(db, username):
    u = _user(db, username)
    return {
        "locations": {r.name: r.record_uid
                      for r in db.query(Location).filter_by(user_id=u.id)},
        "objects": {r.object_name: r.record_uid
                    for r in db.query(AstroObject).filter_by(user_id=u.id)},
        "components": {(r.kind, r.name): r.record_uid
                       for r in db.query(Component).filter_by(user_id=u.id)},
        "rigs": {r.rig_name: r.record_uid
                 for r in db.query(Rig).filter_by(user_id=u.id)},
    }


def _post_file(client, url, path):
    return client.post(url,
                       data={"file": (io.BytesIO(path.read_bytes()), path.name)},
                       content_type="multipart/form-data")


def _post_full(client, cfg, rigs, jrn, clear=True):
    return client.post("/tools/import", data={
        "username": "default",
        "clear_existing": "true" if clear else "false",
        "config_file": (io.BytesIO(cfg.read_bytes()), cfg.name),
        "rigs_file": (io.BytesIO(rigs.read_bytes()), rigs.name),
        "journal_file": (io.BytesIO(jrn.read_bytes()), jrn.name),
    }, content_type="multipart/form-data")


def _strip_uids(doc):
    """The same document with every record_uid removed (an older export)."""
    if isinstance(doc, dict):
        return {k: _strip_uids(v) for k, v in doc.items() if k != "record_uid"}
    if isinstance(doc, list):
        return [_strip_uids(v) for v in doc]
    return doc


def _without_uids(tmp_path, name, src):
    old = _strip_uids(yaml.safe_load(src.read_text()))
    dst = tmp_path / name
    dst.write_text(yaml.safe_dump(old))
    return dst


# --- Every entry point keeps the UIDs the file carries ------------------------

def test_config_import_keeps_record_uids(client, db_session, tmp_path):
    _populate(db_session, "default")
    before = _uids(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    assert _post_file(client, "/import_config", tmp_path / "config_default.yaml").status_code == 302
    db_session.expire_all()
    after = _uids(db_session, "default")
    assert after["locations"] == before["locations"]
    assert after["objects"] == before["objects"]


def test_rigs_import_keeps_record_uids(client, db_session, tmp_path):
    _populate(db_session, "default")
    before = _uids(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    assert _post_file(client, "/import_rig_config", tmp_path / "rigs_default.yaml").status_code == 302
    db_session.expire_all()
    after = _uids(db_session, "default")
    assert after["components"] == before["components"]
    assert after["rigs"] == before["rigs"]


def test_full_import_keeps_record_uids(client, db_session, tmp_path):
    _populate(db_session, "default")
    before = _uids(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    assert _post_full(client, tmp_path / "config_default.yaml",
                      tmp_path / "rigs_default.yaml",
                      tmp_path / "journal_default.yaml").status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "default") == before


# --- A file without UIDs gets the previous UID back by natural key (I2) -------

def test_config_without_uids_restores_by_natural_key(client, db_session, tmp_path):
    _populate(db_session, "default")
    before = _uids(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    p = _without_uids(tmp_path, "config_old.yaml", tmp_path / "config_default.yaml")
    assert _post_file(client, "/import_config", p).status_code == 302
    db_session.expire_all()
    after = _uids(db_session, "default")
    assert after["locations"] == before["locations"]
    assert after["objects"] == before["objects"]


def test_rigs_without_uids_restore_by_natural_key(client, db_session, tmp_path):
    _populate(db_session, "default")
    before = _uids(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    p = _without_uids(tmp_path, "rigs_old.yaml", tmp_path / "rigs_default.yaml")
    assert _post_file(client, "/import_rig_config", p).status_code == 302
    db_session.expire_all()
    after = _uids(db_session, "default")
    assert after["components"] == before["components"]
    assert after["rigs"] == before["rigs"]


def test_full_import_without_uids_restores_all_four_types(client, db_session, tmp_path):
    _populate(db_session, "default")
    before = _uids(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    cfg = _without_uids(tmp_path, "config_old.yaml", tmp_path / "config_default.yaml")
    rigs = _without_uids(tmp_path, "rigs_old.yaml", tmp_path / "rigs_default.yaml")
    jrn = _without_uids(tmp_path, "journal_old.yaml", tmp_path / "journal_default.yaml")

    assert _post_full(client, cfg, rigs, jrn, clear=True).status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "default") == before


# --- Idempotency and clashes --------------------------------------------------

def test_import_same_file_twice_no_duplicates_no_uid_change(client, db_session, tmp_path):
    _populate(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True
    cfg = tmp_path / "config_default.yaml"

    assert _post_file(client, "/import_config", cfg).status_code == 302
    db_session.expire_all()
    first = _uids(db_session, "default")
    assert _post_file(client, "/import_config", cfg).status_code == 302
    db_session.expire_all()

    u = _user(db_session, "default")
    assert _uids(db_session, "default") == first
    assert db_session.query(Location).filter_by(user_id=u.id, name="Home").count() == 1
    assert db_session.query(AstroObject).filter_by(user_id=u.id, object_name="M42").count() == 1


def test_uid_clash_fails_and_changes_nothing(client, db_session, tmp_path):
    u = _populate(db_session, "default")
    before = _uids(db_session, "default")
    cfg = export_user_data(db_session, u)[0]
    # Two locations under one UID: the second entry clashes with the first.
    cfg["locations"]["Second"] = dict(cfg["locations"]["Home"], record_uid="a" * 32)
    cfg["locations"]["Home"]["record_uid"] = "a" * 32
    p = tmp_path / "clash.yaml"
    p.write_text(yaml.safe_dump(cfg))

    assert _post_file(client, "/import_config", p).status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "default") == before


# --- Per-user scoping (I8) ----------------------------------------------------

def test_two_accounts_can_import_the_same_file(db_session, tmp_path):
    _populate(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    ok = import_user_from_yaml("Second",
                               str(tmp_path / "config_default.yaml"),
                               str(tmp_path / "rigs_default.yaml"),
                               str(tmp_path / "journal_default.yaml"),
                               clear_existing=True)
    assert ok is True
    db_session.expire_all()
    assert _uids(db_session, "Second") == _uids(db_session, "default")


def test_second_user_rows_unchanged(client, db_session, tmp_path):
    _populate(db_session, "default")
    other = _user(db_session, "Second")
    other_loc = Location(user_id=other.id, name="Home", lat=9.0, lon=9.0, timezone="UTC")
    other_obj = AstroObject(user_id=other.id, object_name="M42", ra_hours=9.0, dec_deg=9.0)
    db_session.add_all([other_loc, other_obj])
    db_session.commit()
    before = _uids(db_session, "Second")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True

    assert _post_file(client, "/import_config", tmp_path / "config_default.yaml").status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "Second") == before
