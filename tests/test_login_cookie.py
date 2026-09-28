"""
Login cookie ids are "<id>:<fingerprint>" so a cookie from a deleted account
never loads a new account that SQLite gave the same id.
"""
import types

import pytest

import nova.auth
from nova.auth import load_user, session_id_for


class _FakeUser:
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

    def get_id(self):
        return session_id_for(self)


class _FakeSession:
    def __init__(self, users):
        self.users = users

    def get(self, model, user_id):
        return self.users.get(int(user_id))


@pytest.fixture
def auth_users(monkeypatch):
    """Multi-user loader backed by a dict of id -> _FakeUser."""
    users = {}
    monkeypatch.setattr(nova.auth, "SINGLE_USER_MODE", False)
    monkeypatch.setitem(nova.auth.__dict__, "db", types.SimpleNamespace(session=_FakeSession(users)))
    return users


def test_login_round_trip(mu_client_logged_out):
    client = mu_client_logged_out
    response = client.post('/login', data={'username': 'UserA', 'password': 'password123'})
    assert response.status_code == 303

    with client.session_transaction() as sess:
        assert sess['_user_id'].startswith('1:')

    # Protected route: 200 when the cookie loads, a redirect to /login otherwise
    assert client.get('/get_locations').status_code == 200


def test_deleted_user_cookie_does_not_load_reused_id(auth_users):
    auth_users[7] = _FakeUser(7, "alice", "pbkdf2:sha256$saltA$hashA")
    old_value = session_id_for(auth_users[7])
    assert load_user(old_value) is auth_users[7]

    # alice is deleted, SQLite hands id 7 to a new account
    auth_users[7] = _FakeUser(7, "bob", "pbkdf2:sha256$saltB$hashB")
    assert load_user(old_value) is None
    assert load_user(session_id_for(auth_users[7])) is auth_users[7]


@pytest.mark.parametrize("value", ["7", "", None, 7, "x:y", ":abc", "7:", "7:0000000000000000"])
def test_malformed_or_old_format_values_return_none(auth_users, value):
    auth_users[7] = _FakeUser(7, "alice", "pbkdf2:sha256$saltA$hashA")
    assert load_user(value) is None


def test_password_change_invalidates_old_value(auth_users):
    user = _FakeUser(3, "carol", "pbkdf2:sha256$salt1$hash1")
    auth_users[3] = user
    old_value = session_id_for(user)

    user.password_hash = "pbkdf2:sha256$salt2$hash2"
    assert load_user(old_value) is None
    assert load_user(session_id_for(user)) is user


def test_cookie_does_not_contain_password_hash(auth_users):
    user = _FakeUser(3, "carol", "pbkdf2:sha256$salt1$hash1")
    value = session_id_for(user)
    uid, fp = value.split(":")
    assert uid == "3" and len(fp) == 16
    assert "salt1" not in value and "hash1" not in value
