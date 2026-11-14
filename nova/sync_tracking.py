"""Instance sync — change tracking (step 2).

Every change to a synced record, by any write path, must leave a trace the sync
layer (step 3) can read: sync_changed_at = utc_now() and a fresh per-user
sync_rev on the row, and a SyncTombstone for every delete. Nothing reads these
yet.

Two entry points:

* register_sync_tracking() installs one before_flush listener on
  sqlalchemy.orm.Session — class level, so every session built from
  SessionLocal is covered, including the private ones from
  SessionLocal.session_factory() (purge_user_app_data, the last_seen update).
* stamp_rows() / tombstone_rows() / mark_account() for the few writes that
  bypass the ORM: bulk query.update()/query.delete() and raw SQL.

Rules implemented here:

* a new or genuinely modified row (column changes; a JournalSession's projects
  collection counts) is stamped;
* a save that changes nothing does not bump the rev;
* HorizonPoint new/changed/deleted bumps its parent Location;
* a delete writes a tombstone under the row's natural key (skipped when the
  key is empty);
* a natural-key change (saved view renamed, custom filter key changed, framing
  re-pointed at another object) tombs the OLD key;
* creating a row whose key has a tombstone removes that tombstone;
* session.info["sync_apply_remote"] = True means an explicitly set
  sync_changed_at is kept instead of overwritten (the rev is still assigned).
  Nothing sets that flag yet; the remote-apply path in step 3 will.

Revs are per user and only ever grow. One rev per user per flush is enough:
everything a single flush touches is one change set.
"""

from sqlalchemy import bindparam, event, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from nova.models import (
    AstroObject, Component, HorizonPoint, JournalSession, Location, Project,
    Rig, SavedFraming, SavedView, SyncTombstone, UiPref, UserCustomFilter,
)
from nova.sync_time import utc_now

# The ten tables carrying sync_changed_at / sync_rev (nova.SYNC_TABLES), by
# class so isinstance() is a cheap identity test.
SYNC_MODELS = (
    Location, AstroObject, Component, Rig, JournalSession,
    Project, SavedView, SavedFraming, UserCustomFilter, UiPref,
)

# Reserved table_name for whole-account markers; record_key is the kind.
ACCOUNT_TABLE = "__account__"
ACCOUNT_KINDS = ("seeded", "replaced")

# UPDATE ... RETURNING needs SQLite 3.35+. Detected once, per process.
_RETURNING_SUPPORTED = None


def _sql_time(value=None):
    """A UTC datetime as the text SQLite stores (same shape as the sync_v1 fill)."""
    return (value or utc_now()).strftime("%Y-%m-%d %H:%M:%S.%f")


def record_key(obj):
    """The natural key a tombstone carries for `obj`. Empty string when it has none."""
    if isinstance(obj, (Location, AstroObject, Component, Rig)):
        return (obj.record_uid or "").strip()
    if isinstance(obj, JournalSession):
        return (obj.external_id or "").strip()
    if isinstance(obj, Project):
        return str(obj.id or "").strip()
    if isinstance(obj, SavedView):
        return (obj.name or "").strip()
    if isinstance(obj, SavedFraming):
        # The object the framing belongs to; a framing with no object UID falls
        # back to the object name (same rule as the link helpers).
        return (obj.object_record_uid or "").strip() or (obj.object_name or "").strip()
    if isinstance(obj, UserCustomFilter):
        return (obj.filter_key or "").strip()
    if isinstance(obj, UiPref):
        return str(obj.user_id) if obj.user_id is not None else ""
    return ""


# The FILE-bucket columns per model (see _COLUMN_CLASSES in nova/sync_engine.py,
# which imports this module, so the names are repeated rather than imported).
FILE_SLOTS = {
    "journal_sessions": ("session_image_file", "asiair_log_content",
                         "phd2_log_content", "nina_log_content"),
    "projects": ("final_image_file",),
}

# Columns a write may change without it being a sync change: computed here from
# something else and never carried in a payload, so changing one of them alone
# must not stamp a row or bump a rev. Both columns live here for the same
# reason, even though the engine (nova/sync_engine.py _COLUMN_CLASSES) files
# them in different buckets: `log_analysis_cache` is a FILE-bucket blob (a
# parse-once cache of the raw log) and `calculated_integration_time_minutes` is
# DERIVED (recomputed from synced columns). Neither travels, so neither is a
# change worth a rev. Only the models that have such a column are listed.
DERIVED_COLUMNS = {
    "journal_sessions": (
        "log_analysis_cache",
        "calculated_integration_time_minutes",
    ),
}


