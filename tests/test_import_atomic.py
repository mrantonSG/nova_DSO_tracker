"""Diff 6c-1: a user import completes fully or changes nothing (C1, C3, C4, C5).

The four user imports are strict: one bad entry, a UID clash or a newer
format_version refuses the whole file, and every row of the user stays as it
was. The user's own export always imports. The seeding callers are tolerant:
a bad entry is skipped inside a savepoint and the entries around it are kept.
"""

import io
from datetime import date

import pytest
import yaml
from sqlalchemy import inspect

from nova import app, get_or_create_db_user
from nova.migration import (
    ImportRefused, _migrate_locations, _migrate_objects, export_user_to_yaml,
    import_catalog_pack_for_user, import_user_from_yaml, wipe_user_data,
)
from nova.models import (
    AstroObject, Component, DbUser, HorizonPoint, JournalSession, Location, Project, Rig,
    SavedFraming, SavedView, UiPref, UserCustomFilter, session_projects,
)
from nova.record_links import (
    sync_framing_links, sync_project_link, sync_rig_links, sync_session_links,
)


@pytest.fixture(autouse=True)
def outlook_calls(monkeypatch):
    """No background outlook worker (as in tests/test_import_uids.py). The calls
    are recorded, so a test can assert that none started."""
    calls = []
    monkeypatch.setattr("nova.update_outlook_cache", lambda *args, **kwargs: calls.append(args))
    return calls


_MODELS = (Location, AstroObject, Component, Rig, SavedFraming, SavedView,
           JournalSession, Project, UiPref, UserCustomFilter)


def _user(db, username):
    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if u is None:
        u = DbUser(username=username)
        db.add(u)
        db.flush()
    return u


