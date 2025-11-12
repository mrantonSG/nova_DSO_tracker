import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date
import os

# --- Imports from your 'nova.py' file ---
# We need to import all the database models and the Base class
try:
    from nova import (
        Base,
        DbUser,
        Location,
        AstroObject,
        Project,
        JournalSession,
        get_db,  # We will test functions that use this
        get_or_create_db_user  # We will test this function directly
    )
except ImportError:
    import sys

    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from nova import (
        Base,
        DbUser,
        Location,
        AstroObject,
        Project,
        JournalSession,
        get_db,
        get_or_create_db_user
    )



@pytest.fixture(scope="function")
def db_session(monkeypatch):
    """
    This is the core fixture for all database tests.
    It creates a new, empty, in-memory database for *every single test*.
    It also "monkeypatches" (overrides) the app's `get_db` function
    to ensure all app code uses this test database.
    """
    # 1. Create a new in-memory-only database engine
    engine = create_engine("sqlite:///:memory:")

    # 2. Create all our tables (User, Location, etc.) in this new database
    Base.metadata.create_all(engine)

    # 3. Create a session-maker bound to this new engine
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSessionLocal()

    # --- NEW: Add the guest_user template to the test database ---
    # This pre-populates the DB with the template user,
    # so the seeder function can find it.
    guest_user = DbUser(username="guest_user")
    session.add(guest_user)
    session.commit()

    # --- END NEW ---

    # 4. === Monkeypatching ===
    # This is the critical part. We replace the app's REAL `get_db` function
    # with a fake one that just returns our test session.
    def override_get_db():
        return session

    # This command does the swap.
    monkeypatch.setattr('nova.get_db', override_get_db)

    try:
        # 5. "yield" the session to the test function. The test runs now.
        yield session
    finally:
        # 6. After the test is done, clean up.
        session.close()
        Base.metadata.drop_all(engine)  # Wipe the database


# --- 2. Your Test Functions ---

# The `db_session` argument tells pytest to run the fixture first
# and pass the in-memory database session to this test.
def test_get_or_create_db_user_new(db_session):
    """
    Tests that a new user is correctly created in the empty database.
    """
    # 1. Act
    # We call the real function from nova.py, but it's now using our test DB
    user = get_or_create_db_user(db_session, "new_user")

    # 2. Assert
    assert user is not None
    assert user.username == "new_user"
    assert user.id is not None  # Make sure it got a database ID

    # We can also query the database directly to double-check
    user_from_db = db_session.query(DbUser).filter_by(username="new_user").one()
    assert user_from_db == user


def test_get_or_create_db_user_existing(db_session):
    """
    Tests that an existing user is correctly retrieved.
    """
    # 1. Arrange
    # Manually add a user to our test database
    existing_user = DbUser(username="existing_user")
    db_session.add(existing_user)
    db_session.commit()

    # 2. Act
    # Call the function with the same username
    retrieved_user = get_or_create_db_user(db_session, "existing_user")

    # 3. Assert
    assert retrieved_user is not None
    assert retrieved_user.id == existing_user.id
    assert retrieved_user.username == "existing_user"

    # Make sure it didn't create a new user
    count = db_session.query(DbUser).count()
    assert count == 2 # We expect 2: (guest_user from fixture) + (existing_user from this test)


def test_database_relationships(db_session):
    """
    Tests that the relationships between User, Project, and Session work.
    """
    # 1. Arrange
    # Create and save a user
    user = DbUser(username="test_user")
    db_session.add(user)
    db_session.commit()  # Commit to get the user.id

    # Create and save a project linked to the user
    project = Project(id="proj1", user_id=user.id, name="Test Project")
    db_session.add(project)
    db_session.commit()  # Commit to get the project.id

    # Create and save a journal session linked to both
    session = JournalSession(
        user_id=user.id,
        project_id=project.id,
        date_utc=date(2025, 11, 12),
        object_name="M42"
    )
    db_session.add(session)
    db_session.commit()

    # 2. Act
    # Retrieve the session and test its relationships
    fetched_session = db_session.query(JournalSession).filter_by(object_name="M42").one()

    # 3. Assert
    assert fetched_session is not None
    assert fetched_session.object_name == "M42"

    # Test the back-references (the "magic" of SQLAlchemy)
    assert fetched_session.user.username == "test_user"
    assert fetched_session.project.name == "Test Project"

    # Test the other way
    assert user.projects[0].name == "Test Project"
    assert user.sessions[0].object_name == "M42"
    assert project.sessions[0].object_name == "M42"