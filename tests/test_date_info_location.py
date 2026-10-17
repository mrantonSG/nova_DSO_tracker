"""GET /get_date_info/<name>?location=: the graph page's status strip follows the page's location.

The name is looked up among the caller's own locations only (as graph_dashboard does:
no active filter). Missing, unknown or another user's name falls back to the default.
"""

import pytest

import nova.blueprints.core as core_mod
from modules.astro_calculations import calculate_sun_events_cached, calculate_moon_phase_cached
from nova.models import DbUser, Location

DATE = "2026-01-15"
QUERY = "day=15&month=1&year=2026"
DEFAULT = (50, 10, "UTC")                     # "Default Test Loc" (conftest client fixture)
USER_A_HOME = (40, -100, "UTC")               # "UserA_Home" (conftest multi_user_client)
SYDNEY = (-33.87, 151.21, "Australia/Sydney")


@pytest.fixture(autouse=True)
def _no_simbad(monkeypatch):
    """Separation is not under test; keep get_ra_dec off the network."""
    monkeypatch.setattr(core_mod, "get_ra_dec", lambda name: {})


def _expected(lat, lon, tz):
    sun = calculate_sun_events_cached(DATE, tz, lat, lon)
    return {"astronomical_dusk": sun.get("astronomical_dusk", "N/A"),
            "astronomical_dawn": sun.get("astronomical_dawn", "N/A"),
            "phase": calculate_moon_phase_cached(DATE, lat, lon, tz)}


def _get(client, location=None):
    url = f"/get_date_info/M42?{QUERY}"
    if location is not None:
        url += f"&location={location}"
    response = client.get(url)
    assert response.status_code == 200
    data = response.get_json()
    return {k: data[k] for k in ("astronomical_dusk", "astronomical_dawn", "phase")}


def _add(db_session, user_id, name, where, active=True):
    lat, lon, tz = where
    db_session.add(Location(user_id=user_id, name=name, lat=lat, lon=lon, timezone=tz, active=active))
    db_session.commit()


def _default_user_id(db_session):
    return db_session.query(DbUser).filter_by(username="default").one().id


def test_the_two_sites_differ():
    """The tests below only prove something if Sydney and the defaults disagree."""
    sydney = _expected(*SYDNEY)
    for other in (DEFAULT, USER_A_HOME):
        e = _expected(*other)
        assert e["astronomical_dusk"] != sydney["astronomical_dusk"]
        assert e["astronomical_dawn"] != sydney["astronomical_dawn"]


def test_no_location_param_uses_the_default(client):
    assert _get(client) == _expected(*DEFAULT)


def test_own_location_is_used(client, db_session):
    _add(db_session, _default_user_id(db_session), "Sydney Site", SYDNEY)

    assert _get(client, "Sydney Site") == _expected(*SYDNEY)


def test_another_users_location_falls_back_to_the_default(multi_user_client, db_session):
    client, ids = multi_user_client
    _add(db_session, ids["user_b_id"], "B Sydney Site", SYDNEY)

    assert _get(client, "B Sydney Site") == _expected(*USER_A_HOME)


def test_unknown_name_falls_back_to_the_default(client):
    assert _get(client, "Nowhere") == _expected(*DEFAULT)


def test_own_inactive_location_is_used(client, db_session):
    _add(db_session, _default_user_id(db_session), "Retired Sydney", SYDNEY, active=False)

    assert _get(client, "Retired Sydney") == _expected(*SYDNEY)
