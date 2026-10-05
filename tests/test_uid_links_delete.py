"""
record_uid links survive deletes (step 5 part 1, diff 8).

Agreed rule for deletes: a UID that points at a deleted record is left exactly
as it is. A delete does not clear it, and a later record with the same name or
the same row number does not re-link it. The old link columns keep today's
behaviour. A second user's rows are never changed.

Delete entry points under test:
- /delete_rig and /delete_component (nova/blueprints/tools.py)
- location delete and object delete inside /config_form (nova/blueprints/core.py)
"""
from datetime import date
from types import SimpleNamespace

import pytest

from nova.models import AstroObject, Component, DbUser, JournalSession, Location, Project, Rig, SavedFraming
from nova.record_links import (
    LINKS, NAME_LINKS, count_link_disagreements,
    sync_framing_links, sync_project_link, sync_rig_links, sync_session_links,
)

ZERO = {"wrong": 0, "missing": 0}
_LINKED_MODELS = (Rig, JournalSession, SavedFraming, Project)
_LOCATION_LINKS = tuple(link for link in LINKS if link.target == "locations")
_OBJECT_LINKS = tuple(link for link in LINKS if link.target == "astro_objects")


def _user(db, username):
    user = DbUser(username=username)
    db.add(user)
    db.commit()
    return user.id


def _populate(db, user_id, tag):
    """Objects M42 and M31, location Home, rig '<tag> Rig', and a session, project and framing on M42.

    Returns plain values: some routes close/expire rows, so nothing here relies on identity.
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


def _assert_links_clean(db, user_id, links=LINKS):
    db.expire_all()
    result = count_link_disagreements(db, user_id=user_id, links=links)
    assert result == {link.name: ZERO for link in links}, result


@pytest.fixture
def mu(multi_user_client, monkeypatch):
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    client, ids = multi_user_client
    return client, ids["user_a_id"], ids["user_b_id"]


# --- Route helpers --------------------------------------------------------------

def _delete_location_via_form(client, name, survivor, lat=40.0, lon=-100.0, tz="UTC"):
    """POST /config_form deleting `name` and keeping `survivor` active (the form needs its fields)."""
    return client.post('/config_form', data={
        'submit_locations': '1',
        f'delete_loc_{name}': 'on',
        f'active_{survivor}': 'on',
        f'lat_{survivor}': str(lat),
        f'lon_{survivor}': str(lon),
        f'timezone_{survivor}': tz,
    })


def _create_location_via_form(client, name, lat=48.2, lon=16.4, tz="Europe/Vienna"):
    return client.post('/config_form', data={
        'submit_new_location': '1', 'new_location': name,
        'new_lat': str(lat), 'new_lon': str(lon), 'new_timezone': tz, 'new_active': 'on',
    })


def _delete_object_via_form(client, name, keep_name, keep_ra, keep_dec):
    """POST /config_form deleting `name`; the surviving object's fields are required by the route."""
    return client.post('/config_form', data={
        'submit_objects': '1',
        f'delete_{name}': 'on',
        f'ra_{keep_name}': str(keep_ra),
        f'dec_{keep_name}': str(keep_dec),
    })


def _create_object(client, name, ra=5.6, dec=-5.4):
    return client.post('/confirm_object', json={"object": name, "name": name, "ra": ra, "dec": dec})


# --- 1 & 2: delete_rig ----------------------------------------------------------

