"""
nova/record_links.py: target -> record_uid helpers, per-row sync, the
name-only bulk re-link and the agreement check.

No route calls these yet; this file tests the helpers on their own.
"""

import os
import sys
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import _run_schema_patches
from nova.models import (
    Base, DbUser, Location, Component, Rig, AstroObject, JournalSession, SavedFraming, Project,
)
from nova.record_links import (
    LINKS, NAME_LINKS,
    uid_of, uid_for_id, uid_for_name,
    sync_rig_links, sync_session_links, sync_framing_links, sync_project_link,
    resync_user_links, count_link_disagreements,
)


ZERO = {"wrong": 0, "missing": 0}


class World:
    """Two users, each with a telescope, camera, rig, object and location."""

    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        # Same flags as the app's SessionLocal.
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)
        self.a = DbUser(username="links_a")
        self.b = DbUser(username="links_b")
        self.db.add_all([self.a, self.b])
        self.db.flush()
        self.own = self._targets(self.a, "A", "M 31", "Home")
        self.other = self._targets(self.b, "B", "M 42", "Away")
        self.db.commit()

    def _targets(self, user, tag, obj_name, loc_name):
        tel = Component(user_id=user.id, kind="telescope", name=f"Scope {tag}")
        cam = Component(user_id=user.id, kind="camera", name=f"Cam {tag}")
        obj = AstroObject(user_id=user.id, object_name=obj_name, ra_hours=1.0, dec_deg=2.0)
        loc = Location(user_id=user.id, name=loc_name, lat=1.0, lon=2.0, timezone="UTC")
        self.db.add_all([tel, cam, obj, loc])
        self.db.flush()
        rig = Rig(user_id=user.id, rig_name=f"Rig {tag}", telescope_id=tel.id, camera_id=cam.id)
        sync_rig_links(self.db, rig)  # start in agreement
        self.db.add(rig)
        self.db.flush()
        return {"telescope": tel, "camera": cam, "rig": rig, "object": obj, "location": loc}

    def session(self, user=None, **cols):
        row = JournalSession(user_id=(user or self.a).id, date_utc=date(2026, 1, 1), **cols)
        self.db.add(row)
        return row

    def project(self, user=None, **cols):
        row = Project(id=uuid.uuid4().hex, user_id=(user or self.a).id, name=f"P {uuid.uuid4().hex[:6]}", **cols)
        self.db.add(row)
        return row

    def check(self, **kw):
        return count_link_disagreements(self.db, **kw)


@pytest.fixture
def w():
    world = World()
    yield world
    world.db.close()


# --- The shared list ----------------------------------------------------------

def test_links_list_matches_models():
    tables = Base.metadata.tables
    assert len(LINKS) == 11
    for link in LINKS:
        assert link.uid_col in tables[link.table].c, link.name
        assert link.old_col in tables[link.table].c, link.name
        assert link.match_col in tables[link.target].c, link.name
        assert "record_uid" in tables[link.target].c, link.name
    # Rigs and components are only ever linked by row number.
    for link in LINKS:
        if link.target in ("rigs", "components"):
            assert link.by_id, link.name
    assert {l.name for l in NAME_LINKS} == {
        "journal_sessions.object_record_uid", "journal_sessions.location_record_uid",
        "saved_framings.object_record_uid", "projects.target_object_record_uid",
    }


def test_links_unpack_like_the_old_tuple():
    table, new_col, old_col, target, match_col = LINKS[0]
    assert (table, new_col, old_col, target, match_col) == (
        "rigs", "telescope_record_uid", "telescope_id", "components", "id")


# --- uid_of / uid_for_id / uid_for_name ------------------------------------------

def test_uid_for_id_same_user_only(w):
    rig = w.own["rig"]
    assert uid_for_id(w.db, Rig, w.a.id, rig.id) == rig.record_uid
    assert uid_for_id(w.db, Component, w.a.id, w.own["telescope"].id) == w.own["telescope"].record_uid
    assert uid_for_id(w.db, Rig, w.a.id, w.other["rig"].id) is None
    assert uid_for_id(w.db, Rig, w.a.id, 999999) is None
    assert uid_for_id(w.db, Rig, w.a.id, None) is None
    assert uid_for_id(w.db, Rig, None, rig.id) is None


@pytest.mark.parametrize("model", [AstroObject, Location])
def test_uid_for_id_refuses_name_targets(w, model):
    with pytest.raises(ValueError):
        uid_for_id(w.db, model, w.a.id, 1)


