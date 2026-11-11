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


# ------------------------------------------------ session_projects backfill
def _current_db(tmp_path, name="current.db"):
    """A database on today's schema that has already run its patches once."""
    engine = _engine(tmp_path, name)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username, active) VALUES (1, 'alice', 1)")
        conn.exec_driver_sql("INSERT INTO users (id, username, active) VALUES (2, 'bob', 1)")
        conn.exec_driver_sql("INSERT INTO projects (id, user_id, name, status) "
                             "VALUES ('p1', 1, 'Veil', 'In Progress')")
        conn.exec_driver_sql("INSERT INTO projects (id, user_id, name, status) "
                             "VALUES ('p2', 2, 'Rosette', 'In Progress')")
        _run_schema_patches(conn)
    return engine


def _session_row(conn, session_id, user_id, project_id, linked):
    conn.exec_driver_sql(
        "INSERT INTO journal_sessions (id, user_id, date_utc, object_name, "
        "external_id, project_id, draft, sync_rev, sync_changed_at) "
        "VALUES (?, ?, '2026-01-02', 'M42', ?, ?, 0, 1, '2026-01-01 00:00:00.000000')",
        (session_id, user_id, f"s{session_id}", project_id))
    if linked:
        conn.exec_driver_sql(
            "INSERT INTO session_projects (session_id, project_id) VALUES (?, ?)",
            (session_id, project_id))


def _stamps(conn):
    return {row[0]: (row[1], row[2]) for row in conn.exec_driver_sql(
        "SELECT id, sync_rev, sync_changed_at FROM journal_sessions ORDER BY id;")}


def _counters(conn):
    return dict(conn.exec_driver_sql(
        "SELECT user_id, rev FROM sync_counter ORDER BY user_id;").fetchall())


def test_the_session_projects_backfill_stamps_the_sessions_it_links(tmp_path, capsys):
    engine = _current_db(tmp_path)
    with engine.begin() as conn:
        _session_row(conn, 1, 1, "p1", linked=False)     # alice: link missing
        _session_row(conn, 2, 1, "p1", linked=True)      # alice: already linked
        _session_row(conn, 3, 1, None, linked=False)     # alice: standalone
        _session_row(conn, 4, 2, "p2", linked=False)     # bob: link missing
        before, counters = _stamps(conn), _counters(conn)
    capsys.readouterr()

    with engine.begin() as conn:
        _run_schema_patches(conn)

    out = capsys.readouterr().out
    assert "Backfilled session_projects from journal_sessions.project_id for 2 session(s)" in out
    with engine.connect() as conn:
        links = sorted(tuple(r) for r in conn.exec_driver_sql(
            "SELECT session_id, project_id FROM session_projects;").fetchall())
        assert links == [(1, "p1"), (2, "p1"), (4, "p2")]
        after, new_counters = _stamps(conn), _counters(conn)
    # The two sessions that gained a link carry a fresh rev and stamp, one rev
    # per user from that user's own counter...
    assert new_counters == {1: counters[1] + 1, 2: counters[2] + 1}
    assert after[1][0] == new_counters[1] and after[1][1] != before[1][1]
    assert after[4][0] == new_counters[2] and after[4][1] != before[4][1]
    # ...and the sessions it did not touch are exactly as they were.
    assert after[2] == before[2]
    assert after[3] == before[3]


def test_the_session_projects_backfill_links_only_the_users_own_existing_project(
        tmp_path, capsys):
    """A project_id that names another user's project, or no project at all,
    never becomes a session_projects row - and such a session is not stamped,
    on this startup or the next."""
    engine = _current_db(tmp_path)
    with engine.begin() as conn:
        _session_row(conn, 1, 1, "p1", linked=False)      # alice -> her own project
        _session_row(conn, 2, 1, "p2", linked=False)      # alice -> bob's project
        _session_row(conn, 3, 1, "ghost", linked=False)   # alice -> nothing
        before, counters = _stamps(conn), _counters(conn)
    capsys.readouterr()

    with engine.begin() as conn:
        _run_schema_patches(conn)

    assert "for 1 session(s)" in capsys.readouterr().out
    with engine.connect() as conn:
        links = sorted(tuple(r) for r in conn.exec_driver_sql(
            "SELECT session_id, project_id FROM session_projects;").fetchall())
        assert links == [(1, "p1")]
        after = _stamps(conn)
        assert _counters(conn) == {1: counters[1] + 1, 2: counters[2]}
    assert after[1] != before[1]
    assert after[2] == before[2] and after[3] == before[3]

    with engine.begin() as conn:
        _run_schema_patches(conn)                          # and nothing on a restart
        assert _stamps(conn) == after
    assert "Backfilled session_projects" not in capsys.readouterr().out


