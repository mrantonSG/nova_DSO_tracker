"""
Tests for timezone validation at the import choke point (_migrate_locations).

A present-but-invalid timezone (not in pytz.all_timezones) must cause that
location to be skipped, without clobbering a previously stored row; a missing
timezone key keeps the existing "UTC" default.
"""
import pytest

from nova import app
from nova.models import DbUser, Location
from nova.migration import _migrate_locations


def _run(db_session, user, config):
    # _migrate_locations touches current_app in its logging paths; run inside a
    # request context like the real import routes do.
    with app.test_request_context():
        _migrate_locations(db_session, user, config)
        db_session.commit()


def _locations_by_name(db_session, user):
    return {l.name: l for l in db_session.query(Location).filter_by(user_id=user.id).all()}


def test_invalid_timezone_location_is_skipped(db_session):
    user = db_session.query(DbUser).filter_by(username="guest_user").one()
    config = {
        "default_location": "Vienna",
        "locations": {
            "Nuuk": {
                "lat": 64.17,
                "lon": -51.67,
                "timezone": "Greenland/Sermersooq",  # not an IANA zone name (legacy free-text entry)
            },
            "Vienna": {
                "lat": 48.21,
                "lon": 16.37,
                "timezone": "Europe/Vienna",
            },
        },
    }

    _run(db_session, user, config)

    saved = _locations_by_name(db_session, user)
    assert set(saved) == {"Vienna"}
    assert saved["Vienna"].timezone == "Europe/Vienna"
    assert saved["Vienna"].is_default is True


def test_missing_timezone_keeps_utc_default(db_session):
    user = db_session.query(DbUser).filter_by(username="guest_user").one()
    config = {
        "default_location": "Somewhere",
        "locations": {
            "Somewhere": {"lat": 10.0, "lon": 20.0},  # no "timezone" key
        },
    }

    _run(db_session, user, config)

    saved = _locations_by_name(db_session, user)
    assert set(saved) == {"Somewhere"}
    assert saved["Somewhere"].timezone == "UTC"


def test_invalid_timezone_does_not_clobber_existing_location(db_session):
    user = db_session.query(DbUser).filter_by(username="guest_user").one()
    db_session.add(Location(
        user_id=user.id, name="Vienna", lat=48.21, lon=16.37,
        timezone="Europe/Vienna", is_default=True,
    ))
    db_session.commit()

    config = {
        "default_location": "Vienna",
        "locations": {
            "Vienna": {
                "lat": 48.21,
                "lon": 16.37,
                "timezone": "Greenland/Sermersooq",  # not an IANA zone name (legacy free-text entry)
            },
        },
    }

    _run(db_session, user, config)

    saved = _locations_by_name(db_session, user)
    assert saved["Vienna"].timezone == "Europe/Vienna"
