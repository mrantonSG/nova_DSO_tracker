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
import re
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, NamedTuple, Optional

from sqlalchemy import and_, func, insert as sa_insert, or_, select, text
from sqlalchemy import delete as sa_delete, update as sa_update
from sqlalchemy.orm import selectinload

from nova.helpers import (
    log_file_path, release_file_lock, shared_log_paths, try_acquire_file_lock,
    unique_log_name,
)
from nova.config import UPLOAD_FOLDER
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
KEY_FILES = "__files__"
RESERVED_KEYS = (KEY_CHANGED_AT, KEY_HORIZON, KEY_PROJECT_IDS, KEY_FILES)

# An emptied FILE column is not the same thing as one that was never set. "" is
# the user having removed the file -- the removal routes write it -- and NULL is
# a slot that never held one. Only the first is an explicit removal, and only it
# travels as an explicit null in `__files__` (see record_to_payload): a slot this
# instance cannot send says nothing, and the peer keeps whatever it holds.
FILE_REMOVED = ""

# The one natural key a ui_prefs record carries. Its real key is the user's row
# id, which is instance-local and must not travel.
UI_PREF_KEY = "self"

# --- File transfer (step 4) ---------------------------------------------------

# A note's link to an image, canonicalised for the wire: user-independent, and
# impossible to mistake for a filesystem path or for anything a browser serves.
FILE_TOKEN = "nova-file:"
FILE_TOKEN_MISSING = "nova-file-missing:"

# The URL shape a rich-text column stores today, and that the canonical form
# replaces: /uploads/<username>/<filename>. The username segment is the part
# that must not travel.
_UPLOAD_LINK = re.compile(r"/uploads/([^/\"'\\\s<>]+)/([^/\"'\\\s<>]+)")

# The three places Trix repeats one image: the attachment JSON (HTML-escaped, so
# the whole path sits between &quot;), an <img src>, an <a href>. Each pattern
# captures its own delimiters rather than looking around them: Python's re has
# no variable-width look-behind, and `&quot;` and `&#34;` are different widths.
# The replacement puts the delimiters back untouched, so the text is unchanged
# except for the path.
_HTML_ESCAPED_UPLOAD_LINK = re.compile(
    r"(?P<open>&quot;|&#34;)/uploads/"
    r"(?P<user>[^/\"'\s<>:]+)/(?P<name>[^/\"'\s<>:]+)"
    r"(?P<close>&quot;|&#34;)")
_HTML_ATTR_UPLOAD_LINK = re.compile(
    r"(?P<open>\b(?:src|href)=\")/uploads/"
    r"(?P<user>[^/\"'\s<>]+)/(?P<name>[^/\"'\s<>]+)(?P<close>\")")

# The canonical-token shapes accepted in an incoming record (validation).
_FILE_TOKEN = re.compile(r"nova-file:([0-9a-f]{64})\.([a-z0-9]{1,8})")
_FILE_TOKEN_ANY = re.compile(r"nova-file(?:-missing)?:")
_FILE_TOKEN_MISSING = re.compile(r"nova-file-missing:([^\"'\s<>]+)")
_HEX64 = re.compile(r"[0-9a-f]{64}")

# A link form this instance must never write into a column (see localize_text).
_UPLOAD_USERNAME = re.compile(r"/uploads/[^/\"'\s<>]+/")

# --- File roles and their caps ------------------------------------------------

ROLE_SESSION_PHOTO = "session_photo"
ROLE_PROJECT_IMAGE = "project_image"
ROLE_NOTE_IMAGE = "note_image"
ROLE_SESSION_LOG = "session_log"

# Per-role transfer caps. These are the SYNC caps, not the app's upload caps: a
# file above its cap is skipped and reported, never queued (a file that is
# skipped will never arrive, and a pending entry would hold the watermark for
# ever). session photo matches the 5 MB the upload route enforces; project
# images and note images have no route cap today, so these are the only limit
# a sync sees.
MAX_SYNC_FILE_BYTES = {
    ROLE_SESSION_PHOTO: 5 * 1024 * 1024,
    ROLE_SESSION_LOG: 10 * 1024 * 1024,
    ROLE_NOTE_IMAGE: 10 * 1024 * 1024,
    ROLE_PROJECT_IMAGE: 50 * 1024 * 1024,
}

# The image extensions an upload route accepts (nova/config.py's
# ALLOWED_EXTENSIONS) and the log extensions save_log_to_filesystem is used with.
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif"})
LOG_EXTENSIONS = {"asiair": "txt", "phd2": "txt", "nina": "log"}

# The log type a FILE column names. A ref built from a record carries the column
# name in `slot`; a ref built from a file on disk carries the type. Both must
# resolve to the same directory, so the two spellings meet here.
LOG_TYPE_BY_SLOT = {"asiair_log_content": "asiair", "phd2_log_content": "phd2",
                    "nina_log_content": "nina"}


def log_type_of(slot: str) -> str:
    """The log directory a ref's `slot` names, whichever spelling it uses."""
    if slot in LOG_EXTENSIONS:
        return slot
    return LOG_TYPE_BY_SLOT.get(slot, "")

# Why a file is reported instead of transferred.
SKIP_MISSING = "missing_on_sender"
SKIP_TOO_LARGE = "over_cap"
SKIP_UNSUPPORTED = "unsupported_type"
SKIP_FOREIGN = "not_this_users_file"
SKIP_SHARED = "shared"

# The FILE columns, by model, and the role each carries.
_FILE_SLOTS: dict[type, dict[str, tuple[str, str]]] = {
    JournalSession: {
        "session_image_file": (ROLE_SESSION_PHOTO, "session_image_file"),
        "asiair_log_content": (ROLE_SESSION_LOG, "asiair"),
        "phd2_log_content": (ROLE_SESSION_LOG, "phd2"),
        "nina_log_content": (ROLE_SESSION_LOG, "nina"),
    },
    Project: {
        "final_image_file": (ROLE_PROJECT_IMAGE, "final_image_file"),
    },
}

# Where a staged file lands. Under instance/ on purpose: the final move is a
# same-filesystem os.replace and therefore atomic.
STAGING_DIRNAME = "sync_staging"
# Where a Download from server moves the files it displaces. Never deleted by
# sync in this release; purge removes it with the user.
REPLACED_DIRNAME = "replaced"

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
# A file token in a record the payload cannot explain, or in a place a file
# token may not appear (correction G).
REFUSAL_DANGLING_FILE = "dangling_file_token"
REFUSAL_FILE_TOKEN_COLUMN = "file_token_in_non_text_column"
REFUSAL_BAD_FILE_REF = "bad_file_reference"
REFUSAL_FILE_VERIFY = "file_verification_failed"
REFUSAL_FILE_TOO_LARGE = "file_over_cap"
REFUSAL_NO_DISK_SPACE = "insufficient_disk_space"

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


# --- Files: identity ----------------------------------------------------------
#
# A file is content. Its identity on the wire is its sha256 plus a role, never
# its name: the receiver keeps its own naming scheme (<session id>.<ext>,
# project_<id>.<ext>, note_img_<fresh hex>.<ext>, <session id>_<name>), so code
# that builds a path keeps working, and the same photo on two instances is the
# same file even though both call it something else.

@dataclass(frozen=True)
class FileRef:
    """One file a record references, as the wire describes it."""

    role: str                       # ROLE_*
    table: str                      # the owning table
    key: str                        # the owning record's natural key
    slot: str                       # the column, or "note_img" for rich text
    sha256: str = ""                # "" when the file is missing on this side
    size: int = 0
    ext: str = ""
    name: str = ""                  # display only; never used to build a path
    missing: bool = False
    reason: str = ""                # SKIP_* when the file cannot travel

    @property
    def ident(self) -> str:
        """A stable one-file key: two refs with the same ident are the same file."""
        return self.sha256 or f"missing:{self.table}:{self.key}:{self.slot}:{self.name}"

    def descriptor(self) -> dict:
        """The entry this ref contributes to a payload's `files` manifest."""
        return {"role": self.role, "size": int(self.size or 0),
                "ext": self.ext, "name": self.name}


def file_cap(role: str) -> int:
    """The largest file of `role` a sync will carry."""
    return MAX_SYNC_FILE_BYTES.get(role, 0)


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _role_ext_ok(role: str, ext: str) -> bool:
    if role in (ROLE_SESSION_PHOTO, ROLE_PROJECT_IMAGE, ROLE_NOTE_IMAGE):
        return ext in IMAGE_EXTENSIONS
    if role == ROLE_SESSION_LOG:
        return ext in ("txt", "log")
    return False


def _ref_for_path(*, role: str, table: str, key: str, slot: str, path: str,
                  name: str = "", ext: str = "") -> FileRef:
    """The ref of a path on this instance's own disk, or a skipped one.

    Every reason a file cannot travel is decided here, so the manifest and the
    plan agree by construction:
      * unsupported extension or role -> SKIP_UNSUPPORTED
      * not on disk                   -> SKIP_MISSING (a missing: ref)
      * larger than the role's cap    -> SKIP_TOO_LARGE
    """
    name = name or os.path.basename(path or "")
    ext = (ext or (name.rsplit(".", 1)[1].lower() if "." in name else ""))
    base = dict(role=role, table=table, key=key, slot=slot, name=name, ext=ext)
    if not _role_ext_ok(role, ext):
        return FileRef(**base, missing=True, reason=SKIP_UNSUPPORTED)
    if not path or not os.path.isfile(path):
        return FileRef(**base, missing=True, reason=SKIP_MISSING)
    try:
        size = os.path.getsize(path)
        if size > file_cap(role):
            return FileRef(**base, size=size, missing=True, reason=SKIP_TOO_LARGE)
        return FileRef(**base, sha256=_sha256_of(path), size=size)
    except OSError:
        return FileRef(**base, missing=True, reason=SKIP_MISSING)


# --- Files: rich text ---------------------------------------------------------

def _user_upload_root(user, instance_path: str = None) -> str:
    """This instance's upload directory for `user` (absolute).

    `instance_path` overrides the configured root for tests and for callers that
    operate on a copy; UPLOAD_FOLDER is INSTANCE_PATH/uploads, so the override
    keeps the two in step.
    """
    if instance_path:
        return os.path.join(instance_path, "uploads",
                            getattr(user, "username", "") or "")
    return os.path.join(UPLOAD_FOLDER, getattr(user, "username", "") or "")


