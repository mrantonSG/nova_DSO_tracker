"""
record_uid links: the one rule for turning a link target into its record_uid.

Each <role>_record_uid column stores the record_uid of the row the old link
column points at. Every write path uses these helpers so the rule is the same
everywhere:
- The target must belong to the same user, otherwise the UID is None.
- Rigs and components are found by row number only, never by name.
- Objects and locations are found by exact name ("=", case- and
  space-sensitive), and only when exactly one row matches.
- An empty old link gives None.

Rig and object reads use these UIDs (components_for_rig / rig_components,
objects_by_uid / object_for_uid, framing_for_object / framed_object_uids).
The location link is read by location_references for the delete rule;
session locations shown elsewhere still come from the stored location_name text.
"""

from typing import NamedTuple, Optional

from sqlalchemy import and_, func, inspect, or_, select, text
from sqlalchemy.orm.exc import ObjectDeletedError

from nova.models import (
    AstroObject, Component, JournalSession, Location, Project, Rig, SavedFraming, _new_record_uid,
)


class Link(NamedTuple):
    table: str
    uid_col: str
    old_col: str
    target: str
    match_col: str  # "id" = row-number link, anything else = exact name link

    @property
    def name(self):
        return f"{self.table}.{self.uid_col}"

    @property
    def by_id(self):
        return self.match_col == "id"


# Shared by the uid_links_v1 fill in _run_schema_patches, the sync helpers and
# the agreement check. Order and values must not change: the fill already ran.
LINKS = (
    Link("rigs", "telescope_record_uid", "telescope_id", "components", "id"),
    Link("rigs", "camera_record_uid", "camera_id", "components", "id"),
    Link("rigs", "reducer_extender_record_uid", "reducer_extender_id", "components", "id"),
    Link("rigs", "guide_telescope_record_uid", "guide_telescope_id", "components", "id"),
    Link("rigs", "guide_camera_record_uid", "guide_camera_id", "components", "id"),
    Link("journal_sessions", "rig_record_uid", "rig_id_snapshot", "rigs", "id"),
    Link("journal_sessions", "object_record_uid", "object_name", "astro_objects", "object_name"),
    Link("journal_sessions", "location_record_uid", "location_name", "locations", "name"),
    Link("saved_framings", "rig_record_uid", "rig_id", "rigs", "id"),
    Link("saved_framings", "object_record_uid", "object_name", "astro_objects", "object_name"),
    Link("projects", "target_object_record_uid", "target_object_name", "astro_objects", "object_name"),
)
NAME_LINKS = tuple(link for link in LINKS if not link.by_id)

_ID_MODELS = (Component, Rig)
_NAME_COLUMNS = {AstroObject: AstroObject.object_name, Location: Location.name}
_TABLE_MODELS = {m.__tablename__: m for m in (Rig, JournalSession, SavedFraming, Project)}
_RIG_COMPONENT_COLUMNS = (
    ("telescope_record_uid", "telescope_id"),
    ("camera_record_uid", "camera_id"),
    ("reducer_extender_record_uid", "reducer_extender_id"),
    ("guide_telescope_record_uid", "guide_telescope_id"),
    ("guide_camera_record_uid", "guide_camera_id"),
)


class RigComponents(NamedTuple):
    """A rig's five components, each resolved through its record_uid link."""
    telescope: Optional[Component]
    camera: Optional[Component]
    reducer_extender: Optional[Component]
    guide_telescope: Optional[Component]
    guide_camera: Optional[Component]


def components_by_uid(db, user_id):
    """{record_uid: Component} for every component of user_id. For pages with many rigs."""
    if user_id is None:
        return {}
    return {
        c.record_uid: c
        for c in db.scalars(select(Component).where(
            Component.user_id == user_id,
            Component.record_uid.isnot(None),
            Component.record_uid != ""))
    }


