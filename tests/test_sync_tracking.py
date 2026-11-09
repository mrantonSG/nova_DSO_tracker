"""Instance sync — change tracking (step 2).

Every write to a synced record, by any path, must stamp sync_changed_at and a
fresh per-user sync_rev; every delete must leave a SyncTombstone under the
record's natural key. Nothing reads these yet (step 3 does).
"""
import json
from datetime import date, datetime

import pytest

from nova import get_or_create_db_user
from nova.helpers import purge_user_app_data
from nova.migration import export_user_data, wipe_user_data
from nova.models import (
    AstroObject, Component, DbUser, HorizonPoint, JournalSession, Location,
    Project, Rig, SavedFraming, SavedView, SyncCounter, SyncTombstone, UiPref,
    UserCustomFilter,
)
from nova.record_links import adopt_unlinked_rows_for_user, resync_user_links
from nova.sync_tracking import ACCOUNT_TABLE, mark_file_changed


# --- Helpers -------------------------------------------------------------------

def _user(db, name="default"):
    return db.query(DbUser).filter_by(username=name).one()


def _rev(db, user_id):
    """The user's counter, or 0 when it has none yet."""
    db.expire_all()
    row = db.query(SyncCounter).filter_by(user_id=user_id).one_or_none()
    return row.rev if row else 0


def _tombstones(db, user_id, table=None):
    db.expire_all()
    query = db.query(SyncTombstone).filter_by(user_id=user_id)
    if table is not None:
        query = query.filter_by(table_name=table)
    return sorted((t.record_key, t.sync_rev) for t in query.all())


def _keys(db, user_id, table):
    return [key for key, _ in _tombstones(db, user_id, table)]


@pytest.fixture
def user(db_session):
    db_session.add(DbUser(username="sync_user"))
    db_session.commit()
    return _user(db_session, "sync_user")


# --- One builder per synced model ----------------------------------------------

def _location(db, uid, name="Home"):
    db.add(Location(user_id=uid, name=name, lat=48.0, lon=16.0, timezone="UTC"))
    db.commit()
    return db.query(Location).filter_by(user_id=uid, name=name).one()


def _component(db, uid, name="Cam", kind="camera"):
    if kind == "telescope":
        db.add(Component(user_id=uid, kind=kind, name=name,
                         aperture_mm=100.0, focal_length_mm=500.0))
    else:
        db.add(Component(user_id=uid, kind=kind, name=name, sensor_width_mm=20.0,
                         sensor_height_mm=15.0, pixel_size_um=3.0))
    db.commit()
    return db.query(Component).filter_by(user_id=uid, name=name).one()


def _rig(db, uid, name="Rig"):
    cam = _component(db, uid, name=f"{name} Cam")
    db.add(Rig(user_id=uid, rig_name=name, camera_id=cam.id, active=True))
    db.commit()
    return db.query(Rig).filter_by(user_id=uid, rig_name=name).one()


def _object(db, uid, name="M42"):
    db.add(AstroObject(user_id=uid, object_name=name, ra_hours=5.58, dec_deg=-5.4))
    db.commit()
    return db.query(AstroObject).filter_by(user_id=uid, object_name=name).one()


def _session(db, uid, external_id="sess-1"):
    db.add(JournalSession(user_id=uid, date_utc=date(2025, 1, 1), object_name="M42",
                          external_id=external_id))
    db.commit()
    return db.query(JournalSession).filter_by(user_id=uid, external_id=external_id).one()


def _project(db, uid, pid="proj-1", name="Proj"):
    db.add(Project(id=pid, user_id=uid, name=name))
    db.commit()
    return db.query(Project).filter_by(id=pid).one()


def _view(db, uid, name="View"):
    db.add(SavedView(user_id=uid, name=name, settings_json="{}"))
    db.commit()
    return db.query(SavedView).filter_by(user_id=uid, name=name).one()


def _framing(db, uid, name="M42", object_uid="uid-obj"):
    db.add(SavedFraming(user_id=uid, object_name=name, object_record_uid=object_uid))
    db.commit()
    return db.query(SavedFraming).filter_by(user_id=uid, object_name=name).one()


def _filter(db, uid, key="Ha"):
    db.add(UserCustomFilter(user_id=uid, filter_key=key, filter_label=key))
    db.commit()
    return db.query(UserCustomFilter).filter_by(user_id=uid, filter_key=key).one()