@pytest.mark.parametrize("model", [Rig, Component])
def test_uid_for_name_refuses_rigs_and_components(w, model):
    with pytest.raises(ValueError):
        uid_for_name(w.db, model, w.a.id, "Rig A")


@pytest.mark.parametrize("model,kind", [(AstroObject, "object"), (Location, "location")])
def test_uid_for_name_exact_same_user(w, model, kind):
    target = w.own[kind]
    name = target.object_name if kind == "object" else target.name
    other = w.other[kind]
    other_name = other.object_name if kind == "object" else other.name
    assert uid_for_name(w.db, model, w.a.id, name) == target.record_uid
    assert uid_for_name(w.db, model, w.a.id, other_name) is None
    for variant in (name.lower(), name + " ", " " + name, "", None):
        assert uid_for_name(w.db, model, w.a.id, variant) is None, repr(variant)


def test_uid_for_name_skips_target_with_empty_uid(w):
    w.db.execute(text("UPDATE astro_objects SET record_uid = '' WHERE id = :id"), {"id": w.own["object"].id})
    assert uid_for_name(w.db, AstroObject, w.a.id, "M 31") is None


def test_uid_of_unflushed_row_gets_uid_that_survives_flush(w):
    obj = AstroObject(user_id=w.a.id, object_name="NGC 1", ra_hours=1.0, dec_deg=2.0)
    w.db.add(obj)
    uid = uid_of(obj, w.a.id)
    assert uid
    w.db.flush()
    assert obj.record_uid == uid
    assert uid_of(obj, w.b.id) is None
    assert uid_of(None, w.a.id) is None


def test_uid_for_name_does_not_see_unflushed_rows(w):
    w.db.add(AstroObject(user_id=w.a.id, object_name="NGC 2", ra_hours=1.0, dec_deg=2.0))
    assert uid_for_name(w.db, AstroObject, w.a.id, "NGC 2") is None
    w.db.flush()
    assert uid_for_name(w.db, AstroObject, w.a.id, "NGC 2")


# --- Per-row sync ------------------------------------------------------------------

def test_sync_rig_links_follows_each_id_column(w):
    tel, cam = w.own["telescope"], w.own["camera"]
    rig = Rig(user_id=w.a.id, rig_name="Synced", telescope_id=tel.id, camera_id=cam.id,
              reducer_extender_id=w.other["telescope"].id, guide_telescope_id=999999,
              guide_camera_id=cam.id)
    w.db.add(rig)
    sync_rig_links(w.db, rig)
    assert rig.telescope_record_uid == tel.record_uid
    assert rig.camera_record_uid == cam.record_uid
    assert rig.reducer_extender_record_uid is None  # other user's component
    assert rig.guide_telescope_record_uid is None   # no such row
    assert rig.guide_camera_record_uid == cam.record_uid
    # Old columns untouched.
    assert (rig.telescope_id, rig.reducer_extender_id, rig.guide_telescope_id) == (
        tel.id, w.other["telescope"].id, 999999)

    rig.camera_id = None
    sync_rig_links(w.db, rig)
    assert rig.camera_record_uid is None


def test_sync_session_links(w):
    s = w.session(object_name="M 31", location_name="Home", rig_id_snapshot=w.own["rig"].id)
    sync_session_links(w.db, s)
    assert s.object_record_uid == w.own["object"].record_uid
    assert s.location_record_uid == w.own["location"].record_uid
    assert s.rig_record_uid == w.own["rig"].record_uid

    s.object_name, s.location_name, s.rig_id_snapshot = "", None, w.other["rig"].id
    sync_session_links(w.db, s)
    assert (s.object_record_uid, s.location_record_uid, s.rig_record_uid) == (None, None, None)


def test_sync_session_links_without_rig_leaves_rig_uid(w):
    s = w.session(object_name="M 31", rig_id_snapshot=w.own["rig"].id, rig_record_uid="kept")
    sync_session_links(w.db, s, include_rig=False)
    assert s.rig_record_uid == "kept"
    assert s.object_record_uid == w.own["object"].record_uid


def test_sync_framing_links(w):
    f = SavedFraming(user_id=w.a.id, object_name="M 31", rig_id=w.own["rig"].id)
    w.db.add(f)
    sync_framing_links(w.db, f)
    assert (f.rig_record_uid, f.object_record_uid) == (w.own["rig"].record_uid, w.own["object"].record_uid)
    f.rig_id, f.object_name = w.other["rig"].id, "M 42"
    sync_framing_links(w.db, f)
    assert (f.rig_record_uid, f.object_record_uid) == (None, None)


