"""
Journal session write paths set the object, location and rig UID columns
(diff 3, journal sessions only).

journal_add, journal_edit and mobile_journal_new call sync_session_links
after writing object_name, location_name and rig_id_snapshot. Those columns
are written exactly as before; the UIDs follow them. journal_duplicate is
unchanged and copies the UID columns with the rest of the row.
"""
import pytest

from nova.models import AstroObject, Component, JournalSession, Location, Rig
from nova.record_links import LINKS, count_link_disagreements


SESSION_LINKS = tuple(link for link in LINKS if link.table == "journal_sessions")
ZERO = {"wrong": 0, "missing": 0}


def _assert_session_links_clean(db):
    result = count_link_disagreements(db, links=SESSION_LINKS)
    assert result == {link.name: ZERO for link in SESSION_LINKS}, result


def _old(s):
    return (s.object_name, s.location_name, s.rig_id_snapshot)


def _uids(s):
    return (s.object_record_uid, s.location_record_uid, s.rig_record_uid)


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
    """User A: two objects, two locations, two rigs. User B: one rig."""
    client, ids = multi_user_client
    a, b = ids["user_a_id"], ids["user_b_id"]
    home = db_session.query(Location).filter_by(user_id=a, name="UserA_Home").one()
    away = Location(user_id=a, name="UserA_Away", lat=10, lon=20, timezone="UTC")
    m42 = AstroObject(user_id=a, object_name="M 42", ra_hours=5.6, dec_deg=-5.4)
    m31 = AstroObject(user_id=a, object_name="M 31", ra_hours=0.7, dec_deg=41.3)
    db_session.add_all([away, m42, m31])
    rig1, rig2 = _rig(db_session, a, "A1"), _rig(db_session, a, "A2")
    rig_b = _rig(db_session, b, "B")
    db_session.commit()

    class W:
        pass
    world = W()
    world.client, world.db, world.a, world.b = client, db_session, a, b
    world.home, world.away, world.m42, world.m31 = home, away, m42, m31
    world.rig1, world.rig2, world.rig_b = rig1, rig2, rig_b
    return world


def _form(obj="", loc=None, rig=None, **extra):
    data = {'session_date': "2026-01-10", 'target_object_id': obj,
            'rig_id_snapshot': "" if rig is None else str(rig)}
    if loc is not None:
        data['location_name'] = loc
    data.update(extra)
    return data


def _only_session(w):
    w.db.expire_all()
    return w.db.query(JournalSession).filter_by(user_id=w.a).one()


def _add(w, **kw):
    resp = w.client.post('/journal/add', data=_form(**kw))
    assert resp.status_code == 302
    return _only_session(w)


def _edit(w, session_id, **kw):
    resp = w.client.post(f'/journal/edit/{session_id}', data=_form(**kw))
    assert resp.status_code == 302
    w.db.expire_all()
    return w.db.get(JournalSession, session_id)


def _mobile_new(w, **kw):
    resp = w.client.post('/m/journal/new', data=_form(**kw))
    assert resp.status_code == 302
    return _only_session(w)


CREATE_ROUTES = [_add, _mobile_new]


# --- Create: journal_add and mobile_journal_new ---------------------------------

@pytest.mark.parametrize("create", CREATE_ROUTES, ids=["journal_add", "mobile_new"])
def test_create_sets_all_three_uids(w, create):
    s = create(w, obj=" M 42 ", loc="UserA_Home", rig=w.rig1.id)

    assert _old(s) == ("M 42", "UserA_Home", w.rig1.id)
    assert _uids(s) == (w.m42.record_uid, w.home.record_uid, w.rig1.record_uid)
    assert all(_uids(s))
    _assert_session_links_clean(w.db)


@pytest.mark.parametrize("create", CREATE_ROUTES, ids=["journal_add", "mobile_new"])
def test_create_with_empty_links(w, create):
    s = create(w)

    assert _old(s) == ("", None, None)
    assert _uids(s) == (None, None, None)
    _assert_session_links_clean(w.db)