def mark_file_changed(obj, slot):
    """Make a flush stamp `obj` because one of its files changed on disk.

    A photo, a project image and a log keep their name when they are replaced
    (a session photo is "<id>.<ext>" whichever bytes it holds), so the column
    does not change and before_flush would see an unmodified row and stamp
    nothing. Sync compares file content by digest, not by name, so the record
    has to carry a new rev or the new bytes never travel.

    flag_modified() on the column raises is_modified() for this object (verified:
    a loaded attribute flagged this way puts the object in session.dirty and makes
    is_modified() True), and the listener then stamps it. The attribute must be
    loaded first: flag_modified() raises for an attribute "not present in the
    object state".

    Returns True when the row was marked. Safe to call on any object.
    """
    if obj is None or slot not in FILE_SLOTS.get(obj.__tablename__, ()):
        return False
    try:
        getattr(obj, slot)          # force the load flag_modified() needs
        flag_modified(obj, slot)
    except Exception:
        return False
    return True


def _old_value(obj, name):
    """`obj`'s value for `name` before this flush; the current one if unchanged."""
    history = inspect(obj).attrs[name].history
    if history.has_changes() and history.deleted:
        return history.deleted[0]
    return getattr(obj, name, None)


def _old_record_key(obj):
    """The natural key `obj` had before this flush."""
    if isinstance(obj, SavedFraming):
        uid = (_old_value(obj, "object_record_uid") or "").strip()
        return uid or (_old_value(obj, "object_name") or "").strip()
    if isinstance(obj, UiPref):
        return str(obj.user_id) if obj.user_id is not None else ""
    name = {
        Location: "record_uid", AstroObject: "record_uid", Component: "record_uid",
        Rig: "record_uid", JournalSession: "external_id", Project: "id",
        SavedView: "name", UserCustomFilter: "filter_key",
    }.get(type(obj))
    if name is None:
        return ""
    value = _old_value(obj, name)
    return str(value or "").strip()


# --- The change clock -------------------------------------------------------

def _supports_returning(conn):
    global _RETURNING_SUPPORTED
    if _RETURNING_SUPPORTED is None:
        try:
            row = conn.execute(text("SELECT sqlite_version();")).fetchone()
            parts = tuple(int(p) for p in str(row[0]).split(".")[:2])
            _RETURNING_SUPPORTED = parts >= (3, 35)
        except Exception:
            _RETURNING_SUPPORTED = False
    return _RETURNING_SUPPORTED


def next_rev(conn, user_id):
    """Bump and return `user_id`'s sync rev. None when there is no user.

    Accepts a Connection or a Session. The counter row is created on first use,
    so a user provisioned after the upgrade needs nothing extra.
    """
    if user_id is None:
        return None
    conn.execute(
        text("INSERT OR IGNORE INTO sync_counter (user_id, rev) VALUES (:uid, 0);"),
        {"uid": user_id},
    )
    if _supports_returning(conn):
        row = conn.execute(
            text("UPDATE sync_counter SET rev = rev + 1 WHERE user_id = :uid RETURNING rev;"),
            {"uid": user_id},
        ).fetchone()
    else:
        conn.execute(
            text("UPDATE sync_counter SET rev = rev + 1 WHERE user_id = :uid;"),
            {"uid": user_id},
        )
        row = conn.execute(
            text("SELECT rev FROM sync_counter WHERE user_id = :uid;"), {"uid": user_id}
        ).fetchone()
    return int(row[0]) if row is not None else None


# --- Helpers for the non-ORM write paths ------------------------------------

_TOMBSTONE_UPSERT = (
    "INSERT INTO sync_tombstones "
    "(user_id, table_name, record_key, deleted_at, sync_rev) "
    "VALUES (:uid, :table_name, :record_key, :deleted_at, :rev) "
    "ON CONFLICT(user_id, table_name, record_key) DO UPDATE SET "
    "deleted_at = excluded.deleted_at, sync_rev = excluded.sync_rev;"
)


def stamp_rows(conn, table, user_id, where_sql=None, params=None):
    """Stamp the rows of `table` a bulk write is about to change.

    `where_sql` must select exactly the rows the caller's own statement will
    change and must be evaluated BEFORE it runs. Returns the rowcount.
    """
    if user_id is None:
        return 0
    rev = next_rev(conn, user_id)
    sql = (f"UPDATE {table} SET sync_changed_at = :now, sync_rev = :rev "
           f"WHERE user_id = :uid")
    values = {"now": _sql_time(), "rev": rev, "uid": user_id}
    if where_sql:
        sql += f" AND ({where_sql})"
        values.update(params or {})
    return conn.execute(text(sql + ";"), values).rowcount


