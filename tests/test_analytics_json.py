"""Tests for GET /analytics.json (machine-readable analytics aggregates)."""
import types
from datetime import date, datetime, timedelta

import pytest

import nova
import nova.auth
import nova.blueprints.core as core_module
from nova.models import AnalyticsEvent, AnalyticsLogin, DbUser

SECRET = "test-analytics-json-secret"


@pytest.fixture
def analytics_json_client(su_client_logged_in, monkeypatch):
    """Logged-in single-user client wired so the route reads the test DB."""
    monkeypatch.setattr(core_module, "SessionLocal", nova.SessionLocal)
    monkeypatch.setenv("ANALYTICS_SECRET", SECRET)
    return su_client_logged_in


def _fake_auth_db(users):
    class _FakeResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _FakeAuthSession:
        def __init__(self, items):
            self._items = items

        def scalars(self, stmt):
            return _FakeResult(self._items)

    return types.SimpleNamespace(session=_FakeAuthSession(users),
                                 select=lambda *a, **k: None)


# --- Secret gate -----------------------------------------------------------

def test_403_without_secret(analytics_json_client):
    assert analytics_json_client.get("/analytics.json").status_code == 403


def test_403_with_wrong_secret(analytics_json_client):
    assert analytics_json_client.get("/analytics.json?secret=nope").status_code == 403


def test_403_when_secret_unset(analytics_json_client, monkeypatch):
    monkeypatch.delenv("ANALYTICS_SECRET", raising=False)
    assert analytics_json_client.get("/analytics.json?secret=anything").status_code == 403


# --- Shape / single-user ---------------------------------------------------

def test_shape_single_user_has_null_users(analytics_json_client, db_session):
    today = date.today()
    db_session.add(AnalyticsEvent(event_name="dashboard_load", date=today, count=3))
    db_session.add(AnalyticsLogin(date=today, login_count=2))
    db_session.commit()

    resp = analytics_json_client.get(f"/analytics.json?secret={SECRET}")
    assert resp.status_code == 200
    body = resp.get_json()

    assert set(body) == {"generated_at", "app_version", "mode", "users",
                         "events_daily", "logins_daily"}
    assert body["app_version"] == nova.APP_VERSION
    assert body["mode"] == "single"
    assert body["users"] is None
    assert {"date": today.isoformat(), "event": "dashboard_load", "count": 3} in body["events_daily"]
    assert {"date": today.isoformat(), "count": 2} in body["logins_daily"]


def test_old_rows_outside_90_day_window_excluded(analytics_json_client, db_session):
    today = date.today()
    old = today - timedelta(days=200)
    db_session.add(AnalyticsEvent(event_name="dashboard_load", date=old, count=9))
    db_session.commit()

    body = analytics_json_client.get(f"/analytics.json?secret={SECRET}").get_json()
    assert all(row["date"] != old.isoformat() for row in body["events_daily"])


# --- Multi-user users block ------------------------------------------------

def test_multi_user_users_block(multi_user_client, db_session, monkeypatch):
    client, _ids = multi_user_client
    monkeypatch.setattr(core_module, "SessionLocal", nova.SessionLocal)
    monkeypatch.setenv("ANALYTICS_SECRET", SECRET)
    monkeypatch.setattr(core_module, "ADMIN_USERS", {"admin"})
    monkeypatch.setattr(nova.auth, "db", _fake_auth_db([
        types.SimpleNamespace(username="admin", active=True),
        types.SimpleNamespace(username="alice", active=True),
        types.SimpleNamespace(username="bob", active=False),
    ]))

    body = client.get(f"/analytics.json?secret={SECRET}").get_json()

    assert body["mode"] == "multi"
    assert isinstance(body["users"], dict)
    assert set(body["users"]) == {"provisioned", "used_app", "active_7d",
                                  "active_30d", "excluded"}
    assert body["users"]["provisioned"] == 1  # alice only (admin excluded, bob inactive)
    assert body["users"]["excluded"] == ["admin", "default", "guest"]
    for key in ("used_app", "active_7d", "active_30d"):
        assert isinstance(body["users"][key], int)


# --- Helper-level exact counts --------------------------------------------

def test_dbuser_activity_counts_exclusions_and_windows(db_session, monkeypatch):
    monkeypatch.setattr(core_module, "ADMIN_USERS", {"admin"})
    today = date.today()

    def _midnight(d):
        return datetime(d.year, d.month, d.day)

    def mk(name, active=True, seen=None):
        user = DbUser(username=name, active=active)
        user.last_seen = seen
        db_session.add(user)

    mk("admin")                                                   # excluded
    mk("default")                                                 # excluded
    # "guest_user" already exists (created by the db_session fixture) and is excluded.
    mk("alice", True, _midnight(today))                            # today
    mk("bob", True, _midnight(today - timedelta(days=20)))         # in 30d, not 7d
    mk("dave", True, _midnight(today - timedelta(days=40)))        # outside both
    mk("erin", True, _midnight(today - timedelta(days=6)))         # 7d boundary -> counts
    mk("frank", True, _midnight(today - timedelta(days=7)))        # just outside 7d
    mk("gina", True, _midnight(today - timedelta(days=29)))        # 30d boundary -> counts
    mk("hank", True, _midnight(today - timedelta(days=30)))        # just outside 30d
    mk("carol", False, None)                                       # inactive
    db_session.commit()

    used_app, active_7d, active_30d = core_module._dbuser_activity_counts(db_session)
    assert used_app == 7        # alice, bob, dave, erin, frank, gina, hank
    assert active_7d == 2       # alice, erin (today .. today-6)
    assert active_30d == 5      # alice, bob, erin, frank, gina (today .. today-29)


def test_provisioned_none_in_single_user(monkeypatch):
    monkeypatch.setattr(nova.auth, "db", None)
    assert core_module._provisioned_account_count() is None


def test_provisioned_excludes_admins(monkeypatch):
    monkeypatch.setattr(core_module, "ADMIN_USERS", {"admin"})
    monkeypatch.setattr(nova.auth, "db", _fake_auth_db([
        types.SimpleNamespace(username="admin", active=True),
        types.SimpleNamespace(username="alice", active=True),
        types.SimpleNamespace(username="bob", active=False),
    ]))
    assert core_module._provisioned_account_count() == 1
