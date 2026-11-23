"""/sun_events: the header's calendar date (local_date) next to the observing night (date).

date stays the observing night (the day before when the location's wall hour is < 12)
and keys every per-night value; local_date is the location's wall date, from the same
clock as time. In simulation local_date equals date, so the simulated header is
unchanged. Nothing else in the response may change.
"""

from datetime import date, datetime, time

import pytest
import pytz

import modules.astro_calculations as astro_calculations
import nova.blueprints.core as core_module
from modules.astro_calculations import calculate_moon_phase_cached, calculate_sun_events_cached
from nova.config import astro_context_cache
from nova.models import DbUser, Location
from tests.test_dashboard_rows_parity import _FROZEN, _FrozenDatetime

LONDON = ("London", 51.5074, -0.1278, "Europe/London")
# Keys /sun_events returned before local_date was added.
OLD_KEYS = {"astronomical_dawn", "sunrise", "transit", "sunset", "astronomical_dusk",
            "date", "time", "phase"}


@pytest.fixture
def london(client, db_session, monkeypatch):
    user = db_session.query(DbUser).filter_by(username="default").one()
    name, lat, lon, tz_name = LONDON
    db_session.add(Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz_name))
    db_session.commit()
    astro_context_cache.clear()
    monkeypatch.setattr(core_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(astro_calculations, "SUN_EVENTS_CACHE", {})

    def get(day, clock, sim_date=None):
        _FROZEN[0] = pytz.timezone(tz_name).localize(
            datetime.combine(date.fromisoformat(day), time.fromisoformat(clock)))
        query = {"location": name}
        if sim_date:
            query["sim_date"] = sim_date
        response = client.get('/sun_events', query_string=query)
        assert response.status_code == 200
        return response.get_json()
    return get


@pytest.mark.parametrize("day, clock, local_date, night", [
    ("2026-11-23", "11:36", "2026-11-23", "2026-11-22"),   # before noon: night is yesterday
    ("2026-11-23", "14:00", "2026-11-23", "2026-11-23"),
    # Already 23.11 in Asia/Singapore; the server only ever uses the location's zone.
    ("2026-11-22", "23:30", "2026-11-22", "2026-11-22"),
])
def test_local_date_is_the_locations_calendar_date(london, day, clock, local_date, night):
    data = london(day, clock)
    assert data["local_date"] == local_date
    assert data["date"] == night
    assert data["time"] == clock


@pytest.mark.parametrize("day, clock", [
    ("2026-11-23", "11:36"), ("2026-11-23", "14:00"), ("2026-11-22", "23:30")])
def test_per_night_fields_are_unchanged(london, day, clock):
    """Only local_date is new; dusk, dawn, the other sun events and the Moon are still
    the observing night's, from the same helpers as before."""
    data = london(day, clock)
    assert set(data) == OLD_KEYS | {"local_date"}
    _, lat, lon, tz_name = LONDON
    night = data["date"]
    expected = calculate_sun_events_cached(night, tz_name, lat, lon)
    for key, value in expected.items():
        assert data[key] == value, key
    assert data["phase"] == calculate_moon_phase_cached(night, lat, lon, tz_name)


@pytest.mark.parametrize("clock", ["11:36", "14:00", "23:30"])
def test_simulation_header_is_unchanged(london, clock):
    data = london("2026-11-23", clock, sim_date="2026-12-01")
    assert data["date"] == "2026-12-01"
    assert data["local_date"] == "2026-12-01"
