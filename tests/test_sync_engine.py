"""Instance sync — merge engine (step 3).

Two installations in one process: a hub and a local, each with its own engine,
session and user row, so a payload really crosses a boundary instead of being
handed to the same session. The transport (step 5) is not written yet, so these
tests call export/plan/apply directly, in the order the transport will.

What each block covers is named in its section header. The names the design
fixed by name are marked: (B) transformed records travel back, (C) concurrency,
(D) the hub consumes exactly two revs, (G) project delete unlinks its sessions.
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from nova.models import (
    Base, AstroObject, Component, DbUser, HorizonPoint, JournalSession, Location,
    Project, Rig, SavedFraming, SavedView, SyncCounter, SyncState, SyncTombstone,
    UiPref, UserCustomFilter, session_projects,
)
from nova.record_links import sync_project_link
from nova.sync_engine import (
    _KEY_COLUMN,
    MASS_DELETE_RATIO, MAX_CLOCK_SKEW_SECONDS,
    KEY_FILES, OP_DELETE, OP_TOMBSTONE, OP_UPDATE, PAYLOAD_FORMAT,
    ROLE_SESSION_PHOTO,
    REFUSAL_BAD_FORMAT, REFUSAL_CLOCK_SKEW, REFUSAL_LOCAL_REPLACED,
    REFUSAL_MASS_DELETE, REFUSAL_MISSING_KEY, REFUSAL_MISSING_TIMESTAMP,
    REFUSAL_NEEDS_DOWNLOAD, REFUSAL_NOT_PAIRED, REFUSAL_PLAN_STALE,
    REFUSAL_REMOTE_REPLACED,
    REFUSAL_SERVER_UNTOUCHED, REFUSAL_SESSION_NO_KEY, REFUSAL_SYNC_RUNNING,
    REFUSAL_UNCLASSIFIED_COLUMN, REFUSAL_UNKNOWN_COLUMN, REFUSAL_UNKNOWN_TABLE,
    SyncRefused, account_marker_rev, all_classified_columns, apply_download,
    apply_merge, check_sync_preconditions, counter_rev, export_changes,
    export_snapshot, parse_datetime, plan_download, plan_merge,
    CommitReport, record_pull_applied, record_push_accepted, start_pairing,
    sync_columns,
    sync_lock,
)
from nova.sync_tracking import ACCOUNT_TABLE, mark_account, next_rev
from nova.sync_time import utc_now

HUB_URL = "https://hub.invalid"


# --- Two installations ---------------------------------------------------------

class Node:
    """One installation: its own database, session and user."""

    def __init__(self, name):
        self.name = name
        self.engine = create_engine(
            "sqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = scoped_session(
            sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        )
        self.db = self.Session()
        self.user = DbUser(username=name)
        self.db.add(self.user)
        self.db.commit()

    @property
    def uid(self):
        return self.user.id

    def commit(self):
        self.db.commit()
        self.db.expire_all()

    def state(self):
        self.db.expire_all()
        return self.db.query(SyncState).filter_by(user_id=self.uid).one_or_none()

    def rev(self):
        return counter_rev(self.db, self.uid)

    def close(self):
        try:
            self.db.rollback()
        except Exception:
            pass
        self.Session.remove()
        self.engine.dispose()

    # --- row readers (expire first: an apply writes through Core) ---

    def rows(self, model):
        self.db.expire_all()
        return self.db.query(model).filter_by(user_id=self.uid).all()

    def one(self, model, **match):
        self.db.expire_all()
        return self.db.query(model).filter_by(user_id=self.uid, **match).one_or_none()

    def tombstones(self, table=None):
        self.db.expire_all()
        query = self.db.query(SyncTombstone).filter_by(user_id=self.uid)
        if table is not None:
            query = query.filter_by(table_name=table)
        return sorted((t.record_key, t.sync_rev) for t in query.all())


@pytest.fixture
def hub():
    node = Node("hub")
    yield node
    node.close()


@pytest.fixture
def bare():
    """An installation with no hub pairing at all."""
    node = Node("local")
    yield node
    node.close()


@pytest.fixture
def local(bare):
    """A local paired to the hub and past its download lock, as it is after a
    first Download from server. Every transport test starts from here; the
    refusal tests that need an unpaired or freshly paired install use `bare`."""
    start_pairing(bare.db, bare.user, server_url=HUB_URL, account="hub")
    bare.commit()
    bare.db.query(SyncState).filter_by(user_id=bare.uid).update(
        {"last_sync_at": utc_now()})
    bare.commit()
    return bare


@pytest.fixture
def paired(local):
    """Alias kept for the tests written against the older fixture name."""
    return local


# --- Transport stand-ins -------------------------------------------------------

def push(local, hub, *, allow_mass_delete=False):
    """Sync now, local -> hub. Returns the hub's MergeResult."""
    rev_at_export = local.rev()
    payload = export_changes(local.db, local.user,
                            local.state().local_pushed_rev or 0, side="local")
    plan = plan_merge(hub.db, hub.user, payload, side="server",
                      allow_mass_delete=allow_mass_delete)
    if plan.refused:
        raise SyncRefused(plan.refusals[0])
    result = apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    # The hub reports what it deferred, and the watermark does not move past it.
    # An empty report stands in for the rest of the push response: a plan with
    # no placements has no pending files to report.
    record_push_accepted(local.db, local.user, rev_at_export,
                         deferred=result.deferred, commit=CommitReport())
    local.commit()
    return result


def pull(local, hub, *, allow_mass_delete=False, exclude_revs=()):
    """Sync now, hub -> local. Returns the local's MergeResult."""
    rev_at_export = local.rev()
    cursor = local.state().server_cursor or 0
    payload = export_changes(hub.db, hub.user, cursor, side="server",
                             exclude_revs=exclude_revs)
    server_rev = payload["rev"]
    plan = plan_merge(local.db, local.user, payload, side="local",
                      allow_mass_delete=allow_mass_delete)
    if plan.refused:
        raise SyncRefused(plan.refusals[0])
    result = apply_merge(local.db, plan, user=local.user)
    record_pull_applied(local.db, local.user, result, server_rev, rev_at_export)
    local.commit()
    return result


def download(local, hub):
    """Download from server. Returns the local's MergeResult."""
    snapshot = export_snapshot(hub.db, hub.user, side="server")
    plan = plan_download(local.db, local.user, snapshot)
    if plan.refused:
        raise SyncRefused(plan.refusals[0])
    result = apply_download(local.db, plan, user=local.user)
    local.commit()
    return result


# --- Builders ------------------------------------------------------------------

def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")


def stamp(db, model, row_id, moment):
    """Set a row's sync_changed_at behind the listener's back."""
    db.execute(text(f"UPDATE {model.__tablename__} "
                    f"SET sync_changed_at = :m WHERE id = :i;"),
               {"m": moment.strftime("%Y-%m-%d %H:%M:%S.%f"), "i": row_id})
    db.commit()
    db.expire_all()


def make_payload(records=None, tombstones=None, rev=0, fmt=PAYLOAD_FORMAT):
    return {
        "format": fmt, "account": "hub", "instance": "test-instance",
        "side": "local", "rev": rev, "snapshot": False,
        "records": records or {}, "tombstones": tombstones or [],
    }


def obj_rec(uid, name, at, **extra):
    record = {"record_uid": uid, "object_name": name, "ra_hours": 1.0,
              "dec_deg": 2.0, "sync_changed_at": iso(at)}
    record.update(extra)
    return record


def loc_rec(uid, name, at, **extra):
    record = {"record_uid": uid, "name": name, "lat": 48.0, "lon": 16.0,
              "timezone": "UTC", "sync_changed_at": iso(at), "horizon_mask": []}
    record.update(extra)
    return record


def sess_rec(external_id, at, **extra):
    record = {"external_id": external_id, "date_utc": "2026-01-02",
              "object_name": "M 31", "sync_changed_at": iso(at)}
    record.update(extra)
    return record


def tomb(table, key, at):
    return {"table": table, "key": key, "deleted_at": iso(at)}


def add_object(db, uid, name, ra=1.0, dec=2.0, **fields):
    db.add(AstroObject(user_id=uid, object_name=name, ra_hours=ra, dec_deg=dec,
                       **fields))
    db.commit()
    return db.query(AstroObject).filter_by(user_id=uid, object_name=name).one()


def add_location(db, uid, name):
    db.add(Location(user_id=uid, name=name, lat=48.0, lon=16.0, timezone="UTC"))
    db.commit()
    return db.query(Location).filter_by(user_id=uid, name=name).one()


def add_session(db, uid, external_id, **fields):
    db.add(JournalSession(user_id=uid, external_id=external_id,
                          date_utc=datetime(2026, 1, 2).date(), **fields))
    db.commit()
    return db.query(JournalSession).filter_by(user_id=uid,
                                              external_id=external_id).one()


def add_rig(db, uid, name):
    db.add(Rig(user_id=uid, rig_name=name, active=True))
    db.commit()
    return db.query(Rig).filter_by(user_id=uid, rig_name=name).one()


def add_project(db, uid, project_id, name):
    db.add(Project(id=project_id, user_id=uid, name=name, status="In Progress"))
    db.commit()
    return db.query(Project).filter_by(user_id=uid, id=project_id).one()


def add_view(db, uid, name):
    db.add(SavedView(user_id=uid, name=name, settings_json="{}"))
    db.commit()
    return db.query(SavedView).filter_by(user_id=uid, name=name).one()


def add_framing(db, uid, object_name, **fields):
    db.add(SavedFraming(user_id=uid, object_name=object_name, **fields))
    db.commit()
    return db.query(SavedFraming).filter_by(user_id=uid,
                                            object_name=object_name).one()