def _prefs(db, uid):
    db.add(UiPref(user_id=uid, json_blob="{}"))
    db.commit()
    return db.query(UiPref).filter_by(user_id=uid).one()


# (table, build, natural key, an edit)
MODELS = [
    ("locations", _location, lambda o: o.record_uid, lambda o: setattr(o, "comments", "x")),
    ("astro_objects", _object, lambda o: o.record_uid, lambda o: setattr(o, "common_name", "x")),
    ("components", _component, lambda o: o.record_uid, lambda o: setattr(o, "name", "Renamed")),
    ("rigs", _rig, lambda o: o.record_uid, lambda o: setattr(o, "rig_name", "Renamed")),
    ("journal_sessions", _session, lambda o: o.external_id, lambda o: setattr(o, "notes", "x")),
    ("projects", _project, lambda o: o.id, lambda o: setattr(o, "description_notes", "x")),
    ("saved_views", _view, lambda o: o.name, lambda o: setattr(o, "settings_json", '{"a": 1}')),
    ("saved_framings", _framing, lambda o: o.object_record_uid, lambda o: setattr(o, "rotation", 1.5)),
    ("user_custom_filters", _filter, lambda o: o.filter_key, lambda o: setattr(o, "filter_label", "x")),
    ("ui_prefs", _prefs, lambda o: str(o.user_id), lambda o: setattr(o, "json_blob", '{"a": 1}')),
]
MODEL_IDS = [m[0] for m in MODELS]


# --- Create / edit / delete, one model at a time --------------------------------

@pytest.mark.parametrize("table,build,key_of,edit", MODELS, ids=MODEL_IDS)
def test_create_is_stamped(db_session, user, table, build, key_of, edit):
    before = _rev(db_session, user.id)
    obj = build(db_session, user.id)
    db_session.expire_all()
    assert obj.sync_changed_at is not None
    assert obj.sync_rev > before
    assert obj.sync_rev == _rev(db_session, user.id)


@pytest.mark.parametrize("table,build,key_of,edit", MODELS, ids=MODEL_IDS)
def test_edit_bumps_the_rev(db_session, user, table, build, key_of, edit):
    obj = build(db_session, user.id)
    before = _rev(db_session, user.id)
    edit(obj)
    db_session.commit()
    db_session.expire_all()
    assert obj.sync_rev == before + 1
    assert obj.sync_changed_at is not None


@pytest.mark.parametrize("table,build,key_of,edit", MODELS, ids=MODEL_IDS)
def test_delete_writes_a_tombstone(db_session, user, table, build, key_of, edit):
    obj = build(db_session, user.id)
    key = key_of(obj)
    db_session.delete(obj)
    db_session.commit()
    assert _tombstones(db_session, user.id, table) == [(key, _rev(db_session, user.id))]


def test_delete_of_a_keyless_row_writes_no_tombstone(db_session, user):
    """A session with no external_id has no natural key: nothing to tombstone."""
    db_session.add(JournalSession(user_id=user.id, date_utc=date(2025, 1, 1), external_id=None))
    db_session.commit()
    row = db_session.query(JournalSession).filter_by(user_id=user.id).one()
    db_session.delete(row)
    db_session.commit()
    assert _tombstones(db_session, user.id, "journal_sessions") == []


# --- The rules ------------------------------------------------------------------

def test_a_save_with_no_real_change_does_not_bump(db_session, user):
    loc = _location(db_session, user.id)
    rev, stamp = _rev(db_session, user.id), loc.sync_changed_at

    loc.comments = loc.comments          # the same value written back
    db_session.commit()
    db_session.commit()                  # and a flush with nothing to do

    db_session.expire_all()
    assert _rev(db_session, user.id) == rev
    assert loc.sync_changed_at == stamp
    assert loc.sync_rev == rev


def test_writing_the_analysis_cache_alone_does_not_bump(db_session, user):
    """log_analysis_cache is derived: caching a parse is not a synced change."""
    session = _session(db_session, user.id)
    rev = _rev(db_session, user.id)
    stamp = session.sync_changed_at

    session.log_analysis_cache = json.dumps({"rms": 1.2})
    db_session.commit()

    db_session.expire_all()
    assert _rev(db_session, user.id) == rev
    assert session.sync_rev == rev
    assert session.sync_changed_at == stamp


