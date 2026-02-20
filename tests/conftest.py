import pytest
import sys, os
from datetime import date
import types
from sqlalchemy.sql.elements import BinaryExpression, ColumnElement
# ADDING IMPORT for the literal value wrapper
from sqlalchemy.sql.expression import literal
from sqlalchemy.sql import operators
from sqlalchemy.sql.selectable import Select
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

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
    UserMixin,
    User
)


# --- MOCK COLUMN CLASSES (The definitive fix is here) ---
class MockColumn(ColumnElement):
    """Mocks a SQLAlchemy column for comparison operations."""

    def __init__(self, name):
        self.name = name
        self.type = types.SimpleNamespace(python_type=str)

    def __eq__(self, other):
        # FIX: Wrap the raw string ('other') using literal() to satisfy SQLAlchemy's internal checks.
        # This solves the AttributeError: 'str' object has no attribute '_propagate_attrs'.
        return BinaryExpression(self, literal(other), operators.eq, type_=self.type)

    def __hash__(self):
        return hash(self.name)


class MockSelectQuery(types.SimpleNamespace):
    """Mocks the select object for the login route."""

    def __init__(self, entities):
        super().__init__()
        self.entities = entities
        self.whereclause = types.SimpleNamespace(right=types.SimpleNamespace(value=None))

    def where(self, condition):
        self.whereclause = condition
        return self


# MOCK MODEL CLASS: Inherits from User and adds the missing attributes.
class MockAuthDbUser(User):
    id = MockColumn('id')
    username = MockColumn('username')
    password_hash = MockColumn('password_hash')


# --- END MOCK CLASSES ---


@pytest.fixture(scope="function")
def db_session(monkeypatch):
    # ... (content remains unchanged) ...
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    TestSessionLocal = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )

    monkeypatch.setattr('nova.SessionLocal', TestSessionLocal)
    monkeypatch.setattr('nova.helpers.SessionLocal', TestSessionLocal)
    monkeypatch.setattr('nova.get_db', TestSessionLocal)
    monkeypatch.setattr(TestSessionLocal, 'remove', lambda: None)

    session = TestSessionLocal()
    guest_user = DbUser(username="guest_user")
    session.add(guest_user)
    session.commit()

    try:
        yield session
    finally:
        TestSessionLocal.remove()
        Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session, monkeypatch):
    # ... (content remains unchanged) ...
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
        with client.session_transaction() as sess:
            sess['_user_id'] = 'default'
            sess['_fresh'] = True
        client.get('/')
        yield client


@pytest.fixture
def client_logged_out(db_session, monkeypatch):
    # ... (content remains unchanged) ...
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
                # FIX: Access the comparison value from the expression
                username = select_statement.whereclause.right.value
            except AttributeError:
                username = None

            for user in mock_auth_users.values():
                if user.username == username:
                    return user
            return None

        def remove(self):
            pass

    # --- FIX: Inject the mock 'db' object into the nova module's namespace to solve NameError/AttributeError ---
    import nova

    # Patch the User model with the mock columns required by db.select(User).where(User.username == ...)
    monkeypatch.setattr(nova, 'User', MockAuthDbUser)

    mock_db_object = types.SimpleNamespace()
    mock_db_object.session = MockAuthDbSession()
    mock_db_object.select = MockSelectQuery

    # WORKAROUND: Force the 'db' variable into the module's global dict
    nova.__dict__['db'] = mock_db_object
    # --- END FIX ---

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
                # FIX: Access the comparison value from the expression
                username = select_statement.whereclause.right.value
            except AttributeError:
                username = None

            for user in mock_auth_users.values():
                if user.username == username:
                    return user
            return None

        def remove(self):
            pass

    # --- FIX: Inject the mock 'db' object into the nova module's namespace to solve NameError/AttributeError ---
    import nova

    # Patch the User model with the mock columns required by db.select(User).where(User.username == ...)
    monkeypatch.setattr(nova, 'User', MockAuthDbUser)

    mock_db_object = types.SimpleNamespace()
    mock_db_object.session = MockAuthDbSession()
    mock_db_object.select = MockSelectQuery

    # WORKAROUND: Force the 'db' variable into the module's global dict
    nova.__dict__['db'] = mock_db_object
    # --- END FIX ---

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
    # ... (content remains unchanged) ...
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
    # ... (content remains unchanged) ...
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
        yield client