"""
Tests IANA-timezone validation at the /import_config gate (validate_config).

A config whose location timezone is not in pytz.all_timezones must be rejected
*before* the wipe-and-replace, so the user's existing locations survive the
import attempt.
"""
import io

import pytest

from nova.models import DbUser, Location

INVALID_CONFIG_YAML = """
altitude_threshold: 45
default_location: New Base
imaging_criteria:
    max_moon_illumination: 20
    min_angular_distance: 30
    min_max_altitude: 30
    min_observable_minutes: 60
    search_horizon_months: 6
locations:
    New Base:
        lat: 64.17
        lon: -51.67
        # not an IANA zone name (legacy free-text entry)
        timezone: Greenland/Sermersooq
objects:
    - Object: M31
      RA: 0.71
      DEC: 41.27
"""


@pytest.fixture
def invalid_config_file():
    return io.BytesIO(INVALID_CONFIG_YAML.encode('utf-8'))


def test_import_config_rejects_invalid_timezone(client, db_session, invalid_config_file):
    # The `client` fixture (su_client_logged_in) provisions the "default" user
    # with one existing location; snapshot it before the import attempt.
    user = db_session.query(DbUser).filter_by(username="default").one()
    existing = {
        loc.name: (loc.lat, loc.lon, loc.timezone)
        for loc in db_session.query(Location).filter_by(user_id=user.id)
    }
    assert existing  # sanity: the fixture really gave the user a location

    response = client.post(
        '/import_config',
        data={'file': (invalid_config_file, 'bad_timezone_config.yaml')},
        follow_redirects=True,
    )

    # 1. Import rejected: validation error flashed (naming the invalid value),
    #    no success message, and we land back on the config form.
    assert response.status_code == 200
    body = response.data
    assert b"Configuration validation failed" in body
    assert b"Greenland/Sermersooq" in body
    assert b"is not a valid timezone name" in body
    assert b"Config imported successfully" not in body

    # 2. The user's existing locations are untouched (nothing was wiped or added).
    after = {
        loc.name: (loc.lat, loc.lon, loc.timezone)
        for loc in db_session.query(Location).filter_by(user_id=user.id)
    }
    assert after == existing
