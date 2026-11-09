"""Instance sync — merge engine (step 3).

Pure functions that move one account's records between two Nova installations:
one hub (the VPS) and one or more locals. No routes, no network, no UI. Every
entry point takes an open session and returns JSON-serialisable data or plain
objects; nothing here reads a request.

The rules this implements were fixed before the code was written:

    hub is the tie-breaker    equal sync_changed_at -> the hub's copy wins
    newest wins               by sync_changed_at, corrected by offset_seconds
    edit beats delete         a record changed after its delete survives
    no echoes                 a side never sends back what it just received
    all or nothing            one transaction per apply
    mass-delete brake         over MASS_DELETE_RATIO of the account -> refuse
    no schema changes         step 3 adds a column to nothing

Four things are worth reading before changing anything here.

1. Two revs per apply. Every apply assigns R_verbatim for records stored
   exactly as received, then R_changed for anything the receiver altered or
   decided itself (renamed, unified onto another UID, resurrected on a skipped
   delete, links rewritten, ui_prefs blobs merged). R_changed is always the
   higher of the two, so a record the receiver decided about is never
   suppressed and always travels back to the side that sent it.

2. The apply writes through Core statements, never the ORM. That keeps a whole
   snapshot inside gunicorn's timeout (executemany instead of one flush per
   row) and means the step-2 before_flush listener never sees these rows: the
   engine sets sync_changed_at and sync_rev itself, which is what makes the
   two-rev scheme possible at all.

3. Every imported tombstone is re-stamped with a LOCAL rev. A tombstone's
   sync_rev is compared against this instance's sync_counter, so storing a
   peer's rev would make export_changes hand it straight back.

4. A record the receiver already holds is matched by natural key first. Only
   when that misses does the unique value (a name) decide, and a name already
   in use is resolved by unifying (objects) or renaming (everything else) —
   never by dropping the incoming record.

Refusal codes are constants and carry no user-facing text; the UI in a later
step translates them.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, NamedTuple, Optional

from sqlalchemy import and_, func, insert as sa_insert, or_, select, text
from sqlalchemy import delete as sa_delete, update as sa_update
from sqlalchemy.orm import selectinload

from nova.helpers import release_file_lock, try_acquire_file_lock
from nova.models import (
    INSTANCE_PATH,
    AstroObject,
    Component,
    HorizonPoint,
    JournalSession,
    Location,
    Project,
    Rig,
    SavedFraming,
    SavedView,
    SyncState,
    SyncTombstone,
    UiPref,
    UserCustomFilter,
    session_projects,
)
from nova.record_links import empty_uid_rows
from nova.sync_time import utc_now
from nova.sync_tracking import (
    ACCOUNT_KINDS,
    ACCOUNT_TABLE,
    mark_account,
    next_rev,
    record_key,
)

# --- Payload format ----------------------------------------------------------

PAYLOAD_FORMAT = 1

# Reserved payload keys: carried next to a record's columns, never columns.
KEY_CHANGED_AT = "sync_changed_at"
KEY_HORIZON = "horizon_mask"
KEY_PROJECT_IDS = "project_ids"
RESERVED_KEYS = (KEY_CHANGED_AT, KEY_HORIZON, KEY_PROJECT_IDS)

# The one natural key a ui_prefs record carries. Its real key is the user's row
# id, which is instance-local and must not travel.
UI_PREF_KEY = "self"

# --- Tunables ----------------------------------------------------------------

# Refuse a sync whose measured clock offset is larger than this. No clamping:
# a clock this far out is a configuration error, not something to paper over.
MAX_CLOCK_SKEW_SECONDS = 300

# A plan that would delete more than this share of the account's records is
# refused unless the caller overrides it after asking the user.
MASS_DELETE_RATIO = 0.10
MASS_DELETE_MIN_RECORDS = 20

# --- Refusal codes -----------------------------------------------------------

REFUSAL_NOT_PAIRED = "not_paired"
REFUSAL_NEEDS_DOWNLOAD = "needs_download"
REFUSAL_SERVER_UNTOUCHED = "server_untouched"
REFUSAL_LOCAL_REPLACED = "local_replaced"
REFUSAL_REMOTE_REPLACED = "remote_replaced"
REFUSAL_CLOCK_SKEW = "clock_skew"
REFUSAL_SYNC_RUNNING = "sync_running"
REFUSAL_MASS_DELETE = "mass_delete"
REFUSAL_PLAN_STALE = "plan_stale"
REFUSAL_SESSION_NO_KEY = "session_without_external_id"
REFUSAL_MISSING_KEY = "missing_natural_key"
REFUSAL_MISSING_TIMESTAMP = "missing_timestamp"
REFUSAL_UNKNOWN_TABLE = "unknown_table"
REFUSAL_UNKNOWN_COLUMN = "unknown_column"
REFUSAL_UNCLASSIFIED_COLUMN = "unclassified_column"
REFUSAL_BAD_FORMAT = "bad_format"

# --- Action ops --------------------------------------------------------------

OP_INSERT = "insert"
OP_UPDATE = "update"
OP_DELETE = "delete"
OP_TOMBSTONE = "tombstone"

# --- Column classification ---------------------------------------------------

SYNC = "sync"
LOCAL = "local"
FILE = "file"
DERIVED = "derived"

# Every column of every synced model, in exactly one bucket. A column missing
# from all four fails test_every_column_is_classified, which is the guard
# against a new column silently travelling or silently not travelling.
#
#   SYNC     travels, and is written on both sides
#   LOCAL    this installation's own: row ids, the user, the rev, stable_uid
#   FILE     a path or a blob too large to sync; step 4 carries these
#   DERIVED  recomputed from synced columns; never written from a payload
_COLUMN_CLASSES: dict[type, dict[str, tuple[str, ...]]] = {
    Location: {
        SYNC: (
            "record_uid", "name", "lat", "lon", "timezone", "altitude_threshold",
            "is_default", "active", "comments", "bortle_scale", "elevation",
            "sqm_zenith",
        ),
        LOCAL: ("id", "stable_uid", "user_id", "sync_changed_at", "sync_rev"),
        FILE: (),
        DERIVED: (),
    },
    AstroObject: {
        SYNC: (
            "record_uid", "object_name", "common_name", "ra_hours", "dec_deg",
            "type", "constellation", "magnitude", "size", "sb", "active_project",
            "project_name", "is_shared", "shared_notes", "catalog_sources",
            "catalog_info", "enabled", "image_url", "image_credit",
            "image_source_link", "description_text", "description_credit",
            "description_source_link",
        ),
        LOCAL: (
            "id", "user_id", "original_user_id", "original_item_id",
            "sync_changed_at", "sync_rev",
        ),
        FILE: (),
        DERIVED: (),
    },
    Component: {
        SYNC: (
            "record_uid", "kind", "name", "aperture_mm", "focal_length_mm",
            "sensor_width_mm", "sensor_height_mm", "pixel_size_um", "factor",
            "is_shared",
        ),
        LOCAL: (
            "id", "stable_uid", "user_id", "original_user_id", "original_item_id",
            "sync_changed_at", "sync_rev",
        ),
        FILE: (),
        DERIVED: (),
    },
    Rig: {
        SYNC: (
            "record_uid", "rig_name", "active", "effective_focal_length",
            "f_ratio", "image_scale", "fov_w_arcmin", "guide_is_oag",
            "telescope_record_uid", "camera_record_uid",
            "reducer_extender_record_uid", "guide_telescope_record_uid",
            "guide_camera_record_uid", "guide_scope_name",
            "guide_focal_length_mm", "guide_camera_name", "guide_pixel_size_um",
        ),
        LOCAL: (
            "id", "stable_uid", "user_id", "telescope_id", "camera_id",
            "reducer_extender_id", "guide_telescope_id", "guide_camera_id",
            "sync_changed_at", "sync_rev",
        ),
        FILE: (),
        DERIVED: (),
    },
    JournalSession: {
        SYNC: (
            "project_id", "date_utc", "object_name", "notes", "location_name",
            "object_record_uid", "location_record_uid", "rig_record_uid",
            "seeing_observed_fwhm", "sky_sqm_observed",
            "moon_illumination_session", "moon_angular_separation_session",
            "weather_notes", "telescope_setup_notes", "filter_used_session",
            "guiding_rms_avg_arcsec", "guiding_equipment", "dither_details",
            "dither_pixels", "dither_every_n", "dither_notes",
            "acquisition_software", "gain_setting", "offset_setting",
            "camera_temp_setpoint_c", "camera_temp_actual_avg_c",
            "binning_session", "darks_strategy", "flats_strategy",
            "bias_darkflats_strategy", "session_rating_subjective",
            "transparency_observed_scale", "number_of_subs_light",
            "exposure_time_per_sub_sec",
            "filter_L_subs", "filter_L_exposure_sec",
            "filter_R_subs", "filter_R_exposure_sec",
            "filter_G_subs", "filter_G_exposure_sec",
            "filter_B_subs", "filter_B_exposure_sec",
            "filter_Ha_subs", "filter_Ha_exposure_sec",
            "filter_OIII_subs", "filter_OIII_exposure_sec",
            "filter_SII_subs", "filter_SII_exposure_sec",
            "custom_filter_data",
            "rig_name_snapshot", "rig_efl_snapshot", "rig_fr_snapshot",
            "rig_scale_snapshot", "rig_fov_w_snapshot", "rig_fov_h_snapshot",
            "telescope_name_snapshot", "reducer_name_snapshot",
            "camera_name_snapshot", "external_id", "draft",
        ),
        LOCAL: (
            "id", "user_id", "rig_id_snapshot", "rig_stable_uid_snapshot",
            "sync_changed_at", "sync_rev",
        ),
        FILE: (
            "session_image_file", "asiair_log_content", "phd2_log_content",
            "nina_log_content", "log_analysis_cache",
        ),
        DERIVED: ("calculated_integration_time_minutes",),
    },
    Project: {
        SYNC: (
            "id", "name", "target_object_name", "target_object_record_uid",
            "description_notes", "framing_notes", "processing_notes", "goals",
            "status",
        ),
        LOCAL: ("user_id", "sync_changed_at", "sync_rev"),
        FILE: ("final_image_file",),
        DERIVED: (),
    },
    SavedView: {
        SYNC: ("name", "description", "settings_json", "is_shared"),
        LOCAL: (
            "id", "user_id", "original_user_id", "original_item_id",
            "sync_changed_at", "sync_rev",
        ),
        FILE: (),
        DERIVED: (),
    },
    SavedFraming: {
        SYNC: (
            "object_name", "rig_name", "rig_record_uid", "object_record_uid",
            "ra", "dec", "rotation", "survey", "blend_survey", "blend_opacity",
            "mosaic_cols", "mosaic_rows", "mosaic_overlap", "img_brightness",
            "img_contrast", "img_gamma", "img_saturation", "geo_belt_enabled",
            "updated_at",
        ),
        LOCAL: (
            "id", "user_id", "rig_id", "rig_stable_uid", "sync_changed_at",
            "sync_rev",
        ),
        FILE: (),
        DERIVED: (),
    },
    UserCustomFilter: {
        SYNC: ("filter_key", "filter_label", "created_at"),
        LOCAL: ("id", "user_id", "sync_changed_at", "sync_rev"),
        FILE: (),
        DERIVED: (),
    },
    UiPref: {
        SYNC: ("json_blob",),
        LOCAL: ("id", "user_id", "sync_changed_at", "sync_rev"),
        FILE: (),
        DERIVED: (),
    },
}

# The ten tables carrying sync_changed_at / sync_rev, in the order a plan and an
# apply touch them: parents before children, so a link always lands after the
# row it names.
PLAN_ORDER: tuple[type, ...] = (
    AstroObject, Location, Component, Project, Rig, SavedView, SavedFraming,
    UserCustomFilter, UiPref, JournalSession,
)

TABLE_MODEL: dict[str, type] = {m.__tablename__: m for m in PLAN_ORDER}

# Columns SQLite stores as DATE; every other timestamp column is a DATETIME.
_DATE_COLUMNS = frozenset({"date_utc", "created_at", "updated_at"})

# The natural key of each table: what a tombstone carries and what a merge
# matches on. Mirrors record_key() in nova/sync_tracking.py. SavedFraming and
# UiPref are special-cased (see natural_key) and are absent here.
_KEY_COLUMN: dict[type, str] = {
    Location: "record_uid",
    AstroObject: "record_uid",
    Component: "record_uid",
    Rig: "record_uid",
    JournalSession: "external_id",
    Project: "id",
    SavedView: "name",
    UserCustomFilter: "filter_key",
}

# The UNIQUE value that is not the natural key, where one exists. A record that
# arrives under a new identity but with one of these already in use is a
# collision: it is unified (objects) or renamed (the rest), never dropped.
# Components and ui_prefs have no such constraint; a filter's key and a saved
# view's name are their natural keys, so they cannot collide.
UNIQUE_KEY: dict[type, str] = {
    Location: "name",
    AstroObject: "object_name",
    Rig: "rig_name",
    Project: "name",
    SavedFraming: "object_name",
}

# --- Link topology -----------------------------------------------------------

# Who points at whom, as (source model, link column on the source).
_REF_SOURCES: dict[type, tuple[tuple[type, str], ...]] = {
    AstroObject: (
        (JournalSession, "object_record_uid"),
        (SavedFraming, "object_record_uid"),
        (Project, "target_object_record_uid"),
    ),
    Location: ((JournalSession, "location_record_uid"),),
    Rig: (
        (JournalSession, "rig_record_uid"),
        (SavedFraming, "rig_record_uid"),
    ),
    Component: (
        (Rig, "telescope_record_uid"),
        (Rig, "camera_record_uid"),
        (Rig, "reducer_extender_record_uid"),
        (Rig, "guide_telescope_record_uid"),
        (Rig, "guide_camera_record_uid"),
    ),
    Project: ((JournalSession, "project_id"),),
}

# Tables whose deletes can orphan a live row, i.e. that get a reference check.
_PARENT_TABLES = tuple(m.__tablename__ for m in _REF_SOURCES)

# Synthetic link identities, so a column key alone is enough to override one.
_M2M_COLUMN = "__projects__"
_PREF_COLUMN = "__default_location__"

# Where an identity lives, and the text copies other rows keep of it. A rename
# or a unification of the owner rewrites every dependent's UID and text.
_MIRRORS = (
    (AstroObject, "record_uid", "object_name", (
        (JournalSession, "object_record_uid", "object_name"),
        (SavedFraming, "object_record_uid", "object_name"),
        (Project, "target_object_record_uid", "target_object_name"),
    )),
    (Location, "record_uid", "name", (
        (JournalSession, "location_record_uid", "location_name"),
    )),
    (Rig, "record_uid", "rig_name", (
        (JournalSession, "rig_record_uid", "rig_name_snapshot"),
        (SavedFraming, "rig_record_uid", "rig_name"),
    )),
)

_MIRROR_OWNER = {entry[0]: entry for entry in _MIRRORS}


def sync_columns(model: type) -> tuple[str, ...]:
    """The columns of `model` that travel, in model order."""
    return _COLUMN_CLASSES[model][SYNC]


def column_class(model: type, column: str) -> Optional[str]:
    """The bucket `column` falls in for `model`, or None if unclassified."""
    for bucket, names in _COLUMN_CLASSES[model].items():
        if column in names:
            return bucket
    return None


def all_classified_columns(model: type) -> frozenset[str]:
    """Every column name listed for `model`, whatever its bucket."""
    return frozenset(
        name for names in _COLUMN_CLASSES[model].values() for name in names
    )


# --- Timestamps --------------------------------------------------------------

def to_iso(value: Any) -> Any:
    """A datetime/date as ISO 8601 text; anything else unchanged."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def parse_datetime(value: Any) -> Optional[datetime]:
    """ISO 8601 text to a naive UTC datetime. A datetime passes through."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "")
    if len(text) == 10:
        return datetime.strptime(text, "%Y-%m-%d")
    if "." not in text:
        text += ".000000"
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f")


def parse_date(value: Any) -> Optional[date]:
    """ISO 8601 text to a date. A date/datetime passes through."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def shift(moment: Optional[datetime], offset_seconds: float) -> Optional[datetime]:
    """`moment` moved into this instance's clock by the measured offset.

    offset_seconds is THIS instance's clock minus the peer's, so it is ADDED to
    a timestamp that came from the peer: a local running 200 s fast measures
    +200, and a hub stamp is moved forward by that much before the two are
    compared. A local stamp is already in this clock and is never shifted.
    Applied identically on both sides, which is what makes "newest" mean the
    same thing everywhere.
    """
    if moment is None or not offset_seconds:
        return moment
    return moment + timedelta(seconds=offset_seconds)


