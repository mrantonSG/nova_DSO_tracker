"""
Rig write paths set the five component UID columns (diff 2, equipment only).

add_rig, the rig import (_migrate_components_and_rigs) and the guest seed
call sync_rig_links after writing a rig's *_id columns. The *_id columns are
written exactly as before; the UIDs follow them.
"""
import io

import pytest
import yaml

from nova import _seed_user_from_guest_data
from nova.migration import _migrate_components_and_rigs
from nova.models import Component, DbUser, Rig
from nova.record_links import LINKS, count_link_disagreements, sync_rig_links


ID_COLS = ("telescope_id", "camera_id", "reducer_extender_id", "guide_telescope_id", "guide_camera_id")
UID_COLS = ("telescope_record_uid", "camera_record_uid", "reducer_extender_record_uid",
            "guide_telescope_record_uid", "guide_camera_record_uid")
RIG_LINKS = tuple(link for link in LINKS if link.table == "rigs")
ZERO = {"wrong": 0, "missing": 0}


def _ids(rig):
    return tuple(getattr(rig, c) for c in ID_COLS)


def _uids(rig):
    return tuple(getattr(rig, c) for c in UID_COLS)


def _expected_uids(db, rig):
    """UID of each slot's component if it is the rig owner's, else None."""
    out = []
    for comp_id in _ids(rig):
        comp = db.get(Component, comp_id) if comp_id is not None else None
        out.append(comp.record_uid if comp is not None and comp.user_id == rig.user_id else None)
    return tuple(out)


def _assert_rig_links_clean(db, user_id=None):
    result = count_link_disagreements(db, user_id=user_id, links=RIG_LINKS)
    assert result == {link.name: ZERO for link in RIG_LINKS}, result


def _components(db, user_id, tag):
    """Two of each kind for `user_id`, keyed slot -> [first, second]."""
    rows = {}
    for slot, kind in (("telescope", "telescope"), ("camera", "camera"),
                       ("reducer", "reducer_extender"), ("guide_tel", "telescope"),
                       ("guide_cam", "camera")):
        rows[slot] = [Component(user_id=user_id, kind=kind, name=f"{tag} {slot} {n}",
                                aperture_mm=80, focal_length_mm=480, sensor_width_mm=23.5,
                                sensor_height_mm=15.6, pixel_size_um=3.76, factor=0.8)
                      for n in (1, 2)]
        db.add_all(rows[slot])
    db.commit()
    return rows


def _uid_set(rows):
    return {c.record_uid for pair in rows.values() for c in pair}


# --- add_rig ---------------------------------------------------------------------

@pytest.fixture
def mu(multi_user_client, monkeypatch):
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    return multi_user_client


def _form(comps, n, rig_name="UID Rig", rig_id=None, reducer=True, guide_tel=True, guide_cam=True):
    """add_rig form using the n-th (0/1) component of each slot."""
    data = {
        'rig_name': rig_name,
        'telescope_id': comps["telescope"][n].id,
        'camera_id': comps["camera"][n].id,
        'reducer_extender_id': comps["reducer"][n].id if reducer else '',
        'guide_telescope_id': comps["guide_tel"][n].id if guide_tel else '',
        'guide_camera_id': comps["guide_cam"][n].id if guide_cam else '',
    }
    if rig_id is not None:
        data['rig_id'] = rig_id
    return data


def _posted_ids(data):
    return tuple(int(data[c]) if data[c] != '' else None for c in ID_COLS)


def _post_rig(client, db, data):
    resp = client.post('/add_rig', data=data)
    assert resp.status_code == 302
    db.expire_all()
    return db.query(Rig).filter_by(rig_name=data['rig_name']).one()


def test_add_rig_create_sets_all_five_uids(mu, db_session):
    client, ids = mu
    comps = _components(db_session, ids["user_a_id"], "A")

    data = _form(comps, 0)
    rig = _post_rig(client, db_session, data)

    assert _ids(rig) == _posted_ids(data)
    assert _uids(rig) == (comps["telescope"][0].record_uid, comps["camera"][0].record_uid,
                          comps["reducer"][0].record_uid, comps["guide_tel"][0].record_uid,
                          comps["guide_cam"][0].record_uid)
    assert all(_uids(rig))
    _assert_rig_links_clean(db_session)


def test_add_rig_create_with_empty_optional_slots(mu, db_session):
    client, ids = mu
    comps = _components(db_session, ids["user_a_id"], "A")

    data = _form(comps, 0, reducer=False, guide_tel=False, guide_cam=False)
    rig = _post_rig(client, db_session, data)

    assert _ids(rig) == (comps["telescope"][0].id, comps["camera"][0].id, None, None, None)
    assert _uids(rig) == (comps["telescope"][0].record_uid, comps["camera"][0].record_uid,
                          None, None, None)
    _assert_rig_links_clean(db_session)


