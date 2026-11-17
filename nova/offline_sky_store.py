"""Filesystem tile store for the offline-sky proxy. No database.

Layout under CACHE_DIR/hips/:

    <slug>/properties
    <slug>/Norder<o>/Dir<d>/Npix<n>.<ext>
    <slug>/Norder3/Allsky.<ext>   # HiPS 1.0 location (not the survey root)
    <slug>/Moc.fits        # survey coverage (metadata, like properties)
    .total                 # summed byte count of browsed (unpinned) tiles
    .pinned                # summed byte count of pinned tiles (base + saves)
    .base-pins.json        # instance-level base tiles (orders 0..N), never evicted
    pins/<uuid>.json       # per-user save manifests (name, frame, tile list, bytes)
    jobs/<job_id>.json     # job state + parameters (the job queue)
    jobs/<job_id>.cancel   # cancel marker, written by any process
    jobs/<job_id>.pins     # tiles a running job has claimed (one key per line)
    jobs/.runner.lock      # held by the one process that runs jobs
    .pins.lock             # serialises pin changes against eviction and delete

Sky tiles are not user-specific: the cache is shared across every user of the
instance, which is the one deliberate exception to the "cache keys include
username" rule.

One cap, OFFLINE_SKY_CACHE_MB, covers the whole store: pinned tiles (base layer
and saves) plus browsed tiles. Pinned tiles are never evicted, so a save is only
accepted when pinned + new fits under the cap; browsed tiles fill whatever is
left and are evicted oldest-first to make room. ``properties`` and ``Moc.fits``
are metadata and never evicted or counted.

Size accounting is O(1) on the request path: ``.total`` holds the summed size of
all browsed (unpinned) tiles and ``.pinned`` that of all pinned tiles. ``.total``
is adjusted incrementally when a tile is stored by the proxy, when a job adopts
an already-browsed tile into a save (it becomes pinned), and when a save is
deleted (its no-longer-pinned tiles become browsed). ``.pinned`` is recomputed
from disk whenever pins change (job checkpoint/end, delete) -- never per tile
request. Eviction re-derives both from disk and rewrites them, so drift from the
best-effort cross-process read-modify-write self-heals there. A tile's mtime is
its last-access time (touched on serve, set on write).

Concurrency: the app runs under gunicorn (``-w 2 -k gthread --threads 4``), i.e.
multiple processes and threads. Writes go through write_file_atomic (temp file +
fsync + os.replace), so a tile is never observed half-written; the last writer
wins with identical bytes. The ``.total`` read-modify-write is serialised by an
in-process lock and is best-effort across processes (a torn increment only makes
the cap a hair off). Eviction is guarded by a non-blocking cross-process flock so
only one worker evicts at a time.

Pins and eviction: a job claims each tile in ``jobs/<job_id>.pins`` *before* it
fetches or adopts it, so the tile is pinned from the moment it lands on disk.
Claiming, manifest writes/deletes and the eviction pass all run under one
blocking cross-process lock (``.pins.lock``), so eviction never works from a pin
set that a claim or a delete changes underneath it.
"""

import os
import threading
import time

from nova.config import CACHE_DIR, OFFLINE_SKY_CACHE_MB
from nova.helpers import write_file_atomic, try_acquire_file_lock, release_file_lock
from nova.offline_sky_lock import hold_lock

HIPS_SUBDIR = "hips"
TOTAL_FILENAME = ".total"
PINNED_FILENAME = ".pinned"
# Survey metadata at a HiPS root: never evicted, never counted as tiles.
METADATA_FILES = frozenset({"properties", "Moc.fits"})
BASE_PINS_FILENAME = ".base-pins.json"  # instance-level low-res base tiles (never evicted)
PINS_DIRNAME = "pins"                    # per-user save manifests (<uuid>.json)
JOBS_DIRNAME = "jobs"                    # job state (<job_id>.json)
EVICT_LOCK = ".evict"
PINS_LOCK = ".pins.lock"
JOB_PINS_SUFFIX = ".pins"

