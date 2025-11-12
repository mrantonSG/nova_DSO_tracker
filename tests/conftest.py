import pytest
import sys, os
from datetime import date

# Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import everything we need for the fixtures
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
    SessionLocal,
    UserMixin
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

# --- Database Fixture ---
@pytest.fixture(scope="function")
def db_session(monkeypatch):
    """
    Creates a new, empty, in-memory database AND patches the app
    to use this database for all its operations.
    (Moved from test_nova_api.py)
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    TestSession = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )
    session = TestSession()

    monkeypatch.setattr(sys.modules[__name__], 'SessionLocal', TestSession)
    monkeypatch.setattr('nova.SessionLocal', TestSession)
    monkeypatch.setattr('nova.get_db', TestSession)

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


# --- Logged-In Client Fixture ---
@pytest.fixture
def client(db_session, monkeypatch):
    """
    Creates a Flask test client connected to our in-memory db_session
    and logged in as the 'default' user.
    (Moved from test_nova_api.py)
    """
    monkeypatch.setattr('nova.SINGLE_USER_MODE', True)

    class SingleUserTest(UserMixin):
        def __init__(self, user_id, username):
            self.id = user_id
            self.username = username

    monkeypatch.setattr('nova.User', SingleUserTest)

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
        with client.session_transaction() as sess:
            sess['_user_id'] = 'default'
            sess['_fresh'] = True
        client.get('/') # Prime the session
        yield client

# --- Logged-Out Client Fixture ---
@pytest.fixture
def client_logged_out(db_session, monkeypatch):
    """
    Creates a Flask test client that is NOT logged in.
    (Moved from test_nova_api.py)
    """
    monkeypatch.setattr('nova.SINGLE_USER_MODE', False)

    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

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
        yield client


@pytest.fixture
def multi_user_client(db_session, monkeypatch):
    """
    Creates a Flask test client for a MULTI-USER environment.

    - Sets SINGLE_USER_MODE = False
    - Creates two app users: "UserA" and "UserB"
    - Creates one auth user: "UserA"
    - Logs in the client as "UserA"
    """
    # 1. Force Multi-User Mode
    monkeypatch.setattr('nova.SINGLE_USER_MODE', False)

    # 2. Patch the Auth DB and User model
    # We need to mock the *authentication* user model (nova.User)
    # and the *authentication* database (nova.db)
    class AuthUser(UserMixin):
        # A simple mock of the SQLAlchemy User model for auth
        def __init__(self, id, username, password_hash=""):
            self.id = id
            self.username = username
            self.password_hash = password_hash

        def check_password(self, password):
            return True  # Not needed for this, but good to have

        @property
        def is_active(self):
            return True

    # Store our mock auth users
    mock_auth_users = {
        1: AuthUser(1, "UserA"),
        2: AuthUser(2, "UserB"),
    }

    # Mock the SQLAlchemy 'db.session.get(User, uid)' call
    class MockAuthDbSession:
        def get(self, model, user_id):
            # model will be 'nova.User'
            return mock_auth_users.get(int(user_id))

        def remove(self):
            # This is a no-op (no operation) method to satisfy
            # Flask-SQLAlchemy's teardown, preventing the crash.
            pass
    # Patch the 'db' object in 'nova.py' to use our mock session
    # This is tricky, we patch the *SQLAlchemy* instance
    monkeypatch.setattr('nova.db.session', MockAuthDbSession())

    # Patch the 'User' model in 'nova.py' to be our mock AuthUser
    monkeypatch.setattr('nova.User', AuthUser)

    # 3. Configure the app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    # 4. Provision the *application* (app.db) users
    # These create the entries in the DbUser table via get_or_create
    user_a = get_or_create_db_user(db_session, "UserA")
    user_b = get_or_create_db_user(db_session, "UserB")

    # --- START FIX ---
    # Store the IDs *before* the next commit detaches the objects
    user_a_app_id = user_a.id
    user_b_app_id = user_b.id
    # --- END FIX ---

    # Add a default location for UserA (the logged-in user)
    loc_a = Location(
        user_id=user_a_app_id,  # Use the stored ID
        name="UserA_Home",
        lat=40, lon=-100, timezone="UTC", is_default=True
    )
    db_session.add(loc_a)
    db_session.commit()

    # 5. Create and log in the client
    with app.test_client() as client:
        # Log in as "UserA" (auth user ID is 1)
        with client.session_transaction() as sess:
            sess['_user_id'] = '1'  # Use the auth user ID
            sess['_fresh'] = True

        # Prime the session to load g.db_user correctly
        client.get('/')

        # Yield the client and the user IDs for the test
        # --- START FIX ---
        # Yield the stored IDs
        yield client, {"user_a_id": user_a_app_id, "user_b_id": user_b_app_id}
        # --- END FIX ---