def _resolve_own_image(user, filename: str, instance_path: str = None) -> str:
    """The path of `filename` inside this user's own upload dir, or "".

    Only this user's directory is ever consulted (correction F): a link to
    anyone else's folder — a shared note written on another account — is a
    missing file, never a read of someone else's bytes.
    """
    if not filename or filename != os.path.basename(filename):
        return ""
    root = os.path.realpath(_user_upload_root(user, instance_path))
    path = os.path.realpath(os.path.join(root, filename))
    if os.path.commonpath([path, root]) != root or path == root:
        return ""
    return path if os.path.isfile(path) else ""


def canonicalize_text(db, user, model: type, column: str, text: str,
                      key: str = "", instance_path: str = None,
                      skipped: Optional[list] = None):
    """`text` with every image link replaced by a user-independent token.

    Returns (canonical text, [FileRef]). Three link shapes are handled and each
    replacement covers the whole URL, so the three or four copies Trix writes
    for one image all collapse in one pass:
        /uploads/<user>/note_img_x.jpg          (plain text)
        &quot;/uploads/<user>/note_img_x.jpg&quot; (attachment JSON, HTML-escaped)
        src="/uploads/<user>/note_img_x.jpg"    (img and anchor attributes)
    A file that is not in THIS user's upload dir becomes a name-only
    nova-file-missing: token: the username still comes off the wire, and no
    other user's bytes ever enter a payload.

    `key` is the owning record's natural key, carried on the refs so a transfer
    failure can name the record to re-request. `instance_path` overrides the
    configured root so a caller working on a copy resolves its own files.
    """
    if not isinstance(text, str) or not text:
        return text, []
    if "/uploads/" not in text:
        return text, []

    refs: dict[str, FileRef] = {}

    def resolve(_username_segment: str, filename: str) -> str:
        path = _resolve_own_image(user, filename, instance_path)
        ref = (None if path else FileRef(
            role=ROLE_NOTE_IMAGE, table=model.__tablename__, key=key,
            slot="note_img", name=filename, missing=True, reason=SKIP_FOREIGN))
        if ref is None:
            ref = _ref_for_path(role=ROLE_NOTE_IMAGE, table=model.__tablename__,
                                key=key, slot="note_img", path=path,
                                name=filename)
        if ref.missing:
            # Not this user's, not on disk, or untilable (unsupported type or
            # over its cap): the link cannot travel as bytes, so it travels as
            # a name and the export reports it. The caller passes a list when it
            # wants the reason (correction E: reported, never queued).
            if skipped is not None:
                skipped.append(ref)
            return FILE_TOKEN_MISSING + filename
        refs.setdefault(ref.sha256, ref)
        return f"{FILE_TOKEN}{ref.sha256}.{ref.ext}"

    # Attachment JSON first: the whole path is already between &quot;, and a
    # bare pass over it would leave the quotes intact but match a second time.
    text = _HTML_ESCAPED_UPLOAD_LINK.sub(
        lambda m: m.group("open") + resolve(m.group("user"), m.group("name"))
        + m.group("close"), text)
    text = _HTML_ATTR_UPLOAD_LINK.sub(
        lambda m: m.group("open") + resolve(m.group("user"), m.group("name"))
        + m.group("close"), text)
    text = _UPLOAD_LINK.sub(lambda m: resolve(m.group(1), m.group(2)), text)
    return text, list(refs.values())


def _local_url(filename: str, username: str = "") -> str:
    """This instance's URL for a file it just stored for `username`."""
    return f"/uploads/{username}/{filename}"


def localize_text(model: type, column: str, text: str, mapping: Mapping[str, str],
                  username: str = "") -> str:
    """The canonical form back to this instance's own /uploads/ URLs.

    `mapping` is sha256 -> the local filename the receiver chose. A token whose
    digest is absent becomes a link to the digest-derived name, which 404s
    exactly as a broken link does today: no half-written file, no other user's
    directory.

    `username` is passed in rather than read from a global: the function stays
    pure, and a request can never leak its own identity into an apply.
    """
    if not isinstance(text, str) or "nova-file" not in text:
        return text

    def replace_sha(match):
        digest, ext = match.group(1), match.group(2)
        name = mapping.get(digest)
        if not name:
            return _local_url(f"{digest}.{ext}", username)
        return _local_url(name, username)

    # A missing token is already a filename: it only needs the URL prefix.
    text = _FILE_TOKEN_MISSING.sub(
        lambda m: _local_url(m.group(1), username), text)
    return _FILE_TOKEN.sub(replace_sha, text)


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
    # FileRefs this plan needs on this disk, and the ones it cannot get. A
    # pending entry is a file that should arrive and did not: the watermark
    # holds, exactly as it does for a deferred record. A skipped entry is a
    # file that will never arrive (over its cap, or missing on the sender):
    # the record syncs without it, and holding the watermark would hold it for
    # ever.
    files: list = field(default_factory=list)
    # Every (record, slot) needing a physical placement, not deduped: `files`
    # is what to fetch (one copy per digest), this is what to move into place.
    placements: list = field(default_factory=list)
    pending_files: list = field(default_factory=list)
    skipped_files: list = field(default_factory=list)
    # Files a sync delete or replace leaves unreferenced. Reported, never
    # deleted in this release.
    orphaned_files: list = field(default_factory=list)
    # The manifest the payload carried, so the apply knows what the sender can
    # actually send and which tokens resolve.
    manifest: dict = field(default_factory=dict)

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
            "files": {
                "needed": len(self.files),
                "bytes": sum(int(r.size or 0) for r in self.files),
                "skipped": len(self.skipped_files),
            },
            "orphaned_files": [_ref_summary(r) for r in self.orphaned_files],
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
    # (table, key, slot, sha256) files that should have arrived and did not.
    # Same effect on the watermark as `deferred`: an apply whose placements
    # never landed must not let the cursor move, and a hub's push response
    # carries its commit's pending list back to the pusher.
    pending_files: list = field(default_factory=list)
    # (table, key, slot, sha256) files staged and moved into place by
    # commit_staged_files. Empty until it runs.
    placed_files: list = field(default_factory=list)
    # Files this apply left unreferenced. Reported, never deleted.
    orphaned_files: list = field(default_factory=list)
    # Files the payload reported but could not carry (over cap, not on the
    # sender, not this user's). Reported, never pending: a file that will never
    # arrive must not hold the watermark for ever.
    skipped_files: list = field(default_factory=list)
    # The refs the apply's rows need. commit_staged_files() reads this; it is
    # not JSON, so it stays out of any summary.
    files_expected: list = field(default_factory=list)
    # A download's displaced files, moved aside by move_replaced_files() after
    # the commit. (name, size) pairs; nothing here was deleted.
    replaced_files: list = field(default_factory=list)
    replaced_bytes: int = 0
    # The hub rev a Download wrote, and only a Download. Its cursor moves inside
    # apply_download, before a byte is placed, so commit_staged_files needs this
    # to tell a download whose files were lost from a pull whose were.
    snapshot_rev: Optional[int] = None


@dataclass
class DownloadPlan:
    snapshot: dict
    server_rev: int
    user_id: Optional[int] = None
    wiping: dict = field(default_factory=dict)
    inserting: dict = field(default_factory=dict)
    refusals: list = field(default_factory=list)
    fingerprint: str = ""
    # The files the snapshot brings, and the ones it leaves behind.
    files: list = field(default_factory=list)
    # Every (record, slot) needing a physical placement, not deduped by digest.
    placements: list = field(default_factory=list)
    skipped_files: list = field(default_factory=list)
    # Files a replacement would move aside into instance/replaced/<user>/.
    replaced: list = field(default_factory=list)
    replaced_bytes: int = 0

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def summary(self) -> dict:
        """The dialog's view: what is wiped, what arrives, what is moved aside."""
        return {
            "side": "local",
            "wiping": dict(self.wiping),
            "inserting": dict(self.inserting),
            "refusals": [r._asdict() for r in self.refusals],
            "files": {
                "incoming": len(self.files),
                "bytes": sum(int(r.size or 0) for r in self.files),
                "skipped": len(self.skipped_files),
                "replaced": len(self.replaced),
                "replaced_bytes": int(self.replaced_bytes),
            },
        }


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
    for reserved in (KEY_HORIZON, KEY_PROJECT_IDS, KEY_FILES):
        if reserved in record:
            values[reserved] = record[reserved]
    # Carried so a write stored exactly as received keeps the sender's stamp:
    # the plan replaces this raw text with the offset-corrected datetime.
    if KEY_CHANGED_AT in record:
        values[KEY_CHANGED_AT] = record[KEY_CHANGED_AT]
    return values


def record_to_payload(model: type, row: Any, *, user=None,
                      instance_path: str = None, shared_paths=None) -> dict:
    """One row as a payload record: synced columns, links as UIDs, plus sets.

    With `user`, the FILE columns (`__files__`) and every rich-text image link
    are carried in their canonical, receiver-independent form. Without it the
    record is the step-3 shape, unchanged.
    """
    out: dict[str, Any] = {}
    key = row_key(model, row)
    rich = RICH_TEXT_COLUMNS.get(model, ())
    for column in sync_columns(model):
        value = to_iso(getattr(row, column))
        if user is not None and column in rich:
            value, _refs = canonicalize_text(None, user, model, column, value, key,
                                             instance_path)
        out[column] = value
    out[KEY_CHANGED_AT] = to_iso(row.sync_changed_at)
    if user is not None and model in _FILE_SLOTS:
        slots: dict[str, Any] = {}
        for slot in _FILE_SLOTS[model]:
            if getattr(row, slot, None) == FILE_REMOVED:
                # The user emptied the slot. An explicit null is what tells the
                # peer to clear its copy; leaving the slot out would say the
                # opposite, "no news about this file, keep yours".
                slots[slot] = None
                continue
            ref = _slot_ref(model, row, slot, _FILE_SLOTS[model][slot][0], user,
                            instance_path, shared_paths)
            if ref.missing:
                # Set here but untransferable -- gone from disk, over the cap,
                # another user's bytes. The peer's copy is the only one left, so
                # the slot is left out rather than nulled or sent broken.
                continue
            # In this user's own naming scheme when it travels, never the
            # sender's name: the receiver rebuilds <local id>.<ext>.
            slots[slot] = ref.sha256
        if slots:
            out[KEY_FILES] = slots
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


# --- Files: what a row references ---------------------------------------------

