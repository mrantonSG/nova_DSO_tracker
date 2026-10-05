"""
record_uid reads for a session's, framing's and project's object (step 5 part 4).

The object is found by user + record_uid. The name in object_name /
target_object_name never decides, an empty UID is never compared, and an
object that was deleted is never replaced by a newer row of the same name.
"""
import json
import re
from datetime import date

import pytest

from nova.models import AstroObject, JournalSession, Project, Rig, SavedFraming
from nova.record_links import (
    framed_object_uids, framing_for_object, object_for_uid, objects_by_uid,
    sync_project_link,
)


# --- builders --------------------------------------------------------------------

def _object(db, user_id, name="M42", **cols):
    o = AstroObject(user_id=user_id, object_name=name, ra_hours=5.58, dec_deg=-5.4)
    for key, value in cols.items():
        setattr(o, key, value)
    db.add(o)
    db.flush()
    return o


def _session(db, user_id, obj=None, name="M42", **cols):
    s = JournalSession(user_id=user_id, object_name=name, date_utc=date(2026, 1, 10))
    for key, value in cols.items():
        setattr(s, key, value)
    if obj is not None:
        s.object_record_uid = obj.record_uid
    db.add(s)
    db.flush()
    return s


def _project(db, user_id, obj=None, name="P1", uid=None, **cols):
    p = Project(id=f"proj-{name}-{user_id}", user_id=user_id, name=name)
    for key, value in cols.items():
        setattr(p, key, value)
    if obj is not None:
        p.target_object_name = obj.object_name
        p.target_object_record_uid = obj.record_uid
    sync_project_link(db, p)
    if obj is None and uid is not None:
        p.target_object_record_uid = uid        # after sync, so "" stays ""
    db.add(p)
    db.flush()
    return p


def _framing(db, user_id, obj=None, name="M42", uid=None, **cols):
    f = SavedFraming(user_id=user_id, object_name=name, ra=83.8, dec=-5.4, rotation=0.0,
                     mosaic_cols=1, mosaic_rows=1, mosaic_overlap=10.0)
    for key, value in cols.items():
        setattr(f, key, value)
    if obj is not None:
        f.object_record_uid = obj.record_uid
    elif uid is not None:
        f.object_record_uid = uid
    db.add(f)
    db.flush()
    return f


@pytest.fixture
def mu(multi_user_client):
    client, ids = multi_user_client
    return client, ids["user_a_id"], ids["user_b_id"]


# --- page readers ------------------------------------------------------------------

def _page(client, name="M42"):
    resp = client.get(f"/graph_dashboard/{name}?format=json")
    assert resp.status_code == 200
    return resp.get_json()


def _specific_ids(client, name="M42"):
    return {s["id"] for s in _page(client, name)["sessions"]["all_specific"]}


def _group_names(client, name="M42"):
    return {g["project_name"] for g in _page(client, name)["sessions"]["grouped"]}


def _dashboard_sessions(client):
    html = client.get("/").get_data(as_text=True)
    match = re.search(r"journalSessions:\s*(\[.*?\]),\n", html, re.S)
    assert match, "journalSessions payload not found"
    return json.loads(match.group(1))


def _switcher(client):
    resp = client.get("/api/journal/objects")
    assert resp.status_code == 200
    return resp.get_json()


def _desktop_item(client, name="M42"):
    resp = client.get("/api/get_desktop_data_batch?offset=0&limit=50")
    assert resp.status_code == 200
    return next(r for r in resp.get_json()["results"] if r.get("Object") == name)


# --- helpers -----------------------------------------------------------------------

def test_object_helpers_follow_uid_and_never_another_user(mu, db_session):
    _, a_id, b_id = mu
    a_obj = _object(db_session, a_id, "M42")
    b_obj = _object(db_session, b_id, "M42")
    db_session.commit()

    assert object_for_uid(db_session, a_id, a_obj.record_uid) is a_obj
    assert object_for_uid(db_session, a_id, b_obj.record_uid) is None
    assert object_for_uid(db_session, a_id, None) is None
    assert object_for_uid(db_session, a_id, "") is None
    assert object_for_uid(db_session, a_id, "no-such-uid") is None
    assert set(objects_by_uid(db_session, a_id)) == {a_obj.record_uid}


# --- the object page follows the UID ------------------------------------------------

