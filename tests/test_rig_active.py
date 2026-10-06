"""
Rig.active: the model default and the schema patch.

Covers the default on a fresh insert and the upgrade path for an existing
database whose rigs table predates the column, including idempotency.
No route, template, export or import reads the flag yet.
"""

import json
import os
import sys
from contextlib import contextmanager
from datetime import date

from flask import template_rendered
from sqlalchemy import create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import _run_schema_patches, app
from nova.models import AstroObject, Component, DbUser, JournalSession, Rig, SavedFraming
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


def _make_session_on_rig(db_session, user_id, rig, **snapshots):
    """A journal session linked to `rig`, with any extra *_snapshot columns set."""
    session = JournalSession(user_id=user_id, date_utc=date(2026, 2, 2),
                             object_name="M42", rig_id_snapshot=rig.id)
    sync_session_links(db_session, session)
    for key, value in snapshots.items():
        setattr(session, key, value)
    db_session.add(session)
    db_session.commit()
    return session


def _edit_form_html(client, session_id):
    return client.get(
        f'/graph_dashboard/M42?session_id={session_id}&edit=true'
    ).get_data(as_text=True)


def _rig_selector_block(html):
    """Only the #rig-selector-edit markup, from its id to the next </select>."""
    start = html.find('id="rig-selector-edit"')
    assert start != -1, "rig selector not found in the rendered page"
    end = html.find('</select>', start)
    assert end != -1, "closing </select> for the rig selector not found"
    return html[start:end]


def _framing_rig_select_block(html):
    """Only the #framing-rig-select markup, from its id to the next </select>."""
    start = html.find('id="framing-rig-select"')
    assert start != -1, "framing rig select not found in the rendered page"
    end = html.find('</select>', start)
    assert end != -1, "closing </select> for the framing rig select not found"
    return html[start:end]