_total_lock = threading.Lock()


def hips_root() -> str:
    """Built per call, so a patched CACHE_DIR (tests) is picked up."""
    return os.path.join(CACHE_DIR, HIPS_SUBDIR)


def survey_dir(slug: str) -> str:
    return os.path.join(hips_root(), slug)


def _total_path() -> str:
    return os.path.join(hips_root(), TOTAL_FILENAME)


def _pinned_path() -> str:
    return os.path.join(hips_root(), PINNED_FILENAME)


def _base_pins_path() -> str:
    return os.path.join(hips_root(), BASE_PINS_FILENAME)


def pins_dir() -> str:
    return os.path.join(hips_root(), PINS_DIRNAME)


def jobs_dir() -> str:
    return os.path.join(hips_root(), JOBS_DIRNAME)


def _evict_lock_path() -> str:
    return os.path.join(hips_root(), EVICT_LOCK)


def pins_lock():
    """Context manager: the cross-process pin lock (blocking, not re-entrant)."""
    return hold_lock(os.path.join(hips_root(), PINS_LOCK))


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


def _read_int(path: str) -> int:
    try:
        with open(path, "r", encoding="ascii") as handle:
            return int(handle.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0


def _write_int(path: str, n: int) -> None:
    write_file_atomic(path, lambda p: _write_bytes(p, str(max(0, int(n))).encode("ascii")))


def total_bytes() -> int:
    """Summed size of browsed (unpinned) tiles, or 0. O(1): reads .total."""
    return _read_int(_total_path())


def _set_total(n: int) -> None:
    _write_int(_total_path(), n)


def _add_bytes(n: int) -> None:
    """Adjust the browsed total by ``n`` (may be negative)."""
    if n == 0:
        return
    with _total_lock:
        _set_total(total_bytes() + n)


def adjust_browsed_total(n: int) -> None:
    """Public: a tile moved between browsed and pinned (see module docstring)."""
    _add_bytes(n)


def pinned_bytes() -> int:
    """Summed size of pinned tiles on disk, or 0. O(1): reads .pinned."""
    return _read_int(_pinned_path())


def tile_path(slug: str, rel_path: str) -> str:
    return os.path.join(survey_dir(slug), rel_path)


def read_file(slug: str, rel_path: str):
    """Bytes at hips/<slug>/<rel_path>, or None when absent."""
    path = tile_path(slug, rel_path)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def tile_exists(slug: str, rel_path: str) -> bool:
    """True when hips/<slug>/<rel_path> is on disk (no read of the contents)."""
    return os.path.exists(tile_path(slug, rel_path))


def file_size(slug: str, rel_path: str):
    """Size in bytes of hips/<slug>/<rel_path>, or None when absent."""
    try:
        return os.path.getsize(tile_path(slug, rel_path))
    except OSError:
        return None


def pinned_key_size(key: str):
    """Size of a pinned "<slug>/<rel_path>" key on disk, or None when absent."""
    try:
        return os.path.getsize(os.path.join(hips_root(), *key.split("/")))
    except OSError:
        return None


def touch(slug: str, rel_path: str) -> None:
    """Record a serve as last access (mtime). Never raises."""
    try:
        os.utime(tile_path(slug, rel_path), None)
    except OSError:
        pass


def store_file(slug: str, rel_path: str, data: bytes, *, count: bool = True) -> bool:
    """Write hips/<slug>/<rel_path> atomically and, for tiles, add to the total.

    Returns True when this call wrote the file, False when it was already there
    (another process stored it; nothing is double-counted).
    """
    path = tile_path(slug, rel_path)
    if os.path.exists(path):
        return False
    write_file_atomic(path, lambda p: _write_bytes(p, data))
    if count:
        _add_bytes(len(data))
    return True


def _read_json(path, default=None):
    import json
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError, OSError):
        return default


