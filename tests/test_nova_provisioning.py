import os
import pytest
import yaml
from nova import (
    app, Base, DbUser, Location, AstroObject, Component, Rig,
    get_or_create_db_user, _seed_new_user_from_yaml, _is_test_environment
)
from nova.config import CONFIG_DIR


MINIMAL_CONFIG_YAML = {
    "default_location": "Test Observatory",
    "locations": {
        "Test Observatory": {
            "lat": -31.9505,
            "lon": 115.8605,
            "timezone": "Australia/Perth",
        }
    },
    "objects": [
        {
            "Object": "M42",
            "Name": "Orion Nebula",
            "RA": "5.588",
            "DEC": "-5.391",
            "Type": "HII",
            "Magnitude": 4.0,
            "Size": 65.0,
        }
    ],
    "saved_framings": [],
    "saved_views": []
}

MINIMAL_RIGS_YAML = {"components": [], "rigs": []}
MINIMAL_JOURNAL_YAML = {"sessions": [], "projects": []}


def _write_test_yamls(directory):
    """Write minimal valid YAML files to directory."""
    with open(os.path.join(directory, "config_default.yaml"), "w") as f:
        yaml.dump(MINIMAL_CONFIG_YAML, f)
    with open(os.path.join(directory, "rigs_default.yaml"), "w") as f:
        yaml.dump(MINIMAL_RIGS_YAML, f)
    with open(os.path.join(directory, "journal_default.yaml"), "w") as f:
        yaml.dump(MINIMAL_JOURNAL_YAML, f)


def test_seed_new_user_yaml_creates_expected_models(db_session, tmp_path, monkeypatch):
    """New user seeding from config_default.yaml creates Location and AstroObject rows."""
    _write_test_yamls(tmp_path)
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))

    user = DbUser(username="provisioning_test_user")
    db_session.add(user)
    db_session.flush()

    _seed_new_user_from_yaml(db_session, user)
    db_session.flush()

    locations = db_session.query(Location).filter_by(user_id=user.id).all()
    objects = db_session.query(AstroObject).filter_by(user_id=user.id).all()

    assert len(locations) == 1
    assert locations[0].name == "Test Observatory"
    assert len(objects) == 1
    assert objects[0].object_name == "M42"


def test_seed_new_user_yaml_is_idempotent(db_session, tmp_path, monkeypatch):
    """Calling _seed_new_user_from_yaml twice must not create duplicate rows."""
    _write_test_yamls(tmp_path)
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))

    user = DbUser(username="idempotent_test_user")
    db_session.add(user)
    db_session.flush()

    _seed_new_user_from_yaml(db_session, user)
    db_session.flush()
    _seed_new_user_from_yaml(db_session, user)
    db_session.flush()

    locations = db_session.query(Location).filter_by(user_id=user.id).all()
    objects = db_session.query(AstroObject).filter_by(user_id=user.id).all()

    assert len(locations) == 1
    assert len(objects) == 1


def test_seed_new_user_yaml_missing_files_no_error(db_session, tmp_path, monkeypatch):
    """If YAML files are absent, seeding completes without raising and user exists."""
    # tmp_path is empty — no YAML files written
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))

    user = DbUser(username="empty_yaml_test_user")
    db_session.add(user)
    db_session.flush()

    # Must not raise
    _seed_new_user_from_yaml(db_session, user)
    db_session.flush()

    # User row exists, no data seeded
    assert db_session.query(DbUser).filter_by(username="empty_yaml_test_user").one()
    assert db_session.query(Location).filter_by(user_id=user.id).count() == 0
    assert db_session.query(AstroObject).filter_by(user_id=user.id).count() == 0


def test_new_user_does_not_inherit_guest_user_data(db_session, tmp_path, monkeypatch):
    """guest_user data must not appear on a new user seeded from YAML."""
    _write_test_yamls(tmp_path)
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))

    # Add a distinctive location to guest_user that is NOT in config_default.yaml
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    guest_location = Location(
        user_id=guest.id,
        name="Guest Only Location",
        lat=1.0,
        lon=1.0,
        timezone="UTC",
        altitude_threshold=10,
        is_default=True,
        active=True
    )
    db_session.add(guest_location)
    db_session.flush()

    new_user = DbUser(username="isolation_test_user")
    db_session.add(new_user)
    db_session.flush()

    _seed_new_user_from_yaml(db_session, new_user)
    db_session.flush()

    new_user_location_names = [
        loc.name for loc in
        db_session.query(Location).filter_by(user_id=new_user.id).all()
    ]

    assert "Guest Only Location" not in new_user_location_names
    assert "Test Observatory" in new_user_location_names


def test_get_or_create_db_user_uses_yaml_in_production_mode(db_session, tmp_path, monkeypatch):
    """get_or_create_db_user() must call _seed_new_user_from_yaml (not DB copy)
    when not in test mode."""
    _write_test_yamls(tmp_path)
    monkeypatch.setattr("nova.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("nova._is_test_environment", lambda: False)

    with app.app_context():
        user = get_or_create_db_user(db_session, "prod_mode_test_user")

    assert user is not None
    locations = db_session.query(Location).filter_by(user_id=user.id).all()
    objects = db_session.query(AstroObject).filter_by(user_id=user.id).all()
    assert len(locations) == 1
    assert locations[0].name == "Test Observatory"
    assert len(objects) == 1
    assert objects[0].object_name == "M42"
