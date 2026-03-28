"""
Automated upgrade simulation test to prevent missing DB patch regressions.

This test simulates a real-world upgrade scenario:
1. Creates a "minimal baseline" database (simulating an old schema with no patched columns)
2. Runs the startup DB patch routine against this old database
3. Dynamically verifies all columns defined in models.py are present after patching

If a developer adds a column to models.py without adding a corresponding DB patch,
this test will FAIL with a clear message identifying exactly which column is missing.

This test is SELF-MAINTAINING:
- It introspects all SQLAlchemy model columns at runtime
- No manual updates required when adding new tables/columns
- CI will catch missing patches before release
"""

import pytest
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

# Import the patch function and models
from nova import _run_schema_patches
from nova.models import Base


# =============================================================================
# MINIMAL BASELINE SCHEMA
# =============================================================================
# Individual SQL statements for the minimal baseline.
# This represents the "oldest possible" database state - tables with only
# the bare minimum columns needed for the schema to be valid.
# All columns that were added via patches over time are intentionally omitted.

MINIMAL_BASELINE_STATEMENTS = [
    # users: core identity table
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username VARCHAR(80) NOT NULL UNIQUE,
        password_hash VARCHAR(256),
        active BOOLEAN NOT NULL DEFAULT 1
    )""",

    # locations: bare minimum (no stable_uid)
    """CREATE TABLE locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(128) NOT NULL,
        lat FLOAT NOT NULL,
        lon FLOAT NOT NULL,
        timezone VARCHAR(64) NOT NULL,
        altitude_threshold FLOAT,
        is_default BOOLEAN NOT NULL DEFAULT 0,
        active BOOLEAN NOT NULL DEFAULT 1,
        comments VARCHAR(500)
    )""",

    # horizon_points
    """CREATE TABLE horizon_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
        az_deg FLOAT NOT NULL,
        alt_min_deg FLOAT NOT NULL
    )""",

    # astro_objects: bare minimum (no patched columns like is_shared, stable_uid, etc.)
    """CREATE TABLE astro_objects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        object_name VARCHAR(256) NOT NULL,
        common_name VARCHAR(256),
        ra_hours FLOAT NOT NULL,
        dec_deg FLOAT NOT NULL,
        type VARCHAR(128),
        constellation VARCHAR(64),
        magnitude VARCHAR(32),
        size VARCHAR(64),
        sb VARCHAR(64),
        active_project BOOLEAN NOT NULL DEFAULT 0,
        project_name TEXT
    )""",

    # components: bare minimum (no stable_uid, sharing columns)
    """CREATE TABLE components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind VARCHAR(32) NOT NULL,
        name VARCHAR(256) NOT NULL,
        aperture_mm FLOAT,
        focal_length_mm FLOAT,
        sensor_width_mm FLOAT,
        sensor_height_mm FLOAT,
        pixel_size_um FLOAT,
        factor FLOAT
    )""",

    # rigs: bare minimum (no stable_uid, guide columns)
    """CREATE TABLE rigs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rig_name VARCHAR(256) NOT NULL,
        telescope_id INTEGER REFERENCES components(id) ON DELETE SET NULL,
        camera_id INTEGER REFERENCES components(id) ON DELETE SET NULL,
        reducer_extender_id INTEGER REFERENCES components(id) ON DELETE SET NULL,
        effective_focal_length FLOAT,
        f_ratio FLOAT,
        image_scale FLOAT,
        fov_w_arcmin FLOAT
    )""",

    # projects: bare minimum (no description/notes columns)
    """CREATE TABLE projects (
        id VARCHAR(64) PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(256) NOT NULL
    )""",

    # saved_views: bare minimum (no description, sharing columns)
    """CREATE TABLE saved_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(256) NOT NULL,
        settings_json TEXT NOT NULL
    )""",

    # saved_framings: bare minimum (no rig snapshot, mosaic, image adjustment columns)
    """CREATE TABLE saved_framings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        object_name VARCHAR(256) NOT NULL,
        rig_id INTEGER REFERENCES rigs(id) ON DELETE SET NULL,
        ra FLOAT,
        dec FLOAT,
        rotation FLOAT,
        survey VARCHAR(256),
        blend_survey VARCHAR(256),
        blend_opacity FLOAT,
        updated_at DATE
    )""",

    # journal_sessions: bare minimum
    # This is the most critical table - many columns were added over time
    """CREATE TABLE journal_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        project_id VARCHAR(64) REFERENCES projects(id) ON DELETE SET NULL,
        date_utc DATE NOT NULL,
        object_name VARCHAR(256),
        notes TEXT,
        session_image_file VARCHAR(256),
        location_name VARCHAR(128),
        seeing_observed_fwhm FLOAT,
        sky_sqm_observed FLOAT,
        moon_illumination_session INTEGER,
        moon_angular_separation_session FLOAT,
        weather_notes TEXT,
        telescope_setup_notes TEXT,
        filter_used_session VARCHAR(128),
        guiding_rms_avg_arcsec FLOAT,
        guiding_equipment VARCHAR(256),
        dither_details VARCHAR(256),
        acquisition_software VARCHAR(128),
        gain_setting INTEGER,
        offset_setting INTEGER,
        camera_temp_setpoint_c FLOAT,
        camera_temp_actual_avg_c FLOAT,
        binning_session VARCHAR(16),
        darks_strategy TEXT,
        flats_strategy TEXT,
        bias_darkflats_strategy TEXT,
        session_rating_subjective INTEGER,
        transparency_observed_scale VARCHAR(64),
        number_of_subs_light INTEGER,
        exposure_time_per_sub_sec INTEGER,
        filter_L_subs INTEGER,
        filter_L_exposure_sec INTEGER,
        filter_R_subs INTEGER,
        filter_R_exposure_sec INTEGER,
        filter_G_subs INTEGER,
        filter_G_exposure_sec INTEGER,
        filter_B_subs INTEGER,
        filter_B_exposure_sec INTEGER,
        filter_Ha_subs INTEGER,
        filter_Ha_exposure_sec INTEGER,
        filter_OIII_subs INTEGER,
        filter_OIII_exposure_sec INTEGER,
        filter_SII_subs INTEGER,
        filter_SII_exposure_sec INTEGER,
        calculated_integration_time_minutes FLOAT
    )""",

    # ui_prefs
    """CREATE TABLE ui_prefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        json_blob TEXT
    )""",
]


def get_model_columns():
    """
    Introspect all SQLAlchemy models and return expected table->column mappings.
    This makes the test SELF-MAINTAINING - any new column added to models.py
    will automatically be checked.
    """
    expected = {}

    # SQLAlchemy 2.x compatible: iterate mappers via the registry
    for mapper in Base.registry.mappers:
        table_name = mapper.persist_selectable.name
        columns = {col.name for col in mapper.persist_selectable.columns}
        expected[table_name] = columns

    return expected


def get_actual_columns(conn, table_name):
    """
    Query PRAGMA table_info() to get actual columns in the database.
    Returns a set of column names.
    """
    result = conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
    return {row[1] for row in result}  # row[1] is column name


def test_db_upgrade_simulation():
    """
    Simulates a real-world upgrade scenario to catch missing DB patches.

    FAIL SCENARIO:
    - Developer adds a column to models.py
    - Developer forgets to add the ALTER TABLE statement to _run_schema_patches
    - CI runs this test -> FAILS with clear message about missing column

    SUCCESS SCENARIO:
    - All columns in models.py have corresponding patches
    - Test passes
    """
    # Create an in-memory SQLite database with the MINIMAL baseline schema
    # This simulates an "old" database before any patches were applied
    engine = create_engine("sqlite:///:memory:")

    # Create the minimal baseline schema (simulates old database)
    # Execute statements one at a time (SQLite limitation)
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)

    # Now run the patch routine against this "old" database
    # This is the actual production code that runs on app startup
    with engine.begin() as conn:
        _run_schema_patches(conn)

    # Get all expected columns from SQLAlchemy models
    expected_columns = get_model_columns()

    # Verify every column exists in the patched database
    missing_columns = {}

    with engine.connect() as conn:
        for table_name, expected in expected_columns.items():
            # Skip tables that are created whole by patches (not ALTERed)
            if table_name in ('analytics_event', 'analytics_login', 'user_custom_filters'):
                # Verify these tables exist
                table_exists = conn.exec_driver_sql(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
                ).fetchone()
                if not table_exists:
                    missing_columns[table_name] = {"TABLE MISSING"}
                continue

            # Check if table exists
            table_exists = conn.exec_driver_sql(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
            ).fetchone()

            if not table_exists:
                # Table doesn't exist - report it
                missing_columns[table_name] = expected
                continue

            actual = get_actual_columns(conn, table_name)

            # Find columns that exist in the model but not in the database
            missing = expected - actual
            if missing:
                missing_columns[table_name] = missing

    # Report results
    if missing_columns:
        error_msg = """