# The SYNC text columns that can hold a rich-text /uploads/ link. A new note
# column must be listed here or its images will not travel: the guard test
# checks that every listed name is a real SYNC column, not that every SYNC text
# column is listed (a name or a number cannot carry a link).
RICH_TEXT_COLUMNS: dict[type, tuple[str, ...]] = {
    AstroObject: ("project_name", "shared_notes", "image_url"),
    JournalSession: ("notes",),
    Project: ("description_notes", "framing_notes", "processing_notes", "goals"),
}


def _slot_ref(model: type, row, slot: str, role: str, user,
              instance_path: str = None, shared_paths=None) -> FileRef:
    """The ref one FILE column of `row` carries (photo, project image, log)."""
    key = row_key(model, row)
    value = getattr(row, slot, None)
    if value is None:
        return FileRef(role=role, table=model.__tablename__, key=key, slot=slot,
                       missing=True, reason=SKIP_MISSING)
    if value == "":
        # FILE_REMOVED: the slot was deliberately emptied. That is "no file",
        # not "a file that failed to resolve", so it must not be listed as
        # missing_on_sender in skipped_files.
        return FileRef(role=role, table=model.__tablename__, key=key, slot=slot,
                       sha256="", missing=False)
    if role == ROLE_SESSION_LOG:
        if shared_paths and value in shared_paths:
            return FileRef(role=role, table=model.__tablename__, key=key,
                           slot=slot, name=os.path.basename(value) or "",
                           missing=True, reason=SKIP_SHARED)
        # Path form and raw-content form both end up as bytes on the wire: a
        # raw content value is a legacy row and is read as its own text.
        path = _log_path(value, instance_path)
        name = os.path.basename(path) if path else f"{row.external_id or row.id}.log"
        ext = (name.rsplit(".", 1)[1].lower() if "." in name else "txt")
        base = dict(role=role, table=model.__tablename__, key=key, slot=slot,
                    name=name, ext=ext)
        if not path:
            # Raw content in the column: a file that exists only in the DB.
            try:
                payload = value.encode("utf-8", errors="ignore")
            except Exception:
                return FileRef(**base, missing=True, reason=SKIP_UNSUPPORTED)
            if len(payload) > file_cap(role):
                return FileRef(**base, size=len(payload), missing=True,
                               reason=SKIP_TOO_LARGE)
            return FileRef(**base, sha256=hashlib.sha256(payload).hexdigest(),
                           size=len(payload))
        # Correction F: only a log whose filename prefix is one of THIS user's
        # session ids may travel. Anything else in a shared log directory is
        # somebody else's bytes.
        return _ref_for_path(role=role, table=model.__tablename__, key=key,
                             slot=slot, path=path, name=name, ext=ext)
    path = os.path.join(_user_upload_root(user, instance_path), value)
    return _ref_for_path(role=role, table=model.__tablename__, key=key, slot=slot,
                         path=path, name=value)


def _log_path(db_value: str, instance_path: str = None) -> str:
    """helpers.log_file_path, re-rooted at `instance_path` when one is given.

    A stored log value is an "instance/logs/..." path that resolves against the
    parent of INSTANCE_PATH. Under an override the same relative path is
    re-rooted at the override, which is the point: the caller works inside a
    copy of the instance directory, not the configured one.
    """
    path = log_file_path(db_value)
    if not path or not instance_path:
        return path
    return os.path.join(instance_path, os.path.relpath(path, INSTANCE_PATH))


def collect_files(db, user, *, rows=None, instance_path: str = None,
                  shared_paths=None) -> list:
    """Every file the given rows reference, as FileRefs.

    `rows` is {model: [row, ...]}. Without it every row of every synced model
    for this user is walked, which is what a snapshot or a repair check needs.
    A ref that cannot travel carries its reason (SKIP_*) instead of a digest.
    """
    if rows is None:
        rows = {model: _load_rows(db, model, user.id) for model in PLAN_ORDER}
    if shared_paths is None:
        shared_paths = shared_log_paths(db)

    refs: list[FileRef] = []
    for model, table_rows in rows.items():
        slots = _FILE_SLOTS.get(model, {})
        rich = RICH_TEXT_COLUMNS.get(model, ())
        for row in table_rows:
            key = row_key(model, row)
            for slot, (role, log_type) in slots.items():
                value = getattr(row, slot, None)
                if not value:
                    continue
                ref = _slot_ref(model, row, slot, role, user, instance_path,
                                shared_paths)
                refs.append(ref)
            for column in rich:
                _text, found = canonicalize_text(db, user, model, column,
                                                 getattr(row, column, None), key,
                                                 instance_path, skipped=refs)
                refs.extend(found)

    # One ref per (role, digest): the same file referenced twice travels once.
    unique: dict[str, FileRef] = {}
    for ref in refs:
        unique.setdefault(f"{ref.role}:{ref.ident}", ref)
    return list(unique.values())


def export_files(db, user, refs) -> dict:
    """The payload's `files` manifest: descriptors only, never bytes.

    Keyed by sha256, so a file two records share has one entry. A ref that
    cannot travel is not in here: the export reports it as skipped instead.
    """
    manifest: dict[str, dict] = {}
    for ref in refs:
        if ref.missing or not ref.sha256:
            continue
        manifest.setdefault(ref.sha256, ref.descriptor())
    return manifest


def _ref_summary(ref: FileRef) -> dict:
    """A JSON-safe description of one file, for a plan or an export report."""
    return {
        "role": ref.role, "table": ref.table, "key": ref.key, "slot": ref.slot,
        "sha256": ref.sha256, "size": int(ref.size or 0), "ext": ref.ext,
        "name": ref.name, "missing": bool(ref.missing), "reason": ref.reason,
    }


def _ref_key(ref: FileRef) -> tuple:
    """One FileRef as the (table, key, slot, sha256) tuple the wire uses.

    A MergeResult reports its pending files in this shape, the same one a
    CommitReport and the push response's `pending_files` carry, so a caller
    compares them without knowing which source it got one from.
    """
    return (ref.table, ref.key, ref.slot, ref.sha256)


# --- Files: staging and placement --------------------------------------------

@dataclass
class StagedFile:
    """One file written into the staging directory and not yet moved anywhere."""

    ref: FileRef
    path: str                       # the staged path, <user>/<sha256>.<ext>
    local_name: str = ""            # the name it will take once placed
    skipped: bool = False
    reason: str = ""


def staging_dir(user, instance_path: str = None) -> str:
    """This user's staging directory (created on first use)."""
    base = os.path.join(instance_path or INSTANCE_PATH, STAGING_DIRNAME,
                        str(getattr(user, "id", "0") or "0"))
    os.makedirs(base, exist_ok=True)
    return base


def replaced_dir(user, timestamp: str, instance_path: str = None) -> str:
    """Where a Download from server moves the files it displaces."""
    base = os.path.join(instance_path or INSTANCE_PATH, REPLACED_DIRNAME,
                        str(getattr(user, "id", "0") or "0"), timestamp)
    os.makedirs(base, exist_ok=True)
    return base


def staged_index(user, *, verify: bool = False, instance_path: str = None) -> dict:
    """{sha256: path} of what is already staged for this user.

    With verify, a staged file's digest is recomputed and a mismatch drops it,
    so a retried transfer never trusts a half-written or corrupted file. The
    name is the digest, so the check is a plain hash of the file it names.
    """
    base = os.path.join(instance_path or INSTANCE_PATH, STAGING_DIRNAME,
                        str(getattr(user, "id", "0") or "0"))
    found: dict[str, str] = {}
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return found
    for name in names:
        if name.endswith(".part"):
            continue
        digest, _dot, _ext = name.partition(".")
        if not _HEX64.fullmatch(digest):
            continue
        path = os.path.join(base, name)
        if verify:
            try:
                if not os.path.isfile(path) or _sha256_of(path) != digest:
                    discard_staged(StagedFile(
                        FileRef(role="", table="", key="", slot=""), path))
                    continue
            except OSError:
                continue
        found[digest] = path
    return found


def files_needed(plan, user, *, have: Optional[Mapping[str, str]] = None,
                 instance_path: str = None) -> list:
    """The plan's files this disk does not already hold, by sha256.

    `have` is a {sha256: path} index; without it the staging directory is
    scanned and verified. A digest staged and verified is not requested again,
    which is what makes a retried download resume instead of starting over.
    Deduplicated by digest: one file two records share is fetched once.
    """
    index = dict(have) if have is not None else staged_index(
        user, verify=True, instance_path=instance_path)
    needed: dict[str, FileRef] = {}
    for ref in getattr(plan, "files", []) or []:
        if not ref.sha256 or ref.missing:
            continue
        if ref.sha256 in index:
            continue
        needed.setdefault(ref.sha256, ref)
    return list(needed.values())


def _copy_hashed(source, destination_part: str, *, max_bytes: int):
    """Stream `source` into `destination_part`, returning (sha256, size).

    An over-cap stream is truncated by raising, so a hostile transfer cannot
    fill the disk before the size is checked.
    """
    digest = hashlib.sha256()
    written = 0
    with open(destination_part, "wb") as out:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            if not isinstance(chunk, (bytes, bytearray)):
                chunk = bytes(chunk)
            written += len(chunk)
            if written > max_bytes:
                raise ValueError("over_cap")
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest(), written


def stage_file(user, ref, stream, *, declared_size: Optional[int] = None,
               instance_path: str = None) -> StagedFile:
    """Write one incoming file into staging and verify it.

    The file is streamed to "<sha>.part", fsynced, atomically renamed to
    "<sha>.<ext>", and only then compared with the declared digest and size. A
    mismatch discards it. Nothing outside the user's staging directory is
    written, and no live path is touched: commit_staged_files() runs after the
    DB commit and moves verified files into place.

    An over-cap or unsupported file is returned as skipped, not raised: the
    record it belongs to syncs without it.
    """
    base = staging_dir(user, instance_path)
    cap = file_cap(ref.role)
    if not _role_ext_ok(ref.role, ref.ext) or not _HEX64.fullmatch(ref.sha256 or ""):
        return StagedFile(ref=ref, path="", skipped=True, reason=SKIP_UNSUPPORTED)
    if declared_size is not None and int(declared_size) > cap:
        return StagedFile(ref=ref, path="", skipped=True, reason=SKIP_TOO_LARGE)
    final = os.path.join(base, f"{ref.sha256}.{ref.ext}")
    part = final + ".part"
    try:
        digest, size = _copy_hashed(stream, part, max_bytes=cap)
    except ValueError:
        discard_staged(StagedFile(ref=ref, path=part))
        return StagedFile(ref=ref, path="", skipped=True, reason=SKIP_TOO_LARGE)
    except OSError:
        discard_staged(StagedFile(ref=ref, path=part))
        return StagedFile(ref=ref, path="", skipped=True, reason=SKIP_MISSING)
    if digest != ref.sha256 or (declared_size is not None
                                and int(declared_size) != size):
        discard_staged(StagedFile(ref=ref, path=part))
        return StagedFile(ref=ref, path="", skipped=True, reason=SKIP_UNSUPPORTED)
    try:
        _fsync_file(part)
    except OSError:
        pass
    os.replace(part, final)
    return StagedFile(ref=ref, path=final, local_name=ref.name)


