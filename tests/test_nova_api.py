import pytest
from datetime import date
import sys, os

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import only what the TESTS need
from nova import (
    DbUser,
    Location,
    AstroObject,
    Project,
    JournalSession,
    get_db
)

# --- All Fixtures are GONE (moved to conftest.py) ---


# --- 5. Your API Tests (Unchanged) ---

def test_homepage_loads(client):
    """ Tests if the homepage (/) loads without errors. """
    response = client.get('/')
    assert response.status_code == 200
    assert b"<h1>Nova</h1>" in response.data
    assert b"DSO Tracker" in response.data

# ... (all your other tests remain exactly as they are) ...

def test_moon_api_bug_with_no_ra(client):
    # ...
    pass

def test_graph_dashboard_404(client):
    # ...
    pass

def test_add_journal_session_post(client):
    # ...
    pass

def test_journal_add_redirects_when_logged_out(client_logged_out):
    # ...
    pass

def test_add_journal_session_fails_with_bad_date(client):
    # ...
    pass


def test_su_login_bypass(client):
    """
    Tests that in SINGLE_USER_MODE, a user is automatically logged in.
    We can test this by checking that the client fixture (which is in SU mode)
    has a user_id.
    """
    # The 'client' fixture is already logged in via @before_request
    # We can check that the flash message for 'login required' is NOT present
    # when accessing a protected route.
    response = client.get('/config_form')
    assert response.status_code == 200
    assert b"Please log in to access this page." not in response.data
    assert b"General Settings" in response.data  # Check we are on the config page


def test_mu_login_required(client_logged_out):
    """
    Tests that in MULTI_USER_MODE, a logged-out user is redirected
    from a protected route to the login page.
    """
    # The 'client_logged_out' fixture is in MU mode and not logged in.
    response = client_logged_out.get('/config_form', follow_redirects=False)

    # Assert we are redirected (302) to the login page
    assert response.status_code == 302
    assert response.location.startswith('/login')


def test_mu_data_isolation(multi_user_client, db_session):
    """
    Tests that one user (UserA) cannot edit or view the data
    of another user (UserB).
    """
    # 1. ARRANGE
    # The 'multi_user_client' is logged in as UserA
    client, user_ids = multi_user_client
    user_b_id = user_ids['user_b_id']

    # Create an object that belongs to UserB
    obj_for_user_b = AstroObject(
        user_id=user_b_id,
        object_name="USER_B_OBJECT",
        common_name="UserB's Private Object",
        ra_hours=10,
        dec_deg=10
    )
    db_session.add(obj_for_user_b)

    # Create a journal session for UserB
    session_for_user_b = JournalSession(
        user_id=user_b_id,
        date_utc=date(2025, 1, 1),
        object_name="USER_B_OBJECT"
    )
    db_session.add(session_for_user_b)
    db_session.commit()

    # Get the ID of UserB's session
    session_id_user_b = session_for_user_b.id

    # 2. ACT
    # UserA (the logged-in client) tries to access the edit page
    # for UserB's journal session.
    response = client.get(f'/journal/edit/{session_id_user_b}', follow_redirects=True)

    # 3. ASSERT
    # The app should not find the session (as it queries by user_id)
    # and should redirect with a "not found" error.
    assert response.status_code == 200  # Lands on the journal list page
    assert b"Journal entry not found" in response.data
    assert b"UserB's Private Object" not in response.data  # Does not load data