"""
record_uid link columns (rigs -> components, sessions -> rig/object/location,
framings -> rig/object, projects -> object), filled once by _run_schema_patches
behind the uid_links_v1 marker.

Rules under test: a valid same-user row number is carried over exactly, a name
is carried over by exact name within the same user, a broken or foreign link
stays NULL, the rig name is never used, and no existing column changes.
"""

import os
import sys
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import _run_schema_patches
from nova.models import (
    Base, DbUser, Location, Component, Rig, AstroObject, JournalSession, SavedFraming, Project,
)

from test_db_upgrade_simulation import MINIMAL_BASELINE_STATEMENTS


# (table, new column, old column, target kind)
ID_LINKS = [
    ("rigs", "telescope_record_uid", "telescope_id", "component"),
    ("rigs", "camera_record_uid", "camera_id", "component"),
    ("rigs", "reducer_extender_record_uid", "reducer_extender_id", "component"),
    ("rigs", "guide_telescope_record_uid", "guide_telescope_id", "component"),
    ("rigs", "guide_camera_record_uid", "guide_camera_id", "component"),
    ("journal_sessions", "rig_record_uid", "rig_id_snapshot", "rig"),
    ("saved_framings", "rig_record_uid", "rig_id", "rig"),
]
NAME_LINKS = [
    ("journal_sessions", "object_record_uid", "object_name", "object"),
    ("journal_sessions", "location_record_uid", "location_name", "location"),
    ("saved_framings", "object_record_uid", "object_name", "object"),
    ("projects", "target_object_record_uid", "target_object_name", "object"),
]
ALL_LINKS = ID_LINKS + NAME_LINKS
NEW_INDEXES = {
    "journal_sessions": "ix_journal_sessions_user_object_record_uid",
    "saved_framings": "ix_saved_framings_user_object_record_uid",
}


def _link_id(link):
    return f"{link[0]}.{link[1]}"


# --- helpers -----------------------------------------------------------------

