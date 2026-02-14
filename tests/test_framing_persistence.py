"""
Round-trip persistence tests for SavedFraming YAML export/import.

This test ensures that SavedFraming records can be exported to YAML and
re-imported without losing any data, validating the YAML pipeline.
"""
import pytest
import math

from nova import _migrate_saved_framings
from nova.models import SavedFraming, DbUser


def test_framing_round_trip_full_record(db_session):
    """
    Round-trip test: Create SavedFraming -> Export to YAML dict -> Delete -> Import -> Verify 100% match.

    Tests a SavedFraming with non-default values for ALL fields to ensure
    the YAML export/import pipeline preserves data correctly.
    """
    # 1. Get a user for the test
    user = db_session.query(DbUser).first()
    if not user:
        user = DbUser(username="test_user")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    # 2. Create a SavedFraming record with non-default values
    # Note: Using a rig reference would require additional setup, so we use a rig_name only
    original_framing = SavedFraming(
        user_id=user.id,
        object_name="TestM31",
        rig_id=None,  # No rig, just name for portability
        rig_name="TestRig-300mm",
        ra=10.684,  # M31 RA in hours
        dec=41.269,  # M31 Dec in degrees
        rotation=45.0,  # Non-zero rotation
        survey="DSS2",
        blend_survey="H-alpha",
        blend_opacity=0.7,
        # Mosaic Data - non-default values
        mosaic_cols=3,
        mosaic_rows=3,
        mosaic_overlap=15.0,
        # Image Adjustment Data - non-default values
        img_brightness=10.0,
        img_contrast=5.0,
        img_gamma=1.5,  # Non-default gamma
        img_saturation=-10.0,
        # Overlay Preferences - geo_belt OFF
        geo_belt_enabled=False
    )
    db_session.add(original_framing)
    db_session.commit()
    db_session.refresh(original_framing)
    original_id = original_framing.id

    # 3. Export to YAML dictionary (simulating what export_user_to_yaml does)
    yaml_dict = {
        "object_name": original_framing.object_name,
        "rig_name": original_framing.rig_name,
        "ra": original_framing.ra,
        "dec": original_framing.dec,
        "rotation": original_framing.rotation,
        "survey": original_framing.survey,
        "blend_survey": original_framing.blend_survey,
        "blend_opacity": original_framing.blend_opacity,
        "mosaic_cols": original_framing.mosaic_cols,
        "mosaic_rows": original_framing.mosaic_rows,
        "mosaic_overlap": original_framing.mosaic_overlap,
        "img_brightness": original_framing.img_brightness,
        "img_contrast": original_framing.img_contrast,
        "img_gamma": original_framing.img_gamma,
        "img_saturation": original_framing.img_saturation,
        "geo_belt_enabled": original_framing.geo_belt_enabled
    }

    # 4. Delete the original record
    db_session.delete(original_framing)
    db_session.commit()

    # Verify it's deleted
    assert db_session.query(SavedFraming).filter_by(id=original_id).one_or_none() is None

    # 5. Import from YAML dictionary (simulating what _migrate_saved_framings does)
    config_dict = {"saved_framings": [yaml_dict]}
    _migrate_saved_framings(db_session, user, config_dict)
    db_session.commit()

    # 6. Verify the new record exists and matches original values
    imported_framing = db_session.query(SavedFraming).filter_by(
        user_id=user.id,
        object_name="TestM31"
    ).one()

    # Verify ALL fields match 100%
    assert imported_framing.object_name == original_framing.object_name
    assert imported_framing.rig_name == original_framing.rig_name
    assert imported_framing.rig_id is None  # No rig ID for portability test
    assert math.isclose(imported_framing.ra, original_framing.ra, rel_tol=1e-9)
    assert math.isclose(imported_framing.dec, original_framing.dec, rel_tol=1e-9)
    assert math.isclose(imported_framing.rotation, original_framing.rotation, rel_tol=1e-9)
    assert imported_framing.survey == original_framing.survey
    assert imported_framing.blend_survey == original_framing.blend_survey
    assert math.isclose(imported_framing.blend_opacity, original_framing.blend_opacity, rel_tol=1e-9)
    assert imported_framing.mosaic_cols == original_framing.mosaic_cols
    assert imported_framing.mosaic_rows == original_framing.mosaic_rows
    assert math.isclose(imported_framing.mosaic_overlap, original_framing.mosaic_overlap, rel_tol=1e-9)
    assert math.isclose(imported_framing.img_brightness, original_framing.img_brightness, rel_tol=1e-9)
    assert math.isclose(imported_framing.img_contrast, original_framing.img_contrast, rel_tol=1e-9)
    assert math.isclose(imported_framing.img_gamma, original_framing.img_gamma, rel_tol=1e-9)
    assert math.isclose(imported_framing.img_saturation, original_framing.img_saturation, rel_tol=1e-9)
    assert imported_framing.geo_belt_enabled == original_framing.geo_belt_enabled