def test_sync_project_link(w):
    p = w.project(target_object_name="M 31")
    sync_project_link(w.db, p)
    assert p.target_object_record_uid == w.own["object"].record_uid
    p.target_object_name = None
    sync_project_link(w.db, p)
    assert p.target_object_record_uid is None


def _synced_session_with_deleted_targets(w):
    s = w.session(object_name="M 31", location_name="Home", rig_id_snapshot=w.own["rig"].id)
    sync_session_links(w.db, s)
    uids = (s.object_record_uid, s.location_record_uid, s.rig_record_uid)
    w.db.commit()
    for kind in ("object", "location", "rig"):
        w.db.delete(w.own[kind])
    w.db.commit()
    return s, uids


def test_sync_after_target_deleted_keeps_uid(w):
    s, uids = _synced_session_with_deleted_targets(w)
    s.notes = "edited"  # an edit that leaves the old links as they are
    sync_session_links(w.db, s)
    assert (s.object_record_uid, s.location_record_uid, s.rig_record_uid) == uids
    w.db.commit()
    assert all(v == ZERO for v in w.check().values())


def test_sync_after_component_deleted_keeps_uid(w):
    rig, cam = w.own["rig"], w.own["camera"]
    cam_uid = rig.camera_record_uid
    w.db.delete(cam)
    w.db.commit()
    sync_rig_links(w.db, rig)
    assert rig.camera_record_uid == cam_uid
    assert rig.telescope_record_uid == w.own["telescope"].record_uid


def test_sync_clearing_link_clears_uid_of_deleted_target(w):
    s, _uids = _synced_session_with_deleted_targets(w)
    s.object_name, s.location_name, s.rig_id_snapshot = "", None, None
    sync_session_links(w.db, s)
    assert (s.object_record_uid, s.location_record_uid, s.rig_record_uid) == (None, None, None)


def test_sync_name_that_does_not_resolve_clears_uid_of_live_target(w):
    s = w.session(object_name="M 31", location_name="Home")
    p = w.project(target_object_name="M 31")
    f = SavedFraming(user_id=w.a.id, object_name="M 31", rig_id=w.own["rig"].id)
    w.db.add(f)
    sync_session_links(w.db, s)
    sync_project_link(w.db, p)
    sync_framing_links(w.db, f)
    w.db.commit()

    s.object_name, s.location_name = "Nowhere", "Nowhere"
    p.target_object_name = "Nowhere"
    f.object_name, f.rig_id = "Nowhere", 999999
    sync_session_links(w.db, s)
    sync_project_link(w.db, p)
    sync_framing_links(w.db, f)
    assert (s.object_record_uid, s.location_record_uid) == (None, None)
    assert p.target_object_record_uid is None
    assert (f.object_record_uid, f.rig_record_uid) == (None, None)


def test_sync_keeps_uid_that_exists_only_at_another_user_and_check_reports_it(w):
    foreign_uid = w.other["object"].record_uid
    s = w.session(object_name="Nowhere", object_record_uid=foreign_uid)
    sync_session_links(w.db, s)
    assert s.object_record_uid == foreign_uid  # no row of this user: treated as a deleted target
    w.db.commit()
    assert w.check()["journal_sessions.object_record_uid"] == {"wrong": 1, "missing": 0}


def test_sync_after_delete_then_recreate_follows_new_target(w):
    s, _uids = _synced_session_with_deleted_targets(w)
    new_obj = AstroObject(user_id=w.a.id, object_name="M 31", ra_hours=1.0, dec_deg=2.0)
    w.db.add(new_obj)
    w.db.flush()
    sync_session_links(w.db, s)
    assert s.object_record_uid == new_obj.record_uid


# --- resync_user_links ------------------------------------------------------------

@pytest.mark.parametrize("link", [l for l in LINKS if l.by_id], ids=lambda l: l.name)
def test_resync_refuses_row_number_links(w, link):
    with pytest.raises(ValueError):
        resync_user_links(w.db, w.a.id, links=(link,))


