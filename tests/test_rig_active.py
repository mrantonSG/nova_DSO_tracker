"""
Rig.active: the model default and the schema patch.

Covers the default on a fresh insert and the upgrade path for an existing
database whose rigs table predates the column, including idempotency.
No route, template, export or import reads the flag yet.
"""

import os
import sys
from datetime import date

from sqlalchemy import create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import _run_schema_patches
from nova.models import Component, DbUser, JournalSession, Rig, SavedFraming
from nova.record_links import (
    rig_references, sync_framing_links, sync_rig_links, sync_session_links,
)

from test_db_upgrade_simulation import MINIMAL_BASELINE_STATEMENTS


def _make_components(db_session, user_id):
    tel = Component(user_id=user_id, kind="telescope", name="Scope A",
                    aperture_mm=100.0, focal_length_mm=500.0)
    cam = Component(user_id=user_id, kind="camera", name="Cam A",
                    sensor_width_mm=20.0, sensor_height_mm=15.0, pixel_size_um=3.0)
    db_session.add_all([tel, cam])
    db_session.commit()
    return tel.id, cam.id


def _rig_form_data(tel_id, cam_id, **extra):
    data = {"rig_name": "Form Rig", "telescope_id": tel_id, "camera_id": cam_id}
    data.update(extra)
    return data


def _default_user(db_session):
    return db_session.query(DbUser).filter_by(username="default").one()


def _make_linked_rig(db_session, user_id, name, active=True):
    """A rig plus its own telescope/camera, with record_uid links synced."""
    tel = Component(user_id=user_id, kind="telescope", name=f"{name} Scope",
                    aperture_mm=100.0, focal_length_mm=500.0)
    cam = Component(user_id=user_id, kind="camera", name=f"{name} Cam",
                    sensor_width_mm=20.0, sensor_height_mm=15.0, pixel_size_um=3.0)
    db_session.add_all([tel, cam])
    db_session.flush()
    rig = Rig(user_id=user_id, rig_name=f"{name} Rig", telescope_id=tel.id,
              camera_id=cam.id, active=active)
    sync_rig_links(db_session, rig)
    db_session.add(rig)
    db_session.commit()
    return rig


def _link_session(db_session, user_id, rig_id):
    session = JournalSession(user_id=user_id, date_utc=date(2026, 1, 1),
                             object_name="M42", rig_id_snapshot=rig_id)
    sync_session_links(db_session, session)
    db_session.add(session)
    db_session.commit()
    return session.id


def _link_framing(db_session, user_id, rig, object_name="M42"):
    framing = SavedFraming(user_id=user_id, object_name=object_name,
                           rig_id=rig.id, rig_name=rig.rig_name)
    sync_framing_links(db_session, framing)
    db_session.add(framing)
    db_session.commit()
    return framing.id


def _baseline_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
    return engine