def test_a_new_log_still_bumps_the_session(db_session, user):
    """A real column moving alongside the cache still stamps the row."""
    session = _session(db_session, user.id)
    rev = _rev(db_session, user.id)

    session.asiair_log_content = "instance/logs/asiair/1_Autorun_Log.txt"
    session.log_analysis_cache = json.dumps({"rms": 1.2})
    db_session.commit()

    db_session.expire_all()
    assert _rev(db_session, user.id) > rev
    assert session.sync_rev > rev


def test_a_cache_write_then_a_log_write_still_bumps(db_session, user):
    session = _session(db_session, user.id)
    session.asiair_log_content = "instance/logs/asiair/1_Autorun_Log.txt"
    db_session.commit()
    rev = _rev(db_session, user.id)

    session.log_analysis_cache = json.dumps({"rms": 2.0})
    db_session.commit()
    db_session.expire_all()
    assert _rev(db_session, user.id) == rev     # derived only

    session.phd2_log_content = "instance/logs/phd2/1_PHD2_GuideLog.txt"
    db_session.commit()
    db_session.expire_all()
    assert _rev(db_session, user.id) > rev      # a real log moved


def test_mark_file_changed_does_not_accept_the_derived_cache(db_session, user):
    """FILE_SLOTS is what mark_file_changed accepts; the derived column is not a
    file slot any more, so a caller cannot stamp a row over a cache write."""
    from nova.sync_tracking import FILE_SLOTS
    session = _session(db_session, user.id)
    rev = _rev(db_session, user.id)

    assert "log_analysis_cache" not in FILE_SLOTS["journal_sessions"]
    assert mark_file_changed(session, "log_analysis_cache") is False
    db_session.commit()
    db_session.expire_all()
    assert _rev(db_session, user.id) == rev


def test_horizon_point_changes_bump_their_location(db_session, user):
    loc = _location(db_session, user.id)
    start = loc.sync_rev

    point = HorizonPoint(location_id=loc.id, az_deg=10.0, alt_min_deg=5.0)
    db_session.add(point)
    db_session.commit()
    db_session.expire_all()
    assert loc.sync_rev == start + 1

    point.alt_min_deg = 20.0
    db_session.commit()
    db_session.expire_all()
    assert loc.sync_rev == start + 2

    db_session.delete(point)
    db_session.commit()
    db_session.expire_all()
    assert loc.sync_rev == start + 3
    # HorizonPoint is not a synced table of its own: no tombstone for it.
    assert _keys(db_session, user.id, "horizon_points") == []


def test_a_deleted_location_with_its_points_is_tombstoned_once(db_session, user):
    loc = _location(db_session, user.id)
    key = loc.record_uid
    db_session.add(HorizonPoint(location_id=loc.id, az_deg=10.0, alt_min_deg=5.0))
    db_session.commit()

    db_session.delete(loc)
    db_session.commit()
    assert _keys(db_session, user.id, "locations") == [key]


def test_adding_and_removing_a_project_bumps_the_session(db_session, user):
    session = _session(db_session, user.id)
    project = _project(db_session, user.id)
    start = session.sync_rev

    session.projects.append(project)
    db_session.commit()
    db_session.expire_all()
    assert session.sync_rev > start
    assert session.sync_rev == _rev(db_session, user.id)
    after_append = session.sync_rev

    session.projects.remove(project)
    db_session.commit()
    db_session.expire_all()
    assert session.sync_rev > after_append
    assert session.sync_rev == _rev(db_session, user.id)


def test_renaming_a_saved_view_tombs_the_old_name(db_session, user):
    view = _view(db_session, user.id, name="Old View")
    view.name = "New View"
    db_session.commit()

    assert _keys(db_session, user.id, "saved_views") == ["Old View"]
    db_session.expire_all()
    assert view.sync_rev == _rev(db_session, user.id)


def test_changing_a_custom_filter_key_tombs_the_old_key(db_session, user):
    row = _filter(db_session, user.id, key="Ha")
    row.filter_key = "Halpha"
    db_session.commit()
    assert _keys(db_session, user.id, "user_custom_filters") == ["Ha"]


def test_repointing_a_framing_tombs_its_old_object(db_session, user):
    framing = _framing(db_session, user.id, object_uid="uid-old")
    framing.object_record_uid = "uid-new"
    db_session.commit()
    assert _keys(db_session, user.id, "saved_framings") == ["uid-old"]


