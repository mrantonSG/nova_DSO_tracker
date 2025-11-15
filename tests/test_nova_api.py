import pytest
from datetime import date
import sys, os, io
import json
from unittest.mock import MagicMock # We'll need this for mocking

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import models needed for these tests
from nova import (
    DbUser,
    Location,
    AstroObject,
    Project,
    JournalSession,
    get_db,
    Component,
    Rig,
    UiPref,
    HorizonPoint
)

# --- Existing Tests (from your file) ---

def test_homepage_loads(client):
    """ Tests if the homepage (/) loads without errors. """
    response = client.get('/')
    assert response.status_code == 200
    assert b"<h1>Nova</h1>" in response.data
    assert b"DSO Tracker" in response.data

def test_su_login_bypass(su_client_not_logged_in):
    """
    Tests that in SINGLE_USER_MODE, an unauthenticated user
    is automatically logged in by the @before_request hook.
    """
    client = su_client_not_logged_in
    response = client.get('/config_form', follow_redirects=True)
    assert response.status_code == 200
    assert b"General Settings" in response.data
    assert b"Please log in to access this page." not in response.data

def test_mu_login_required(mu_client_logged_out):
    """
    Tests that in MULTI_USER_MODE, a logged-out user is redirected
    from a protected route to the login page.
    """
    # The 'mu_client_logged_out' fixture is in MU mode and not logged in.
    response = mu_client_logged_out.get('/config_form', follow_redirects=False)

    # Assert we are redirected (302) to the login page
    assert response.status_code == 302
    assert response.location.startswith('/login')

def test_mu_data_isolation(multi_user_client, db_session):
    """
    Tests that one user (UserA) cannot edit or view the data
    of another user (UserB).
    """
    client, user_ids = multi_user_client
    user_b_id = user_ids['user_b_id']

    session_for_user_b = JournalSession(
        user_id=user_b_id,
        date_utc=date(2025, 1, 1),
        object_name="USER_B_OBJECT"
    )
    db_session.add(session_for_user_b)
    db_session.commit()
    session_id_user_b = session_for_user_b.id

    response = client.get(f'/journal/edit/{session_id_user_b}', follow_redirects=True)
    assert response.status_code == 200
    assert b"Journal entry not found" in response.data

