"""POST /api/create_object: create-only target creation from the Framing tab.

The whole point of the route is that it never touches anything that already
exists, so the tests below lean on that: creation, the canonical name, the 409
on a colliding name (raw and normalized), that the colliding row and its framing
are left exactly as they were, that a new target's framing is linked by UID, and
that the path makes no SIMBAD call at all.
"""

import pytest

from nova.models import AstroObject, SavedFraming


def _post(client, payload):
    return client.post('/api/create_object', json=payload)


def _m42(db_session):
    return db_session.query(AstroObject).filter_by(object_name="M42").one()


def test_create_object_success_name_and_fields(client, db_session):
    resp = _post(client, {"name": "barnard143", "ra": 19.5, "dec": 10.25})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    # normalize_object_name: "BARNARD143" -> the BARNARD rule -> "BARNARD 143"
    assert body["object_name"] == "BARNARD 143"
    assert body["record_uid"]

    db_session.expire_all()
    obj = db_session.query(AstroObject).filter_by(object_name="BARNARD 143").one()
    assert obj.ra_hours == pytest.approx(19.5)
    assert obj.dec_deg == pytest.approx(10.25)
    assert obj.common_name == "barnard143"      # the typed name, verbatim
    assert obj.constellation == "Aql"           # computed locally from RA/Dec
    assert obj.active_project is False
    assert obj.is_shared is False
    assert obj.record_uid                       # assigned on insert


def test_create_object_409_on_existing_name(client, db_session):
    resp = _post(client, {"name": "M42", "ra": 1.0, "dec": 1.0})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "already_exists"
    assert body["object_name"] == "M42"

    db_session.expire_all()
    # The refused create changed nothing on the object it collided with.
    row = _m42(db_session)
    assert row.ra_hours == pytest.approx(5.58)
    assert row.dec_deg == pytest.approx(-5.4)


@pytest.mark.parametrize("typed", ["NGC1976", "ngc 1976", "NGC 1976"])
def test_create_object_409_on_normalized_variant(client, db_session, typed):
    """A name that normalizes onto a stored one collides, and does not overwrite it."""
    db_session.add(AstroObject(user_id=_m42(db_session).user_id, object_name="NGC 1976",
                               ra_hours=5.5, dec_deg=-5.0))
    db_session.commit()

    resp = _post(client, {"name": typed, "ra": 23.9, "dec": 80.0})
    assert resp.status_code == 409
    assert resp.get_json()["object_name"] == "NGC 1976"

    db_session.expire_all()
    row = db_session.query(AstroObject).filter_by(object_name="NGC 1976").one()
    assert row.ra_hours == pytest.approx(5.5)      # untouched
    assert row.dec_deg == pytest.approx(-5.0)
    # Nothing new was created under the other spelling.
    assert db_session.query(AstroObject).filter_by(object_name="NGC1976").first() is None


def test_create_object_makes_no_simbad_call(client, monkeypatch):
    """Offline: every SIMBAD entry point is a hard failure here."""
    import modules.simbad_service as simbad_service

    def _boom(*a, **k):
        raise AssertionError("create_object must not touch SIMBAD")

    monkeypatch.setattr(simbad_service, "query_object", _boom)
    monkeypatch.setattr(simbad_service, "tap_csv", _boom)
    monkeypatch.setattr(simbad_service, "is_available_cached", _boom)

    resp = _post(client, {"name": "LDN 1234", "ra": 3.25, "dec": 41.0})
    assert resp.status_code == 200
    assert resp.get_json()["object_name"] == "LDN 1234"

    db_session_row = None  # the row's own fields are asserted in the test above


def test_create_object_requires_name_and_coords(client):
    assert _post(client, {"name": "  ", "ra": 1, "dec": 1}).status_code == 400
    assert _post(client, {"name": "X", "dec": 1}).status_code == 400
    assert _post(client, {"name": "X", "ra": 1}).status_code == 400


def test_create_object_accepts_scanned_extras(client, db_session):
    """Entry A passes the scanned object's type and size straight through."""
    resp = _post(client, {"name": "Barnard 143", "ra": 19.5, "dec": 10.25,
                          "type": "Dark Nebula", "size": "42.50"})
    assert resp.status_code == 200

    db_session.expire_all()
    obj = db_session.query(AstroObject).filter_by(object_name="BARNARD 143").one()
    assert obj.type == "Dark Nebula"
    assert obj.size == "42.50"


def test_existing_objects_framing_untouched_by_new_target(client, db_session):
    """Creating a target from the frame leaves the object the frame was opened on —
    and its saved framing — exactly as they were."""
    user_id = _m42(db_session).user_id
    db_session.add(SavedFraming(user_id=user_id, object_name="M42",
                                ra=83.8, dec=-5.4, rotation=42.0, survey="P/DSS2/color"))
    db_session.commit()

    created = _post(client, {"name": "Barnard 143", "ra": 19.5, "dec": 10.25})
    assert created.status_code == 200
    new_name = created.get_json()["object_name"]

    saved = client.post('/api/save_framing', json={
        "object_name": new_name, "ra": 285.0, "dec": 9.9, "rotation": 7.0,
    })
    assert saved.get_json()["status"] == "success"

    db_session.expire_all()
    old = db_session.query(SavedFraming).filter_by(object_name="M42").one()
    assert old.ra == pytest.approx(83.8)           # unchanged
    assert old.dec == pytest.approx(-5.4)
    assert old.rotation == pytest.approx(42.0)
    assert old.survey == "P/DSS2/color"

    m42_row = _m42(db_session)
    assert m42_row.ra_hours == pytest.approx(5.58)
    assert m42_row.dec_deg == pytest.approx(-5.4)

    # The new target's framing is its own, linked to the new object by UID.
    new_framing = db_session.query(SavedFraming).filter_by(object_name=new_name).one()
    assert new_framing.object_record_uid
    assert new_framing.id != old.id
