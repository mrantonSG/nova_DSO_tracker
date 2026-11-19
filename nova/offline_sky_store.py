"""Filesystem tile store for the offline-sky proxy. No database.

Layout under OFFLINE_SKY_DIR (default: the instance's own cache/hips):

    <slug>/properties
    <slug>/Norder<o>/Dir<d>/Npix<n>.<ext>
    <slug>/Norder3/Allsky.<ext>   # HiPS 1.0 location (not the survey root)
    <slug>/Moc.fits        # survey coverage (metadata, like properties)
    .total                 # summed byte count of browsed (unpinned) tiles
    .pinned                # summed byte count of pinned tiles (base + jobs)
    .base-pins.json        # instance-level base tiles (orders 0..N), never evicted
    jobs/<job_id>.json     # job state + parameters (the job queue)
    jobs/<job_id>.cancel   # cancel marker, written by any process
    jobs/<job_id>.pins     # tiles a running job has claimed (one key per line)
    jobs/.runner.lock      # held by the one process that runs jobs
    .pins.lock             # serialises pin changes against eviction and delete

Sky tiles are not user-specific: the cache is shared across every user of the
instance, which is the one deliberate exception to the "cache keys include
username" rule.

One cap, OFFLINE_SKY_CACHE_MB, covers the whole store: pinned tiles (base layer
and running jobs' claims) plus browsed tiles. Pinned tiles are never evicted;
browsed tiles fill whatever is left and are evicted oldest-first to make room.
``properties`` and ``Moc.fits`` are metadata and never evicted or counted.

Size accounting is O(1) on the request path: ``.total`` holds the summed size of
all browsed (unpinned) tiles and ``.pinned`` that of all pinned tiles. ``.total``
is adjusted incrementally when a tile is stored by the proxy and when a job
adopts an already-browsed tile (it becomes pinned). ``.pinned`` is recomputed
from disk whenever pins change (job checkpoint/end) -- never per tile request.
Eviction re-derives both from disk and rewrites them, so drift from the
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
Claiming and the eviction pass run under one blocking cross-process lock
(``.pins.lock``), so eviction never works from a pin set that a claim changes
underneath it.

Writability: the store is optional. When its directory cannot be written (a
read-only mount, a wrong owner) browsing still works -- the proxy serves the
bytes it fetched and ``store_file`` reports the failure instead of raising -- and
only downloads are switched off, see ``DOWNLOADS_AVAILABLE``.
"""

import json
import logging
import os
import threading
import time

from nova.config import CACHE_DIR, INSTANCE_PATH, OFFLINE_SKY_CACHE_MB, OFFLINE_SKY_DIR
from nova.helpers import write_file_atomic, try_acquire_file_lock, release_file_lock
from nova.offline_sky_lock import hold_lock

HIPS_SUBDIR = "hips"
TOTAL_FILENAME = ".total"
PINNED_FILENAME = ".pinned"
# Survey metadata at a HiPS root: never evicted, never counted as tiles.
METADATA_FILES = frozenset({"properties", "Moc.fits"})
BASE_PINS_FILENAME = ".base-pins.json"  # instance-level low-res base tiles (never evicted)
JOBS_DIRNAME = "jobs"                    # job state (<job_id>.json)
EVICT_LOCK = ".evict"
PINS_LOCK = ".pins.lock"
JOB_PINS_SUFFIX = ".pins"

# The retired per-object save manifests lived here: pins/<uuid>.json. The folder
# is read once at startup (retire_save_entries) and never written again.
RETIRED_PINS_DIRNAME = "pins"

_total_lock = threading.Lock()
_log = logging.getLogger(__name__)

# False when the store's directory cannot be written. Set by check_dir() at
# startup; browsing keeps working either way, only downloads need it.
DOWNLOADS_AVAILABLE = True


def store_path() -> str:
    """The configured store directory: OFFLINE_SKY_DIR, else the instance cache.

    Built per call from the module-level names, so a patched CACHE_DIR or
    OFFLINE_SKY_DIR (tests) is picked up.
    """
    configured = (OFFLINE_SKY_DIR or "").strip()
    if not configured:
        return os.path.join(CACHE_DIR, HIPS_SUBDIR)
    if os.path.isabs(configured):
        return configured
    return os.path.join(INSTANCE_PATH, configured)


def hips_root() -> str:
    """The store directory (see store_path)."""
    return store_path()