def rig_components(rig, by_uid):
    """The rig's five components from a components_by_uid map.

    The record_uid alone decides: an empty UID, or one not in the map, is a
    missing component, never the row its *_id column names.
    """
    return RigComponents(
        *(by_uid.get(getattr(rig, uid_col)) for uid_col, _ in _RIG_COMPONENT_COLUMNS))


def components_for_rig(db, rig):
    """RigComponents of a single rig, one query. Empty or unknown UIDs are missing."""
    uids = {getattr(rig, uid_col) for uid_col, _ in _RIG_COMPONENT_COLUMNS} - {None, ""}
    by_uid = {}
    if uids:
        by_uid = {
            c.record_uid: c
            for c in db.scalars(select(Component).where(
                Component.user_id == rig.user_id, Component.record_uid.in_(uids)))
        }
    return rig_components(rig, by_uid)


def rigs_by_uid(db, user_id):
    """{record_uid: Rig} for every rig of user_id. For pages with many sessions or framings."""
    if user_id is None:
        return {}
    return {
        r.record_uid: r
        for r in db.scalars(select(Rig).where(
            Rig.user_id == user_id,
            Rig.record_uid.isnot(None),
            Rig.record_uid != ""))
    }


def rig_for_uid(db, user_id, uid):
    """The rig of user_id with record_uid `uid`, or None. The UID alone decides:
    an empty UID, or one with no rig of that user, is no rig."""
    if not uid or user_id is None:
        return None
    return db.scalars(select(Rig).where(
        Rig.user_id == user_id, Rig.record_uid == uid).limit(1)).first()


def objects_by_uid(db, user_id):
    """{record_uid: AstroObject} for every object of user_id. For pages with many rows."""
    if user_id is None:
        return {}
    return {
        o.record_uid: o
        for o in db.scalars(select(AstroObject).where(
            AstroObject.user_id == user_id,
            AstroObject.record_uid.isnot(None),
            AstroObject.record_uid != ""))
    }


def object_for_uid(db, user_id, uid):
    """The object of user_id with record_uid `uid`, or None. The UID alone decides:
    an empty UID, or one with no object of that user, is no object."""
    if not uid or user_id is None:
        return None
    return db.scalars(select(AstroObject).where(
        AstroObject.user_id == user_id, AstroObject.record_uid == uid).limit(1)).first()


def framing_for_object(db, user_id, object_uid):
    """The saved framing of user_id whose object_record_uid is `object_uid`, or None.
    An empty UID, or one with no framing of that user, is no framing."""
    if not object_uid or user_id is None:
        return None
    return db.scalars(select(SavedFraming).where(
        SavedFraming.user_id == user_id,
        SavedFraming.object_record_uid == object_uid).limit(1)).first()


def framed_object_uids(db, user_id):
    """record_uids of user_id's objects that have a saved framing. For list pages."""
    if user_id is None:
        return set()
    return set(db.scalars(select(SavedFraming.object_record_uid).where(
        SavedFraming.user_id == user_id,
        SavedFraming.object_record_uid.isnot(None),
        SavedFraming.object_record_uid != "")))


# --- Reference counts: what a delete would break --------------------------------

class ObjectReferences(NamedTuple):
    """Rows of one user that point at an object by UID."""
    sessions: int
    projects: int
    framings: int


def object_references(db, user_id, uid):
    """Sessions, projects and framings of user_id linked to object `uid` by UID.

    An empty UID, or no user, links nothing: all counts are 0.
    """
    if not uid or user_id is None:
        return ObjectReferences(0, 0, 0)

    def count(model, column):
        return db.scalar(select(func.count()).select_from(model).where(
            model.user_id == user_id, column == uid)) or 0

    return ObjectReferences(
        count(JournalSession, JournalSession.object_record_uid),
        count(Project, Project.target_object_record_uid),
        count(SavedFraming, SavedFraming.object_record_uid),
    )


class RigReferences(NamedTuple):
    """Rows of one user that point at a rig by UID."""
    sessions: int
    framings: int


