"""
Rig.active: the model default and the schema patch.

Covers the default on a fresh insert and the upgrade path for an existing
database whose rigs table predates the column, including idempotency.
No route, template, export or import reads the flag yet.
"""

import os
import sys

from sqlalchemy import create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import _run_schema_patches
from nova.models import DbUser, Rig

from test_db_upgrade_simulation import MINIMAL_BASELINE_STATEMENTS


def _baseline_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
    return engine


def _rig_columns(conn):
    return {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(rigs);").fetchall()}


def _rig_actives(conn):
    return dict(conn.exec_driver_sql("SELECT id, active FROM rigs ORDER BY id;").fetchall())


def _active_indexes(conn):
    """Names of every index on exactly the `active` column of rigs."""
    found = []
    for row in conn.exec_driver_sql("PRAGMA index_list(rigs);").fetchall():
        name = row[1]
        cols = [r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info({name});").fetchall()]
        if cols == ["active"]:
            found.append(name)
    return found


# --- 1. Default on insert ----------------------------------------------------

def test_new_rig_is_active_by_default(db_session):
    user = DbUser(username="rig_active_user")
    db_session.add(user)
    db_session.commit()

    rig = Rig(user_id=user.id, rig_name="Default Rig")
    db_session.add(rig)
    db_session.commit()

    assert rig.active is True


# --- 2. Upgrade path ---------------------------------------------------------

def test_upgrade_adds_column_and_every_existing_row_is_active():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a');")
        for n in range(3):
            conn.exec_driver_sql(
                "INSERT INTO rigs (user_id, rig_name) VALUES (1, ?);", (f"Rig{n}",))

    with engine.begin() as conn:
        assert "active" not in _rig_columns(conn)
        _run_schema_patches(conn)

    with engine.connect() as conn:
        assert "active" in _rig_columns(conn)
        assert _rig_actives(conn) == {1: 1, 2: 1, 3: 1}
        assert _active_indexes(conn) == ["ix_rigs_active"]


# --- 3. Idempotency ----------------------------------------------------------

def test_running_the_patches_twice_changes_nothing_and_raises_nothing():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a');")
        for n in range(2):
            conn.exec_driver_sql(
                "INSERT INTO rigs (user_id, rig_name) VALUES (1, ?);", (f"Rig{n}",))

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        columns_first = _rig_columns(conn)
        actives_first = _rig_actives(conn)

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        assert _rig_columns(conn) == columns_first
        assert _rig_actives(conn) == actives_first
        assert _active_indexes(conn) == ["ix_rigs_active"]