def test_object_page_lists_only_uid_linked_sessions(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    linked = _session(db_session, a_id, obj=obj)
    _session(db_session, a_id, obj=None, name="M42")      # same name, empty UID
    db_session.commit()

    assert _specific_ids(client) == {linked.id}


def test_object_page_groups_only_uid_linked_projects(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _project(db_session, a_id, obj=obj, name="Linked Project")
    _project(db_session, a_id, obj=None, name="Empty UID Project")
    db_session.commit()

    assert _group_names(client) == {"Linked Project"}


def test_empty_record_uid_object_attracts_nothing(mu, db_session):
    """An object whose record_uid is empty must not match any row (correction 1)."""
    client, a_id, _ = mu
    blank = _object(db_session, a_id, "M42", record_uid="")
    _session(db_session, a_id, obj=None, name="M42", object_record_uid="")
    _project(db_session, a_id, obj=None, name="P1", uid="")
    _framing(db_session, a_id, obj=None, name="M42", uid="")
    db_session.commit()

    assert object_for_uid(db_session, a_id, "") is None
    assert objects_by_uid(db_session, a_id) == {}
    assert _specific_ids(client) == set()
    assert _group_names(client) == set()
    assert _switcher(client) == []
    assert framed_object_uids(db_session, a_id) == set()


# --- key case: delete and recreate with the same name -------------------------------

def test_deleted_object_recreated_same_name_keeps_old_rows_off(mu, db_session):
    client, a_id, _ = mu
    old = _object(db_session, a_id, "M42")
    s = _session(db_session, a_id, obj=old)
    db_session.commit()
    old_uid = s.object_record_uid

    db_session.delete(old)
    db_session.commit()
    new = _object(db_session, a_id, "M42")
    db_session.commit()

    assert new.record_uid != old_uid
    assert object_for_uid(db_session, a_id, old_uid) is None
    assert _specific_ids(client) == set()                 # new M42 shows no sessions
    # ... but the old session still shows, under its stored name.
    assert any(x["id"] == s.id and x["object_name"] == "M42"
               for x in _dashboard_sessions(client))


# --- empty UID: not attached, stored name still displayed ---------------------------

def test_empty_uid_row_is_not_attached_and_name_still_displayed(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42", common_name="Orion Nebula")
    linked = _session(db_session, a_id, obj=obj)
    orphan = _session(db_session, a_id, obj=None, name="M42")
    db_session.commit()

    rows = {x["id"]: x for x in _dashboard_sessions(client)}
    assert rows[linked.id]["target_common_name"] == "Orion Nebula"   # via UID
    assert rows[orphan.id]["target_common_name"] == "M42"            # stored name
    assert _specific_ids(client) == {linked.id}


# --- switcher ------------------------------------------------------------------------

def test_journal_objects_switcher_drops_unlinked_sessions(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _session(db_session, a_id, obj=obj, calculated_integration_time_minutes=60)
    _session(db_session, a_id, obj=None, name="M42", calculated_integration_time_minutes=60)
    db_session.commit()

    entries = _switcher(client)
    assert [e["catalog_id"] for e in entries] == ["M42"]
    assert entries[0]["total_hours"] == 1.0               # only the linked session
    assert "uid" not in entries[0]                        # JSON shape unchanged


def test_switcher_lists_each_object_once(mu, db_session):
    """A project-only object and an object with both a session and a project each
    appear exactly once, and both passes use the same key."""
    client, a_id, _ = mu
    both = _object(db_session, a_id, "M42")
    _session(db_session, a_id, obj=both, calculated_integration_time_minutes=30)
    _project(db_session, a_id, obj=both, name="Both Project")
    only = _object(db_session, a_id, "NGC 7000")
    _project(db_session, a_id, obj=only, name="Only Project")
    db_session.commit()

    catalog_ids = [e["catalog_id"] for e in _switcher(client)]
    assert sorted(catalog_ids) == ["M42", "NGC 7000"]
    assert len(catalog_ids) == len(set(catalog_ids))


# --- desktop batch ---------------------------------------------------------------------

def test_desktop_batch_counts_and_framing_follow_the_uid(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _session(db_session, a_id, obj=obj)
    _session(db_session, a_id, obj=obj)
    _session(db_session, a_id, obj=None, name="M42")      # not counted
    _framing(db_session, a_id, obj=obj, rig_name="A Rig")
    db_session.commit()

    item = _desktop_item(client)
    assert item["session_count"] == 2
    assert item["framing_rig"] == "A Rig"


def test_desktop_batch_framing_rig_follows_the_rig_uid(mu, db_session):
    client, a_id, b_id = mu
    b_rig = Rig(user_id=b_id, rig_name="B Rig")
    a_rig = Rig(user_id=a_id, rig_name="A Own Rig")
    db_session.add_all([b_rig, a_rig])
    db_session.flush()
    obj = _object(db_session, a_id, "M42")
    # rig_id is the row number of B's rig; the UID names A's own rig.
    _framing(db_session, a_id, obj=obj, rig_id=b_rig.id, rig_name="Stale Text",
             rig_record_uid=a_rig.record_uid)
    db_session.commit()

    assert _desktop_item(client)["framing_rig"] == "A Own Rig"

    a_rig.rig_name = "A Renamed Rig"
    db_session.commit()
    assert _desktop_item(client)["framing_rig"] == "A Renamed Rig"


def test_desktop_batch_framing_without_rig_uid_shows_stored_text(mu, db_session):
    client, a_id, _ = mu
    a_rig = Rig(user_id=a_id, rig_name="A Own Rig")
    db_session.add(a_rig)
    db_session.flush()
    obj = _object(db_session, a_id, "M42")
    _framing(db_session, a_id, obj=obj, rig_id=a_rig.id, rig_name="Stored Text")
    db_session.commit()

    assert _desktop_item(client)["framing_rig"] == "Stored Text"


# --- has_framing follows the UID --------------------------------------------------------

def test_has_framing_follows_the_uid(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _framing(db_session, a_id, obj=obj, rig_name="A Rig")
    _framing(db_session, a_id, obj=None, name="NGC 7000", uid="dangling-uid")
    db_session.commit()

    resp = client.get("/api/mobile_up_now_inputs")
    assert resp.status_code == 200
    by_name = {o["Object"]: o for o in resp.get_json()["objects"]}
    assert by_name["M42"]["has_framing"] is True


def test_framing_with_dangling_uid_is_not_a_framing(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    _framing(db_session, a_id, obj=None, name="M42", uid="dangling-uid")
    db_session.commit()

    # The framing's UID names no object, so it attaches to none.
    assert framing_for_object(db_session, a_id, obj.record_uid) is None
    assert _desktop_item(client)["framing_rig"] == ""


# --- active_project follows the UID (project save and delete) --------------------------

def test_project_save_sets_active_project_on_the_uid_object(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42")
    project = _project(db_session, a_id, obj=None, name="P1", uid="dangling-uid")
    db_session.commit()
    pid = project.id

    resp = client.post(f"/project/{pid}", data={
        "name": "P1", "target_object_id": "M42", "status": "In Progress",
    })
    assert resp.status_code in (200, 302)
    db_session.expire_all()
    assert db_session.get(AstroObject, obj.id).active_project is True


def test_project_delete_clears_active_project_on_the_uid_object(mu, db_session):
    client, a_id, _ = mu
    obj = _object(db_session, a_id, "M42", active_project=True)
    project = _project(db_session, a_id, obj=obj, name="P1")
    db_session.commit()
    pid = project.id

    resp = client.post(f"/project/delete/{pid}")
    assert resp.status_code in (200, 302)
    db_session.expire_all()
    assert db_session.get(AstroObject, obj.id).active_project is False


# --- another user's object is never resolved -------------------------------------------

def test_foreign_object_never_used_and_other_user_unchanged(mu, db_session):
    client, a_id, b_id = mu
    a_obj = _object(db_session, a_id, "M42")
    b_obj = _object(db_session, b_id, "M42")
    s = _session(db_session, a_id, obj=None, name="M42")
    s.object_record_uid = b_obj.record_uid          # A's row points at B's object
    _project(db_session, a_id, obj=None, name="P1", uid=b_obj.record_uid)
    db_session.commit()

    assert _specific_ids(client) == set()           # B's object is not used for A
    assert a_obj.record_uid != b_obj.record_uid
    assert object_for_uid(db_session, a_id, b_obj.record_uid) is None
    db_session.expire_all()
    unchanged = db_session.get(AstroObject, b_obj.id)
    assert (unchanged.user_id, unchanged.object_name) == (b_id, "M42")


def test_framed_uids_are_scoped_to_the_user(mu, db_session):
    _, a_id, b_id = mu
    a_obj = _object(db_session, a_id, "M42")
    b_obj = _object(db_session, b_id, "M42")
    _framing(db_session, a_id, obj=a_obj)
    _framing(db_session, b_id, obj=b_obj)
    db_session.commit()

    assert framed_object_uids(db_session, a_id) == {a_obj.record_uid}