@pytest.mark.parametrize("create", CREATE_ROUTES, ids=["journal_add", "mobile_new"])
def test_create_with_other_users_rig_gives_no_rig_uid(w, create):
    s = create(w, obj="M 42", loc="UserA_Home", rig=w.rig_b.id)

    assert s.rig_id_snapshot is None  # route refuses another user's rig, as before
    assert s.rig_record_uid is None
    assert s.rig_record_uid != w.rig_b.record_uid
    assert (s.object_record_uid, s.location_record_uid) == (w.m42.record_uid, w.home.record_uid)
    _assert_session_links_clean(w.db)


@pytest.mark.parametrize("create", CREATE_ROUTES, ids=["journal_add", "mobile_new"])
@pytest.mark.parametrize("obj,loc", [("NGC 9999", "Nowhere"), ("m 42", "userA_home"), ("M  42", "UserA_Home ")])
def test_create_with_unmatched_names_gives_no_uid(w, create, obj, loc):
    s = create(w, obj=obj, loc=loc, rig=w.rig1.id)

    assert _old(s) == (obj.strip(), loc, w.rig1.id)
    assert (s.object_record_uid, s.location_record_uid) == (None, None)
    assert s.rig_record_uid == w.rig1.record_uid
    _assert_session_links_clean(w.db)


# --- journal_edit ------------------------------------------------------------------

def test_edit_sets_changes_and_clears(w):
    s = _add(w)
    sid = s.id
    assert _uids(s) == (None, None, None)

    # Set.
    s = _edit(w, sid, obj="M 42", loc="UserA_Home", rig=w.rig1.id)
    assert _old(s) == ("M 42", "UserA_Home", w.rig1.id)
    assert _uids(s) == (w.m42.record_uid, w.home.record_uid, w.rig1.record_uid)
    _assert_session_links_clean(w.db)

    # Change.
    s = _edit(w, sid, obj="M 31", loc="UserA_Away", rig=w.rig2.id)
    assert _old(s) == ("M 31", "UserA_Away", w.rig2.id)
    assert _uids(s) == (w.m31.record_uid, w.away.record_uid, w.rig2.record_uid)
    _assert_session_links_clean(w.db)

    # Clear.
    s = _edit(w, sid)
    assert _old(s) == ("", None, None)
    assert _uids(s) == (None, None, None)
    _assert_session_links_clean(w.db)


def test_edit_to_other_users_rig_clears_rig_uid(w):
    s = _add(w, obj="M 42", loc="UserA_Home", rig=w.rig1.id)
    assert s.rig_record_uid == w.rig1.record_uid

    s = _edit(w, s.id, obj="M 42", loc="UserA_Home", rig=w.rig_b.id)

    assert s.rig_id_snapshot is None
    assert s.rig_record_uid is None
    _assert_session_links_clean(w.db)


def test_edit_to_unmatched_names_clears_uids(w):
    s = _add(w, obj="M 42", loc="UserA_Home", rig=w.rig1.id)
    assert s.object_record_uid and s.location_record_uid

    s = _edit(w, s.id, obj="NGC 9999", loc="Nowhere", rig=w.rig1.id)

    assert _old(s) == ("NGC 9999", "Nowhere", w.rig1.id)
    assert _uids(s) == (None, None, w.rig1.record_uid)
    _assert_session_links_clean(w.db)


# --- journal_duplicate (unchanged) -------------------------------------------------

def test_duplicate_copies_uids(w):
    src = _add(w, obj="M 42", loc="UserA_Home", rig=w.rig1.id)
    src_id = src.id

    resp = w.client.post(f'/journal/duplicate/{src_id}')
    assert resp.status_code == 302

    w.db.expire_all()
    src = w.db.get(JournalSession, src_id)
    copy = w.db.query(JournalSession).filter(JournalSession.user_id == w.a,
                                             JournalSession.id != src_id).one()
    assert _old(copy) == _old(src)
    assert _uids(copy) == _uids(src)
    assert all(_uids(copy))
    _assert_session_links_clean(w.db)