def write_json_atomic(path, data) -> None:
    """Atomically write ``data`` (any JSON-serialisable value) to ``path``.

    Shared by the pin-manifest and job-state writers; the raw ``open`` stays in
    ``_write_bytes`` (the writer body ``write_file_atomic`` fsyncs and replaces).
    """
    import json
    write_file_atomic(path, lambda p: _write_bytes(p, json.dumps(data, ensure_ascii=False).encode("utf-8")))


def _write_json(path, data) -> None:
    write_json_atomic(path, data)


# ---------------------------------------------------------------------------
# Pin manifests
#
# Two kinds of pin, both expressed as sets of "<slug>/<rel_path>" strings:
#   * base tiles  -- instance level, whole-sky orders 0..OFFLINE_SKY_BASE_ORDER,
#     written by the base job and never removed except when the base is re-fetched.
#   * save tiles  -- one manifest per user save, keyed by a UUID under pins/.
# Eviction skips the union of the two. A tile referenced by several manifests is
# pinned until the last referencing manifest is deleted.
# ---------------------------------------------------------------------------

# A partial save (stopped for space, or failed) keeps its coverage only while
# something can still resume it -- see save_covers. A cancelled save never does:
# the user took it back, so its tiles go back to the browsing budget (they stay
# on disk, as ordinary evictable tiles; nothing is deleted here).
PARTIAL_STATUSES = ("stopped", "failed")

def base_pinned_rel_paths() -> set:
    data = _read_json(_base_pins_path(), {})
    if isinstance(data, dict) and isinstance(data.get("paths"), list):
        return {str(p) for p in data["paths"]}
    return set()


def add_base_pins(rel_paths) -> None:
    """Merge ``rel_paths`` into the instance-level base pin set."""
    merged = base_pinned_rel_paths() | {str(p) for p in rel_paths}
    _write_json(_base_pins_path(), {"paths": sorted(merged)})


def read_save_manifest(uuid: str):
    path = os.path.join(pins_dir(), f"{uuid}.json")
    data = _read_json(path, None)
    if not isinstance(data, dict):
        return None
    return data


def write_save_manifest(manifest: dict) -> None:
    os.makedirs(pins_dir(), exist_ok=True)
    _write_json(os.path.join(pins_dir(), f"{manifest['id']}.json"), manifest)


def delete_save_manifest(uuid: str) -> bool:
    """Remove a save manifest (and its job's claimed tiles) and keep the totals right.

    Tiles only this save pinned -- listed in the manifest or claimed by its
    running job -- become browsed again: their on-disk size moves from
    ``.pinned`` to ``.total`` (they are then ordinary, evictable tiles). Runs
    under the pin lock, so a job writing the manifest at the same moment either
    finishes first (and its tiles are released here) or finds it gone.
    """
    path = os.path.join(pins_dir(), f"{uuid}.json")
    with pins_lock():
        manifest = read_save_manifest(uuid)
        try:
            os.remove(path)
        except FileNotFoundError:
            return False
        keys = set(take_job_pins_locked(uuid))
        keys |= save_covers(manifest)
        if keys:
            still_pinned = pinned_rel_paths()
            released = 0
            for key in keys - still_pinned:
                size = pinned_key_size(key)
                if size:
                    released += size
            _add_bytes(released)
        refresh_pinned_bytes()
    return True


def list_save_manifests():
    """All save manifests as [(uuid, manifest), ...] (every user)."""
    out = []
    try:
        names = os.listdir(pins_dir())
    except FileNotFoundError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        uuid = name[:-5]
        manifest = read_save_manifest(uuid)
        if manifest is not None:
            out.append((uuid, manifest))
    return out


# ---------------------------------------------------------------------------
# Job pins: tiles a running job has claimed but not yet written to a manifest
# ---------------------------------------------------------------------------

def _job_pins_path(job_id: str) -> str:
    return os.path.join(jobs_dir(), f"{job_id}{JOB_PINS_SUFFIX}")


def _read_lines(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip()}
    except (FileNotFoundError, OSError):
        return set()