def check_dir() -> bool:
    """Create the store directory and report whether it can be written.

    Sets ``DOWNLOADS_AVAILABLE``. Never raises: a store on a read-only mount
    must not stop the app from starting or the proxy from serving.
    """
    global DOWNLOADS_AVAILABLE
    path = hips_root()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        DOWNLOADS_AVAILABLE = False
        _log.warning("Offline sky: cannot create the tile store at %s (%s); "
                     "sky browsing still works, downloads are switched off.",
                     path, exc)
        return False
    if not os.access(path, os.W_OK):
        DOWNLOADS_AVAILABLE = False
        _log.warning("Offline sky: the tile store at %s is not writable; "
                     "sky browsing still works, downloads are switched off.", path)
        return False
    DOWNLOADS_AVAILABLE = True
    return True


def downloads_available() -> bool:
    """True when the store can be written (downloads possible)."""
    return bool(DOWNLOADS_AVAILABLE)


def survey_dir(slug: str) -> str:
    return os.path.join(hips_root(), slug)


def _total_path() -> str:
    return os.path.join(hips_root(), TOTAL_FILENAME)


def _pinned_path() -> str:
    return os.path.join(hips_root(), PINNED_FILENAME)


def _base_pins_path() -> str:
    return os.path.join(hips_root(), BASE_PINS_FILENAME)


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
    """Write a sidecar counter. Never raises: the store may be read-only."""
    try:
        write_file_atomic(path, lambda p: _write_bytes(p, str(max(0, int(n))).encode("ascii")))
    except OSError as exc:
        _store_failed(exc)


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


_store_failure_logged = False


def _store_failed(exc: OSError) -> None:
    """One log line per process when the store cannot be written."""
    global _store_failure_logged
    if not _store_failure_logged:
        _store_failure_logged = True
        _log.warning("Offline sky: cannot write to the tile store at %s (%s); "
                     "tiles are served but not kept.", hips_root(), exc)


def store_file(slug: str, rel_path: str, data: bytes, *, count: bool = True) -> bool:
    """Write hips/<slug>/<rel_path> atomically and, for tiles, add to the total.

    Returns True when this call wrote the file, False when it was already there
    (another process stored it; nothing is double-counted) or when the store is
    not writable. Never raises: the request path serves the bytes either way.
    """
    path = tile_path(slug, rel_path)
    try:
        if os.path.exists(path):
            return False
        write_file_atomic(path, lambda p: _write_bytes(p, data))
    except OSError as exc:
        _store_failed(exc)
        return False
    if count:
        _add_bytes(len(data))       # its own write is guarded too
    return True


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError, OSError):
        return default


def write_json_atomic(path, data) -> None:
    """Atomically write ``data`` (any JSON-serialisable value) to ``path``.

    The raw ``open`` stays in ``_write_bytes`` (the writer body
    ``write_file_atomic`` fsyncs and replaces).
    """
    write_file_atomic(path, lambda p: _write_bytes(p, json.dumps(data, ensure_ascii=False).encode("utf-8")))


def _write_json(path, data) -> None:
    write_json_atomic(path, data)


# ---------------------------------------------------------------------------
# Pins
#
# Two kinds of pin, both expressed as sets of "<slug>/<rel_path>" strings:
#   * base tiles  -- instance level, whole-sky orders 0..OFFLINE_SKY_BASE_ORDER,
#     written by the base job and never removed except when the base is re-fetched.
#   * job claims  -- tiles a running job has claimed before fetching them (see
#     jobs/<job_id>.pins below), handed to a pin set when the job ends.
# Eviction skips the union.
# ---------------------------------------------------------------------------

def base_pinned_rel_paths() -> set:
    data = _read_json(_base_pins_path(), {})
    if isinstance(data, dict) and isinstance(data.get("paths"), list):
        return {str(p) for p in data["paths"]}
    return set()


def add_base_pins(rel_paths) -> None:
    """Merge ``rel_paths`` into the instance-level base pin set."""
    merged = base_pinned_rel_paths() | {str(p) for p in rel_paths}
    _write_json(_base_pins_path(), {"paths": sorted(merged)})


# ---------------------------------------------------------------------------
# Retirement of the per-object save manifests
#
# "Save for offline" stored one manifest per (user, rig, object) under
# pins/<uuid>.json. The Configuration -> General download page replaces it, so
# the manifests are dropped on the next startup: the tiles they pinned stay on
# disk as ordinary browsed tiles (nothing is deleted), and the entries -- which
# only ever named what a save had fetched -- go with them.
# ---------------------------------------------------------------------------