# --- Natural keys ------------------------------------------------------------

def natural_key(model: type, values: Mapping[str, Any]) -> str:
    """The natural key a payload record carries. Empty string when it has none."""
    if model is UiPref:
        return UI_PREF_KEY
    if model is SavedFraming:
        uid = str(values.get("object_record_uid") or "").strip()
        return uid or str(values.get("object_name") or "").strip()
    column = _KEY_COLUMN[model]
    value = values.get(column)
    return str(value).strip() if value is not None else ""


def row_key(model: type, row: Any) -> str:
    """The natural key an ORM row carries. The same rule as natural_key()."""
    if model is UiPref:
        return UI_PREF_KEY
    return record_key(row)


# --- Refusals ----------------------------------------------------------------

class Refusal(NamedTuple):
    """Why something was refused. `code` is a constant; the UI translates it."""

    code: str
    detail: dict


class SyncRefused(Exception):
    """Raised by the export/apply entry points when a plan may not proceed."""

    def __init__(self, refusal: Refusal):
        super().__init__(f"{refusal.code}: {refusal.detail}")
        self.refusal = refusal


# --- Plan objects ------------------------------------------------------------

class Renamed(NamedTuple):
    table: str
    key: str
    field: str
    old: str
    new: str


class Unified(NamedTuple):
    table: str
    loser: str
    winner: str
    name: str


class Resurrected(NamedTuple):
    table: str
    key: str
    reason: str


class Conflict(NamedTuple):
    table: str
    key: str
    resolution: str
    detail: dict


class Totals(NamedTuple):
    records: int
    deletions: int
    ratio: float


@dataclass
class Action:
    """One write an apply will perform, exactly as planned."""

    op: str
    table: str
    key: str
    changed: bool
    values: dict
    row_id: Optional[int] = None
    # The row's own identity before the apply: {column: value}. Empty on insert.
    before: dict = field(default_factory=dict)
    # The links the row held before the apply: {(table, column): value}.
    links: dict = field(default_factory=dict)


@dataclass
class MergePlan:
    side: str
    user_id: Optional[int] = None
    actions: list = field(default_factory=list)
    created: dict = field(default_factory=dict)
    updated: dict = field(default_factory=dict)
    deleted: dict = field(default_factory=dict)
    renames: list = field(default_factory=list)
    unified: list = field(default_factory=list)
    resurrected: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    refusals: list = field(default_factory=list)
    totals: Totals = Totals(0, 0, 0.0)
    fingerprint: str = ""
    touched: list = field(default_factory=list)
    inserts: list = field(default_factory=list)
    ref_counts: dict = field(default_factory=dict)
    # (table, key) records this plan could not place yet. The apply reports
    # them back so the caller does not move its watermark past them.
    deferred: list = field(default_factory=list)

    @property
    def has_verbatim(self) -> bool:
        return any(not a.changed for a in self.actions)

    @property
    def has_changed(self) -> bool:
        return any(a.changed for a in self.actions)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def summary(self) -> dict:
        """A JSON-safe description of the plan, for an Apply/Abort dialog."""
        return {
            "side": self.side,
            "created": dict(self.created),
            "updated": dict(self.updated),
            "deleted": dict(self.deleted),
            "renames": [r._asdict() for r in self.renames],
            "unified": [u._asdict() for u in self.unified],
            "resurrected": [r._asdict() for r in self.resurrected],
            "conflicts": [c._asdict() for c in self.conflicts],
            "refusals": [r._asdict() for r in self.refusals],
            "totals": self.totals._asdict(),
        }