def job_pins(job_id: str) -> set:
    """Keys claimed by ``job_id`` (pinned until the job hands them to a manifest)."""
    return _read_lines(_job_pins_path(job_id))


def all_job_pins() -> set:
    out = set()
    try:
        names = os.listdir(jobs_dir())
    except FileNotFoundError:
        return out
    for name in names:
        if name.endswith(JOB_PINS_SUFFIX) and not name.startswith("."):
            out |= _read_lines(os.path.join(jobs_dir(), name))
    return out


def claim_for_job(job_id: str, key: str, guard=None):
    """Pin ``key`` for ``job_id`` *before* the job fetches or adopts it.

    Under the pin lock: ``guard()`` (may raise to refuse the claim, e.g. the
    save was deleted), append the key, return the tile's on-disk size (None
    when absent). Once this returns, eviction can no longer remove the tile.
    """
    with pins_lock():
        if guard is not None:
            guard()
        os.makedirs(jobs_dir(), exist_ok=True)
        with open(_job_pins_path(job_id), "a", encoding="utf-8") as handle:
            handle.write(key + "\n")
        return pinned_key_size(key)


def take_job_pins_locked(job_id: str) -> set:
    """Remove ``job_id``'s claim file and return its keys. Caller holds pins_lock()."""
    keys = job_pins(job_id)
    try:
        os.remove(_job_pins_path(job_id))
    except OSError:
        pass
    return keys


def save_covered_keys(manifest) -> set:
    """The keys one save covers: its own tiles plus the ones every mirror 404'd.

    Both are what the save means to have done; counting only ``paths`` would let
    the next save fetch the missing ones all over again.
    """
    keys = set()
    if not isinstance(manifest, dict):
        return keys
    for field in ("paths", "missing_paths"):
        value = manifest.get(field)
        if isinstance(value, list):
            keys |= {str(p) for p in value}
    return keys


def save_covers(manifest) -> set:
    """What one save *pins*: its coverage, or nothing when it cannot be resumed.

    A partial save (``stopped``, ``failed``) keeps its coverage while its job
    still has params, because that is what a later run resumes from (it picks up
    from the checkpoint rather than fetching the tiles again). A cancelled save,
    and a save with no params to resume with (the first step-2 version wrote
    none), covers nothing. A manifest with no status covers.
    """
    if not isinstance(manifest, dict):
        return set()
    status = manifest.get("status")
    if status == "cancelled":
        return set()
    if status in PARTIAL_STATUSES and not _job_has_params(manifest.get("id")):
        return set()
    # A save whose entry has been deleted covers nothing: the user removed it,
    # and its own file is only the record of what the job did. This is also what
    # heals a stale record after a merge.
    entry_id = manifest.get("entry_id")
    if entry_id and entry_id != manifest.get("id") and read_save_manifest(entry_id) is None:
        return set()
    return save_covered_keys(manifest)


def warm_up() -> None:
    """Read the metadata every estimate needs, once per process.

    A survey's ``properties`` grows stale after a week, and reading it then means
    an upstream fetch (up to the socket timeout) inside the request that happens
    to come first -- so the first estimate after a week takes seconds. Reading it
    here, off the request path, means an estimate only ever reads cached files.
    """
    from nova.offline_sky_sources import SURVEY_SOURCES
    from nova.offline_sky_facts import survey_coverage, survey_facts
    for slug in SURVEY_SOURCES:
        # Both files an estimate needs: the properties (its order range) and the
        # MOC (which tiles are covered). Reading only the first leaves the MOC
        # fetch on the request path, which is the bigger of the two.
        for read in (lambda s: survey_facts(s, fetch=True),
                     lambda s: survey_coverage(s, fetch=True)):
            try:
                read(slug)
            except Exception:
                continue      # upstream trouble is not the caller's problem here
    refresh_pinned_bytes()


def _job_has_params(job_id) -> bool:
    """True when the save's job still holds the params a re-run needs.

    Read off the job file rather than through nova.offline_sky_jobs, so the store
    keeps no import of the jobs layer. A save with no job file (or one the first
    step-2 version wrote without params) can never run again, so it resumes
    nothing.
    """
    if not job_id:
        return False
    data = _read_json(os.path.join(jobs_dir(), f"{job_id}.json"), None)
    return bool(isinstance(data, dict) and data.get("params"))


