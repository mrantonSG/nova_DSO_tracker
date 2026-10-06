"""
Rig.active: the model default and the schema patch.

Covers the default on a fresh insert and the upgrade path for an existing
database whose rigs table predates the column, including idempotency.
No route, template, export or import reads the flag yet.
"""

import os
import sys

from sqlalchemy import create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import _run_schema_patches
from nova.models import Component, DbUser, Rig

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