def add_filter(db, uid, key, label=None):
    db.add(UserCustomFilter(user_id=uid, filter_key=key,
                            filter_label=label or key.upper()))
    db.commit()
    return db.query(UserCustomFilter).filter_by(user_id=uid, filter_key=key).one()


def set_prefs(db, uid, settings):
    prefs = db.query(UiPref).filter_by(user_id=uid).one_or_none()
    if prefs is None:
        prefs = UiPref(user_id=uid)
        db.add(prefs)
    prefs.json_blob = json.dumps(settings)
    db.commit()
    return prefs


# =============================================================================
# 1. Column classification and payload shape
# =============================================================================

def test_every_column_is_classified():
    """Every column of every synced model is in exactly one bucket."""
    for model in (Location, AstroObject, Component, Rig, JournalSession, Project,
                  SavedView, SavedFraming, UserCustomFilter, UiPref):
        declared = {column.name for column in model.__table__.columns}
        listed = all_classified_columns(model)
        assert declared == listed, (
            f"{model.__name__}: missing {sorted(declared - listed)}, "
            f"unknown {sorted(listed - declared)}"
        )


def test_local_and_file_columns_never_travel():
    for model in (Location, AstroObject, Component, Rig, JournalSession, Project,
                  SavedView, SavedFraming, UserCustomFilter, UiPref):
        travelled = set(sync_columns(model))
        key = _KEY_COLUMN.get(model)
        for column in ("id", "user_id", "stable_uid", "sync_rev", "sync_changed_at"):
            if column == key:
                continue  # the natural key is the identity and must travel
            assert column not in travelled, f"{model.__name__}.{column} travels"
    assert "session_image_file" not in sync_columns(JournalSession)
    assert "final_image_file" not in sync_columns(Project)
    assert "calculated_integration_time_minutes" not in sync_columns(JournalSession)


def test_export_snapshot_shape(hub):
    add_object(hub.db, hub.uid, "M 31")
    payload = export_snapshot(hub.db, hub.user, side="server")
    assert payload["format"] == PAYLOAD_FORMAT
    assert payload["snapshot"] is True
    assert payload["server_rev"] == payload["rev"] == hub.rev()
    record = payload["records"]["astro_objects"][0]
    assert record["object_name"] == "M 31"
    assert parse_datetime(record["sync_changed_at"]) is not None
    assert "id" not in record and "user_id" not in record


def test_location_horizon_travels_inside_the_record(hub):
    location = add_location(hub.db, hub.uid, "Home")
    hub.db.add(HorizonPoint(location_id=location.id, az_deg=90.0, alt_min_deg=15.0))
    hub.db.add(HorizonPoint(location_id=location.id, az_deg=0.0, alt_min_deg=20.0))
    hub.commit()
    record = export_snapshot(hub.db, hub.user)["records"]["locations"][0]
    assert record["horizon_mask"] == [[0.0, 20.0], [90.0, 15.0]]


def test_session_project_ids_travel_in_the_record(hub):
    project = add_project(hub.db, hub.uid, "p1", "Veil")
    session = add_session(hub.db, hub.uid, "s1")
    hub.db.execute(session_projects.insert().values(session_id=session.id,
                                                project_id=project.id))
    hub.commit()
    record = export_snapshot(hub.db, hub.user)["records"]["journal_sessions"][0]
    assert record["project_ids"] == ["p1"]


def test_export_changes_only_sends_rows_above_the_cursor(hub):
    add_object(hub.db, hub.uid, "M 31")
    cursor = hub.rev()
    add_object(hub.db, hub.uid, "M 42")
    payload = export_changes(hub.db, hub.user, cursor)
    names = [r["object_name"] for r in payload["records"]["astro_objects"]]
    assert names == ["M 42"]


def test_bad_format_is_refused(hub):
    plan = plan_merge(hub.db, hub.user, make_payload(fmt=99), side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_BAD_FORMAT]


def test_unknown_table_and_column_are_refused(hub):
    plan = plan_merge(hub.db, hub.user,
                      make_payload({"nope": [{"a": 1}]}), side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_UNKNOWN_TABLE]

    plan = plan_merge(hub.db, hub.user,
                      make_payload({"astro_objects": [{"nonsense": 1}]}),
                      side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_UNKNOWN_COLUMN]


def test_a_local_column_in_a_payload_is_refused(hub):
    """A peer that sends user_id is sending something the engine never writes."""
    plan = plan_merge(hub.db, hub.user,
                      make_payload({"astro_objects": [obj_rec("u1", "M 31", utc_now(),
                                                              user_id=7)]}),
                      side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_UNCLASSIFIED_COLUMN]


def test_a_payload_with_no_timestamp_is_refused(hub):
    plan = plan_merge(hub.db, hub.user,
                      make_payload({"astro_objects": [
                          {"record_uid": "u1", "object_name": "M 31"}]}),
                      side="server")
    assert [r.code for r in plan.refusals] == ["missing_timestamp"]


def test_a_session_without_an_external_id_is_refused(hub):
    plan = plan_merge(hub.db, hub.user,
                      make_payload({"journal_sessions": [
                          {"date_utc": "2026-01-02",
                           "sync_changed_at": iso(utc_now())}]}),
                      side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_SESSION_NO_KEY]


def test_an_object_without_a_record_uid_is_refused(hub):
    plan = plan_merge(hub.db, hub.user,
                      make_payload({"astro_objects": [
                          {"object_name": "M 31", "ra_hours": 1.0, "dec_deg": 2.0,
                           "sync_changed_at": iso(utc_now())}]}),
                      side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_MISSING_KEY]


def test_a_refused_plan_never_writes(hub):
    before = hub.rev()
    plan = plan_merge(hub.db, hub.user, make_payload(fmt=0), side="server")
    assert plan.refused
    with pytest.raises(SyncRefused):
        apply_merge(hub.db, plan, user=hub.user)
    assert hub.rows(AstroObject) == []
    assert hub.rev() == before


# =============================================================================
# 2. Records crossing: insert, update, delete
# =============================================================================

def test_a_record_made_on_the_local_reaches_the_hub(local, hub):
    add_object(local.db, local.uid, "M 31", ra=5.0, dec=-10.0)
    push(local, hub)
    arrived = hub.one(AstroObject, object_name="M 31")
    assert arrived is not None and arrived.ra_hours == 5.0
    assert arrived.user_id == hub.uid


def test_a_record_made_on_the_hub_reaches_the_local(local, hub):
    add_object(hub.db, hub.uid, "M 42")
    pull(local, hub)
    assert local.one(AstroObject, object_name="M 42") is not None


def test_the_newer_side_wins_an_update(local, hub):
    add_object(local.db, local.uid, "M 31", ra=1.0, dec=2.0)
    push(local, hub)
    pull(local, hub)

    # Both sides hold the same object now. The hub edits it later.
    hub_obj = hub.one(AstroObject, object_name="M 31")
    hub_obj.ra_hours = 9.0
    hub.commit()
    pull(local, hub)
    assert local.one(AstroObject, object_name="M 31").ra_hours == 9.0


def test_the_newer_local_edit_reaches_the_hub(local, hub):
    add_object(local.db, local.uid, "M 31")
    push(local, hub)
    pull(local, hub)

    local_obj = local.one(AstroObject, object_name="M 31")
    local_obj.dec_deg = -33.5
    local.commit()
    push(local, hub)
    assert hub.one(AstroObject, object_name="M 31").dec_deg == -33.5


def test_equal_timestamps_go_to_the_hub_on_the_hub(local, hub):
    """On the hub the incoming copy loses a tie."""
    moment = utc_now()
    add_object(hub.db, hub.uid, "M 31", ra=1.0, dec=2.0)
    hub_obj = hub.one(AstroObject, object_name="M 31")
    stamp(hub.db, AstroObject, hub_obj.id, moment)

    plan = plan_merge(hub.db, hub.user,
                      make_payload({"astro_objects": [
                          obj_rec(hub_obj.record_uid, "M 31", moment, ra_hours=99.0)]}),
                      side="server")
    apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    assert hub.one(AstroObject, object_name="M 31").ra_hours == 1.0


def test_equal_timestamps_go_to_the_incoming_copy_on_a_local(local, hub):
    """On a local the incoming copy wins a tie: the hub is the tie-breaker."""
    moment = utc_now()
    add_object(local.db, local.uid, "M 31", ra=1.0, dec=2.0)
    local_obj = local.one(AstroObject, object_name="M 31")
    stamp(local.db, AstroObject, local_obj.id, moment)

    plan = plan_merge(local.db, local.user,
                      make_payload({"astro_objects": [
                          obj_rec(local_obj.record_uid, "M 31", moment, ra_hours=99.0)]}),
                      side="local")
    apply_merge(local.db, plan, user=local.user)
    local.commit()
    assert local.one(AstroObject, object_name="M 31").ra_hours == 99.0