def rig_references(db, user_id, uid):
    """Sessions and saved framings of user_id linked to rig `uid` by UID.

    An empty UID, or no user, links nothing: both counts are 0.
    """
    if not uid or user_id is None:
        return RigReferences(0, 0)

    def count(model, column):
        return db.scalar(select(func.count()).select_from(model).where(
            model.user_id == user_id, column == uid)) or 0

    return RigReferences(
        count(JournalSession, JournalSession.rig_record_uid),
        count(SavedFraming, SavedFraming.rig_record_uid),
    )


def location_references(db, user_id, uid):
    """Journal sessions of user_id linked to location `uid` by UID. An empty UID links nothing."""
    if not uid or user_id is None:
        return 0
    return db.scalar(select(func.count()).select_from(JournalSession).where(
        JournalSession.user_id == user_id, JournalSession.location_record_uid == uid)) or 0


def component_rig_usage(db, user_id, uid):
    """Rigs of user_id that use component `uid` in any of the five roles, by UID.

    The record_uid alone decides: an empty UID is used by no rig.
    """
    if not uid or user_id is None:
        return []
    roles = [getattr(Rig, uid_col) == uid for uid_col, _ in _RIG_COMPONENT_COLUMNS]
    return list(db.scalars(select(Rig).where(Rig.user_id == user_id, or_(*roles))))


# --- Target -> record_uid ------------------------------------------------------

def uid_of(row, user_id):
    """record_uid of an already loaded target row; None if there is no row or it is another user's.

    A row not yet in the database gets its record_uid here rather than at
    flush, so it can be linked before the session is flushed.
    """
    if row is None or user_id is None or row.user_id != user_id:
        return None
    if not row.record_uid and not inspect(row).persistent:
        row.record_uid = _new_record_uid()
    return row.record_uid or None


def uid_for_id(db, model, user_id, row_id):
    """record_uid of `model` row `row_id` if it belongs to `user_id`. Rigs and components only."""
    if model not in _ID_MODELS:
        raise ValueError(f"{model.__name__} is not linked by row number")
    if row_id is None or user_id is None:
        return None
    return uid_of(db.get(model, row_id), user_id)


def uid_for_name(db, model, user_id, name):
    """record_uid of the one `user_id` row of `model` named exactly `name`. Objects and locations only.

    Queries the database, and the app's sessions do not autoflush: flush
    first, or use uid_of, for a target added in the same transaction.
    """
    column = _NAME_COLUMNS.get(model)
    if column is None:
        raise ValueError(f"{model.__name__} is not linked by name")
    if not name or user_id is None:
        return None
    uids = db.scalars(
        select(model.record_uid)
        .where(model.user_id == user_id, column == name,
               model.record_uid.isnot(None), model.record_uid != "")
        .limit(2)
    ).all()
    return uids[0] if len(uids) == 1 else None


# --- Per-row sync: call after the last write to the row's old link columns ----

def _sync_uid(db, row, uid_col, model, old_value):
    """Set row.<uid_col> from the old link value `old_value` pointing into `model`.

    - Old link resolves: the resolved UID.
    - Old link empty: None.
    - Old link set but resolves to nothing: keep the current UID only if it
      points at no row of this user in `model` (its target was deleted),
      otherwise None.
    """
    user_id = row.user_id
    if old_value is None or old_value == "":
        setattr(row, uid_col, None)
        return
    if model in _ID_MODELS:
        resolved = uid_for_id(db, model, user_id, old_value)
    else:
        resolved = uid_for_name(db, model, user_id, old_value)
    if resolved is None:
        current = getattr(row, uid_col)
        live = current and db.scalar(
            select(model.id).where(model.user_id == user_id, model.record_uid == current).limit(1)
        ) is not None
        resolved = current if current and not live else None
    setattr(row, uid_col, resolved)