def test_recreating_a_deleted_key_drops_its_tombstone(db_session, user):
    loc = _location(db_session, user.id, name="Home")
    key = loc.record_uid
    db_session.delete(loc)
    db_session.commit()
    assert _keys(db_session, user.id, "locations") == [key]

    db_session.add(Location(user_id=user.id, name="Home", lat=1.0, lon=2.0,
                            timezone="UTC", record_uid=key))
    db_session.commit()
    assert _keys(db_session, user.id, "locations") == []


def test_remote_apply_keeps_its_timestamp_and_still_gets_a_rev(db_session, user):
    when = datetime(2020, 1, 2, 3, 4, 5)
    db_session.info["sync_apply_remote"] = True
    try:
        db_session.add(Location(user_id=user.id, name="Remote", lat=1.0, lon=2.0,
                                timezone="UTC", sync_changed_at=when))
        db_session.commit()
    finally:
        db_session.info.pop("sync_apply_remote", None)

    db_session.expire_all()
    loc = db_session.query(Location).filter_by(user_id=user.id, name="Remote").one()
    assert loc.sync_changed_at == when
    assert loc.sync_rev == _rev(db_session, user.id)


def test_an_ordinary_write_overwrites_a_preset_timestamp(db_session, user):
    """Without the flag, a preset sync_changed_at is not trusted."""
    db_session.add(Location(user_id=user.id, name="Local", lat=1.0, lon=2.0,
                            timezone="UTC", sync_changed_at=datetime(2020, 1, 2, 3, 4, 5)))
    db_session.commit()
    db_session.expire_all()
    loc = db_session.query(Location).filter_by(user_id=user.id, name="Local").one()
    assert loc.sync_changed_at != datetime(2020, 1, 2, 3, 4, 5)


def test_revs_are_per_user(db_session):
    a, b = DbUser(username="sync_a"), DbUser(username="sync_b")
    db_session.add_all([a, b])
    db_session.commit()

    _location(db_session, a.id, name="A Home")
    assert _rev(db_session, a.id) == 1
    assert db_session.query(SyncCounter).filter_by(user_id=b.id).one_or_none() is None

    _location(db_session, b.id, name="B Home")
    assert _rev(db_session, a.id) == 1     # A's counter never moved
    assert _rev(db_session, b.id) == 1


# --- Bulk write paths -----------------------------------------------------------