@dataclass
class MergeResult:
    created: dict = field(default_factory=dict)
    updated: dict = field(default_factory=dict)
    deleted: dict = field(default_factory=dict)
    resurrected: list = field(default_factory=list)
    skipped_at_apply: list = field(default_factory=list)
    r_verbatim: Optional[int] = None
    r_changed: Optional[int] = None
    # The counter as it stood before this apply bumped it. record_pull_applied
    # compares it with the rev the export carried to tell whether a local write
    # slipped in between the two.
    counter_before: Optional[int] = None
    # The rev the receiver wrote records verbatim under. A hub hands it back to
    # the pusher, which excludes it from its next pull: a side never receives
    # what it just sent, but everything the receiver decided still travels.
    push_revs: list = field(default_factory=list)
    # (table, key) records this apply could not place. record_push_accepted
    # leaves the watermark alone when it is non-empty, so they are re-offered.
    deferred: list = field(default_factory=list)


@dataclass
class DownloadPlan:
    snapshot: dict
    server_rev: int
    user_id: Optional[int] = None
    wiping: dict = field(default_factory=dict)
    inserting: dict = field(default_factory=dict)
    refusals: list = field(default_factory=list)
    fingerprint: str = ""

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


# --- Payload helpers ---------------------------------------------------------

def _record_values(model: type, record: Mapping[str, Any]) -> dict:
    """The column values a payload record carries, parsed back to Python.

    The two sets that are not columns — a location's horizon mask and a
    session's project ids — ride along under their reserved keys, because the
    apply writes them beside the row.
    """
    values: dict[str, Any] = {}
    for column in sync_columns(model):
        if column not in record:
            continue
        raw = record[column]
        values[column] = parse_date(raw) if column in _DATE_COLUMNS else raw
    for reserved in (KEY_HORIZON, KEY_PROJECT_IDS):
        if reserved in record:
            values[reserved] = record[reserved]
    # Carried so a write stored exactly as received keeps the sender's stamp:
    # the plan replaces this raw text with the offset-corrected datetime.
    if KEY_CHANGED_AT in record:
        values[KEY_CHANGED_AT] = record[KEY_CHANGED_AT]
    return values


def record_to_payload(model: type, row: Any) -> dict:
    """One row as a payload record: synced columns, links as UIDs, plus sets."""
    out: dict[str, Any] = {}
    for column in sync_columns(model):
        out[column] = to_iso(getattr(row, column))
    out[KEY_CHANGED_AT] = to_iso(row.sync_changed_at)
    if model is Location:
        out[KEY_HORIZON] = [
            [point.az_deg, point.alt_min_deg]
            for point in sorted(row.horizon_points, key=lambda p: p.az_deg)
        ]
    elif model is JournalSession:
        out[KEY_PROJECT_IDS] = sorted(project.id for project in row.projects)
    return out


def _tombstone_to_payload(tombstone: SyncTombstone) -> dict:
    key = tombstone.record_key
    if tombstone.table_name == UiPref.__tablename__:
        key = UI_PREF_KEY
    return {
        "table": tombstone.table_name,
        "key": key,
        "deleted_at": to_iso(tombstone.deleted_at),
    }