def sync_rig_links(db, rig):
    """Set the five component UIDs of `rig` from its *_id columns."""
    for uid_col, id_col in _RIG_COMPONENT_COLUMNS:
        _sync_uid(db, rig, uid_col, Component, getattr(rig, id_col))


def sync_session_links(db, session, include_rig=True):
    """Set the object, location and (unless include_rig=False) rig UIDs of a journal session."""
    _sync_uid(db, session, "object_record_uid", AstroObject, session.object_name)
    _sync_uid(db, session, "location_record_uid", Location, session.location_name)
    if include_rig:
        _sync_uid(db, session, "rig_record_uid", Rig, session.rig_id_snapshot)


def sync_framing_links(db, framing):
    """Set the rig and object UIDs of a saved framing."""
    _sync_uid(db, framing, "rig_record_uid", Rig, framing.rig_id)
    _sync_uid(db, framing, "object_record_uid", AstroObject, framing.object_name)


def sync_project_link(db, project):
    """Set the target object UID of a project."""
    _sync_uid(db, project, "target_object_record_uid", AstroObject, project.target_object_name)


def refresh_rig_row_numbers(db, user_id):
    """Point the old rig columns of this user's sessions and framings at the rigs
    their UIDs name (I6).

    Only a rig UID that resolves to a rig of this user sets rig_id_snapshot /
    rig_id to that rig's row number. A row whose UID is empty, or is non-empty
    and resolves to nothing, is left untouched: the row keeps whatever its old
    column held.

    Run at the end of a rig import, so a session or framing linked before the
    rigs came back still names the right row number.
    """
    if user_id is None:
        return
    rigs = {
        r.record_uid: r.id
        for r in db.scalars(select(Rig).where(
            Rig.user_id == user_id, Rig.record_uid.isnot(None), Rig.record_uid != ""))
    }
    for model, col in ((JournalSession, "rig_id_snapshot"), (SavedFraming, "rig_id")):
        for row in db.scalars(select(model).where(model.user_id == user_id)):
            if row.rig_record_uid in rigs:
                setattr(row, col, rigs[row.rig_record_uid])
    db.flush()


# --- Adopt on create and move between objects ----------------------------------

def _empty_uid_rows(db, user_id, model, uid_col, name_col, uid, name):
    """This user's `model` rows whose UID equals `uid`, or whose UID is empty
    (NULL or "") and whose name column equals `name` exactly. A row whose UID
    points elsewhere is never returned."""
    clauses = []
    if name is not None:
        clauses.append(and_(or_(uid_col.is_(None), uid_col == ""), name_col == name))
    if uid:
        clauses.append(uid_col == uid)
    if not clauses:
        return []
    return db.query(model).filter(model.user_id == user_id, or_(*clauses)).all()


def _loaded_row_of_user(obj, model, user_id):
    """True if obj is a `model` of user_id. An expired instance whose row is
    gone (e.g. bulk-deleted) is skipped instead of raising."""
    if not isinstance(obj, model):
        return False
    try:
        return obj.user_id == user_id
    except ObjectDeletedError:
        return False


def adopt_unlinked_rows(db, row):
    """Link a just-flushed AstroObject or Location to this user's rows that name it.

    A row is adopted only when it belongs to the same user, its stored name
    equals `row`'s name exactly ("=", as the sync helpers compare), and its UID
    column is empty (NULL or ""). A row whose UID points at anything, including
    a deleted record, is never touched. Returns the number of rows adopted.
    """
    if isinstance(row, AstroObject):
        name, targets = row.object_name, (
            (JournalSession, JournalSession.object_name, JournalSession.object_record_uid),
            (Project, Project.target_object_name, Project.target_object_record_uid),
            (SavedFraming, SavedFraming.object_name, SavedFraming.object_record_uid),
        )
    elif isinstance(row, Location):
        name, targets = row.name, (
            (JournalSession, JournalSession.location_name, JournalSession.location_record_uid),
        )
    else:
        raise ValueError(f"{type(row).__name__} is not adopted on create")
    uid = row.record_uid
    if not uid or name is None:
        return 0
    adopted = 0
    for model, name_col, uid_col in targets:
        adopted += db.query(model).filter(
            model.user_id == row.user_id, name_col == name,
            or_(uid_col.is_(None), uid_col == ""),
        ).update({uid_col: uid}, synchronize_session=False)
    # Loaded rows still hold the empty UID; reload it on next access
    # (SessionLocal uses expire_on_commit=False).
    for model, _name_col, uid_col in targets:
        for obj in list(db.identity_map.values()):
            if _loaded_row_of_user(obj, model, row.user_id):
                db.expire(obj, [uid_col.key])
    return adopted


