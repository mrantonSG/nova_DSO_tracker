"""
Bulk write paths keep the record_uid links in step (step 5 part 1, diff 7).

- /import_config recreates the user's objects and locations, then re-links the
  user's sessions and projects by exact name (resync_user_links).
- /import_rig_config, seed-guest-account and reset-guest-from-template never
  re-link sessions or framings they did not touch to rigs.
- /import_journal: _migrate_journal sets the UIDs (diff 6); the route adds nothing.
- merge_objects, repair-corrupt-ids and repair_journals: a rewritten
  object_name / target_object_name takes its UID through the sync helpers.
  A framing moved by merge_objects gets only its object UID; its rig UID stays.
- purge_user_app_data: an old link it sets to NULL on another user's row
  clears that row's UID too.
A second user's rows are never changed.
"""
import io
from datetime import date
from types import SimpleNamespace

import pytest
import yaml

from nova import app
from nova.helpers import purge_user_app_data
from nova.migration import repair_journals
from nova.models import AstroObject, Component, DbUser, JournalSession, Location, Project, Rig, SavedFraming
from nova.record_links import (
    LINKS, NAME_LINKS, count_link_disagreements,
    sync_framing_links, sync_project_link, sync_rig_links, sync_session_links,
)


ZERO = {"wrong": 0, "missing": 0}
_LINKED_MODELS = (Rig, JournalSession, SavedFraming, Project)

RIGS_YAML = {
    "components": {
        "telescopes": [{"id": "t1", "name": "Imp Scope", "aperture_mm": 80, "focal_length_mm": 480}],
        "cameras": [{"id": "c1", "name": "Imp Cam", "sensor_width_mm": 23.5,
                     "sensor_height_mm": 15.6, "pixel_size_um": 3.76}],
        "reducers_extenders": [],
    },
    "rigs": [{"rig_name": "Imp Rig", "telescope_id": "t1", "camera_id": "c1"}],
}


def _user(db, username):
    user = DbUser(username=username)
    db.add(user)
    db.commit()
    return user.id