# ---------------------------------------------------------------------------
# Entries: one per (user, rig, object), not one per save
#
# A save merges into the entry it belongs to, so saving the same object for the
# same rig twice adds surveys to one entry instead of making a second row. The
# key is (user_id, rig_id, name): an entry made before rigs were recorded has no
# rig_id, so those match on name and identical geometry instead. A rig-less
# entry never merges into a rig-tagged one -- the rig-less one is superseded, not
# absorbed (see entry_match).
# ---------------------------------------------------------------------------

def entry_rig_id(manifest):
    """The rig a save was made for, or None (an entry from before rigs were
    recorded, or one saved without a rig)."""
    value = manifest.get("rig_id") if isinstance(manifest, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _same_geometry(a, b) -> bool:
    """True when two saves describe the same frame (to a tenth of an arcsecond)."""
    for field in ("ra", "dec", "frame_w_deg", "frame_h_deg"):
        try:
            if abs(float(a.get(field)) - float(b.get(field))) > 1e-5:
                return False
        except (TypeError, ValueError):
            return False
    return True


def entry_match(manifest, entries):
    """The entry ``manifest`` belongs to, or None to start a new one.

    Same user, same object name, and then: same rig when both name one; name and
    identical geometry when neither does. A save that names a rig never joins a
    rig-less entry (the older one cannot be shown a match claim) and a rig-less
    save never joins a rig-tagged entry.
    """
    uid = manifest.get("user_id")
    name = manifest.get("name")
    rig_id = entry_rig_id(manifest)
    for other in entries:
        if other.get("user_id") != uid or other.get("name") != name:
            continue
        other_rig = entry_rig_id(other)
        if rig_id is not None and other_rig == rig_id:
            return other
        if rig_id is None and other_rig is None and _same_geometry(manifest, other):
            return other
    return None


def merge_into_entry(entry, manifest) -> dict:
    """Fold ``manifest`` into ``entry`` in place and return the entry.

    Union, never replace: ``surveys`` and the covered keys grow, so a running job
    cannot overwrite what another save already has (the job's own manifest write
    goes through here too). ``created`` stays the earliest -- an entry is as old
    as its first save -- and ``max_order``/``target_order`` take the deepest
    detail reached.
    """
    entry["surveys"] = sorted(set(entry.get("surveys") or []) | set(manifest.get("surveys") or []))
    entry["paths"] = sorted(set(entry.get("paths") or []) | set(manifest.get("paths") or []))
    entry["missing_paths"] = sorted(set(entry.get("missing_paths") or [])
                                    | set(manifest.get("missing_paths") or []))
    entry["missing"] = len(entry["missing_paths"])
    for field in ("max_order", "target_order"):
        values = [v for v in (entry.get(field), manifest.get(field)) if isinstance(v, int)]
        if values:
            entry[field] = max(values)
    created = [c for c in (entry.get("created"), manifest.get("created")) if c]
    if created:
        entry["created"] = min(created)
    for field in ("rig_id", "rig_name"):
        if entry.get(field) is None and manifest.get(field) is not None:
            entry[field] = manifest[field]
    # Which saves make up this entry: its own id, plus the ones folded in. This
    # is what keeps a merged save's file from showing as a second row.
    ids = list(entry.get("merged_saves") or [entry.get("id")])
    for save_id in ([manifest.get("id")] + list(manifest.get("merged_saves") or [])):
        if save_id and save_id not in ids:
            ids.append(save_id)
    entry["merged_saves"] = [i for i in ids if i]
    # The status is the job's to write (it knows which save it is running); a
    # merge only grows what the entry holds.

    return entry


def _weakest_status(current, incoming):
    """The status an entry takes when a save with ``incoming`` joins it.

    Active beats final (the entry keeps being polled while any job runs, and the
    least far along of two active ones wins); among final statuses anything but
    ``done`` beats ``done``, so an entry never reads as finished while one of its
    saves stopped short.
    """
    order = ("queued", "running", "paused")
    if current in order or incoming in order:
        if current in order and incoming in order:
            return order[min(order.index(current), order.index(incoming))]
        return incoming if incoming in order else current
    if current in (None, "done"):
        return incoming if incoming else current
    return current


def reconcile_entries() -> int:
    """Fold duplicate entries from before rigs were recorded into one each.

    Two rig-less entries for the same user, the same object name and the same
    frame are one entry: the earliest one absorbs the others (their ``surveys``
    and covered keys), and their files are then deleted -- only after the merged
    one is on disk, so a crash can never lose a save. Entries with a rig are
    never touched, and a rig-less entry is never folded into a rig-tagged one.
    Returns how many files were removed.
    """
    entries = [m for _uuid, m in list_save_manifests()]
    groups = {}
    for manifest in entries:
        if entry_rig_id(manifest) is not None:
            continue
        key = (manifest.get("user_id"), manifest.get("name"))
        groups.setdefault(key, []).append(manifest)

    removed = 0
    for _key, group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda m: (m.get("created") or "", m.get("id") or ""))
        keeper, rest = group[0], group[1:]
        merged = merge_into_entry(dict(keeper), {})       # a copy, then fold each in
        try:
            for other in rest:
                if not _same_geometry(merged, other):
                    continue
                merge_into_entry(merged, other)
            write_save_manifest(merged)                   # the merged entry first
        except Exception:
            continue                                      # never delete on a failed write
        for other in rest:
            if not _same_geometry(merged, other):
                continue
            try:
                os.remove(os.path.join(pins_dir(), f"{other['id']}.json"))
                removed += 1
            except OSError:
                pass
    if removed:
        refresh_pinned_bytes()
    return removed


