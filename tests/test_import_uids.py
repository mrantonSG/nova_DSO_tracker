"""Diff 6b part 1: the importer keeps the file's record UIDs.

Covers the four owned types (locations, components, rigs, objects) for every
entry point: the three config routes and the full import.  A file without UIDs
gives each record its previous UID back by natural key (I2), a clashing UID
fails the import (I7), and every lookup is per user (I8).
"""

import io
import json
from datetime import date

import pytest
import yaml

from nova.migration import (
    _migrate_journal, export_user_data, export_user_to_yaml, import_user_from_yaml,
)
from nova.models import (
    AstroObject, Component, DbUser, JournalSession, Location, Project, Rig,
    SavedFraming, SavedView, UiPref,
)
from nova.record_links import (
    LINKS, count_link_disagreements,
    sync_framing_links, sync_project_link, sync_rig_links, sync_session_links,
)


@pytest.fixture(autouse=True)
def _no_outlook_worker(monkeypatch):
    """The import routes start the background outlook worker, which keeps a
    connection to the test database open (SQLite: "database table is locked" at
    teardown). Same no-op patch as tests/test_uid_links_bulk_paths.py."""
    monkeypatch.setattr("nova.update_outlook_cache", lambda *args, **kwargs: None)


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


def _without_active(src, tmp_path, name):
    """The same rigs document with every "active" key removed (an older file)."""
    doc = yaml.safe_load(src.read_text())
    for row in doc.get("rigs", []):
        row.pop("active", None)
    dst = tmp_path / name
    dst.write_text(yaml.safe_dump(doc))
    return dst


def _packed_rig(db, user, active):
    """A second rig of `user`, on the same telescope and camera as "Main"."""
    main = db.query(Rig).filter_by(user_id=user.id, rig_name="Main").one()
    rig = Rig(user_id=user.id, rig_name="Packed",
              telescope_id=main.telescope_id, camera_id=main.camera_id,
              active=active)
    db.add(rig)
    sync_rig_links(db, rig)
    db.commit()
    return rig


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


def test_full_clear_round_trip_keeps_the_active_flag_and_the_uid(db_session, tmp_path):
    u = _populate(db_session, "default")
    _packed_rig(db_session, u, active=False)
    before = {r.rig_name: (r.record_uid, r.active)
              for r in db_session.query(Rig).filter_by(user_id=u.id)}

    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True
    assert import_user_from_yaml("default",
                                 str(tmp_path / "config_default.yaml"),
                                 str(tmp_path / "rigs_default.yaml"),
                                 str(tmp_path / "journal_default.yaml"),
                                 clear_existing=True) is True
    db_session.expire_all()
    after = {r.rig_name: (r.record_uid, r.active)
             for r in db_session.query(Rig).filter_by(user_id=u.id)}

    assert after == before
    assert after["Main"][1] is True
    assert after["Packed"][1] is False


def test_file_without_the_key_imports_every_rig_as_active(db_session, tmp_path):
    _populate(db_session, "default")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True
    old_rigs = _without_active(tmp_path / "rigs_default.yaml", tmp_path, "rigs_old.yaml")

    assert import_user_from_yaml("Second",
                                 str(tmp_path / "config_default.yaml"),
                                 str(old_rigs),
                                 str(tmp_path / "journal_default.yaml"),
                                 clear_existing=True) is True
    db_session.expire_all()
    rows = db_session.query(Rig).filter_by(user_id=_user(db_session, "Second").id).all()
    assert rows
    assert all(r.active is True for r in rows)