def test_resync_relinks_recreated_targets_by_exact_name(w):
    s = w.session(object_name="M 31", location_name="Home")
    p = w.project(target_object_name="M 31")
    gone = w.session(object_name="Gone", object_record_uid="dangling")
    theirs = w.session(user=w.b, object_name="M 42", location_name="Away")
    for row in (s, theirs):
        sync_session_links(w.db, row)
    sync_project_link(w.db, p)
    theirs_uid = theirs.object_record_uid
    w.db.commit()

    # A config import deletes and recreates the user's objects and locations.
    w.db.delete(w.own["object"])
    w.db.delete(w.own["location"])
    w.db.flush()
    new_obj = AstroObject(user_id=w.a.id, object_name="M 31", ra_hours=1.0, dec_deg=2.0)
    new_loc = Location(user_id=w.a.id, name="Home", lat=1.0, lon=2.0, timezone="UTC")
    w.db.add_all([new_obj, new_loc])  # not flushed: resync flushes first

    changed = resync_user_links(w.db, w.a.id)
    assert changed["journal_sessions.object_record_uid"] == 1
    assert changed["journal_sessions.location_record_uid"] == 1
    assert changed["projects.target_object_record_uid"] == 1
    # Loaded rows see the new value.
    assert s.object_record_uid == new_obj.record_uid
    assert s.location_record_uid == new_loc.record_uid
    assert p.target_object_record_uid == new_obj.record_uid
    # A name with no target keeps its UID; another user's rows are untouched.
    assert gone.object_record_uid == "dangling"
    assert theirs.object_record_uid == theirs_uid
    assert resync_user_links(w.db, w.a.id)["journal_sessions.object_record_uid"] == 0


# --- count_link_disagreements ---------------------------------------------------------

def test_check_is_zero_after_uid_links_v1_fill():
    world = World()
    db, own, other = world.db, world.own, world.other
    world.session(object_name="M 31", location_name="Home", rig_id_snapshot=own["rig"].id)
    world.session(object_name="M 42", location_name="Away", rig_id_snapshot=other["rig"].id)
    world.session(object_name="nothing", rig_id_snapshot=999999)
    db.add(SavedFraming(user_id=world.a.id, object_name="M 31", rig_id=own["rig"].id))
    world.project(target_object_name="M 31")
    db.commit()
    with world.engine.begin() as conn:
        _run_schema_patches(conn)
    assert world.check() == {l.name: ZERO for l in LINKS}
    db.close()


def test_check_counts_missing_wrong_and_foreign(w):
    s = w.session(object_name="M 31", location_name="Home", rig_id_snapshot=w.own["rig"].id)
    sync_session_links(w.db, s)
    w.db.commit()
    assert all(v == ZERO for v in w.check().values())

    s.object_record_uid = None                               # resolves, UID empty
    s.location_record_uid = w.other["location"].record_uid   # another user's row
    s.rig_record_uid = w.own["telescope"].record_uid         # not a rig at all -> points at nothing
    w.db.commit()
    result = w.check()
    assert result["journal_sessions.object_record_uid"] == {"wrong": 0, "missing": 1}
    assert result["journal_sessions.location_record_uid"] == {"wrong": 1, "missing": 0}
    assert result["journal_sessions.rig_record_uid"] == ZERO

    # Old link cleared but UID still points at an existing row: wrong.
    s.object_name, s.object_record_uid = "", w.own["object"].record_uid
    w.db.commit()
    assert w.check()["journal_sessions.object_record_uid"] == {"wrong": 1, "missing": 0}


def test_check_accepts_uid_of_deleted_target(w):
    s = w.session(object_name="M 31", rig_id_snapshot=w.own["rig"].id)
    sync_session_links(w.db, s)
    w.db.commit()
    w.db.delete(w.own["object"])
    w.db.delete(w.own["rig"])
    w.db.commit()
    result = w.check()
    assert result["journal_sessions.object_record_uid"] == ZERO
    assert result["journal_sessions.rig_record_uid"] == ZERO


def test_check_filters_by_user_and_is_read_only(w):
    s = w.session(user=w.b, object_name="M 42")  # UID never set: missing for user B only
    w.db.commit()
    assert w.check(user_id=w.a.id)["journal_sessions.object_record_uid"] == ZERO
    assert w.check(user_id=w.b.id)["journal_sessions.object_record_uid"] == {"wrong": 0, "missing": 1}
    with w.engine.connect() as conn:
        assert count_link_disagreements(conn)["journal_sessions.object_record_uid"]["missing"] == 1
    w.db.refresh(s)
    assert s.object_record_uid is None