def _rig_columns(conn):
    return {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(rigs);").fetchall()}


def _rig_actives(conn):
    return dict(conn.exec_driver_sql("SELECT id, active FROM rigs ORDER BY id;").fetchall())


def _active_indexes(conn):
    """Names of every index on exactly the `active` column of rigs."""
    found = []
    for row in conn.exec_driver_sql("PRAGMA index_list(rigs);").fetchall():
        name = row[1]
        cols = [r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info({name});").fetchall()]
        if cols == ["active"]:
            found.append(name)
    return found


# --- 1. Default on insert ----------------------------------------------------

def test_new_rig_is_active_by_default(db_session):
    user = DbUser(username="rig_active_user")
    db_session.add(user)
    db_session.commit()

    rig = Rig(user_id=user.id, rig_name="Default Rig")
    db_session.add(rig)
    db_session.commit()

    assert rig.active is True


# --- 2. Upgrade path ---------------------------------------------------------

def test_upgrade_adds_column_and_every_existing_row_is_active():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a');")
        for n in range(3):
            conn.exec_driver_sql(
                "INSERT INTO rigs (user_id, rig_name) VALUES (1, ?);", (f"Rig{n}",))

    with engine.begin() as conn:
        assert "active" not in _rig_columns(conn)
        _run_schema_patches(conn)

    with engine.connect() as conn:
        assert "active" in _rig_columns(conn)
        assert _rig_actives(conn) == {1: 1, 2: 1, 3: 1}
        assert _active_indexes(conn) == ["ix_rigs_active"]


# --- 3. Idempotency ----------------------------------------------------------

def test_running_the_patches_twice_changes_nothing_and_raises_nothing():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a');")
        for n in range(2):
            conn.exec_driver_sql(
                "INSERT INTO rigs (user_id, rig_name) VALUES (1, ?);", (f"Rig{n}",))

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        columns_first = _rig_columns(conn)
        actives_first = _rig_actives(conn)

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        assert _rig_columns(conn) == columns_first
        assert _rig_actives(conn) == actives_first
        assert _active_indexes(conn) == ["ix_rigs_active"]


# --- 4. The rig form saves the flag -----------------------------------------

def test_form_with_marker_and_unchecked_creates_inactive_rig(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    resp = client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Off Rig", rig_active_present="1"))

    assert resp.status_code == 302
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="Off Rig").one()
    assert rig.active is False


def test_form_with_marker_and_checked_creates_active_rig(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    resp = client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="On Rig", rig_active_present="1", rig_active="on"))

    assert resp.status_code == 302
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="On Rig").one()
    assert rig.active is True


def test_form_update_with_marker_toggles_the_flag_both_ways(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    # Create without the marker: defaults to active.
    client.post('/add_rig', data=_rig_form_data(tel_id, cam_id, rig_name="Toggle Rig"))
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="Toggle Rig").one()
    assert rig.active is True

    # Update with the marker and no checkbox: deactivate.
    client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Toggle Rig", rig_id=rig.id, rig_active_present="1"))
    db_session.expire_all()
    assert db_session.get(Rig, rig.id).active is False

    # Update with the marker and the checkbox: reactivate.
    client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Toggle Rig", rig_id=rig.id,
        rig_active_present="1", rig_active="on"))
    db_session.expire_all()
    assert db_session.get(Rig, rig.id).active is True


def test_form_without_marker_new_rig_is_active(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    client.post('/add_rig', data=_rig_form_data(tel_id, cam_id, rig_name="No Marker New"))

    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="No Marker New").one()
    assert rig.active is True


def test_form_without_marker_leaves_an_inactive_rig_inactive(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Stay Off", rig_active_present="1"))
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="Stay Off").one()
    assert rig.active is False

    # A POST without the marker (e.g. another form) must not deactivate or reactivate it.
    client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Stay Off", rig_id=rig.id))

    db_session.expire_all()
    assert db_session.get(Rig, rig.id).active is False


def test_form_without_marker_leaves_an_active_rig_active(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    client.post('/add_rig', data=_rig_form_data(tel_id, cam_id, rig_name="Stay On"))
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="Stay On").one()
    assert rig.active is True

    client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Stay On", rig_id=rig.id))

    db_session.expire_all()
    assert db_session.get(Rig, rig.id).active is True


