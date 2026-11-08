"""users.last_seen: the startup patch and the multi-user activity write.

The column exists on the hosted instance but not in older app.db files, so the
patch must add it without touching existing values. The write must happen at
most once per user per day, must be date-only, must never touch single-user
installs, and must never make a request fail.
"""

import os
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nova
from nova import _run_schema_patches
from nova.models import DbUser
from test_db_upgrade_simulation import MINIMAL_BASELINE_STATEMENTS


def _baseline_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
    return engine


def _users_columns(conn):
    return [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users);").fetchall()]


def _count_last_seen_updates(engine):
    """Append every UPDATE users SET last_seen statement the engine executes."""
    statements = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE USERS SET LAST_SEEN"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    return statements


@pytest.fixture(autouse=True)
def _fresh_last_seen_cache(monkeypatch):
    monkeypatch.setattr(nova, "_last_seen_day", {})


# --- Patch -------------------------------------------------------------------

def test_patch_adds_last_seen_to_a_legacy_users_table():
    engine = _baseline_engine()
    with engine.begin() as conn:
        assert "last_seen" not in _users_columns(conn)
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'legacy');")

    with engine.begin() as conn:
        _run_schema_patches(conn)
    with engine.connect() as conn:
        assert _users_columns(conn).count("last_seen") == 1
        assert conn.exec_driver_sql("SELECT last_seen FROM users WHERE id = 1;").scalar() is None

    # Idempotent: a second startup run is a no-op.
    with engine.begin() as conn:
        _run_schema_patches(conn)
    with engine.connect() as conn:
        assert _users_columns(conn).count("last_seen") == 1


def test_patch_keeps_an_existing_last_seen_value():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN last_seen DATETIME;")
        conn.exec_driver_sql(
            "INSERT INTO users (id, username, last_seen) "
            "VALUES (1, 'existing', '2025-11-06 02:24:04.501007');")

    with engine.begin() as conn:
        _run_schema_patches(conn)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT last_seen FROM users;").scalar() == \
            "2025-11-06 02:24:04.501007"


# --- Write path --------------------------------------------------------------

def _make_user(db_session, name="active_user"):
    user = DbUser(username=name)
    db_session.add(user)
    db_session.commit()
    return user


def test_write_happens_once_per_user_per_day(db_session, monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", False)
    user = _make_user(db_session)
    updates = _count_last_seen_updates(db_session.get_bind())

    nova._touch_user_last_seen(user)
    nova._touch_user_last_seen(user)
    nova._touch_user_last_seen(user)

    assert len(updates) == 1
    db_session.refresh(user)
    assert user.last_seen.date() == datetime.now(timezone.utc).date()


def test_cold_cache_still_writes_at_most_once_when_the_row_is_already_today(db_session, monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", False)
    user = _make_user(db_session)
    now = datetime.now(timezone.utc)
    user.last_seen = datetime(now.year, now.month, now.day)
    db_session.commit()
    updates = _count_last_seen_updates(db_session.get_bind())

    # Another worker wrote today's date; this process's cache is empty.
    nova._touch_user_last_seen(user)

    assert updates == []
    db_session.refresh(user)
    assert user.last_seen.time() == datetime.min.time()


def test_stored_value_has_no_time_of_day(db_session, monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", False)
    user = _make_user(db_session)

    nova._touch_user_last_seen(user)

    db_session.refresh(user)
    today = datetime.now(timezone.utc).date()
    assert user.last_seen == datetime(today.year, today.month, today.day)
    assert user.last_seen.time() == datetime.min.time()
    assert user.last_seen.tzinfo is None


def test_nothing_is_written_in_single_user_mode(db_session, monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", True)
    user = _make_user(db_session)
    updates = _count_last_seen_updates(db_session.get_bind())

    nova._touch_user_last_seen(user)

    assert updates == []
    db_session.refresh(user)
    assert user.last_seen is None


def test_a_db_error_in_the_write_path_is_swallowed(db_session, monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", False)
    user = _make_user(db_session)

    class _BrokenSessionFactory:
        def session_factory(self):
            raise RuntimeError("database is down")

        def remove(self):
            pass

    monkeypatch.setattr("nova.SessionLocal", _BrokenSessionFactory())

    nova._touch_user_last_seen(user)  # must not raise

    db_session.refresh(user)
    assert user.last_seen is None


def test_a_db_error_does_not_change_the_response(multi_user_client, monkeypatch):
    client, _ids = multi_user_client

    class _BrokenSessionFactory:
        def session_factory(self):
            raise RuntimeError("database is down")

        def remove(self):
            pass

    monkeypatch.setattr("nova.SessionLocal", _BrokenSessionFactory())

    assert client.get("/").status_code == 200