"""GET /get_imaging_opportunities/<name>?plot_loc_name=: the graph page's location by name.

A name that is one of the caller's own locations (user_id + name, no active filter) sets
everything location-specific: coordinates, timezone, horizon mask, altitude threshold.
Missing, unknown or another user's name: as before, the plot_lat/lon/tz match, else the
global threshold.
"""

from datetime import timedelta
from urllib.parse import urlencode

import pytest

import nova.blueprints.core as core_mod
from nova.helpers import bust_astro_context_cache
from nova.models import DbUser, HorizonPoint, Location

GLOBAL_THRESHOLD = 20                      # resolve_altitude_threshold default, no UiPref set
DEFAULT = (50, 10, "UTC")                  # "Default Test Loc" (conftest client fixture)
USER_A_HOME = (40, -100, "UTC")            # "UserA_Home" (conftest multi_user_client)
SYDNEY = (-33.87, 151.21, "Australia/Sydney")
SYDNEY_THRESHOLD = 5
SYDNEY_MASK = [[90.0, 25.0], [180.0, 35.0]]


@pytest.fixture(autouse=True)
def calls(monkeypatch):
    """Record the location-specific inputs; no SIMBAD, no real altitude math."""
    seen = []
    monkeypatch.setattr(core_mod, "get_ra_dec",
                        lambda name: {"RA (hours)": 5.58, "DEC (degrees)": -5.4, "Common Name": name})

    def fake_duration(ra, dec, lat, lon, date_str, tz_name, altitude_threshold,
                      sampling_interval, horizon_mask=None):
        seen.append((lat, lon, tz_name, altitude_threshold, horizon_mask))
        return timedelta(0), 0, None, None   # below min_obs: the date is skipped

    monkeypatch.setattr(core_mod, "calculate_observable_duration_vectorized", fake_duration)
    return seen


def _get(client, where, name=None):
    lat, lon, tz = where
    params = {"plot_lat": lat, "plot_lon": lon, "plot_tz": tz}
    if name is not None:
        params["plot_loc_name"] = name
    response = client.get(f"/get_imaging_opportunities/M42?{urlencode(params)}")
    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def _used(calls):
    """The one set of inputs every date was computed with."""
    assert calls and len(set((a, b, c, d, repr(e)) for a, b, c, d, e in calls)) == 1
    return calls[0]


def _add_sydney(db_session, user_id, name):
    lat, lon, tz = SYDNEY
    loc = Location(user_id=user_id, name=name, lat=lat, lon=lon, timezone=tz,
                   altitude_threshold=SYDNEY_THRESHOLD)
    db_session.add(loc)
    db_session.flush()
    for az, alt in SYDNEY_MASK:
        db_session.add(HorizonPoint(location_id=loc.id, az_deg=az, alt_min_deg=alt))
    db_session.commit()
    bust_astro_context_cache(user_id)


def _default_user_id(db_session):
    return db_session.query(DbUser).filter_by(username="default").one().id


# --- a. no plot_loc_name: as before ------------------------------------------------------

def test_no_name_unmatched_coords_use_the_global_threshold(client, db_session, calls):
    _add_sydney(db_session, _default_user_id(db_session), "Sydney Site")

    _get(client, DEFAULT)

    assert _used(calls) == (*DEFAULT, GLOBAL_THRESHOLD, [])


def test_no_name_exact_coords_still_match_the_location(client, db_session, calls):
    _add_sydney(db_session, _default_user_id(db_session), "Sydney Site")

    _get(client, SYDNEY)

    assert _used(calls) == (*SYDNEY, SYDNEY_THRESHOLD, SYDNEY_MASK)


# --- b. own location by name: wins over unmatched coords ---------------------------------

def test_own_location_by_name_sets_everything(client, db_session, calls):
    _add_sydney(db_session, _default_user_id(db_session), "Sydney Site")

    _get(client, DEFAULT, "Sydney Site")

    assert _used(calls) == (*SYDNEY, SYDNEY_THRESHOLD, SYDNEY_MASK)


def test_own_inactive_location_by_name_is_used(client, db_session, calls):
    user_id = _default_user_id(db_session)
    _add_sydney(db_session, user_id, "Retired Sydney")
    db_session.query(Location).filter_by(user_id=user_id, name="Retired Sydney").update({"active": False})
    db_session.commit()
    bust_astro_context_cache(user_id)

    _get(client, DEFAULT, "Retired Sydney")

    assert _used(calls) == (*SYDNEY, SYDNEY_THRESHOLD, SYDNEY_MASK)


# --- c. another user's name: ignored -----------------------------------------------------

def test_another_users_location_name_is_ignored(multi_user_client, db_session, calls):
    client, ids = multi_user_client
    _add_sydney(db_session, ids["user_b_id"], "B Sydney Site")

    _get(client, USER_A_HOME, "B Sydney Site")

    assert _used(calls) == (*USER_A_HOME, GLOBAL_THRESHOLD, [])


# --- d. unknown name: as before ----------------------------------------------------------

def test_unknown_name_behaves_as_before(client, db_session, calls):
    _add_sydney(db_session, _default_user_id(db_session), "Sydney Site")

    _get(client, DEFAULT, "Nowhere")

    assert _used(calls) == (*DEFAULT, GLOBAL_THRESHOLD, [])
