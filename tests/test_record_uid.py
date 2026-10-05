"""
record_uid on Location, Component, Rig and AstroObject.

Covers the model default, the per-user unique index, the schema patch
(upgrade and fresh paths, backfill, idempotency), copy paths, YAML upsert
and the guarantee that record_uid never leaks into to_dict() or YAML export.
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import (
    _run_schema_patches,
    _seed_user_from_guest_data,
    _migrate_locations,
    _migrate_objects,
    _migrate_components_and_rigs,
    export_user_to_yaml,
)
from nova.migration import import_catalog_pack_for_user, normalize_object_name
from nova.models import Base, DbUser, Location, Component, Rig, AstroObject

from test_db_upgrade_simulation import MINIMAL_BASELINE_STATEMENTS


TABLES = ("locations", "components", "rigs", "astro_objects")


def _make(model, user_id, n=0, **extra):
    """Build a minimal valid row of `model` for `user_id`."""
    if model is Location:
        return Location(user_id=user_id, name=f"Loc{n}", lat=1.0, lon=2.0, timezone="UTC", **extra)
    if model is Component:
        return Component(user_id=user_id, kind="telescope", name=f"Scope{n}", **extra)
    if model is Rig:
        return Rig(user_id=user_id, rig_name=f"Rig{n}", **extra)
    if model is AstroObject:
        return AstroObject(user_id=user_id, object_name=f"OBJ{n}", ra_hours=1.0, dec_deg=2.0, **extra)
    raise ValueError(model)


def _new_user(db_session, username):
    user = DbUser(username=username)
    db_session.add(user)
    db_session.commit()
    return user


def _baseline_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for stmt in MINIMAL_BASELINE_STATEMENTS:
            conn.exec_driver_sql(stmt)
    return engine


def _record_uid_indexes(conn, table):
    """Return [(name, unique)] for every index on exactly (user_id, record_uid)."""
    found = []
    for row in conn.exec_driver_sql(f"PRAGMA index_list({table});").fetchall():
        name, unique = row[1], row[2]
        cols = [r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info({name});").fetchall()]
        if cols == ["user_id", "record_uid"]:
            found.append((name, unique))
    return found


def _uids(conn, table):
    return dict(conn.exec_driver_sql(f"SELECT id, record_uid FROM {table} ORDER BY id;").fetchall())


# --- 1. Default on insert ----------------------------------------------------

@pytest.mark.parametrize("model", [Location, Component, Rig, AstroObject])
def test_insert_assigns_non_empty_distinct_record_uid(db_session, model):
    user = _new_user(db_session, "uid_user")
    rows = [_make(model, user.id, n) for n in range(3)]
    db_session.add_all(rows)
    db_session.commit()

    uids = [r.record_uid for r in rows]
    assert all(uids)
    assert len(set(uids)) == len(uids)


# --- 2. Per-user uniqueness --------------------------------------------------

@pytest.mark.parametrize("model", [Location, Component, Rig, AstroObject])
def test_record_uid_unique_per_user_only(db_session, model):
    user_a = _new_user(db_session, "uid_a")
    user_b = _new_user(db_session, "uid_b")
    shared = "a" * 32

    db_session.add_all([
        _make(model, user_a.id, 1, record_uid=shared),
        _make(model, user_b.id, 1, record_uid=shared),
    ])
    db_session.commit()

    db_session.add(_make(model, user_a.id, 2, record_uid=shared))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- 3. Upgrade path ---------------------------------------------------------

def test_upgrade_adds_column_index_and_backfills_idempotently():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a'), (2, 'old_b');")
        for uid in (1, 2):
            for n in range(2):
                conn.exec_driver_sql(
                    "INSERT INTO locations (user_id, name, lat, lon, timezone) VALUES (?, ?, 1, 2, 'UTC');",
                    (uid, f"Loc{n}"))
                conn.exec_driver_sql(
                    "INSERT INTO components (user_id, kind, name) VALUES (?, 'telescope', ?);",
                    (uid, f"Scope{n}"))
                conn.exec_driver_sql(
                    "INSERT INTO rigs (user_id, rig_name) VALUES (?, ?);",
                    (uid, f"Rig{n}"))
                conn.exec_driver_sql(
                    "INSERT INTO astro_objects (user_id, object_name, ra_hours, dec_deg) VALUES (?, ?, 1, 2);",
                    (uid, f"OBJ{n}"))

    with engine.begin() as conn:
        _run_schema_patches(conn)

    first = {}
    with engine.connect() as conn:
        for table in TABLES:
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table});").fetchall()}
            assert "record_uid" in cols
            assert _record_uid_indexes(conn, table) == [(f"uq_{table}_user_record_uid", 1)]

            uids = _uids(conn, table)
            assert len(uids) == 4
            assert all(uids.values())
            assert len(set(uids.values())) == 4
            first[table] = uids

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        for table in TABLES:
            assert _uids(conn, table) == first[table]
            assert len(_record_uid_indexes(conn, table)) == 1


def test_backfill_only_touches_null_or_empty_rows():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a');")
        for n in range(3):
            conn.exec_driver_sql(
                "INSERT INTO astro_objects (user_id, object_name, ra_hours, dec_deg) VALUES (1, ?, 1, 2);",
                (f"OBJ{n}",))
        _run_schema_patches(conn)

    with engine.begin() as conn:
        before = _uids(conn, "astro_objects")
        ids = sorted(before)
        conn.exec_driver_sql("UPDATE astro_objects SET record_uid = NULL WHERE id = ?;", (ids[0],))
        conn.exec_driver_sql("UPDATE astro_objects SET record_uid = '' WHERE id = ?;", (ids[1],))
        _run_schema_patches(conn)
        after = _uids(conn, "astro_objects")

    assert after[ids[0]] and after[ids[1]]
    assert after[ids[0]] != before[ids[0]]
    assert after[ids[2]] == before[ids[2]]


# --- 4. Fresh path -----------------------------------------------------------

def test_fresh_create_all_then_patch_creates_index_once():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        for table in TABLES:
            assert _record_uid_indexes(conn, table) == [(f"uq_{table}_user_record_uid", 1)]


# --- 5. Copies get their own UID ---------------------------------------------

def test_import_item_copy_gets_new_record_uid(multi_user_client, db_session):
    client, user_ids = multi_user_client
    user_b_id = user_ids['user_b_id']

    obj = AstroObject(user_id=user_b_id, object_name="SHARED_UID_OBJ", ra_hours=1, dec_deg=1, is_shared=True)
    comp = Component(user_id=user_b_id, kind="telescope", name="Shared UID Scope", is_shared=True)
    db_session.add_all([obj, comp])
    db_session.commit()

    assert client.post('/api/import_item', json={'id': obj.id, 'type': 'object'}).status_code == 200
    assert client.post('/api/import_item', json={'id': comp.id, 'type': 'component'}).status_code == 200

    new_obj = db_session.query(AstroObject).filter_by(original_item_id=obj.id).one()
    new_comp = db_session.query(Component).filter_by(original_item_id=comp.id).one()
    assert new_obj.record_uid and new_obj.record_uid != obj.record_uid
    assert new_comp.record_uid and new_comp.record_uid != comp.record_uid


def test_guest_seeding_copies_get_new_record_uid(db_session):
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    g_loc = _make(Location, guest.id, 1)
    g_obj = _make(AstroObject, guest.id, 1)
    g_tel = Component(user_id=guest.id, kind="telescope", name="Guest Scope")
    g_cam = Component(user_id=guest.id, kind="camera", name="Guest Cam")
    db_session.add_all([g_loc, g_obj, g_tel, g_cam])
    db_session.flush()
    g_rig = Rig(user_id=guest.id, rig_name="Guest Rig", telescope_id=g_tel.id, camera_id=g_cam.id)
    db_session.add(g_rig)
    new_user = _new_user(db_session, "seeded_user")

    _seed_user_from_guest_data(db_session, new_user)
    db_session.commit()

    guest_uids = {r.record_uid for r in (g_loc, g_obj, g_tel, g_cam, g_rig)}
    copies = (
        db_session.query(Location).filter_by(user_id=new_user.id).all()
        + db_session.query(AstroObject).filter_by(user_id=new_user.id).all()
        + db_session.query(Component).filter_by(user_id=new_user.id).all()
        + db_session.query(Rig).filter_by(user_id=new_user.id).all()
    )
    assert len(copies) == 5
    for row in copies:
        assert row.record_uid
        assert row.record_uid not in guest_uids


def test_catalog_pack_import_gives_each_user_own_record_uid(db_session):
    user_a = _new_user(db_session, "pack_a")
    user_b = _new_user(db_session, "pack_b")
    pack = {"objects": [{"Object": "PACK_OBJ_1", "RA": 10.5, "DEC": 20.2, "Type": "Galaxy"}]}

    import_catalog_pack_for_user(db_session, user_a, pack, "uid_pack")
    import_catalog_pack_for_user(db_session, user_b, pack, "uid_pack")
    db_session.commit()

    obj_a = db_session.query(AstroObject).filter_by(user_id=user_a.id).one()
    obj_b = db_session.query(AstroObject).filter_by(user_id=user_b.id).one()
    assert obj_a.record_uid and obj_b.record_uid
    assert obj_a.record_uid != obj_b.record_uid


# --- 6. YAML upsert keeps UID ------------------------------------------------

def test_yaml_upsert_keeps_record_uid(db_session):
    user = _new_user(db_session, "upsert_user")
    loc = Location(user_id=user.id, name="Home", lat=1.0, lon=2.0, timezone="UTC")
    obj = AstroObject(user_id=user.id, object_name=normalize_object_name("M 31"), ra_hours=0.7, dec_deg=41.0)
    tel = Component(user_id=user.id, kind="telescope", name="Scope")
    cam = Component(user_id=user.id, kind="camera", name="Cam")
    db_session.add_all([loc, obj, tel, cam])
    db_session.flush()
    rig = Rig(user_id=user.id, rig_name="Main", telescope_id=tel.id, camera_id=cam.id)
    db_session.add(rig)
    db_session.commit()
    before = {r: r.record_uid for r in (loc, obj, tel, cam, rig)}

    _migrate_locations(db_session, user, {
        "default_location": "Home",
        "locations": {"Home": {"lat": 10.0, "lon": 20.0, "timezone": "UTC"}},
    })
    _migrate_objects(db_session, user, {
        "objects": [{"Object": "M 31", "RA": 0.7, "DEC": 41.0, "Common Name": "Andromeda"}],
    })
    _migrate_components_and_rigs(db_session, user, {
        "components": {
            "telescopes": [{"id": 1, "name": "Scope", "aperture_mm": 100, "focal_length_mm": 500}],
            "cameras": [{"id": 2, "name": "Cam", "sensor_width_mm": 10, "sensor_height_mm": 8,
                         "pixel_size_um": 3.76}],
        },
        "rigs": [{"rig_name": "Main", "telescope_id": 1, "camera_id": 2}],
    }, user.username)
    db_session.commit()

    for row in before:
        db_session.refresh(row)
    # The upsert ran (fields changed) ...
    assert loc.lat == 10.0
    assert obj.common_name == "Andromeda"
    assert tel.aperture_mm == 100
    assert rig.effective_focal_length is not None
    # ... and no row was replaced.
    assert db_session.query(Location).filter_by(user_id=user.id).count() == 1
    assert db_session.query(AstroObject).filter_by(user_id=user.id).count() == 1
    assert db_session.query(Component).filter_by(user_id=user.id).count() == 2
    assert db_session.query(Rig).filter_by(user_id=user.id).count() == 1
    for row, uid in before.items():
        assert row.record_uid == uid


# --- 7. Leak guard -----------------------------------------------------------

def test_record_uid_in_yaml_export_but_not_in_to_dict(db_session, tmp_path):
    user = _new_user(db_session, "leak_user")
    tel = Component(user_id=user.id, kind="telescope", name="Scope", aperture_mm=100, focal_length_mm=500)
    cam = Component(user_id=user.id, kind="camera", name="Cam", sensor_width_mm=10, sensor_height_mm=8,
                    pixel_size_um=3.76)
    obj = _make(AstroObject, user.id, 1)
    db_session.add_all([_make(Location, user.id, 1), obj, tel, cam])
    db_session.flush()
    db_session.add(Rig(user_id=user.id, rig_name="Main", telescope_id=tel.id, camera_id=cam.id))
    db_session.commit()

    # to_dict() is the API shape and still must not carry the UID.
    assert "record_uid" not in obj.to_dict()

    out_dir = tmp_path / "export"
    assert export_user_to_yaml(user.username, out_dir=str(out_dir)) is True
    files = sorted(os.listdir(out_dir))
    assert files
    by_name = {}
    for name in files:
        with open(out_dir / name, encoding="utf-8") as f:
            by_name[name] = f.read()

    # The config file carries the record and link UID keys; so do the rigs.
    cfg = next(c for n, c in by_name.items() if n.startswith("config_"))
    rigs = next(c for n, c in by_name.items() if n.startswith("rigs_"))
    assert "record_uid" in cfg and "record_uid" in rigs
    # The object UID is in the config file; the component UIDs in the rigs file.
    assert obj.record_uid in cfg
    assert tel.record_uid in rigs and cam.record_uid in rigs


# --- (g) stable_uid untouched ------------------------------------------------

def test_patch_leaves_stable_uid_unchanged():
    engine = _baseline_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username) VALUES (1, 'old_a');")
        for table in ("locations", "components", "rigs"):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN stable_uid VARCHAR(36);")
        conn.exec_driver_sql(
            "INSERT INTO locations (user_id, name, lat, lon, timezone, stable_uid) "
            "VALUES (1, 'Loc', 1, 2, 'UTC', 'loc-stable-1');")
        conn.exec_driver_sql(
            "INSERT INTO components (user_id, kind, name, stable_uid) VALUES (1, 'telescope', 'Scope', 'comp-stable-1');")
        conn.exec_driver_sql(
            "INSERT INTO rigs (user_id, rig_name, stable_uid) VALUES (1, 'Rig', 'rig-stable-1');")

    with engine.begin() as conn:
        _run_schema_patches(conn)

    with engine.connect() as conn:
        for table, expected in (("locations", "loc-stable-1"),
                                ("components", "comp-stable-1"),
                                ("rigs", "rig-stable-1")):
            stable, record = conn.exec_driver_sql(f"SELECT stable_uid, record_uid FROM {table};").one()
            assert stable == expected
            assert record and record != stable
