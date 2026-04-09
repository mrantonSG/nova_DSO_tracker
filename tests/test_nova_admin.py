"""
Tests for admin blueprint guard logic and user management routes.
Multi-user mode only — all tests are skipped if SINGLE_USER_MODE is True.
"""
import pytest
import types
from unittest.mock import patch
from tests.conftest import *


class AdminMockUser(UserMixin):
    """Mock user with MockColumn class attrs for queries and __init__ for instantiation."""
    id = MockColumn('id')
    username = MockColumn('username')
    password_hash = MockColumn('password_hash')

    def __init__(self, id=None, username="", active=True):
        self.id = id
        self.username = username
        self.password_hash = ""
        self.active = active

    def set_password(self, password):
        self.password_hash = f"pbkdf2:sha256$mock${password}"

    def check_password(self, password):
        return True

    @property
    def is_active(self):
        return self.active


class _MockAdminSelect(MockSelectQuery):
    """Extends MockSelectQuery with order_by support."""
    def order_by(self, *args):
        return self


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _MockAdminSession:
    def __init__(self, users, next_id):
        self._users = users
        self._next_id = next_id

    def get(self, model, user_id):
        return self._users.get(int(user_id))

    def scalar(self, stmt):
        try:
            username = stmt.whereclause.right.value
        except AttributeError:
            return None
        for u in self._users.values():
            if u.username == username:
                return u
        return None

    def scalars(self, stmt):
        return _ScalarResult(list(self._users.values()))

    def add(self, user):
        if user.id is None:
            user.id = self._next_id[0]
            self._next_id[0] += 1
        self._users[user.id] = user

    def delete(self, user):
        self._users.pop(user.id, None)

    def commit(self):
        pass

    def remove(self):
        pass


@pytest.fixture
def _mu_admin_env(db_session, monkeypatch):
    """Configure multi-user mock auth with admin + testuser."""
    monkeypatch.setattr('nova.SINGLE_USER_MODE', False)
    monkeypatch.setattr('nova.auth.SINGLE_USER_MODE', False)
    monkeypatch.setattr('nova.blueprints.admin.SINGLE_USER_MODE', False)

    import nova
    import nova.auth

    # Disable CSRF protection for POST tests
    monkeypatch.setattr(nova, 'csrf', types.SimpleNamespace(protect=lambda: None))

    users = {
        1: AdminMockUser(id=1, username="admin"),
        2: AdminMockUser(id=2, username="testuser"),
    }
    next_id = [3]
    session = _MockAdminSession(users, next_id)

    monkeypatch.setattr(nova, 'User', AdminMockUser)
    monkeypatch.setattr(nova.auth, 'User', AdminMockUser)

    mock_db = types.SimpleNamespace()
    mock_db.session = session
    mock_db.select = _MockAdminSelect

    nova.__dict__['db'] = mock_db
    nova.auth.__dict__['db'] = mock_db

    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    app.config['WTF_CSRF_ENABLED'] = False

    get_or_create_db_user(db_session, "admin")
    get_or_create_db_user(db_session, "testuser")
    db_session.commit()


@pytest.fixture
def admin_client(_mu_admin_env):
    """Authenticated client logged in as admin (user_id=1)."""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True
        yield client


@pytest.fixture
def nonadmin_client(_mu_admin_env):
    """Authenticated client logged in as testuser (user_id=2)."""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = '2'
            sess['_fresh'] = True
        yield client


class TestAdminGuard:
    def test_single_user_mode_redirects_to_index(self, client):
        """In single-user mode, /admin/users redirects to index."""
        with patch("nova.blueprints.admin.SINGLE_USER_MODE", True):
            resp = client.get("/admin/users")
            assert resp.status_code == 302
            assert "/" in resp.headers["Location"]

    def test_unauthenticated_redirects_to_login(self, mu_client_logged_out):
        """Unauthenticated request to /admin/users redirects to login."""
        with patch("nova.blueprints.admin.SINGLE_USER_MODE", False):
            resp = mu_client_logged_out.get("/admin/users")
            assert resp.status_code == 302
            assert "login" in resp.headers["Location"].lower()

    def test_non_admin_user_redirected(self, nonadmin_client):
        """Non-admin authenticated user is redirected from /admin/users."""
        resp = nonadmin_client.get("/admin/users")
        assert resp.status_code == 302

    def test_admin_can_access_users_page(self, admin_client):
        """Admin user can access /admin/users."""
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200


class TestAdminCreateUser:
    def test_create_user_missing_fields(self, admin_client):
        """POST with missing username/password flashes error and redirects."""
        resp = admin_client.post("/admin/users/create", data={"username": "", "password": ""})
        assert resp.status_code == 302

    def test_create_duplicate_user(self, admin_client):
        """POST with existing username flashes error."""
        resp = admin_client.post("/admin/users/create", data={"username": "admin", "password": "x"})
        assert resp.status_code == 302

    def test_create_user_success(self, admin_client):
        """POST with valid unique credentials creates user."""
        resp = admin_client.post("/admin/users/create", data={"username": "newuser99", "password": "pass123"})
        assert resp.status_code == 302
        import nova
        u = nova.db.session.scalar(
            nova.db.select(nova.User).where(nova.User.username == "newuser99")
        )
        assert u is not None


class TestAdminDeleteUser:
    def test_cannot_delete_admin(self, admin_client):
        """Attempt to delete admin account is rejected."""
        resp = admin_client.post("/admin/users/1/delete")
        assert resp.status_code == 302

    def test_delete_nonexistent_user(self, admin_client):
        """Delete request for nonexistent user_id redirects with error."""
        resp = admin_client.post("/admin/users/999999/delete")
        assert resp.status_code == 302


class TestAdminToggleUser:
    def test_cannot_deactivate_admin(self, admin_client):
        """Toggle on admin account is rejected."""
        resp = admin_client.post("/admin/users/1/toggle")
        assert resp.status_code == 302