def test_update_without_the_key_leaves_an_inactive_rig_inactive(db_session, tmp_path):
    u = _populate(db_session, "default")
    _packed_rig(db_session, u, active=False)
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True
    old_rigs = _without_active(tmp_path / "rigs_default.yaml", tmp_path, "rigs_old.yaml")

    # No wipe: the same user, so every rig goes through the UPDATE branch.
    assert import_user_from_yaml("default",
                                 str(tmp_path / "config_default.yaml"),
                                 str(old_rigs),
                                 str(tmp_path / "journal_default.yaml"),
                                 clear_existing=False) is True
    db_session.expire_all()
    assert db_session.query(Rig).filter_by(user_id=u.id, rig_name="Packed").one().active is False

    # And with the key present the update branch does apply it.
    with_key = tmp_path / "rigs_on.yaml"
    doc = yaml.safe_load((tmp_path / "rigs_default.yaml").read_text())
    for row in doc["rigs"]:
        if row["rig_name"] == "Packed":
            row["active"] = True
    with_key.write_text(yaml.safe_dump(doc))

    assert import_user_from_yaml("default",
                                 str(tmp_path / "config_default.yaml"),
                                 str(with_key),
                                 str(tmp_path / "journal_default.yaml"),
                                 clear_existing=False) is True
    db_session.expire_all()
    assert db_session.query(Rig).filter_by(user_id=u.id, rig_name="Packed").one().active is True


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


# --- Link UIDs and fidelity (6b part 2) ---------------------------------------

ZERO = {"wrong": 0, "missing": 0}


def _populate_links(db, username):
    """`_populate` plus one journal session, one framing, one project and one view."""
    u = _populate(db, username)
    rig = db.query(Rig).filter_by(user_id=u.id, rig_name="Main").one()

    s = db.query(JournalSession).filter_by(user_id=u.id, external_id=f"{username}_s1").one_or_none()
    if s is None:
        s = JournalSession(user_id=u.id, date_utc=date(2026, 1, 5), external_id=f"{username}_s1",
                           object_name="M42", location_name="Home", rig_id_snapshot=rig.id)
        db.add(s)
        sync_session_links(db, s)
    f = db.query(SavedFraming).filter_by(user_id=u.id, object_name="M42").one_or_none()
    if f is None:
        f = SavedFraming(user_id=u.id, object_name="M42", rig_name="Main", rig_id=rig.id)
        db.add(f)
        sync_framing_links(db, f)
    p = db.query(Project).filter_by(user_id=u.id, name="Orion").one_or_none()
    if p is None:
        p = Project(id=f"{username}_p1", user_id=u.id, name="Orion", target_object_name="M42")
        db.add(p)
        sync_project_link(db, p)
    if db.query(SavedView).filter_by(user_id=u.id, name="Wide").one_or_none() is None:
        db.add(SavedView(user_id=u.id, name="Wide", description="d", is_shared=True,
                         settings_json='{"zoom": 3}'))
    db.commit()
    return u


def _links(db, username):
    """Every linked row's UIDs, keyed by a natural key that survives a re-import."""
    u = _user(db, username)
    return {
        "sessions": {s.external_id: (s.object_record_uid, s.location_record_uid, s.rig_record_uid)
                     for s in db.query(JournalSession).filter_by(user_id=u.id)},
        "framings": {f.object_name: (f.object_record_uid, f.rig_record_uid)
                     for f in db.query(SavedFraming).filter_by(user_id=u.id)},
        "projects": {p.name: p.target_object_record_uid
                     for p in db.query(Project).filter_by(user_id=u.id)},
    }


def _every_session_has_its_rig(db, username):
    u = _user(db, username)
    rigs = {r.record_uid: r.id for r in db.query(Rig).filter_by(user_id=u.id)}
    sessions = db.query(JournalSession).filter_by(user_id=u.id).all()
    assert sessions
    for s in sessions:
        assert s.rig_record_uid, s.external_id
        assert s.rig_id_snapshot == rigs[s.rig_record_uid], s.external_id


def _export(tmp_path, username="default"):
    assert export_user_to_yaml(username, out_dir=str(tmp_path)) is True
    return (tmp_path / f"config_{username}.yaml",
            tmp_path / f"rigs_{username}.yaml",
            tmp_path / f"journal_{username}.yaml")


# --- Round trip: every entry point keeps every link ---------------------------