def _fsync_file(path: str) -> None:
    """Flush a file to disk before it is renamed into place."""
    handle = os.open(path, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def staged_ok(staged: StagedFile) -> bool:
    """Whether a staged file is on disk and still hashes to its digest."""
    if staged is None or staged.skipped or not staged.path:
        return False
    try:
        return (os.path.isfile(staged.path)
                and _sha256_of(staged.path) == staged.ref.sha256)
    except OSError:
        return False


def discard_staged(staged: StagedFile) -> None:
    """Remove a staged file and its .part. Never raises."""
    if staged is None or not staged.path:
        return
    for path in (staged.path, staged.path + ".part"):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


@dataclass
class CommitReport:
    """What commit_staged_files() moved, and what it could not."""

    placed: list = field(default_factory=list)      # (table, key, slot, sha256)
    pending: list = field(default_factory=list)      # same shape: try again
    skipped: list = field(default_factory=list)      # FileRef with a reason
    orphaned: list = field(default_factory=list)     # FileRef, left on disk


def local_name_for(model: type, row, ref: FileRef) -> str:
    """The name this instance gives `ref`'s file. Never the sender's name.

    It reproduces what the write path would have produced, so every reader and
    every cleanup rule that builds a path from the column keeps working:
        session photo   <local session id>.<ext>
        project image   project_<local project id>.<ext>
        note image      note_img_<fresh hex>.<ext>
        session log     <local session id>_<sanitized name>
    """
    if ref.role == ROLE_SESSION_PHOTO:
        if row is not None and getattr(row, "id", None) is not None:
            return f"{row.id}.{ref.ext}"
        return f"{_stable_id(ref.key)}.{ref.ext}"
    if ref.role == ROLE_PROJECT_IMAGE:
        return f"project_{getattr(row, 'id', None) or ref.key}.{ref.ext}"
    if ref.role == ROLE_NOTE_IMAGE:
        # Deterministic, not random: the same digest always lands on the same
        # name, so localize_text (inside the row's own write) and the physical
        # move (commit_staged_files, after the commit) agree without having to
        # hand a name across the commit boundary.
        return note_image_name(ref.sha256, ref.ext)
    if ref.role == ROLE_SESSION_LOG:
        safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", ref.name or "log.txt")[:100]
        owner = getattr(row, "id", None) or _stable_id(ref.key)
        return f"{owner}_{safe}"
    return f"{ref.sha256}.{ref.ext}"


def _stable_id(key: str) -> str:
    """A filename-safe stand-in for a natural key when no row id is at hand."""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", str(key or ""))[:80]
    return cleaned or "record"


def note_image_name(sha256: str, ext: str) -> str:
    """The name this instance gives an arriving note image. Deterministic.

    The original route used twelve random hex digits; a digest prefix is just
    as opaque, keeps the note_img_ prefix the read route allows across users,
    and makes the name a pure function of the content.
    """
    return f"note_img_{sha256[:12]}.{ext}"


def note_image_names(values: Mapping[str, Any]) -> dict:
    """{sha256: local name} for every note-image token a record's text carries."""
    mapping: dict[str, str] = {}
    for column, value in values.items():
        if not isinstance(value, str) or FILE_TOKEN not in value:
            continue
        for match in _FILE_TOKEN.finditer(value):
            mapping.setdefault(match.group(1),
                               note_image_name(match.group(1), match.group(2)))
    return mapping


def _slot_role(model: type, slot: str):
    entry = _FILE_SLOTS.get(model, {}).get(slot)
    return entry[0] if entry else None


def commit_staged_files(db, result, staged, *, user,
                        instance_path: str = None) -> CommitReport:
    """Move verified files into place and write their columns. After the commit.

    The plan's record write happened in apply_merge/apply_download and the
    caller committed it. This runs next, then writes the local filename into
    each FILE column, one small Core UPDATE per row per slot.

    Those updates must NOT bump sync_rev or sync_changed_at: the value they
    write (<local id>.<ext>) is this instance's naming scheme, instance-local
    by definition. Bumping would send the receiver's name back to the sender
    as an edit and overwrite the sender's own file name.

    Idempotent: a file already in place is left alone; a staged file that is
    gone is reported pending, not an error. A file that fails verification is
    discarded and reported pending, and its column is not written, so no record
    ever points at a half-written file.
    """
    report = CommitReport()
    by_digest = {s.ref.sha256: s for s in (staged or []) if s and not s.skipped}
    for ref in getattr(result, "files_expected", []) or []:
        if ref.missing or not ref.sha256:
            report.skipped.append(ref)
            continue
        s = by_digest.get(ref.sha256)
        if s is None:
            # No staged copy this run. That is a retry of a file an earlier run
            # already placed in the common case, so it is pending only when the
            # live file is not yet the one the digest names.
            if not _live_file_ok(db, ref, user, instance_path=instance_path):
                report.pending.append((ref.table, ref.key, ref.slot, ref.sha256))
                continue
            s = StagedFile(ref=ref, path="")   # already in place: nothing to move
        elif not staged_ok(s):
            discard_staged(s)
            report.pending.append((ref.table, ref.key, ref.slot, ref.sha256))
            continue
        model = TABLE_MODEL.get(ref.table)
        if model is None:
            report.skipped.append(ref)
            continue
        # A note image names no FILE column: its record's rich text already
        # points at the digest-derived name the row's write localized to. Only
        # the bytes need moving.
        if ref.role == ROLE_NOTE_IMAGE:
            outcome = _place_note_image(user, ref, s, instance_path=instance_path)
            if outcome == "placed":
                report.placed.append((ref.table, ref.key, ref.slot, ref.sha256))
            else:
                report.pending.append((ref.table, ref.key, ref.slot, ref.sha256))
            continue
        if ref.slot not in _FILE_SLOTS.get(model, {}):
            report.skipped.append(ref)
            continue
        outcome = _place_one(db, user, model, ref, s, instance_path=instance_path)
        if outcome == "placed":
            report.placed.append((ref.table, ref.key, ref.slot, ref.sha256))
        else:
            report.pending.append((ref.table, ref.key, ref.slot, ref.sha256))

    # The watermark follows this report, not the plan's earlier guess: a file
    # whose bytes landed is no longer pending, and one whose transfer failed and
    # whose live copy is still not here holds the watermark for the next run.
    if result is not None:
        result.pending_files = [tuple(entry) for entry in report.pending]
        result.placed_files = [tuple(entry) for entry in report.placed]
        # A Download has no record-level watermark step of its own, and its
        # cursor moved inside apply_download, before a single byte was placed.
        # The hub would then sit below the cursor for good and a file that never
        # arrived would never be offered again. Drop the cursor to zero so the
        # next pull walks the snapshot once more: every record is a content
        # no-op, the file that was already here is skipped by digest, and the
        # bytes that were lost are fetched this time.
        if report.pending and getattr(result, "snapshot_rev", None) is not None:
            state = db.scalars(
                select(SyncState).where(SyncState.user_id == user.id)).first()
            if state is not None:
                state.server_cursor = 0

    # A staged copy is dropped only once every placement for its digest is done:
    # one file two records share is placed twice, and the first move would
    # otherwise leave the second with nothing to move.
    wanted: dict[str, int] = {}
    done: dict[str, int] = {}
    for ref in getattr(result, "files_expected", []) or []:
        if ref.sha256 and not ref.missing:
            wanted[ref.sha256] = wanted.get(ref.sha256, 0) + 1
    for entry in report.placed:
        done[entry[3]] = done.get(entry[3], 0) + 1
    for digest, count in done.items():
        if count >= wanted.get(digest, count) and digest in by_digest:
            # Nothing left to retry under this digest; drop the staged copy so
            # the next run does not think it has more work to do.
            discard_staged(by_digest[digest])

    for shape in ("skipped", "orphaned"):
        target = getattr(result, f"{shape}_files", None)
        if target is None:
            target = []
            setattr(result, f"{shape}_files", target)
        target.extend(getattr(report, shape))
    return report


def _place_from_staged(staged_path: str, live: str) -> None:
    """Write a staged file to `live` without ever leaving a half-written file.

    A plain copy onto the live name is not atomic: a failure part-way through
    leaves the new bytes truncated and the old ones already gone, and a record
    that names that file then points at rubbish - the one thing rule 4 forbids.
    The bytes go to "<live>.part", are flushed to disk, and are renamed over the
    live name, exactly as stage_file() does when it stages. os.replace is atomic
    within one filesystem, so a reader sees either the old file or the new one,
    never a mixture, and a leftover .part is cleanup, not data loss.
    """
    part = live + ".part"
    try:
        shutil.copyfile(staged_path, part)
        _fsync_file(part)
        os.replace(part, live)
    except OSError:
        try:
            if os.path.isfile(part):
                os.remove(part)
        except OSError:
            pass
        raise


def _place_note_image(user, ref: FileRef, staged: StagedFile,
                      instance_path: str = None) -> str:
    """Move an arriving note image to the name its record's text already names.

    A note image has no FILE column: it is referenced only by the digest-derived
    name localize_text put into the record's rich text during the row's write.
    The name is a pure function of the digest, so this computes the same one
    without the row. An existing file with the same digest is left alone.
    """
    name = note_image_name(ref.sha256, ref.ext)
    root = os.path.realpath(_user_upload_root(user, instance_path))
    os.makedirs(root, exist_ok=True)
    live = os.path.realpath(os.path.join(root, name))
    if os.path.commonpath([live, root]) != root:
        return "skipped"
    if staged.path:
        staging_root = os.path.realpath(staging_dir(user, instance_path))
        if os.path.commonpath(
                [os.path.realpath(staged.path), staging_root]) != staging_root:
            return "skipped"
    elif not (os.path.exists(live) and _sha256_of(live) == ref.sha256):
        return "pending"      # nothing staged and nothing in place: try again
    try:
        if not (os.path.exists(live) and _sha256_of(live) == ref.sha256):
            # Copied, not moved: one file two records share is placed twice from
            # the same staged copy. commit_staged_files discards it once every
            # placement for the digest is done.
            _place_from_staged(staged.path, live)
    except OSError:
        return "pending"
    return "placed"


def _live_file_ok(db, ref: FileRef, user, instance_path: str = None) -> bool:
    """Whether `ref`'s file is already on this disk under its live name.

    This instance's own naming scheme, so a second commit with no staged copy is
    a no-op when an earlier run already placed the file, and pending when the
    transfer failed and the bytes never arrived. A photo's name carries the
    local row id and a log's its stored path, so both are resolved through the
    row the natural key names, exactly as _place_one rebuilds them.
    """
    if not ref.sha256:
        return False
    model = TABLE_MODEL.get(ref.table)
    row = _row_by_key(db, model, user.id, ref.key) if model is not None else None
    if ref.role == ROLE_SESSION_LOG:
        live = _log_path(getattr(row, ref.slot, None), instance_path) if row else ""
    elif ref.role == ROLE_NOTE_IMAGE:
        live = os.path.join(_user_upload_root(user, instance_path),
                            note_image_name(ref.sha256, ref.ext))
    else:
        live = os.path.join(_user_upload_root(user, instance_path),
                            local_name_for(model, row, ref))
    try:
        return bool(live) and os.path.isfile(live) and _sha256_of(live) == ref.sha256
    except OSError:
        return False


def _place_one(db, user, model: type, ref: FileRef, staged: StagedFile,
               instance_path: str = None) -> str:
    """Move one verified file to its live path and record the name in its column."""
    conn = db.connection()
    natural = _KEY_COLUMN.get(model)
    if natural is None:
        return "skipped"
    key_value = ref.key
    row = db.execute(
        select(model).where(model.user_id == user.id,
                            getattr(model, natural) == key_value)
    ).scalars().first()
    if row is None:
        return "skipped"      # the record was deleted between plan and commit

    if ref.role == ROLE_SESSION_LOG:
        log_type = _FILE_SLOTS[model][ref.slot][1]
        local_name = local_name_for(model, row, ref)
        local_name = unique_log_name(db, log_type, local_name, row.id)
        log_dir = os.path.join(instance_path or INSTANCE_PATH, "logs", log_type)
        os.makedirs(log_dir, exist_ok=True)
        live = os.path.join(log_dir, local_name)
        if staged.path:
            staging_root = os.path.realpath(staging_dir(user, instance_path))
            if os.path.commonpath([os.path.realpath(staged.path), staging_root]) != staging_root:
                return "skipped"
        elif not (os.path.exists(live) and _sha256_of(live) == ref.sha256):
            return "pending"  # nothing staged and nothing in place: try again
        if os.path.realpath(os.path.dirname(live)) != os.path.realpath(log_dir):
            return "skipped"
        try:
            if not (os.path.exists(live) and _sha256_of(live) == ref.sha256):
                _place_from_staged(staged.path, live)
        except OSError:
            return "pending"
        value = os.path.join("instance", "logs", log_type, local_name)
        if ref.slot == "asiair_log_content":
            conn.execute(sa_update(model.__table__).where(
                model.__table__.c.id == row.id,
                model.__table__.c.user_id == user.id,
            ).values(asiair_log_content=value))
        elif ref.slot == "phd2_log_content":
            conn.execute(sa_update(model.__table__).where(
                model.__table__.c.id == row.id,
                model.__table__.c.user_id == user.id,
            ).values(phd2_log_content=value))
        else:
            conn.execute(sa_update(model.__table__).where(
                model.__table__.c.id == row.id,
                model.__table__.c.user_id == user.id,
            ).values(nina_log_content=value))
        # The log changed, so the cached parse of it is stale.
        conn.execute(sa_update(model.__table__).where(
            model.__table__.c.id == row.id,
            model.__table__.c.user_id == user.id,
        ).values(log_analysis_cache=None))
        return "placed"

    local_name = local_name_for(model, row, ref)
    root = os.path.realpath(_user_upload_root(user, instance_path))
    os.makedirs(root, exist_ok=True)
    live = os.path.realpath(os.path.join(root, local_name))
    if os.path.commonpath([live, root]) != root:
        return "skipped"
    if not staged.path and not (os.path.exists(live)
                                and _sha256_of(live) == ref.sha256):
        return "pending"          # nothing staged and nothing in place: try again
    # Correction D: a stale thumbnail goes before the original is replaced.
    from nova.helpers import remove_thumbnail
    remove_thumbnail(live)
    try:
        if not (os.path.exists(live) and _sha256_of(live) == ref.sha256):
            # Copied, not moved: one digest may feed several records, each with
            # its own local name. The staged copy is discarded once all of them
            # are placed (see commit_staged_files).
            _place_from_staged(staged.path, live)
    except OSError:
        return "pending"
    column = "session_image_file" if ref.role == ROLE_SESSION_PHOTO else "final_image_file"
    conn.execute(sa_update(model.__table__).where(
        model.__table__.c.id == row.id,
        model.__table__.c.user_id == user.id,
    ).values(**{column: local_name}))
    return "placed"


def missing_local_files(db, user, instance_path: str = None) -> list:
    """Every file this user's records name that this disk does not have.

    Re-derives each FILE column's ref from the row itself, the same rule the
    export uses. The repair request names the ref by (table, key, slot) because
    a file that is gone cannot be hashed here: the peer resolves it by record.

    Runs at the end of a sync and on demand; it is the safety net for a crash
    between commit and move, a file deleted out of band, and a download that
    stopped after its commit.
    """
    found: list[FileRef] = []
    for model in PLAN_ORDER:
        if model not in _FILE_SLOTS:
            continue
        for row in _load_rows(db, model, user.id):
            for slot, (role, _log_type) in _FILE_SLOTS[model].items():
                if not getattr(row, slot, None):
                    continue
                ref = _slot_ref(model, row, slot, role, user, instance_path)
                if ref.missing:
                    found.append(ref)
    return found


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
                   exclude_revs: Iterable[int] = (), with_files: bool = True,
                   instance_path: str = None) -> dict:
    """Everything this instance has changed for `user` above `since_rev`.

    Rows and tombstones with sync_rev > since_rev. Account markers ride only in
    a hub's payload: a local never pushes one.

    exclude_revs are revs the caller must not be handed back. A hub fills it
    with the revs it wrote verbatim for the pusher (MergeResult.push_revs), so
    the pull that follows leaves out exactly what the pusher just sent while
    everything the hub decided about those records still travels. A side never
    receives what it just sent.

    with_files describes the changed rows' files in the payload: an `__files__`
    map on the record and a `files` manifest beside it, plus `skipped_files`
    for what cannot travel. Descriptors only; the bytes are the caller's to
    fetch with stage_file(). with_files=False is the step-3 shape.
    """
    uid = user.id
    payload = _envelope(db, user, side=side, snapshot=False)
    excluded = sorted({int(rev) for rev in exclude_revs if rev is not None})
    shared = shared_log_paths(db) if with_files else None

    changed: dict[type, list] = {}
    for model in PLAN_ORDER:
        query = select(model).where(model.user_id == uid,
                                    model.sync_rev > since_rev)
        if excluded:
            query = query.where(model.sync_rev.notin_(excluded))
        rows = list(db.scalars(query).all())
        if rows:
            changed[model] = rows
            payload["records"][model.__tablename__] = [
                record_to_payload(model, row, user=user if with_files else None,
                                  instance_path=instance_path, shared_paths=shared)
                for row in rows
            ]

    payload["tombstones"] = [
        _tombstone_to_payload(t)
        for t in _visible_tombstones(db, uid, side, since_rev, excluded)
    ]
    if with_files:
        refs = collect_files(db, user, rows=changed, instance_path=instance_path,
                             shared_paths=shared)
        payload["files"] = export_files(db, user, refs)
        payload["skipped_files"] = [_ref_summary(r) for r in refs if r.missing]
    return payload