def test_get_rig_data_reports_active_for_each_rig(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    tel_id, cam_id = _make_components(db_session, user.id)

    client.post('/add_rig', data=_rig_form_data(tel_id, cam_id, rig_name="Live Rig"))
    client.post('/add_rig', data=_rig_form_data(
        tel_id, cam_id, rig_name="Off Rig", rig_active_present="1"))

    resp = client.get('/get_rig_data')
    assert resp.status_code == 200
    payload = resp.get_json()
    actives = {r["rig_name"]: r["active"] for r in payload["rigs"]}
    assert actives["Live Rig"] is True
    assert actives["Off Rig"] is False


# --- 5. rig_references -------------------------------------------------------

def test_rig_references_counts_sessions_and_framings(db_session):
    user = DbUser(username="refs_user")
    db_session.add(user)
    db_session.commit()
    rig = _make_linked_rig(db_session, user.id, "Refs")
    _link_session(db_session, user.id, rig.id)
    _link_framing(db_session, user.id, rig)

    refs = rig_references(db_session, user.id, rig.record_uid)

    assert (refs.sessions, refs.framings) == (1, 1)


def test_rig_references_without_uid_counts_nothing(db_session):
    user = DbUser(username="refs_empty")
    db_session.add(user)
    db_session.commit()

    assert rig_references(db_session, user.id, "") == (0, 0)
    assert rig_references(db_session, user.id, None) == (0, 0)


# --- 6. The delete guard -----------------------------------------------------

def test_delete_rig_blocked_while_a_session_refers_to_it(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Busy")
    session_id = _link_session(db_session, user.id, rig.id)

    resp = client.post('/delete_rig', data={'rig_id': rig.id}, follow_redirects=True)

    assert resp.status_code == 200
    assert "Cannot delete rig" in resp.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.get(Rig, rig.id) is not None
    assert db_session.get(JournalSession, session_id) is not None


def test_delete_rig_blocked_flash_names_the_session_count(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Two")
    _link_session(db_session, user.id, rig.id)
    _link_session(db_session, user.id, rig.id)

    resp = client.post('/delete_rig', data={'rig_id': rig.id}, follow_redirects=True)

    assert "used by 2 session(s)" in resp.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.get(Rig, rig.id) is not None


def test_delete_rig_blocked_even_when_the_rig_is_inactive(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Off", active=False)
    _link_session(db_session, user.id, rig.id)

    client.post('/delete_rig', data={'rig_id': rig.id}, follow_redirects=True)

    db_session.expire_all()
    assert db_session.get(Rig, rig.id) is not None


def test_delete_rig_with_only_framings_removes_them_with_the_rig(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Framed")
    other = _make_linked_rig(db_session, user.id, "Other")
    mine_id = _link_framing(db_session, user.id, rig)
    other_id = _link_framing(db_session, user.id, other, object_name="M31")

    resp = client.post('/delete_rig', data={'rig_id': rig.id}, follow_redirects=True)

    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.get(Rig, rig.id) is None
    assert db_session.get(SavedFraming, mine_id) is None
    assert db_session.get(SavedFraming, other_id) is not None


def test_delete_rig_with_nothing_linked_still_works(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Spare")

    resp = client.post('/delete_rig', data={'rig_id': rig.id}, follow_redirects=True)

    assert resp.status_code == 200
    assert "Rig deleted successfully." in resp.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.get(Rig, rig.id) is None


def test_delete_rig_leaves_another_users_rows_untouched(multi_user_client, monkeypatch, db_session):
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    client, ids = multi_user_client
    a_id, b_id = ids["user_a_id"], ids["user_b_id"]
    rig_a = _make_linked_rig(db_session, a_id, "A")
    rig_b = _make_linked_rig(db_session, b_id, "B")
    fa = _link_framing(db_session, a_id, rig_a)
    fb = _link_framing(db_session, b_id, rig_b)

    resp = client.post('/delete_rig', data={'rig_id': rig_a.id})

    assert resp.status_code == 302
    db_session.expire_all()
    assert db_session.get(Rig, rig_a.id) is None
    assert db_session.get(SavedFraming, fa) is None
    assert db_session.get(Rig, rig_b.id) is not None
    assert db_session.get(SavedFraming, fb) is not None


# --- 7. /get_rig_data reports both counts ------------------------------------

def test_get_rig_data_reports_the_session_and_framing_counts(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Counted")
    _link_session(db_session, user.id, rig.id)
    _link_framing(db_session, user.id, rig)

    payload = client.get('/get_rig_data').get_json()
    row = {r["rig_name"]: r for r in payload["rigs"]}["Counted Rig"]

    assert row["session_count"] == 1
    assert row["framing_count"] == 1