def test_add_rig_update_sets_changes_and_clears(mu, db_session):
    client, ids = mu
    comps = _components(db_session, ids["user_a_id"], "A")

    # Start with only telescope and camera.
    rig = _post_rig(client, db_session, _form(comps, 0, reducer=False, guide_tel=False, guide_cam=False))
    rig_id = rig.id
    assert _uids(rig)[2:] == (None, None, None)

    # Set the three optional slots.
    data = _form(comps, 0, rig_id=rig_id)
    rig = _post_rig(client, db_session, data)
    assert rig.id == rig_id
    assert _ids(rig) == _posted_ids(data)
    assert _uids(rig) == _expected_uids(db_session, rig)
    assert all(_uids(rig))
    _assert_rig_links_clean(db_session)

    # Change all five slots.
    before = _uids(rig)
    data = _form(comps, 1, rig_id=rig_id)
    rig = _post_rig(client, db_session, data)
    assert _ids(rig) == _posted_ids(data)
    assert _uids(rig) == (comps["telescope"][1].record_uid, comps["camera"][1].record_uid,
                          comps["reducer"][1].record_uid, comps["guide_tel"][1].record_uid,
                          comps["guide_cam"][1].record_uid)
    assert not set(_uids(rig)) & set(before)
    _assert_rig_links_clean(db_session)

    # Clear the three optional slots (telescope and camera are required by add_rig).
    data = _form(comps, 1, rig_id=rig_id, reducer=False, guide_tel=False, guide_cam=False)
    rig = _post_rig(client, db_session, data)
    assert _ids(rig) == (comps["telescope"][1].id, comps["camera"][1].id, None, None, None)
    assert _uids(rig) == (comps["telescope"][1].record_uid, comps["camera"][1].record_uid,
                          None, None, None)
    _assert_rig_links_clean(db_session)


def test_add_rig_rejected_other_user_component_writes_nothing(mu, db_session):
    client, ids = mu
    own = _components(db_session, ids["user_a_id"], "A")
    other = _components(db_session, ids["user_b_id"], "B")

    data = _form(own, 0)
    data['guide_camera_id'] = other["guide_cam"][0].id
    client.post('/add_rig', data=data)

    db_session.expire_all()
    assert db_session.query(Rig).filter_by(rig_name=data['rig_name']).one_or_none() is None
    _assert_rig_links_clean(db_session)


# --- Rig import -----------------------------------------------------------------

def _user(db, username):
    user = DbUser(username=username)
    db.add(user)
    db.commit()
    return user


def _by_name(db, user_id, name):
    return db.query(Component).filter_by(user_id=user_id, name=name).one()


RIGS_YAML = {
    "components": {
        "telescopes": [
            {"id": 1, "name": "Imp Scope", "aperture_mm": 80, "focal_length_mm": 480},
            {"id": 2, "name": "Imp Guide Scope", "aperture_mm": 30, "focal_length_mm": 120},
        ],
        "cameras": [
            {"id": 1, "name": "Imp Cam", "sensor_width_mm": 23.5, "sensor_height_mm": 15.6, "pixel_size_um": 3.76},
            {"id": 2, "name": "Imp Guide Cam", "sensor_width_mm": 5.6, "sensor_height_mm": 3.2, "pixel_size_um": 2.9},
        ],
        "reducers_extenders": [
            {"id": 1, "name": "Imp Reducer", "factor": 0.8},
        ],
    },
    "rigs": [
        {"rig_name": "Imp Rig", "telescope_id": 1, "camera_id": 1, "reducer_extender_id": 1,
         "guide_telescope_id": 2, "guide_camera_id": 2, "guide_is_oag": False},
    ],
}


def test_rig_import_create_sets_all_five_uids(db_session):
    user = _user(db_session, "imp_create")

    _migrate_components_and_rigs(db_session, user, RIGS_YAML, user.username)
    db_session.commit()

    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="Imp Rig").one()
    names = ("Imp Scope", "Imp Cam", "Imp Reducer", "Imp Guide Scope", "Imp Guide Cam")
    comps = [_by_name(db_session, user.id, n) for n in names]
    assert _ids(rig) == tuple(c.id for c in comps)
    assert _uids(rig) == tuple(c.record_uid for c in comps)
    assert all(_uids(rig))
    _assert_rig_links_clean(db_session)


def test_rig_import_guide_links_by_name_created_in_same_import(db_session):
    """Guide optics named only, not listed under components: created on the fly and linked."""
    user = _user(db_session, "imp_guide_names")
    data = {
        "components": {
            "telescopes": [{"id": 1, "name": "N Scope"}],
            "cameras": [{"id": 1, "name": "N Cam"}],
        },
        "rigs": [{"rig_name": "N Rig", "telescope_id": 1, "camera_id": 1,
                  "guide_telescope_name": "N Guide Scope", "guide_camera_name": "N Guide Cam",
                  "guide_is_oag": True}],
    }

    _migrate_components_and_rigs(db_session, user, data, user.username)
    db_session.commit()

    rig = db_session.query(Rig).filter_by(user_id=user.id, rig_name="N Rig").one()
    g_tel = _by_name(db_session, user.id, "N Guide Scope")
    g_cam = _by_name(db_session, user.id, "N Guide Cam")
    assert (rig.guide_telescope_id, rig.guide_camera_id) == (g_tel.id, g_cam.id)
    assert (rig.guide_telescope_record_uid, rig.guide_camera_record_uid) == (g_tel.record_uid, g_cam.record_uid)
    assert rig.reducer_extender_id is None and rig.reducer_extender_record_uid is None
    assert _uids(rig) == _expected_uids(db_session, rig)
    _assert_rig_links_clean(db_session)


