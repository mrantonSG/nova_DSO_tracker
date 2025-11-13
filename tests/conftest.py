import pytest
import sys, os
from datetime import date

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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


@pytest.fixture(scope="function")
def db_session(monkeypatch):
    """
    Creates a new, empty, in-memory database AND patches the app
    to use this database for all its operations.

    This version (v6) correctly patches the REAL SessionLocal.remove()
    to prevent app-side db.close() calls from detaching the
    session from the test thread.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # 1. Create a Test factory that mirrors the app's factory
    TestSessionLocal = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )

    # 2. Patch the app's REAL 'SessionLocal' to use our test factory
    monkeypatch.setattr('nova.SessionLocal', TestSessionLocal)

    # 3. Patch the app's 'get_db' to use our test factory
    # (This ensures all calls to get_db() get the test session)
    monkeypatch.setattr('nova.get_db', TestSessionLocal)

    # 4. Patch the *remove* method to do nothing.
    #    This is the key: db.close() calls SessionLocal.remove().
    #    We make this do nothing so the session stays open for the test.
    monkeypatch.setattr(TestSessionLocal, 'remove', lambda: None)

    # 5. Get the single session instance we will use for this test
    session = TestSessionLocal()

    # 6. Add the guest user template
    guest_user = DbUser(username="guest_user")
    session.add(guest_user)
    session.commit()

    try:
        yield session  # The test runs here
    finally:
        # 7. The test is over, now we clean up

        # --- THIS IS THE CORRECTED CLEANUP ---
        # We call the *real* remove method via the factory itself.
        # This properly closes the session we've been using.
        TestSessionLocal.remove()
        # --- END OF FIX ---

        Base.metadata.drop_all(engine)


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
    (This version is corrected to not patch nova.User AND fixes scalar)
    """
    # 1. Force Multi-User Mode
    monkeypatch.setattr('nova.SINGLE_USER_MODE', False)

    # 2. Patch the Auth DB and User model
    class MockAuthUser(UserMixin):
        def __init__(self, id, username, password_hash=""):
            self.id = id
            self.username = username
            self.password_hash = password_hash

        def check_password(self, password): return True

        @property
        def is_active(self): return True

    mock_auth_users = {
        1: MockAuthUser(1, "UserA"),
        2: MockAuthUser(2, "UserB"),
    }

    class MockAuthDbSession:
        def get(self, model, user_id):
            return mock_auth_users.get(int(user_id))

        def scalar(self, select_statement):
            try:
                # --- THIS IS THE FIX ---
                # Access the .value of the parameter on the right
                username = select_statement.whereclause.right.value
                # --- END OF FIX ---

                for user in mock_auth_users.values():
                    if user.username == username:
                        return user
            except Exception as e:
                print(f"[MOCK_AUTH_ERROR] scalar failed: {e}")
            return None

        def remove(self):
            pass

    monkeypatch.setattr('nova.db.session', MockAuthDbSession())

    # 3. Configure the app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    # 4. Provision the *application* (app.db) users
    user_a = get_or_create_db_user(db_session, "UserA")
    user_b = get_or_create_db_user(db_session, "UserB")
    user_a_app_id = user_a.id
    user_b_app_id = user_b.id
    loc_a = Location(
        user_id=user_a_app_id,
        name="UserA_Home",
        lat=40, lon=-100, timezone="UTC", is_default=True
    )
    db_session.add(loc_a)
    db_session.commit()

    # 5. Create and log in the client
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True
        client.get('/')
        yield client, {"user_a_id": user_a_app_id, "user_b_id": user_b_app_id}


@pytest.fixture
def mu_client_logged_out(db_session, monkeypatch):
    """
    Creates a Flask test client for a MULTI-USER environment
    that is NOT logged in.
    (This version is corrected to not patch nova.User AND fixes scalar)
    """
    # 1. Force Multi-User Mode
    monkeypatch.setattr('nova.SINGLE_USER_MODE', False)

    # 2. Patch the Auth DB and User model
    class MockAuthUser(UserMixin):
        def __init__(self, id, username, password="password"):
            self.id = id
            self.username = username
            self.mock_password = password
            self.password_hash = f"hash_for_{password}"

        def check_password(self, password):
            return self.mock_password == password

        @property
        def is_active(self): return True

    mock_auth_users = {
        1: MockAuthUser(1, "UserA", password="password123"),
        2: MockAuthUser(2, "UserB", password="password456"),
    }

    class MockAuthDbSession:
        def get(self, model, user_id):
            return mock_auth_users.get(int(user_id))

        def scalar(self, select_statement):
            try:
                # --- THIS IS THE FIX ---
                # Access the .value of the parameter on the right
                username = select_statement.whereclause.right.value
                # --- END OF FIX ---

                for user in mock_auth_users.values():
                    if user.username == username:
                        return user
            except Exception as e:
                print(f"[MOCK_AUTH_ERROR] scalar failed: {e}")
            return None

        def remove(self):
            pass

    monkeypatch.setattr('nova.db.session', MockAuthDbSession())

    # 3. Configure the app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    # 4. Provision the *application* (app.db) users
    get_or_create_db_user(db_session, "UserA")
    get_or_create_db_user(db_session, "UserB")
    db_session.commit()

    # 5. Create the client *without* a session cookie
    with app.test_client() as client:
        yield client

@pytest.fixture
def client(db_session, monkeypatch):
    """
    Creates a Flask test client in SINGLE_USER_MODE that is
    NOT manually logged in. It relies on the @before_request hook
    to auto-login the 'default' user.
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

    # Add M42 so the plotting APIs have something to find
    m42 = AstroObject(
        user_id=user.id,
        object_name="M42",
        common_name="Orion Nebula",
        ra_hours=5.58,
        dec_deg=-5.4
    )
    db_session.add(m42)

    db_session.commit()

    with app.test_client() as client:
        client.get('/')
        yield client

@pytest.fixture
def su_client_not_logged_in(db_session, monkeypatch):
    """
    Creates a Flask test client in SINGLE_USER_MODE that is
    *not* manually logged in, to test the @before_request hook.
    """
    monkeypatch.setattr('nova.SINGLE_USER_MODE', True)

    class SingleUserTest(UserMixin):
        def __init__(self, user_id, username):
            self.id = user_id
            self.username = username

    monkeypatch.setattr('nova.User', SingleUserTest)

    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    # Create the 'default' user and location (needed for the app to run)
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