def test_delete_rig_keeps_rig_uids_in_sessions_and_framings(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")
    a = _populate(db_session, a_id, "A")
    before_b = _snapshot(db_session, b_id)

    resp = client.post('/delete_rig', data={'rig_id': a.rig_id})
    assert resp.status_code == 302

    db_session.expire_all()
    assert db_session.get(Rig, a.rig_id) is None
    s = db_session.get(JournalSession, a.session_id)
    f = db_session.get(SavedFraming, a.framing_id)
    # Old columns keep today's behaviour: they still hold the deleted row number.
    assert (s.rig_id_snapshot, s.rig_record_uid) == (a.rig_id, a.rig_uid)
    assert (f.rig_id, f.rig_record_uid) == (a.rig_id, a.rig_uid)
    _assert_links_clean(db_session, a_id)
    assert _snapshot(db_session, b_id) == before_b


def test_new_rig_after_delete_does_not_relink_old_rows(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")  # B first, so A's rig is the highest row number
    a = _populate(db_session, a_id, "A")
    before_b = _snapshot(db_session, b_id)

    assert client.post('/delete_rig', data={'rig_id': a.rig_id}).status_code == 302
    db_session.expire_all()
    assert db_session.query(Rig).filter_by(user_id=a_id).count() == 0

    # Same rig name, and the freed row number is reused by the new rig.
    resp = client.post('/add_rig', data={'rig_name': 'A Rig', 'telescope_id': a.tel_id, 'camera_id': a.cam_id})
    assert resp.status_code == 302

    db_session.expire_all()
    new_rig = db_session.query(Rig).filter_by(user_id=a_id).one()
    assert new_rig.id == a.rig_id          # row number reused
    assert new_rig.record_uid != a.rig_uid
    s = db_session.get(JournalSession, a.session_id)
    f = db_session.get(SavedFraming, a.framing_id)
    assert (s.rig_id_snapshot, f.rig_id) == (new_rig.id, new_rig.id)  # old columns point at the new row
    # The UIDs still hold the OLD rig's UID, never the new rig's.
    assert s.rig_record_uid == a.rig_uid and s.rig_record_uid != new_rig.record_uid
    assert f.rig_record_uid == a.rig_uid and f.rig_record_uid != new_rig.record_uid
    _assert_links_clean(db_session, a_id)
    assert _snapshot(db_session, b_id) == before_b


# --- 3: delete_component --------------------------------------------------------

def test_delete_component_blocked_when_used_and_allowed_when_unused(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")
    a = _populate(db_session, a_id, "A")
    red = Component(user_id=a_id, kind="reducer_extender", name="A Red")
    free = Component(user_id=a_id, kind="camera", name="A Spare",
                     sensor_width_mm=1.0, sensor_height_mm=1.0, pixel_size_um=1.0)
    db_session.add_all([red, free])
    db_session.flush()
    rig_red = Rig(user_id=a_id, rig_name="A Red Rig", telescope_id=a.tel_id, camera_id=a.cam_id,
                  reducer_extender_id=red.id)
    sync_rig_links(db_session, rig_red)
    db_session.add(rig_red)
    db_session.commit()
    red_id, free_id = red.id, free.id
    before_rigs = _snapshot(db_session, a_id, models=(Rig,))
    before_b = _snapshot(db_session, b_id)

    # Used as telescope, camera and reducer: all three deletes are refused.
    for comp_id in (a.tel_id, a.cam_id, red_id):
        assert client.post('/delete_component', data={'component_id': comp_id}).status_code == 302
    db_session.expire_all()
    for comp_id in (a.tel_id, a.cam_id, red_id):
        assert db_session.get(Component, comp_id) is not None
    assert _snapshot(db_session, a_id, models=(Rig,)) == before_rigs

    # An unused component can be deleted; rig UIDs do not move.
    assert client.post('/delete_component', data={'component_id': free_id}).status_code == 302
    db_session.expire_all()
    assert db_session.get(Component, free_id) is None
    assert _snapshot(db_session, a_id, models=(Rig,)) == before_rigs
    _assert_links_clean(db_session, a_id)
    assert _snapshot(db_session, b_id) == before_b


# --- 4 & 6 (location): delete location, then recreate the same name -------------

def test_delete_location_keeps_session_links(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")
    a = _populate(db_session, a_id, "A")
    before_b = _snapshot(db_session, b_id)

    # The fixture gave A a second location ("UserA_Home"), so "Home" may be deleted.
    resp = _delete_location_via_form(client, "Home", "UserA_Home")
    assert resp.status_code == 302

    db_session.expire_all()
    assert db_session.query(Location).filter_by(user_id=a_id, name="Home").one_or_none() is None
    s = db_session.get(JournalSession, a.session_id)
    assert (s.location_name, s.location_record_uid) == ("Home", a.home_uid)
    _assert_links_clean(db_session, a_id, links=NAME_LINKS)
    assert _snapshot(db_session, b_id) == before_b


def test_new_location_same_name_does_not_relink_old_session(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")
    a = _populate(db_session, a_id, "A")
    before_b = _snapshot(db_session, b_id)

    assert _delete_location_via_form(client, "Home", "UserA_Home").status_code == 302
    assert _create_location_via_form(client, "Home").status_code == 302

    db_session.expire_all()
    new_home = db_session.query(Location).filter_by(user_id=a_id, name="Home").one()
    assert new_home.record_uid != a.home_uid  # a fresh record_uid
    s = db_session.get(JournalSession, a.session_id)
    assert (s.location_name, s.location_record_uid) == ("Home", a.home_uid)  # still the OLD UID
    # Observed reporter output (not guessed): see the assertion below.
    result = count_link_disagreements(db_session, user_id=a_id, links=_LOCATION_LINKS)
    assert result == {link.name: ZERO for link in _LOCATION_LINKS}, result
    assert _snapshot(db_session, b_id) == before_b


# --- 5 & 6 (object): delete object, then recreate the same name -----------------

def test_delete_object_keeps_session_project_framing_links(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")
    a = _populate(db_session, a_id, "A")
    before_b = _snapshot(db_session, b_id)

    resp = _delete_object_via_form(client, "M42", "M31", 0.7, 41.3)
    assert resp.status_code == 302

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is None
    s = db_session.get(JournalSession, a.session_id)
    p = db_session.get(Project, a.project_id)
    f = db_session.get(SavedFraming, a.framing_id)
    assert (s.object_name, s.object_record_uid) == ("M42", a.m42_uid)
    assert (p.target_object_name, p.target_object_record_uid) == ("M42", a.m42_uid)
    assert (f.object_name, f.object_record_uid) == ("M42", a.m42_uid)
    _assert_links_clean(db_session, a_id, links=NAME_LINKS)
    assert _snapshot(db_session, b_id) == before_b


def test_new_object_same_name_does_not_relink_old_links(mu, db_session):
    client, a_id, b_id = mu
    _populate(db_session, b_id, "B")
    a = _populate(db_session, a_id, "A")
    before_b = _snapshot(db_session, b_id)

    assert _delete_object_via_form(client, "M42", "M31", 0.7, 41.3).status_code == 302
    assert _create_object(client, "M42").status_code == 200

    db_session.expire_all()
    new_m42 = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    assert new_m42.record_uid != a.m42_uid  # a fresh record_uid
    s = db_session.get(JournalSession, a.session_id)
    p = db_session.get(Project, a.project_id)
    f = db_session.get(SavedFraming, a.framing_id)
    assert (s.object_name, s.object_record_uid) == ("M42", a.m42_uid)  # still the OLD UID
    assert (p.target_object_name, p.target_object_record_uid) == ("M42", a.m42_uid)
    assert (f.object_name, f.object_record_uid) == ("M42", a.m42_uid)
    # Observed reporter output (not guessed): see the assertion below.
    result = count_link_disagreements(db_session, user_id=a_id, links=_OBJECT_LINKS)
    assert result == {link.name: ZERO for link in _OBJECT_LINKS}, result
    assert _snapshot(db_session, b_id) == before_b
