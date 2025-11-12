import pytest
from datetime import date
import sys, os

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import everything we need
from nova import (
    app,
    Base,
    DbUser,
    Location,
    AstroObject,
    Project,
    JournalSession,
    get_db,
    get_or_create_db_user,
    SessionLocal,  # <-- Import the app's Session maker
    UserMixin
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session


# --- 3. The Database Fixture (This is the main fix) ---
# This fixture creates the DB, the session, and patches the app
@pytest.fixture(scope="function")
def db_session(monkeypatch):
    """
    Creates a new, empty, in-memory database AND patches the app
    to use this database for all its operations.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # Create a new ScopedSession that will be shared
    TestSession = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )
    session = TestSession()

    # --- THIS IS THE CRITICAL FIX ---
    # We are overriding the app's internal SessionLocal *and* get_db
    # to use our new test session.
    monkeypatch.setattr(sys.modules[__name__], 'SessionLocal', TestSession)
    monkeypatch.setattr('nova.SessionLocal', TestSession)
    monkeypatch.setattr('nova.get_db', TestSession)
    # --- END CRITICAL FIX ---

    # Add the guest_user template
    guest_user = DbUser(username="guest_user")
    session.add(guest_user)
    session.commit()

    try:
        yield session
    finally:
        session.close()
        TestSession.remove()
        Base.metadata.drop_all(engine)


# --- 4. The Logged-In Client Fixture ---
@pytest.fixture
def client(db_session, monkeypatch): # <-- Add monkeypatch
    """
    Creates a Flask test client connected to our in-memory db_session
    and logged in as the 'default' user.
    """
    # --- START FIX: Force SINGLE_USER_MODE ---
    # This makes the test client log in as the "default" user successfully
    monkeypatch.setattr('nova.SINGLE_USER_MODE', True)

    # --- NEW FIX: Patch the User class to match SINGLE_USER_MODE ---
    # Define the simple User class that SINGLE_USER_MODE expects
    class SingleUserTest(UserMixin):
        def __init__(self, user_id, username):
            self.id = user_id
            self.username = username

    # Patch the 'User' name in the nova module to point to this simple class
    monkeypatch.setattr('nova.User', SingleUserTest)
    # --- END FIX ---

    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    # Create the 'default' user and location
    user = get_or_create_db_user(db_session, "default")
    location = Location(
        user_id=user.id,
        name="Default Test Loc",
        lat=50,
        lon=10,
        timezone="UTC",
        is_default=True
    )
    db_session.add(location)
    db_session.commit()

    with app.test_client() as client:
        # Log in as the 'default' user
        with client.session_transaction() as sess:
            sess['_user_id'] = 'default'
            sess['_fresh'] = True

        # Prime the session to trigger @before_request hooks
        client.get('/')

        yield client


# --- 5. Your API Tests ---

def test_homepage_loads(client):
    """ Tests if the homepage (/) loads without errors. """
    response = client.get('/')
    assert response.status_code == 200
    assert b"<h1>Nova</h1>" in response.data
    assert b"DSO Tracker" in response.data


def test_moon_api_bug_with_no_ra(client):
    """ Tests the moon API. """
    response = client.get(
        '/api/get_moon_data?date=2025-01-01&lat=50&lon=10&ra=&dec=&tz=UTC'
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'success'
    assert data['moon_illumination'] is not None
    assert data['angular_separation'] is None


def test_graph_dashboard_404(client):
    """ Tests 404 redirect. """
    response = client.get('/graph_dashboard/ObjectThatDoesNotExist', follow_redirects=True)
    assert response.status_code == 200
    # --- START FIX: Check for the correct flash message text ---
    assert b"Object &#39;ObjectThatDoesNotExist&#39; not found in your configuration." in response.data
    # --- END FIX ---


def test_add_journal_session_post(client):
    """
    Tests that a user can successfully submit a new journal session
    via a POST request and that all data is saved correctly.
    """
    # 1. ARRANGE
    # We get the *same* session that the client and app are using
    db = get_db()
    user = db.query(DbUser).filter_by(username="default").one()

    # Create a project to link to
    test_project = Project(id="proj_123", user_id=user.id, name="My Test Project")
    db.add(test_project)

    # Create an object (M42) that the seeder did *not* create
    # (since the test guest_user is empty)
    test_object = AstroObject(
        user_id=user.id,
        object_name="M42",
        common_name="Orion Nebula",
        ra_hours=5.58,
        dec_deg=-5.4
    )
    db.add(test_object)
    db.commit()  # Commit the project and object

    form_data = {
        'session_date': '2025-11-12',
        'location_name': 'Default Test Loc',
        'target_object_id': 'M42',
        'seeing_observed_fwhm': 2.5,
        'guiding_rms_avg_arcsec': 0.5,
        'project_selection': 'proj_123',
        'moon_illumination_session': 8.0,
        'moon_angular_separation_session': 95.2,
        'general_notes_problems_learnings': '<p>Test notes</p>'
    }

    # 2. ACT
    response = client.post('/journal/add', data=form_data, follow_redirects=False)

    # 3. ASSERT
    assert response.status_code == 302
    assert response.location.startswith('/graph_dashboard/M42')

    # --- This is the check that failed last time ---
    new_session = db.query(JournalSession).filter_by(object_name="M42").one_or_none()

    assert new_session is not None
    assert new_session.seeing_observed_fwhm == 2.5
    assert new_session.notes == '<p>Test notes</p>'
    assert new_session.project_id == 'proj_123'
    assert new_session.moon_illumination_session == 8.0


@pytest.fixture
def client_logged_out(db_session, monkeypatch): # <-- Add monkeypatch
    """
    Creates a Flask test client that is NOT logged in,
    but still uses the in-memory test database.
    """
    # --- START FIX: Force MULTI_USER_MODE ---
    # This makes the app correctly require a login
    monkeypatch.setattr('nova.SINGLE_USER_MODE', False)
    # --- END FIX ---

    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    # We still need the 'default' user and location
    # for the homepage redirect to work.
    user = get_or_create_db_user(db_session, "default")
    location = Location(
        user_id=user.id,
        name="Default Test Loc",
        lat=50,
        lon=10,
        timezone="UTC",
        is_default=True
    )
    db_session.add(location)
    db_session.commit()

    with app.test_client() as client:
        yield client  # The test runs here

def test_journal_add_redirects_when_logged_out(client_logged_out):
    """
    Tests that a logged-out user is redirected to the login page
    when trying to access a @login_required route.
    """
    # 1. Act
    # Use the new 'client_logged_out' fixture
    response = client_logged_out.get('/journal/add', follow_redirects=False)

    # 2. Assert
    # It should be a 302 Redirect
    assert response.status_code == 302
    # It should be redirecting to the '/login' page
    # --- START FIX: Check for the correct redirect URL ---
    assert response.location == '/login?next=%2Fjournal%2Fadd'
    # --- END FIX ---


def test_add_journal_session_fails_with_bad_date(client):
    """
    Tests that submitting a journal with an invalid date
    does not create a new session and returns an error.
    """
    # 1. ARRANGE
    db = get_db()

    # We need to create the 'M42' object so the redirect target exists
    user = db.query(DbUser).filter_by(username="default").one()
    test_object = AstroObject(
        user_id=user.id,
        object_name="M42",
        common_name="Orion Nebula",  # <-- This is the name we check for
        ra_hours=5.58,
        dec_deg=-5.4
    )
    db.add(test_object)
    db.commit()

    form_data = {
        'session_date': 'NOT-A-DATE',  # Invalid data
        'location_name': 'Default Test Loc',
        'target_object_id': 'M42',
        'project_selection': 'standalone'
    }

    # 2. ACT
    # Send the bad data. We set follow_redirects=True
    # to catch the flash message on the *next* page.
    response = client.post('/journal/add', data=form_data, follow_redirects=True)

    # 3. ASSERT
    # It should NOT create a session, but it should redirect
    # and land successfully (200 OK) on the object's page.
    assert response.status_code == 200

    # --- THIS IS THE FIX ---
    assert b"Orion Nebula" in response.data  # Check we landed on the right page
    # --- END OF FIX ---

    assert b"Invalid date format." in response.data  # Check for our flash message

    # --- The most important check ---
    # Make sure NOTHING was added to the database
    session_count = db.query(JournalSession).count()
    assert session_count == 0