def test_every_route_and_the_full_import_keep_every_link(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    before_uids, before_links = _uids(db_session, "default"), _links(db_session, "default")
    cfg, rigs, jrn = _export(tmp_path)

    assert _post_file(client, "/import_config", cfg).status_code == 302
    db_session.expire_all()
    assert _links(db_session, "default") == before_links
    assert _post_file(client, "/import_rig_config", rigs).status_code == 302
    db_session.expire_all()
    assert _links(db_session, "default") == before_links
    assert _post_file(client, "/import_journal", jrn).status_code == 302
    db_session.expire_all()
    assert _links(db_session, "default") == before_links
    _every_session_has_its_rig(db_session, "default")

    assert _post_full(client, cfg, rigs, jrn, clear=True).status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "default") == before_uids
    assert _links(db_session, "default") == before_links
    _every_session_has_its_rig(db_session, "default")


def test_the_three_buttons_in_any_order_keep_every_link(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    before_uids, before_links = _uids(db_session, "default"), _links(db_session, "default")
    cfg, rigs, jrn = _export(tmp_path)

    assert _post_file(client, "/import_journal", jrn).status_code == 302
    assert _post_file(client, "/import_rig_config", rigs).status_code == 302
    assert _post_file(client, "/import_config", cfg).status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "default") == before_uids
    assert _links(db_session, "default") == before_links
    _every_session_has_its_rig(db_session, "default")


# --- Files without UIDs: the links survive by name or by the previous UID ------

