"""
Tests for purge_user_app_data() in nova/helpers.py.

Two app.db users with deliberately overlapping ids: alice (id 2, session 1)
and bob (id 21, session 12), so prefix-matching bugs ("2" vs "21", "1" vs "12")
show up. All folders live under tmp_path; users.db is replaced by a small mock.
"""
import os
import types
from datetime import date

import pytest
from sqlalchemy import select, func, or_

from nova.models import (
    Base, DbUser, Location, HorizonPoint, AstroObject, Component, Rig,
    SavedView, SavedFraming, JournalSession, Project, UiPref,
    UserCustomFilter, session_projects,
)
from nova.helpers import purge_user_app_data, delete_user, find_orphaned_usernames
from sqla_mocks import MockColumn, MockSelectQuery


A_ID, A_NAME, A_SESSION = 2, "alice", 1
B_ID, B_NAME, B_SESSION = 21, "bob", 12

_PER_USER_MODELS = (Location, AstroObject, Component, Rig, SavedView, SavedFraming,
                    JournalSession, Project, UiPref, UserCustomFilter)

# What purging one seeded user must delete (and what dry run must report).
EXPECTED_DELETED = {
    "session_projects": 1,
    "horizon_points": 1,
    "journal_sessions": 1,
    "projects": 1,
    "saved_framings": 1,
    "saved_views": 1,
    "rigs": 1,
    "components": 2,
    "astro_objects": 2,
    "locations": 1,
    "ui_prefs": 1,
    "user_custom_filters": 1,
    "users": 1,
}

_DIR_ATTRS = {
    "uploads": "UPLOAD_FOLDER",
    "configs": "CONFIG_DIR",
    "logs/asiair": "ASIAIR_LOGS_DIR",
    "logs/phd2": "PHD2_LOGS_DIR",
    "logs/nina": "NINA_LOGS_DIR",
}


# --- users.db mock ---

class _LoginUser:
    username = MockColumn("username")


def _install_auth_mock(monkeypatch, logins, fail_commit=False):
    """users.db lookup that finds a login row only for names in `logins`; scalars()
    lists all of them. delete + commit removes the name from `logins`;
    fail_commit makes commit raise."""
    import nova.auth

    class _Session:
        pending = None

        def scalar(self, stmt):
            name = stmt.whereclause.right.value
            return types.SimpleNamespace(username=name) if name in logins else None

        def scalars(self, stmt):
            return list(logins)

        def delete(self, user):
            self.pending = user.username

        def commit(self):
            if fail_commit:
                raise RuntimeError("users.db is locked")
            logins.discard(self.pending)

        def rollback(self):
            self.pending = None

    monkeypatch.setattr(nova.auth, "db", types.SimpleNamespace(session=_Session(), select=MockSelectQuery))
    monkeypatch.setattr(nova.auth, "User", _LoginUser)


# --- Seeding ---

def _seed_user(s, uid, username, session_id):
    s.add(DbUser(id=uid, username=username))
    loc = Location(user_id=uid, name=f"Home {username}", lat=50, lon=10, timezone="UTC", is_default=True)
    scope = Component(user_id=uid, kind="telescope", name=f"Scope {username}",
                      aperture_mm=100, focal_length_mm=500, is_shared=True)
    cam = Component(user_id=uid, kind="camera", name=f"Cam {username}",
                    sensor_width_mm=23.5, sensor_height_mm=15.7, pixel_size_um=3.76)
    obj = AstroObject(user_id=uid, object_name="M42", ra_hours=5.58, dec_deg=-5.4)
    shared_obj = AstroObject(user_id=uid, object_name="M31", ra_hours=0.71, dec_deg=41.3, is_shared=True)
    s.add_all([loc, scope, cam, obj, shared_obj])
    s.flush()

    s.add(HorizonPoint(location_id=loc.id, az_deg=0, alt_min_deg=10))
    rig = Rig(user_id=uid, rig_name=f"Rig {username}", telescope_id=scope.id, camera_id=cam.id)
    project = Project(id=f"proj-{username}", user_id=uid, name=f"Project {username}")
    s.add_all([rig, project])
    s.flush()

    s.add(SavedView(user_id=uid, name="View", settings_json="{}", is_shared=True))
    s.add(SavedFraming(user_id=uid, object_name="M42", rig_id=rig.id))
    s.add(JournalSession(id=session_id, user_id=uid, date_utc=date(2026, 1, 1), object_name="M42",
                         project_id=project.id, rig_id_snapshot=rig.id))
    s.add(UiPref(user_id=uid, json_blob="{}"))
    s.add(UserCustomFilter(user_id=uid, filter_key="duo", filter_label="Duo"))
    s.flush()
    s.execute(session_projects.insert().values(session_id=session_id, project_id=project.id))

    return types.SimpleNamespace(user_id=uid, username=username, session_id=session_id,
                                 location_id=loc.id, project_id=project.id, shared_obj_id=shared_obj.id)


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _seed_files(dirs, username, uid, session_id):
    """Create the user's files; return the paths purge should report (upload dir, not the image)."""
    upload_dir = os.path.join(dirs["uploads"], username)
    _write(os.path.join(upload_dir, "img.jpg"))
    targets = [upload_dir, os.path.join(dirs["configs"], f"config_{username}.yaml")]
    for key in ("logs/asiair", "logs/phd2", "logs/nina"):
        targets.append(os.path.join(dirs[key], f"{session_id}_x.txt"))
    targets.append(os.path.join(dirs["cache"], f"heatmap_v6_{uid}_loc_fp.part0.json"))
    targets.append(os.path.join(dirs["cache"], f"import_conflicts_{uid}.json"))
    for path in targets[1:]:
        _write(path)
    return targets