def export_snapshot(db, user, *, side: str = "server",
                    instance_path: str = None) -> dict:
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
    shared = shared_log_paths(db)
    everything: dict[type, list] = {}
    for model in PLAN_ORDER:
        rows = _load_rows(db, model, uid)
        everything[model] = rows
        if rows:
            payload["records"][model.__tablename__] = [
                record_to_payload(model, row, user=user,
                                  instance_path=instance_path,
                                  shared_paths=shared) for row in rows
            ]

    payload["tombstones"] = [
        _tombstone_to_payload(t) for t in _visible_tombstones(db, uid, side, None)
    ]
    refs = collect_files(db, user, rows=everything, instance_path=instance_path,
                         shared_paths=shared)
    payload["files"] = export_files(db, user, refs)
    payload["skipped_files"] = [_ref_summary(r) for r in refs if r.missing]
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

    # The report a hub returns with an accepted push: the files it could not
    # place. A local reads it to keep its watermark, so it must be a list of
    # 4-tuples (table, key, slot, sha256) and nothing else, or a hostile hub
    # could shape it into anything.
    pending = payload.get("pending_files")
    if pending is not None:
        if not isinstance(pending, list) or not all(
                isinstance(entry, (list, tuple)) and len(entry) == 4
                for entry in pending):
            refusals.append(Refusal(REFUSAL_BAD_FILE_REF,
                                    {"reason": "pending_files is not a list of refs"}))

    manifest = payload.get("files")
    if manifest is not None and not isinstance(manifest, dict):
        return [Refusal(REFUSAL_BAD_FORMAT, {"reason": "files is not a mapping"})]
    for digest, descriptor in (manifest or {}).items():
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            refusals.append(Refusal(REFUSAL_BAD_FILE_REF, {"sha256": digest}))
            continue
        if not isinstance(descriptor, dict):
            refusals.append(Refusal(REFUSAL_BAD_FILE_REF, {"sha256": digest}))
            continue
        if descriptor.get("role") not in MAX_SYNC_FILE_BYTES:
            refusals.append(Refusal(REFUSAL_BAD_FILE_REF,
                                    {"sha256": digest, "role": descriptor.get("role")}))
        try:
            size = int(descriptor.get("size") or 0)
        except (TypeError, ValueError):
            size = -1
        if size < 0:
            refusals.append(Refusal(REFUSAL_BAD_FILE_REF, {"sha256": digest}))
        elif size > file_cap(descriptor.get("role") or ""):
            refusals.append(Refusal(REFUSAL_FILE_TOO_LARGE,
                                    {"sha256": digest, "size": size}))
        if not _role_ext_ok(descriptor.get("role") or "",
                            str(descriptor.get("ext") or "")):
            refusals.append(Refusal(REFUSAL_BAD_FILE_REF,
                                    {"sha256": digest, "ext": descriptor.get("ext")}))

    records = payload.get("records") or {}
    if not isinstance(records, dict):
        return [Refusal(REFUSAL_BAD_FORMAT, {"reason": "records is not a mapping"})]
    for table, rows in records.items():
        model = TABLE_MODEL.get(table)
        if model is None:
            refusals.append(Refusal(REFUSAL_UNKNOWN_TABLE, {"table": table}))
            continue
        allowed = set(sync_columns(model)) | set(RESERVED_KEYS)
        refusals.extend(_validate_file_tokens(model, rows or [], manifest or {}))
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


