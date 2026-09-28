"""Tests for POST /api/internal/provision_user."""
import types

import pytest

import nova.auth
from werkzeug.security import check_password_hash, generate_password_hash

from sqla_mocks import MockSelectQuery


class ProvisionUser:
    """In-memory account with real werkzeug hash semantics.

    Instances live in the fake session, not the ORM, so the patched
    ``nova.auth.User`` (the login-time model) never needs set_password.
    """

    def __init__(self, username, password):
        self.username = username
        self.active = True
        self.password_hash = generate_password_hash(password)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

API_KEY = "test-provisioning-key"
URL = "/api/internal/provision_user"


class FakeAuthSession:
    """Dict-backed stand-in for auth_db.session; mirrors the deprovision fake."""

    def __init__(self, users):
        self.users = dict(users)
        self.committed = False

    def scalar(self, select_statement):
        try:
            username = select_statement.whereclause.right.value
        except AttributeError:
            return None
        return self.users.get(username)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def remove(self):
        pass


@pytest.fixture
def auth_session(mu_client_logged_out, monkeypatch):
    """Install a fake auth DB with one existing user, 'UserA'."""
    user = ProvisionUser("UserA", "password123")
    session = FakeAuthSession({"UserA": user})
    fake_db = types.SimpleNamespace(session=session, select=MockSelectQuery)
    # delete-style lookups in api.py and the login path both read nova.auth.db.
    monkeypatch.setattr(nova.auth, "db", fake_db)
    monkeypatch.setattr("nova.blueprints.api.auth_db", fake_db)
    # mu fixture has already pointed nova.auth.User at the mock-column model,
    # so the select in the route works in single- or multi-user import modes.
    monkeypatch.setattr("nova.blueprints.api.User", nova.auth.User)
    monkeypatch.setenv("PROVISIONING_API_KEY", API_KEY)
    return session


def _provision(client, username, password):
    return client.post(
        URL,
        json={"username": username, "password": password},
        headers={"X-Api-Key": API_KEY},
    )


def test_provision_existing_same_password_keeps_hash(mu_client_logged_out, auth_session):
    user = auth_session.users["UserA"]
    original_hash = user.password_hash

    resp = _provision(mu_client_logged_out, "UserA", "password123")

    assert resp.status_code == 200
    assert user.password_hash == original_hash


def test_provision_existing_different_password_rehashes(mu_client_logged_out, auth_session):
    user = auth_session.users["UserA"]
    original_hash = user.password_hash

    resp = _provision(mu_client_logged_out, "UserA", "hunter2new")

    assert resp.status_code == 200
    assert user.password_hash != original_hash
    assert user.check_password("hunter2new")