def adopt_unlinked_rows_for_user(db, user_id, links=NAME_LINKS):
    """Link every empty-UID row of `user_id` whose stored name names one of the
    user's objects or locations, in one set-based UPDATE per link.

    Only rows whose UID column is empty (NULL or "") change; a row with any
    non-empty UID, including a dangling one, is never touched. Call once after
    a bulk create, with a flush first so the new rows are seen. Returns
    {link name: rows changed}.
    """
    db.flush()
    changed = {}
    for link in links:
        if link.by_id:
            raise ValueError(f"{link.name} is a row-number link and is never adopted in bulk")
        expected = _expected_uid_sql(link, link.table)
        empty = f"NULLIF({link.table}.{link.uid_col}, '') IS NULL"
        changed[link.name] = db.execute(
            text(f"UPDATE {link.table} SET {link.uid_col} = {expected} "
                 f"WHERE user_id = :user_id AND {empty} AND {expected} IS NOT NULL"),
            {"user_id": user_id},
        ).rowcount
        # Loaded rows still hold the empty UID; reload it on next access.
        model = _TABLE_MODELS[link.table]
        for obj in list(db.identity_map.values()):
            if _loaded_row_of_user(obj, model, user_id):
                db.expire(obj, [link.uid_col])
    return changed


def repoint_object_links(db, user_id, old_uid, old_name, new_uid, new_name,
                         delete_conflicting_framing=False):
    """Point this user's rows linked to one object at another object.

    A row is linked to the old object when its UID equals `old_uid`, or when its
    UID is empty (NULL or "") and its name equals `old_name` exactly. Each such
    row gets the new name text and the new UID. A row already linked to the new
    object (its UID equals `new_uid`) only gets the new name text, its UID
    untouched. A row whose UID points at any other object is never touched. When
    `delete_conflicting_framing` is set, a saved framing already linked to the
    target wins and the linked framing is deleted instead of moved (the merge
    rule). The rig UID of a framing is not touched.
    Returns {"sessions": n, "projects": n, "framings": n}.
    """
    moved = {"sessions": 0, "projects": 0, "framings": 0}
    for model, key, uid_col, name_col in (
        (JournalSession, "sessions", JournalSession.object_record_uid, JournalSession.object_name),
        (Project, "projects", Project.target_object_record_uid, Project.target_object_name),
    ):
        for r in _empty_uid_rows(db, user_id, model, uid_col, name_col, old_uid, old_name):
            setattr(r, name_col.key, new_name)
            setattr(r, uid_col.key, new_uid)
            moved[key] += 1
        if new_uid:
            for r in db.query(model).filter(model.user_id == user_id, uid_col == new_uid).all():
                if getattr(r, name_col.key) != new_name:
                    setattr(r, name_col.key, new_name)
                    moved[key] += 1
    framing_keep = None
    if delete_conflicting_framing:
        framing_keep = db.query(SavedFraming).filter_by(
            user_id=user_id, object_name=new_name).one_or_none()
    for f in _empty_uid_rows(db, user_id, SavedFraming, SavedFraming.object_record_uid,
                             SavedFraming.object_name, old_uid, old_name):
        if framing_keep is not None and framing_keep.id != f.id:
            db.delete(f)
        else:
            f.object_name = new_name
            f.object_record_uid = new_uid
            moved["framings"] += 1
    if new_uid:
        for f in db.query(SavedFraming).filter(
                SavedFraming.user_id == user_id,
                SavedFraming.object_record_uid == new_uid).all():
            if f.object_name == new_name:
                continue
            # A framing name is unique per user: do not collide with another one.
            clash = db.query(SavedFraming).filter(
                SavedFraming.user_id == user_id,
                SavedFraming.object_name == new_name,
                SavedFraming.id != f.id,
            ).first()
            if clash is None:
                f.object_name = new_name
                moved["framings"] += 1
    return moved