def _text_sync_columns(model: type) -> frozenset:
    """The columns of `model` a file token may sit in: exactly the rich text.

    A token is only ever resolved by localize_text(), which _write_row runs on
    the columns in RICH_TEXT_COLUMNS and no others. A plain SYNC Text column is
    copied through verbatim, so a token accepted there would reach the row
    unresolved and leave the record pointing at a name no reader can fetch.
    Being Text is not the test; being rewritten is.
    """
    return frozenset(RICH_TEXT_COLUMNS.get(model, ()))


def _validate_file_tokens(model: type, rows, manifest: Mapping[str, Any]) -> list:
    """Correction G: a token must name a manifest entry, in a text column.

    A `nova-file:` token the manifest does not explain would be resolved by
    nobody and would arrive at the other side as a link to a file that does not
    exist; a token in a column that is not text is a record trying to smuggle a
    reference through a numeric field. Both refuse the payload rather than leave
    a record pointing at nothing.
    """
    refusals: list[Refusal] = []
    text_columns = _text_sync_columns(model)
    for row in rows:
        if not isinstance(row, dict):
            continue
        files = row.get(KEY_FILES) or {}
        if isinstance(files, dict):
            for slot, digest in files.items():
                if slot not in _FILE_SLOTS.get(model, {}):
                    refusals.append(Refusal(REFUSAL_BAD_FILE_REF,
                                            {"table": model.__tablename__,
                                             "slot": slot}))
                elif digest is None:
                    continue   # an explicit removal names no manifest entry
                elif not isinstance(digest, str) or not _HEX64.fullmatch(digest):
                    # A placement is resolved by digest; a malformed one would
                    # surface on the receiving side too late to refuse the whole
                    # payload, which is where this check belongs.
                    refusals.append(Refusal(REFUSAL_BAD_FILE_REF,
                                            {"table": model.__tablename__,
                                             "slot": slot, "sha256": digest}))
                elif digest not in manifest:
                    refusals.append(Refusal(REFUSAL_DANGLING_FILE, {
                        "table": model.__tablename__, "slot": slot,
                        "sha256": digest}))
        for column, value in row.items():
            if not isinstance(value, str) or not _FILE_TOKEN_ANY.search(value):
                continue
            if column not in text_columns:
                refusals.append(Refusal(REFUSAL_FILE_TOKEN_COLUMN, {
                    "table": model.__tablename__, "column": column}))
                continue
            for match in _FILE_TOKEN.finditer(value):
                if match.group(1) not in manifest:
                    refusals.append(Refusal(REFUSAL_DANGLING_FILE, {
                        "table": model.__tablename__, "column": column,
                        "sha256": match.group(1)}))
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


def _local_file_digest(model: type, row, slot: str, role: str, user,
                       instance_path: str = None) -> Optional[str]:
    """The sha256 of the file `row` holds for `slot`, or None when it is absent.

    None means "different from any digest": a record whose text matches but
    whose file is gone here is a change (correction B).
    """
    ref = _slot_ref(model, row, slot, role, user, instance_path)
    return None if ref.missing else ref.sha256


def _files_equal(model: type, row, record: Mapping[str, Any], user,
                 instance_path: str = None) -> bool:
    """Whether every file the record names is already the one on this disk.

    A slot the record does not name is not compared: the record is saying it
    carries no file there, and a receiver that holds one is not a conflict, it
    is the newest-wins rule's business. A slot the record names with a digest
    that is absent, or whose local file cannot be read, is a difference. A slot
    the record names as null is an explicit removal: a difference only when this
    side still names a file there, so a removal a side has already taken is not
    re-applied for ever.
    """
    incoming = record.get(KEY_FILES)
    if not isinstance(incoming, dict):
        return True
    if user is None:
        return True
    slots = _FILE_SLOTS.get(model)
    if not slots:
        return True
    for slot, digest in incoming.items():
        if slot not in slots:
            continue
        if digest is None:
            if getattr(row, slot, None):
                return False
            continue
        role = slots[slot][0]
        local = _local_file_digest(model, row, slot, role, user, instance_path)
        if local != digest:
            return False
    return True


def _row_content(db, model: type, row, record: Mapping[str, Any], user=None,
                 instance_path: str = None) -> bool:
    """True when `row` already holds exactly what `record` carries.

    Synced content only. sync_changed_at is not a column of any model, so it
    never enters this comparison: a record that comes back with a stamp a
    re-measured offset has nudged is still the same record, and equal content
    means no write and no rev whatever the two stamps say.

    Text is compared in its canonical form (a /uploads/<user>/ link on this disk
    against the record's nova-file: token), so the same note is a no-op however
    its two sides name the user. Files are content too (correction B): a record
    that names a digest this disk does not hold is a change even when every
    column matches, so the new bytes travel through newest-wins.
    """
    rich = RICH_TEXT_COLUMNS.get(model, ())
    for column, value in _record_values(model, record).items():
        if column in RESERVED_KEYS:
            continue  # sets, compared below against the rows that hold them
        current = getattr(row, column, None)
        if column in rich and user is not None:
            current, _refs = canonicalize_text(db, user, model, column,
                                               current, row_key(model, row),
                                               instance_path)
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
    return _files_equal(model, row, record, user, instance_path)


def _same_content(db, model: type, row, values: Mapping[str, Any], user=None,
                  record: Optional[Mapping[str, Any]] = None,
                  instance_path: str = None) -> bool:
    """Whether `row` already carries `values`, ignoring the columns that name it.

    The same rule as _row_content, for the place where two identities have
    already been decided to be one record (a name collision): the key column
    differs by definition there, so what is left to compare is the content.
    `record` is the incoming record, when the caller has one, so its file
    digests can be compared too.
    """
    key_column = _KEY_COLUMN.get(model)
    rich = RICH_TEXT_COLUMNS.get(model, ())
    for column, value in values.items():
        if column in RESERVED_KEYS or column == key_column:
            continue
        current = getattr(row, column, None)
        if column in rich and user is not None:
            current, _refs = canonicalize_text(db, user, model, column,
                                               current, row_key(model, row),
                                               instance_path)
        if current != value:
            return False
    if record is None:
        return True
    return _files_equal(model, row, record, user, instance_path)


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
        # Files: refs the records bring, refs that will not travel, and refs
        # for a token whose descriptor the sender did not send.
        self.files: list[FileRef] = []
        # Every (record, slot) that needs a physical placement, NOT deduped by
        # digest: one file two records share is fetched once but placed twice.
        self.placements: list[FileRef] = []
        self.pending_files: list[FileRef] = []
        self.skipped_files: list[FileRef] = []
        self.orphaned_files: list[FileRef] = []
        self.manifest: dict = {}
        # The local user, so file digests can be read from disk.
        self.user = None
        # The instance directory those digests are read under. Production leaves
        # it None; a caller working on a copy passes its own.
        self.instance_path: Optional[str] = None

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
                and not _same_content(db, model, winner_row, values,
                                      builder.user, record,
                                      builder.instance_path)):
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

    if _row_content(db, model, existing, record, builder.user,
                    builder.instance_path) and not decided:
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


def _plan_files(db, builder: _Builder, payload, index: dict) -> None:
    """Collect the files the plan's writes need, and the ones it leaves behind.

    A record that will be written names its files in `__files__`; a rich-text
    column names them inline as nova-file: tokens. A named digest the manifest
    describes and this disk does not hold is `files` (the caller fetches it).
    A digest the manifest does not describe, or one the sender marked skipped,
    is `skipped_files`: the record still syncs, without that file. Rich-text
    images are always optional, so they are skipped, never pending: there is no
    record change to hold back for them.

    The receiver's own rows that a delete or a replace displaces are reported
    in `orphaned_files`. Nothing is deleted: the file stays on disk.
    """
    written = {(a.table, a.key) for a in builder.actions
               if a.op in (OP_INSERT, OP_UPDATE)}
    deleted = {(a.table, a.key) for a in builder.actions if a.op == OP_DELETE}
    skipped_by_sender = {
        (s.get("table"), str(s.get("key")))
        for s in (payload.get("skipped_files") or []) if isinstance(s, dict)
    }

    manifest = builder.manifest
    seen: set[str] = set()
    for model in PLAN_ORDER:
        table = model.__tablename__
        slots = _FILE_SLOTS.get(model, {})
        if not slots and model not in RICH_TEXT_COLUMNS:
            continue
        for record in (payload.get("records") or {}).get(table) or []:
            if not isinstance(record, dict):
                continue
            key = natural_key(model, record)
            if (table, key) not in written and (table, key) not in deleted:
                continue
            for slot, digest in (record.get(KEY_FILES) or {}).items():
                if slot not in slots or digest is None:
                    # An explicit removal needs nothing fetched or placed: the
                    # column write below is the whole of it.
                    continue
                role = slots[slot][0]
                ref = FileRef(role=role, table=table, key=key, slot=slot,
                              sha256=digest,
                              size=int((manifest.get(digest) or {}).get("size") or 0),
                              ext=str((manifest.get(digest) or {}).get("ext") or ""),
                              name=str((manifest.get(digest) or {}).get("name") or ""))
                if digest in skipped_by_sender:
                    ref = FileRef(**{**ref.__dict__, "missing": True,
                                     "reason": SKIP_MISSING})
                    builder.skipped_files.append(ref)
                elif digest not in manifest:
                    builder.skipped_files.append(
                        FileRef(**{**ref.__dict__, "missing": True,
                                   "reason": SKIP_MISSING}))
                elif (table, key) in written:
                    builder.placements.append(ref)
                    if digest not in seen:
                        builder.files.append(ref)
                seen.add(digest)
            for column in RICH_TEXT_COLUMNS.get(model, ()):
                for ref in _record_text_refs(model, record, column, key, manifest):
                    if ref.ident in seen:
                        continue
                    seen.add(ref.ident)
                    if ref.sha256 and ref.sha256 not in manifest:
                        builder.skipped_files.append(
                            FileRef(**{**ref.__dict__, "missing": True,
                                       "reason": SKIP_MISSING}))
                    else:
                        builder.placements.append(ref)
                        builder.files.append(ref)

    _collect_orphans(db, builder, index, deleted)