def counter_rev(db, user_id) -> int:
    """This user's sync counter, or 0 before the first change."""
    row = db.execute(
        text("SELECT rev FROM sync_counter WHERE user_id = :uid;"), {"uid": user_id}
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _envelope(db, user, *, side: str, snapshot: bool) -> dict:
    return {
        "format": PAYLOAD_FORMAT,
        "account": user.username,
        "instance": os.environ.get("INSTANCE_ID", ""),
        "side": side,
        "rev": counter_rev(db, user.id),
        "snapshot": snapshot,
        "records": {},
        "tombstones": [],
    }


def _load_rows(db, model: type, user_id: int) -> list:
    query = select(model).where(model.user_id == user_id)
    if model is Location:
        query = query.options(selectinload(Location.horizon_points))
    elif model is JournalSession:
        query = query.options(selectinload(JournalSession.projects))
    return list(db.scalars(query))


def _prefs_settings(blob) -> dict:
    """A ui_prefs blob as a dict. A missing or unreadable blob reads as {}."""
    if not blob:
        return {}
    try:
        settings = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _blob_default_location(blob) -> Optional[str]:
    """The location name a ui_prefs blob names as default, or None."""
    name = _prefs_settings(blob).get("default_location")
    return str(name) if name else None


# --- Export ------------------------------------------------------------------

def export_changes(db, user, since_rev: int, *, side: str = "local",
                   exclude_revs: Iterable[int] = ()) -> dict:
    """Everything this instance has changed for `user` above `since_rev`.

    Rows and tombstones with sync_rev > since_rev. Account markers ride only in
    a hub's payload: a local never pushes one.

    exclude_revs are revs the caller must not be handed back. A hub fills it
    with the revs it wrote verbatim for the pusher (MergeResult.push_revs), so
    the pull that follows leaves out exactly what the pusher just sent while
    everything the hub decided about those records still travels. A side never
    receives what it just sent.
    """
    uid = user.id
    payload = _envelope(db, user, side=side, snapshot=False)
    excluded = sorted({int(rev) for rev in exclude_revs if rev is not None})

    for model in PLAN_ORDER:
        query = select(model).where(model.user_id == uid,
                                    model.sync_rev > since_rev)
        if excluded:
            query = query.where(model.sync_rev.notin_(excluded))
        rows = db.scalars(query).all()
        if rows:
            payload["records"][model.__tablename__] = [
                record_to_payload(model, row) for row in rows
            ]

    payload["tombstones"] = [
        _tombstone_to_payload(t)
        for t in _visible_tombstones(db, uid, side, since_rev, excluded)
    ]
    return payload


def export_snapshot(db, user, *, side: str = "server") -> dict:
    """The whole account, for a Download from server.

    A hub refuses while it holds nothing beyond its own starter data: handing
    that out would replace a local's real records with template records.
    """
    uid = user.id
    if side == "server" and server_untouched(db, uid):
        raise SyncRefused(Refusal(
            REFUSAL_SERVER_UNTOUCHED,
            {"user_id": uid, "seeded_rev": account_marker_rev(db, uid, "seeded")},
        ))

    payload = _envelope(db, user, side=side, snapshot=True)
    # The rev this snapshot was taken at: the local stores it as its cursor, so
    # the next Sync now asks the hub only for what happened after this point.
    payload["server_rev"] = payload["rev"]
    for model in PLAN_ORDER:
        rows = _load_rows(db, model, uid)
        if rows:
            payload["records"][model.__tablename__] = [
                record_to_payload(model, row) for row in rows
            ]

    payload["tombstones"] = [
        _tombstone_to_payload(t) for t in _visible_tombstones(db, uid, side, None)
    ]
    return payload


def _visible_tombstones(db, user_id, side, since_rev, exclude_revs=()):
    """The tombstones this side may show: a local's never include markers."""
    query = select(SyncTombstone).where(SyncTombstone.user_id == user_id)
    if since_rev is not None:
        query = query.where(SyncTombstone.sync_rev > since_rev)
    excluded = [int(rev) for rev in exclude_revs if rev is not None]
    if excluded:
        query = query.where(SyncTombstone.sync_rev.notin_(excluded))
    rows = db.scalars(query).all()
    if side == "server":
        return rows
    return [t for t in rows if t.table_name != ACCOUNT_TABLE]


def account_marker_rev(db, user_id, kind: str) -> int:
    """sync_rev of the newest account marker of `kind`, or 0."""
    row = db.scalars(
        select(SyncTombstone)
        .where(
            SyncTombstone.user_id == user_id,
            SyncTombstone.table_name == ACCOUNT_TABLE,
            SyncTombstone.record_key == kind,
        )
        .order_by(SyncTombstone.sync_rev.desc())
    ).first()
    return int(row.sync_rev) if row is not None and row.sync_rev else 0


def server_untouched(db, user_id) -> bool:
    """True when the account holds nothing above its newest "seeded" marker."""
    threshold = account_marker_rev(db, user_id, "seeded")
    for model in PLAN_ORDER:
        found = db.scalar(
            select(func.count()).select_from(model).where(
                model.user_id == user_id, model.sync_rev > threshold
            )
        )
        if found:
            return False
    found = db.scalar(
        select(func.count()).select_from(SyncTombstone).where(
            SyncTombstone.user_id == user_id,
            SyncTombstone.sync_rev > threshold,
        )
    )
    return not found


# --- Pairing, lock, preconditions -------------------------------------------

def start_pairing(db, user, *, server_url: str, token: str = None,
                  account: str = None) -> SyncState:
    """Create or re-arm this installation's pairing with a hub.

    Re-pairing clears last_sync_at, which is what re-arms the download lock: a
    fresh pairing must download before it may push.
    """
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None:
        state = SyncState(user_id=user.id)
        db.add(state)
    state.server_url = server_url
    state.token = token
    state.server_account = account
    state.server_cursor = 0
    state.local_pushed_rev = 0
    state.paired_at = utc_now()
    state.last_sync_at = None
    db.flush()
    return state


@contextmanager
def sync_lock(user_id: int, instance_path: str = None):
    """Hold this user's sync lock for the duration of the block.

    One OS advisory lock per user in the instance folder, the same approach as
    ensure_db_initialized_unified, so two gunicorn workers cannot sync the same
    account at once. Non-blocking: a second holder is refused with sync_running
    rather than queued behind the first.
    """
    base = instance_path or INSTANCE_PATH
    handle = try_acquire_file_lock(os.path.join(base, f"sync_user_{user_id}"))
    if handle is None:
        raise SyncRefused(Refusal(REFUSAL_SYNC_RUNNING, {"user_id": user_id}))
    try:
        yield
    finally:
        release_file_lock(handle)


def check_sync_preconditions(db, user, *, side: str = "local") -> Optional[Refusal]:
    """What stops "Sync now" before a plan is even built. None when clear."""
    if side != "local":
        return None
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None or not state.server_url:
        return Refusal(REFUSAL_NOT_PAIRED, {"user_id": user.id})

    # Download lock: a paired local downloads before it pushes. A freshly
    # provisioned account is seeded from templates and those rows are stamped
    # at provisioning time, so pushing them could beat the hub's real data on
    # timestamps. A completed download is what sets last_sync_at.
    if state.last_sync_at is None:
        return Refusal(REFUSAL_NEEDS_DOWNLOAD, {
            "user_id": user.id,
            "seeded_rev": account_marker_rev(db, user.id, "seeded"),
        })

    # A local YAML replace import writes a "replaced" marker and no per-record
    # tombstones, so until that marker has been pushed the local's history is
    # not a delta anything can merge. A Download from server clears it: the
    # download's own marker sits below the local_pushed_rev it leaves behind.
    marker_rev = account_marker_rev(db, user.id, "replaced")
    if marker_rev > (state.local_pushed_rev or 0):
        return Refusal(REFUSAL_LOCAL_REPLACED, {
            "marker_rev": marker_rev,
            "local_pushed_rev": state.local_pushed_rev or 0,
        })
    return None


# --- Link bookkeeping --------------------------------------------------------

def _row_links(model: type, row) -> dict:
    """The links `row` holds, as {(target table, column): value}."""
    links: dict[tuple[str, str], Any] = {}
    for target_model, sources in _REF_SOURCES.items():
        for source_model, column in sources:
            if source_model is not model:
                continue
            value = getattr(row, column, None)
            if value:
                links[(target_model.__tablename__, column)] = str(value)
    if model is JournalSession:
        for project in row.projects:
            links[(Project.__tablename__, f"{_M2M_COLUMN}:{project.id}")] = str(project.id)
    if model is UiPref:
        name = _blob_default_location(row.json_blob)
        if name:
            links[(Location.__tablename__, _PREF_COLUMN)] = name
    return links


def _values_links(model: type, values: Mapping[str, Any]) -> dict:
    """The links a planned write adds or replaces, the same shape as _row_links."""
    links: dict[tuple[str, str], Any] = {}
    for target_model, sources in _REF_SOURCES.items():
        for source_model, column in sources:
            if source_model is not model or column not in values:
                continue
            value = values[column]
            if value:
                links[(target_model.__tablename__, column)] = str(value)
    if model is JournalSession and KEY_PROJECT_IDS in values:
        for pid in values[KEY_PROJECT_IDS] or []:
            links[(Project.__tablename__, f"{_M2M_COLUMN}:{pid}")] = str(pid)
    if model is UiPref and "json_blob" in values:
        name = _blob_default_location(values["json_blob"])
        if name:
            links[(Location.__tablename__, _PREF_COLUMN)] = name
    return links


def _pair_of(link: tuple[tuple[str, str], Any]) -> tuple[str, str]:
    """A link, as _row_links and _values_links write them, as a (table, key).

    A link is {(target table, column or marker): value}, and the *value* is
    what the delete check asks about — the referenced record's key — in every
    shape: a scalar UID column, a project id inside the m2m marker, or the
    location name ui_prefs keeps in its blob.
    """
    (table, _column), value = link
    return table, str(value)


def _grouped_reference_counts(db, user_id: int, deletes: Iterable[tuple[str, str]]):
    """Live references to each (table, key) about to be deleted, from the DB.

    Returns {(table, key): count}. One grouped query per source column, so a
    batch of deletes costs a fixed number of queries rather than one each.
    """
    wanted: dict[str, set] = {}
    for table, key in deletes:
        wanted.setdefault(table, set()).add(key)
    if not wanted:
        return {}

    counts: dict[tuple[str, str], int] = {}

    def add(source_model, column, target_table):
        keys = wanted.get(target_table)
        if not keys:
            return
        col = getattr(source_model, column)
        rows = db.execute(
            select(col, func.count()).where(
                source_model.user_id == user_id, col.in_(sorted(keys))
            ).group_by(col)
        ).all()
        for value, number in rows:
            if value:
                pair = (target_table, str(value))
                counts[pair] = counts.get(pair, 0) + number

    for target_model, sources in _REF_SOURCES.items():
        table = target_model.__tablename__
        if table not in wanted:
            continue
        for source_model, column in sources:
            add(source_model, column, table)

    # session_projects rows, which are not a model. Scoped to this user's own
    # sessions: another account's session pointing at the same project id is
    # not a reference this account's delete would orphan.
    project_keys = wanted.get(Project.__tablename__)
    if project_keys:
        rows = db.execute(
            select(session_projects.c.project_id, func.count())
            .where(
                session_projects.c.project_id.in_(sorted(project_keys)),
                session_projects.c.session_id.in_(
                    select(JournalSession.id).where(
                        JournalSession.user_id == user_id)),
            )
            .group_by(session_projects.c.project_id)
        ).all()
        for value, number in rows:
            if value:
                pair = (Project.__tablename__, str(value))
                counts[pair] = counts.get(pair, 0) + number

    # A location is also named by ui_prefs' default_location, text inside a blob.
    location_keys = wanted.get(Location.__tablename__)
    if location_keys:
        name = _blob_default_location(_user_prefs_blob(db, user_id))
        if name and name in location_keys:
            pair = (Location.__tablename__, name)
            counts[pair] = counts.get(pair, 0) + 1

    return counts


def _user_prefs_blob(db, user_id) -> Optional[str]:
    """The user's ui_prefs json_blob, or None."""
    return db.scalar(select(UiPref.json_blob).where(UiPref.user_id == user_id))


def _post_plan_reference_counts(db, user_id, builder) -> dict:
    """References that will remain, per (table, key), once the plan is applied.

    The live count already includes every reference an updated or deleted row
    holds, so those come off, and every reference an inserted or updated row
    will hold goes on. This is the same arithmetic the plan's delete check and
    the apply's re-check both need.
    """
    deletes = [
        (a.table, a.key) for a in builder.actions
        if a.op == OP_DELETE and a.table in _PARENT_TABLES
    ]
    counts = dict(_grouped_reference_counts(db, user_id, deletes))

    for action in builder.actions:
        model = TABLE_MODEL.get(action.table)
        if model is None:
            continue
        if action.op in (OP_UPDATE, OP_DELETE):
            for link in action.links.items():
                pair = _pair_of(link)
                counts[pair] = counts.get(pair, 0) - 1
        if action.op in (OP_INSERT, OP_UPDATE):
            for link in _values_links(model, action.values).items():
                pair = _pair_of(link)
                counts[pair] = counts.get(pair, 0) + 1
    return counts


# --- Planning ----------------------------------------------------------------

def _validate_payload(payload, *, side: Optional[str] = None,
                      allow_snapshot: bool = False) -> list:
    """Structural checks on an incoming payload. Empty list when it is usable.

    `side` is the side RECEIVING it. On the hub that means a local sent it, and
    a local sends neither a snapshot (a whole account only ever travels as a
    download, which wipes first) nor an account marker — _visible_tombstones
    strips markers out of every payload a local builds, so one arriving here was
    not something this engine produced, and a peer must not be able to declare
    the account replaced. Both are refused, "replaced" included.

    The other direction is deliberately unchecked: a hub does send the markers
    in its own history, and _plan_tombstone decides what each kind means to a
    local. allow_snapshot is for the one caller that receives a real snapshot,
    plan_download.
    """
    refusals: list[Refusal] = []
    if not isinstance(payload, dict):
        return [Refusal(REFUSAL_BAD_FORMAT, {"reason": "not a mapping"})]
    if payload.get("format") != PAYLOAD_FORMAT:
        return [Refusal(REFUSAL_BAD_FORMAT, {"found": payload.get("format")})]

    if side == "server" and not allow_snapshot and payload.get("snapshot"):
        refusals.append(Refusal(REFUSAL_BAD_FORMAT, {"reason": "snapshot in a push"}))

    records = payload.get("records") or {}
    if not isinstance(records, dict):
        return [Refusal(REFUSAL_BAD_FORMAT, {"reason": "records is not a mapping"})]
    for table, rows in records.items():
        model = TABLE_MODEL.get(table)
        if model is None:
            refusals.append(Refusal(REFUSAL_UNKNOWN_TABLE, {"table": table}))
            continue
        allowed = set(sync_columns(model)) | set(RESERVED_KEYS)
        for row in rows or []:
            if not isinstance(row, dict):
                refusals.append(Refusal(REFUSAL_BAD_FORMAT, {"table": table}))
                continue
            for name in row:
                if name in allowed:
                    continue
                code = (REFUSAL_UNCLASSIFIED_COLUMN
                        if column_class(model, name) is not None
                        else REFUSAL_UNKNOWN_COLUMN)
                refusals.append(Refusal(code, {"table": table, "column": name}))

    for tombstone in payload.get("tombstones") or []:
        if not isinstance(tombstone, dict) or not tombstone.get("table"):
            refusals.append(Refusal(REFUSAL_BAD_FORMAT, {"reason": "bad tombstone"}))
            continue
        table = tombstone["table"]
        if side == "server" and table == ACCOUNT_TABLE:
            refusals.append(Refusal(REFUSAL_BAD_FORMAT, {
                "reason": "account marker", "key": tombstone.get("key")}))
            continue
        if table != ACCOUNT_TABLE and table not in TABLE_MODEL:
            refusals.append(Refusal(REFUSAL_UNKNOWN_TABLE, {"table": table}))
    return refusals


def free_name(taken: set, base: str) -> str:
    """`base` with the first free " (n)" suffix. Never returns a taken name."""
    if base not in taken:
        return base
    number = 2
    while f"{base} ({number})" in taken:
        number += 1
    return f"{base} ({number})"


def _identity_values(model: type, row) -> dict:
    """The identity columns of `row`, as they stand before an apply."""
    out: dict[str, Any] = {}
    key_column = _KEY_COLUMN.get(model)
    if key_column:
        out[key_column] = getattr(row, key_column, None)
    unique_column = UNIQUE_KEY.get(model)
    if unique_column:
        out[unique_column] = getattr(row, unique_column, None)
    return out


def _row_content(db, model: type, row, record: Mapping[str, Any]) -> bool:
    """True when `row` already holds exactly what `record` carries.

    Synced content only. sync_changed_at is not a column of any model, so it
    never enters this comparison: a record that comes back with a stamp a
    re-measured offset has nudged is still the same record, and equal content
    means no write and no rev whatever the two stamps say.
    """
    for column, value in _record_values(model, record).items():
        if column in RESERVED_KEYS:
            continue  # sets, compared below against the rows that hold them
        current = getattr(row, column, None)
        if isinstance(current, (date, datetime)) and isinstance(value, (date, datetime)):
            if current != value:
                return False
            continue
        if current != value:
            return False
    if model is Location:
        incoming = [[float(p[0]), float(p[1])] for p in (record.get(KEY_HORIZON) or [])]
        current = [[p.az_deg, p.alt_min_deg]
                   for p in sorted(row.horizon_points, key=lambda p: p.az_deg)]
        if incoming != current:
            return False
    elif model is JournalSession:
        if sorted(record.get(KEY_PROJECT_IDS) or []) != sorted(
                project.id for project in row.projects):
            return False
    return True


def _same_content(db, model: type, row, values: Mapping[str, Any]) -> bool:
    """Whether `row` already carries `values`, ignoring the columns that name it.

    The same rule as _row_content, for the place where two identities have
    already been decided to be one record (a name collision): the key column
    differs by definition there, so what is left to compare is the content.
    """
    key_column = _KEY_COLUMN.get(model)
    for column, value in values.items():
        if column in RESERVED_KEYS or column == key_column:
            continue
        if getattr(row, column, None) != value:
            return False
    return True


class _Builder:
    """Collects the actions and the counts of one plan."""

    def __init__(self, side: str, user_id: int, offset_seconds: float = 0.0):
        self.side = side
        self.user_id = user_id
        self.offset_seconds = offset_seconds
        self.deferred: list[tuple[str, str]] = []
        self.actions: list[Action] = []
        self.by_key: dict[tuple[str, str], Action] = {}
        self.created: dict[str, int] = {}
        self.updated: dict[str, int] = {}
        self.deleted: dict[str, int] = {}
        self.renames: list[Renamed] = []
        self.unified: list[Unified] = []
        self.resurrected: list[Resurrected] = []
        self.conflicts: list[Conflict] = []
        self.touched: list[tuple] = []
        self.inserts: list[tuple] = []
        self.remap: dict[str, str] = {}     # retired object uid -> surviving uid
        self.taken: dict[type, set] = {}    # unique values in use, per model

    def add(self, action: Action) -> None:
        self.actions.append(action)
        self.by_key[(action.table, action.key)] = action

    def to_local_clock(self, moment: Optional[datetime]) -> Optional[datetime]:
        """A peer's timestamp, moved into this instance's clock.

        The one place the offset is applied on the plan side: every comparison
        between an incoming stamp and a stored one goes through here, so
        "newest" means the same thing on both sides. None passes through (the
        caller refuses a record without a stamp).
        """
        return shift(moment, self.offset_seconds)

    def rekey(self, action: Action, new_key: str) -> None:
        """A dependent whose natural key changed under it (a framing's object)."""
        self.by_key.pop((action.table, action.key), None)
        action.key = new_key
        self.by_key[(action.table, new_key)] = action

    def remember(self, model: type, row) -> None:
        """Note an existing row the plan read, for the staleness fingerprint."""
        self.touched.append(
            (model.__tablename__, row.id, row.sync_rev, row.sync_changed_at)
        )

    def count_created(self, table: str) -> None:
        self.created[table] = self.created.get(table, 0) + 1

    def count_updated(self, table: str) -> None:
        self.updated[table] = self.updated.get(table, 0) + 1

    def count_deleted(self, table: str) -> None:
        self.deleted[table] = self.deleted.get(table, 0) + 1


def _plan_links_to(db, builder: _Builder, owner_model: type, old_uid, old_name,
                   new_uid, new_name, index: dict) -> None:
    """Rewrite every dependent of an identity that moved or was renamed.

    A dependent is a row of another table whose UID equals `old_uid`, or whose
    UID is empty and whose name column equals `old_name` exactly (the same rule
    empty_uid_rows uses). Its UID becomes `new_uid` and its text `new_name`. The
    receiver decided this, so the write is a changed one and travels back.
    """
    owner = _MIRROR_OWNER[owner_model]
    for dep_model, dep_uid_col, dep_name_col in owner[3]:
        dep_table = dep_model.__tablename__
        uid_attr = getattr(dep_model, dep_uid_col)
        name_attr = getattr(dep_model, dep_name_col)
        rows = empty_uid_rows(db, builder.user_id, dep_model, uid_attr, name_attr,
                              old_uid, old_name)
        for row in rows:
            key = row_key(dep_model, row)
            action = builder.by_key.get((dep_table, key))
            changes = {dep_uid_col: new_uid, dep_name_col: new_name}
            if action is not None:
                action.values.update(changes)
                action.changed = True
            else:
                builder.remember(dep_model, row)
                action = Action(
                    OP_UPDATE, dep_table, key, True, changes, row.id,
                    _identity_values(dep_model, row), _row_links(dep_model, row),
                )
                builder.add(action)
                builder.count_updated(dep_table)
            if dep_model is SavedFraming and dep_uid_col == "object_record_uid":
                # A framing is keyed by its object: its key moved with the UID.
                moved = index.get(dep_table, {}).pop(key, None)
                if moved is not None:
                    index[dep_table][new_uid] = moved
                builder.rekey(action, new_uid)

    if owner_model is Location and new_name and old_name and new_name != old_name:
        # ui_prefs names its default location as text inside its blob: the
        # location equivalent of repoint_object_links' session mirror.
        _plan_pref_location(db, builder, old_name, new_name)


def _plan_pref_location(db, builder: _Builder, old_name: str,
                        new_name: str) -> None:
    """Repoint ui_prefs' default_location at a location's new name.

    The blob is a synced column, so this is a changed write: the receiver
    decided it, and it travels back to the side that sent the rename.
    """
    table = UiPref.__tablename__
    row = db.scalars(select(UiPref).where(UiPref.user_id == builder.user_id)).first()
    if row is None or _blob_default_location(row.json_blob) != old_name:
        return
    settings = _prefs_settings(row.json_blob)
    settings["default_location"] = new_name
    blob = json.dumps(settings, ensure_ascii=False)
    action = builder.by_key.get((table, UI_PREF_KEY))
    if action is not None:
        action.values["json_blob"] = blob
        action.changed = True
        return
    builder.remember(UiPref, row)
    builder.add(Action(OP_UPDATE, table, UI_PREF_KEY, True, {"json_blob": blob},
                       row.id, _identity_values(UiPref, row),
                       _row_links(UiPref, row)))
    builder.count_updated(table)


def _plan_object_collision(db, builder: _Builder, model: type, record: dict,
                           values: dict, key: str, clash, index: dict) -> None:
    """An object arrives under a UID the receiver does not have, but its name is
    already in use. Unify onto the identity the hub holds.

    On the hub (side="server") the hub's own row is the hub's identity, so the
    hub keeps it and the incoming UID is retired. On a local (side="local") the
    incoming UID is the hub's identity, so the local's row moves onto it. Both
    sides end up on the same UID, which is what makes the next sync a no-op.
    """
    hub_side = builder.side == "server"
    winner_row = clash if hub_side else None
    winner_uid = clash.record_uid if hub_side else key
    loser_uid = key if hub_side else clash.record_uid
    winner_name = clash.object_name if hub_side else values["object_name"]

    builder.unified.append(
        Unified(model.__tablename__, loser_uid, winner_uid, winner_name))
    builder.conflicts.append(Conflict(
        model.__tablename__, key, "unified",
        {"loser": loser_uid, "winner": winner_uid, "name": winner_name},
    ))
    builder.remap[loser_uid] = winner_uid

    if hub_side:
        # The hub keeps its own row, adopting the incoming content when it is
        # newer. Retire the incoming UID so both sides forget it. "Newer" is
        # only half of it: content that already matches is not a change, however
        # far a re-measured offset has moved the incoming stamp.
        incoming_at = builder.to_local_clock(
            parse_datetime(record.get(KEY_CHANGED_AT)))
        if (incoming_at and incoming_at > (winner_row.sync_changed_at or datetime.min)
                and not _same_content(db, model, winner_row, values)):
            merged = dict(values)
            merged.pop("record_uid", None)
            builder.remember(model, winner_row)
            builder.add(Action(OP_UPDATE, model.__tablename__, winner_uid, True,
                               merged, winner_row.id,
                               _identity_values(model, winner_row),
                               _row_links(model, winner_row)))
            builder.count_updated(model.__tablename__)
        builder.add(Action(OP_TOMBSTONE, model.__tablename__, loser_uid, True, {}))
        return

    # A local adopts the hub's identity: move its row, then repoint everything
    # that named it. The repoint runs now, not at the end, so a framing that
    # arrives later under the surviving UID is matched by identity.
    builder.remember(model, clash)
    builder.add(Action(
        OP_UPDATE, model.__tablename__, loser_uid, True, {"record_uid": winner_uid},
        clash.id, _identity_values(model, clash), _row_links(model, clash),
    ))
    builder.count_updated(model.__tablename__)
    builder.add(Action(OP_TOMBSTONE, model.__tablename__, loser_uid, True, {}))
    index.get(model.__tablename__, {}).pop(loser_uid, None)
    index.setdefault(model.__tablename__, {})[winner_uid] = clash
    _plan_links_to(db, builder, model, loser_uid, clash.object_name,
                   winner_uid, winner_name, index)


def _merge_prefs_blob(local_blob, incoming_blob, incoming_newer: bool):
    """Merge two ui_prefs blobs. Returns (json text, whether it differs).

    A union of top-level keys: a key only one side has is kept, and a key both
    sides have goes to the newer record. Newest-wins on the whole blob would
    throw away the other side's preferences, which is why this is a merge and
    therefore a decision the receiver made (a changed write).
    """
    local = _prefs_settings(local_blob)
    incoming = _prefs_settings(incoming_blob)
    merged = dict(incoming) if incoming_newer else dict(local)
    other = local if incoming_newer else incoming
    for key, value in other.items():
        merged.setdefault(key, value)
    text = json.dumps(merged, ensure_ascii=False)
    return text, text != (local_blob or "")


def _plan_record(db, builder: _Builder, model: type, record: dict, index: dict,
                 unique_index: dict, refusals: list) -> None:
    """Decide what one incoming record does to the receiver."""
    table = model.__tablename__

    key = natural_key(model, record)
    if not key:
        code = REFUSAL_SESSION_NO_KEY if model is JournalSession else REFUSAL_MISSING_KEY
        refusals.append(Refusal(code, {"table": table}))
        return

    incoming_at = builder.to_local_clock(parse_datetime(record.get(KEY_CHANGED_AT)))
    if incoming_at is None:
        refusals.append(Refusal(REFUSAL_MISSING_TIMESTAMP,
                                {"table": table, "key": key}))
        return

    values = _record_values(model, record)
    # A record stored exactly as received keeps the sender's stamp, so a third
    # copy rates it against the same moment the other two did.
    values[KEY_CHANGED_AT] = incoming_at
    # A UID the receiver already retired in this plan points at the winner.
    for column in ("object_record_uid", "rig_record_uid"):
        uid = values.get(column)
        if uid and uid in builder.remap:
            values[column] = builder.remap[uid]

    existing = index.get(table, {}).get(key)
    if existing is None and model is SavedFraming:
        # A framing the receiver holds without an object UID is keyed by its
        # name. If the name is the one this record carries, it is the same
        # framing: adopt the incoming UID rather than treating it as a clash.
        name_key = str(values.get("object_name") or "").strip()
        candidate = index.get(table, {}).get(name_key)
        if candidate is not None and not (candidate.object_record_uid or "").strip():
            _plan_record_update(db, builder, model, record, values, name_key,
                                candidate, unique_index)
            action = builder.by_key.get((table, name_key))
            if action is not None:
                index[table].pop(name_key, None)
                index[table][key] = candidate
                builder.rekey(action, key)
            return
    if existing is not None:
        _plan_record_update(db, builder, model, record, values, key, existing,
                            unique_index)
        return

    # Not present by identity: a unique value already in use means the same
    # thing was created on both sides.
    unique_column = UNIQUE_KEY.get(model)
    if unique_column and unique_column in values:
        wanted = str(values[unique_column] or "")
        holder = unique_index.get(model, {}).get(wanted)
        if holder is not None and row_key(model, holder) != key:
            if model is AstroObject:
                _plan_object_collision(db, builder, model, record, values, key,
                                       holder, index)
                return
            if model is SavedFraming:
                # A framing is named after its object and renaming it would
                # break that link. The objects converge first; until then the
                # incoming framing waits rather than being dropped or mislabelled.
                builder.conflicts.append(Conflict(
                    table, key, "deferred",
                    {"reason": "object_identity_unresolved", "name": wanted},
                ))
                builder.deferred.append((table, key))
                return
            taken = builder.taken.setdefault(model, set())
            fresh = free_name(taken, wanted)
            builder.renames.append(Renamed(table, key, unique_column, wanted, fresh))
            values[unique_column] = fresh
            taken.add(fresh)

    builder.inserts.append((table, key))
    builder.add(Action(OP_INSERT, table, key, False, values))
    builder.count_created(table)
    if unique_column and unique_column in values:
        builder.taken.setdefault(model, set()).add(str(values[unique_column]))


def _plan_record_update(db, builder: _Builder, model: type, record: dict,
                        values: dict, key: str, existing, unique_index: dict) -> None:
    """An incoming record the receiver already holds under the same identity."""
    table = model.__tablename__
    builder.remember(model, existing)
    decided = False

    # The same identity, but a unique value that now collides with another row:
    # rename the incoming value rather than break the merge.
    unique_column = UNIQUE_KEY.get(model)
    if unique_column and unique_column in values:
        wanted = str(values[unique_column] or "")
        holder = unique_index.get(model, {}).get(wanted)
        if holder is not None and holder.id != existing.id:
            taken = builder.taken.setdefault(model, set())
            fresh = free_name(taken, wanted)
            builder.renames.append(Renamed(table, key, unique_column, wanted, fresh))
            values[unique_column] = fresh
            taken.add(fresh)
            decided = True

    incoming_at = builder.to_local_clock(parse_datetime(record.get(KEY_CHANGED_AT)))
    if model is UiPref:
        merged, differs = _merge_prefs_blob(
            existing.json_blob, values.get("json_blob"),
            incoming_at > (existing.sync_changed_at or datetime.min),
        )
        if not differs:
            return
        values["json_blob"] = merged
        builder.add(Action(OP_UPDATE, table, key, True, values, existing.id,
                           _identity_values(model, existing),
                           _row_links(model, existing)))
        builder.count_updated(table)
        return

    if _row_content(db, model, existing, record) and not decided:
        return  # nothing to do: an idempotent re-delivery

    local_at = existing.sync_changed_at or datetime.min
    take = incoming_at > local_at or (
        incoming_at == local_at and builder.side == "local"
    )
    if not take:
        builder.conflicts.append(Conflict(
            table, key, "kept_local",
            {"incoming_at": to_iso(incoming_at), "local_at": to_iso(local_at)},
        ))
        return
    builder.add(Action(OP_UPDATE, table, key, decided, values, existing.id,
                       _identity_values(model, existing),
                       _row_links(model, existing)))
    builder.count_updated(table)


def _plan_tombstone(db, builder: _Builder, tombstone: dict, index: dict,
                    planned_keys: set, refusals: list) -> None:
    """Decide what one incoming tombstone does to the receiver."""
    table = tombstone.get("table")
    key = str(tombstone.get("key") or "")
    deleted_at = builder.to_local_clock(parse_datetime(tombstone.get("deleted_at")))

    if table == ACCOUNT_TABLE:
        if key not in ACCOUNT_KINDS:
            refusals.append(Refusal(REFUSAL_BAD_FORMAT, {"marker": key}))
            return
        if key == "replaced":
            # The hub replaced the account. Merging against pre-replacement
            # state is meaningless, so the caller must download instead.
            refusals.append(Refusal(REFUSAL_REMOTE_REPLACED,
                                    {"deleted_at": to_iso(deleted_at)}))
            return
        # "seeded" is the hub's own bookkeeping — where its starter data came
        # from. It says nothing about a local's records, so a local ignores it
        # rather than writing a marker of its own.
        return

    model = TABLE_MODEL.get(table)
    if model is None:
        refusals.append(Refusal(REFUSAL_UNKNOWN_TABLE, {"table": table}))
        return
    if not key:
        return

    existing = index.get(table, {}).get(key)
    if existing is None:
        # Nothing here to delete. Keep the tombstone so both sides agree on
        # what has been deleted, and so a later re-create can clear it — but
        # only the first time: re-stamping a tombstone this instance already
        # holds would put it above the peer's cursor again and echo for ever.
        if not _has_tombstone(db, builder.user_id, table, key):
            builder.add(Action(OP_TOMBSTONE, table, key, False, {}))
        return

    builder.remember(model, existing)

    # A record this same payload updated: edit beats delete, already decided.
    if (table, key) in planned_keys:
        builder.conflicts.append(Conflict(
            table, key, "kept_edited", {"reason": "incoming_record_is_newer"}))
        return

    if deleted_at is None:
        refusals.append(Refusal(REFUSAL_MISSING_TIMESTAMP,
                                {"table": table, "key": key}))
        return
    if (existing.sync_changed_at or datetime.min) > deleted_at:
        # Edit beats delete, the other order: the local's edit survives and the
        # row is touched so the hub learns it is still alive.
        builder.resurrected.append(Resurrected(table, key, "local_edit_newer"))
        builder.add(Action(OP_UPDATE, table, key, True, {"sync_changed_at": utc_now()},
                           existing.id, _identity_values(model, existing),
                           _row_links(model, existing)))
        builder.count_updated(table)
        return

    builder.add(Action(OP_DELETE, table, key, False, {}, existing.id,
                       _identity_values(model, existing),
                       _row_links(model, existing)))
    builder.count_deleted(table)
    # Record the deletion on this side too, stamped with a local rev by the
    # apply: this instance's own delta must be able to describe the removal
    # even though the tombstone came from the peer.
    builder.add(Action(OP_TOMBSTONE, table, key, False, {}))


def _plan_renames(db, builder: _Builder) -> None:
    """Repair the text copies of an identity that was renamed in place.

    A rename that moves an identity (an object unified onto another UID) is
    repaired as it happens. This is the other case: the UID is unchanged but the
    name the dependents mirror is not.
    """
    for action in list(builder.actions):
        if action.op != OP_UPDATE:
            continue
        owner = _MIRROR_OWNER.get(TABLE_MODEL.get(action.table))
        if owner is None:
            continue
        _, uid_col, name_col, _dependents = owner
        old_name = action.before.get(name_col)
        new_name = action.values.get(name_col, old_name)
        if new_name is None or new_name == old_name:
            continue
        uid = action.values.get(uid_col, action.before.get(uid_col))
        _plan_links_to(db, builder, TABLE_MODEL[action.table], uid, old_name,
                       uid, new_name, {})


def _fingerprint(db, user_id, touched, inserts, ref_counts) -> str:
    """A digest of everything the plan read, so a stale plan can be refused."""
    digest = hashlib.sha256()
    for model in PLAN_ORDER:
        table = model.__tablename__
        number = db.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        ) or 0
        top = db.scalar(
            select(func.max(model.sync_rev)).where(model.user_id == user_id)
        ) or 0
        digest.update(f"{table}:{number}:{top};".encode())
    digest.update(f"counter:{counter_rev(db, user_id)};".encode())
    for table, row_id, rev, stamp in sorted(touched, key=lambda i: (i[0], i[1])):
        digest.update(f"{table}#{row_id}:{rev}:{to_iso(stamp)};".encode())
    for table, key in sorted(inserts):
        digest.update(f"{table}+{key};".encode())
    for pair, number in sorted(ref_counts.items()):
        digest.update(f"{pair[0]}~{pair[1]}:{number};".encode())
    return digest.hexdigest()


