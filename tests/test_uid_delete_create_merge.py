"""
record_uid rules (step 5): D1 objects and locations, D3 components, D2 create,
D4 merge and rename.

D1: an object a session or project refers to by UID cannot be deleted; a location
a session refers to by UID cannot be deleted. A refused delete changes nothing.
D3: a component used in any of the five rig roles by UID cannot be deleted.
D2: a new object or location adopts this user's rows that name it and have an
empty UID; a row with a UID (even a dangling one), another name, another case or
another user is never touched. A bulk create adopts once per user, not per row.
D4: merge and rename move exactly the rows linked by UID, plus empty-UID rows
with the exact name, and rewrite their name text; a row already linked to the
kept object gets the kept name text too. A UID pointing elsewhere never moves.
A self-merge is refused. repair-corrupt-ids follows the same rules.
"""
from datetime import date

import pytest

from nova import app
from nova.migration import _migrate_locations, _migrate_objects, import_catalog_pack_for_user
from nova.models import (
    AstroObject, Component, DbUser, JournalSession, Location, Project, Rig, SavedFraming,
)
from nova.record_links import adopt_unlinked_rows_for_user, sync_project_link, sync_rig_links


# --- builders ----------------------------------------------------------------------

def _object(db, user_id, name="M42", **cols):
    o = AstroObject(user_id=user_id, object_name=name, ra_hours=5.6, dec_deg=-5.4)
    for key, value in cols.items():
        setattr(o, key, value)
    db.add(o)
    db.flush()
    return o


def _location(db, user_id, name="Home"):
    loc = Location(user_id=user_id, name=name, lat=48.2, lon=16.4, timezone="Europe/Vienna")
    db.add(loc)
    db.flush()
    return loc


def _session(db, user_id, obj=None, loc=None, name="M42"):
    s = JournalSession(user_id=user_id, object_name=name, date_utc=date(2026, 1, 10))
    if obj is not None:
        s.object_record_uid = obj.record_uid
    if loc is not None:
        s.location_name = loc.name
        s.location_record_uid = loc.record_uid
    db.add(s)
    db.flush()
    return s


def _project(db, user_id, obj=None, name="P1"):
    p = Project(id=f"proj-{name}-{user_id}", user_id=user_id, name=name)
    if obj is not None:
        p.target_object_name = obj.object_name
        p.target_object_record_uid = obj.record_uid
    db.add(p)
    db.flush()
    return p


def _framing(db, user_id, obj=None, name="M42"):
    f = SavedFraming(user_id=user_id, object_name=name, ra=83.8, dec=-5.4, rotation=0.0,
                     mosaic_cols=1, mosaic_rows=1, mosaic_overlap=10.0)
    if obj is not None:
        f.object_record_uid = obj.record_uid
    db.add(f)
    db.flush()
    return f


def _component(db, user_id, kind="telescope", name="Comp", **cols):
    c = Component(user_id=user_id, kind=kind, name=name, **cols)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def mu(multi_user_client):
    client, ids = multi_user_client
    return client, ids["user_a_id"], ids["user_b_id"]


# --- route helpers -----------------------------------------------------------------

def _delete_object_via_form(client, name, keep_name, keep_ra, keep_dec):
    return client.post('/config_form', data={
        'submit_objects': '1',
        f'delete_{name}': 'on',
        f'ra_{keep_name}': str(keep_ra),
        f'dec_{keep_name}': str(keep_dec),
    })


def _delete_location_via_form(client, name, survivor, lat=40.0, lon=-100.0, tz="UTC"):
    return client.post('/config_form', data={
        'submit_locations': '1',
        f'delete_loc_{name}': 'on',
        f'active_{survivor}': 'on',
        f'lat_{survivor}': str(lat),
        f'lon_{survivor}': str(lon),
        f'timezone_{survivor}': tz,
    })


# --- D1: object delete, config form -------------------------------------------------

def test_config_form_refuses_object_with_a_session(mu, db_session):
    client, a_id, b_id = mu
    obj = _object(db_session, a_id, "M42")
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)   # survivor needs fields
    s = _session(db_session, a_id, obj=obj)
    b_obj = _object(db_session, b_id, "M42")
    db_session.commit()

    assert _delete_object_via_form(client, "M42", "M31", 0.7, 41.3).status_code == 302

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is not None
    assert db_session.get(JournalSession, s.id).object_record_uid == obj.record_uid
    assert db_session.get(AstroObject, b_obj.id).record_uid == b_obj.record_uid  # B untouched


