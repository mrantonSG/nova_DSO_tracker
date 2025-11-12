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