def test_rig_import_updates_existing_rig(db_session):
    user = _user(db_session, "imp_update")
    old = _components(db_session, user.id, "Old")
    rig = Rig(user_id=user.id, rig_name="Imp Rig",
              telescope_id=old["telescope"][0].id, camera_id=old["camera"][0].id,
              reducer_extender_id=old["reducer"][0].id,
              guide_telescope_id=old["guide_tel"][0].id, guide_camera_id=old["guide_cam"][0].id)
    db_session.add(rig)
    db_session.commit()
    rig_id = rig.id

    # Changes all five.
    _migrate_components_and_rigs(db_session, user, RIGS_YAML, user.username)
    db_session.commit()
    db_session.expire_all()

    rig = db_session.get(Rig, rig_id)
    names = ("Imp Scope", "Imp Cam", "Imp Reducer", "Imp Guide Scope", "Imp Guide Cam")
    comps = [_by_name(db_session, user.id, n) for n in names]
    assert _ids(rig) == tuple(c.id for c in comps)
    assert _uids(rig) == tuple(c.record_uid for c in comps)
    assert not set(_uids(rig)) & _uid_set(old)
    _assert_rig_links_clean(db_session)

    # Clears reducer and guide links.
    data = {"components": RIGS_YAML["components"],
            "rigs": [{"rig_name": "Imp Rig", "telescope_id": 1, "camera_id": 1}]}
    _migrate_components_and_rigs(db_session, user, data, user.username)
    db_session.commit()
    db_session.expire_all()

    rig = db_session.get(Rig, rig_id)
    assert _ids(rig) == (comps[0].id, comps[1].id, None, None, None)
    assert _uids(rig) == (comps[0].record_uid, comps[1].record_uid, None, None, None)
    _assert_rig_links_clean(db_session)


def test_rig_import_route_replaces_and_links(mu, db_session):
    """The /import_rig_config route deletes the user's components and rigs, then imports."""
    client, ids = mu
    _components(db_session, ids["user_a_id"], "Gone")

    resp = client.post('/import_rig_config',
                       data={'file': (io.BytesIO(yaml.safe_dump(RIGS_YAML).encode()), 'rigs.yaml')})
    assert resp.status_code == 302

    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=ids["user_a_id"], rig_name="Imp Rig").one()
    assert all(_uids(rig))
    assert _uids(rig) == _expected_uids(db_session, rig)
    _assert_rig_links_clean(db_session)


# --- Guest seed -------------------------------------------------------------------

def test_guest_seed_rig_points_at_new_users_components(db_session):
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    g = _components(db_session, guest.id, "Guest")
    g_rig = Rig(user_id=guest.id, rig_name="Guest Rig",
                telescope_id=g["telescope"][0].id, camera_id=g["camera"][0].id,
                reducer_extender_id=g["reducer"][0].id,
                guide_telescope_id=g["guide_tel"][0].id, guide_camera_id=g["guide_cam"][0].id)
    sync_rig_links(db_session, g_rig)  # guest rig holds guest UIDs
    db_session.add(g_rig)
    db_session.commit()
    assert all(_uids(g_rig))

    new_user = _user(db_session, "seeded_uid_user")
    # Already has the guest's camera by (kind, name): the rig must link to this one.
    own_cam = Component(user_id=new_user.id, kind="camera", name=g["camera"][0].name)
    db_session.add(own_cam)
    db_session.commit()

    _seed_user_from_guest_data(db_session, new_user)
    db_session.commit()
    db_session.expire_all()

    rig = db_session.query(Rig).filter_by(user_id=new_user.id, rig_name="Guest Rig").one()
    tel = db_session.query(Component).filter_by(user_id=new_user.id, name=g["telescope"][0].name).one()
    red = db_session.query(Component).filter_by(user_id=new_user.id, name=g["reducer"][0].name).one()

    # Old columns exactly as before: mapped to the new user's ids; guide links are not copied.
    assert _ids(rig) == (tel.id, own_cam.id, red.id, None, None)
    assert _uids(rig) == (tel.record_uid, own_cam.record_uid, red.record_uid, None, None)

    guest_uids = _uid_set(g) | {g_rig.record_uid} | set(_uids(g_rig))
    assert not {u for u in _uids(rig) if u} & guest_uids
    _assert_rig_links_clean(db_session)
    _assert_rig_links_clean(db_session, user_id=new_user.id)