@pytest.fixture
def env(db_session, tmp_path, isolated_cache_dir, monkeypatch):
    dirs = {"cache": str(isolated_cache_dir)}
    for rel, attr in _DIR_ATTRS.items():
        path = tmp_path / rel
        path.mkdir(parents=True)
        dirs[rel] = str(path)
        monkeypatch.setattr(f"nova.helpers.{attr}", str(path))
    monkeypatch.setattr("nova.helpers.ADMIN_USERS", {"admin"})
    logins = set()
    _install_auth_mock(monkeypatch, logins)

    a = _seed_user(db_session, A_ID, A_NAME, A_SESSION)
    b = _seed_user(db_session, B_ID, B_NAME, B_SESSION)
    db_session.commit()

    return types.SimpleNamespace(
        s=db_session, tmp=tmp_path, dirs=dirs, logins=logins, a=a, b=b,
        files_a=_seed_files(dirs, A_NAME, A_ID, A_SESSION),
        files_b=_seed_files(dirs, B_NAME, B_ID, B_SESSION),
    )


# --- Helpers ---

def _purge(env, username, dry_run):
    env.s.commit()  # purge uses its own session; leave nothing pending in ours
    summary = purge_user_app_data(username, dry_run=dry_run)
    env.s.expire_all()
    return summary


def _user_row_counts(s, ids):
    counts = {m.__tablename__: s.query(m).filter(m.user_id == ids.user_id).count()
              for m in _PER_USER_MODELS}
    counts["users"] = s.query(DbUser).filter(DbUser.id == ids.user_id).count()
    counts["horizon_points"] = s.query(HorizonPoint).filter(
        HorizonPoint.location_id == ids.location_id).count()
    counts["session_projects"] = s.scalar(
        select(func.count()).select_from(session_projects).where(or_(
            session_projects.c.session_id == ids.session_id,
            session_projects.c.project_id == ids.project_id)))
    return counts


def _db_snapshot(s):
    return {t.name: sorted((tuple(r) for r in s.execute(select(t)).all()), key=repr)
            for t in Base.metadata.sorted_tables}


def _tree(root):
    found = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            found.add(os.path.relpath(os.path.join(dirpath, name), root))
    return found


def _file_snapshot(env):
    """Everything under tmp_path, plus the cache dir if it lives elsewhere."""
    snap = {("tmp", p) for p in _tree(env.tmp)}
    cache = os.path.realpath(env.dirs["cache"])
    if not cache.startswith(os.path.realpath(env.tmp) + os.sep):
        snap |= {("cache", p) for p in _tree(cache)}
    return snap


# --- Tests ---

def test_purge_removes_all_of_a_and_nothing_of_b(env):
    assert _user_row_counts(env.s, env.a) == EXPECTED_DELETED
    b_before = _user_row_counts(env.s, env.b)

    summary = _purge(env, A_NAME, dry_run=False)

    assert summary["refused"] is None
    assert summary["user_id"] == A_ID
    assert summary["deleted_rows"] == EXPECTED_DELETED
    assert all(v == 0 for v in summary["nullified_references"].values())
    assert all(v == 0 for v in _user_row_counts(env.s, env.a).values())
    assert _user_row_counts(env.s, env.b) == b_before

    assert summary["file_errors"] == []
    assert set(summary["deleted_files"]) == set(env.files_a)
    for path in env.files_a:
        assert not os.path.lexists(path), path
    for path in env.files_b:
        assert os.path.exists(path), path
    assert os.path.exists(os.path.join(env.dirs["uploads"], B_NAME, "img.jpg"))


