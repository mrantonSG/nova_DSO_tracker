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
from nova.record_links import sync_rig_links, sync_session_links


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
    sync_session_links(db_session, journal)   # the object page finds sessions by UID

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
    sync_rig_links(db_session, rig)
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


# --- SIMBAD tab: iframe when a host is known up, message when none is ---

def test_graph_dashboard_shows_simbad_iframe_when_a_host_is_known_up(client):
    from modules import simbad_service
    saved = dict(simbad_service._health)
    simbad_service._health.clear()
    try:
        simbad_service.mark_up("simbad.harvard.edu")
        response = client.get('/graph_dashboard/M42')
        body = response.data
        assert response.status_code == 200
        assert b'id="simbadIframe"' in body
        assert b'id="simbadUnavailable"' not in body
        # The iframe must be bound to the known-up host, not the default.
        assert b'simbad.harvard.edu' in body
    finally:
        simbad_service._health.clear()
        simbad_service._health.update(saved)


def test_graph_dashboard_shows_simbad_unavailable_message_when_all_hosts_down(client):
    from modules import simbad_service
    saved = dict(simbad_service._health)
    simbad_service._health.clear()
    try:
        for host in simbad_service.HOSTS:
            simbad_service.mark_down(host)
        response = client.get('/graph_dashboard/M42')
        body = response.data
        assert response.status_code == 200
        assert b'id="simbadIframe"' not in body
        assert b'id="simbadUnavailable"' in body
        assert b'SIMBAD is currently unavailable. Please try again later.' in body
    finally:
        simbad_service._health.clear()
        simbad_service._health.update(saved)
