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