def retired_pins_dir() -> str:
    return os.path.join(hips_root(), RETIRED_PINS_DIRNAME)


def retire_save_entries() -> int:
    """Drop every save manifest, keeping the tiles they pinned. Returns how many.

    Idempotent and safe with nothing to do (no pins/ directory, an empty one).
    Runs under the pin lock, so it cannot race a job; the job files of retired
    saves are removed with their manifests, which is what makes a job that was
    still queued stop. Logs one line per entry before it goes, then the summary.

    A store that cannot be written is left alone: there is nothing to retire
    there, and taking the pin lock on a read-only mount would only block.
    """
    directory = retired_pins_dir()
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except FileNotFoundError:
        return 0
    except OSError as exc:
        _store_failed(exc)
        return 0
    if not names:
        return 0
    if not os.access(hips_root(), os.W_OK):
        return 0

    retired = 0
    with pins_lock():
        for name in names:
            path = os.path.join(directory, name)
            entry_id = name[:-5]
            manifest = _read_json(path, None)
            if isinstance(manifest, dict):
                name_label = manifest.get("name") or "RA/Dec"
                surveys = manifest.get("surveys") or []
                tiles = len(manifest.get("paths") or []) + len(manifest.get("missing_paths") or [])
                # print, not logging.info: the app's logging config drops INFO, and
                # this is the one line that tells an operator what went.
                print(f"[STARTUP] Offline sky: retiring the saved entry {name_label!r} "
                      f"({', '.join(str(s) for s in surveys) or 'no survey'}, {tiles} tiles); "
                      f"its tiles stay on disk.")
            try:
                os.remove(path)
            except OSError:
                continue
            retired += 1
            # A job that was still queued or paused for this entry has nothing
            # left to write to, so its state, claims and marker go too.
            for suffix in (".json", ".cancel", JOB_PINS_SUFFIX):
                try:
                    os.remove(os.path.join(jobs_dir(), f"{entry_id}{suffix}"))
                except OSError:
                    pass
        try:
            os.rmdir(directory)
        except OSError:
            pass
        # The released tiles are browsed now: re-derive both totals from disk.
        _reconcile_totals_locked()
    if retired:
        plural = "y" if retired == 1 else "ies"
        print(f"[STARTUP] Offline sky: retired {retired} saved entr{plural}; their "
              f"tiles are ordinary browsed tiles now.")
    return retired


def _reconcile_totals_locked() -> None:
    """Rewrite .total and .pinned from disk. Caller holds the pin lock."""
    root = hips_root()
    pinned = {p.replace("/", os.sep) for p in pinned_rel_paths()}
    total = 0
    pinned_total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if not _is_tile_rel(rel):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if rel in pinned:
                pinned_total += size
            else:
                total += size
    with _total_lock:
        _set_total(total)
    _write_int(_pinned_path(), pinned_total)


# ---------------------------------------------------------------------------
# Job pins: tiles a running job has claimed but not yet handed to a pin set
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


def warm_up() -> None:
    """Read the survey metadata a job's planning needs, once per process.

    A survey's ``properties`` grows stale after a week, and reading it then means
    an upstream fetch (up to the socket timeout) inside whatever request happens
    to come first -- so the first plan after a week takes seconds. Reading it
    here, off the request path, means a job only ever reads cached files.
    """
    from nova.offline_sky_sources import SURVEY_SOURCES
    from nova.offline_sky_facts import survey_coverage, survey_facts
    for slug in SURVEY_SOURCES:
        # Both files a plan needs: the properties (its order range) and the MOC
        # (which tiles are covered). Reading only the first leaves the MOC fetch
        # on the request path, which is the bigger of the two.
        for read in (lambda s: survey_facts(s, fetch=True),
                     lambda s: survey_coverage(s, fetch=True)):
            try:
                read(slug)
            except Exception:
                continue      # upstream trouble is not the caller's problem here
    refresh_pinned_bytes()


def pinned_rel_paths() -> set:
    """Rel paths eviction must skip: base tiles, plus every tile a running job
    has claimed."""
    return base_pinned_rel_paths() | all_job_pins()


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
    """True for "<slug>/..." tile files (not metadata, sidecars or job files)."""
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return False  # files at the hips root are sidecars/locks
    if parts[0] in (RETIRED_PINS_DIRNAME, JOBS_DIRNAME):
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