def test_config_form_refuses_object_with_a_project(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    p = _project(db_session, a_id, obj=obj)
    db_session.commit()

    assert _delete_object_via_form(client, "M42", "M31", 0.7, 41.3).status_code == 302

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is not None
    assert db_session.get(Project, p.id).target_object_record_uid == obj.record_uid


def test_config_form_refused_object_delete_leaves_its_framing_in_place(mu, db_session):
    """A refused delete changes nothing: the framing that referred to the object stays too."""
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    _session(db_session, a_id, obj=obj)                 # the session is what blocks the delete
    framing = _framing(db_session, a_id, obj=obj)
    db_session.commit()
    framing_id = framing.id

    assert _delete_object_via_form(client, "M42", "M31", 0.7, 41.3).status_code == 302

    db_session.expire_all()
    kept = db_session.get(SavedFraming, framing_id)
    assert kept is not None
    assert kept.object_record_uid == obj.record_uid


def test_config_form_deletes_an_unreferenced_object(mu, db_session):
    client, a_id, _ = mu
    _object(db_session, a_id, "M42")
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    db_session.commit()

    assert _delete_object_via_form(client, "M42", "M31", 0.7, 41.3).status_code == 302

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is None


def test_config_form_deletes_object_with_only_a_framing_and_the_framing(mu, db_session):
    """Q1: a framing alone does not block; it is deleted with the object."""
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    framing = _framing(db_session, a_id, obj=obj)
    db_session.commit()
    framing_id = framing.id

    assert _delete_object_via_form(client, "M42", "M31", 0.7, 41.3).status_code == 302

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is None
    assert db_session.get(SavedFraming, framing_id) is None


# --- D1: object delete, bulk API ----------------------------------------------------

def test_bulk_delete_skips_referenced_objects_and_deletes_free_ones(mu, db_session):
    client, a_id, _ = mu
    used = _object(db_session, a_id, "M42")
    _session(db_session, a_id, obj=used)
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    db_session.commit()

    resp = client.post('/api/bulk_update_objects',
                       json={"action": "delete", "object_ids": ["M42", "M31"]})
    assert resp.status_code == 200, resp.get_json()

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is not None
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M31").one_or_none() is None


# --- D1: location delete, config form -----------------------------------------------

def test_config_form_refuses_location_with_a_session(mu, db_session):
    client, a_id, b_id = mu
    home = _location(db_session, a_id, "Home")
    s = _session(db_session, a_id, loc=home)
    b_home = _location(db_session, b_id, "Home")
    db_session.commit()

    assert _delete_location_via_form(client, "Home", "UserA_Home").status_code == 302

    db_session.expire_all()
    assert db_session.query(Location).filter_by(user_id=a_id, name="Home").one_or_none() is not None
    assert db_session.get(JournalSession, s.id).location_record_uid == home.record_uid
    assert db_session.get(Location, b_home.id).record_uid == b_home.record_uid  # B untouched


def test_config_form_deletes_unreferenced_location(mu, db_session):
    client, a_id, _ = mu
    _location(db_session, a_id, "Home")
    db_session.commit()

    assert _delete_location_via_form(client, "Home", "UserA_Home").status_code == 302

    db_session.expire_all()
    assert db_session.query(Location).filter_by(user_id=a_id, name="Home").one_or_none() is None


# --- correction 1: a submit branch that blocks nothing still returns 302 ------------

def test_config_form_locations_only_submit_returns_302(mu, db_session):
    client, a_id, _ = mu
    _location(db_session, a_id, "Home")
    db_session.commit()

    resp = _delete_location_via_form(client, "Home", "UserA_Home")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith('/config_form')


def test_config_form_objects_only_submit_returns_302(mu, db_session):
    client, a_id, _ = mu
    _object(db_session, a_id, "M42")
    _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    db_session.commit()

    resp = _delete_object_via_form(client, "M42", "M31", 0.7, 41.3)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith('/config_form')


# --- D3: component delete, all five roles by UID ------------------------------------

@pytest.mark.parametrize("role", [
    "telescope_id", "camera_id", "reducer_extender_id",
    "guide_telescope_id", "guide_camera_id",
])
def test_delete_component_blocked_in_each_of_the_five_roles(mu, db_session, role):
    client, a_id, _ = mu
    comp = _component(db_session, a_id, name=f"Used {role}")
    rig = Rig(user_id=a_id, rig_name="R", **{role: comp.id})
    sync_rig_links(db_session, rig)   # the check goes by UID (D3)
    db_session.add(rig)
    db_session.commit()

    assert client.post('/delete_component', data={'component_id': comp.id}).status_code == 302

    db_session.expire_all()
    assert db_session.get(Component, comp.id) is not None


def test_delete_component_allowed_when_unused(mu, db_session):
    client, a_id, _ = mu
    comp = _component(db_session, a_id, name="Free", kind="camera",
                      sensor_width_mm=1.0, sensor_height_mm=1.0, pixel_size_um=1.0)
    db_session.commit()

    assert client.post('/delete_component', data={'component_id': comp.id}).status_code == 302

    db_session.expire_all()
    assert db_session.get(Component, comp.id) is None


# --- D2: adopt on create -----------------------------------------------------------

def _empty_project(db, user_id, target_name, name="P1"):
    p = Project(id=f"proj-{name}-{user_id}", user_id=user_id, name=name,
                target_object_name=target_name)
    db.add(p)
    db.flush()
    return p


def _create_location_via_form(client, name, lat=48.2, lon=16.4, tz="Europe/Vienna"):
    return client.post('/config_form', data={
        'submit_new_location': '1', 'new_location': name,
        'new_lat': str(lat), 'new_lon': str(lon), 'new_timezone': tz, 'new_active': 'on',
    })


def _new_object(client, name, ra=5.6, dec=-5.4):
    return client.post('/confirm_object', json={"object": name, "name": name, "ra": ra, "dec": dec})


def test_confirm_object_adopts_empty_uid_rows(mu, db_session):
    client, a_id, b_id = mu
    s = _session(db_session, a_id, name="M42")                 # empty UID
    p = _empty_project(db_session, a_id, "M42")                # empty UID
    f = _framing(db_session, a_id, name="M42")                 # empty UID
    b_s = _session(db_session, b_id, name="M42")               # another user
    db_session.commit()

    assert _new_object(client, "M42").status_code == 200

    db_session.expire_all()
    m42 = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    assert db_session.get(JournalSession, s.id).object_record_uid == m42.record_uid
    assert db_session.get(Project, p.id).target_object_record_uid == m42.record_uid
    assert db_session.get(SavedFraming, f.id).object_record_uid == m42.record_uid
    assert db_session.get(JournalSession, b_s.id).object_record_uid is None


def test_confirm_object_does_not_adopt_dangling_uid_or_other_names(mu, db_session):
    client, a_id, _ = mu
    dangling = _session(db_session, a_id, name="M42")
    dangling.object_record_uid = "deleted-uid"
    lower = _session(db_session, a_id, name="m42")             # different case
    db_session.commit()

    assert _new_object(client, "M42").status_code == 200

    db_session.expire_all()
    assert db_session.get(JournalSession, dangling.id).object_record_uid == "deleted-uid"
    assert db_session.get(JournalSession, lower.id).object_record_uid is None


def test_add_location_adopts_empty_uid_session(mu, db_session):
    client, a_id, _ = mu
    s = _session(db_session, a_id, name="M42")
    s.location_name = "NewHome"                                # empty UID, exact name
    db_session.commit()

    assert _create_location_via_form(client, "NewHome").status_code == 302

    db_session.expire_all()
    loc = db_session.query(Location).filter_by(user_id=a_id, name="NewHome").one()
    assert db_session.get(JournalSession, s.id).location_record_uid == loc.record_uid


def test_import_item_adopts_empty_uid_rows(mu, db_session):
    client, a_id, b_id = mu
    shared = AstroObject(user_id=b_id, object_name="M42", ra_hours=5.6, dec_deg=-5.4, is_shared=True)
    db_session.add(shared)
    db_session.flush()
    s = _session(db_session, a_id, name="M42")
    db_session.commit()

    resp = client.post('/api/import_item', json={"id": shared.id, "type": "object"})
    assert resp.status_code == 200, resp.get_json()

    db_session.expire_all()
    mine = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    assert db_session.get(JournalSession, s.id).object_record_uid == mine.record_uid


def test_migrate_objects_adopts_empty_uid_rows(mu, db_session):
    _, a_id, _b = mu
    user = db_session.get(DbUser, a_id)
    s = _session(db_session, a_id, name="M42")
    db_session.commit()

    _migrate_objects(db_session, user, {"objects": [{"Object": "M42", "RA": 5.6, "DEC": -5.4}]})
    db_session.commit()

    db_session.expire_all()
    obj = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    assert db_session.get(JournalSession, s.id).object_record_uid == obj.record_uid


def test_migrate_objects_adopts_several_rows_in_one_pass(mu, db_session):
    _, a_id, _b = mu
    user = db_session.get(DbUser, a_id)
    s1 = _session(db_session, a_id, name="M42")
    s2 = _session(db_session, a_id, name="M45")
    s3 = _session(db_session, a_id, name="M99")                # no such object
    db_session.commit()

    _migrate_objects(db_session, user, {"objects": [
        {"Object": "M42", "RA": 5.6, "DEC": -5.4},
        {"Object": "M45", "RA": 3.8, "DEC": 24.1},
    ]})
    db_session.commit()

    db_session.expire_all()
    m42 = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    m45 = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M45").one()
    assert db_session.get(JournalSession, s1.id).object_record_uid == m42.record_uid
    assert db_session.get(JournalSession, s2.id).object_record_uid == m45.record_uid
    assert db_session.get(JournalSession, s3.id).object_record_uid is None


def test_migrate_locations_adopts_empty_uid_session(mu, db_session):
    _, a_id, _b = mu
    user = db_session.get(DbUser, a_id)
    s = _session(db_session, a_id, name="M42")
    s.location_name = "Home"
    db_session.commit()

    _migrate_locations(db_session, user,
                       {"locations": {"Home": {"lat": 48.2, "lon": 16.4, "timezone": "UTC"}}})
    db_session.commit()

    db_session.expire_all()
    home = db_session.query(Location).filter_by(user_id=a_id, name="Home").one()
    assert db_session.get(JournalSession, s.id).location_record_uid == home.record_uid


def test_catalog_pack_import_adopts_empty_uid_rows(mu, db_session):
    _, a_id, _b = mu
    user = db_session.get(DbUser, a_id)
    s = _session(db_session, a_id, name="M42")
    db_session.commit()

    import_catalog_pack_for_user(
        db_session, user, {"objects": [{"Object": "M42", "RA": 5.6, "DEC": -5.4}]}, "testpack")
    db_session.commit()

    db_session.expire_all()
    obj = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one()
    assert db_session.get(JournalSession, s.id).object_record_uid == obj.record_uid


def test_guest_seed_adopts_empty_uid_rows(db_session):
    from nova import _seed_user_from_guest_data
    db = db_session
    guest = db.query(DbUser).filter_by(username="guest_user").one()
    db.add(AstroObject(user_id=guest.id, object_name="M42", ra_hours=5.6, dec_deg=-5.4))
    fresh = DbUser(username="fresh_seed_user")
    db.add(fresh)
    db.flush()
    s = _session(db, fresh.id, name="M42")
    db.commit()

    _seed_user_from_guest_data(db, fresh)
    db.commit()

    db.expire_all()
    obj = db.query(AstroObject).filter_by(user_id=fresh.id, object_name="M42").one()
    assert db.get(JournalSession, s.id).object_record_uid == obj.record_uid


def test_adopt_unlinked_rows_for_user_links_only_exact_empty_uid_rows(db_session):
    db = db_session
    u = DbUser(username="adopt_u")
    other = DbUser(username="adopt_other")
    db.add_all([u, other])
    db.flush()
    m42 = AstroObject(user_id=u.id, object_name="M42", ra_hours=5.6, dec_deg=-5.4)
    m31 = AstroObject(user_id=u.id, object_name="M31", ra_hours=0.7, dec_deg=41.3)
    home = Location(user_id=u.id, name="Home", lat=48.2, lon=16.4, timezone="UTC")
    db.add_all([m42, m31, home])
    db.flush()
    exact = _session(db, u.id, name="M42")
    located = _session(db, u.id, name="M42")
    located.location_name = "Home"
    dangling = _session(db, u.id, name="M31")
    dangling.object_record_uid = "deleted-uid"
    lower = _session(db, u.id, name="m42")                     # different case
    foreign = _session(db, other.id, name="M42")               # another user
    db.commit()

    adopt_unlinked_rows_for_user(db, u.id)
    db.commit()

    db.expire_all()
    assert db.get(JournalSession, exact.id).object_record_uid == m42.record_uid
    assert db.get(JournalSession, located.id).location_record_uid == home.record_uid
    assert db.get(JournalSession, dangling.id).object_record_uid == "deleted-uid"
    assert db.get(JournalSession, lower.id).object_record_uid is None
    assert db.get(JournalSession, foreign.id).object_record_uid is None


# --- D4: merge by UID ---------------------------------------------------------------

def test_merge_objects_moves_only_uid_linked_and_empty_uid_rows(mu, db_session):
    client, a_id, b_id = mu
    m42 = _object(db_session, a_id, "M42")
    m31 = _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    m45 = _object(db_session, a_id, "M45", ra_hours=3.8, dec_deg=24.1)
    linked = _session(db_session, a_id, obj=m42)
    empty = _session(db_session, a_id, name="M42")
    already_keep = _session(db_session, a_id, name="M42")      # UID already the kept object
    already_keep.object_record_uid = m31.record_uid
    elsewhere = _session(db_session, a_id, name="M42")
    elsewhere.object_record_uid = m45.record_uid
    b_row = _session(db_session, b_id, name="M42")
    proj = _project(db_session, a_id, obj=m42)
    empty_proj = _empty_project(db_session, a_id, "M42", name="P4")
    already_proj = _empty_project(db_session, a_id, "M42", name="P2")
    already_proj.target_object_record_uid = m31.record_uid
    other_proj = _empty_project(db_session, a_id, "M42", name="P3")
    other_proj.target_object_record_uid = m45.record_uid
    db_session.commit()

    resp = client.post('/api/merge_objects', json={"keep_id": "M31", "merge_id": "M42"})
    assert resp.status_code == 200, resp.get_json()

    db_session.expire_all()
    keep_uid = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M31").one().record_uid
    got = db_session.get(JournalSession, linked.id)
    assert (got.object_name, got.object_record_uid) == ("M31", keep_uid)
    got = db_session.get(JournalSession, empty.id)
    assert (got.object_name, got.object_record_uid) == ("M31", keep_uid)
    # Already linked to the kept object: the name text follows, the UID stays.
    got = db_session.get(JournalSession, already_keep.id)
    assert (got.object_name, got.object_record_uid) == ("M31", keep_uid)
    got = db_session.get(JournalSession, elsewhere.id)
    assert (got.object_name, got.object_record_uid) == ("M42", m45.record_uid)   # other UID: untouched
    assert db_session.get(Project, proj.id).target_object_record_uid == keep_uid
    assert db_session.get(Project, empty_proj.id).target_object_record_uid == keep_uid
    got = db_session.get(Project, already_proj.id)
    assert (got.target_object_name, got.target_object_record_uid) == ("M31", keep_uid)
    got = db_session.get(Project, other_proj.id)
    assert (got.target_object_name, got.target_object_record_uid) == ("M42", m45.record_uid)
    assert db_session.get(JournalSession, b_row.id).object_record_uid is None     # other user


def test_merge_objects_framing_conflict_keeps_kept_framing(mu, db_session):
    client, a_id, _ = mu
    m42 = _object(db_session, a_id, "M42")
    m31 = _object(db_session, a_id, "M31", ra_hours=0.7, dec_deg=41.3)
    keep_f = _framing(db_session, a_id, obj=m31, name="M31")
    merge_f = _framing(db_session, a_id, obj=m42, name="M42")
    db_session.commit()
    keep_id, merge_id = keep_f.id, merge_f.id

    assert client.post('/api/merge_objects',
                       json={"keep_id": "M31", "merge_id": "M42"}).status_code == 200

    db_session.expire_all()
    keep_uid = db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M31").one().record_uid
    kept = db_session.get(SavedFraming, keep_id)
    assert kept is not None and kept.object_record_uid == keep_uid
    assert db_session.get(SavedFraming, merge_id) is None


def test_merge_objects_refuses_self_merge(mu, db_session):
    client, a_id, _ = mu
    m42 = _object(db_session, a_id, "M42")
    s = _session(db_session, a_id, obj=m42)
    f = _framing(db_session, a_id, obj=m42)
    db_session.commit()

    resp = client.post('/api/merge_objects', json={"keep_id": "M42", "merge_id": "M42"})
    assert resp.status_code == 400

    db_session.expire_all()
    assert db_session.query(AstroObject).filter_by(user_id=a_id, object_name="M42").one_or_none() is not None
    assert db_session.get(JournalSession, s.id).object_record_uid == m42.record_uid
    assert db_session.get(SavedFraming, f.id).object_record_uid == m42.record_uid


# --- D4: repair-corrupt-ids ---------------------------------------------------------

def test_repair_rename_keeps_project_and_framing_links(db_session):
    db = db_session
    u = DbUser(username="repair_rename_u")
    db.add(u)
    db.flush()
    obj = AstroObject(user_id=u.id, object_name="SH2129", ra_hours=1, dec_deg=1)
    db.add(obj)
    db.flush()
    p = Project(id="rp1", user_id=u.id, name="P", target_object_name="SH2129",
                target_object_record_uid=obj.record_uid)
    f = SavedFraming(user_id=u.id, object_name="SH2129", object_record_uid=obj.record_uid,
                     ra=1, dec=1, rotation=0.0, mosaic_cols=1, mosaic_rows=1, mosaic_overlap=10.0)
    db.add_all([p, f])
    db.commit()
    obj_uid = obj.record_uid
    u_id = u.id                                  # the CLI closes the session: keep plain ids

    result = app.test_cli_runner().invoke(args=["repair-corrupt-ids"])
    assert result.exit_code == 0, result.output
    assert "FATAL" not in result.output, result.output

    db.expire_all()
    p2 = db.get(Project, "rp1")
    assert (p2.target_object_name, p2.target_object_record_uid) == ("SH 2-129", obj_uid)
    f2 = db.query(SavedFraming).filter_by(user_id=u_id).one()
    assert (f2.object_name, f2.object_record_uid) == ("SH 2-129", obj_uid)
    # Saving the project again keeps its link.
    sync_project_link(db, p2)
    assert p2.target_object_record_uid == obj_uid


def test_repair_merge_moves_rows_to_surviving_object(db_session):
    db = db_session
    u = DbUser(username="repair_merge_u")
    db.add(u)
    db.flush()
    corrupt = AstroObject(user_id=u.id, object_name="NGC1976", ra_hours=5.6, dec_deg=-5.4)
    correct = AstroObject(user_id=u.id, object_name="NGC 1976", ra_hours=5.6, dec_deg=-5.4)
    db.add_all([corrupt, correct])
    db.flush()
    linked = JournalSession(user_id=u.id, date_utc=date(2026, 1, 1), object_name="NGC1976",
                            object_record_uid=corrupt.record_uid)
    already = JournalSession(user_id=u.id, date_utc=date(2026, 1, 3), object_name="NGC1976",
                             object_record_uid=correct.record_uid)  # already on the survivor
    empty = JournalSession(user_id=u.id, date_utc=date(2026, 1, 2), object_name="NGC1976")
    db.add_all([linked, already, empty])
    db.commit()
    correct_uid = correct.record_uid
    already_id = already.id
    linked_id, empty_id = linked.id, empty.id   # the CLI closes the session: keep plain ids

    result = app.test_cli_runner().invoke(args=["repair-corrupt-ids"])
    assert result.exit_code == 0, result.output

    db.expire_all()
    for sid in (linked_id, empty_id):
        row = db.get(JournalSession, sid)
        assert (row.object_name, row.object_record_uid) == ("NGC 1976", correct_uid)
    row = db.get(JournalSession, already_id)
    assert (row.object_name, row.object_record_uid) == ("NGC 1976", correct_uid)