def _totals(db, user_id, deletions: int) -> Totals:
    records = 0
    for model in PLAN_ORDER:
        records += db.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        ) or 0
    ratio = (deletions / records) if records else 0.0
    return Totals(records, deletions, ratio)


def plan_merge(db, user, payload, *, side: str, offset_seconds: float = 0.0,
               allow_mass_delete: bool = False) -> MergePlan:
    """Decide what an incoming payload does, without writing anything.

    The result is what apply_merge() executes, exactly, and what an Apply/Abort
    dialog shows: counts, renames, unifications, conflicts and any refusal.
    """
    plan = MergePlan(side=side, user_id=user.id if user is not None else None)
    if side not in ("local", "server"):
        plan.refusals.append(Refusal(REFUSAL_BAD_FORMAT, {"side": side}))
        return plan
    uid = plan.user_id

    try:
        skew = float(offset_seconds or 0.0)
    except (TypeError, ValueError):
        skew = 0.0
    if abs(skew) > MAX_CLOCK_SKEW_SECONDS:
        plan.refusals.append(Refusal(REFUSAL_CLOCK_SKEW, {
            "offset_seconds": skew, "max": MAX_CLOCK_SKEW_SECONDS,
        }))

    precondition = check_sync_preconditions(db, user, side=side)
    if precondition is not None:
        plan.refusals.append(precondition)

    plan.refusals.extend(_validate_payload(payload, side=side))
    if plan.refusals:
        plan.totals = _totals(db, uid, 0)
        return plan

    # The receiver's state, by natural key and by unique value.
    index: dict[str, dict] = {}
    unique_index: dict[type, dict] = {}
    builder = _Builder(side, uid, skew)

    for model in PLAN_ORDER:
        table = model.__tablename__
        rows = _load_rows(db, model, uid)
        index[table] = {row_key(model, row): row for row in rows}
        unique_column = UNIQUE_KEY.get(model)
        if unique_column:
            unique_index[model] = {}
            taken = builder.taken.setdefault(model, set())
            for row in rows:
                value = getattr(row, unique_column, None)
                if value:
                    unique_index[model][str(value)] = row
                    taken.add(str(value))

    # Phase 1 — incoming records. The plan order puts objects first, so a
    # unification is known before the sessions and framings that name the
    # retired UID arrive.
    records = payload.get("records") or {}
    for model in PLAN_ORDER:
        for record in records.get(model.__tablename__) or []:
            if isinstance(record, dict):
                _plan_record(db, builder, model, record, index, unique_index,
                             plan.refusals)

    planned_keys = {
        (action.table, action.key) for action in builder.actions
        if action.op in (OP_INSERT, OP_UPDATE)
    }

    # Phase 2 — incoming tombstones, against the state phase 1 decided.
    for tombstone in payload.get("tombstones") or []:
        if isinstance(tombstone, dict):
            _plan_tombstone(db, builder, tombstone, index, planned_keys,
                            plan.refusals)

    # Phase 3 — the text copies of a renamed identity.
    _plan_renames(db, builder)

    # Phase 4 — a delete that would orphan a live record is skipped, and the
    # row is touched so the hub learns it is still needed.
    ref_counts = _post_plan_reference_counts(db, uid, builder)
    survivors = {
        pair for pair in (
            (a.table, a.key) for a in builder.actions if a.op == OP_DELETE
        )
        if ref_counts.get(pair, 0) > 0
    }
    if survivors:
        # The paired tombstone goes with the delete it came in with: the record
        # is still here, and a tombstone under it would only tell the peer to
        # delete it again.
        dropped = {
            (action.table, action.key) for action in builder.actions
            if action.op == OP_TOMBSTONE and (action.table, action.key) in survivors
        }
        kept: list[Action] = []
        for action in builder.actions:
            if (action.op == OP_TOMBSTONE
                    and (action.table, action.key) in dropped):
                continue
            if action.op == OP_DELETE and (action.table, action.key) in survivors:
                builder.resurrected.append(
                    Resurrected(action.table, action.key, "still_referenced"))
                builder.deleted[action.table] = builder.deleted.get(action.table, 1) - 1
                kept.append(Action(
                    OP_UPDATE, action.table, action.key, True,
                    {"sync_changed_at": utc_now()}, action.row_id, action.before,
                    action.links,
                ))
                builder.count_updated(action.table)
                continue
            kept.append(action)
        builder.actions = kept
        builder.by_key = {(a.table, a.key): a for a in kept}

    plan.actions = builder.actions
    plan.created = builder.created
    plan.updated = builder.updated
    plan.deleted = {t: n for t, n in builder.deleted.items() if n}
    plan.renames = builder.renames
    plan.unified = builder.unified
    plan.resurrected = builder.resurrected
    plan.conflicts = builder.conflicts
    plan.touched = builder.touched
    plan.inserts = builder.inserts
    plan.ref_counts = ref_counts
    plan.deferred = builder.deferred

    deletions = sum(plan.deleted.values())
    plan.totals = _totals(db, uid, deletions)
    if (not allow_mass_delete
            and plan.totals.records >= MASS_DELETE_MIN_RECORDS
            and plan.totals.ratio > MASS_DELETE_RATIO):
        plan.refusals.append(Refusal(REFUSAL_MASS_DELETE, {
            "deletions": deletions,
            "records": plan.totals.records,
            "ratio": plan.totals.ratio,
            "limit": MASS_DELETE_RATIO,
        }))

    plan.fingerprint = _fingerprint(db, uid, builder.touched, builder.inserts,
                                    ref_counts)
    return plan