def add_entry_saves(entry_id, save_ids) -> None:
    """Record save ids on an entry, under the pin lock.

    Runs with the manifest write in ``admit_save`` (same lock), so a save is
    named on the entry before its job can run and no concurrent create can drop
    the other's id.
    """
    if not entry_id:
        return
    with pins_lock():
        entry = read_save_manifest(entry_id)
        if entry is None:
            return
        have = list(entry.get("merged_saves") or [])
        for save_id in save_ids:
            if save_id not in have:
                have.append(save_id)
        entry["merged_saves"] = have
        write_save_manifest(entry)


def finish_into_entry(job_id, entry_id) -> bool:
    """Hand a finished job's claims to its entry. True when claims were handed on.

    Called by the runner instead of the entry keeping its own claims file: the
    entry's coverage grows, so the tiles stay pinned with no second bookkeeping
    pass. False when the entry has been deleted meanwhile (the job's own
    ``write_manifest`` then stops, as for a deleted save).
    """
    with pins_lock():
        entry = read_save_manifest(entry_id)
        if entry is None:
            return False
        claimed = {k for k in take_job_pins_locked(job_id)
                   if pinned_key_size(k) is not None}
        entry["paths"] = sorted(set(entry.get("paths") or []) | claimed)
        entry["missing"] = len(entry.get("missing_paths") or [])
        entry["bytes"] = sum(pinned_key_size(k) or 0 for k in save_covered_keys(entry))
        write_save_manifest(entry)
        refresh_pinned_bytes()
    return True


def pinned_rel_paths() -> set:
    """Rel paths eviction must skip: base tiles, what every covering save holds
    and every tile a running job has claimed."""
    pinned = base_pinned_rel_paths()
    for _uuid, manifest in list_save_manifests():
        pinned |= save_covers(manifest)
    return pinned | all_job_pins()


def refresh_pinned_bytes() -> int:
    """Recompute ``.pinned`` from disk (union of all pins, each tile once)."""
    total = 0
    for key in pinned_rel_paths():
        size = pinned_key_size(key)
        if size:
            total += size
    _write_int(_pinned_path(), total)
    return total


