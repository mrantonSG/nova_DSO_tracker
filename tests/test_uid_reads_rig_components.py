"""
record_uid reads for a rig's components (step 5 part 2).

A rig's component is found by user + record_uid; the row number in the *_id
column never decides. An empty UID, or one with no component of that user, is
a missing component, exactly as a missing component is treated today.
"""
import pytest

from nova.models import Component, JournalSession, Rig
from nova.record_links import (
    components_by_uid, components_for_rig, rig_components, sync_rig_links,
)


def _components(db, user_id, tag, guide=False):
    tel = Component(user_id=user_id, kind="telescope", name=f"{tag} Scope",
                    aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=user_id, kind="camera", name=f"{tag} Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    red = Component(user_id=user_id, kind="reducer_extender", name=f"{tag} Red", factor=0.8)
    db.add_all([tel, cam, red])
    comps = {"tel": tel, "cam": cam, "red": red}
    if guide:
        gt = Component(user_id=user_id, kind="telescope", name=f"{tag} Guide Scope",
                       focal_length_mm=200)
        gc = Component(user_id=user_id, kind="camera", name=f"{tag} Guide Cam", pixel_size_um=2.9)
        db.add_all([gt, gc])
        comps.update({"guide_tel": gt, "guide_cam": gc})
    db.flush()
    return comps


def _rig(db, user_id, tag, comps, guide=False, sync=True):
    rig = Rig(user_id=user_id, rig_name=f"{tag} Rig",
              telescope_id=comps["tel"].id, camera_id=comps["cam"].id,
              reducer_extender_id=comps["red"].id)
    if guide:
        rig.guide_telescope_id = comps["guide_tel"].id
        rig.guide_camera_id = comps["guide_cam"].id
    if sync:
        sync_rig_links(db, rig)
    db.add(rig)
    db.flush()
    return rig


def _telescope(db, user_id, name, focal_length_mm):
    comp = Component(user_id=user_id, kind="telescope", name=name,
                     aperture_mm=200, focal_length_mm=focal_length_mm)
    db.add(comp)
    db.flush()
    return comp


@pytest.fixture
def mu(multi_user_client, monkeypatch):
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    client, ids = multi_user_client
    return client, ids["user_a_id"], ids["user_b_id"]


def _row(client, rig_id):
    return next(r for r in client.get('/get_rig_data').get_json()["rigs"] if r["rig_id"] == rig_id)


# --- helpers --------------------------------------------------------------------

def test_helpers_agree_and_foreign_uid_is_unresolved(mu, db_session):
    _, a_id, b_id = mu
    a = _components(db_session, a_id, "A", guide=True)
    rig = _rig(db_session, a_id, "A", a, guide=True)
    b = _components(db_session, b_id, "B")
    db_session.commit()

    expected = ("A Scope", "A Cam", "A Red", "A Guide Scope", "A Guide Cam")
    one = components_for_rig(db_session, rig)
    two = rig_components(rig, components_by_uid(db_session, a_id))
    assert (one.telescope.name, one.camera.name, one.reducer_extender.name,
            one.guide_telescope.name, one.guide_camera.name) == expected
    assert (two.telescope.name, two.camera.name, two.reducer_extender.name,
            two.guide_telescope.name, two.guide_camera.name) == expected

    # A UID that names another user's component is not resolved by either helper
    rig.telescope_record_uid = b["tel"].record_uid
    db_session.commit()
    assert components_for_rig(db_session, rig).telescope is None
    assert rig_components(rig, components_by_uid(db_session, a_id)).telescope is None


# --- /get_rig_data --------------------------------------------------------------

def test_get_rig_data_resolves_every_role_by_uid(mu, db_session):
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A", guide=True)
    rig = _rig(db_session, a_id, "A", comps, guide=True)
    db_session.commit()

    row = _row(client, rig.id)
    assert (row["telescope_name"], row["camera_name"], row["reducer_name"]) == ("A Scope", "A Cam", "A Red")
    assert (row["guide_telescope_name"], row["guide_camera_name"]) == ("A Guide Scope", "A Guide Cam")
    assert row["effective_focal_length"] == pytest.approx(384.0)  # 480 * 0.8


def test_row_number_reuse_shows_the_uid_component(mu, db_session):
    """The key case: telescope_id's row number now names another component."""
    client, a_id, _ = mu
    old = _components(db_session, a_id, "Old")
    rig = _rig(db_session, a_id, "A", old)
    new_tel = _telescope(db_session, a_id, "New Scope", 1000)
    rig.telescope_id = new_tel.id          # row number now belongs to New Scope
    db_session.commit()                    # telescope_record_uid still points at Old Scope

    row = _row(client, rig.id)
    assert row["telescope_id"] == new_tel.id            # old column reported as before
    assert row["telescope_name"] == "Old Scope"         # the UID decides
    assert row["effective_focal_length"] == pytest.approx(384.0)  # Old Scope 480*0.8, not 1000*0.8


def test_guide_row_number_reuse_shows_the_uid_component(mu, db_session):
    """Same reuse case for a guide role."""
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A", guide=True)
    rig = _rig(db_session, a_id, "A", comps, guide=True)
    new_gt = _telescope(db_session, a_id, "New Guide Scope", 999)
    rig.guide_telescope_id = new_gt.id     # row number now belongs to New Guide Scope
    db_session.commit()                    # guide_telescope_record_uid still points at A Guide Scope

    row = _row(client, rig.id)
    assert row["guide_telescope_id"] == new_gt.id
    assert row["guide_telescope_name"] == "A Guide Scope"


def test_empty_and_unknown_uid_are_missing_not_the_row(mu, db_session):
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A")
    empty = _rig(db_session, a_id, "Empty", comps, sync=False)   # all UIDs NULL, ids set
    dead = _rig(db_session, a_id, "Dead", comps, sync=False)
    dead.telescope_record_uid = "no-such-uid"
    db_session.commit()

    for rid in (empty.id, dead.id):
        row = _row(client, rid)
        assert row["telescope_id"] is not None           # the row still exists
        assert row["telescope_name"] is None             # but is treated as missing
        assert row["effective_focal_length"] is None     # no error


def test_uid_of_another_user_is_never_resolved(mu, db_session):
    client, a_id, b_id = mu
    a = _components(db_session, a_id, "A")
    b = _components(db_session, b_id, "B")
    rig = _rig(db_session, a_id, "A", a)
    rig.telescope_id = b["tel"].id                       # even the row number names B's component
    rig.telescope_record_uid = b["tel"].record_uid
    db_session.commit()

    row = _row(client, rig.id)
    assert row["telescope_name"] is None


def test_second_users_rigs_resolve_their_own_components(mu, db_session):
    client, a_id, b_id = mu
    b = _components(db_session, b_id, "B")
    b_rig = _rig(db_session, b_id, "B", b)
    db_session.commit()

    # A's rig list never contains B's rigs
    assert all(r["rig_id"] != b_rig.id for r in client.get('/get_rig_data').get_json()["rigs"])
    # and B's own rig still resolves through B's UIDs
    rc = components_for_rig(db_session, db_session.get(Rig, b_rig.id))
    assert (rc.telescope.name, rc.camera.name) == ("B Scope", "B Cam")


# --- journal-add snapshot -------------------------------------------------------

def test_journal_add_snapshot_uses_the_uid_component(mu, db_session):
    client, a_id, _ = mu
    old = _components(db_session, a_id, "Old")
    rig = _rig(db_session, a_id, "A", old)
    new_tel = _telescope(db_session, a_id, "New Scope", 1000)
    rig.telescope_id = new_tel.id          # row number now belongs to New Scope
    db_session.commit()                    # telescope_record_uid still points at Old Scope

    resp = client.post('/journal/add', data={
        "session_date": "2026-02-01",
        "target_object_id": "M42",
        "location_name": "UserA_Home",
        "rig_id_snapshot": rig.id,
        "telescope_setup_notes": "snapshot check",
    })
    assert resp.status_code == 302

    db_session.expire_all()
    session = (db_session.query(JournalSession).filter_by(user_id=a_id)
               .order_by(JournalSession.id.desc()).first())
    assert session is not None
    assert session.telescope_name_snapshot == "Old Scope"      # UID, not the reused row number
    assert session.camera_name_snapshot == "Old Cam"
    assert session.rig_efl_snapshot == pytest.approx(384.0)    # Old Scope 480*0.8, not 1000*0.8