# --- Applying ----------------------------------------------------------------

_APPLY_ORDER = tuple(model.__tablename__ for model in PLAN_ORDER)
_DELETE_ORDER = tuple(reversed(_APPLY_ORDER))


def _plan_user_id(db, plan) -> int:
    """The user a plan belongs to. Every row it touches is one user's."""
    if plan.user_id is not None:
        return plan.user_id
    for table, row_id, _rev, _stamp in plan.touched:
        model = TABLE_MODEL[table]
        found = db.scalar(
            select(model.user_id).where(model.__table__.c.id == row_id))
        if found is not None:
            return found
    raise SyncRefused(Refusal(REFUSAL_BAD_FORMAT, {"reason": "plan has no user"}))


def apply_merge(db, plan: MergePlan, *, user=None) -> MergeResult:
    """Apply exactly the plan, in one transaction.

    Refuses a plan that carries a refusal, and refuses a plan whose rows have
    moved since it was built (plan_stale). The caller re-plans and asks again.
    """
    if plan.refusals:
        raise SyncRefused(plan.refusals[0])

    user_id = user.id if user is not None else _plan_user_id(db, plan)
    if _fingerprint(db, user_id, plan.touched, plan.inserts,
                    plan.ref_counts) != plan.fingerprint:
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"user_id": user_id}))

    conn = db.connection()
    result = MergeResult(counter_before=counter_rev(db, user_id),
                         deferred=list(plan.deferred))

    # Two revs, in this order: records stored exactly as received, then records
    # the receiver altered or decided about. R_changed is always the higher.
    r_verbatim = next_rev(conn, user_id) if plan.has_verbatim else None
    r_changed = next_rev(conn, user_id) if plan.has_changed else None
    result.r_verbatim = r_verbatim
    result.r_changed = r_changed
    # Handed back to the side that pushed: its next pull excludes this rev, so
    # it does not receive its own records back. A verbatim rev is by definition
    # the one records stored exactly as received.
    if r_verbatim is not None:
        result.push_revs = [r_verbatim]

    # 1. Upserts, parents first.
    for table in _APPLY_ORDER:
        model = TABLE_MODEL[table]
        for action in plan.actions:
            if action.table != table or action.op not in (OP_INSERT, OP_UPDATE):
                continue
            _write_row(db, conn, model, action,
                       r_changed if action.changed else r_verbatim, user_id)
            if action.op == OP_INSERT:
                # The record exists again, so a tombstone under its key would
                # only make the other side delete it. The listener does the same
                # on a re-create through the ORM; Core writes need it here.
                _drop_tombstone(conn, user_id, table, action.key)
                result.created[table] = result.created.get(table, 0) + 1
            else:
                result.updated[table] = result.updated.get(table, 0) + 1

    # 2. Deletes, children first. A parent that something now points at is
    #    skipped rather than orphaning it: the plan's check, re-run because
    #    time passed between the plan and the apply.
    for table in _DELETE_ORDER:
        model = TABLE_MODEL[table]
        for action in plan.actions:
            if action.table != table or action.op != OP_DELETE:
                continue
            if table in _PARENT_TABLES and _grouped_reference_counts(
                    db, user_id, [(table, action.key)]).get((table, action.key)):
                result.skipped_at_apply.append(
                    Resurrected(table, action.key, "still_referenced"))
                continue
            _delete_row(conn, model, action, user_id)
            result.deleted[table] = result.deleted.get(table, 0) + 1

    # 3. Tombstones, each re-stamped with a local rev. One whose delete was
    #    skipped above is not written: the record is still here, and a
    #    tombstone under it would only tell the peer to delete it again.
    stopped = {(item.table, item.key) for item in result.skipped_at_apply}
    for action in plan.actions:
        if action.op != OP_TOMBSTONE or (action.table, action.key) in stopped:
            continue
        _write_tombstone(conn, user_id, action,
                         r_changed if action.changed else r_verbatim)

    result.resurrected = list(plan.resurrected)
    db.flush()
    # The rows went in through Core, so the session's copies of them are stale.
    # Expire rather than leave a later read serving pre-merge values.
    db.expire_all()
    return result