def test_imported_copy_keeps_row_and_clears_origin(env):
    copy = AstroObject(user_id=B_ID, object_name="NGC 7000", ra_hours=20.98, dec_deg=44.3,
                       original_user_id=A_ID, original_item_id=env.a.shared_obj_id)
    env.s.add(copy)
    env.s.commit()
    copy_id = copy.id

    summary = _purge(env, A_NAME, dry_run=False)

    assert summary["nullified_references"]["astro_objects.original_user_id"] == 1
    row = env.s.get(AstroObject, copy_id)
    assert row is not None
    assert row.user_id == B_ID
    assert row.original_user_id is None
    assert row.original_item_id is None


def test_cross_user_project_links_are_cleared(env):
    b_session = env.s.get(JournalSession, B_SESSION)
    b_session.project_id = env.a.project_id
    env.s.execute(session_projects.insert().values(session_id=B_SESSION, project_id=env.a.project_id))
    env.s.commit()

    summary = _purge(env, A_NAME, dry_run=False)

    assert summary["nullified_references"]["journal_sessions.project_id"] == 1
    b_session = env.s.get(JournalSession, B_SESSION)
    assert b_session is not None
    assert b_session.user_id == B_ID
    assert b_session.project_id is None
    links = env.s.execute(select(session_projects)).all()
    assert all(link.project_id != env.a.project_id for link in links)
    # B's own link to B's project is untouched
    assert (B_SESSION, env.b.project_id) in {(l.session_id, l.project_id) for l in links}


def test_no_shared_items_of_a_remain(env):
    for model in (AstroObject, Component, SavedView):
        assert env.s.query(model).filter_by(user_id=A_ID, is_shared=True).count() == 1

    _purge(env, A_NAME, dry_run=False)

    for model in (AstroObject, Component, SavedView):
        assert env.s.query(model).filter_by(user_id=A_ID, is_shared=True).count() == 0, model.__tablename__
        assert env.s.query(model).filter_by(user_id=B_ID, is_shared=True).count() == 1, model.__tablename__


def test_dry_run_changes_nothing_and_counts_match_real_run(env):
    db_before = _db_snapshot(env.s)
    files_before = _file_snapshot(env)

    dry = _purge(env, A_NAME, dry_run=True)

    assert dry["refused"] is None
    assert dry["dry_run"] is True
    assert _db_snapshot(env.s) == db_before
    assert _file_snapshot(env) == files_before
    assert dry["deleted_rows"] == EXPECTED_DELETED
    assert set(dry["deleted_files"]) == set(env.files_a)

    real = _purge(env, A_NAME, dry_run=False)

    assert real["deleted_rows"] == dry["deleted_rows"]
    assert real["nullified_references"] == dry["nullified_references"]
    assert set(real["deleted_files"]) == set(dry["deleted_files"])


@pytest.mark.parametrize("case, username", [
    ("admin", A_NAME),
    ("default", "default"),
    ("guest_user", "guest_user"),
    ("empty", ""),
    ("login_row", A_NAME),
])
def test_refusals_change_nothing(env, monkeypatch, case, username):
    if case == "admin":
        monkeypatch.setattr("nova.helpers.ADMIN_USERS", {"admin", A_NAME})
    if case == "login_row":
        env.logins.add(A_NAME)
    db_before = _db_snapshot(env.s)
    files_before = _file_snapshot(env)

    summary = _purge(env, username, dry_run=False)

    assert summary["refused"]
    assert summary["deleted_rows"] == {}
    assert summary["deleted_files"] == []
    assert _db_snapshot(env.s) == db_before
    assert _file_snapshot(env) == files_before


def test_path_traversal_username_does_not_escape_uploads(env):
    keep = env.tmp / "outside" / "keep.txt"
    _write(str(keep))

    summary = _purge(env, "../outside", dry_run=False)

    assert keep.exists()
    assert os.path.join(env.dirs["uploads"], "../outside") not in summary["deleted_files"]
    assert any("outside base folder" in e for e in summary["file_errors"])


def test_exact_id_matching_spares_b_files(env):
    """alice is id 2 / session 1; bob's id-21 / session-12 files must not match."""
    _purge(env, A_NAME, dry_run=False)

    for key in ("logs/asiair", "logs/phd2", "logs/nina"):
        assert not os.path.exists(os.path.join(env.dirs[key], f"{A_SESSION}_x.txt"))
        assert os.path.exists(os.path.join(env.dirs[key], f"{B_SESSION}_x.txt"))
    assert not os.path.exists(os.path.join(env.dirs["cache"], f"heatmap_v6_{A_ID}_loc_fp.part0.json"))
    assert os.path.exists(os.path.join(env.dirs["cache"], f"heatmap_v6_{B_ID}_loc_fp.part0.json"))
    assert os.path.exists(os.path.join(env.dirs["cache"], f"import_conflicts_{B_ID}.json"))
    assert env.s.get(JournalSession, B_SESSION) is not None
    assert env.s.get(DbUser, B_ID) is not None