def test_set_location_stamps_the_rows_it_clears(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _user(db_session, "default")
    client.post('/config_form', data={
        'submit_new_location': '1', 'new_location': 'Second Loc',
        'new_lat': '48.2', 'new_lon': '16.4', 'new_timezone': 'Europe/Vienna',
        'new_active': 'on',
    })
    db_session.expire_all()
    first = db_session.query(Location).filter_by(user_id=user.id, name="Default Test Loc").one()
    second = db_session.query(Location).filter_by(user_id=user.id, name="Second Loc").one()
    assert first.is_default is True
    start = _rev(db_session, user.id)

    resp = client.post('/set_location', json={"location": "Second Loc"})
    assert resp.status_code == 200

    db_session.expire_all()
    assert first.is_default is False
    assert second.is_default is True
    assert first.sync_rev > start      # cleared by the bulk UPDATE
    assert second.sync_rev > start     # set through the ORM


def test_bulk_delete_of_objects_writes_tombstones(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _user(db_session, "default")
    client.post('/confirm_object', json={"object": "M31", "name": "M31", "ra": 0.71, "dec": 41.3})
    db_session.expire_all()
    m42_uid = db_session.query(AstroObject).filter_by(
        user_id=user.id, object_name="M42").one().record_uid
    m31_uid = db_session.query(AstroObject).filter_by(
        user_id=user.id, object_name="M31").one().record_uid

    resp = client.post('/api/bulk_update_objects',
                       json={"action": "delete", "object_ids": ["M42", "M31"]})
    assert resp.status_code == 200
    assert "Deleted 2 objects" in resp.get_json()["message"]

    assert sorted(_keys(db_session, user.id, "astro_objects")) == sorted([m42_uid, m31_uid])


def test_bulk_disable_stamps_the_rows_it_changes(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _user(db_session, "default")
    start = _rev(db_session, user.id)

    resp = client.post('/api/bulk_update_objects',
                       json={"action": "disable", "object_ids": ["M42"]})
    assert resp.status_code == 200

    db_session.expire_all()
    m42 = db_session.query(AstroObject).filter_by(user_id=user.id, object_name="M42").one()
    assert m42.enabled is False
    assert m42.sync_rev > start


def test_bulk_delete_of_objects_tombs_their_framings(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _user(db_session, "default")
    db_session.expire_all()
    m42_uid = db_session.query(AstroObject).filter_by(
        user_id=user.id, object_name="M42").one().record_uid
    db_session.add(SavedFraming(user_id=user.id, object_name="M42", object_record_uid=m42_uid))
    db_session.commit()

    resp = client.post('/api/bulk_update_objects',
                       json={"action": "delete", "object_ids": ["M42"]})
    assert resp.status_code == 200
    assert _keys(db_session, user.id, "saved_framings") == [m42_uid]
    assert _keys(db_session, user.id, "astro_objects") == [m42_uid]


def test_adopt_unlinked_rows_stamps_the_rows_it_links(db_session, user):
    obj = _object(db_session, user.id, name="M42")
    session = _session(db_session, user.id)
    session.object_record_uid = None       # an empty link, waiting to be adopted
    db_session.commit()
    start = session.sync_rev

    changed = adopt_unlinked_rows_for_user(db_session, user.id)
    db_session.commit()

    assert changed["journal_sessions.object_record_uid"] == 1
    db_session.expire_all()
    assert session.object_record_uid == obj.record_uid
    assert session.sync_rev > start


def test_resync_user_links_stamps_the_rows_it_relinks(db_session, user):
    obj = _object(db_session, user.id, name="M42")
    session = _session(db_session, user.id)
    session.object_record_uid = "uid-stale"
    db_session.commit()
    start = session.sync_rev

    changed = resync_user_links(db_session, user.id)
    db_session.commit()

    assert changed["journal_sessions.object_record_uid"] == 1
    db_session.expire_all()
    assert session.object_record_uid == obj.record_uid
    assert session.sync_rev > start


# --- Whole-account paths --------------------------------------------------------

def test_wipe_marks_the_account_replaced_and_tombs_no_record(db_session, user):
    _location(db_session, user.id)
    _object(db_session, user.id)
    _session(db_session, user.id)

    wipe_user_data(db_session, user, everything=True)
    db_session.commit()

    db_session.expire_all()
    rows = db_session.query(SyncTombstone).filter_by(user_id=user.id).all()
    assert [(r.table_name, r.record_key) for r in rows] == [(ACCOUNT_TABLE, "replaced")]
    assert rows[0].sync_rev == _rev(db_session, user.id)
    for table in ("locations", "astro_objects", "journal_sessions"):
        assert _keys(db_session, user.id, table) == []


def test_seeding_a_new_user_marks_the_account_seeded(db_session):
    new_user = get_or_create_db_user(db_session, "seeded_user")

    assert new_user is not None
    assert _tombstones(db_session, new_user.id) == [("seeded", _rev(db_session, new_user.id))]
    assert _rev(db_session, new_user.id) >= 1


def test_purge_leaves_no_sync_rows_for_the_user_id(db_session, monkeypatch):
    import nova.auth
    monkeypatch.setattr(nova.auth, "db", None)      # no users.db to consult

    doomed = DbUser(username="doomed")
    db_session.add(doomed)
    db_session.commit()
    doomed_id = doomed.id
    _location(db_session, doomed_id, name="Doomed Home")
    extra = _location(db_session, doomed_id, name="Doomed Shed")
    db_session.delete(extra)                        # leaves a tombstone and a counter
    db_session.commit()
    assert _rev(db_session, doomed_id) > 0
    assert _keys(db_session, doomed_id, "locations") != []

    summary = purge_user_app_data("doomed", dry_run=False)
    assert summary["refused"] is None

    db_session.expire_all()
    for model in (SyncCounter, SyncTombstone):
        assert db_session.query(model).filter_by(user_id=doomed_id).count() == 0


def test_export_never_carries_the_sync_columns(db_session, user):
    _location(db_session, user.id, name="Home")
    _object(db_session, user.id, name="M42")
    _view(db_session, user.id, name="View")
    _prefs(db_session, user.id)

    config, rigs, journal = export_user_data(db_session, user)
    blob = json.dumps({"config": config, "rigs": rigs, "journal": journal}, default=str)

    assert "sync_changed_at" not in blob
    assert "sync_rev" not in blob


# --- Route sweep ----------------------------------------------------------------

def test_route_sweep_stamps_every_touched_row(su_client_logged_in, db_session):
    """Add / edit / delete through the real routes, one row of each kind."""
    client = su_client_logged_in
    user = _user(db_session, "default")

    def rev():
        return _rev(db_session, user.id)

    def fresh(model, **filters):
        db_session.expire_all()
        return db_session.query(model).filter_by(user_id=user.id, **filters).one()

    # --- location (config form) ---
    start = rev()
    client.post('/config_form', data={
        'submit_new_location': '1', 'new_location': 'Sweep Loc',
        'new_lat': '48.2', 'new_lon': '16.4', 'new_timezone': 'Europe/Vienna',
        'new_active': 'on',
    })
    loc = fresh(Location, name="Sweep Loc")
    assert loc.sync_changed_at is not None and loc.sync_rev > start

    # --- object (confirm_object) ---
    start = rev()
    client.post('/confirm_object', json={"object": "M31", "name": "M31", "ra": 0.71, "dec": 41.3})
    obj = fresh(AstroObject, object_name="M31")
    assert obj.sync_changed_at is not None and obj.sync_rev > start

    # --- rig (add_rig) ---
    telescope = _component(db_session, user.id, name="Sweep Scope", kind="telescope")
    camera = _component(db_session, user.id, name="Sweep Cam")
    start = rev()
    client.post('/add_rig', data={"rig_name": "Sweep Rig", "telescope_id": telescope.id,
                                  "camera_id": camera.id})
    rig = fresh(Rig, rig_name="Sweep Rig")
    assert rig.sync_changed_at is not None and rig.sync_rev > start

    # --- session (journal add) ---
    start = rev()
    client.post('/journal/add', data={
        "session_date": "2025-01-01", "target_object_id": "M42",
        "number_of_subs_light": 10, "exposure_time_per_sub_sec": 60,
        "project_selection": "standalone",
    }, follow_redirects=True)
    session = fresh(JournalSession, object_name="M42")
    assert session.sync_changed_at is not None and session.sync_rev > start

    # --- project (add_project) ---
    start = rev()
    client.post('/journal/add_project', data={"name": "Sweep Project",
                                              "target_object_id": "M42",
                                              "status": "In Progress"})
    project = fresh(Project, name="Sweep Project")
    assert project.sync_changed_at is not None and project.sync_rev > start

    # --- framing (save_framing) ---
    start = rev()
    client.post('/api/save_framing', json={"object_name": "M42", "ra": 83.8, "dec": -5.4})
    framing = fresh(SavedFraming, object_name="M42")
    assert framing.sync_changed_at is not None and framing.sync_rev > start

    # --- saved view (save_saved_view) ---
    start = rev()
    client.post('/api/save_saved_view', json={"name": "Sweep View",
                                              "settings": {"activeTab": "position"}})
    view = fresh(SavedView, name="Sweep View")
    assert view.sync_changed_at is not None and view.sync_rev > start

    # --- an edit through a route (the form needs every location's fields) ---
    start = rev()
    client.post('/config_form', data={
        'submit_locations': '1',
        'active_Sweep Loc': 'on', 'lat_Sweep Loc': '49.0', 'lon_Sweep Loc': '17.0',
        'timezone_Sweep Loc': 'Europe/Vienna',
        'active_Default Test Loc': 'on', 'lat_Default Test Loc': '50.0',
        'lon_Default Test Loc': '10.0', 'timezone_Default Test Loc': 'UTC',
    })
    assert fresh(Location, name="Sweep Loc").sync_rev > start

    # --- deletes through routes ---
    client.post('/api/delete_saved_view', json={"name": "Sweep View"})
    assert "Sweep View" in _keys(db_session, user.id, "saved_views")

    client.post('/api/delete_framing', json={"object_name": "M42"})
    assert framing.object_record_uid in _keys(db_session, user.id, "saved_framings")

    session_id, external_id = session.id, session.external_id
    client.post(f'/journal/delete/{session_id}')
    assert external_id in _keys(db_session, user.id, "journal_sessions")

    client.post('/config_form', data={
        'submit_locations': '1', 'delete_loc_Sweep Loc': 'on',
        'active_Default Test Loc': 'on', 'lat_Default Test Loc': '50.0',
        'lon_Default Test Loc': '10.0', 'timezone_Default Test Loc': 'UTC',
    })
    assert loc.record_uid in _keys(db_session, user.id, "locations")