================================================================================
DB UPGRADE SIMULATION TEST FAILED
================================================================================

The following columns are defined in models.py but have NO corresponding DB patch.
This means users upgrading from an older version will have a BROKEN database.

MISSING COLUMNS:
"""
        for table, columns in missing_columns.items():
            if columns == {"TABLE MISSING"}:
                error_msg += f"\n  Table '{table}': ENTIRE TABLE IS MISSING\n"
            else:
                error_msg += f"\n  Table '{table}':\n"
                for col in sorted(columns):
                    error_msg += f"    - {col}\n"

        error_msg += """
HOW TO FIX:
1. Open nova/__init__.py
2. Find the _run_schema_patches() function
3. Add the missing column(s) with ALTER TABLE statements, e.g.:

    cols_{table} = conn.exec_driver_sql("PRAGMA table_info({table});").fetchall()
    colnames_{table} = {{row[1] for row in cols_{table}}}
    if "column_name" not in colnames_{table}:
        conn.exec_driver_sql("ALTER TABLE {table} ADD COLUMN column_name TYPE;")
        print("[DB PATCH] Added missing column {table}.column_name")

This test ensures that future schema changes are always accompanied by
corresponding upgrade patches, preventing the v5.2 regression.
================================================================================
"""
        pytest.fail(error_msg)


def test_db_upgrade_simulation_fresh_db():
    """
    Verify that a fresh database (via create_all) matches the patched schema.
    This catches cases where the patch logic diverges from model definitions.
    """
    # Create a fresh database using create_all (what tests normally use)
    fresh_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(fresh_engine)

    # Create a patched database (upgrade simulation)
    patched_engine = create_engine("sqlite:///:memory:")
    with patched_engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
    with patched_engine.begin() as conn:
        _run_schema_patches(conn)

    # Get expected columns from models
    expected_columns = get_model_columns()

    # Compare fresh vs patched
    discrepancies = {}

    tables_to_check = [t for t in expected_columns.keys()
                       if t not in ('analytics_event', 'analytics_login', 'user_custom_filters')]

    with fresh_engine.connect() as fresh_conn, patched_engine.connect() as patched_conn:
        for table_name in tables_to_check:
            fresh_cols = get_actual_columns(fresh_conn, table_name)
            patched_cols = get_actual_columns(patched_conn, table_name)

            # Columns in fresh but not in patched = missing patch
            missing_in_patched = fresh_cols - patched_cols
            if missing_in_patched:
                if table_name not in discrepancies:
                    discrepancies[table_name] = {}
                discrepancies[table_name]['missing_in_patched'] = missing_in_patched

            # Columns in patched but not in fresh = stale patch or model drift
            extra_in_patched = patched_cols - fresh_cols
            if extra_in_patched:
                if table_name not in discrepancies:
                    discrepancies[table_name] = {}
                discrepancies[table_name]['extra_in_patched'] = extra_in_patched

    if discrepancies:
        error_msg = """
================================================================================
DB SCHEMA DRIFT DETECTED
================================================================================

The patched schema differs from a fresh create_all() schema.
This indicates either:
1. A column was added to models.py but not to patches (test above should catch this)
2. A patch adds a column that was removed from models.py (stale patch)
3. A column type or constraint differs between patch and model

DISCREPANCIES:
"""
        for table, issues in discrepancies.items():
            error_msg += f"\n  Table '{table}':\n"
            if 'missing_in_patched' in issues:
                error_msg += f"    Missing in patched (need patch): {issues['missing_in_patched']}\n"
            if 'extra_in_patched' in issues:
                error_msg += f"    Extra in patched (stale patch?): {issues['extra_in_patched']}\n"

        error_msg += "\n" + "=" * 80 + "\n"
        pytest.fail(error_msg)


if __name__ == "__main__":
    # Run the test standalone for quick verification
    test_db_upgrade_simulation()
    print("\n[SUCCESS] DB upgrade simulation test passed!")
