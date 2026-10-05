"""
YAML import writes set the record_uid links (diff 6, nova/migration.py).

- Saved framings: the rig UID follows the rig the import (or
  _heal_saved_framings) resolved by name for the same user; the object UID is
  found by exact name.
- Journal sessions: the rig UID is never resolved on import. A new session
  gets NULL; an existing session keeps its rig UID while rig_id_snapshot stays
  the same and gets NULL when the import changes it. Object and location UIDs
  are found by exact name from the row's final names.
- Projects: the target object UID follows the row's final target_object_name.
The old columns are written exactly as before.
"""
from datetime import date

import pytest

from nova import app
from nova.migration import (
    _heal_saved_framings, _migrate_journal, _migrate_saved_framings, import_user_from_yaml,
)
from nova.models import AstroObject, Component, DbUser, JournalSession, Location, Project, Rig, SavedFraming
from nova.record_links import NAME_LINKS, count_link_disagreements, sync_rig_links


ZERO = {"wrong": 0, "missing": 0}


def _rig(db, user, tag):
    tel = Component(user_id=user.id, kind="telescope", name=f"{tag} Scope", aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=user.id, kind="camera", name=f"{tag} Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db.add_all([tel, cam])
    db.flush()
    rig = Rig(user_id=user.id, rig_name=f"{tag} Rig", telescope_id=tel.id, camera_id=cam.id)
    sync_rig_links(db, rig)
    db.add(rig)
    db.flush()
    return rig


@pytest.fixture
def w(db_session):
    """User A: objects M42 and M31, location Home, rigs A1 and A2. User B: M42, Home and rig B."""
    db = db_session
    a, b = DbUser(username="yaml_links_a"), DbUser(username="yaml_links_b")
    db.add_all([a, b])
    db.flush()
    m42 = AstroObject(user_id=a.id, object_name="M42", ra_hours=5.6, dec_deg=-5.4)
    m31 = AstroObject(user_id=a.id, object_name="M31", ra_hours=0.7, dec_deg=41.3)
    b_m42 = AstroObject(user_id=b.id, object_name="M42", ra_hours=5.6, dec_deg=-5.4)
    home = Location(user_id=a.id, name="Home", lat=48.2, lon=16.4, timezone="Europe/Vienna")
    b_home = Location(user_id=b.id, name="Home", lat=48.2, lon=16.4, timezone="Europe/Vienna")
    db.add_all([m42, m31, b_m42, home, b_home])
    db.flush()
    rig1, rig2, rig_b = _rig(db, a, "A1"), _rig(db, a, "A2"), _rig(db, b, "B")
    db.commit()

    class W:
        pass
    world = W()
    world.db, world.a, world.b = db, a, b
    world.m42, world.m31, world.b_m42, world.home, world.b_home = m42, m31, b_m42, home, b_home
    world.rig1, world.rig2, world.rig_b = rig1, rig2, rig_b
    return world


# --- Saved framings import ----------------------------------------------------

def _import_framings(w, *rows):
    _migrate_saved_framings(w.db, w.a, {"saved_framings": list(rows)})
    w.db.commit()


def _framing(w, obj):
    w.db.expire_all()
    return w.db.query(SavedFraming).filter_by(user_id=w.a.id, object_name=obj).one()


def test_framings_import_rig_resolved(w):
    _import_framings(w, {"object_name": "M42", "rig_name": "A1 Rig"})
    f = _framing(w, "M42")

    assert (f.rig_id, f.rig_name) == (w.rig1.id, "A1 Rig")
    assert (f.rig_record_uid, f.object_record_uid) == (w.rig1.record_uid, w.m42.record_uid)


def test_framings_import_update_follows_newly_resolved_rig(w):
    _import_framings(w, {"object_name": "M42", "rig_name": "A1 Rig"})
    _import_framings(w, {"object_name": "M42", "rig_name": "A2 Rig"})
    f = _framing(w, "M42")

    assert (f.rig_id, f.rig_record_uid) == (w.rig2.id, w.rig2.record_uid)


@pytest.mark.parametrize("rig_name", ["Missing Rig", "B Rig", "a1 rig", None])
def test_framings_import_rig_not_resolved(w, rig_name):
    _import_framings(w, {"object_name": "M42", "rig_name": "A1 Rig"})
    _import_framings(w, {"object_name": "M42", "rig_name": rig_name})
    f = _framing(w, "M42")

    assert (f.rig_id, f.rig_name) == (None, rig_name)
    assert f.rig_record_uid is None
    assert f.object_record_uid == w.m42.record_uid


@pytest.mark.parametrize("obj", ["NGC 9999", "m42", "M 42"])
def test_framings_import_object_no_match(w, obj):
    _import_framings(w, {"object_name": obj})
    f = _framing(w, obj)

    assert f.object_record_uid is None


def test_framings_import_same_object_name_in_two_users_links_own(w):
    # uq_user_object forbids two same-named objects within one user, so the
    # ambiguous case that can be built here is the same name across users.
    _import_framings(w, {"object_name": "M42"})
    f = _framing(w, "M42")

    assert f.object_record_uid == w.m42.record_uid
    assert f.object_record_uid != w.b_m42.record_uid


def test_heal_saved_framings_sets_uid_of_resolved_rig(w):
    healed = SavedFraming(user_id=w.a.id, object_name="M31", rig_name="A2 Rig")
    unresolved = SavedFraming(user_id=w.a.id, object_name="M42", rig_name="B Rig")
    w.db.add_all([healed, unresolved])
    w.db.commit()

    _heal_saved_framings(w.db, w.a)
    w.db.commit()

    assert (healed.rig_id, healed.rig_record_uid) == (w.rig2.id, w.rig2.record_uid)
    assert (unresolved.rig_id, unresolved.rig_record_uid) == (None, None)


# --- Journal import: sessions -------------------------------------------------

def _import_journal(w, sessions=(), projects=()):
    _migrate_journal(w.db, w.a, {"projects": list(projects), "sessions": list(sessions)})
    w.db.commit()


def _session(w, ext_id):
    w.db.expire_all()
    return w.db.query(JournalSession).filter_by(user_id=w.a.id, external_id=ext_id).one()


def _existing_session(w, rig, **cols):
    """A session already linked to `rig`; its rig UID is set explicitly, not resolved."""
    s = JournalSession(user_id=w.a.id, date_utc=date(2026, 1, 1), external_id="s1",
                       object_name="M31", location_name="Home",
                       rig_id_snapshot=rig.id, rig_record_uid=rig.record_uid, **cols)
    w.db.add(s)
    w.db.commit()
    return s


@pytest.mark.parametrize("ext_id", ["s1", None], ids=["with_external_id", "without_external_id"])
def test_journal_import_new_session_has_no_rig_uid(w, ext_id):
    _import_journal(w, [{"session_id": ext_id, "session_date": "2026-01-10",
                         "object_name": "M42", "location_name": "Home",
                         "rig_id_snapshot": w.rig1.id, "rig_name_snapshot": "A1 Rig"}])
    s = _session(w, ext_id)

    assert s.rig_id_snapshot == w.rig1.id  # written as before
    assert s.rig_record_uid is None
    assert (s.object_record_uid, s.location_record_uid) == (w.m42.record_uid, w.home.record_uid)


@pytest.mark.parametrize("yaml_rig", ["same", "absent"])
def test_journal_import_unchanged_rig_snapshot_keeps_rig_uid(w, yaml_rig):
    _existing_session(w, w.rig1)
    row = {"session_id": "s1", "session_date": "2026-01-10"}
    if yaml_rig == "same":
        row["rig_id_snapshot"] = w.rig1.id
    _import_journal(w, [row])
    s = _session(w, "s1")

    assert s.rig_id_snapshot == w.rig1.id
    assert s.rig_record_uid == w.rig1.record_uid


@pytest.mark.parametrize("new_rig", ["rig2", "rig_b", "unknown"])
def test_journal_import_changed_rig_snapshot_gives_null(w, new_rig):
    _existing_session(w, w.rig1)
    new_id = {"rig2": w.rig2.id, "rig_b": w.rig_b.id, "unknown": 999999}[new_rig]
    _import_journal(w, [{"session_id": "s1", "session_date": "2026-01-10", "rig_id_snapshot": new_id}])
    s = _session(w, "s1")

    assert s.rig_id_snapshot == new_id  # written as before
    assert s.rig_record_uid is None


def test_journal_import_object_and_location_by_exact_name(w):
    _existing_session(w, w.rig1)
    _import_journal(w, [{"session_id": "s1", "session_date": "2026-01-10",
                         "object_name": "m42", "location_name": "Home"}])
    s = _session(w, "s1")

    assert s.object_name == "M42"  # final, normalized value
    assert (s.object_record_uid, s.location_record_uid) == (w.m42.record_uid, w.home.record_uid)
    assert s.object_record_uid != w.b_m42.record_uid
    assert s.location_record_uid != w.b_home.record_uid


def test_journal_import_absent_names_use_final_values(w):
    _existing_session(w, w.rig1)
    _import_journal(w, [{"session_id": "s1", "session_date": "2026-01-10"}])
    s = _session(w, "s1")

    assert (s.object_name, s.location_name) == ("M31", "Home")
    assert (s.object_record_uid, s.location_record_uid) == (w.m31.record_uid, w.home.record_uid)


@pytest.mark.parametrize("obj,loc", [("NGC 9999", "Nowhere"), ("M42", "home"), ("M42", "Home ")])
def test_journal_import_unknown_name_gives_null(w, obj, loc):
    _existing_session(w, w.rig1, object_record_uid=w.m31.record_uid, location_record_uid=w.home.record_uid)
    _import_journal(w, [{"session_id": "s1", "session_date": "2026-01-10",
                         "object_name": obj, "location_name": loc}])
    s = _session(w, "s1")

    expected_obj = w.m42.record_uid if obj == "M42" else None
    assert s.object_record_uid == expected_obj
    assert s.location_record_uid is None


# --- Journal import: projects -------------------------------------------------

def _project(w, project_id):
    w.db.expire_all()
    return w.db.get(Project, project_id)


def test_projects_import_insert(w):
    _import_journal(w, projects=[{"project_id": "p1", "project_name": "Orion", "target_object_id": "M42"}])
    p = _project(w, "p1")

    assert p.target_object_name == "M42"
    assert p.target_object_record_uid == w.m42.record_uid


def test_projects_import_insert_unknown_target(w):
    _import_journal(w, projects=[{"project_id": "p1", "project_name": "X", "target_object_id": "NGC 9999"}])

    assert _project(w, "p1").target_object_record_uid is None


def test_projects_import_update(w):
    _import_journal(w, projects=[{"project_id": "p1", "project_name": "P", "target_object_id": "M31"}])
    _import_journal(w, projects=[{"project_id": "p1", "project_name": "P", "target_object_id": "M42"}])
    p = _project(w, "p1")

    assert (p.target_object_name, p.target_object_record_uid) == ("M42", w.m42.record_uid)


def test_projects_import_update_with_none_keeps_final_value_and_uid(w):
    _import_journal(w, projects=[{"project_id": "p1", "project_name": "P", "target_object_id": "M31"}])
    _import_journal(w, projects=[{"project_id": "p1", "project_name": "P"}])
    p = _project(w, "p1")

    assert (p.target_object_name, p.target_object_record_uid) == ("M31", w.m31.record_uid)


def test_projects_import_copy_of_other_users_id_links_own_object(w):
    w.db.add(Project(id="p_b", user_id=w.b.id, name="B's", target_object_name="M42"))
    w.db.commit()
    _import_journal(w, projects=[{"project_id": "p_b", "project_name": "Mine", "target_object_id": "M42"}])
    w.db.expire_all()
    copy = w.db.query(Project).filter_by(user_id=w.a.id, name="Mine").one()

    assert copy.id != "p_b"
    assert copy.target_object_record_uid == w.m42.record_uid


# --- import_user_from_yaml ----------------------------------------------------

CONFIG_YAML = """
default_location: Home
locations:
  Home: {lat: 48.2, lon: 16.4, timezone: Europe/Vienna}
objects:
  - {Object: M42, RA: 5.59, DEC: -5.39, Constellation: Ori}
  - {Object: M31, RA: 0.71, DEC: 41.27, Constellation: And}
saved_framings:
  - {object_name: M42, rig_name: Missing Rig}
  - {object_name: NGC 9999}
"""
RIGS_YAML = "components: {}\nrigs: []\n"
JOURNAL_YAML = """
projects:
  - {project_id: imp_p1, project_name: Orion, target_object_id: M42}
  - {project_id: imp_p2, project_name: Nowhere, target_object_id: NGC 9999}
sessions:
  - {session_id: imp_s1, session_date: '2026-01-10', object_name: M42, location_name: Home}
  - {session_id: imp_s2, session_date: '2026-01-11', object_name: M31, location_name: Elsewhere}
"""


@pytest.mark.parametrize("clear", [False, True], ids=["upsert", "clear"])
def test_import_user_from_yaml_leaves_name_links_clean(db_session, tmp_path, clear):
    paths = []
    for name, content in (("cfg.yaml", CONFIG_YAML), ("rigs.yaml", RIGS_YAML), ("jrn.yaml", JOURNAL_YAML)):
        path = tmp_path / name
        path.write_text(content)
        paths.append(str(path))

    with app.app_context():
        assert import_user_from_yaml("yaml_import_user", *paths, clear_existing=False)
        assert import_user_from_yaml("yaml_import_user", *paths, clear_existing=clear)

    db = db_session
    db.expire_all()
    user = db.query(DbUser).filter_by(username="yaml_import_user").one()
    result = count_link_disagreements(db, user_id=user.id, links=NAME_LINKS)
    assert result == {link.name: ZERO for link in NAME_LINKS}, result

    m42 = db.query(AstroObject).filter_by(user_id=user.id, object_name="M42").one()
    home = db.query(Location).filter_by(user_id=user.id, name="Home").one()
    s1 = db.query(JournalSession).filter_by(user_id=user.id, external_id="imp_s1").one()
    assert (s1.object_record_uid, s1.location_record_uid, s1.rig_record_uid) == (m42.record_uid, home.record_uid, None)
    assert db.get(Project, "imp_p1").target_object_record_uid == m42.record_uid
    framing = db.query(SavedFraming).filter_by(user_id=user.id, object_name="M42").one()
    assert (framing.object_record_uid, framing.rig_record_uid) == (m42.record_uid, None)