# --- Bulk ------------------------------------------------------------------------

def _expected_uid_sql(link, row):
    """SQL for the UID that `row`.old_col resolves to under the rule, else NULL."""
    name_guard = "" if link.by_id else f" AND {row}.{link.old_col} <> ''"
    return (f"(SELECT CASE WHEN COUNT(*) = 1 THEN MAX(t.record_uid) END FROM {link.target} t "
            f"WHERE t.{link.match_col} = {row}.{link.old_col}{name_guard} "
            f"AND t.user_id = {row}.user_id AND t.record_uid <> '')")


def resync_user_links(db, user_id, links=NAME_LINKS):
    """Re-link `user_id`'s object and location links by exact name, e.g. after a config import.

    Only rows whose old name matches exactly one target change; a row whose
    name matches nothing keeps its UID. Rig and component links are refused:
    they are never re-linked in bulk. Flushes first so rows added in this
    transaction are seen. Returns {link name: rows changed}.
    """
    for link in links:
        if link.by_id:
            raise ValueError(f"{link.name} is a row-number link and is never re-linked in bulk")
    db.flush()
    changed = {}
    for link in links:
        expected = _expected_uid_sql(link, link.table)
        changed[link.name] = db.execute(
            text(f"UPDATE {link.table} SET {link.uid_col} = {expected} "
                 f"WHERE user_id = :user_id AND {expected} IS NOT NULL "
                 f"AND {link.uid_col} IS NOT {expected}"),
            {"user_id": user_id},
        ).rowcount
        # Loaded rows still hold the old value; reload it on next access.
        model = _TABLE_MODELS[link.table]
        for obj in list(db.identity_map.values()):
            if _loaded_row_of_user(obj, model, user_id):
                db.expire(obj, [link.uid_col])
    return changed


# --- Agreement check ---------------------------------------------------------

def count_link_disagreements(conn, user_id=None, links=LINKS):
    """Per link, count rows whose UID disagrees with what the old link resolves to now.

    Read-only; `conn` is a Session or a Connection.
    - wrong: the UID points at a row of the target table that is not the one
      the old link resolves to (another user's row included).
    - missing: the UID is empty but the old link resolves to a row.
    A UID that points at no row at all (its target was deleted) is accepted.
    Returns {link name: {"wrong": n, "missing": n}}.
    """
    user_filter = " WHERE s.user_id = :user_id" if user_id is not None else ""
    result = {}
    for link in links:
        expected = _expected_uid_sql(link, "s")
        uid = f"NULLIF(s.{link.uid_col}, '')"
        row = conn.execute(
            text(f"SELECT "
                 f"COALESCE(SUM(CASE WHEN {uid} IS NOT NULL "
                 f"AND EXISTS (SELECT 1 FROM {link.target} h WHERE h.record_uid = s.{link.uid_col}) "
                 f"AND {uid} IS NOT {expected} THEN 1 ELSE 0 END), 0), "
                 f"COALESCE(SUM(CASE WHEN {uid} IS NULL AND {expected} IS NOT NULL "
                 f"THEN 1 ELSE 0 END), 0) "
                 f"FROM {link.table} s{user_filter}"),
            {"user_id": user_id} if user_id is not None else {},
        ).one()
        result[link.name] = {"wrong": row[0], "missing": row[1]}
    return result