def test_get_plot_data_api(client):
    """
    Tests the main plotting data API endpoint for the graph dashboard.
    """
    response = client.get(
        '/api/get_plot_data/M42',
        query_string={
            'plot_loc_name': 'Default Test Loc',
            'plot_lat': 50,
            'plot_lon': 10,
            'plot_tz': 'UTC'
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "times" in data
    assert "object_alt" in data
    assert "sun_events" in data
    assert len(data['times']) > 0

# --- NEW TEST: Main Data API (Success) ---
def test_api_get_object_data_success(client):
    """
    Tests the most critical data API: /api/get_object_data
    This API powers the main object list on the homepage.
    """
    # 1. ARRANGE
    # The 'client' fixture already created "M42" (RA 5.58)
    # and a location (Lat 50.0).

    # 2. ACT
    response = client.get('/api/get_object_data/M42')
    data = response.get_json()

    # 3. ASSERT
    assert response.status_code == 200
    assert data['Object'] == 'M42'
    assert data['Common Name'] == 'Orion Nebula'
    assert data['error'] is False

    # Check for calculated fields
    assert 'Altitude Current' in data
    assert 'Transit Time' in data
    assert 'Max Altitude (°)'in data
    assert 'best_month_ra' in data
    assert 'max_culmination_alt' in data

    # Check calculations based on M42 (RA 5.58) and Lat 50.0
    # RA 5.58 / 2 = ~2.7, so month index 2 = "Dec"
    assert data['best_month_ra'] == 'Dec'
    # Max alt = 90 - abs(50 - (-5.4)) = 90 - 55.4 = 34.6
    assert data['max_culmination_alt'] == pytest.approx(34.6)


# --- NEW TEST: Main Data API (Not Found) ---
def test_api_get_object_data_not_found(client):
    """
    Tests that the object data API returns a proper 404
    error if the object is not in the user's database.
    """
    # 1. ACT
    response = client.get('/api/get_object_data/MISSING_OBJECT')
    data = response.get_json()

    # 3. ASSERT
    assert response.status_code == 404
    assert data['error'] is True
    assert "Error: Object 'MISSING_OBJECT' not found" in data['Common Name']


# --- NEW TEST: Weather API (with Mocking) ---
def test_api_get_weather_forecast(client, monkeypatch):
    """
    Tests the weather API by "monkeypatching" (mocking) the
    nova.get_hybrid_weather_forecast function to return fake data,
    bypassing the live web request.
    """
    # 1. ARRANGE
    # This is the mock data *returned by* get_hybrid_weather_forecast
    mock_forecast_data = {
        'init': '2025111200',
        'dataseries': [
            {
                'timepoint': 3,
                'cloudcover': 1,
                'seeing': 7,
                'transparency': 6
            }
        ]
    }

    # This line replaces the real function with a fake one (a lambda)
    # that just returns our mock data.
    monkeypatch.setattr('nova.get_hybrid_weather_forecast',
                        lambda lat, lon: mock_forecast_data)

    # 2. ACT
    response = client.get(
        '/api/get_weather_forecast',
        query_string={'lat': 50, 'lon': 10, 'tz': 'UTC'}
    )
    data = response.get_json()

    # 3. ASSERT
    assert response.status_code == 200
    assert 'weather_forecast' in data
    forecast_list = data['weather_forecast']

    # Check that the API correctly processed our mock data
    assert len(forecast_list) == 1
    assert forecast_list[0]['seeing'] == 7
    # Check that it converted 'timepoint' to 'start'/'end' ISO strings
    assert forecast_list[0]['start'] == '2025-11-12T03:00:00+00:00'
    assert forecast_list[0]['end'] == '2025-11-12T04:00:00+00:00'


# --- NEW TEST: Delete Workflow (with Guard Logic) ---
def test_delete_component_and_rig_routes(client, db_session):
    """
    Tests the delete workflow:
    1. Confirms a component in use by a rig CANNOT be deleted.
    2. Confirms the rig can be deleted.
    3. Confirms the component can be deleted *after* the rig is gone.
    """
    # 1. ARRANGE
    user = db_session.query(DbUser).filter_by(username="default").one()

    # Create components
    scope = Component(user_id=user.id, kind="telescope", name="Scope to Delete")
    cam = Component(user_id=user.id, kind="camera", name="Cam to Delete")
    db_session.add_all([scope, cam])
    db_session.commit()

    # Create a rig using the components
    rig = Rig(user_id=user.id, rig_name="Rig to Delete",
              telescope_id=scope.id, camera_id=cam.id)
    db_session.add(rig)
    db_session.commit()

    scope_id = scope.id
    rig_id = rig.id

    # 2. ACT (Attempt 1: Delete component while in use)
    response_fail = client.post(
        '/delete_component',
        data={'component_id': scope_id},
        follow_redirects=True
    )

    # 3. ASSERT (Attempt 1)
    assert response_fail.status_code == 200
    assert b"Cannot delete component: It is used in at least one rig." in response_fail.data
    # Check DB: Component should still exist
    assert db_session.get(Component, scope_id) is not None

    # 4. ACT (Attempt 2: Delete the rig first)
    response_rig_delete = client.post(
        '/delete_rig',
        data={'rig_id': rig_id},
        follow_redirects=True
    )

    # 5. ASSERT (Attempt 2)
    assert response_rig_delete.status_code == 200
    assert b"Rig deleted successfully." in response_rig_delete.data
    # Check DB: Rig should be gone
    assert db_session.get(Rig, rig_id) is None

    # 6. ACT (Attempt 3: Delete the component again)
    response_comp_delete = client.post(
        '/delete_component',
        data={'component_id': scope_id},
        follow_redirects=True
    )

    # 7. ASSERT (Attempt 3)
    assert response_comp_delete.status_code == 200
    assert b"Component deleted successfully." in response_comp_delete.data
    # Check DB: Component should now be gone
    assert db_session.get(Component, scope_id) is None

# --- NEW TEST: Add Object from SIMBAD Search ---
def test_api_confirm_object_from_simbad(client, db_session):
    """
    Tests the /confirm_object endpoint, which is used when
    a user adds a new object from the SIMBAD search modal.
    """
    # 1. ARRANGE
    # This payload mimics the data sent from the "Add to My Objects" modal
    simbad_payload = {
        "object": "M101",
        "name": "Pinwheel Galaxy",
        "ra": 14.057,
        "dec": 54.348,
        "constellation": "UMa",
        "type": "Galaxy",
        "magnitude": "7.9",
        "size": "28x26",
        "sb": "14.3",
        "project": "<p>My brand new notes for M101</p>",
        "shared_notes": "",
        "is_shared": False
    }

    # 2. ACT
    response = client.post('/confirm_object', json=simbad_payload)

    # 3. ASSERT
    assert response.status_code == 200
    assert response.get_json()['status'] == 'success'

    # Check the database directly to confirm it was saved
    user = db_session.query(DbUser).filter_by(username="default").one()
    new_obj = db_session.query(AstroObject).filter_by(
        user_id=user.id,
        object_name="M101"
    ).one_or_none()

    assert new_obj is not None
    assert new_obj.common_name == "Pinwheel Galaxy"
    assert new_obj.ra_hours == 14.057
    assert new_obj.project_name == "<p>My brand new notes for M101</p>"


# --- NEW TEST: Confirm Object Normalization ---
def test_api_confirm_object_normalization(client, db_session):
    """
    Tests that the /confirm_object endpoint correctly normalizes
    the object name (e.g., "M 42" -> "M42") before saving.
    """
    # 1. ARRANGE
    payload = {
        "object": "M 42",  # <-- Name with a space
        "name": "Orion",
        "ra": 5.58,
        "dec": -5.4,
        # ... other fields are not critical for this test
    }

    # 2. ACT
    client.post('/confirm_object', json=payload)

    # 3. ASSERT
    user = db_session.query(DbUser).filter_by(username="default").one()

    # Check that the object was saved with the NORMALIZED name
    obj_normalized = db_session.query(AstroObject).filter_by(
        user_id=user.id,
        object_name="M42"  # <-- The correct, normalized name
    ).one_or_none()
    assert obj_normalized is not None

    # Check that the "corrupt" name was NOT saved
    obj_corrupt = db_session.query(AstroObject).filter_by(
        user_id=user.id,
        object_name="M 42"
    ).one_or_none()
    assert obj_corrupt is None

# --- NEW TEST: Update Project Notes ---
def test_api_update_project_notes(client, db_session):
    """
    Tests the /update_project endpoint, which saves notes
    from the Trix editor on the graph dashboard.
    """
    # 1. ARRANGE
    # The 'client' fixture created M42. Let's get it.
    obj = db_session.query(AstroObject).filter_by(object_name="M42").one()
    assert obj.project_name is None  # Check initial state
    obj_id = obj.id  # <-- Get the ID before the object becomes stale

    notes_payload = {
        "object": "M42",
        "project": "<div>These are my <strong>new</strong> notes.</div>"
    }

    # 2. ACT
    response = client.post('/update_project', json=notes_payload)

    # 3. ASSERT
    assert response.status_code == 200
    assert response.get_json()['status'] == 'success'

    # --- FIX ---
    # The client.post() call commits a transaction in a separate
    # session, which detaches our local 'obj'.
    # We must re-fetch the object from the DB using our session.
    updated_obj = db_session.get(AstroObject, obj_id)

    assert updated_obj is not None
    assert updated_obj.project_name == "<div>These are my <strong>new</strong> notes.</div>"


# --- NEW TEST: Image Upload in Editor ---
def test_upload_editor_image(client, monkeypatch, tmp_path):
    """
    Tests the /upload_editor_image endpoint for the Trix editor.
    This test requires 'monkeypatch' and 'tmp_path' fixtures.
    """
    # 1. ARRANGE
    # Create a temporary folder for this test's uploads
    mock_upload_folder = tmp_path / "uploads"
    # Tell the app to use this temporary folder instead of the real one
    monkeypatch.setattr('nova.UPLOAD_FOLDER', str(mock_upload_folder))

    # Create a mock file in memory
    mock_file = io.BytesIO(b'fake-image-data-bytes')

    # 2. ACT
    response = client.post(
        '/upload_editor_image',
        data={
            # The form field name is 'file',
            # and we send it as a (file_object, filename) tuple
            'file': (mock_file, 'test_image.jpg')
        }
    )

    # 3. ASSERT
    # Check the API response
    assert response.status_code == 200
    data = response.get_json()
    assert 'url' in data
    url = data['url']

    # The URL should point to the 'default' user's folder
    # and have a new, unique, non-guessable filename.
    assert url.startswith('/uploads/default/note_img_')
    assert url.endswith('.jpg')

    # Get the unique filename from the URL
    new_filename = os.path.basename(url)

    # Check the file system (our temporary folder)
    expected_file_path = mock_upload_folder / 'default' / new_filename
    assert os.path.exists(expected_file_path)

    # Check the file content
    with open(expected_file_path, 'rb') as f:
        content = f.read()
    assert content == b'fake-image-data-bytes'


# ===================================================================
# --- NEW MOBILE ROUTE TESTS ---
# ===================================================================

@pytest.mark.parametrize("route", [
    "/m/up_now",
    "/m/location",
    "/m/outlook",
    "/m/add_object",
    "/m/edit_notes/M42"
])
def test_mobile_routes_require_login_multi_user(mu_client_logged_out, route):
    """
    Tests that all mobile pages (except login) redirect to the
    login page when logged out in multi-user mode.
    (This uses the 'mu_client_logged_out' fixture from conftest.py)
    """
    response = mu_client_logged_out.get(route, follow_redirects=False)

    # Assert we are redirected (302) to the login page
    assert response.status_code == 302
    assert response.location.startswith('/login')


@pytest.mark.parametrize("route", [
    "/m/up_now",
    "/m/location",
    "/m/outlook",
    "/m/add_object"
])
def test_mobile_pages_load_when_logged_in(client, route):
    """
    Tests that the main mobile pages load correctly for a
    logged-in user.
    (This uses the 'client' fixture, which is logged-in)
    """
    response = client.get(route)

    assert response.status_code == 200
    # Check for the header from mobile_base.html
    assert b"Nova Pocket" in response.data


def test_mobile_up_now_renders_data_from_server(client):
    """
    Tests our new high-performance "Up Now" page.
    It confirms that the data (M42 from the fixture) is rendered
    by the server directly into the HTML.
    """
    # 1. ACT
    response = client.get('/m/up_now')

    # 2. ASSERT
    assert response.status_code == 200

    # Check that M42 (from the 'client' fixture) is in the HTML
    assert b"M42" in response.data
    assert b"Orion Nebula" in response.data

    # Check that our new data attributes for sorting are present
    assert b"data-sort-alt=" in response.data
    assert b"data-sort-dur=" in response.data

    # CRITICAL: Check that the *old* slow JavaScript fetch loop is GONE.
    # This proves we are using the new server-side-rendered template.
    assert b"fetchAllObjects" not in response.data
    assert b"fetch(fetchUrlBase" not in response.data


# --- Your other existing tests/stubs from test_nova_api.py ---
# ...
def test_moon_api_bug_with_no_ra(client):
    pass

def test_graph_dashboard_404(client):
    pass

def test_add_journal_session_post(client):
    pass

def test_journal_add_redirects_when_logged_out(client_logged_out):
    pass

def test_add_journal_session_fails_with_bad_date(client):
    pass

def test_get_imaging_opportunities_success(client):
    pass

def test_get_imaging_opportunities_fail_moon(client, db_session):
    pass

def test_journal_edit_post(client, db_session):
    pass

def test_journal_delete_post(client, db_session):
    pass

def test_add_component_and_rig_routes(client, db_session):
    pass

def test_api_update_object(client, db_session):
    pass

def test_config_form_post_update_and_delete_locations(client, db_session):
    pass