def _write_row(db, conn, model: type, action: Action, rev: Optional[int],
               user_id: int) -> None:
    """One insert or update, through Core so the ORM listener never sees it."""
    values = dict(action.values)
    horizon = values.pop(KEY_HORIZON, None)
    project_ids = values.pop(KEY_PROJECT_IDS, None)

    # A record stored exactly as received keeps the sender's stamp (the plan put
    # it in values, offset-corrected). Anything the receiver decided is stamped
    # now: its decision is what the stamp describes.
    stamp = values.pop("sync_changed_at", None)
    if stamp is None or action.changed:
        stamp = utc_now()
    values["sync_changed_at"] = stamp
    values["sync_rev"] = rev

    if action.op == OP_INSERT:
        values.setdefault("user_id", user_id)
        inserted = conn.execute(sa_insert(model.__table__).values(**values))
        keys = inserted.inserted_primary_key
        new_id = keys[0] if keys else None
    else:
        new_id = action.row_id
        conn.execute(
            sa_update(model.__table__)
            .where(model.__table__.c.id == action.row_id,
                   model.__table__.c.user_id == user_id)
            .values(**values)
        )

    if model is Location and horizon is not None and new_id is not None:
        conn.execute(sa_delete(HorizonPoint.__table__).where(
            HorizonPoint.__table__.c.location_id == new_id))
        rows = [
            {"location_id": new_id, "az_deg": float(az), "alt_min_deg": float(alt)}
            for az, alt in horizon
        ]
        if rows:
            conn.execute(sa_insert(HorizonPoint.__table__), rows)

    if model is JournalSession and project_ids is not None and new_id is not None:
        conn.execute(sa_delete(session_projects).where(
            session_projects.c.session_id == new_id))
        known = set(db.scalars(
            select(Project.id).where(Project.user_id == user_id)
        ).all())
        rows = [
            {"session_id": new_id, "project_id": pid}
            for pid in project_ids if pid in known
        ]
        if rows:
            conn.execute(sa_insert(session_projects), rows)