# --- delete_user() ---

@pytest.fixture
def app_ctx():
    from nova import app
    with app.app_context():
        yield


@pytest.fixture
def purge_spy(monkeypatch):
    """Replaces purge_user_app_data in nova.helpers; set .result to change what it returns."""
    spy = types.SimpleNamespace(calls=[], result={"refused": None, "file_errors": []})

    def fake(*args, **kwargs):
        spy.calls.append((args, kwargs))
        return spy.result

    monkeypatch.setattr("nova.helpers.purge_user_app_data", fake)
    return spy


def test_delete_user_removes_login_then_purges(app_ctx, monkeypatch, purge_spy):
    logins = {A_NAME}
    _install_auth_mock(monkeypatch, logins)

    assert delete_user(A_NAME) is True
    assert A_NAME not in logins
    assert purge_spy.calls == [((A_NAME,), {"dry_run": False})]


def test_delete_user_failed_login_delete_skips_purge(app_ctx, monkeypatch, purge_spy):
    logins = {A_NAME}
    _install_auth_mock(monkeypatch, logins, fail_commit=True)

    assert delete_user(A_NAME) is False
    assert A_NAME in logins
    assert purge_spy.calls == []


def test_delete_user_refused_purge_still_true_and_warns(app_ctx, monkeypatch, purge_spy, caplog):
    logins = {A_NAME}
    _install_auth_mock(monkeypatch, logins)
    purge_spy.result = {"refused": "user is in ADMIN_USERS", "file_errors": []}

    with caplog.at_level("WARNING", logger="nova.helpers"):
        assert delete_user(A_NAME) is True

    assert len(purge_spy.calls) == 1
    assert any("purge was incomplete" in r.getMessage() and "user is in ADMIN_USERS" in r.getMessage()
               for r in caplog.records)


def test_delete_user_refuses_admin(app_ctx, monkeypatch, purge_spy, caplog):
    logins = {A_NAME}
    _install_auth_mock(monkeypatch, logins)
    monkeypatch.setattr("nova.helpers.ADMIN_USERS", {"admin", A_NAME})

    with caplog.at_level("WARNING", logger="nova.helpers"):
        assert delete_user(A_NAME) is False

    assert A_NAME in logins
    assert purge_spy.calls == []
    assert any("Refused to delete admin account" in r.getMessage() for r in caplog.records)


# --- find_orphaned_usernames() ---

@pytest.fixture
def multi_user(monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", False)


def test_find_orphans_returns_db_users_without_login(env, multi_user):
    env.logins.update({B_NAME, "carol"})
    for name in ("admin", "default", "guest_user"):
        if name != "guest_user":  # conftest's db_session already seeds guest_user
            env.s.add(DbUser(username=name))
        os.makedirs(os.path.join(env.dirs["uploads"], name))
    env.s.commit()

    assert find_orphaned_usernames() == [A_NAME]


def test_find_orphans_includes_upload_folder_without_login_or_db_user(env, multi_user):
    env.logins.update({A_NAME, B_NAME})
    os.makedirs(os.path.join(env.dirs["uploads"], "ghost"))
    _write(os.path.join(env.dirs["uploads"], "stray.txt"))  # plain files are not users

    assert find_orphaned_usernames() == ["ghost"]


def test_find_orphans_zero_logins_raises(env, multi_user):
    with pytest.raises(RuntimeError, match="zero logins"):
        find_orphaned_usernames()


def test_find_orphans_unreadable_login_table_raises(env, multi_user, monkeypatch):
    import nova.auth
    env.logins.add(B_NAME)

    def boom(stmt):
        raise RuntimeError("no such table: user")

    monkeypatch.setattr(nova.auth.db.session, "scalars", boom)
    with pytest.raises(RuntimeError, match="Could not read"):
        find_orphaned_usernames()


def test_find_orphans_single_user_mode_raises(env, monkeypatch):
    monkeypatch.setattr("nova.SINGLE_USER_MODE", True)
    env.logins.add(B_NAME)

    with pytest.raises(RuntimeError, match="SINGLE_USER_MODE"):
        find_orphaned_usernames()