def _populate(db, username):
    """One row of every kind an import touches, linked, with a horizon point,
    a session_projects row, settings and a custom filter."""
    u = _user(db, username)
    loc = Location(user_id=u.id, name="Home", lat=48.2, lon=16.4, timezone="Europe/Vienna")
    loc.horizon_points = [HorizonPoint(az_deg=0.0, alt_min_deg=10.0)]
    # The client fixture already gives "default" an M42
    if db.query(AstroObject).filter_by(user_id=u.id, object_name="M42").one_or_none() is None:
        db.add(AstroObject(user_id=u.id, object_name="M42", ra_hours=5.59, dec_deg=-5.39))
    tel = Component(user_id=u.id, kind="telescope", name="Scope", aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=u.id, kind="camera", name="Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db.add_all([loc, tel, cam])
    db.flush()
    rig = Rig(user_id=u.id, rig_name="Main", telescope_id=tel.id, camera_id=cam.id)
    sync_rig_links(db, rig)
    db.add(rig)
    db.flush()
    project = Project(id=f"{username}_p1", user_id=u.id, name="Orion", target_object_name="M42")
    sync_project_link(db, project)
    session = JournalSession(user_id=u.id, date_utc=date(2026, 1, 5), external_id=f"{username}_s1",
                             object_name="M42", location_name="Home", rig_id_snapshot=rig.id)
    sync_session_links(db, session)
    session.projects = [project]
    framing = SavedFraming(user_id=u.id, object_name="M42", rig_name="Main", rig_id=rig.id)
    sync_framing_links(db, framing)
    db.add_all([project, session, framing,
                SavedView(user_id=u.id, name="Wide", settings_json='{"zoom": 3}'),
                UiPref(user_id=u.id, json_blob='{"altitude_threshold": 20}'),
                UserCustomFilter(user_id=u.id, filter_key="uvir", filter_label="UV/IR")])
    db.commit()
    return u


def _rows(db, model, where):
    keys = [a.key for a in inspect(model).column_attrs]
    return sorted(tuple(str(getattr(r, k)) for k in keys) for r in db.query(model).filter(where))


def _everything(db, username):
    """Every column of every row the user owns, plus horizon points and session_projects."""
    db.expire_all()
    u = _user(db, username)
    snap = {m.__tablename__: _rows(db, m, m.user_id == u.id) for m in _MODELS}
    loc_ids = [i for (i,) in db.query(Location.id).filter_by(user_id=u.id)]
    snap["horizon_points"] = _rows(db, HorizonPoint, HorizonPoint.location_id.in_(loc_ids))
    sess_ids = [i for (i,) in db.query(JournalSession.id).filter_by(user_id=u.id)]
    snap["session_projects"] = sorted(tuple(r) for r in db.execute(
        session_projects.select().where(session_projects.c.session_id.in_(sess_ids))).all())
    return snap


def _orphans(db):
    """Child rows whose parent is gone, and rows of a user that does not exist."""
    db.expire_all()
    users = {i for (i,) in db.query(DbUser.id)}
    locs = {i for (i,) in db.query(Location.id)}
    sessions = {i for (i,) in db.query(JournalSession.id)}
    projects = {i for (i,) in db.query(Project.id)}
    found = {
        "horizon_points": [h.id for h in db.query(HorizonPoint) if h.location_id not in locs],
        "session_projects": [tuple(r) for r in db.execute(session_projects.select()).all()
                             if r.session_id not in sessions or r.project_id not in projects],
    }
    for model in _MODELS:
        found[model.__tablename__] = [r.id for r in db.query(model) if r.user_id not in users]
    return {k: v for k, v in found.items() if v}


def _export(tmp_path, username="default"):
    assert export_user_to_yaml(username, out_dir=str(tmp_path)) is True
    return {k: yaml.safe_load((tmp_path / f"{k}_{username}.yaml").read_text())
            for k in ("config", "rigs", "journal")}


def _file(doc, name):
    return (io.BytesIO(yaml.safe_dump(doc).encode()), name)


def _post(client, route, docs):
    if route == "/tools/import":
        return client.post(route, data={
            "username": "default", "clear_existing": "true",
            "config_file": _file(docs["config"], "c.yaml"),
            "rigs_file": _file(docs["rigs"], "r.yaml"),
            "journal_file": _file(docs["journal"], "j.yaml"),
        }, content_type="multipart/form-data")
    key = {"/import_config": "config", "/import_rig_config": "rigs", "/import_journal": "journal"}[route]
    return client.post(route, data={"file": _file(docs[key], f"{key}.yaml")},
                       content_type="multipart/form-data")


def _flashes(client):
    with client.session_transaction() as sess:
        return " | ".join(message for _category, message in sess.get("_flashes", []))


def _bad_framing(docs):
    docs["config"]["saved_framings"].append({"rig_name": "Main"})  # no object_name: the last step
    return "#2", "the name is missing"


def _bad_rig(docs):
    docs["rigs"]["rigs"].append({"telescope": "Scope", "camera": "Cam"})  # no rig_name
    return "#2", "the name is missing"


def _bad_session(docs):
    docs["journal"]["sessions"].append({"session_id": "bad_s", "object_name": "M42"})
    return "bad_s", "the date is missing"


def _config_uid_clash(docs):
    locs = docs["config"]["locations"]
    locs["Second"] = dict(locs["Home"], record_uid="a" * 32)
    locs["Home"]["record_uid"] = "a" * 32
    return "Second", "its record_uid already belongs to another record"


def _rig_uid_clash(docs):
    main = docs["rigs"]["rigs"][0]
    docs["rigs"]["rigs"].append({"rig_name": "Copy", "telescope": "Scope", "camera": "Cam",
                                 "record_uid": main["record_uid"]})
    return "Copy", "its record_uid already belongs to another record"


# --- C1: one bad entry or a UID clash changes nothing ---------------------------

@pytest.mark.parametrize("route, spoil", [
    ("/import_config", _bad_framing),
    ("/import_rig_config", _bad_rig),
    ("/import_journal", _bad_session),
    ("/tools/import", _bad_session),       # config and rigs are already in when it fails
    ("/import_config", _config_uid_clash),
    ("/import_rig_config", _rig_uid_clash),
    ("/tools/import", _config_uid_clash),
])
def test_bad_entry_refuses_the_file_and_changes_nothing(client, db_session, tmp_path,
                                                        outlook_calls, route, spoil):
    _populate(db_session, "default")
    _populate(db_session, "Second")
    docs = _export(tmp_path)
    name, reason = spoil(docs)
    before, before_other = _everything(db_session, "default"), _everything(db_session, "Second")

    assert _post(client, route, docs).status_code == 302

    message = _flashes(client)
    assert "Import refused" in message and f"'{name}'" in message and reason in message, message
    assert _everything(db_session, "default") == before
    assert _everything(db_session, "Second") == before_other
    assert outlook_calls == []


# --- C5: format_version -----------------------------------------------------------

@pytest.mark.parametrize("route, key", [
    ("/import_config", "config"), ("/import_rig_config", "rigs"),
    ("/import_journal", "journal"), ("/tools/import", "journal"),
])
def test_newer_format_version_is_refused(client, db_session, tmp_path, outlook_calls, route, key):
    _populate(db_session, "default")
    docs = _export(tmp_path)
    docs[key]["format_version"] = 99
    before = _everything(db_session, "default")

    assert _post(client, route, docs).status_code == 302

    assert "format version 99" in _flashes(client)
    assert _everything(db_session, "default") == before
    assert outlook_calls == []


def test_file_without_format_version_imports(client, db_session, tmp_path):
    u = _populate(db_session, "default")
    docs = _export(tmp_path)
    for doc in docs.values():
        doc.pop("format_version")
    home_uid = db_session.query(Location).filter_by(user_id=u.id, name="Home").one().record_uid

    assert _post(client, "/import_config", docs).status_code == 302

    assert "Config imported successfully" in _flashes(client)
    db_session.expire_all()
    assert db_session.query(Location).filter_by(user_id=u.id, name="Home").one().record_uid == home_uid


# --- Correction 3: files that were not provided -------------------------------------

def test_full_import_with_only_the_config_file_works_as_today(client, db_session, tmp_path):
    _populate(db_session, "default")
    docs = _export(tmp_path)
    before = _everything(db_session, "default")

    resp = client.post("/tools/import", data={
        "username": "default", "clear_existing": "true",
        "config_file": _file(docs["config"], "c.yaml"),
    }, content_type="multipart/form-data")

    assert resp.status_code == 302
    assert "Please provide config, rigs, and journal YAML files." in _flashes(client)
    assert _everything(db_session, "default") == before


def test_import_user_from_yaml_skips_files_not_provided(db_session, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("locations:\n  Home: {lat: 48.2, lon: 16.4, timezone: Europe/Vienna}\nobjects: []\n")

    with app.app_context():
        assert import_user_from_yaml("partial_user", str(cfg), None, str(tmp_path / "absent.yaml"))

    u = _user(db_session, "partial_user")
    assert [l.name for l in db_session.query(Location).filter_by(user_id=u.id)] == ["Home"]


def test_import_user_from_yaml_refuses_a_provided_file_it_cannot_read(db_session, tmp_path):
    u = _populate(db_session, "unreadable_user")
    before = _everything(db_session, "unreadable_user")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("locations: [unclosed\n")

    with app.app_context(), pytest.raises(ImportRefused) as refused:
        import_user_from_yaml("unreadable_user", str(cfg), None, None, clear_existing=True)

    assert (refused.value.section, refused.value.label) == ("file", "config")
    assert _everything(db_session, "unreadable_user") == before
    assert db_session.get(DbUser, u.id) is not None


# --- C4: the wipe leaves nothing orphaned and keeps the user row --------------------

def test_full_clear_leaves_no_orphans_and_keeps_the_user_id(client, db_session, tmp_path):
    u = _populate(db_session, "default")
    user_id = u.id
    docs = _export(tmp_path)

    assert _post(client, "/tools/import", docs).status_code == 302

    assert "Import completed successfully" in _flashes(client)
    assert db_session.query(DbUser.id).filter_by(username="default").scalar() == user_id
    assert _orphans(db_session) == {}
    assert db_session.query(SavedFraming).filter_by(user_id=user_id).count() == 1
    assert db_session.query(HorizonPoint).count() == 1


def test_wipe_everything_empties_the_user_and_keeps_the_row(db_session):
    u = _populate(db_session, "default")
    _populate(db_session, "Second")
    other_before = _everything(db_session, "Second")

    wipe_user_data(db_session, u, everything=True)
    db_session.commit()

    assert all(rows == [] for rows in _everything(db_session, "default").values())
    assert db_session.get(DbUser, u.id) is not None
    assert _orphans(db_session) == {}
    assert _everything(db_session, "Second") == other_before


def test_config_import_leaves_no_orphan_horizon_points(client, db_session, tmp_path):
    _populate(db_session, "default")
    docs = _export(tmp_path)

    assert _post(client, "/import_config", docs).status_code == 302

    assert _orphans(db_session) == {}


# --- Correction 1: the user's own export of edge rows always imports ----------------

def _populate_edge_rows(db, username):
    """Valid stored rows at the edges: a rig without a camera, a rig and a
    component with an empty name, a framing with an empty object name, a view
    with empty settings, a location without horizon points and a session with
    only the required fields."""
    u = _user(db, username)
    tel = Component(user_id=u.id, kind="telescope", name="Scope", aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=u.id, kind="camera", name="Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    unnamed = Component(user_id=u.id, kind="telescope", name="", aperture_mm=50, focal_length_mm=250)
    db.add_all([tel, cam, unnamed,
                Location(user_id=u.id, name="Bare", lat=10.0, lon=20.0, timezone="UTC")])
    db.flush()
    no_camera = Rig(user_id=u.id, rig_name="NoCamera", telescope_id=tel.id)
    unnamed_rig = Rig(user_id=u.id, rig_name="", telescope_id=unnamed.id, camera_id=cam.id)
    for rig in (no_camera, unnamed_rig):
        sync_rig_links(db, rig)
        db.add(rig)
    db.flush()
    framing = SavedFraming(user_id=u.id, object_name="", rig_name="NoCamera", rig_id=no_camera.id)
    sync_framing_links(db, framing)
    db.add_all([framing,
                SavedView(user_id=u.id, name="Empty", settings_json="{}"),
                JournalSession(user_id=u.id, date_utc=date(2026, 2, 1))])
    db.commit()
    return u


def _natural(db, username):
    """The edge rows by natural key and UID, so a re-import compares equal."""
    db.expire_all()
    u = _user(db, username)
    return {
        "locations": sorted((l.name, l.lat, l.lon, l.timezone, l.record_uid, len(l.horizon_points))
                            for l in db.query(Location).filter_by(user_id=u.id)),
        "components": sorted((c.kind, c.name, c.record_uid)
                             for c in db.query(Component).filter_by(user_id=u.id)),
        "rigs": sorted((r.rig_name, r.record_uid, r.telescope_record_uid, r.camera_record_uid)
                       for r in db.query(Rig).filter_by(user_id=u.id)),
        "framings": sorted((f.object_name, f.rig_record_uid)
                           for f in db.query(SavedFraming).filter_by(user_id=u.id)),
        "views": sorted((v.name, v.settings_json) for v in db.query(SavedView).filter_by(user_id=u.id)),
        "sessions": sorted((str(s.date_utc), s.object_name, s.location_name, s.rig_record_uid)
                           for s in db.query(JournalSession).filter_by(user_id=u.id)),
    }


def test_own_export_of_edge_rows_imports_strictly_and_brings_every_row_back(db_session, tmp_path):
    _populate_edge_rows(db_session, "edge_user")
    before = _natural(db_session, "edge_user")
    assert export_user_to_yaml("edge_user", out_dir=str(tmp_path)) is True

    with app.app_context():
        assert import_user_from_yaml("edge_user",
                                     str(tmp_path / "config_edge_user.yaml"),
                                     str(tmp_path / "rigs_edge_user.yaml"),
                                     str(tmp_path / "journal_edge_user.yaml"),
                                     clear_existing=True) is True

    assert _natural(db_session, "edge_user") == before
    u = _user(db_session, "edge_user")
    no_camera = db_session.query(Rig).filter_by(user_id=u.id, rig_name="NoCamera").one()
    assert no_camera.telescope_id is not None and no_camera.camera_id is None


# --- C3: tolerant callers skip a bad entry and keep the rest -----------------------

SEED_CONFIG = """
default_location: Home
locations:
  Home: {lat: 48.2, lon: 16.4, timezone: Europe/Vienna}
objects:
  - {Object: M31, RA: 0.71, DEC: 41.27}
  - {Object: M42, RA: not-a-number, DEC: -5.39}
  - {Object: M45, RA: 3.79, DEC: 24.12}
"""
SEED_JOURNAL = """
projects: []
sessions:
  - {session_id: seed_s1, session_date: '2026-01-10', object_name: M31}
  - {session_id: seed_s2, session_date: '2026-01-11', project_ids: 5}
  - {session_id: seed_s3, session_date: '2026-01-12', object_name: M45}
"""


def _assert_seeded_around_the_bad_entries(db, user_id):
    db.expire_all()
    assert [l.name for l in db.query(Location).filter_by(user_id=user_id)] == ["Home"]
    assert sorted(o.object_name for o in db.query(AstroObject).filter_by(user_id=user_id)) == ["M31", "M45"]
    assert sorted(s.external_id for s in db.query(JournalSession).filter_by(user_id=user_id)) == \
        ["seed_s1", "seed_s3"]


def test_tolerant_bad_object_keeps_the_entries_before_and_after(db_session):
    u = _user(db_session, "tolerant_user")
    config = yaml.safe_load(SEED_CONFIG)
    with app.app_context():
        _migrate_locations(db_session, u, config)
        _migrate_objects(db_session, u, config)
    db_session.commit()

    db_session.expire_all()
    assert db_session.query(Location).filter_by(user_id=u.id).count() == 1  # not rolled back
    assert sorted(o.object_name for o in db_session.query(AstroObject).filter_by(user_id=u.id)) == \
        ["M31", "M45"]


def test_tolerant_import_then_rollback_leaves_nothing_behind(db_session):
    """A released savepoint must not commit: pysqlite would, if the savepoint
    opened the transaction (see _open_transaction)."""
    u = _user(db_session, "rollback_user")
    db_session.commit()

    with app.app_context():
        _migrate_objects(db_session, u, {"objects": [{"Object": "M31", "RA": 0.71, "DEC": 41.27}]})
    db_session.rollback()
    import_catalog_pack_for_user(db_session, u, {"objects": [{"Object": "M45", "RA": 3.79, "DEC": 24.12}]}, "p")
    db_session.rollback()

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=u.id).count() == 0


def test_guest_repair_seed_skips_bad_entries(db_session, tmp_path, monkeypatch):
    (tmp_path / "config_guest_user.yaml").write_text(SEED_CONFIG)
    (tmp_path / "journal_guest_user.yaml").write_text(SEED_JOURNAL)
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))

    with app.app_context():
        guest = get_or_create_db_user(db_session, "guest_user")  # no locations: repair path

    _assert_seeded_around_the_bad_entries(db_session, guest.id)


def test_new_user_seed_skips_bad_entries_and_keeps_the_user(db_session, tmp_path, monkeypatch):
    (tmp_path / "config_default.yaml").write_text(SEED_CONFIG)
    (tmp_path / "journal_default.yaml").write_text(SEED_JOURNAL)
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("nova._is_test_environment", lambda: False)

    with app.app_context():
        user = get_or_create_db_user(db_session, "newbie")

    assert user is not None
    _assert_seeded_around_the_bad_entries(db_session, user.id)


def test_catalog_pack_skips_bad_entry(db_session):
    u = _user(db_session, "catalog_user")
    pack = {"objects": [{"Object": "M31", "RA": 0.71, "DEC": 41.27},
                        {"Object": "M42", "RA": "not-a-number", "DEC": -5.39},
                        {"Object": "M45", "RA": 3.79, "DEC": 24.12}]}
    created, _enriched, skipped, _conflicts = import_catalog_pack_for_user(db_session, u, pack, "p")
    db_session.commit()

    assert (created, skipped) == (2, 1)
    assert sorted(o.object_name for o in db_session.query(AstroObject).filter_by(user_id=u.id)) == \
        ["M31", "M45"]
