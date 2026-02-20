# In tests/test_graph_dashboard.py
import pytest
import sys, os
from datetime import date

# Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the models we need to create test data
from nova import (
    DbUser,
    AstroObject,
    JournalSession,
    Component,
    Rig,
    get_db
)


def test_graph_dashboard_full_context(client, db_session):
    """
    Tests that the graph_dashboard page loads correctly and
    displays all its different data components:
    1. Object details (from the 'client' fixture's M42)
    2. Journal entries for that object
    3. Rig list for the FOV calculator
    """

    # 1. ARRANGE
    # The 'client' fixture is logged in as 'default' and already created M42.
    user = db_session.query(DbUser).filter_by(username="default").one()

    # Add a journal session for M42
    journal = JournalSession(
        user_id=user.id,
        date_utc=date(2025, 10, 10),
        object_name="M42",
        notes="Test session notes for dashboard"
    )
    db_session.add(journal)

    # Add a Component (Telescope)
    scope = Component(
        user_id=user.id,
        kind="telescope",
        name="Test Scope",
        aperture_mm=80,
        focal_length_mm=400
    )
    db_session.add(scope)

    # Add a Component (Camera)
    cam = Component(
        user_id=user.id,
        kind="camera",
        name="Test Camera",
        sensor_width_mm=20,
        sensor_height_mm=15,
        pixel_size_um=3.8
    )
    db_session.add(cam)

    # We must commit here so the scope and cam get IDs
    db_session.commit()

    # Add a Rig
    rig = Rig(
        user_id=user.id,
        rig_name="My FOV Rig",
        telescope_id=scope.id,
        camera_id=cam.id
    )
    db_session.add(rig)
    db_session.commit()

    # 2. ACT
    # Request the graph dashboard page for M42
    response = client.get('/graph_dashboard/M42')
    response_data = response.data  # Get data once for checks

    # 3. ASSERT
    assert response.status_code == 200

    # Check for Object Details
    assert b"M42" in response_data
    assert b"Orion Nebula" in response_data

    # --- FIX 1: Check for the correct chart ID ---
    assert b"chart-area" in response_data

    # Check for Journal Entry
    # --- FIX 2: Check for the date in YYYY-MM-DD format ---
    assert b"2025-10-10" in response_data
    # (The note "Test session notes..." is not visible in the list, so we don't check for it)

    # Check for Rig in FOV Calculator
    assert b"My FOV Rig" in response_data

    # --- FIX 3: Check for the correct calculated FOV in the data attribute ---
    assert b'data-fovw="171.85154209975758"' in response_data
    assert b'data-fovh="128.90039980471445"' in response_data