def test_framing_import_with_defaults(db_session):
    """
    Test that importing a YAML dict with missing fields uses proper defaults.

    This ensures backward compatibility with YAML exports that may not
    include all fields (e.g., from older versions).
    """
    # Get or create a user
    user = db_session.query(DbUser).first()
    if not user:
        user = DbUser(username="test_user")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    # Import a minimal YAML dict (only required fields)
    minimal_yaml_dict = {
        "object_name": "MinimalFraming",
        # All other fields missing - should use defaults
    }

    config_dict = {"saved_framings": [minimal_yaml_dict]}
    _migrate_saved_framings(db_session, user, config_dict)
    db_session.commit()

    # Verify the record was created with defaults
    imported_framing = db_session.query(SavedFraming).filter_by(
        user_id=user.id,
        object_name="MinimalFraming"
    ).one()

    # Verify defaults are applied correctly
    assert imported_framing.mosaic_cols == 1
    assert imported_framing.mosaic_rows == 1
    assert math.isclose(imported_framing.mosaic_overlap, 10.0)
    assert math.isclose(imported_framing.img_brightness, 0.0)
    assert math.isclose(imported_framing.img_contrast, 0.0)
    assert math.isclose(imported_framing.img_gamma, 1.0)
    assert math.isclose(imported_framing.img_saturation, 0.0)
    assert imported_framing.geo_belt_enabled is True


def test_framing_upsert_logic(db_session):
    """
    Test that importing updates existing framings instead of creating duplicates.

    This verifies the "upsert" (update-or-insert) logic in _migrate_saved_framings.
    """
    # Get or create a user
    user = db_session.query(DbUser).first()
    if not user:
        user = DbUser(username="test_user")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    # Create initial framing
    initial_framing = SavedFraming(
        user_id=user.id,
        object_name="UpsertTest",
        img_gamma=1.0,
        mosaic_cols=1
    )
    db_session.add(initial_framing)
    db_session.commit()
    db_session.refresh(initial_framing)
    initial_id = initial_framing.id

    # Import updated values for the same object_name
    updated_yaml_dict = {
        "object_name": "UpsertTest",
        "img_gamma": 1.8,  # Changed
        "mosaic_cols": 2  # Changed
    }

    config_dict = {"saved_framings": [updated_yaml_dict]}
    _migrate_saved_framings(db_session, user, config_dict)
    db_session.commit()

    # Verify it's the same record (same ID), not a duplicate
    updated_framing = db_session.query(SavedFraming).filter_by(
        user_id=user.id,
        object_name="UpsertTest"
    ).one()

    assert updated_framing.id == initial_id
    assert math.isclose(updated_framing.img_gamma, 1.8)
    assert updated_framing.mosaic_cols == 2

    # Verify no duplicate was created
    count = db_session.query(SavedFraming).filter_by(
        user_id=user.id,
        object_name="UpsertTest"
    ).count()
    assert count == 1