def test_a_delete_on_the_hub_reaches_the_local(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    pull(local, hub)
    assert local.rows(AstroObject)

    hub.db.delete(hub.one(AstroObject, object_name="M 31"))
    hub.commit()
    pull(local, hub)
    assert local.rows(AstroObject) == []


def test_a_delete_on_the_local_reaches_the_hub(local, hub):
    add_object(local.db, local.uid, "M 31")
    push(local, hub)
    pull(local, hub)

    local.db.delete(local.one(AstroObject, object_name="M 31"))
    local.commit()
    push(local, hub)
    assert hub.rows(AstroObject) == []


def test_an_edit_beats_a_delete(local, hub):
    """The local edits a record after the hub deleted it: the edit survives."""
    add_object(local.db, local.uid, "M 31", ra=1.0, dec=2.0)
    push(local, hub)
    pull(local, hub)

    hub_obj = hub.one(AstroObject, object_name="M 31")
    hub.db.delete(hub_obj)
    hub.commit()

    local_obj = local.one(AstroObject, object_name="M 31")
    local_obj.ra_hours = 7.0
    local.commit()

    pull(local, hub)
    survived = local.one(AstroObject, object_name="M 31")
    assert survived is not None and survived.ra_hours == 7.0

    # The hub learns the record is still alive on the next push.
    push(local, hub)
    assert hub.one(AstroObject, object_name="M 31") is not None


def test_applying_the_same_payload_twice_changes_nothing(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    payload = export_changes(hub.db, hub.user, 0, side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")
    apply_merge(local.db, plan, user=local.user)
    local.commit()
    rev_after_first = local.rev()

    again = plan_merge(local.db, local.user, payload, side="local")
    assert again.actions == []
    apply_merge(local.db, again, user=local.user)
    local.commit()
    assert local.rev() == rev_after_first
    assert len(local.rows(AstroObject)) == 1


def test_a_re_delivered_row_is_not_re_stamped(local, hub):
    """(M4) A second apply of the same payload changes nothing at all."""
    add_object(hub.db, hub.uid, "M 31")
    payload = export_snapshot(hub.db, hub.user, side="server")

    first = apply_merge(local.db,
                        plan_merge(local.db, local.user, payload, side="local"),
                        user=local.user)
    local.commit()
    landed = local.one(AstroObject, object_name="M 31")
    before = (landed.sync_changed_at, landed.sync_rev, local.rev())

    again = plan_merge(local.db, local.user, payload, side="local")
    assert again.actions == []
    second = apply_merge(local.db, again, user=local.user)
    local.commit()

    after = local.one(AstroObject, object_name="M 31")
    assert (after.sync_changed_at, after.sync_rev) == before[:2]
    assert local.rev() == before[2]
    assert second.r_verbatim is None and second.r_changed is None
    assert second.push_revs == []


def test_a_pushed_record_keeps_the_senders_timestamp(local, hub):
    """(D1) A verbatim insert stores the author's sync_changed_at."""
    add_object(local.db, local.uid, "M 31")
    moment = utc_now() - timedelta(days=2)
    stamp(local.db, AstroObject, local.one(AstroObject, object_name="M 31").id,
          moment)

    push(local, hub)
    assert hub.one(AstroObject, object_name="M 31").sync_changed_at == moment


def test_a_pushed_edit_keeps_the_senders_timestamp(local, hub):
    """(D1) ... and so does a verbatim update. The hub must not keep its own
    older stamp, or a third copy would rate its own row as the newer one."""
    add_object(local.db, local.uid, "M 31")
    push(local, hub)
    pull(local, hub)

    local_obj = local.one(AstroObject, object_name="M 31")
    local_obj.ra_hours = 6.0
    local.commit()
    moment = utc_now() + timedelta(days=1)
    stamp(local.db, AstroObject, local_obj.id, moment)

    push(local, hub)
    updated = hub.one(AstroObject, object_name="M 31")
    assert updated.ra_hours == 6.0
    assert updated.sync_changed_at == moment


def test_an_edit_through_the_hub_reaches_a_third_copy(hub):
    """(D1) end to end: hub creates, two locals pull, the second edits and
    pushes, the first pulls. The edit must land on the first copy."""
    early = Node("early")
    late = Node("late")
    try:
        for node in (early, late):
            start_pairing(node.db, node.user, server_url=HUB_URL)
            node.commit()
            node.db.query(SyncState).filter_by(user_id=node.uid).update(
                {"last_sync_at": utc_now()})
            node.commit()

        add_object(hub.db, hub.uid, "M 31", ra=1.0)
        pull(early, hub)
        pull(late, hub)

        edited = late.one(AstroObject, object_name="M 31")
        edited.ra_hours = 6.0
        late.commit()
        push(late, hub)
        assert hub.one(AstroObject, object_name="M 31").ra_hours == 6.0

        pull(early, hub)
        assert early.one(AstroObject, object_name="M 31").ra_hours == 6.0
    finally:
        late.close()
        early.close()


def test_a_newer_hub_edit_beats_a_local_delete(local, hub):
    """(Rule 5, the other order) The local deletes, the hub edits afterwards."""
    add_object(local.db, local.uid, "M 31", ra=1.0)
    push(local, hub)
    pull(local, hub)

    local.db.delete(local.one(AstroObject, object_name="M 31"))
    local.commit()
    hub_obj = hub.one(AstroObject, object_name="M 31")
    hub_obj.ra_hours = 8.0
    hub.commit()

    result = push(local, hub)
    survived = hub.one(AstroObject, object_name="M 31")
    assert survived is not None and survived.ra_hours == 8.0
    assert [r.reason for r in result.resurrected] == ["local_edit_newer"]

    pull(local, hub)
    back = local.one(AstroObject, object_name="M 31")
    assert back is not None and back.ra_hours == 8.0
    assert local.tombstones("astro_objects") == []


def test_a_pull_that_re_creates_a_row_drops_its_tombstone(local, hub):
    """(Rule 14) The apply's own insert path clears the tombstone under the key."""
    obj = add_object(hub.db, hub.uid, "M 31", ra=1.0)
    uid = obj.record_uid
    pull(local, hub)

    hub.db.delete(hub.one(AstroObject, object_name="M 31"))
    hub.commit()
    pull(local, hub)
    assert local.rows(AstroObject) == []
    assert [key for key, _ in local.tombstones("astro_objects")] == [uid]

    # The hub puts the same record back under the key it had before.
    hub.db.add(AstroObject(user_id=hub.uid, object_name="M 31", ra_hours=3.0,
                           dec_deg=2.0, record_uid=uid))
    hub.commit()
    pull(local, hub)

    assert local.tombstones("astro_objects") == []
    assert local.one(AstroObject, object_name="M 31").ra_hours == 3.0


def test_identical_content_is_a_no_op_however_the_stamp_moved(hub):
    """(Correction 2) The offset is re-measured every sync, so a record can come
    back with a stamp a second off. Equal content writes nothing."""
    obj = add_object(hub.db, hub.uid, "M 31", ra=1.0, dec=2.0)
    before = hub.rev()

    payload = make_payload({"astro_objects": [
        obj_rec(obj.record_uid, "M 31", utc_now() + timedelta(seconds=1.5))]})
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    assert plan.actions == []

    result = apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    assert result.r_verbatim is None and result.r_changed is None
    assert result.push_revs == []
    assert hub.rev() == before
    assert hub.one(AstroObject, object_name="M 31").ra_hours == 1.0


def test_a_collision_of_identical_content_does_not_re_stamp_the_row(local, hub):
    """(Correction 2) The same rule where the receiver has to decide: the
    retired UID is forgotten, but a row that already matches is left alone."""
    hub_obj = add_object(hub.db, hub.uid, "Veil", ra=1.0, dec=2.0)
    stamp_before = hub_obj.sync_changed_at

    payload = make_payload({"astro_objects": [
        obj_rec("other-uid", "Veil", utc_now() + timedelta(seconds=1.5))]})
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    assert [(a.op, a.table) for a in plan.actions] == [
        (OP_TOMBSTONE, "astro_objects")]

    apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    survivor = hub.one(AstroObject, object_name="Veil")
    assert survivor.record_uid == hub_obj.record_uid
    assert survivor.sync_changed_at == stamp_before


def test_a_deleted_record_can_come_back(local, hub):
    """Delete on the hub, then re-create the same name: both sides converge."""
    add_object(hub.db, hub.uid, "M 31")
    pull(local, hub)
    hub.db.delete(hub.one(AstroObject, object_name="M 31"))
    hub.commit()
    pull(local, hub)
    assert local.rows(AstroObject) == []

    add_object(hub.db, hub.uid, "M 31", ra=3.0)
    pull(local, hub)
    assert local.one(AstroObject, object_name="M 31").ra_hours == 3.0


# =============================================================================
# 3. No echoes, and the two-rev scheme
# =============================================================================

def test_a_push_then_a_pull_does_not_echo(local, hub):
    add_object(local.db, local.uid, "M 31")
    push(local, hub)
    result = pull(local, hub)
    assert result.created == {}
    assert result.updated == {}


def test_the_hub_never_gets_back_what_it_sent(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    pull(local, hub)
    assert push(local, hub).created == {}


def test_a_verbatim_only_apply_uses_one_rev(local, hub):
    """(D) The hub consumes R_verbatim and nothing else."""
    add_object(local.db, local.uid, "M 31")
    rev_at_export = local.rev()
    payload = export_changes(local.db, local.user, 0, side="local")
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    assert plan.has_verbatim and not plan.has_changed

    before = hub.rev()
    result = apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    assert result.r_changed is None
    assert result.r_verbatim == before + 1
    assert hub.rev() == before + 1


def test_a_transformed_record_travels_back_to_its_author(local, hub):
    """(B) What the hub decided about the local's record reaches the local."""
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    add_object(local.db, local.uid, "Veil")          # same name, another UID

    push(local, hub)
    assert len(local.rows(AstroObject)) == 1         # the local still has one

    # The pull brings the hub's identity back, and it is what the local ends up
    # holding. Without the second rev this record would never leave the hub.
    result = pull(local, hub)
    assert result.r_changed is not None
    assert local.one(AstroObject, object_name="Veil").record_uid == \
        hub_obj.record_uid


def test_changed_writes_sit_above_verbatim_writes(local, hub):
    add_object(hub.db, hub.uid, "Veil")
    add_object(local.db, local.uid, "Veil")     # unified: a changed write
    add_object(local.db, local.uid, "M 42")     # stored as received: verbatim
    payload = export_changes(local.db, local.user, 0, side="local")
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    assert plan.has_changed                       # the unification is a decision

    result = apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    assert result.r_verbatim < result.r_changed


def test_the_local_keeps_the_hubs_uid_after_a_unification(local, hub):
    add_object(hub.db, hub.uid, "Veil")
    hub_uid = hub.one(AstroObject, object_name="Veil").record_uid
    add_object(local.db, local.uid, "Veil")
    local_uid = local.one(AstroObject, object_name="Veil").record_uid
    assert hub_uid != local_uid

    push(local, hub)
    pull(local, hub)
    assert local.one(AstroObject, object_name="Veil").record_uid == hub_uid
    assert hub.one(AstroObject, object_name="Veil").record_uid == hub_uid


def test_the_local_retires_the_uid_it_gave_up(local, hub):
    add_object(hub.db, hub.uid, "Veil")
    add_object(local.db, local.uid, "Veil")
    local_uid = local.one(AstroObject, object_name="Veil").record_uid

    push(local, hub)
    pull(local, hub)
    keys = [key for key, _ in local.tombstones("astro_objects")]
    assert local_uid in keys


def test_an_imported_tombstone_is_stamped_with_a_local_rev(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    pull(local, hub)
    hub.db.delete(hub.one(AstroObject, object_name="M 31"))
    hub.commit()
    pull(local, hub)

    keys = [key for key, _ in local.tombstones("astro_objects")]
    assert keys, "the delete should have left a tombstone"
    assert push(local, hub).created == {}


def test_the_local_does_not_push_an_account_marker(local, hub):
    mark_account(local.db.connection(), local.uid, "seeded")
    local.commit()
    payload = export_changes(local.db, local.user, 0, side="local")
    assert all(t["table"] != ACCOUNT_TABLE for t in payload["tombstones"])


def test_the_hub_does_show_its_account_marker(local, hub):
    mark_account(hub.db.connection(), hub.uid, "seeded")
    hub.commit()
    payload = export_changes(hub.db, hub.user, 0, side="server")
    assert any(t["table"] == ACCOUNT_TABLE for t in payload["tombstones"])


def test_a_pusher_can_exclude_what_the_hub_stored_verbatim(local, hub):
    """(Rule 2) A hub hands back the revs it wrote verbatim, and the pull that
    follows leaves them out: a side never receives what it just sent."""
    add_object(local.db, local.uid, "M 42")
    result = push(local, hub)

    assert result.r_verbatim is not None
    assert result.push_revs == [result.r_verbatim]

    cursor = local.state().server_cursor or 0
    echoed = export_changes(hub.db, hub.user, cursor, side="server",
                            exclude_revs=result.push_revs)
    assert echoed["records"] == {}

    everything = export_changes(hub.db, hub.user, cursor, side="server")
    assert [r["object_name"] for r in everything["records"]["astro_objects"]] == [
        "M 42"]


def test_a_second_local_still_receives_a_pushed_record(local, hub):
    """(Rule 2) The exclusion is the pusher's, not the hub's."""
    other = Node("other")
    try:
        start_pairing(other.db, other.user, server_url=HUB_URL)
        other.commit()
        other.db.query(SyncState).filter_by(user_id=other.uid).update(
            {"last_sync_at": utc_now()})
        other.commit()

        add_object(local.db, local.uid, "M 42")
        push(local, hub)
        pull(other, hub)
        assert other.one(AstroObject, object_name="M 42") is not None
    finally:
        other.close()


def test_a_record_the_hub_decides_about_reaches_the_pusher(local, hub):
    """(Rule 2) Excluding the verbatim rev must not exclude the rev the hub
    decided on: that is how the pusher learns its record was unified."""
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    add_object(local.db, local.uid, "Veil")     # the hub unifies this one
    add_object(local.db, local.uid, "M 42")     # ... and stores this verbatim

    result = push(local, hub)
    assert result.r_verbatim is not None
    assert result.push_revs == [result.r_verbatim]

    pull(local, hub, exclude_revs=result.push_revs)
    assert local.one(AstroObject, object_name="Veil").record_uid == \
        hub_obj.record_uid


# =============================================================================
# 4. Natural keys and collisions
# =============================================================================

def test_a_location_name_collision_renames_the_incoming_record(local, hub):
    add_location(hub.db, hub.uid, "Home")
    add_location(local.db, local.uid, "Home")
    plan = plan_merge(local.db, local.user,
                      export_snapshot(hub.db, hub.user, side="server"),
                      side="local")
    assert [(r.field, r.old, r.new) for r in plan.renames] == [
        ("name", "Home", "Home (2)")]
    apply_merge(local.db, plan, user=local.user)
    local.commit()
    assert {loc.name for loc in local.rows(Location)} == {"Home", "Home (2)"}


def test_a_rig_name_collision_renames_the_incoming_record(local, hub):
    add_rig(hub.db, hub.uid, "Rig A")
    add_rig(local.db, local.uid, "Rig A")
    plan = plan_merge(local.db, local.user,
                      export_snapshot(hub.db, hub.user, side="server"),
                      side="local")
    assert [r.new for r in plan.renames] == ["Rig A (2)"]


def test_a_project_name_collision_renames_the_incoming_record(local, hub):
    add_project(hub.db, hub.uid, "hub-p", "Veil")
    add_project(local.db, local.uid, "local-p", "Veil")
    plan = plan_merge(local.db, local.user,
                      export_snapshot(hub.db, hub.user, side="server"),
                      side="local")
    assert [r.new for r in plan.renames] == ["Veil (2)"]


def test_a_saved_view_of_the_same_name_is_the_same_view(local, hub):
    """A view is keyed by its name, so the same name is the same view.

    Unlike a location (keyed by a UID, with its name only a unique value), there
    is no second chance here: the two are one record and the newer content wins.
    Nothing is renamed, because renaming would mean there were two views.
    """
    hub_view = add_view(hub.db, hub.uid, "Wide")
    hub_view.settings_json = '{"fov": 2.0}'
    hub.commit()
    add_view(local.db, local.uid, "Wide")
    stamp(hub.db, SavedView, hub_view.id, utc_now() + timedelta(seconds=5))

    plan = plan_merge(local.db, local.user,
                      export_snapshot(hub.db, hub.user, side="server"),
                      side="local")
    assert plan.renames == []
    assert plan.created == {}
    assert plan.updated == {"saved_views": 1}
    assert plan.actions[0].values["settings_json"] == '{"fov": 2.0}'


def test_a_custom_filter_key_is_matched_by_identity(local, hub):
    """A filter's key is its natural key, so the same key is the same filter."""
    add_filter(hub.db, hub.uid, "OIII", "OIII (hub)")
    add_filter(local.db, local.uid, "OIII", "OIII (local)")
    plan = plan_merge(local.db, local.user,
                      export_snapshot(hub.db, hub.user, side="server"),
                      side="local")
    assert plan.renames == []
    assert plan.created == {}
    assert len(local.rows(UserCustomFilter)) == 1


def test_an_object_unification_does_not_touch_notes(local, hub):
    add_object(hub.db, hub.uid, "Veil", description_text="hub notes")
    add_object(local.db, local.uid, "Veil", description_text="local notes")
    # A delta, not a snapshot: a hub refuses a snapshot in a push (Rule 3).
    plan = plan_merge(hub.db, hub.user,
                      export_changes(local.db, local.user, 0, side="local"),
                      side="server")
    assert len(plan.unified) == 1
    apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    # The local's copy was made later, so it is newer and replaces the text.
    # It is never concatenated onto the hub's own notes.
    text = hub.one(AstroObject, object_name="Veil").description_text
    assert text == "local notes"
    assert "hub notes" not in text


def test_a_unification_moves_the_local_sessions_with_it(local, hub):
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    local_obj = add_object(local.db, local.uid, "Veil")
    add_session(local.db, local.uid, "s1", object_record_uid=local_obj.record_uid,
                object_name="Veil")

    push(local, hub)
    pull(local, hub)
    session = local.one(JournalSession, external_id="s1")
    assert session.object_record_uid == hub_obj.record_uid
    assert session.object_name == "Veil"


def test_a_unification_keeps_the_local_framing(local, hub):
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    add_framing(hub.db, hub.uid, "Veil", object_record_uid=hub_obj.record_uid,
                ra=10.0)
    local_obj = add_object(local.db, local.uid, "Veil")
    add_framing(local.db, local.uid, "Veil", object_record_uid=local_obj.record_uid,
                ra=20.0)

    push(local, hub)
    pull(local, hub)
    framings = local.rows(SavedFraming)
    assert len(framings) == 1
    assert framings[0].object_record_uid == hub_obj.record_uid


def test_a_renamed_object_repairs_the_sessions_mirror(local, hub):
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    add_session(hub.db, hub.uid, "s1", object_record_uid=hub_obj.record_uid,
                object_name="Veil")
    pull(local, hub)

    # Only the object is renamed, so the session is not in the delta and the
    # engine has to repair its mirror itself.
    hub_obj = hub.one(AstroObject, object_name="Veil")
    hub_obj.object_name = "Veil Nebula"
    hub.commit()

    pull(local, hub)
    assert local.one(AstroObject, object_name="Veil Nebula") is not None
    assert local.one(JournalSession, external_id="s1").object_name == "Veil Nebula"


def test_a_renamed_location_repairs_the_sessions_mirror(local, hub):
    add_location(hub.db, hub.uid, "Home")
    add_session(hub.db, hub.uid, "s1", location_name="Home")
    pull(local, hub)

    location = hub.one(Location, name="Home")
    location.name = "Backyard"
    hub.commit()
    session = hub.one(JournalSession, external_id="s1")
    session.location_name = "Backyard"
    hub.commit()

    pull(local, hub)
    assert local.one(JournalSession, external_id="s1").location_name == "Backyard"


def test_a_renamed_location_repairs_the_ui_prefs_default(local, hub):
    add_location(hub.db, hub.uid, "Home")
    set_prefs(hub.db, hub.uid, {"default_location": "Home", "theme": "dark"})
    pull(local, hub)

    # Only the location is renamed; the local's own ui_prefs still says "Home".
    location = hub.one(Location, name="Home")
    location.name = "Backyard"
    hub.commit()
    pull(local, hub)

    prefs = local.db.query(UiPref).filter_by(user_id=local.uid).one()
    settings = json.loads(prefs.json_blob)
    assert settings["default_location"] == "Backyard"
    assert settings["theme"] == "dark"        # the merge kept the other key


def test_ui_prefs_blobs_merge_instead_of_replacing(local, hub):
    set_prefs(hub.db, hub.uid, {"theme": "dark"})
    set_prefs(local.db, local.uid, {"rig_sort": "name-desc"})
    pull(local, hub)
    merged = json.loads(local.db.query(UiPref).filter_by(
        user_id=local.uid).one().json_blob)
    assert merged["theme"] == "dark"
    assert merged["rig_sort"] == "name-desc"


def test_components_of_the_same_name_are_never_renamed(local, hub):
    """(Rule 10) A component has no unique value to collide on: two cameras may
    share a name, and neither is renamed to make room for the other."""
    for _ in range(2):
        hub.db.add(Component(user_id=hub.uid, kind="camera", name="Cam",
                             sensor_width_mm=20.0, sensor_height_mm=15.0,
                             pixel_size_um=3.0))
    local.db.add(Component(user_id=local.uid, kind="camera", name="Cam",
                           sensor_width_mm=20.0, sensor_height_mm=15.0,
                           pixel_size_um=3.0))
    hub.commit()
    local.commit()

    plan = plan_merge(local.db, local.user,
                      export_snapshot(hub.db, hub.user, side="server"),
                      side="local")
    assert plan.renames == []
    assert plan.conflicts == []
    assert plan.created == {"components": 2}

    apply_merge(local.db, plan, user=local.user)
    local.commit()
    assert [c.name for c in local.rows(Component)] == ["Cam"] * 3


def test_a_deferred_framing_is_re_offered_until_it_lands(local, hub):
    """(V2) A record the receiver cannot place yet must not be skipped by the
    cursor, or the next pull steps past it and it never arrives."""
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    add_framing(hub.db, hub.uid, "Veil", object_record_uid=hub_obj.record_uid,
                ra=10.0)
    # The local holds a framing of the same name pointing at an object of its
    # own: until the two identities converge the incoming one cannot be told
    # apart from it, so it waits.
    add_framing(local.db, local.uid, "Veil", object_record_uid="local-only",
                ra=20.0)

    cursor = local.state().server_cursor or 0
    result = pull(local, hub)
    assert result.deferred == [("saved_framings", hub_obj.record_uid)]
    assert (local.state().server_cursor or 0) == cursor     # not moved past it
    assert len(local.rows(SavedFraming)) == 1

    # The blocker goes, so the next pull offers it again and it lands.
    local.db.delete(local.one(SavedFraming, object_record_uid="local-only"))
    local.commit()
    pull(local, hub)

    landed = local.one(SavedFraming, object_record_uid=hub_obj.record_uid)
    assert landed is not None and landed.ra == 10.0
    assert (local.state().server_cursor or 0) != cursor


# =============================================================================
# 5. Referential safety
# =============================================================================

def test_deleting_a_referenced_object_is_skipped(local, hub):
    add_object(local.db, local.uid, "Veil")
    obj = local.one(AstroObject, object_name="Veil")
    add_session(local.db, local.uid, "s1", object_record_uid=obj.record_uid,
                object_name="Veil")
    push(local, hub)

    # The hub deletes the object; the local still has a session pointing at it.
    hub.db.delete(hub.one(AstroObject, object_name="Veil"))
    hub.commit()
    payload = export_changes(hub.db, hub.user, 0, side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert any(r.reason == "still_referenced" for r in plan.resurrected)
    apply_merge(local.db, plan, user=local.user)
    local.commit()
    assert local.one(AstroObject, object_name="Veil") is not None


def test_a_skipped_delete_tells_the_hub_the_record_is_alive(local, hub):
    add_object(local.db, local.uid, "Veil")
    obj = local.one(AstroObject, object_name="Veil")
    add_session(local.db, local.uid, "s1", object_record_uid=obj.record_uid,
                object_name="Veil")
    push(local, hub)

    hub.db.delete(hub.one(AstroObject, object_name="Veil"))
    hub.commit()
    pull(local, hub)
    push(local, hub)
    assert hub.one(AstroObject, object_name="Veil") is not None


def test_a_referenced_component_is_not_deleted(local, hub):
    component = Component(user_id=local.uid, kind="camera", name="Cam",
                          sensor_width_mm=20.0, sensor_height_mm=15.0,
                          pixel_size_um=3.0)
    local.db.add(component)
    local.commit()
    component = local.one(Component, name="Cam")
    local.db.add(Rig(user_id=local.uid, rig_name="Rig", camera_id=component.id,
                     camera_record_uid=component.record_uid, active=True))
    local.commit()
    push(local, hub)

    hub.db.delete(hub.one(Component, name="Cam"))
    hub.commit()
    pull(local, hub)
    assert local.one(Component, name="Cam") is not None


def test_a_deleted_project_leaves_its_sessions_standalone(local, hub):
    """(G) The shape the route produces — links cleared, then the row — seen
    through a sync. The route itself is covered by the test below."""
    add_project(hub.db, hub.uid, "p1", "Veil")
    session = add_session(hub.db, hub.uid, "s1", project_id="p1")
    hub.db.execute(session_projects.insert().values(session_id=session.id,
                                                    project_id="p1"))
    hub.commit()
    pull(local, hub)

    # Delete the project the way the route does: unlink through the ORM.
    project = hub.one(Project, id="p1")
    for link in list(project.journal_sessions_m2m):
        project.journal_sessions_m2m.remove(link)
    for linked in hub.db.query(JournalSession).filter(
            JournalSession.project_id == "p1").all():
        linked.project_id = None
    hub.db.flush()
    hub.db.delete(project)
    hub.commit()

    assert hub.one(JournalSession, external_id="s1") is not None
    pull(local, hub)
    assert local.one(Project, id="p1") is None
    survived = local.one(JournalSession, external_id="s1")
    assert survived is not None and survived.project_id is None


def test_the_delete_project_route_unlinks_before_it_deletes(client, db_session):
    """(Rule 11 / G) The real route, through the test client."""
    user = db_session.query(DbUser).filter_by(username="default").one()
    project = Project(id="p_sync_test", user_id=user.id, name="Veil",
                      target_object_name="M42", status="In Progress")
    session = JournalSession(user_id=user.id, project_id="p_sync_test",
                             external_id="s1", object_name="M42",
                             date_utc=datetime(2026, 1, 2).date())
    db_session.add_all([project, session])
    db_session.flush()
    sync_project_link(db_session, project)
    db_session.commit()
    session_id = session.id

    response = client.post("/project/delete/p_sync_test",
                           data={"redirect_object": "M42"})
    assert response.status_code in (301, 302)

    db_session.expire_all()
    assert db_session.get(Project, "p_sync_test") is None
    survivor = db_session.get(JournalSession, session_id)
    assert survivor is not None and survivor.project_id is None
    assert db_session.execute(
        session_projects.select().where(
            session_projects.c.session_id == session_id)
    ).all() == []


def test_a_skipped_delete_leaves_no_tombstone(local, hub):
    """(V3) A delete that phase 4 turns into a re-stamp must not leave the
    tombstone that came with it: the record is still here."""
    add_object(local.db, local.uid, "Veil")
    obj = local.one(AstroObject, object_name="Veil")
    add_session(local.db, local.uid, "s1", object_record_uid=obj.record_uid,
                object_name="Veil")
    push(local, hub)

    hub.db.delete(hub.one(AstroObject, object_name="Veil"))
    hub.commit()
    pull(local, hub)

    assert local.one(AstroObject, object_name="Veil") is not None
    assert [key for key, _ in local.tombstones("astro_objects")] == []


# =============================================================================
# 6. Account markers and the locks
# =============================================================================

def test_an_untouched_hub_refuses_a_snapshot(hub):
    mark_account(hub.db.connection(), hub.uid, "seeded")
    hub.commit()
    with pytest.raises(SyncRefused) as caught:
        export_snapshot(hub.db, hub.user, side="server")
    assert caught.value.refusal.code == REFUSAL_SERVER_UNTOUCHED


def test_a_hub_with_real_changes_serves_a_snapshot(hub):
    mark_account(hub.db.connection(), hub.uid, "seeded")
    hub.commit()
    add_object(hub.db, hub.uid, "M 31")
    assert export_snapshot(hub.db, hub.user, side="server")["records"]


def test_a_tombstone_alone_also_makes_the_hub_touched(hub):
    mark_account(hub.db.connection(), hub.uid, "seeded")
    hub.commit()
    obj = add_object(hub.db, hub.uid, "M 31")
    hub.db.delete(obj)
    hub.commit()
    assert export_snapshot(hub.db, hub.user, side="server")["tombstones"]


def test_a_local_must_download_before_it_pushes(bare, hub):
    start_pairing(bare.db, bare.user, server_url=HUB_URL)
    bare.commit()
    refusal = check_sync_preconditions(bare.db, bare.user, side="local")
    assert refusal.code == REFUSAL_NEEDS_DOWNLOAD

    plan = plan_merge(bare.db, bare.user, make_payload(), side="local")
    assert [r.code for r in plan.refusals] == [REFUSAL_NEEDS_DOWNLOAD]


def _complete_download(node, hub):
    """Pair, download, and place files so the node is fully synced (cursor and
    completion time set), the state a re-pair must unwind."""
    from nova.sync_engine import commit_staged_files
    start_pairing(node.db, node.user, server_url=HUB_URL, account="hub")
    node.commit()
    add_object(hub.db, hub.uid, "M 31")
    result = download(node, hub)
    commit_staged_files(node.db, result, [], user=node.user)
    node.commit()
    state = node.state()
    assert state.server_cursor > 0 and state.local_pushed_rev > 0
    assert state.last_sync_at is not None
    assert check_sync_preconditions(node.db, node.user, side="local") is None


def test_a_new_pairing_starts_at_the_download_lock(bare):
    start_pairing(bare.db, bare.user, server_url=HUB_URL, account="hub")
    bare.commit()
    state = bare.state()
    assert state.server_cursor == 0
    assert state.local_pushed_rev == 0
    assert state.last_sync_at is None
    assert check_sync_preconditions(bare.db, bare.user, side="local").code == REFUSAL_NEEDS_DOWNLOAD


def test_re_pairing_after_a_download_rearms_the_download_lock(bare, hub):
    """A completed download's watermarks are reset on re-pair, so a fresh
    download is required before pushing again (the disconnect -> connect path)."""
    _complete_download(bare, hub)
    start_pairing(bare.db, bare.user, server_url=HUB_URL, account="hub")
    bare.commit()
    state = bare.state()
    assert state.server_cursor == 0
    assert state.local_pushed_rev == 0
    assert state.last_sync_at is None
    assert check_sync_preconditions(bare.db, bare.user, side="local").code == REFUSAL_NEEDS_DOWNLOAD


def test_re_pairing_with_a_different_account_rearms_the_download_lock(bare, hub):
    """A token for a different account is a fresh pairing: the watermarks are
    reset and a download is required, even though the old account had synced."""
    _complete_download(bare, hub)
    start_pairing(bare.db, bare.user, server_url=HUB_URL, token="other-token",
                  account="other")
    bare.commit()
    state = bare.state()
    assert state.server_cursor == 0
    assert state.local_pushed_rev == 0
    assert state.last_sync_at is None
    assert check_sync_preconditions(bare.db, bare.user, side="local").code == REFUSAL_NEEDS_DOWNLOAD


def test_a_freshly_downloaded_local_may_push(paired):
    assert check_sync_preconditions(paired.db, paired.user, side="local") is None


def test_an_unpaired_local_is_refused(bare):
    refusal = check_sync_preconditions(bare.db, bare.user, side="local")
    assert refusal.code == REFUSAL_NOT_PAIRED


def test_a_local_replace_import_blocks_a_push(paired):
    """A local YAML replace import writes a marker no delta can describe."""
    mark_account(paired.db.connection(), paired.uid, "replaced")
    paired.commit()
    refusal = check_sync_preconditions(paired.db, paired.user, side="local")
    assert refusal.code == REFUSAL_LOCAL_REPLACED


def test_a_download_clears_the_local_replace_marker(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    add_object(local.db, local.uid, "M 42")
    mark_account(local.db.connection(), local.uid, "replaced")
    local.commit()
    start_pairing(local.db, local.user, server_url=HUB_URL)
    local.commit()

    download(local, hub)
    assert check_sync_preconditions(local.db, local.user, side="local") is None
    assert account_marker_rev(local.db, local.uid, "replaced") < (
        local.state().local_pushed_rev)


def test_a_hub_replace_marker_makes_the_local_download(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    pull(local, hub)
    mark_account(hub.db.connection(), hub.uid, "replaced")
    hub.commit()

    payload = export_changes(hub.db, hub.user, 0, side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert REFUSAL_REMOTE_REPLACED in [r.code for r in plan.refusals]


def test_the_hub_refuses_an_account_marker_from_a_local(local, hub):
    """(Rule 3) A local's payload never carries one — _visible_tombstones strips
    markers out of everything a local builds — so one arriving here was not made
    by this engine. "replaced" included: a peer must not be able to declare the
    account replaced by asking."""
    for key in ("replaced", "seeded"):
        payload = make_payload(tombstones=[{"table": ACCOUNT_TABLE, "key": key,
                                            "deleted_at": iso(utc_now())}])
        plan = plan_merge(hub.db, hub.user, payload, side="server")
        assert [r.code for r in plan.refusals] == [REFUSAL_BAD_FORMAT], key


def test_the_hub_refuses_a_snapshot_in_a_push(local, hub):
    """(Rule 3) A whole account only ever travels as a download, which wipes."""
    payload = make_payload({"astro_objects": [obj_rec("u1", "M 31", utc_now())]})
    payload["snapshot"] = True
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_BAD_FORMAT]


def test_a_local_accepts_the_hubs_account_marker(local, hub):
    """(Rule 3, other direction) A hub does send the markers in its own history,
    and a local leaves them to _plan_tombstone: "seeded" is the hub's own
    bookkeeping and is ignored, not refused."""
    add_object(hub.db, hub.uid, "M 31")
    payload = export_changes(hub.db, hub.user, 0, side="server")
    payload["tombstones"].append({"table": ACCOUNT_TABLE, "key": "seeded",
                                  "deleted_at": iso(utc_now())})

    plan = plan_merge(local.db, local.user, payload, side="local")
    assert plan.refusals == []
    assert [a for a in plan.actions if a.table == ACCOUNT_TABLE] == []
    assert local.tombstones(ACCOUNT_TABLE) == []


def test_the_hub_refuses_an_unknown_marker(local, hub):
    payload = make_payload(tombstones=[{"table": ACCOUNT_TABLE, "key": "nonsense",
                                        "deleted_at": iso(utc_now())}])
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    assert [r.code for r in plan.refusals] == [REFUSAL_BAD_FORMAT]


def test_the_sync_lock_refuses_a_second_holder(paired, tmp_path):
    with sync_lock(paired.uid, instance_path=str(tmp_path)):
        with pytest.raises(SyncRefused) as caught:
            with sync_lock(paired.uid, instance_path=str(tmp_path)):
                pass
    assert caught.value.refusal.code == REFUSAL_SYNC_RUNNING


def test_the_sync_lock_is_per_user(paired, tmp_path):
    with sync_lock(paired.uid, instance_path=str(tmp_path)):
        with sync_lock(paired.uid + 1, instance_path=str(tmp_path)):
            pass  # a different account is not blocked


def test_the_sync_lock_is_released(paired, tmp_path):
    with sync_lock(paired.uid, instance_path=str(tmp_path)):
        pass
    with sync_lock(paired.uid, instance_path=str(tmp_path)):
        pass


# =============================================================================
# 7. Clock, mass-delete brake, staleness
# =============================================================================

def test_a_large_clock_offset_is_refused(hub):
    plan = plan_merge(hub.db, hub.user, make_payload(), side="server",
                      offset_seconds=MAX_CLOCK_SKEW_SECONDS + 1)
    assert REFUSAL_CLOCK_SKEW in [r.code for r in plan.refusals]


def test_an_acceptable_clock_offset_is_fine(hub):
    plan = plan_merge(hub.db, hub.user, make_payload(), side="server",
                      offset_seconds=MAX_CLOCK_SKEW_SECONDS - 1)
    assert not plan.refused


def test_the_offset_is_added_to_the_incoming_timestamp(local):
    """offset_seconds is this instance's clock minus the peer's, so it is added."""
    from nova.sync_engine import shift
    moment = datetime(2026, 1, 1, 12, 0, 0)
    assert shift(moment, 60) == moment + timedelta(seconds=60)
    assert shift(moment, -60) == moment - timedelta(seconds=60)


def test_a_fast_local_clock_does_not_hide_a_newer_hub_edit(local, hub):
    """(V1) The proving variant: raw comparison keeps the local, shifted
    comparison takes the hub's edit.

    The local's clock runs 200 s fast, so offset_seconds is +200 and the hub's
    stamp is moved forward by it before the two are compared. The local's own
    copy reads moment+200; the hub's edit is stamped moment+100 — lower, since
    it is a hub stamp — but in true time it happened 100 s later. Compared raw,
    moment+100 < moment+200, so the local would keep its row and the edit would
    never land. Shifted, moment+100+200 > moment+200, so the edit wins.
    """
    add_object(local.db, local.uid, "M 31", ra=1.0)
    push(local, hub)
    pull(local, hub)

    moment = utc_now()
    stamp(local.db, AstroObject, local.one(AstroObject, object_name="M 31").id,
          moment + timedelta(seconds=200))
    hub_obj = hub.one(AstroObject, object_name="M 31")
    hub_obj.ra_hours = 9.0
    hub.commit()
    stamp(hub.db, AstroObject, hub_obj.id, moment + timedelta(seconds=100))

    payload = export_changes(hub.db, hub.user, local.state().server_cursor or 0,
                             side="server")
    plan = plan_merge(local.db, local.user, payload, side="local",
                      offset_seconds=200)
    assert not plan.refused
    apply_merge(local.db, plan, user=local.user)
    local.commit()
    assert local.one(AstroObject, object_name="M 31").ra_hours == 9.0


def test_an_apply_that_raises_mid_way_changes_nothing(local, hub, monkeypatch):
    """(Rule 7) One transaction per apply: a failure leaves the rows, the
    tombstones, the counter and the watermarks exactly as they were."""
    add_object(hub.db, hub.uid, "M 42")
    add_object(hub.db, hub.uid, "M 31")
    pull(local, hub)
    hub.db.delete(hub.one(AstroObject, object_name="M 31"))
    hub.commit()

    payload = export_changes(hub.db, hub.user, local.state().server_cursor or 0,
                             side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert any(a.op == OP_TOMBSTONE for a in plan.actions)

    before_rev = local.rev()
    before_cursor = local.state().server_cursor
    before_pushed = local.state().local_pushed_rev

    import nova.sync_engine as engine

    def boom(conn, user_id, action, rev):
        raise RuntimeError("mid-apply")

    monkeypatch.setattr(engine, "_write_tombstone", boom)
    with pytest.raises(RuntimeError):
        apply_merge(local.db, plan, user=local.user)
    local.db.rollback()
    local.db.expire_all()

    assert {o.object_name for o in local.rows(AstroObject)} == {"M 31", "M 42"}
    assert local.tombstones("astro_objects") == []
    assert local.rev() == before_rev
    assert local.state().server_cursor == before_cursor
    assert local.state().local_pushed_rev == before_pushed


def test_an_apply_is_scoped_to_the_plans_user(local):
    """(M1) Every by-id write carries user_id, so a stray row id cannot reach
    another account's row."""
    other = DbUser(username="other")
    local.db.add(other)
    local.db.commit()
    local.db.add(AstroObject(user_id=other.id, object_name="Theirs",
                             ra_hours=1.0, dec_deg=2.0))
    local.db.commit()
    their_id = local.db.query(AstroObject).filter_by(
        object_name="Theirs").one().id

    add_object(local.db, local.uid, "Mine")
    mine = local.one(AstroObject, object_name="Mine")
    payload = make_payload(tombstones=[
        tomb("astro_objects", mine.record_uid, utc_now() + timedelta(seconds=5))])
    plan = plan_merge(local.db, local.user, payload, side="local")
    action = next(a for a in plan.actions if a.op == OP_DELETE)
    action.row_id = their_id          # point the delete at the other account

    apply_merge(local.db, plan, user=local.user)
    local.commit()
    local.db.expire_all()
    assert local.db.query(AstroObject).filter_by(
        id=their_id).one_or_none() is not None


def test_a_mass_delete_is_refused(local, hub):
    for index in range(40):
        add_object(hub.db, hub.uid, f"OBJ{index}")
    pull(local, hub)

    payload = make_payload(tombstones=[
        tomb("astro_objects", obj.record_uid, utc_now())
        for obj in hub.rows(AstroObject)])
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert REFUSAL_MASS_DELETE in [r.code for r in plan.refusals]
    assert plan.totals.ratio > MASS_DELETE_RATIO


def test_a_mass_delete_is_allowed_when_asked(local, hub):
    for index in range(40):
        add_object(hub.db, hub.uid, f"OBJ{index}")
    pull(local, hub)
    payload = make_payload(tombstones=[
        tomb("astro_objects", obj.record_uid, utc_now())
        for obj in hub.rows(AstroObject)])
    plan = plan_merge(local.db, local.user, payload, side="local",
                      allow_mass_delete=True)
    assert not plan.refused
    apply_merge(local.db, plan, user=local.user)
    local.commit()
    assert local.rows(AstroObject) == []


def test_a_small_delete_is_never_refused(local, hub):
    """Below the 20-record floor the ratio does not matter."""
    add_object(hub.db, hub.uid, "M 31")
    add_object(hub.db, hub.uid, "M 42")
    pull(local, hub)
    payload = make_payload(tombstones=[
        tomb("astro_objects", hub.one(AstroObject, object_name="M 31").record_uid,
             utc_now())])
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert not plan.refused


def test_a_stale_plan_is_refused(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    payload = export_changes(hub.db, hub.user, 0, side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")

    add_object(local.db, local.uid, "M 42")      # a local write moves the state
    with pytest.raises(SyncRefused) as caught:
        apply_merge(local.db, plan, user=local.user)
    assert caught.value.refusal.code == REFUSAL_PLAN_STALE


def test_planning_twice_gives_the_same_actions(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    add_location(hub.db, hub.uid, "Home")
    payload = export_snapshot(hub.db, hub.user, side="server")
    first = plan_merge(local.db, local.user, payload, side="local")
    second = plan_merge(local.db, local.user, payload, side="local")
    assert first.fingerprint == second.fingerprint
    assert [(a.table, a.key, a.op) for a in first.actions] == [
        (a.table, a.key, a.op) for a in second.actions]


def _one_file_payload(digest, manifest):
    """A payload whose session names a photo the receiver does not hold."""
    return {
        "format": PAYLOAD_FORMAT, "account": "hub", "side": "server",
        "rev": 1, "snapshot": False, "tombstones": [],
        "files": {digest: manifest},
        "records": {"journal_sessions": [{
            "external_id": "s1", "date_utc": "2026-01-02",
            "sync_changed_at": "2026-01-01T00:00:00.000000",
            KEY_FILES: {"session_image_file": digest},
        }]},
    }


def test_a_plans_placements_start_pending(hub, local):
    """Rule 6 without any transport: a plan that needs a file is pending until a
    commit says otherwise, so a lost transfer cannot let the cursor move."""
    digest = "ab" * 32
    payload = _one_file_payload(digest, {"role": ROLE_SESSION_PHOTO,
                                         "size": 10, "ext": "png",
                                         "name": "x.png"})
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert plan.refused is False
    assert [r.sha256 for r in plan.pending_files] == [digest]

    rev_at_export = local.rev()
    cursor_before = local.state().server_cursor or 0
    result = apply_merge(local.db, plan, user=local.user)
    assert [entry[3] for entry in result.pending_files] == [digest]

    # commit_staged_files never runs: the bytes were lost on the way.
    record_pull_applied(local.db, local.user, result, payload["rev"], rev_at_export)
    local.commit()
    assert local.state().server_cursor == cursor_before


def test_a_plan_with_no_files_is_not_pending(hub, local):
    payload = _one_file_payload("ab" * 32, {"role": ROLE_SESSION_PHOTO,
                                            "size": 10, "ext": "png",
                                            "name": "x.png"})
    del payload["records"]["journal_sessions"][0][KEY_FILES]
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert plan.pending_files == []


# =============================================================================
# 8. Concurrency
# =============================================================================

def test_an_edit_between_export_and_apply_is_not_lost(local, hub):
    """(C) A local write after the export is pushed next time, never swallowed."""
    add_object(hub.db, hub.uid, "M 31")
    payload = export_changes(hub.db, hub.user, 0, side="server")
    server_rev = payload["rev"]
    rev_at_export = local.rev()

    # The user edits something locally while the payload is in flight.
    add_location(local.db, local.uid, "Home")

    plan = plan_merge(local.db, local.user, payload, side="local")
    result = apply_merge(local.db, plan, user=local.user)
    record_pull_applied(local.db, local.user, result, server_rev, rev_at_export)
    local.commit()

    # The watermark did not move past the local edit, so it still pushes.
    pushed = export_changes(local.db, local.user,
                            local.state().local_pushed_rev, side="local")
    assert [loc["name"] for loc in pushed["records"]["locations"]] == ["Home"]


def test_a_quiet_pull_moves_the_watermark(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    rev_at_export = local.rev()
    payload = export_changes(hub.db, hub.user, 0, side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")
    result = apply_merge(local.db, plan, user=local.user)
    record_pull_applied(local.db, local.user, result, payload["rev"], rev_at_export)
    local.commit()
    assert local.state().local_pushed_rev == result.r_verbatim


def test_an_accepted_push_advances_to_the_rev_it_exported(local, hub):
    add_object(local.db, local.uid, "M 31")
    rev_at_export = local.rev()
    payload = export_changes(local.db, local.user, 0, side="local")
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    record_push_accepted(local.db, local.user, rev_at_export)
    local.commit()
    assert local.state().local_pushed_rev == rev_at_export


def test_the_watermark_never_passes_a_rev_the_sync_did_not_make(local, hub):
    add_object(local.db, local.uid, "M 31")
    rev_at_export = local.rev()
    add_object(local.db, local.uid, "M 42")     # made after the export

    payload = export_changes(local.db, local.user, 0, side="local")
    plan = plan_merge(hub.db, hub.user, payload, side="server")
    apply_merge(hub.db, plan, user=hub.user)
    hub.commit()
    record_push_accepted(local.db, local.user, rev_at_export)
    local.commit()

    assert local.state().local_pushed_rev == rev_at_export
    assert local.state().local_pushed_rev < local.rev()
    still_to_push = export_changes(local.db, local.user,
                                   local.state().local_pushed_rev, side="local")
    assert [o["object_name"] for o in still_to_push["records"]["astro_objects"]] == [
        "M 42"]


def test_a_renamed_record_reaches_the_hub_on_the_next_push(local, hub):
    """(B) and (C) together: the local's own decision is above the watermark."""
    add_object(hub.db, hub.uid, "Veil")
    add_object(local.db, local.uid, "Veil")
    rev_at_export = local.rev()
    payload = export_changes(hub.db, hub.user, 0, side="server")
    plan = plan_merge(local.db, local.user, payload, side="local")
    assert plan.has_changed
    result = apply_merge(local.db, plan, user=local.user)
    record_pull_applied(local.db, local.user, result, payload["rev"], rev_at_export)
    local.commit()

    # Nothing arrived verbatim, so the watermark does not move: the record the
    # local decided about sits above it and is still to be pushed.
    assert result.r_verbatim is None
    assert local.state().local_pushed_rev == 0
    assert result.r_changed > local.state().local_pushed_rev
    to_push = export_changes(local.db, local.user,
                             local.state().local_pushed_rev, side="local")
    assert to_push["records"]["astro_objects"]


def test_a_deferred_push_does_not_advance_the_watermark(local, hub):
    """(Correction 3) A hub that could not place a record must leave the local's
    watermark alone, or the next push would never offer it again."""
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    # A stale framing on the hub: it names the object but points at an identity
    # that is no longer the object's own, which is what a delete-and-re-create
    # leaves behind. It is what makes the incoming framing unresolvable.
    add_framing(hub.db, hub.uid, "Veil", object_record_uid="retired-uid", ra=1.0)

    add_object(local.db, local.uid, "Veil")
    local_uid = local.one(AstroObject, object_name="Veil").record_uid
    add_framing(local.db, local.uid, "Veil", object_record_uid=local_uid, ra=2.0)

    watermark = local.state().local_pushed_rev or 0
    result = push(local, hub)

    assert result.deferred == [("saved_framings", local_uid)]
    assert (local.state().local_pushed_rev or 0) == watermark
    assert [f.object_record_uid for f in hub.rows(SavedFraming)] == ["retired-uid"]

    # Clear the blocker and push again: the same record is offered again, and
    # this time it lands under the identity its object has on the hub.
    hub.db.delete(hub.one(SavedFraming, object_record_uid="retired-uid"))
    hub.commit()
    push(local, hub)

    assert [f.object_record_uid for f in hub.rows(SavedFraming)] == [
        hub_obj.record_uid]
    assert (local.state().local_pushed_rev or 0) > watermark


# =============================================================================
# 9. Download from server
# =============================================================================

def test_a_download_without_a_timestamp_is_refused(local, hub):
    """(M3) The apply writes every record straight through, so the plan refuses
    the whole snapshot rather than writing half of it."""
    snapshot = make_payload({"astro_objects": [
        {"record_uid": "u1", "object_name": "M 31"}]})
    snapshot["snapshot"] = True
    plan = plan_download(local.db, local.user, snapshot)
    assert [r.code for r in plan.refusals] == [REFUSAL_MISSING_TIMESTAMP]


def test_a_download_without_a_natural_key_is_refused(local, hub):
    """(M3) A record the apply could not key is refused, not written."""
    snapshot = make_payload({"astro_objects": [
        {"object_name": "M 31", "ra_hours": 1.0, "dec_deg": 2.0,
         "sync_changed_at": iso(utc_now())}]})
    snapshot["snapshot"] = True
    plan = plan_download(local.db, local.user, snapshot)
    assert [r.code for r in plan.refusals] == [REFUSAL_MISSING_KEY]


def test_a_download_replaces_the_local_account(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    add_object(local.db, local.uid, "M 99")
    download(local, hub)
    assert [o.object_name for o in local.rows(AstroObject)] == ["M 31"]


def test_a_download_leaves_no_per_record_tombstones(local, hub):
    add_object(local.db, local.uid, "M 99")
    add_object(hub.db, hub.uid, "M 31")
    download(local, hub)
    assert local.tombstones("astro_objects") == []


def test_a_download_does_not_trip_the_local_replace_lock(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    start_pairing(local.db, local.user, server_url=HUB_URL)
    local.commit()
    download(local, hub)
    assert check_sync_preconditions(local.db, local.user, side="local") is None


def test_a_download_leaves_nothing_to_push(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    download(local, hub)
    payload = export_changes(local.db, local.user,
                            local.state().local_pushed_rev, side="local")
    assert payload["records"] == {} and payload["tombstones"] == []


def test_a_download_leaves_the_cursor_at_zero_until_files_place(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    result = download(local, hub)
    state = local.state()
    assert state.server_cursor == 0          # apply_download does not advance it
    assert state.last_sync_at is None        # and does not mark it complete
    assert state.local_pushed_rev > 0        # but a download has committed
    assert result.created["astro_objects"] == 1


def test_commit_staged_files_marks_the_download_complete(local, hub):
    from nova.sync_engine import commit_staged_files
    add_object(hub.db, hub.uid, "M 31")
    result = download(local, hub)
    commit_staged_files(local.db, result, [], user=local.user)
    local.commit()
    state = local.state()
    assert state.server_cursor == hub.rev()
    assert state.last_sync_at is not None


def test_a_download_moves_the_local_horizon_points(local, hub):
    location = add_location(hub.db, hub.uid, "Home")
    hub.db.add(HorizonPoint(location_id=location.id, az_deg=90.0, alt_min_deg=15.0))
    hub.commit()
    download(local, hub)
    landed = local.one(Location, name="Home")
    assert [(p.az_deg, p.alt_min_deg) for p in landed.horizon_points] == [(90.0, 15.0)]


def test_a_download_keeps_the_hubs_timestamps(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    moment = utc_now() - timedelta(days=3)
    stamp(hub.db, AstroObject, hub.one(AstroObject, object_name="M 31").id, moment)
    download(local, hub)
    assert local.one(AstroObject, object_name="M 31").sync_changed_at == moment


def test_a_download_moves_the_session_project_links(local, hub):
    add_project(hub.db, hub.uid, "p1", "Veil")
    session = add_session(hub.db, hub.uid, "s1")
    hub.db.execute(session_projects.insert().values(session_id=session.id,
                                                project_id="p1"))
    hub.commit()
    download(local, hub)
    landed = local.one(JournalSession, external_id="s1")
    assert [p.id for p in landed.projects] == ["p1"]


def test_a_download_plan_counts_what_it_would_wipe(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    add_object(local.db, local.uid, "M 99")
    plan = plan_download(local.db, local.user,
                         export_snapshot(hub.db, hub.user, side="server"))
    assert plan.wiping["astro_objects"] == 1
    assert plan.inserting["astro_objects"] == 1


def test_a_stale_download_plan_is_refused(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    plan = plan_download(local.db, local.user,
                         export_snapshot(hub.db, hub.user, side="server"))
    add_object(local.db, local.uid, "M 99")
    with pytest.raises(SyncRefused) as caught:
        apply_download(local.db, plan, user=local.user)
    assert caught.value.refusal.code == REFUSAL_PLAN_STALE


# =============================================================================
# 10. Round trips: both sides converge
# =============================================================================

def test_a_full_round_trip_converges(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    add_location(hub.db, hub.uid, "Home")
    add_project(hub.db, hub.uid, "p1", "Veil")
    add_session(hub.db, hub.uid, "s1", object_name="M 31", location_name="Home",
                project_id="p1")
    add_rig(hub.db, hub.uid, "Rig A")
    add_view(hub.db, hub.uid, "Wide")
    add_framing(hub.db, hub.uid, "M 31", ra=10.0)
    add_filter(hub.db, hub.uid, "OIII")
    set_prefs(hub.db, hub.uid, {"theme": "dark"})

    pull(local, hub)
    push(local, hub)
    assert push(local, hub).created == {}

    for model, field in ((AstroObject, "object_name"), (Location, "name"),
                         (Project, "name"), (Rig, "rig_name"),
                         (SavedView, "name"), (SavedFraming, "object_name"),
                         (UserCustomFilter, "filter_key")):
        assert {getattr(r, field) for r in local.rows(model)} == \
               {getattr(r, field) for r in hub.rows(model)}, model.__name__


def test_a_local_create_then_hub_edit_then_local_delete_converges(local, hub):
    add_object(local.db, local.uid, "M 31")
    push(local, hub)

    hub_obj = hub.one(AstroObject, object_name="M 31")
    hub_obj.ra_hours = 12.0
    hub.commit()
    pull(local, hub)
    assert local.one(AstroObject, object_name="M 31").ra_hours == 12.0

    local.db.delete(local.one(AstroObject, object_name="M 31"))
    local.commit()
    push(local, hub)
    assert hub.rows(AstroObject) == []


def test_three_way_convergence_with_two_locals(hub):
    other = Node("other")
    try:
        first = Node("first")
        try:
            start_pairing(first.db, first.user, server_url=HUB_URL)
            first.commit()
            first.db.query(SyncState).filter_by(user_id=first.uid).update(
                {"last_sync_at": utc_now()})
            first.commit()
            start_pairing(other.db, other.user, server_url=HUB_URL)
            other.commit()
            other.db.query(SyncState).filter_by(user_id=other.uid).update(
                {"last_sync_at": utc_now()})
            other.commit()

            add_object(first.db, first.uid, "M 31", ra=1.0)
            push(first, hub)
            pull(other, hub)
            assert other.one(AstroObject, object_name="M 31") is not None

            add_object(other.db, other.uid, "M 42")
            push(other, hub)
            pull(first, hub)
            assert first.one(AstroObject, object_name="M 42") is not None
        finally:
            first.close()
    finally:
        other.close()


def test_a_snapshot_round_trip_is_idempotent(local, hub):
    add_object(hub.db, hub.uid, "M 31")
    add_location(hub.db, hub.uid, "Home")
    download(local, hub)
    snapshot = export_snapshot(local.db, local.user, side="local")
    assert len(snapshot["records"]["astro_objects"]) == 1
    assert len(snapshot["records"]["locations"]) == 1
    # The local's own snapshot, applied to itself, changes nothing.
    plan = plan_merge(local.db, local.user, snapshot, side="local")
    assert plan.actions == []


def test_a_framing_without_an_object_uid_adopts_the_hubs(local, hub):
    """A local framing keyed by name only is the same framing, not a clash."""
    hub_obj = add_object(hub.db, hub.uid, "Veil")
    add_framing(hub.db, hub.uid, "Veil", object_record_uid=hub_obj.record_uid,
                ra=10.0)
    add_object(local.db, local.uid, "Veil")            # same UID as the hub's
    add_framing(local.db, local.uid, "Veil", ra=20.0)  # no object_record_uid

    pull(local, hub)
    framings = local.rows(SavedFraming)
    assert len(framings) == 1
    assert framings[0].object_record_uid == hub_obj.record_uid