def test_the_session_projects_backfill_is_a_no_op_when_nothing_is_missing(tmp_path, capsys):
    engine = _current_db(tmp_path)
    with engine.begin() as conn:
        _session_row(conn, 1, 1, "p1", linked=True)
        _session_row(conn, 2, 1, None, linked=False)
    with engine.connect() as conn:
        snapshot = _snapshot_all(conn)
    capsys.readouterr()

    with engine.begin() as conn:
        _run_schema_patches(conn)

    assert "Backfilled session_projects" not in capsys.readouterr().out
    with engine.connect() as conn:
        assert _snapshot_all(conn) == snapshot        # no rev, no stamp, no row


def test_a_backfilled_link_is_exported_as_a_change(tmp_path):
    """What the stamp is for: the session shows up above the old watermark."""
    engine = _current_db(tmp_path)
    with engine.begin() as conn:
        _session_row(conn, 1, 1, "p1", linked=False)
        watermark = _counters(conn)[1]
        _run_schema_patches(conn)

    from nova.sync_engine import export_changes
    db = sessionmaker(bind=engine)()
    try:
        alice = db.get(DbUser, 1)
        payload = export_changes(db, alice, watermark, side="local", with_files=False)
    finally:
        db.close()
    sessions = payload["records"]["journal_sessions"]
    assert [(s["external_id"], s["project_ids"]) for s in sessions] == [("s1", ["p1"])]


def test_an_upgrade_in_the_same_startup_stamps_backfilled_sessions_once(tmp_path):
    """A pre-sync database: the backfill links the session and the sync_v1 fill
    stamps every row at rev 1 — no second rev on top."""
    engine = _old_db(tmp_path, "upgrade_links.db")
    _seed_old_db(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE session_projects (session_id INTEGER NOT NULL, "
            "project_id VARCHAR(64) NOT NULL, PRIMARY KEY (session_id, project_id))")
        conn.exec_driver_sql("UPDATE journal_sessions SET project_id = 'p1'")
        _run_schema_patches(conn)

    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM session_projects").scalar() >= 1
        assert {rev for rev, _ in _stamps(conn).values()} == {1}
        assert set(_counters(conn).values()) == {1}


def test_a_second_startup_prints_no_index_or_backfill_lines(tmp_path, capsys):
    """The lines that used to print on every start of every worker."""
    engine = _old_db(tmp_path, "quiet.db")
    _seed_old_db(engine)
    with engine.begin() as conn:
        # The old global index, by its name. This baseline predates the
        # external_id column, so it sits on the row id; only the name matters.
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_journal_external_id ON journal_sessions(id)")
        _run_schema_patches(conn)
    first = capsys.readouterr().out
    # The first run really did all of it, and says so.
    assert "Dropped old global unique index uq_journal_external_id" in first
    assert "Created composite unique index uq_journal_user_external_id" in first
    assert "Created performance index idx_journal_user_date" in first
    assert "Created performance index idx_journal_object_name" in first
    assert "Created performance index idx_saved_views_name" in first

    with engine.begin() as conn:
        _run_schema_patches(conn)
    second = capsys.readouterr().out

    assert "Dropped" not in second
    assert "Created" not in second
    assert "Backfilled" not in second
    with engine.connect() as conn:
        names = {r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index';").fetchall()}
    assert "uq_journal_external_id" not in names
    assert {"uq_journal_user_external_id", "idx_journal_user_date",
            "idx_journal_object_name", "idx_saved_views_name"} <= names