def cap_bytes() -> int:
    """The whole-store cap, in bytes (OFFLINE_SKY_CACHE_MB MiB); <= 0 means no cap."""
    try:
        return int(OFFLINE_SKY_CACHE_MB) * 1024 * 1024
    except (TypeError, ValueError):
        return 2048 * 1024 * 1024


def disk_free_bytes() -> int:
    """Free bytes on the filesystem holding the store."""
    import shutil
    path = hips_root()
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    try:
        return int(shutil.disk_usage(path or ".").free)
    except OSError:
        return 0


def _is_tile_rel(rel: str) -> bool:
    """True for "<slug>/..." tile files (not metadata, sidecars, pins or jobs)."""
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return False  # files at the hips root are sidecars/locks
    if parts[0] in (PINS_DIRNAME, JOBS_DIRNAME):
        return False
    name = parts[-1]
    if name.startswith(".") or name in METADATA_FILES:
        return False
    if len(parts) == 2:
        return False  # anything else at a survey root is metadata too
    return True


def evict_browsed() -> dict:
    """Synchronous, oldest-first eviction until pinned + browsed <= the cap.

    Walks the tree (this is the rare, off-request path). Skips survey metadata
    (properties, Moc.fits), sidecar/lock files, manifests, job files, and every
    pinned tile. Recomputes the true browsed and pinned byte totals from disk
    every pass and rewrites ``.total`` / ``.pinned``, so any drift from the
    cross-process read-modify-write is self-healed here (under the caller's
    eviction flock). Returns a summary.
    """
    with pins_lock():
        return _evict_browsed_locked()


def _evict_browsed_locked() -> dict:
    root = hips_root()
    cap = cap_bytes()
    pinned = {p.replace("/", os.sep) for p in pinned_rel_paths()}
    entries = []
    pinned_total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if not _is_tile_rel(rel):
                continue
            try:
                st = os.stat(full)
            except FileNotFoundError:
                continue
            if rel in pinned:
                pinned_total += st.st_size
                continue
            entries.append((st.st_mtime, full, rel, st.st_size))

    current = sum(e[3] for e in entries)
    budget = cap - pinned_total  # room left for browsed tiles
    removed = 0
    removed_bytes = 0
    if cap > 0 and current > budget:
        entries.sort(key=lambda e: e[0])  # oldest mtime first
        for _mtime, full, _rel, size in entries:
            if current - removed_bytes <= budget:
                break
            try:
                os.remove(full)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            removed += 1
            removed_bytes += size

    # Always reconcile .total/.pinned to the true on-disk bytes (even when no
    # tile was removed), so a lost cross-process increment is corrected.
    with _total_lock:
        _set_total(current - removed_bytes)
    _write_int(_pinned_path(), pinned_total)
    return {"removed": removed, "bytes": removed_bytes, "total": current - removed_bytes,
            "pinned": pinned_total}


_evict_thread_lock = threading.Lock()
_evict_running = False


def evict_now() -> dict:
    """Run one eviction pass under the cross-process eviction lock.

    Returns the pass summary, or None when another worker is evicting.
    """
    fh = try_acquire_file_lock(_evict_lock_path())
    try:
        if fh is None:
            return None
        return evict_browsed()
    finally:
        release_file_lock(fh)


def maybe_evict_async() -> None:
    """Start a background eviction if pinned + browsed exceeds the cap.

    At most one eviction runs at a time: an in-process flag plus a non-blocking
    cross-process flock keep the gunicorn workers from evicting concurrently.
    """
    cap = cap_bytes()
    if cap <= 0 or total_bytes() + pinned_bytes() <= cap:
        return
    global _evict_running
    with _evict_thread_lock:
        if _evict_running:
            return
        _evict_running = True

    def _run():
        global _evict_running
        try:
            evict_now()
        finally:
            with _evict_thread_lock:
                _evict_running = False

    threading.Thread(target=_run, name="offline-sky-evict", daemon=True).start()