@contextmanager
def _captured_templates():
    """Collect (template name, context) for every render during the block."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


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


# --- 8. Inactive rigs are hidden where rigs are only listed -------------------

def test_object_page_context_offers_active_rigs_and_keeps_available_rigs(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    _make_linked_rig(db_session, user.id, "Live")
    _make_linked_rig(db_session, user.id, "Off", active=False)

    with _captured_templates() as rendered:
        resp = client.get('/graph_dashboard/M42')

    assert resp.status_code == 200
    ctx = next(c for name, c in rendered if name == 'graph_view.html')
    assert {r["rig_name"] for r in ctx["available_rigs"]} == {"Live Rig", "Off Rig"}
    assert {r["rig_name"] for r in ctx["active_rigs"]} == {"Live Rig"}


def test_rig_data_tab_hides_inactive_rigs(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    db_session.query(AstroObject).filter_by(user_id=user.id, object_name="M42").one().size = "60'"
    db_session.commit()
    _make_linked_rig(db_session, user.id, "Live")
    _make_linked_rig(db_session, user.id, "Off", active=False)

    html = client.get('/graph_dashboard/M42').get_data(as_text=True)

    # <td><strong>…</strong></td> is the framing-table row markup from the Rig Data tab.
    assert "<td><strong>Live Rig</strong></td>" in html
    assert "<td><strong>Off Rig</strong></td>" not in html


def test_mobile_journal_form_offers_active_rigs_only(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    _make_linked_rig(db_session, user.id, "Live")
    _make_linked_rig(db_session, user.id, "Off", active=False)

    with _captured_templates() as rendered:
        resp = client.get('/m/journal/new')

    assert resp.status_code == 200
    ctx = next(c for name, c in rendered if name == 'mobile_journal_new.html')
    assert sorted(r.rig_name for r in ctx["rigs"]) == ["Live Rig"]


def test_ai_best_objects_uses_active_rigs_only(su_client_logged_in, db_session, monkeypatch):
    # The AI blueprint is only registered when AI_API_KEY is set, so call the view directly.
    import nova.ai.routes as ai_routes
    from flask import g

    user = _default_user(db_session)
    _make_linked_rig(db_session, user.id, "Live")
    _make_linked_rig(db_session, user.id, "Off", active=False)

    captured = {}

    def fake_prompt(**kwargs):
        captured["rigs"] = kwargs["rigs"]
        return {"system": "sys", "user": "usr"}

    monkeypatch.setattr(ai_routes, "user_has_ai_access", lambda username: True)
    monkeypatch.setattr(ai_routes, "build_best_objects_prompt", fake_prompt)
    monkeypatch.setattr(ai_routes, "get_ai_response",
                        lambda *a, **k: '[{"Object": "M42", "reason": "test"}]')

    with app.test_request_context('/api/ai/best_objects', method='POST', json={
        "object_list": [{
            "Object": "M42", "enabled": True,
            "Observable Duration (min)": 120, "Max Altitude (°)": 60,
            "Angular Separation (°)": 90, "Size": "60'", "Magnitude": 99,
        }],
        "location_name": "Default Test Loc",
    }):
        g.db_user = user
        resp = ai_routes.get_best_objects()

    assert resp.status_code == 200
    assert sorted(r["name"] for r in captured["rigs"]) == ["Live Rig"]


# --- 9. The journal rig selector ---------------------------------------------

def test_journal_rig_selector_keeps_the_sessions_own_inactive_rig(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Off", active=False)
    session = _make_session_on_rig(db_session, user.id, rig,
                                   rig_name_snapshot="Off Rig", rig_efl_snapshot=480.0)

    block = _rig_selector_block(_edit_form_html(client, session.id))

    assert f'<option value="{rig.id}" selected>Off Rig (Inactive)</option>' in block


def test_journal_rig_selector_lists_no_inactive_rig_for_an_active_session(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    live = _make_linked_rig(db_session, user.id, "Live")
    off = _make_linked_rig(db_session, user.id, "Off", active=False)
    session = _make_session_on_rig(db_session, user.id, live)

    block = _rig_selector_block(_edit_form_html(client, session.id))

    assert f'<option value="{live.id}" selected>Live Rig</option>' in block
    assert f'value="{off.id}"' not in block
    assert "Off Rig" not in block


def test_journal_rig_selector_never_lists_a_second_inactive_rig(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    off = _make_linked_rig(db_session, user.id, "Off", active=False)
    other_off = _make_linked_rig(db_session, user.id, "Other Off", active=False)
    session = _make_session_on_rig(db_session, user.id, off,
                                   rig_name_snapshot="Off Rig", rig_efl_snapshot=480.0)

    block = _rig_selector_block(_edit_form_html(client, session.id))

    assert f'<option value="{off.id}" selected>Off Rig (Inactive)</option>' in block
    assert f'value="{other_off.id}"' not in block
    assert "Other Off" not in block


def test_journal_edit_post_keeps_the_inactive_rig_and_its_snapshots(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    rig = _make_linked_rig(db_session, user.id, "Off", active=False)
    session = _make_session_on_rig(db_session, user.id, rig,
                                   rig_name_snapshot="Off Rig",
                                   rig_efl_snapshot=480.0, rig_fr_snapshot=6.0,
                                   rig_scale_snapshot=1.62,
                                   telescope_name_snapshot="Off Rig Scope")
    expected = (session.rig_record_uid, session.rig_name_snapshot, session.rig_efl_snapshot,
                session.rig_fr_snapshot, session.rig_scale_snapshot, session.telescope_name_snapshot)

    resp = client.post(f'/journal/edit/{session.id}', data={
        'session_date': '2026-02-02',
        'target_object_id': 'M42',
        'rig_id_snapshot': str(rig.id),
        'form_action': 'save_close',
    })

    assert resp.status_code == 302
    db_session.expire_all()
    s = db_session.get(JournalSession, session.id)
    assert (s.rig_record_uid, s.rig_name_snapshot, s.rig_efl_snapshot, s.rig_fr_snapshot,
            s.rig_scale_snapshot, s.telescope_name_snapshot) == expected


def test_framing_modal_rig_select_hides_inactive_rigs_without_a_saved_framing(
        su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    live = _make_linked_rig(db_session, user.id, "Live")
    off = _make_linked_rig(db_session, user.id, "Off", active=False)

    html = client.get('/graph_dashboard/M42').get_data(as_text=True)
    block = _framing_rig_select_block(html)

    assert f'value="{live.id}"' in block
    assert "Live Rig" in block
    assert f'value="{off.id}"' not in block
    assert "Off Rig" not in block


def test_framing_modal_keeps_the_data_the_restore_needs_for_an_inactive_rig(
        su_client_logged_in, db_session):
    """The framing's inactive rig is not rendered as an option (the restore adds
    it in JS), but window.availableRigs still carries what that needs."""
    client = su_client_logged_in
    user = _default_user(db_session)
    off = _make_linked_rig(db_session, user.id, "Off", active=False)
    _link_framing(db_session, user.id, off)

    html = client.get('/graph_dashboard/M42').get_data(as_text=True)
    block = _framing_rig_select_block(html)
    assert f'value="{off.id}"' not in block
    assert "Off Rig" not in block

    marker = 'window.availableRigs = '
    start = html.find(marker)
    assert start != -1, "availableRigs not found in the rendered page"
    start += len(marker)
    rigs = json.loads(html[start:html.find('];', start) + 1])
    entry = next(r for r in rigs if r["rig_id"] == off.id)
    assert entry["active"] is False
    assert entry["rig_uid"] == off.record_uid
    assert entry["rig_name"] == "Off Rig"
    assert entry["fov_w_arcmin"] and entry["fov_h_arcmin"]


def test_save_framing_with_an_inactive_rig_keeps_its_uid_and_name(
        su_client_logged_in, db_session):
    client = su_client_logged_in
    user = _default_user(db_session)
    off = _make_linked_rig(db_session, user.id, "Off", active=False)
    _make_linked_rig(db_session, user.id, "Live")
    framing_id = _link_framing(db_session, user.id, off)

    resp = client.post('/api/save_framing', json={
        "object_name": "M42",
        "rig": str(off.id),
        "ra": 83.8,
        "dec": -5.4,
        "rotation": 12.0,
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"

    db_session.expire_all()
    framing = db_session.get(SavedFraming, framing_id)
    assert framing.rig_id == off.id
    assert framing.rig_record_uid == off.record_uid
    assert framing.rig_name == "Off Rig"