def test_rigs_without_uids_keep_every_rig_link(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    before_links = _links(db_session, "default")
    cfg, rigs, _jrn = _export(tmp_path)

    stripped = _without_uids(tmp_path, "rigs_old.yaml", rigs)
    assert _post_file(client, "/import_rig_config", stripped).status_code == 302
    db_session.expire_all()
    assert _links(db_session, "default") == before_links
    _every_session_has_its_rig(db_session, "default")


def test_journal_without_uids_keeps_every_rig_link(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    before_links = _links(db_session, "default")
    _cfg, _rigs, jrn = _export(tmp_path)

    stripped = _without_uids(tmp_path, "journal_old.yaml", jrn)
    assert _post_file(client, "/import_journal", stripped).status_code == 302
    db_session.expire_all()
    assert _links(db_session, "default") == before_links
    _every_session_has_its_rig(db_session, "default")


def test_config_without_uids_keeps_every_name_link(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    before_links = _links(db_session, "default")
    cfg, _rigs, _jrn = _export(tmp_path)

    stripped = _without_uids(tmp_path, "config_old.yaml", cfg)
    assert _post_file(client, "/import_config", stripped).status_code == 302
    db_session.expire_all()
    assert _links(db_session, "default") == before_links


# --- 6b part 2: the corrected rules ------------------------------------------

def test_journal_rig_key_null_clears_the_rig_and_absent_restores_it(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    _cfg, _rigs, jrn = _export(tmp_path)
    u = _user(db_session, "default")
    rig = db_session.query(Rig).filter_by(user_id=u.id).one()

    # Key removed (an old-format file): I2 gives the session its previous rig back.
    doc = yaml.safe_load(jrn.read_text())
    del doc["sessions"][0]["rig_record_uid"]
    absent = tmp_path / "jrn_absent.yaml"
    absent.write_text(yaml.safe_dump(doc))
    assert _post_file(client, "/import_journal", absent).status_code == 302
    db_session.expire_all()
    s = db_session.query(JournalSession).filter_by(user_id=u.id).one()
    assert (s.rig_record_uid, s.rig_id_snapshot) == (rig.record_uid, rig.id)

    # Key present, null: the file says the session has no rig.
    doc = yaml.safe_load(jrn.read_text())
    doc["sessions"][0]["rig_record_uid"] = None
    nulled = tmp_path / "jrn_null.yaml"
    nulled.write_text(yaml.safe_dump(doc))
    assert _post_file(client, "/import_journal", nulled).status_code == 302
    db_session.expire_all()
    s = db_session.query(JournalSession).filter_by(user_id=u.id).one()
    assert (s.rig_record_uid, s.rig_id_snapshot) == (None, None)


def test_rig_row_number_in_the_file_is_never_stored(db_session, tmp_path):
    _populate_links(db_session, "default")
    cfg, rigs, jrn = _export(tmp_path)
    other = _user(db_session, "Second")
    assert import_user_from_yaml("Second", str(cfg), str(rigs), str(jrn),
                                 clear_existing=True) is True
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=other.id).one()

    # The file names a real row number, but carries no UID: nothing is stored.
    doc = {"projects": [], "sessions": [
        {"session_id": "rowid_s1", "session_date": "2026-02-01",
         "rig_id_snapshot": rig.id, "rig_name_snapshot": "Main"}]}
    p = tmp_path / "jrn_rowid.yaml"
    p.write_text(yaml.safe_dump(doc))
    assert import_user_from_yaml("Second", str(cfg), str(rigs), str(p),
                                 clear_existing=False) is True
    db_session.expire_all()
    s = db_session.query(JournalSession).filter_by(user_id=other.id, external_id="rowid_s1").one()
    assert (s.rig_record_uid, s.rig_id_snapshot) == (None, None)


def test_journal_before_rigs_fixes_old_columns_to_the_new_row_numbers(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    cfg, rigs, jrn = _export(tmp_path)
    u = _user(db_session, "default")
    old_rig_id = db_session.query(Rig).filter_by(user_id=u.id).one().id

    # Another user's rig, created later, so the re-imported rig gets a NEW row number.
    other = _user(db_session, "Second")
    db_session.add(Rig(user_id=other.id, rig_name="Other Rig"))
    db_session.commit()
    other_rig_id = db_session.query(Rig).filter_by(user_id=other.id).one().id

    assert _post_file(client, "/import_journal", jrn).status_code == 302
    db_session.expire_all()
    s = db_session.query(JournalSession).filter_by(user_id=u.id).one()
    assert s.rig_id_snapshot == old_rig_id  # the rig has not moved yet

    assert _post_file(client, "/import_rig_config", rigs).status_code == 302
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=u.id).one()
    assert rig.id == other_rig_id + 1 and rig.id != old_rig_id
    s = db_session.query(JournalSession).filter_by(user_id=u.id).one()
    f = db_session.query(SavedFraming).filter_by(user_id=u.id).one()
    assert (s.rig_record_uid, s.rig_id_snapshot) == (rig.record_uid, rig.id)
    assert (f.rig_record_uid, f.rig_id) == (rig.record_uid, rig.id)


def test_rig_role_uid_and_row_number_agree_after_the_import(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    rigs = _export(tmp_path)[1]
    assert _post_file(client, "/import_rig_config", rigs).status_code == 302
    db_session.expire_all()
    u = _user(db_session, "default")
    rig = db_session.query(Rig).filter_by(user_id=u.id).one()
    tel = db_session.query(Component).filter_by(user_id=u.id, kind="telescope").one()
    cam = db_session.query(Component).filter_by(user_id=u.id, kind="camera").one()

    assert (rig.telescope_record_uid, rig.telescope_id) == (tel.record_uid, tel.id)
    assert (rig.camera_record_uid, rig.camera_id) == (cam.record_uid, cam.id)
    assert count_link_disagreements(db_session, user_id=u.id, links=LINKS) == {
        link.name: dict(ZERO) for link in LINKS}


# --- Settings, views and session ids survive the round trip (I9) ---------------

def test_saved_views_settings_and_session_ids_survive_the_round_trip(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    u = _user(db_session, "default")
    db_session.query(UiPref).filter_by(user_id=u.id).delete()
    db_session.add(UiPref(user_id=u.id, json_blob=json.dumps({
        "altitude_threshold": 22, "imaging_criteria": {"min_max_altitude": 35},
        "sampling_interval_minutes": 10, "telemetry": False, "rig_sort": "name",
        "language": "de"})))
    db_session.commit()

    # A session with no id: the import gives it one, so a second import updates it.
    cfg, rigs, _jrn = _export(tmp_path)
    first = tmp_path / "jrn_noid.yaml"
    first.write_text(yaml.safe_dump({"projects": [], "sessions": [
        {"session_date": "2026-03-01", "object_name": "M42", "location_name": "Home"}]}))
    assert _post_full(client, cfg, rigs, first, clear=True).status_code == 302
    db_session.expire_all()
    session = db_session.query(JournalSession).filter_by(user_id=u.id).one()
    assert session.external_id

    cfg, rigs, jrn = _export(tmp_path)
    assert _post_full(client, cfg, rigs, jrn, clear=True).status_code == 302
    db_session.expire_all()
    assert db_session.query(JournalSession).filter_by(user_id=u.id).count() == 1

    view = db_session.query(SavedView).filter_by(user_id=u.id).one()
    assert (view.name, view.description, view.is_shared,
            json.loads(view.settings_json)) == ("Wide", "d", True, {"zoom": 3})
    blob = json.loads(db_session.query(UiPref).filter_by(user_id=u.id).one().json_blob)
    assert (blob["altitude_threshold"], blob["sampling_interval_minutes"]) == (22, 10)
    assert blob["imaging_criteria"] == {"min_max_altitude": 35}
    assert blob["telemetry"] is False
    assert blob["rig_sort"] == "name"
    assert blob["language"] == "de"
    for forbidden in ("format_version", "record_uid", "locations", "objects",
                      "saved_framings", "saved_views"):
        assert forbidden not in blob


# --- Another account's files (I8) --------------------------------------------

def test_other_account_journal_links_by_name_and_keeps_the_file_rig_uid(db_session, tmp_path):
    _populate_links(db_session, "default")
    _cfg, _rigs, jrn = _export(tmp_path)
    other = _user(db_session, "Second")
    db_session.add_all([
        Location(user_id=other.id, name="Home", lat=9.0, lon=9.0, timezone="UTC"),
        AstroObject(user_id=other.id, object_name="M42", ra_hours=9.0, dec_deg=9.0)])
    db_session.commit()

    file_rig_uid = yaml.safe_load(jrn.read_text())["sessions"][0]["rig_record_uid"]
    assert file_rig_uid
    _migrate_journal(db_session, other, yaml.safe_load(jrn.read_text()))
    db_session.commit()

    s = db_session.query(JournalSession).filter_by(user_id=other.id).one()
    obj = db_session.query(AstroObject).filter_by(user_id=other.id, object_name="M42").one()
    loc = db_session.query(Location).filter_by(user_id=other.id, name="Home").one()
    assert s.object_record_uid == obj.record_uid
    assert s.location_record_uid == loc.record_uid
    assert s.rig_record_uid == file_rig_uid  # kept exactly, even though it dangles here
    assert s.rig_id_snapshot is None


def test_other_account_full_set_ends_consistent(db_session, tmp_path):
    _populate_links(db_session, "default")
    before_uids = _uids(db_session, "default")
    cfg, rigs, jrn = _export(tmp_path)

    assert import_user_from_yaml("Second", str(cfg), str(rigs), str(jrn),
                                 clear_existing=True) is True
    db_session.expire_all()
    other = _user(db_session, "Second")
    assert _uids(db_session, "Second") == before_uids
    result = count_link_disagreements(db_session, user_id=other.id, links=LINKS)
    assert result == {link.name: dict(ZERO) for link in LINKS}, result
    _every_session_has_its_rig(db_session, "Second")


def test_second_users_links_never_change_on_the_three_buttons(client, db_session, tmp_path):
    _populate_links(db_session, "default")
    _populate_links(db_session, "Second")
    before_uids, before_links = _uids(db_session, "Second"), _links(db_session, "Second")
    cfg, rigs, jrn = _export(tmp_path)

    assert _post_file(client, "/import_config", cfg).status_code == 302
    assert _post_file(client, "/import_rig_config", rigs).status_code == 302
    assert _post_file(client, "/import_journal", jrn).status_code == 302
    db_session.expire_all()
    assert _uids(db_session, "Second") == before_uids
    assert _links(db_session, "Second") == before_links
