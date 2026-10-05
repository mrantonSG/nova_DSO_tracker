"""
record_uid delete rules (step 5 part 5a): D1 objects and locations, D3 components.

D1: an object a session or project refers to by UID cannot be deleted; a location
a session refers to by UID cannot be deleted. A refused delete changes nothing.
D3: a component used in any of the five rig roles by UID cannot be deleted.
The D2 (create) and D4 (merge/rename) cases follow in part 5b.
"""
from datetime import date

import pytest

from nova.models import (
    AstroObject, Component, JournalSession, Location, Project, Rig, SavedFraming,
)
from nova.record_links import sync_rig_links


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