def tombstone_keys_of(db, table, user_id, keys_query):
    """Tombstone the rows a bulk delete is about to remove.

    `keys_query` is a Query of the natural keys the delete will match, evaluated
    BEFORE it runs, in the same transaction.
    """
    return tombstone_rows(db.connection(), table, user_id,
                          [k for (k,) in keys_query.all() if k])


def stamp_rows_in(conn, table, user_id, column, values, where_sql=None):
    """stamp_rows() for a `column IN (...)` criterion (the bulk enable/disable style)."""
    values = list(values or [])
    if user_id is None or not values:
        return 0
    rev = next_rev(conn, user_id)
    sql = (f"UPDATE {table} SET sync_changed_at = :now, sync_rev = :rev "
           f"WHERE user_id = :uid AND {column} IN :values")
    if where_sql:
        sql += f" AND ({where_sql})"
    return conn.execute(
        text(sql + ";").bindparams(bindparam("values", expanding=True)),
        {"now": _sql_time(), "rev": rev, "uid": user_id, "values": values},
    ).rowcount


def tombstone_rows(conn, table, user_id, keys):
    """Write a tombstone per key for rows deleted in bulk. One rev for the batch."""
    keys = [k for k in (keys or []) if k]
    if user_id is None or not keys:
        return 0
    rev = next_rev(conn, user_id)
    deleted_at = _sql_time()
    for key in keys:
        conn.execute(text(_TOMBSTONE_UPSERT), {
            "uid": user_id, "table_name": table, "record_key": str(key),
            "deleted_at": deleted_at, "rev": rev,
        })
    return len(keys)


def mark_account(conn, user_id, kind):
    """Record a whole-account event ("seeded" / "replaced") as a reserved tombstone.

    A replaced account has no per-record tombstones: the marker tells the other
    instance to drop everything it holds for this account and take what comes.
    """
    if user_id is None:
        raise ValueError("mark_account needs a user id")
    if kind not in ACCOUNT_KINDS:
        raise ValueError(f"unknown account marker {kind!r}")
    rev = next_rev(conn, user_id)
    conn.execute(text(_TOMBSTONE_UPSERT), {
        "uid": user_id, "table_name": ACCOUNT_TABLE, "record_key": kind,
        "deleted_at": _sql_time(), "rev": rev,
    })
    return rev


# --- The ORM listener -------------------------------------------------------

def _user_id_of(obj):
    return getattr(obj, "user_id", None)


def _point_location(point, session):
    """The Location a HorizonPoint belongs to, loaded or fetched by id."""
    location = inspect(point).attrs["location"].value
    if location is not None:
        return location
    if point.location_id is not None:
        return session.get(Location, point.location_id)
    return None


def _derived_only(session, obj):
    """True when the only columns this flush changes are the DERIVED ones.

    A parse of a log cached into log_analysis_cache is derived: it is never
    exported and never compared on the wire, so caching it must leave the rev
    exactly where it was (a derived column carries no change). Reading the row's
    own history rather than is_modified() is what makes this exact - is_modified
    answers "any column moved", which is the question this one has to refine.
    """
    derived = DERIVED_COLUMNS.get(obj.__tablename__)
    if not derived:
        return False
    state = inspect(obj)
    changed = False
    for column in obj.__table__.columns:
        if not state.attrs[column.name].history.has_changes():
            continue
        if column.name not in derived:
            return False          # a real column moved: this is a change
        changed = True
    return changed


def _really_changed(session, obj):
    """True when the flush would write something worth syncing."""
    if _derived_only(session, obj):
        return False
    if isinstance(obj, JournalSession):
        # Adding or removing a project link is a session change even though no
        # column of the row itself moves.
        return session.is_modified(obj, include_collections=True)
    return session.is_modified(obj, include_collections=False)


def _stamp(obj, rev, keep_timestamp):
    if rev is None:
        return
    if not (keep_timestamp and obj.sync_changed_at is not None):
        obj.sync_changed_at = utc_now()
    obj.sync_rev = rev


class _TombstoneCache:
    """The tombstones of the (user, table) pairs one flush touches.

    One SELECT per pair, on first use, instead of one per object: a flush that
    touches a thousand rows of a table loads that table's tombstones once. Rows
    added during the flush are not in here — _upsert_tombstone() dedupes those
    with its own `seen` set.
    """

    def __init__(self, session):
        self._session = session
        self._pairs = {}

    def _pair(self, user_id, table):
        pair = (user_id, table)
        if pair not in self._pairs:
            rows = self._session.execute(
                select(SyncTombstone).where(
                    SyncTombstone.user_id == user_id,
                    SyncTombstone.table_name == table,
                )
            ).scalars().all()
            # UNIQUE(user_id, table_name, record_key): at most one row per key.
            self._pairs[pair] = {row.record_key: row for row in rows}
        return self._pairs[pair]

    def get(self, user_id, table, key):
        """The tombstone row for `key`, or None."""
        return self._pair(user_id, table).get(key)


