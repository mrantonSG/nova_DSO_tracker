"""Instance sync, step 1: schema additions and the one-time sync_v1 backfill.

Each test builds a real SQLite file under tmp_path, so every connection sees
the same database (an in-memory URL would give each pool connection its own).
"""

import os
import sys
from datetime import date
from unittest.mock import patch

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nova
from nova import SYNC_TABLES, _run_schema_patches
from nova.migration import export_user_data
from nova.models import (
    AstroObject, Base, Component, DbUser, JournalSession, Location, Project,
    Rig, SavedFraming, SavedView, UiPref,
)
from test_db_upgrade_simulation import MINIMAL_BASELINE_STATEMENTS

SYNC_TABLES_CREATED_WHOLE = (
    "sync_counter", "sync_tombstones", "sync_state", "sync_tokens",
)


# --------------------------------------------------------------- helpers
def _engine(tmp_path, name="app.db"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def _old_db(tmp_path, name="old.db"):
    """A pre-patch database, as built by the UID upgrade simulation."""
    engine = _engine(tmp_path, name)
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
    return engine


def _seed_old_db(engine):
    """One row per synced table, for two users."""
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'alice')")
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (2, 'bob')")
        conn.exec_driver_sql(
            "INSERT INTO locations (id, user_id, name, lat, lon, timezone) "
            "VALUES (1, 1, 'Home', 50.0, 10.0, 'UTC')"
        )
        conn.exec_driver_sql(
            "INSERT INTO astro_objects (id, user_id, object_name, ra_hours, dec_deg) "
            "VALUES (1, 1, 'M42', 5.58, -5.4)"
        )
        conn.exec_driver_sql(
            "INSERT INTO components (id, user_id, kind, name, focal_length_mm) "
            "VALUES (1, 1, 'telescope', 'Refractor', 400.0)"
        )
        conn.exec_driver_sql(
            "INSERT INTO rigs (id, user_id, rig_name, telescope_id) "
            "VALUES (1, 1, 'Rig A', 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects (id, user_id, name) VALUES ('p1', 1, 'Project')"
        )
        conn.exec_driver_sql(
            "INSERT INTO saved_views (id, user_id, name, settings_json) "
            "VALUES (1, 1, 'View', '{}')"
        )
        conn.exec_driver_sql(
            "INSERT INTO saved_framings (id, user_id, object_name) VALUES (1, 1, 'M42')"
        )
        conn.exec_driver_sql(
            "INSERT INTO journal_sessions (id, user_id, date_utc, object_name) "
            "VALUES (1, 1, '2026-01-01', 'M42')"
        )
        conn.exec_driver_sql(
            "INSERT INTO journal_sessions (id, user_id, date_utc, object_name) "
            "VALUES (2, 2, '2026-01-02', 'M31')"
        )
        conn.exec_driver_sql(
            "INSERT INTO ui_prefs (id, user_id, json_blob) VALUES (1, 1, '{}')"
        )


def _columns(conn, table):
    return [r[1] for r in conn.exec_driver_sql(
        f"PRAGMA table_info({table});").fetchall()]


def _table_exists(conn, table):
    return conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    ).fetchone() is not None


def _dump(conn, table, columns):
    cols = ", ".join(f'"{c}"' for c in columns)
    return [tuple(r) for r in conn.exec_driver_sql(
        f"SELECT {cols} FROM {table} ORDER BY rowid;").fetchall()]