def _fresh_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _baseline_engine():
    """Old-style database: no new columns, no unique (user_id, name) constraints."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
        # Old link and stable_uid columns that later patches added, so rows can carry them.
        for table, col, typ in (
            ("rigs", "guide_telescope_id", "INTEGER"),
            ("rigs", "guide_camera_id", "INTEGER"),
            ("rigs", "stable_uid", "VARCHAR(36)"),
            ("components", "stable_uid", "VARCHAR(36)"),
            ("locations", "stable_uid", "VARCHAR(36)"),
            ("journal_sessions", "rig_id_snapshot", "INTEGER"),
            ("journal_sessions", "rig_name_snapshot", "VARCHAR(256)"),
            ("journal_sessions", "rig_stable_uid_snapshot", "VARCHAR(36)"),
            ("saved_framings", "rig_name", "VARCHAR(256)"),
            ("saved_framings", "rig_stable_uid", "VARCHAR(36)"),
            ("projects", "target_object_name", "VARCHAR(256)"),
        ):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {typ};")
    return engine


def _patch(engine):
    with engine.begin() as conn:
        _run_schema_patches(conn)


def _value(engine, table, col, row_id):
    with engine.connect() as conn:
        return conn.exec_driver_sql(f"SELECT {col} FROM {table} WHERE id = ?;", (row_id,)).scalar()


def _columns(conn, table):
    return [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table});").fetchall()]


def _indexes_on(conn, table, cols):
    found = []
    for row in conn.exec_driver_sql(f"PRAGMA index_list({table});").fetchall():
        name = row[1]
        idx_cols = [r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info({name});").fetchall()]
        if idx_cols == cols:
            found.append(name)
    return found


def _markers(engine):
    with engine.connect() as conn:
        return [r[0] for r in conn.exec_driver_sql("SELECT name FROM nova_migrations;").fetchall()]


class World:
    """Two users, each with a telescope, camera, rig, object and location."""

    def __init__(self, engine):
        self.engine = engine
        self.db = Session(engine, expire_on_commit=False)
        self.a = DbUser(username="link_a")
        self.b = DbUser(username="link_b")
        self.db.add_all([self.a, self.b])
        self.db.flush()
        self.own = self._targets(self.a, "A", "M 31", "Home")
        self.other = self._targets(self.b, "B", "M 42", "Away")
        self.db.commit()
        self._n = 0

    def _targets(self, user, tag, obj_name, loc_name):
        comp = Component(user_id=user.id, kind="telescope", name=f"Scope {tag}")
        cam = Component(user_id=user.id, kind="camera", name=f"Cam {tag}")
        obj = AstroObject(user_id=user.id, object_name=obj_name, ra_hours=1.0, dec_deg=2.0)
        loc = Location(user_id=user.id, name=loc_name, lat=1.0, lon=2.0, timezone="UTC")
        self.db.add_all([comp, cam, obj, loc])
        self.db.flush()
        rig = Rig(user_id=user.id, rig_name=f"Rig {tag}", telescope_id=comp.id, camera_id=cam.id)
        self.db.add(rig)
        self.db.flush()
        return {"component": comp, "rig": rig, "object": obj, "location": loc}

    def id_of(self, kind, owner="own"):
        return getattr(self, owner)[kind].id

    def name_of(self, kind, owner="own"):
        t = getattr(self, owner)[kind]
        return t.object_name if kind == "object" else t.name

    def uid_of(self, kind, owner="own"):
        return getattr(self, owner)[kind].record_uid

    def source(self, table, **cols):
        """Add a user-A row of `table` with `cols` set; return its id."""
        self._n += 1
        n = self._n
        if table == "rigs":
            row = Rig(user_id=self.a.id, rig_name=f"Src {n}", **cols)
        elif table == "journal_sessions":
            row = JournalSession(user_id=self.a.id, date_utc=date(2026, 1, 1), **cols)
        elif table == "saved_framings":
            cols.setdefault("object_name", f"FRAME {n}")
            row = SavedFraming(user_id=self.a.id, **cols)
        elif table == "projects":
            row = Project(id=uuid.uuid4().hex, user_id=self.a.id, name=f"P {n}", **cols)
        else:
            raise ValueError(table)
        self.db.add(row)
        self.db.commit()
        return row.id


def _world():
    return World(_fresh_engine())


# --- 1. Columns and indexes --------------------------------------------------

def _assert_schema(engine):
    with engine.connect() as conn:
        for table, new_col, _old, _kind in ALL_LINKS:
            assert new_col in _columns(conn, table), f"{table}.{new_col}"
        for table, name in NEW_INDEXES.items():
            assert _indexes_on(conn, table, ["user_id", "object_record_uid"]) == [name]
    assert _markers(engine) == ["uid_links_v1"]


def test_upgrade_adds_columns_indexes_and_marker():
    engine = _baseline_engine()
    _patch(engine)
    _assert_schema(engine)
    _patch(engine)  # second start
    _assert_schema(engine)


def test_fresh_create_all_then_patch_creates_each_index_once():
    engine = _fresh_engine()
    _patch(engine)
    _assert_schema(engine)


# --- 2. Valid same-user link is carried over exactly -------------------------

@pytest.mark.parametrize("link", ID_LINKS, ids=_link_id)
def test_valid_row_number_link_is_carried_over(link):
    table, new_col, old_col, kind = link
    w = _world()
    row_id = w.source(table, **{old_col: w.id_of(kind)})
    _patch(w.engine)
    assert _value(w.engine, table, new_col, row_id) == w.uid_of(kind)


@pytest.mark.parametrize("link", NAME_LINKS, ids=_link_id)
def test_valid_name_link_is_carried_over(link):
    table, new_col, old_col, kind = link
    w = _world()
    row_id = w.source(table, **{old_col: w.name_of(kind)})
    _patch(w.engine)
    assert _value(w.engine, table, new_col, row_id) == w.uid_of(kind)


# --- 3. Link to another user's row stays NULL --------------------------------

@pytest.mark.parametrize("link", ALL_LINKS, ids=_link_id)
def test_link_to_other_users_row_stays_null(link):
    table, new_col, old_col, kind = link
    w = _world()
    value = w.id_of(kind, "other") if link in ID_LINKS else w.name_of(kind, "other")
    row_id = w.source(table, **{old_col: value})
    _patch(w.engine)
    assert _value(w.engine, table, new_col, row_id) is None


# --- 4. Link to nothing stays NULL --------------------------------------------

@pytest.mark.parametrize("link", ALL_LINKS, ids=_link_id)
def test_link_to_nothing_stays_null(link):
    table, new_col, old_col, kind = link
    w = _world()
    broken = 99999 if link in ID_LINKS else "NO SUCH NAME"
    row_ids = [w.source(table, **{old_col: broken})]
    if not (table == "saved_framings" and old_col == "object_name"):  # NOT NULL column
        row_ids.append(w.source(table, **{old_col: None}))
    _patch(w.engine)
    for row_id in row_ids:
        assert _value(w.engine, table, new_col, row_id) is None


# --- 5. Names match exactly ----------------------------------------------------

@pytest.mark.parametrize("variant", ["lower", "trailing_space", "leading_space", "empty"])
@pytest.mark.parametrize("link", NAME_LINKS, ids=_link_id)
def test_name_must_match_exactly(link, variant):
    table, new_col, old_col, kind = link
    w = _world()
    name = w.name_of(kind)
    value = {"lower": name.lower(), "trailing_space": name + " ",
             "leading_space": " " + name, "empty": ""}[variant]
    assert value != name
    row_id = w.source(table, **{old_col: value})
    _patch(w.engine)
    assert _value(w.engine, table, new_col, row_id) is None


# --- 6. The rig name is never used ---------------------------------------------

@pytest.mark.parametrize("table,id_col,name_col", [
    ("journal_sessions", "rig_id_snapshot", "rig_name_snapshot"),
    ("saved_framings", "rig_id", "rig_name"),
])
def test_rig_name_is_never_used(table, id_col, name_col):
    w = _world()
    own_name = w.own["rig"].rig_name
    no_id = w.source(table, **{id_col: None, name_col: own_name})
    broken_id = w.source(table, **{id_col: 99999, name_col: own_name})
    # Valid row number with a name that points at a different rig: the row number wins.
    second_rig = Rig(user_id=w.a.id, rig_name="Second Rig")
    w.db.add(second_rig)
    w.db.commit()
    mismatched = w.source(table, **{id_col: second_rig.id, name_col: own_name})
    _patch(w.engine)
    assert _value(w.engine, table, "rig_record_uid", no_id) is None
    assert _value(w.engine, table, "rig_record_uid", broken_id) is None
    assert _value(w.engine, table, "rig_record_uid", mismatched) == second_rig.record_uid


# --- 7. Duplicate names leave the link NULL -------------------------------------

def test_duplicate_target_names_stay_null():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'dup');")
        for name in ("DUP", "DUP", "SOLO"):
            conn.exec_driver_sql(
                "INSERT INTO astro_objects (user_id, object_name, ra_hours, dec_deg) VALUES (1, ?, 1, 2);",
                (name,))
        for name in ("Twin", "Twin", "Single"):
            conn.exec_driver_sql(
                "INSERT INTO locations (user_id, name, lat, lon, timezone) VALUES (1, ?, 1, 2, 'UTC');",
                (name,))
        conn.exec_driver_sql(
            "INSERT INTO journal_sessions (id, user_id, date_utc, object_name, location_name) "
            "VALUES (1, 1, '2026-01-01', 'DUP', 'Twin'), (2, 1, '2026-01-01', 'SOLO', 'Single');")
        conn.exec_driver_sql(
            "INSERT INTO saved_framings (id, user_id, object_name) VALUES (1, 1, 'DUP'), (2, 1, 'SOLO');")
        conn.exec_driver_sql(
            "INSERT INTO projects (id, user_id, name, target_object_name) "
            "VALUES ('p1', 1, 'P1', 'DUP'), ('p2', 1, 'P2', 'SOLO');")

    _patch(engine)

    for table, col, dup_id, solo_id in (
        ("journal_sessions", "object_record_uid", 1, 2),
        ("journal_sessions", "location_record_uid", 1, 2),
        ("saved_framings", "object_record_uid", 1, 2),
        ("projects", "target_object_record_uid", "p1", "p2"),
    ):
        assert _value(engine, table, col, dup_id) is None, f"{table}.{col}"
        assert _value(engine, table, col, solo_id), f"{table}.{col}"


# --- 8. Rerun fills only empty values ------------------------------------------

def _snapshot_new_columns(engine):
    with engine.connect() as conn:
        return {
            (table, new_col): conn.exec_driver_sql(
                f"SELECT id, {new_col} FROM {table} ORDER BY id;").fetchall()
            for table, new_col, _old, _kind in ALL_LINKS
        }


def _world_with_every_link_set():
    w = _world()
    ids = {}
    for table, new_col, old_col, kind in ALL_LINKS:
        value = w.id_of(kind) if (table, new_col, old_col, kind) in ID_LINKS else w.name_of(kind)
        ids[(table, new_col)] = w.source(table, **{old_col: value})
    return w, ids


def test_rerun_without_marker_changes_nothing_and_keeps_existing_values():
    w, ids = _world_with_every_link_set()
    preset_rig = w.source("rigs", telescope_id=w.id_of("component"), telescope_record_uid="kept-value")

    _patch(w.engine)
    first = _snapshot_new_columns(w.engine)
    assert _value(w.engine, "rigs", "telescope_record_uid", preset_rig) == "kept-value"

    with w.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM nova_migrations;")
    _patch(w.engine)

    assert _snapshot_new_columns(w.engine) == first
    assert _value(w.engine, "rigs", "telescope_record_uid", preset_rig) == "kept-value"


# --- 9. Runs once only ----------------------------------------------------------

def test_fill_runs_once_and_does_not_relink_after_unlink():
    w, ids = _world_with_every_link_set()
    session_id = ids[("journal_sessions", "object_record_uid")]

    _patch(w.engine)
    assert _markers(w.engine) == ["uid_links_v1"]
    assert _value(w.engine, "journal_sessions", "object_record_uid", session_id) == w.uid_of("object")

    with w.engine.begin() as conn:  # user unlinks; the old column is still filled
        conn.exec_driver_sql("UPDATE journal_sessions SET object_record_uid = NULL WHERE id = ?;", (session_id,))
    _patch(w.engine)
    assert _value(w.engine, "journal_sessions", "object_record_uid", session_id) is None
    assert _markers(w.engine) == ["uid_links_v1"]

    with w.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM nova_migrations;")
    _patch(w.engine)
    assert _value(w.engine, "journal_sessions", "object_record_uid", session_id) == w.uid_of("object")


# --- 10. Existing columns are untouched -----------------------------------------

def test_existing_columns_and_stable_uids_unchanged():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'u1'), (2, 'u2');")
        conn.exec_driver_sql(
            "INSERT INTO components (id, user_id, kind, name, stable_uid) VALUES "
            "(1, 1, 'telescope', 'Scope', 'comp-s-1'), (2, 1, 'camera', 'Cam', 'comp-s-2'), "
            "(3, 2, 'telescope', 'Other', 'comp-s-3');")
        conn.exec_driver_sql(
            "INSERT INTO rigs (id, user_id, rig_name, telescope_id, camera_id, reducer_extender_id, "
            "guide_telescope_id, guide_camera_id, stable_uid) VALUES "
            "(1, 1, 'Rig', 1, 2, 3, 99999, 2, 'rig-s-1');")
        conn.exec_driver_sql(
            "INSERT INTO astro_objects (id, user_id, object_name, ra_hours, dec_deg) VALUES (1, 1, 'M 31', 1, 2);")
        conn.exec_driver_sql(
            "INSERT INTO locations (id, user_id, name, lat, lon, timezone, stable_uid) "
            "VALUES (1, 1, 'Home', 1, 2, 'UTC', 'loc-s-1');")
        conn.exec_driver_sql(
            "INSERT INTO journal_sessions (id, user_id, date_utc, object_name, location_name, "
            "rig_id_snapshot, rig_name_snapshot, rig_stable_uid_snapshot) VALUES "
            "(1, 1, '2026-01-01', 'M 31', 'Home', 1, 'Rig', 'rig-s-1'), "
            "(2, 1, '2026-01-02', 'm 31', 'Nowhere', 99999, 'Rig', 'rig-s-x');")
        conn.exec_driver_sql(
            "INSERT INTO saved_framings (id, user_id, object_name, rig_id, rig_name, rig_stable_uid) "
            "VALUES (1, 1, 'M 31', 1, 'Rig', 'rig-s-1');")
        conn.exec_driver_sql(
            "INSERT INTO projects (id, user_id, name, target_object_name) VALUES ('p1', 1, 'P', 'M 31');")

    tables = ("components", "rigs", "astro_objects", "locations", "journal_sessions",
              "saved_framings", "projects")

    def snapshot(columns_by_table):
        with engine.connect() as conn:
            return {
                t: conn.exec_driver_sql(
                    f"SELECT {', '.join(cols)} FROM {t} ORDER BY id;").fetchall()
                for t, cols in columns_by_table.items()
            }

    with engine.connect() as conn:
        old_columns = {t: _columns(conn, t) for t in tables}
    before = snapshot(old_columns)

    _patch(engine)

    assert snapshot(old_columns) == before
    # And the links that are valid did get filled, so the patch really ran.
    assert _value(engine, "rigs", "telescope_record_uid", 1)
    assert _value(engine, "rigs", "reducer_extender_record_uid", 1) is None  # other user's component
    assert _value(engine, "rigs", "guide_telescope_record_uid", 1) is None  # points at nothing


# --- 11. Failure part-way rolls back --------------------------------------------

def test_failure_during_fill_rolls_back_and_writes_no_marker():
    w, ids = _world_with_every_link_set()
    _patch(w.engine)

    with w.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM nova_migrations;")
        for table, new_col, _old, _kind in ALL_LINKS:
            conn.exec_driver_sql(f"UPDATE {table} SET {new_col} = NULL;")
        # projects is filled last, so every earlier link has been written when this fires.
        conn.exec_driver_sql(
            "CREATE TRIGGER fail_project_link BEFORE UPDATE OF target_object_record_uid ON projects "
            "BEGIN SELECT RAISE(ABORT, 'forced failure'); END;")

    with pytest.raises(Exception, match="forced failure"):
        _patch(w.engine)

    with w.engine.connect() as conn:
        for table, new_col, _old, _kind in ALL_LINKS:
            filled = conn.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table} WHERE {new_col} IS NOT NULL;").scalar()
            assert filled == 0, f"{table}.{new_col}"
    assert _markers(w.engine) == []