def _delete_row(conn, model: type, action: Action, user_id: int) -> None:
    """One delete, with the children SQLite's foreign keys would have taken.

    Scoped to the plan's user: a row id from a payload must never reach another
    account's row.
    """
    row_id = action.row_id
    if row_id is None:
        return
    if model is Location:
        conn.execute(sa_delete(HorizonPoint.__table__).where(
            HorizonPoint.__table__.c.location_id == row_id))
    if model is JournalSession:
        conn.execute(sa_delete(session_projects).where(
            session_projects.c.session_id == row_id))
    if model is Project:
        conn.execute(sa_delete(session_projects).where(
            session_projects.c.project_id == action.key))
    conn.execute(sa_delete(model.__table__).where(
        model.__table__.c.id == row_id,
        model.__table__.c.user_id == user_id))


def _drop_tombstone(conn, user_id: int, table: str, key: str) -> None:
    """Remove the tombstone under a key, because a record now carries it."""
    conn.execute(
        text("DELETE FROM sync_tombstones "
             "WHERE user_id = :uid AND table_name = :table_name "
             "AND record_key = :record_key;"),
        {"uid": user_id, "table_name": table,
         "record_key": UI_PREF_KEY if table == UiPref.__tablename__ else key},
    )


def _has_tombstone(db, user_id: int, table: str, key: str) -> bool:
    """Whether this instance already holds a tombstone under a key."""
    return db.scalar(
        select(SyncTombstone.user_id).where(
            SyncTombstone.user_id == user_id,
            SyncTombstone.table_name == table,
            SyncTombstone.record_key == (
                UI_PREF_KEY if table == UiPref.__tablename__ else key),
        )
    ) is not None


def _write_tombstone(conn, user_id: int, action: Action, rev: Optional[int]) -> None:
    """One tombstone, stamped with a LOCAL rev (never the peer's)."""
    key = UI_PREF_KEY if action.table == UiPref.__tablename__ else action.key
    conn.execute(
        text(
            "INSERT INTO sync_tombstones "
            "(user_id, table_name, record_key, deleted_at, sync_rev) "
            "VALUES (:uid, :table_name, :record_key, :deleted_at, :rev) "
            "ON CONFLICT(user_id, table_name, record_key) DO UPDATE SET "
            "deleted_at = excluded.deleted_at, sync_rev = excluded.sync_rev;"
        ),
        {
            "uid": user_id,
            "table_name": action.table,
            "record_key": key,
            "deleted_at": utc_now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            "rev": rev,
        },
    )


# --- Download from server ----------------------------------------------------

def plan_download(db, user, snapshot, *, offset_seconds: float = 0.0) -> DownloadPlan:
    """What a Download from server would wipe and write. Writes nothing."""
    plan = DownloadPlan(
        snapshot=snapshot,
        server_rev=int(snapshot.get("server_rev") or 0),
        user_id=user.id if user is not None else None,
    )

    try:
        skew = float(offset_seconds or 0.0)
    except (TypeError, ValueError):
        skew = 0.0
    if abs(skew) > MAX_CLOCK_SKEW_SECONDS:
        plan.refusals.append(Refusal(REFUSAL_CLOCK_SKEW, {
            "offset_seconds": skew, "max": MAX_CLOCK_SKEW_SECONDS,
        }))

    plan.refusals.extend(_validate_payload(snapshot, allow_snapshot=True))
    if plan.refusals:
        return plan

    uid = plan.user_id
    # All or nothing: an apply writes every record straight through, so a
    # record it could not key or stamp refuses the whole snapshot rather than
    # leaving half of it written.
    for table, rows in (snapshot.get("records") or {}).items():
        model = TABLE_MODEL.get(table)
        if model is None:
            continue
        for record in rows or []:
            if not isinstance(record, dict):
                continue
            if not natural_key(model, record):
                plan.refusals.append(Refusal(REFUSAL_MISSING_KEY,
                                             {"table": table}))
            if parse_datetime(record.get(KEY_CHANGED_AT)) is None:
                plan.refusals.append(Refusal(REFUSAL_MISSING_TIMESTAMP,
                                             {"table": table}))
    if plan.refusals:
        return plan

    for model in PLAN_ORDER:
        table = model.__tablename__
        plan.wiping[table] = db.scalar(
            select(func.count()).select_from(model).where(model.user_id == uid)
        ) or 0
        plan.inserting[table] = len((snapshot.get("records") or {}).get(table) or [])
    plan.fingerprint = _fingerprint(db, uid, [], [], {})
    return plan


def apply_download(db, plan: DownloadPlan, *, user=None) -> MergeResult:
    """Replace the local account with a hub snapshot, in one transaction.

    The wipe is a replacement, not a set of deletions: no per-record tombstones
    are written, only the account marker. Every row keeps the hub's
    sync_changed_at, and local_pushed_rev ends above the whole batch, so nothing
    here counts as "to push".
    """
    if plan.refusals:
        raise SyncRefused(plan.refusals[0])

    uid = user.id if user is not None else plan.user_id
    if uid is None:
        raise SyncRefused(Refusal(REFUSAL_BAD_FORMAT, {"reason": "plan has no user"}))
    if _fingerprint(db, uid, [], [], {}) != plan.fingerprint:
        raise SyncRefused(Refusal(REFUSAL_PLAN_STALE, {"user_id": uid}))

    conn = db.connection()

    # Wipe. Children explicitly: PRAGMA foreign_keys is off, so no cascade
    # fires. The marker is written before the apply's rev, so a download can
    # never trip the local_replaced refusal it would otherwise create.
    conn.execute(sa_delete(session_projects).where(
        session_projects.c.session_id.in_(
            select(JournalSession.id).where(JournalSession.user_id == uid))))
    conn.execute(sa_delete(session_projects).where(
        session_projects.c.project_id.in_(
            select(Project.id).where(Project.user_id == uid))))
    conn.execute(sa_delete(HorizonPoint.__table__).where(
        HorizonPoint.__table__.c.location_id.in_(
            select(Location.id).where(Location.user_id == uid))))
    for model in PLAN_ORDER:
        conn.execute(sa_delete(model.__table__).where(model.user_id == uid))
    conn.execute(sa_delete(SyncTombstone.__table__).where(SyncTombstone.user_id == uid))
    mark_account(conn, uid, "replaced")

    # Write, with one verbatim rev: everything here came from the hub.
    r_verbatim = next_rev(conn, uid)
    result = MergeResult(r_verbatim=r_verbatim)
    records = plan.snapshot.get("records") or {}

    for model in PLAN_ORDER:
        table = model.__tablename__
        actions = []
        for record in records.get(table) or []:
            if not isinstance(record, dict):
                continue
            values = _record_values(model, record)
            values[KEY_HORIZON] = record.get(KEY_HORIZON)
            values[KEY_PROJECT_IDS] = record.get(KEY_PROJECT_IDS)
            values["sync_changed_at"] = parse_datetime(record.get(KEY_CHANGED_AT))
            actions.append(Action(OP_INSERT, table, natural_key(model, record),
                                  False, values))
        for action in actions:
            _write_row(db, conn, model, action, r_verbatim, uid)
        if actions:
            result.created[table] = len(actions)

    # The hub's tombstones come across too, stamped with this instance's rev.
    for tombstone in plan.snapshot.get("tombstones") or []:
        if not isinstance(tombstone, dict):
            continue
        table = tombstone.get("table")
        key = str(tombstone.get("key") or "")
        if table == ACCOUNT_TABLE or table not in TABLE_MODEL or not key:
            continue
        _write_tombstone(conn, uid, Action(OP_TOMBSTONE, table, key, False, {}),
                         r_verbatim)

    # Watermarks last, in the same transaction.
    state = db.scalars(select(SyncState).where(SyncState.user_id == uid)).first()
    if state is None:
        state = SyncState(user_id=uid)
        db.add(state)
    state.server_cursor = plan.server_rev
    state.local_pushed_rev = counter_rev(db, uid)
    state.last_sync_at = utc_now()

    db.flush()
    db.expire_all()
    return result


# --- Sync-run watermarks -----------------------------------------------------

def record_push_accepted(db, user, rev_at_export: int, *, deferred: Iterable = ()) -> None:
    """After a hub accepts a push: this local's changes are on the hub.

    local_pushed_rev never advances past a rev this sync did not create, so it
    is set to the rev the export carried, not to the counter's current value.
    It does not advance at all when the hub deferred something: that record was
    not placed, so leaving the watermark where it is is what makes the next push
    offer it again.
    """
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None:
        return
    if deferred:
        db.flush()
        return
    state.local_pushed_rev = max(state.local_pushed_rev or 0, int(rev_at_export))
    db.flush()


def record_pull_applied(db, user, result: MergeResult, server_rev: int,
                        rev_at_export: int) -> None:
    """After a pull-apply: move the watermarks the pull earned.

    Call this inside the apply's transaction, before the commit. local_pushed_rev
    advances to R_verbatim only when no local write happened between the export
    and the apply — the counter as the apply found it still reads rev_at_export;
    otherwise it is left alone, so an edit that slipped in is pushed next time
    rather than swallowed. R_changed always stays above it, which is how a
    record this side renamed or unified reaches the hub.

    The cursor does not move past a record the apply deferred: the next pull
    must offer it again until it lands.
    """
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None:
        return
    if (result.counter_before == int(rev_at_export)
            and result.r_verbatim is not None):
        state.local_pushed_rev = result.r_verbatim
    if result.deferred:
        db.flush()
        return
    state.server_cursor = int(server_rev)
    state.last_sync_at = utc_now()
    db.flush()