def _record_text_refs(model: type, record: Mapping[str, Any], column: str,
                      key: str, manifest: Optional[Mapping[str, Any]] = None) -> list:
    """The note-image refs a record's canonical text still names.

    A token carries only a digest and an extension, but the sender resolves its
    own file by name, so the name and size come from the manifest descriptor the
    sender wrote beside that digest.
    """
    text = record.get(column)
    if not isinstance(text, str) or FILE_TOKEN not in text:
        return []
    refs = []
    for match in _FILE_TOKEN.finditer(text):
        digest, ext = match.group(1), match.group(2)
        descriptor = (manifest or {}).get(digest) or {}
        refs.append(FileRef(role=ROLE_NOTE_IMAGE, table=model.__tablename__,
                            key=key, slot="note_img", sha256=digest,
                            ext=ext,
                            size=int(descriptor.get("size") or 0),
                            name=str(descriptor.get("name") or "")))
    return refs


def _collect_orphans(db, builder: _Builder, index: dict, deleted: set) -> None:
    """The files a delete or replace leaves unreferenced, as refs. Never deleted.

    Only rows this instance already held are considered: a replaced photo is
    the local file whose row the plan overwrites with another digest, and a
    delete's files are the ones the deleted row named.
    """
    user = builder.user
    if user is None:
        return
    for table, key in deleted:
        model = TABLE_MODEL.get(table)
        row = (index.get(table) or {}).get(key)
        if model is None or row is None or model not in _FILE_SLOTS:
            continue
        for slot, (role, _log_type) in _FILE_SLOTS[model].items():
            ref = _slot_ref(model, row, slot, role, user, builder.instance_path)
            if ref.sha256:
                builder.orphaned_files.append(ref)

    for action in builder.actions:
        if action.op != OP_UPDATE:
            continue
        model = TABLE_MODEL.get(action.table)
        row = (index.get(action.table) or {}).get(action.key)
        incoming = action.values.get(KEY_FILES)
        if model is None or row is None or not isinstance(incoming, dict):
            continue
        for slot, (role, _log_type) in _FILE_SLOTS.get(model, {}).items():
            if slot not in incoming:
                continue
            old = _slot_ref(model, row, slot, role, user, builder.instance_path)
            if old.sha256 and old.sha256 != incoming.get(slot):
                builder.orphaned_files.append(old)


def plan_merge(db, user, payload, *, side: str, offset_seconds: float = 0.0,
               allow_mass_delete: bool = False,
               instance_path: str = None) -> MergePlan:
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
    builder.user = user
    builder.instance_path = instance_path
    builder.manifest = payload.get("files") or {}

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

    # Phase 3b — the files these records need, and the ones they will not get.
    _plan_files(db, builder, payload, index)

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
    plan.files = builder.files
    plan.placements = builder.placements
    plan.skipped_files = builder.skipped_files
    plan.orphaned_files = builder.orphaned_files
    plan.manifest = builder.manifest
    # A file that should arrive and did not is pending from the start: staging
    # fails or never runs, and the watermark must hold.
    plan.pending_files = list(builder.files)

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
                         deferred=list(plan.deferred),
                         pending_files=[_ref_key(r) for r in plan.pending_files],
                         files_expected=list(plan.placements or plan.files),
                         skipped_files=list(plan.skipped_files),
                         orphaned_files=list(plan.orphaned_files))

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
                       r_changed if action.changed else r_verbatim, user_id,
                       getattr(user, "username", "") or "")
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
               user_id: int, username: str = "") -> None:
    """One insert or update, through Core so the ORM listener never sees it."""
    values = dict(action.values)
    horizon = values.pop(KEY_HORIZON, None)
    project_ids = values.pop(KEY_PROJECT_IDS, None)
    files = values.pop(KEY_FILES, None)   # a reference set, never a column
    if isinstance(files, dict):
        # An explicit removal is the one thing in `__files__` that is a column
        # write: the record said "no file here". Everything else in the map is
        # a digest, and a digest becomes this instance's own name through the
        # placement step -- never a column value.
        for slot, digest in files.items():
            if digest is None and slot in _FILE_SLOTS.get(model, {}):
                values[slot] = FILE_REMOVED

    # The incoming text is canonical (nova-file: tokens). It is localized here,
    # inside the same statement as the row, so a record never exists pointing at
    # a token and its images never point at the sender's username.
    name_for = note_image_names(values)
    for column in RICH_TEXT_COLUMNS.get(model, ()):
        if isinstance(values.get(column), str):
            values[column] = localize_text(model, column, values[column],
                                           name_for, username)

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

def plan_download(db, user, snapshot, *, offset_seconds: float = 0.0,
                  instance_path: str = None) -> DownloadPlan:
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

    # The snapshot's files, and what the replacement will move aside. The
    # displaced files are exactly today's uploads and this account's log files:
    # a download wipes every record, so nothing this account holds is still
    # referenced afterwards.
    incoming = {model: [] for model in PLAN_ORDER}
    manifest = snapshot.get("files") or {}
    for table, rows in (snapshot.get("records") or {}).items():
        model = TABLE_MODEL.get(table)
        if model is None:
            continue
        for record in rows or []:
            if not isinstance(record, dict):
                continue
            key = natural_key(model, record)
            for slot, digest in (record.get(KEY_FILES) or {}).items():
                if slot not in _FILE_SLOTS.get(model, {}) or digest not in manifest:
                    plan.skipped_files.append({"table": table, "key": key,
                                               "slot": slot, "sha256": digest,
                                               "reason": SKIP_MISSING})
                    continue
                descriptor = manifest.get(digest) or {}
                plan.placements.append(FileRef(
                    role=descriptor.get("role") or _FILE_SLOTS[model][slot][0],
                    table=table, key=key, slot=slot, sha256=digest,
                    size=int(descriptor.get("size") or 0),
                    ext=str(descriptor.get("ext") or ""),
                    name=str(descriptor.get("name") or "")))
            for column in RICH_TEXT_COLUMNS.get(model, ()):
                for ref in _record_text_refs(model, record, column, key, manifest):
                    if ref.sha256 and ref.sha256 in manifest:
                        plan.placements.append(ref)
                    else:
                        plan.skipped_files.append({
                            "table": table, "key": key, "slot": ref.slot,
                            "sha256": ref.sha256, "reason": SKIP_MISSING})
    plan.files = _unique_refs(plan.placements)
    plan.replaced = _replacement_files(db, user, plan.files,
                                       instance_path=instance_path)
    plan.replaced_bytes = sum(int(r.size or 0) for r in plan.replaced)
    # A download whose bytes would not fit is refused here, before the caller
    # fetches anything: a half-filled staging directory is worse than a "no".
    refusal = disk_space_refusal(plan, instance_path=instance_path)
    if refusal is not None:
        plan.refusals.append(refusal)
    plan.fingerprint = _fingerprint(db, uid, [], [], {})
    return plan


def _unique_refs(refs) -> list:
    seen: set[str] = set()
    out = []
    for ref in refs:
        if ref.sha256 in seen:
            continue
        seen.add(ref.sha256)
        out.append(ref)
    return out


def _replacement_files(db, user, incoming, *, instance_path: str = None) -> list:
    """The files a Download from server displaces, as refs.

    Everything in this user's uploads directory, plus every log file whose name
    is one of this account's session ids. Every one of them is a candidate: the
    wipe removes every record, so at plan time nothing here is referenced. What
    actually moves is decided after the commit, by move_replaced_files(), which
    knows the local names the new records ended up with — a file the new rows
    still name (same content, same local id) stays exactly where it is.

    `incoming` is accepted for the caller's convenience and is not used to
    pre-filter: a digest match does not mean the file keeps its name.
    """
    del incoming
    displaced: list[FileRef] = []
    root = _user_upload_root(user, instance_path)
    for name in _listdir(root):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        if name.startswith("note_img_"):
            role = ROLE_NOTE_IMAGE
        elif name.startswith("project_"):
            role = ROLE_PROJECT_IMAGE
        else:
            role = ROLE_SESSION_PHOTO
        try:
            size = os.path.getsize(path)
            digest = _sha256_of(path)
        except OSError:
            continue
        displaced.append(FileRef(role=role, table="", key="", slot="", name=name,
                                 sha256=digest, size=size,
                                 ext=name.rsplit(".", 1)[-1].lower()))
    # Logs: only paths this user references exclusively (referential ownership).
    # A shared path (two distinct owners) is not this user's to displace; a
    # stale-prefix path this user's rows still name is.
    shared = shared_log_paths(db)
    seen = set()
    for session in db.scalars(
            select(JournalSession).where(JournalSession.user_id == user.id)).all():
        for slot, log_type in (("asiair_log_content", "asiair"),
                               ("phd2_log_content", "phd2"),
                               ("nina_log_content", "nina")):
            value = getattr(session, slot, None)
            if not value or value in shared or value in seen:
                continue
            if not value.startswith("instance/logs/"):
                continue
            seen.add(value)
            path = _log_path(value, instance_path)
            if not path or not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
                digest = _sha256_of(path)
            except OSError:
                continue
            name = os.path.basename(path)
            displaced.append(FileRef(role=ROLE_SESSION_LOG, table="", key="",
                                     slot=log_type, name=name, sha256=digest,
                                     size=size,
                                     ext=name.rsplit(".", 1)[-1].lower()))
    return displaced


def _listdir(path: str) -> list:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


# --- Download: moving the displaced files aside (correction C) ----------------

# Headroom over the incoming bytes before a download may start: the staging
# copy and the live copy both exist for a moment, so free space must cover the
# payload twice with room to spare.
DISK_SPACE_FACTOR = 2.0
DISK_SPACE_SLACK_BYTES = 32 * 1024 * 1024


def _free_bytes(path: str) -> Optional[int]:
    """Free bytes on the filesystem holding `path`, or None when it cannot be read."""
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe or ".").free
    except OSError:
        return None