def _snapshot_all(conn):
    """Column lists and every row of every table, for a no-op comparison."""
    tables = [r[0] for r in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;").fetchall()]
    snap = {}
    for table in tables:
        cols = _columns(conn, table)
        snap[table] = (tuple(cols), _dump(conn, table, cols))
    return snap


# --------------------------------------------------------------- tests
def test_fresh_install_has_sync_schema_and_marker(tmp_path):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        for table in SYNC_TABLES:
            cols = _columns(conn, table)
            assert "sync_changed_at" in cols, table
            assert "sync_rev" in cols, table
        for table in SYNC_TABLES_CREATED_WHOLE:
            assert _table_exists(conn, table), table
        assert conn.exec_driver_sql(
            "SELECT 1 FROM nova_migrations WHERE name = 'sync_v1';"
        ).fetchone(), "sync_v1 marker missing on a fresh install"
        # Fresh install has no users or rows to stamp, so nothing to backfill.


def test_upgrade_stamps_existing_rows_and_preserves_data(tmp_path):
    engine = _old_db(tmp_path)
    _seed_old_db(engine)

    with engine.connect() as conn:
        before_columns = {
            t: _columns(conn, t) for t in SYNC_TABLES if _table_exists(conn, t)
        }
        before_rows = {
            t: _dump(conn, t, cols) for t, cols in before_columns.items()
        }

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        stamps = set()
        for table in SYNC_TABLES:
            revs = conn.exec_driver_sql(
                f"SELECT DISTINCT sync_rev FROM {table};").fetchall()
            assert all(r[0] == 1 for r in revs), (table, revs)
            stamps.update(
                r[0] for r in conn.exec_driver_sql(
                    f"SELECT DISTINCT sync_changed_at FROM {table};").fetchall()
            )
        assert len(stamps) == 1, f"expected one shared upgrade time, got {stamps}"

        counters = conn.exec_driver_sql(
            "SELECT user_id, rev FROM sync_counter ORDER BY user_id;").fetchall()
        assert [tuple(r) for r in counters] == [(1, 1), (2, 1)]

        external = conn.exec_driver_sql(
            "SELECT user_id, external_id FROM journal_sessions ORDER BY id;").fetchall()
        assert all(ext for _uid, ext in external), external
        assert len(set(tuple(r) for r in external)) == len(external)

        # Only the new columns (and external_id, covered above) may change.
        for table, cols in before_columns.items():
            assert _dump(conn, table, cols) == before_rows[table], table


def test_backfill_fills_null_and_empty_external_id(tmp_path):
    engine = _old_db(tmp_path, "old_ext.db")
    # A database from the UID era already has the column; two rows still
    # need an id (one NULL, one empty string).
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "ALTER TABLE journal_sessions ADD COLUMN external_id TEXT;")
    _seed_old_db(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE journal_sessions SET external_id = '' WHERE id = 2;")

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT user_id, external_id FROM journal_sessions ORDER BY id;").fetchall()
        assert all(ext for _uid, ext in rows), rows
        assert len({(uid, ext) for uid, ext in rows}) == len(rows)


def test_second_startup_changes_nothing(tmp_path):
    engine = _old_db(tmp_path, "second.db")
    _seed_old_db(engine)
    with engine.begin() as conn:
        _run_schema_patches(conn)
    with engine.connect() as conn:
        snapshot = _snapshot_all(conn)

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        assert _snapshot_all(conn) == snapshot


def test_crash_during_backfill_leaves_no_partial_state(tmp_path):
    engine = _old_db(tmp_path, "crash.db")
    _seed_old_db(engine)

    def boom():
        raise RuntimeError("simulated crash mid-backfill")

    with patch.object(nova, "generate_session_id", boom):
        with pytest.raises(RuntimeError):
            with engine.begin() as conn:
                _run_schema_patches(conn)

    with engine.connect() as conn:
        # SQLite rolls the DDL back too, so the marker table itself may be
        # gone; either way there must be no sync_v1 marker.
        if _table_exists(conn, "nova_migrations"):
            assert conn.exec_driver_sql(
                "SELECT 1 FROM nova_migrations WHERE name = 'sync_v1';"
            ).fetchone() is None, "marker written despite the crash"
        for table in SYNC_TABLES:
            cols = _columns(conn, table)
            if "sync_rev" in cols:
                stamped = conn.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {table} WHERE sync_rev IS NOT NULL;"
                ).fetchone()[0]
                assert stamped == 0, (table, stamped)
        if _table_exists(conn, "sync_counter"):
            assert conn.exec_driver_sql(
                "SELECT COUNT(*) FROM sync_counter;").fetchone()[0] == 0

    # The next startup finishes the job.
    with engine.begin() as conn:
        _run_schema_patches(conn)
    with engine.connect() as conn:
        assert conn.exec_driver_sql(
            "SELECT 1 FROM nova_migrations WHERE name = 'sync_v1';"
        ).fetchone()
        remaining = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM journal_sessions "
            "WHERE external_id IS NULL OR external_id = '';").fetchone()[0]
        assert remaining == 0


# --------------------------------------------------- YAML export stability
def _seed_fresh_db(engine):
    """One user with records in every exported table; UID links pre-filled so
    the uid_links backfill has nothing to fill in."""
    session = sessionmaker(bind=engine)()
    user = DbUser(username="alice")
    session.add(user)
    session.flush()
    uid = user.id

    location = Location(user_id=uid, name="Home", lat=50.0, lon=10.0,
                        timezone="UTC", record_uid="locuid")
    obj = AstroObject(user_id=uid, object_name="M42", ra_hours=5.58,
                      dec_deg=-5.4, record_uid="objuid")
    telescope = Component(user_id=uid, kind="telescope", name="Refractor",
                          focal_length_mm=400.0, record_uid="teluid")
    session.add_all([location, obj, telescope])
    session.flush()

    rig = Rig(user_id=uid, rig_name="Rig A", telescope_id=telescope.id,
              telescope_record_uid="teluid", record_uid="riguid")
    project = Project(id="p1", user_id=uid, name="Project",
                      target_object_record_uid="objuid")
    journal = JournalSession(
        user_id=uid, date_utc=date(2026, 1, 1), object_name="M42",
        location_record_uid="locuid", object_record_uid="objuid",
        rig_record_uid="riguid", external_id="external-1",
    )
    view = SavedView(user_id=uid, name="View", settings_json="{}")
    framing = SavedFraming(user_id=uid, object_name="M42", rig_name="Rig A",
                           rig_record_uid="riguid", object_record_uid="objuid")
    prefs = UiPref(user_id=uid, json_blob='{"theme": "dark"}')
    session.add_all([rig, project, journal, view, framing, prefs])
    session.commit()
    session.close()
    return uid


def _export_docs(engine, user_id):
    session = sessionmaker(bind=engine)()
    user = session.get(DbUser, user_id)
    docs = export_user_data(session, user)
    session.close()
    # Same serialisation the download routes use.
    return tuple(
        yaml.dump(doc, sort_keys=False, allow_unicode=True, indent=2,
                  default_flow_style=False)
        for doc in docs
    )


def test_yaml_export_byte_identical_across_backfill(tmp_path):
    # A database with the new columns (fresh schema) is the only one the ORM
    # can export, so "before" is the export immediately after create_all and
    # "after" is the export after the sync_v1 backfill has stamped every row.
    engine = _engine(tmp_path, "export.db")
    Base.metadata.create_all(engine)
    user_id = _seed_fresh_db(engine)

    before = _export_docs(engine, user_id)
    with engine.begin() as conn:
        _run_schema_patches(conn)
    after = _export_docs(engine, user_id)

    assert after == before
