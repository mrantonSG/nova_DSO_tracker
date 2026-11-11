"""
The mobile journal form's rig select: each option carries the rig's row id,
and posting that value stores the rig on the new session.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova.models import Component, DbUser, JournalSession, Rig
from nova.record_links import sync_rig_links


def _make_rig(db_session, user_id):
    tel = Component(user_id=user_id, kind="telescope", name="Mobile Scope",
                    aperture_mm=100.0, focal_length_mm=500.0)
    cam = Component(user_id=user_id, kind="camera", name="Mobile Cam",
                    sensor_width_mm=20.0, sensor_height_mm=15.0, pixel_size_um=3.0)
    db_session.add_all([tel, cam])
    db_session.flush()
    rig = Rig(user_id=user_id, rig_name="Mobile Rig", telescope_id=tel.id, camera_id=cam.id)
    sync_rig_links(db_session, rig)
    db_session.add(rig)
    db_session.commit()
    return rig


def _rig_option_values(html):
    """Option values inside the #rig_id_snapshot select, placeholder included."""
    start = html.find('id="rig_id_snapshot"')
    assert start != -1, "rig select not found in the rendered page"
    end = html.find('</select>', start)
    assert end != -1, "closing </select> for the rig select not found"
    return re.findall(r'<option value="([^"]*)"', html[start:end])


def test_mobile_journal_rig_option_value_is_the_rig_id_and_saves_the_rig(
        su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    rig = _make_rig(db_session, user.id)

    resp = client.get('/m/journal/new')
    assert resp.status_code == 200
    values = _rig_option_values(resp.get_data(as_text=True))
    assert values == ["", str(rig.id)]

    resp = client.post('/m/journal/new', data={
        'session_date': "2026-01-10",
        'target_object_id': "M42",
        'rig_id_snapshot': values[1],
    })
    assert resp.status_code == 302

    db_session.expire_all()
    session = db_session.query(JournalSession).filter_by(user_id=user.id).one()
    assert session.rig_id_snapshot == rig.id
    assert session.rig_record_uid == rig.record_uid
    assert session.rig_name_snapshot == "Mobile Rig"


def test_mobile_quick_entry_links_its_project_in_session_projects(
        su_client_logged_in, db_session):
    """The quick entry used to set project_id only. Every reader, and the sync
    export, goes through session_projects, so the link must be written too —
    through the ORM, in the same stamped write as the session."""
    from sqlalchemy import text
    from nova.models import Project
    from nova.sync_engine import counter_rev, export_changes

    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.add(Project(id="veil", user_id=user.id, name="Veil", status="In Progress"))
    db_session.commit()
    rev_before = counter_rev(db_session, user.id)

    resp = client.post('/m/journal/new', data={
        'session_date': "2026-01-10", 'target_object_id': "M42",
        'project_selection': "veil"})
    assert resp.status_code == 302

    db_session.expire_all()
    session = db_session.query(JournalSession).filter_by(user_id=user.id).one()
    assert session.project_id == "veil"
    assert [p.id for p in session.projects] == ["veil"]
    links = db_session.execute(text(
        "SELECT project_id FROM session_projects WHERE session_id = :s"),
        {"s": session.id}).fetchall()
    assert [row[0] for row in links] == ["veil"]
    # One stamped write, and the link is in what a sync would send.
    assert session.sync_rev is not None and session.sync_rev > rev_before
    payload = export_changes(db_session, user, rev_before, side="local", with_files=False)
    assert [s["project_ids"] for s in payload["records"]["journal_sessions"]] == [["veil"]]


def test_mobile_quick_entry_links_a_project_it_creates(su_client_logged_in, db_session):
    from nova.models import Project
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()

    resp = client.post('/m/journal/new', data={
        'session_date': "2026-01-10", 'target_object_id': "M42",
        'project_selection': "new_project", 'new_project_name': "Orion wide"})
    assert resp.status_code == 302

    db_session.expire_all()
    project = db_session.query(Project).filter_by(user_id=user.id, name="Orion wide").one()
    session = db_session.query(JournalSession).filter_by(user_id=user.id).one()
    assert session.project_id == project.id
    assert [p.id for p in session.projects] == [project.id]


def test_mobile_quick_entry_never_links_another_users_project(
        su_client_logged_in, db_session):
    from nova.models import Project
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    other = DbUser(username="someone-else")
    db_session.add(other)
    db_session.commit()
    db_session.add(Project(id="theirs", user_id=other.id, name="Theirs", status="In Progress"))
    db_session.commit()

    client.post('/m/journal/new', data={
        'session_date': "2026-01-10", 'target_object_id': "M42",
        'project_selection': "theirs"})

    db_session.expire_all()
    session = db_session.query(JournalSession).filter_by(user_id=user.id).one()
    assert session.project_id is None
    assert session.projects == []


def test_mobile_standalone_entry_has_no_project_link(su_client_logged_in, db_session):
    client = su_client_logged_in
    user = db_session.query(DbUser).filter_by(username="default").one()
    client.post('/m/journal/new', data={
        'session_date': "2026-01-10", 'target_object_id': "M42",
        'project_selection': "standalone"})
    db_session.expire_all()
    session = db_session.query(JournalSession).filter_by(user_id=user.id).one()
    assert session.project_id is None and session.projects == []
