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

The app still reads the old columns; nothing reads the UID columns yet.
"""

from typing import NamedTuple

from sqlalchemy import inspect, select, text

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
            if isinstance(obj, model) and obj.user_id == user_id:
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