def _populate(db, user_id, tag):
    """Objects M42 and M31, location Home, rig '<tag> Rig', and a session, project and framing on M42.

    Returns plain values: the CLI commands close the session, which detaches the rows.
    """
    m42 = AstroObject(user_id=user_id, object_name="M42", ra_hours=5.6, dec_deg=-5.4)
    m31 = AstroObject(user_id=user_id, object_name="M31", ra_hours=0.7, dec_deg=41.3)
    home = Location(user_id=user_id, name="Home", lat=48.2, lon=16.4, timezone="Europe/Vienna")
    tel = Component(user_id=user_id, kind="telescope", name=f"{tag} Scope", aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=user_id, kind="camera", name=f"{tag} Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db.add_all([m42, m31, home, tel, cam])
    db.flush()
    rig = Rig(user_id=user_id, rig_name=f"{tag} Rig", telescope_id=tel.id, camera_id=cam.id)
    sync_rig_links(db, rig)
    db.add(rig)
    db.flush()
    session = JournalSession(user_id=user_id, date_utc=date(2026, 1, 1), object_name="M42",
                             location_name="Home", rig_id_snapshot=rig.id)
    project = Project(id=f"{tag}_p1", user_id=user_id, name=f"{tag} Orion", target_object_name="M42")
    framing = SavedFraming(user_id=user_id, object_name="M42", rig_id=rig.id, rig_name=rig.rig_name)
    sync_session_links(db, session)
    sync_project_link(db, project)
    sync_framing_links(db, framing)
    db.add_all([session, project, framing])
    db.commit()
    return SimpleNamespace(
        user_id=user_id, m42_uid=m42.record_uid, m31_uid=m31.record_uid, home_uid=home.record_uid,
        tel_id=tel.id, tel_uid=tel.record_uid, cam_id=cam.id, cam_uid=cam.record_uid,
        rig_id=rig.id, rig_uid=rig.record_uid,
        session_id=session.id, project_id=project.id, framing_id=framing.id,
    )


def _snapshot(db, user_id, models=_LINKED_MODELS):
    """Every old link column and UID column of the user's linked rows, keyed by table."""
    db.expire_all()
    snap = {}
    for model in models:
        cols = [c for link in LINKS if link.table == model.__tablename__ for c in (link.old_col, link.uid_col)]
        snap[model.__tablename__] = sorted(
            (str(row.id),) + tuple(getattr(row, c) for c in cols)
            for row in db.query(model).filter_by(user_id=user_id)
        )
    return snap


def _assert_name_links_clean(db, user_id):
    db.expire_all()
    result = count_link_disagreements(db, user_id=user_id, links=NAME_LINKS)
    assert result == {link.name: ZERO for link in NAME_LINKS}, result


@pytest.fixture
def mu(multi_user_client, monkeypatch):
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    client, ids = multi_user_client
    return client, ids["user_a_id"], ids["user_b_id"]


# --- /import_config -------------------------------------------------------------

CONFIG_YAML = """
altitude_threshold: 20
default_location: Home
imaging_criteria:
    max_moon_illumination: 20
    min_angular_distance: 30
    min_max_altitude: 30
    min_observable_minutes: 60
    search_horizon_months: 6
locations:
    Home: {lat: 48.2, lon: 16.4, timezone: Europe/Vienna, active: true}
objects:
    - {Object: M42, RA: 5.59, DEC: -5.39, Constellation: Ori}
    - {Object: M45, RA: 3.79, DEC: 24.12, Constellation: Tau}
saved_framings:
    - {object_name: M42, rig_name: A Rig}
"""


def test_config_import_relinks_sessions_and_projects_by_exact_name(mu, db_session, monkeypatch):
    client, a_id, b_id = mu
    monkeypatch.setattr('nova.update_outlook_cache', lambda *args, **kwargs: None)
    a = _populate(db_session, a_id, "A")
    _populate(db_session, b_id, "B")
    # Nothing is named M45 or M99 yet: the import creates M45, not M99.
    extra = [JournalSession(user_id=a_id, date_utc=date(2026, 1, 2), object_name=name, location_name="Home")
             for name in ("M45", "M99")]
    for s in extra:
        sync_session_links(db_session, s)
    db_session.add_all(extra)
    db_session.commit()
    before_b = _snapshot(db_session, b_id)

    resp = client.post('/import_config', data={'file': (io.BytesIO(CONFIG_YAML.encode()), 'config.yaml')},
                       content_type='multipart/form-data')
    assert resp.status_code == 302

    db_session.expire_all()
    m42 = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    m45 = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M45").one()
    home = db_session.query(Location).filter_by(user_id=a_id, name="Home").one()
    assert (m42.record_uid, home.record_uid) != (a.m42_uid, a.home_uid)  # recreated by the import

    s = db_session.get(JournalSession, a.session_id)
    assert (s.object_name, s.location_name, s.rig_id_snapshot) == ("M42", "Home", a.rig_id)  # old columns as before
    assert (s.object_record_uid, s.location_record_uid) == (m42.record_uid, home.record_uid)
    assert s.rig_record_uid == a.rig_uid  # rig links are never re-linked in bulk
    assert db_session.get(Project, a.project_id).target_object_record_uid == m42.record_uid
    by_name = {row.object_name: row for row in db_session.query(JournalSession).filter_by(user_id=a_id)}
    assert by_name["M45"].object_record_uid == m45.record_uid
    assert by_name["M99"].object_record_uid is None
    framing = db_session.query(SavedFraming).filter_by(user_id=a_id, object_name="M42").one()
    assert (framing.object_record_uid, framing.rig_id, framing.rig_record_uid) == (m42.record_uid, a.rig_id, a.rig_uid)

    _assert_name_links_clean(db_session, a_id)
    assert _snapshot(db_session, b_id) == before_b


# --- /import_journal ------------------------------------------------------------

def test_journal_import_route_sets_name_uids_and_no_rig_uid(mu, db_session):
    client, a_id, b_id = mu
    a = _populate(db_session, a_id, "A")
    _populate(db_session, b_id, "B")
    before_b = _snapshot(db_session, b_id)
    journal = {
        "projects": [{"project_id": "jr_p1", "project_name": "Imported", "target_object_id": "M31"}],
        "sessions": [{"session_id": "jr_s1", "session_date": "2026-01-10", "object_name": "M31",
                      "location_name": "Home", "rig_id_snapshot": a.rig_id}],
    }

    resp = client.post('/import_journal', data={'file': (io.BytesIO(yaml.safe_dump(journal).encode()), 'journal.yaml')},
                       content_type='multipart/form-data')
    assert resp.status_code == 302

    db_session.expire_all()
    s = db_session.query(JournalSession).filter_by(user_id=a_id).one()  # the old session was wiped
    assert (s.external_id, s.rig_id_snapshot) == ("jr_s1", a.rig_id)
    assert (s.object_record_uid, s.location_record_uid, s.rig_record_uid) == (a.m31_uid, a.home_uid, None)
    assert db_session.query(Project).filter_by(user_id=a_id).one().target_object_record_uid == a.m31_uid
    _assert_name_links_clean(db_session, a_id)
    assert _snapshot(db_session, b_id) == before_b


# --- /import_rig_config ---------------------------------------------------------

def test_rig_import_never_relinks_untouched_sessions_and_framings(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, a_id, "A")
    _populate(db_session, b_id, "B")
    before_a = _snapshot(db_session, a_id, models=(JournalSession, SavedFraming))
    before_b = _snapshot(db_session, b_id)

    resp = client.post('/import_rig_config',
                       data={'file': (io.BytesIO(yaml.safe_dump(RIGS_YAML).encode()), 'rigs.yaml')})
    assert resp.status_code == 302

    db_session.expire_all()
    assert db_session.query(Rig).filter_by(user_id=a_id).one().rig_name == "Imp Rig"  # the import ran
    assert _snapshot(db_session, a_id, models=(JournalSession, SavedFraming)) == before_a
    assert _snapshot(db_session, b_id) == before_b


# --- merge_objects --------------------------------------------------------------

def _add_merge_source(db, user_id, tag, rig_id):
    """Object NGC 1976 with a session, a project and a framing on it; returns its UID."""
    src = AstroObject(user_id=user_id, object_name="NGC 1976", ra_hours=5.6, dec_deg=-5.4)
    db.add(src)
    db.flush()
    session = JournalSession(user_id=user_id, date_utc=date(2026, 1, 3), object_name="NGC 1976",
                             location_name="Home", rig_id_snapshot=rig_id)
    project = Project(id=f"{tag}_p2", user_id=user_id, name=f"{tag} Nebula", target_object_name="NGC 1976")
    framing = SavedFraming(user_id=user_id, object_name="NGC 1976", rig_id=rig_id)
    sync_session_links(db, session)
    sync_project_link(db, project)
    sync_framing_links(db, framing)
    db.add_all([session, project, framing])
    db.commit()
    return src.record_uid


@pytest.mark.parametrize("keep", ["M31", "M42"], ids=["framing_moved", "framing_conflict"])
def test_merge_objects_uids_follow_kept_object(mu, db_session, keep):
    client, a_id, b_id = mu
    a = _populate(db_session, a_id, "A")
    b = _populate(db_session, b_id, "B")
    _add_merge_source(db_session, a_id, "A", a.rig_id)
    _add_merge_source(db_session, b_id, "B", b.rig_id)
    before_b = _snapshot(db_session, b_id)

    resp = client.post('/api/merge_objects', json={"keep_id": keep, "merge_id": "NGC 1976"})
    assert resp.status_code == 200, resp.get_json()

    db_session.expire_all()
    keep_uid = db_session.query(AstroObject).filter_by(user_id=a_id, object_name=keep).one().record_uid
    sessions = db_session.query(JournalSession).filter_by(user_id=a_id, object_name=keep).all()
    assert sessions and all(s.object_record_uid == keep_uid for s in sessions)
    assert all(s.rig_record_uid == a.rig_uid for s in sessions)
    project = db_session.get(Project, "A_p2")
    assert (project.target_object_name, project.target_object_record_uid) == (keep, keep_uid)
    framing = db_session.query(SavedFraming).filter_by(user_id=a_id, object_name=keep).one()
    assert (framing.object_record_uid, framing.rig_record_uid) == (keep_uid, a.rig_uid)

    _assert_name_links_clean(db_session, a_id)
    assert _snapshot(db_session, b_id) == before_b


def test_merge_objects_moved_framing_keeps_its_rig_uid(mu, db_session):
    """Only the object UID moves; a rig UID that disagrees with rig_id is left as it was."""
    client, a_id, _b_id = mu
    a = _populate(db_session, a_id, "A")
    _add_merge_source(db_session, a_id, "A", a.rig_id)
    framing = db_session.query(SavedFraming).filter_by(user_id=a_id, object_name="NGC 1976").one()
    framing.rig_record_uid = "stale-rig-uid"
    db_session.commit()
    framing_id = framing.id

    resp = client.post('/api/merge_objects', json={"keep_id": "M31", "merge_id": "NGC 1976"})
    assert resp.status_code == 200, resp.get_json()

    db_session.expire_all()
    framing = db_session.get(SavedFraming, framing_id)
    assert (framing.object_name, framing.object_record_uid) == ("M31", a.m31_uid)
    assert (framing.rig_id, framing.rig_record_uid) == (a.rig_id, "stale-rig-uid")


# --- repair-corrupt-ids ---------------------------------------------------------

def test_repair_corrupt_ids_session_uids_follow_rewritten_names(db_session):
    db = db_session
    a_id = _user(db, "uid_repair_a")
    b_id = _user(db, "uid_repair_b")
    _populate(db, b_id, "B")
    # Stored corrupt on purpose: the command exists to rewrite exactly these names.
    renamed = AstroObject(user_id=a_id, object_name="SH2129", ra_hours=1, dec_deg=1)
    corrupt = AstroObject(user_id=a_id, object_name="NGC1976", ra_hours=5.6, dec_deg=-5.4)
    correct = AstroObject(user_id=a_id, object_name="NGC 1976", ra_hours=5.6, dec_deg=-5.4)
    db.add_all([renamed, corrupt, correct])
    db.flush()
    sessions = {name: JournalSession(user_id=a_id, date_utc=date(2026, 1, 1), object_name=name)
                for name in ("SH2129", "NGC1976", "NGC 1976")}
    for s in sessions.values():
        sync_session_links(db, s)
    unlinked = JournalSession(user_id=a_id, date_utc=date(2026, 1, 2), object_name="SH2129")  # UID left empty
    db.add_all([*sessions.values(), unlinked])
    db.commit()
    renamed_uid, correct_uid = renamed.record_uid, correct.record_uid
    ids = {name: s.id for name, s in sessions.items()}
    unlinked_id = unlinked.id
    before_b = _snapshot(db, b_id)

    result = app.test_cli_runner().invoke(args=["repair-corrupt-ids"])
    assert result.exit_code == 0, result.output
    assert "REPAIR COMPLETE" in result.output

    db.expire_all()
    got = {name: db.get(JournalSession, sid) for name, sid in ids.items()}
    # Rename path: same object row, so the same UID
    assert (got["SH2129"].object_name, got["SH2129"].object_record_uid) == ("SH 2-129", renamed_uid)
    assert db.get(JournalSession, unlinked_id).object_record_uid == renamed_uid
    # Merge path: follows the surviving object
    assert (got["NGC1976"].object_name, got["NGC1976"].object_record_uid) == ("NGC 1976", correct_uid)
    assert got["NGC 1976"].object_record_uid == correct_uid
    session_links = [link for link in LINKS if link.table == "journal_sessions" and not link.by_id]
    result = count_link_disagreements(db, user_id=a_id, links=session_links)
    assert result == {link.name: ZERO for link in session_links}, result
    assert _snapshot(db, b_id) == before_b


# --- repair_journals ------------------------------------------------------------

def test_repair_journals_backfill_sets_object_uid(db_session, tmp_path, monkeypatch):
    db = db_session
    a_id = _user(db, "uid_rj_a")
    b_id = _user(db, "uid_rj_b")
    a = _populate(db, a_id, "A")
    _populate(db, b_id, "B")
    blank = JournalSession(user_id=a_id, date_utc=date(2026, 1, 10))
    db.add(blank)
    db.commit()
    blank_id = blank.id
    before_b = _snapshot(db, b_id)
    (tmp_path / "journal_uid_rj_a.yaml").write_text("sessions: []\n")
    monkeypatch.setattr("nova.migration.CONFIG_DIR", str(tmp_path))
    # The backfill branch only runs when _read_yaml returns a dict, but the real one
    # returns a (data, error) tuple (reported, not fixed). Feed the branch directly.
    monkeypatch.setattr("nova.migration._read_yaml",
                        lambda path: {"sessions": [{"date": "2026-01-10", "object_name": "M42"}]})

    repair_journals(dry_run=False)

    db.expire_all()
    s = db.get(JournalSession, blank_id)
    assert (s.object_name, s.object_record_uid) == ("M42", a.m42_uid)
    _assert_name_links_clean(db, a_id)
    assert _snapshot(db, b_id) == before_b


# --- purge_user_app_data --------------------------------------------------------

def test_purge_clears_uids_with_the_old_links_it_nulls(db_session, tmp_path, monkeypatch):
    db = db_session
    monkeypatch.setattr("nova.auth.db", None)
    for attr in ("UPLOAD_FOLDER", "CONFIG_DIR", "ASIAIR_LOGS_DIR", "PHD2_LOGS_DIR", "NINA_LOGS_DIR"):
        monkeypatch.setattr(f"nova.helpers.{attr}", str(tmp_path / attr))
    v_id = _user(db, "uid_purge_victim")
    b_id = _user(db, "uid_purge_b")
    v = _populate(db, v_id, "V")
    b = _populate(db, b_id, "B")
    before_b = _snapshot(db, b_id)
    # B's rows that point at V's rig and telescope and hold V's UIDs
    cross_session = JournalSession(user_id=b_id, date_utc=date(2026, 1, 5), object_name="M31",
                                   object_record_uid=b.m31_uid,
                                   rig_id_snapshot=v.rig_id, rig_record_uid=v.rig_uid)
    cross_framing = SavedFraming(user_id=b_id, object_name="M31", object_record_uid=b.m31_uid,
                                 rig_id=v.rig_id, rig_record_uid=v.rig_uid)
    cross_rig = Rig(user_id=b_id, rig_name="B Mixed", telescope_id=v.tel_id, telescope_record_uid=v.tel_uid,
                    camera_id=b.cam_id, camera_record_uid=b.cam_uid)
    db.add_all([cross_session, cross_framing, cross_rig])
    db.commit()
    cross = {"journal_sessions": str(cross_session.id), "saved_framings": str(cross_framing.id),
             "rigs": str(cross_rig.id)}

    summary = purge_user_app_data("uid_purge_victim", dry_run=False)
    assert summary["refused"] is None, summary

    db.expire_all()
    s = db.get(JournalSession, int(cross["journal_sessions"]))
    assert (s.rig_id_snapshot, s.rig_record_uid) == (None, None)
    f = db.get(SavedFraming, int(cross["saved_framings"]))
    assert (f.rig_id, f.rig_record_uid) == (None, None)
    r = db.get(Rig, int(cross["rigs"]))
    assert (r.telescope_id, r.telescope_record_uid) == (None, None)
    assert (r.camera_id, r.camera_record_uid) == (b.cam_id, b.cam_uid)  # B's own component: untouched

    result = count_link_disagreements(db, user_id=b_id)
    assert result == {link.name: ZERO for link in LINKS}, result
    after_b = _snapshot(db, b_id)
    after_b = {table: [row for row in rows if row[0] != cross.get(table)] for table, rows in after_b.items()}
    assert after_b == before_b


# --- Guest commands -------------------------------------------------------------

TEMPLATES = {
    "config_guest_user.yaml": """
default_location: Home
locations:
  Home: {lat: 48.2, lon: 16.4, timezone: Europe/Vienna}
objects:
  - {Object: M42, RA: 5.59, DEC: -5.39, Constellation: Ori}
saved_framings:
  - {object_name: M42, rig_name: Imp Rig}
""",
    "rigs_default.yaml": yaml.safe_dump(RIGS_YAML),
    "journal_default.yaml": """
projects:
  - {project_id: tpl_p1, project_name: Tpl Orion, target_object_id: M42}
sessions:
  - {session_id: tpl_s1, session_date: '2026-01-10', object_name: M42, location_name: Home}
""",
}


@pytest.fixture
def templates(tmp_path, monkeypatch):
    for name, content in TEMPLATES.items():
        (tmp_path / name).write_text(content)
    monkeypatch.setattr("nova.TEMPLATE_DIR", str(tmp_path))


def _guest_id(db):
    return db.query(DbUser).filter_by(username="guest_user").one().id


def test_seed_guest_account_never_relinks_untouched_framings(db_session, templates):
    db = db_session
    guest_id = _guest_id(db)
    b_id = _user(db, "uid_guest_b")
    g = _populate(db, guest_id, "G")
    _populate(db, b_id, "B")
    before_framings = _snapshot(db, guest_id, models=(SavedFraming,))
    before_b = _snapshot(db, b_id)

    result = app.test_cli_runner().invoke(args=["seed-guest-account"])
    assert result.exit_code == 0, result.output
    assert "SEEDING COMPLETE" in result.output

    # The command deletes the guest's rigs but not its framings. The framing keeps
    # rig_id and its rig UID exactly as they were; it is not re-linked to the new rig.
    assert _snapshot(db, guest_id, models=(SavedFraming,)) == before_framings
    assert _snapshot(db, b_id) == before_b
    db.expire_all()
    s = db.query(JournalSession).filter_by(user_id=guest_id).one()
    assert (s.object_record_uid, s.location_record_uid, s.rig_record_uid) == (g.m42_uid, g.home_uid, None)
    _assert_name_links_clean(db, guest_id)


def test_reset_guest_from_template_links_new_rows_and_leaves_other_users(db_session, templates):
    db = db_session
    guest_id = _guest_id(db)
    b_id = _user(db, "uid_guest_b")
    _populate(db, guest_id, "G")
    _populate(db, b_id, "B")
    before_b = _snapshot(db, b_id)

    result = app.test_cli_runner().invoke(args=["reset-guest-from-template"])
    assert result.exit_code == 0, result.output
    assert "Guest user fully reset" in result.output

    assert _snapshot(db, b_id) == before_b
    db.expire_all()
    m42 = db.query(AstroObject).filter_by(user_id=guest_id, object_name="M42").one()
    home = db.query(Location).filter_by(user_id=guest_id, name="Home").one()
    rig = db.query(Rig).filter_by(user_id=guest_id).one()
    framing = db.query(SavedFraming).filter_by(user_id=guest_id).one()
    # Created before the rigs, healed by _heal_saved_framings in the same command
    assert (framing.rig_id, framing.rig_record_uid, framing.object_record_uid) == (
        rig.id, rig.record_uid, m42.record_uid)
    s = db.query(JournalSession).filter_by(user_id=guest_id).one()
    assert (s.object_record_uid, s.location_record_uid, s.rig_record_uid) == (m42.record_uid, home.record_uid, None)
    _assert_name_links_clean(db, guest_id)
