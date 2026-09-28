"""Tests for POST /api/internal/deprovision_user with action "delete"."""
import types

import pytest

import nova.auth
from sqla_mocks import MockSelectQuery

API_KEY = "test-provisioning-key"
URL = "/api/internal/deprovision_user"


class FakeAuthSession:
    """Dict-backed stand-in for auth_db.session that supports delete/commit.

    The conftest mock session only supports lookups, and delete_user() needs
    delete/commit/rollback to run its real code path.
    """

    def __init__(self, usernames):
        self.users = {name: types.SimpleNamespace(username=name) for name in usernames}
        self._pending_delete = []

    def scalar(self, select_statement):
        try:
            username = select_statement.whereclause.right.value
        except AttributeError:
            return None
        return self.users.get(username)

    def delete(self, user):
        self._pending_delete.append(user.username)

    def commit(self):
        for name in self._pending_delete:
            self.users.pop(name, None)
        self._pending_delete = []

    def rollback(self):
        self._pending_delete = []

    def remove(self):
        pass


@pytest.fixture
def auth_session(mu_client_logged_out, monkeypatch):
    """Install a fake auth DB where api.py and delete_user() both look it up."""
    session = FakeAuthSession(["UserA", "UserB"])
    fake_db = types.SimpleNamespace(session=session, select=MockSelectQuery)
    # delete_user() imports db/User from nova.auth at call time; api.py bound
    # them at import time, so both places need patching.
    monkeypatch.setattr(nova.auth, "db", fake_db)
    monkeypatch.setattr("nova.blueprints.api.auth_db", fake_db)
    monkeypatch.setattr("nova.blueprints.api.User", nova.auth.User)
    monkeypatch.setenv("PROVISIONING_API_KEY", API_KEY)
    return session


def _delete(client, username):
    return client.post(
        URL,
        json={"username": username, "action": "delete"},
        headers={"X-Api-Key": API_KEY},
    )


def test_delete_unknown_user_returns_404(mu_client_logged_out, auth_session):
    resp = _delete(mu_client_logged_out, "NoSuchUser")

    assert resp.status_code == 404
    assert resp.get_json() == {"status": "not_found"}


def test_delete_existing_user_returns_200_and_removes_user(mu_client_logged_out, auth_session):
    resp = _delete(mu_client_logged_out, "UserA")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "success", "message": "deleted"}
    assert "UserA" not in auth_session.users


def test_delete_failure_returns_500_and_keeps_user(mu_client_logged_out, auth_session, monkeypatch):
    monkeypatch.setattr("nova.blueprints.api.delete_user", lambda username: False)

    resp = _delete(mu_client_logged_out, "UserA")

    assert resp.status_code == 500
    assert resp.get_json() == {"status": "error", "message": "delete failed"}
    assert "UserA" in auth_session.users