def disk_space_refusal(plan, instance_path: str = None) -> Optional[Refusal]:
    """The refusal for a download whose bytes would not fit, or None.

    Checked before a single byte is fetched, so a download that cannot finish
    never starts and never leaves a half-filled staging directory behind.
    """
    needed = sum(int(r.size or 0) for r in getattr(plan, "files", []) or [])
    if not needed:
        return None
    free = _free_bytes(instance_path or INSTANCE_PATH)
    if free is None:
        return None      # cannot measure: do not block a download over that
    required = int(needed * DISK_SPACE_FACTOR) + DISK_SPACE_SLACK_BYTES
    if free < required:
        return Refusal(REFUSAL_NO_DISK_SPACE, {
            "needed_bytes": needed, "required_bytes": required, "free_bytes": free,
        })
    return None


def move_replaced_files(db, plan, user, *, instance_path: str = None,
                        timestamp: Optional[str] = None) -> CommitReport:
    """Move the files a download displaces into instance/replaced/<user>/<ts>/.

    Runs after apply_download's commit and before commit_staged_files: by then
    every row is the hub's, so a file no current record names is one this
    account no longer refers to. Nothing is deleted (correction C) — a
    replacement is a move, and the old bytes stay on disk under a timestamped
    directory until purge takes the user away.

    Idempotent by construction: the move happens first, the column update
    second. A crash in between leaves the row still naming the old file, which
    the next run finds gone from its live path and simply does not move again.
    A file a current record references, and every staged file about to land, is
    never moved.
    """
    report = CommitReport()
    if user is None:
        return report
    displaced = list(getattr(plan, "replaced", []) or [])
    if not displaced:
        return report
    stamp = timestamp or utc_now().strftime("%Y%m%dT%H%M%SZ")
    try:
        target = replaced_dir(user, stamp, instance_path)
    except OSError:
        return report
    keep = _referenced_paths(db, user, instance_path=instance_path)
    # A download's rows carry no FILE column until commit_staged_files writes
    # one, so a file about to be placed under an unchanged name looks
    # unreferenced here. The placements say exactly which names will land.
    keep |= _incoming_paths(db, plan, user, instance_path=instance_path)
    staged = set(staged_index(user, instance_path=instance_path).values())
    staged = {os.path.realpath(p) for p in staged}

    for ref in displaced:
        live = _displaced_path(user, ref, instance_path=instance_path)
        if not live:
            continue
        real = os.path.realpath(live)
        if real in staged:
            continue                      # about to be replaced by the arriving file
        if os.path.exists(real) and real in keep:
            continue                      # a current record still names it
        try:
            if not os.path.isfile(real):
                continue
            size = os.path.getsize(real)
            moved = os.path.join(target, os.path.basename(real))
            if os.path.exists(moved):
                # Already moved by an earlier run of this same replacement.
                continue
            shutil.move(real, moved)
        except OSError:
            report.pending.append((ref.table, ref.key, ref.slot, ref.sha256))
            continue
        report.placed.append((ref.table, ref.key, ref.slot, ref.sha256))
        report.orphaned.append(FileRef(role=ref.role, table="", key="", slot="",
                                       name=os.path.basename(real),
                                       sha256=ref.sha256, size=size, ext=ref.ext))
    return report


def _incoming_paths(db, plan, user, instance_path: str = None) -> set:
    """The realpaths the download's placements are about to write.

    After apply_download's commit the rows exist but their FILE columns are
    still the payload's (a digest, or nothing), so _referenced_paths cannot see
    them. This resolves each placement to the name commit_staged_files will give
    it and returns that path, so the move-aside never moves a file that is
    about to be rewritten in place.
    """
    keep: set[str] = set()
    root = os.path.realpath(_user_upload_root(user, instance_path))
    for ref in getattr(plan, "placements", []) or []:
        if ref.missing or not ref.sha256:
            continue
        if ref.role == ROLE_NOTE_IMAGE:
            keep.add(os.path.realpath(
                os.path.join(root, note_image_name(ref.sha256, ref.ext))))
            continue
        model = TABLE_MODEL.get(ref.table)
        if model is None:
            continue
        if ref.role == ROLE_SESSION_LOG:
            log_type = log_type_of(ref.slot)
            if not log_type:
                continue
            row = _row_by_key(db, model, user.id, ref.key)
            name = (local_name_for(model, row, ref) if row is not None
                    else local_name_for(model, None, ref))
            keep.add(os.path.realpath(os.path.join(
                instance_path or INSTANCE_PATH, "logs", log_type, name)))
            continue
        if ref.slot not in _FILE_SLOTS.get(model, {}):
            continue
        row = _row_by_key(db, model, user.id, ref.key)
        name = local_name_for(model, row, ref)
        keep.add(os.path.realpath(os.path.join(root, name)))
    return keep


def _row_by_key(db, model: type, user_id: int, key: str):
    """The row a natural key names, or None."""
    column = _KEY_COLUMN.get(model)
    if column is None:
        return None
    return db.execute(
        select(model).where(model.user_id == user_id,
                            getattr(model, column) == key)
    ).scalars().first()


def _displaced_path(user, ref: FileRef, instance_path: str = None) -> str:
    """Where a displaced ref lives on this disk, or "" when it has no home."""
    if ref.role == ROLE_SESSION_LOG:
        log_type = log_type_of(ref.slot) or "asiair"
        base = os.path.join(instance_path or INSTANCE_PATH, "logs", log_type)
    else:
        base = _user_upload_root(user, instance_path)
    name = os.path.basename(ref.name or "")
    if not name:
        return ""
    return os.path.join(base, name)


def file_source_path(ref: FileRef, user, instance_path: str = None) -> str:
    """Where `ref`'s bytes live on the side that is sending them.

    The sender's own naming scheme, resolved the same way its readers resolve
    it: a session photo and a note image are in the user's uploads directory, a
    log is in its type's directory. Returns "" when the file is not there, which
    is the same answer _slot_ref() would have given at export time.
    """
    if not ref.sha256:
        return ""
    if ref.role == ROLE_SESSION_LOG:
        log_type = log_type_of(ref.slot)
        if not log_type:
            return ""
        base = os.path.join(instance_path or INSTANCE_PATH, "logs", log_type)
        path = os.path.join(base, os.path.basename(ref.name or ""))
    else:
        path = os.path.join(_user_upload_root(user, instance_path),
                            os.path.basename(ref.name or ""))
    try:
        return path if os.path.isfile(path) else ""
    except OSError:
        return ""


def _referenced_paths(db, user, instance_path: str = None) -> set:
    """The realpaths every current record's FILE columns and text links name.

    A log's column holds an "instance/logs/..." path that resolves against the
    *parent* of INSTANCE_PATH (helpers.log_file_path). Under an override the
    same relative path is re-rooted at the override, which is the whole point:
    the caller is working inside a copy of the instance directory.
    """
    keep: set[str] = set()

    def _reroot(path: str) -> str:
        if not path or not instance_path:
            return path
        return os.path.join(instance_path, os.path.relpath(path, INSTANCE_PATH))

    for model in PLAN_ORDER:
        slots = _FILE_SLOTS.get(model, {})
        rich = RICH_TEXT_COLUMNS.get(model, ())
        for row in _load_rows(db, model, user.id):
            for slot in slots:
                value = getattr(row, slot, None)
                if not value:
                    continue
                if slots[slot][0] == ROLE_SESSION_LOG:
                    path = _reroot(log_file_path(value))
                else:
                    path = os.path.join(_user_upload_root(user, instance_path), value)
                if path:
                    keep.add(os.path.realpath(path))
            for column in rich:
                text = getattr(row, column, None)
                if not isinstance(text, str) or "/uploads/" not in text:
                    continue
                for match in _UPLOAD_LINK.finditer(text):
                    filename = match.group(2)
                    if filename != os.path.basename(filename):
                        continue
                    root = os.path.realpath(_user_upload_root(user, instance_path))
                    path = os.path.realpath(os.path.join(root, filename))
                    if path.startswith(root + os.sep):
                        keep.add(path)
    return keep



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
    counter_before = counter_rev(db, uid)
    r_verbatim = next_rev(conn, uid)
    username = (getattr(user, "username", "") or "") if user is not None else ""
    result = MergeResult(
        counter_before=counter_before,
        r_verbatim=r_verbatim,
        snapshot_rev=plan.server_rev,
        pending_files=[_ref_key(r) for r in (plan.placements or plan.files)],
        files_expected=list(plan.placements or plan.files),
        skipped_files=list(plan.skipped_files),
        # What the wipe displaced. The move itself is the caller's next step,
        # after this transaction commits (correction C): a move cannot be rolled
        # back, so it must not happen before the rows that justify it are safe.
        replaced_files=[(r.name, int(r.size or 0)) for r in plan.replaced],
        replaced_bytes=int(plan.replaced_bytes),
    )
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
            _write_row(db, conn, model, action, r_verbatim, uid, username)
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

def record_push_accepted(db, user, rev_at_export: int, *, deferred: Iterable = (),
                         commit: "CommitReport" = None) -> None:
    """After a hub accepts a push: this local's changes are on the hub.

    local_pushed_rev never advances past a rev this sync did not create, so it
    is set to the rev the export carried, not to the counter's current value.
    It does not advance at all when the hub deferred a record or could not place
    a file: neither was written, so leaving the watermark where it is is what
    makes the next push offer it again. Re-delivering a record that did land is
    a no-op, so re-offering the whole batch costs nothing.

    `commit` is the CommitReport the hub's push response carried. Its `pending`
    list is a file that should have arrived and did not, which holds the
    watermark exactly as a deferral does.
    """
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None:
        return
    if deferred or getattr(commit, "pending", None):
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

    The cursor does not move past a record the apply deferred, nor past a file
    that should have arrived and did not: the next pull must offer both again
    until they land. `result.pending_files` is the file half of that, and it is
    right whether or not commit_staged_files has run: the apply seeds it from
    the plan (a transfer that never ran has placed nothing) and the commit
    rewrites it with what it actually managed to move.
    """
    state = db.scalars(select(SyncState).where(SyncState.user_id == user.id)).first()
    if state is None:
        return
    if (result.counter_before == int(rev_at_export)
            and result.r_verbatim is not None):
        state.local_pushed_rev = result.r_verbatim
    if result.deferred or result.pending_files:
        db.flush()
        return
    state.server_cursor = int(server_rev)
    state.last_sync_at = utc_now()
    db.flush()
