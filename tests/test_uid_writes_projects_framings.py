"""
Project target and saved framing write paths set their UID columns
(diff 4, project targets and saved framings).

Every write to projects.target_object_name calls sync_project_link after it;
save_framing calls sync_framing_links once rig_id and object_name are final.
The old columns are written exactly as before; the UIDs follow them.
"""
from datetime import date

import pytest

from nova.models import AstroObject, Component, JournalSession, Project, Rig, SavedFraming
from nova.record_links import LINKS, count_link_disagreements


CHECKED_LINKS = tuple(link for link in LINKS if link.table in ("projects", "saved_framings"))
ZERO = {"wrong": 0, "missing": 0}


def _assert_links_clean(db):
    result = count_link_disagreements(db, links=CHECKED_LINKS)
    assert result == {link.name: ZERO for link in CHECKED_LINKS}, result


def _rig(db, user_id, tag):
    tel = Component(user_id=user_id, kind="telescope", name=f"{tag} Scope", aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=user_id, kind="camera", name=f"{tag} Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db.add_all([tel, cam])
    db.flush()
    rig = Rig(user_id=user_id, rig_name=f"{tag} Rig", telescope_id=tel.id, camera_id=cam.id)
    db.add(rig)
    return rig


@pytest.fixture
def w(multi_user_client, db_session):
    """User A: objects M 42 and M 31, two rigs. User B: an object M 42 and a rig."""
    client, ids = multi_user_client
    a, b = ids["user_a_id"], ids["user_b_id"]
    m42 = AstroObject(user_id=a, object_name="M 42", ra_hours=5.6, dec_deg=-5.4)
    m31 = AstroObject(user_id=a, object_name="M 31", ra_hours=0.7, dec_deg=41.3)
    b_m42 = AstroObject(user_id=b, object_name="M 42", ra_hours=5.6, dec_deg=-5.4)
    db_session.add_all([m42, m31, b_m42])
    rig1, rig2 = _rig(db_session, a, "A1"), _rig(db_session, a, "A2")
    rig_b = _rig(db_session, b, "B")
    db_session.commit()

    class W:
        pass
    world = W()
    world.client, world.db, world.a, world.b = client, db_session, a, b
    world.m42, world.m31, world.b_m42 = m42, m31, b_m42
    world.rig1, world.rig2, world.rig_b = rig1, rig2, rig_b
    return world


def _project(w, name):
    w.db.expire_all()
    return w.db.query(Project).filter_by(user_id=w.a, name=name).one()


def _target(p):
    return (p.target_object_name, p.target_object_record_uid)


def _session(w, obj="M 42"):
    s = JournalSession(user_id=w.a, object_name=obj, date_utc=date(2026, 1, 1))
    w.db.add(s)
    w.db.commit()
    return s


# --- _get_or_create_named_project (journal_add / journal_edit "new project") ------

def _journal_add_new_project(w, obj, name="Named P"):
    resp = w.client.post('/journal/add', data={
        'session_date': "2026-01-10", 'target_object_id': obj,
        'project_selection': "new_project", 'new_project_name': name,
    })
    assert resp.status_code == 302
    return _project(w, name)


def test_named_project_helper_sets_target(w):
    p = _journal_add_new_project(w, " M 42 ")
    assert _target(p) == ("M 42", w.m42.record_uid)
    assert p.target_object_record_uid != w.b_m42.record_uid
    _assert_links_clean(w.db)


def test_named_project_helper_without_target(w):
    p = _journal_add_new_project(w, "")
    assert _target(p) == (None, None)
    _assert_links_clean(w.db)


def test_named_project_helper_unmatched_name(w):
    p = _journal_add_new_project(w, "NGC 9999")
    assert _target(p) == ("NGC 9999", None)
    _assert_links_clean(w.db)


def test_named_project_helper_from_journal_edit(w):
    s = _session(w, "M 31")
    resp = w.client.post(f'/journal/edit/{s.id}', data={
        'session_date': "2026-01-10", 'target_object_id': "M 31",
        'project_selection': "new_project", 'new_project_name': "Edit New P",
    })
    assert resp.status_code == 302
    p = _project(w, "Edit New P")
    assert _target(p) == ("M 31", w.m31.record_uid)
    _assert_links_clean(w.db)


# --- journal_edit: loop over selected existing projects ---------------------------

def _edit_with_project(w, session_id, obj, project_id):
    resp = w.client.post(f'/journal/edit/{session_id}', data={
        'session_date': "2026-01-10", 'target_object_id': obj, 'project_selection': project_id,
    })
    assert resp.status_code == 302


def test_journal_edit_selected_project_set_and_changed(w):
    p = Project(id="p" * 32, user_id=w.a, name="Loop P")
    w.db.add(p)
    s = _session(w)

    # Set.
    _edit_with_project(w, s.id, "M 42", p.id)
    assert _target(_project(w, "Loop P")) == ("M 42", w.m42.record_uid)
    _assert_links_clean(w.db)

    # Change.
    _edit_with_project(w, s.id, "M 31", p.id)
    assert _target(_project(w, "Loop P")) == ("M 31", w.m31.record_uid)
    _assert_links_clean(w.db)

    # Unmatched name.
    _edit_with_project(w, s.id, "NGC 9999", p.id)
    assert _target(_project(w, "Loop P")) == ("NGC 9999", None)
    _assert_links_clean(w.db)


def test_journal_edit_empty_object_leaves_project_target(w):
    """The loop only writes when the session has an object, as before: nothing to clear."""
    p = Project(id="q" * 32, user_id=w.a, name="Keep P", target_object_name="M 42")
    w.db.add(p)
    s = _session(w)
    _edit_with_project(w, s.id, "M 42", p.id)
    assert _target(_project(w, "Keep P")) == ("M 42", w.m42.record_uid)

    _edit_with_project(w, s.id, "", p.id)

    assert _target(_project(w, "Keep P")) == ("M 42", w.m42.record_uid)
    _assert_links_clean(w.db)


# --- add_project_from_journal ----------------------------------------------------------

def _add_project(w, name, obj):
    data = {'name': name, 'status': "Planned", 'target_object_id': obj}
    resp = w.client.post('/journal/add_project', data=data)
    assert resp.status_code == 302
    return _project(w, name)


@pytest.mark.parametrize("obj,expected", [
    ("M 42", "M 42"), ("", ""), ("NGC 9999", "NGC 9999"), ("m 42", "m 42"),
])
def test_add_project_from_journal(w, obj, expected):
    p = _add_project(w, "AddP", obj)
    assert p.target_object_name == expected
    assert p.target_object_record_uid == (w.m42.record_uid if expected == "M 42" else None)
    _assert_links_clean(w.db)


# --- mobile_journal_new "new project" -------------------------------------------------

@pytest.mark.parametrize("obj,expected_name", [(" M 42 ", "M 42"), ("", None), ("NGC 9999", "NGC 9999")])
def test_mobile_new_project_target(w, obj, expected_name):
    resp = w.client.post('/m/journal/new', data={
        'session_date': "2026-01-10", 'target_object_id': obj,
        'project_selection': "new_project", 'new_project_name': "Mobile P",
    })
    assert resp.status_code == 302
    p = _project(w, "Mobile P")
    assert p.target_object_name == expected_name
    assert p.target_object_record_uid == (w.m42.record_uid if expected_name == "M 42" else None)
    _assert_links_clean(w.db)


# --- project_detail POST (project edit) -------------------------------------------------

def _edit_project(w, project_id, obj=None):
    data = {'name': "Edit P", 'status': "Planned"}
    if obj is not None:
        data['target_object_id'] = obj
    resp = w.client.post(f'/project/{project_id}', data=data)
    assert resp.status_code == 302
    return _project(w, "Edit P")


def test_project_edit_sets_changes_and_clears(w):
    p = Project(id="e" * 32, user_id=w.a, name="Edit P")
    w.db.add(p)
    w.db.commit()

    # Set.
    assert _target(_edit_project(w, p.id, "M 42")) == ("M 42", w.m42.record_uid)
    _assert_links_clean(w.db)

    # Change.
    assert _target(_edit_project(w, p.id, "M 31")) == ("M 31", w.m31.record_uid)
    _assert_links_clean(w.db)

    # Unmatched name.
    assert _target(_edit_project(w, p.id, "NGC 9999")) == ("NGC 9999", None)
    _assert_links_clean(w.db)

    # Set again, then clear by leaving the field out.
    assert _target(_edit_project(w, p.id, "M 42")) == ("M 42", w.m42.record_uid)
    assert _target(_edit_project(w, p.id)) == (None, None)
    _assert_links_clean(w.db)

    # Empty field.
    assert _target(_edit_project(w, p.id, "")) == ("", None)
    _assert_links_clean(w.db)


# --- save_framing --------------------------------------------------------------------

def _save_framing(w, obj, rig=None):
    payload = {'object_name': obj, 'ra': 83.8, 'dec': -5.4, 'rotation': 0}
    if rig is not None:
        payload['rig'] = str(rig)
    resp = w.client.post('/api/save_framing', json=payload)
    assert resp.status_code == 200, resp.get_json()
    w.db.expire_all()
    return w.db.query(SavedFraming).filter_by(user_id=w.a, object_name=obj).one()


def _framing(f):
    return (f.rig_id, f.rig_record_uid, f.object_name, f.object_record_uid)


def test_save_framing_own_rig(w):
    f = _save_framing(w, "M 42", w.rig1.id)
    assert _framing(f) == (w.rig1.id, w.rig1.record_uid, "M 42", w.m42.record_uid)
    assert f.rig_name == "A1 Rig"
    _assert_links_clean(w.db)


def test_save_framing_no_rig(w):
    f = _save_framing(w, "M 42")
    assert _framing(f) == (None, None, "M 42", w.m42.record_uid)
    _assert_links_clean(w.db)


def test_save_framing_other_users_rig_stored_as_none(w):
    f = _save_framing(w, "M 42", w.rig_b.id)
    assert (f.rig_id, f.rig_record_uid, f.rig_name) == (None, None, None)
    assert f.object_record_uid == w.m42.record_uid
    _assert_links_clean(w.db)


def test_save_framing_unmatched_object(w):
    f = _save_framing(w, "NGC 9999", w.rig1.id)
    assert _framing(f) == (w.rig1.id, w.rig1.record_uid, "NGC 9999", None)
    _assert_links_clean(w.db)


def test_save_framing_later_save_updates_rig_uid(w):
    f = _save_framing(w, "M 42", w.rig1.id)
    fid = f.id
    assert f.object_record_uid == w.m42.record_uid  # first save sets the object UID

    f = _save_framing(w, "M 42", w.rig2.id)
    assert f.id == fid
    assert _framing(f) == (w.rig2.id, w.rig2.record_uid, "M 42", w.m42.record_uid)
    _assert_links_clean(w.db)

    f = _save_framing(w, "M 42")
    assert f.id == fid
    assert _framing(f) == (None, None, "M 42", w.m42.record_uid)
    _assert_links_clean(w.db)