def _upsert_tombstone(session, user_id, table, key, rev, seen, tombstones):
    if user_id is None or not key:
        return
    tag = (user_id, table, key)
    if tag in seen:
        return
    seen.add(tag)
    existing = tombstones.get(user_id, table, key)
    if existing is not None:
        existing.deleted_at = utc_now()
        existing.sync_rev = rev
    else:
        session.add(SyncTombstone(
            user_id=user_id, table_name=table, record_key=key,
            deleted_at=utc_now(), sync_rev=rev,
        ))


def _drop_tombstone(session, user_id, table, key, tombstones):
    if user_id is None or not key:
        return
    # A row dropped earlier in this flush stays in the cache, exactly as a
    # re-SELECT would still return it: it is only gone once the flush lands.
    row = tombstones.get(user_id, table, key)
    if row is not None:
        session.delete(row)


def _before_flush(session, flush_context, instances):
    new = [o for o in session.new if isinstance(o, SYNC_MODELS) or isinstance(o, HorizonPoint)]
    dirty = [o for o in session.dirty if isinstance(o, SYNC_MODELS) or isinstance(o, HorizonPoint)]
    deleted = [o for o in session.deleted if isinstance(o, SYNC_MODELS) or isinstance(o, HorizonPoint)]
    if not (new or dirty or deleted):
        return

    conn = session.connection()
    keep_timestamp = bool(session.info.get("sync_apply_remote"))
    revs = {}

    def rev_for(user_id):
        """One rev per user per flush: this flush is one change set."""
        if user_id is None:
            return None
        if user_id not in revs:
            revs[user_id] = next_rev(conn, user_id)
        return revs[user_id]

    # Keys this flush creates. A key deleted and re-created in one flush is a
    # replacement, not a deletion: it gets no tombstone.
    created_keys = {
        (_user_id_of(o), o.__tablename__, record_key(o))
        for o in new if not isinstance(o, HorizonPoint) and record_key(o)
    }

    tombstoned_locations = set()
    seen_tombstones = set()
    tombstones = _TombstoneCache(session)
    for obj in deleted:
        if isinstance(obj, HorizonPoint):
            continue
        user_id = _user_id_of(obj)
        key = record_key(obj)
        if key and (user_id, obj.__tablename__, key) not in created_keys:
            _upsert_tombstone(session, user_id, obj.__tablename__, key,
                              rev_for(user_id), seen_tombstones, tombstones)
        if isinstance(obj, Location) and obj.id is not None:
            tombstoned_locations.add(obj.id)

    # A HorizonPoint change is a change of its Location's horizon mask.
    points = [o for o in new + dirty + deleted if isinstance(o, HorizonPoint)]
    bumped = []
    if points:
        for point in points:
            location = _point_location(point, session)
            if location is None or location in bumped:
                continue
            if location.id is not None and location.id in tombstoned_locations:
                continue  # the location itself is being deleted: it has its own tombstone
            bumped.append(location)
        for location in bumped:
            _stamp(location, rev_for(_user_id_of(location)), keep_timestamp)

    for obj in new:
        if isinstance(obj, HorizonPoint):
            continue
        user_id = _user_id_of(obj)
        key = record_key(obj)
        _stamp(obj, rev_for(user_id), keep_timestamp)
        # The key is live again: its tombstone must not survive to delete it.
        _drop_tombstone(session, user_id, obj.__tablename__, key, tombstones)

    for obj in dirty:
        if isinstance(obj, HorizonPoint):
            continue
        if not _really_changed(session, obj):
            continue  # a save that writes nothing must not move the rev
        user_id = _user_id_of(obj)
        rev = rev_for(user_id)
        table = obj.__tablename__
        old_key, new_key = _old_record_key(obj), record_key(obj)
        if old_key and old_key != new_key:
            _upsert_tombstone(session, user_id, table, old_key, rev, seen_tombstones,
                              tombstones)
        _stamp(obj, rev, keep_timestamp)
        if new_key and new_key != old_key:
            _drop_tombstone(session, user_id, table, new_key, tombstones)


def register_sync_tracking():
    """Install the listener once per process. Safe to call more than once."""
    if event.contains(Session, "before_flush", _before_flush):
        return False
    event.listen(Session, "before_flush", _before_flush)
    return True
