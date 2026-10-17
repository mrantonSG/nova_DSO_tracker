"""POST /set_location: the dashboard's location switch.

It makes one of the caller's own active locations the default (Location.is_default
and UiPref default_location). Another user's location, an unknown name and an
inactive location are all refused the same way, and change nothing.
"""

import json
from urllib.parse import urlsplit

from nova.models import DbUser, Location, UiPref

LOC_NAME = "Default Test Loc"  # created by the conftest client fixture (is_default=True)
NOT_FOUND = (404, {"status": "error", "message": "Location not found"})


def _post(client, name):
    return client.post('/set_location', json={"location": name})


def _state(db_session):
    """Every location's default flag and every user's UiPref blob, to compare before/after."""
    db_session.expire_all()
    locations = sorted((l.user_id, l.name, l.is_default, l.active)
                       for l in db_session.query(Location).all())
    prefs = sorted((p.user_id, p.json_blob) for p in db_session.query(UiPref).all())
    return locations, prefs


def _defaults(db_session, user_id):
    db_session.expire_all()
    return [l.name for l in db_session.query(Location).filter_by(user_id=user_id, is_default=True)]


def _default_user(db_session):
    return db_session.query(DbUser).filter_by(username="default").one()


# --- own active location -----------------------------------------------------------------

def test_set_location_own_active_location_becomes_the_default(client, db_session):
    user = _default_user(db_session)
    db_session.add(Location(user_id=user.id, name="Second Site", lat=10, lon=20, timezone="UTC"))
    db_session.commit()

    response = _post(client, "Second Site")

    assert (response.status_code, response.get_json()) == (
        200, {"status": "success", "message": "Location set to Second Site"})
    assert _defaults(db_session, user.id) == ["Second Site"]
    prefs = db_session.query(UiPref).filter_by(user_id=user.id).one()
    assert json.loads(prefs.json_blob)["default_location"] == "Second Site"


# --- refused: nothing changes ------------------------------------------------------------

def test_set_location_rejects_another_users_location(multi_user_client, db_session):
    client, ids = multi_user_client
    db_session.add(Location(user_id=ids["user_b_id"], name="B Home",
                            lat=1, lon=2, timezone="UTC", is_default=True))
    db_session.add(Location(user_id=ids["user_b_id"], name="B Secret Site",
                            lat=3, lon=4, timezone="UTC"))
    db_session.commit()
    before = _state(db_session)

    response = _post(client, "B Secret Site")

    assert (response.status_code, response.get_json()) == NOT_FOUND
    assert _state(db_session) == before
    assert _defaults(db_session, ids["user_a_id"]) == ["UserA_Home"]
    assert _defaults(db_session, ids["user_b_id"]) == ["B Home"]


def test_set_location_rejects_an_unknown_name(client, db_session):
    before = _state(db_session)

    response = _post(client, "Nowhere")

    assert (response.status_code, response.get_json()) == NOT_FOUND
    assert _state(db_session) == before
    assert _defaults(db_session, _default_user(db_session).id) == [LOC_NAME]


def test_set_location_rejects_an_inactive_location_like_an_unknown_name(client, db_session):
    user = _default_user(db_session)
    db_session.add(Location(user_id=user.id, name="Retired Site", lat=10, lon=20,
                            timezone="UTC", active=False))
    db_session.commit()
    before = _state(db_session)

    response = _post(client, "Retired Site")

    assert (response.status_code, response.get_json()) == NOT_FOUND
    assert _state(db_session) == before
    assert _defaults(db_session, user.id) == [LOC_NAME]


def test_set_location_logged_out_redirects_to_login(mu_client_logged_out, db_session):
    user_a = db_session.query(DbUser).filter_by(username="UserA").one()
    db_session.add(Location(user_id=user_a.id, name="A Home", lat=1, lon=2,
                            timezone="UTC", is_default=True))
    db_session.add(Location(user_id=user_a.id, name="A Field", lat=3, lon=4, timezone="UTC"))
    db_session.commit()
    before = _state(db_session)

    response = _post(mu_client_logged_out, "A Field")

    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/login"
    assert _state(db_session) == before


# --- graph route: ?location= beats the default -------------------------------------------

def test_graph_dashboard_location_param_renders_that_location(client, db_session):
    user = _default_user(db_session)
    db_session.add(Location(user_id=user.id, name="Second Site", lat=10, lon=20, timezone="UTC"))
    db_session.commit()

    response = client.get('/graph_dashboard/M42?location=Second Site')

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'plotLocName: "Second Site"' in page
    assert 'plotLocName: "Default Test Loc"' not in page
    # Rendering the page does not move the default.
    assert _defaults(db_session, user.id) == [LOC_NAME]
