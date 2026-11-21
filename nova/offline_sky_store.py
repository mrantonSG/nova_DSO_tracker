"""Filesystem tile store for the offline-sky proxy. No database.

Layout under OFFLINE_SKY_DIR (default: the instance's own cache/hips):

    <slug>/properties
    <slug>/Norder<o>/Dir<d>/Npix<n>.<ext>
    <slug>/Norder3/Allsky.<ext>   # HiPS 1.0 location (not the survey root)
    <slug>/Moc.fits        # survey coverage (metadata, like properties)
    .total                 # summed byte count of browsed (unpinned) tiles
    .pinned                # summed byte count of pinned tiles (base + jobs)
    .base-pins.json        # instance-level base tiles (orders 0..N), never evicted
    .settings.json         # the installation's chosen store limit (see below)
    .downloads.json        # the ticked locations for downloads: (user, record_uid)
    <slug>/.missing.json   # tiles every upstream answered 404 for, per survey
    jobs/<job_id>.json     # job state + parameters (the job queue)
    jobs/<job_id>.cancel   # cancel marker, written by any process
    jobs/<job_id>.pins     # tiles a running job has claimed (one key per line)
    jobs/.runner.lock      # held by the one process that runs jobs
    .pins.lock             # serialises pin changes against eviction and delete

Sky tiles are not user-specific: the cache is shared across every user of the
instance, which is the one deliberate exception to the "cache keys include
username" rule.

One limit covers the whole store: pinned tiles (base layer and running jobs'
claims) plus browsed tiles. Pinned tiles are never evicted; browsed tiles fill
whatever is left and are evicted oldest-first to make room. ``properties`` and
``Moc.fits`` are metadata and never evicted or counted.

Where the limit comes from (see ``limit_mb``): the installation's choice in
``.settings.json`` if one was ever made, otherwise the ``OFFLINE_SKY_CACHE_MB``
startup default. That file holds ``{"cache_mb": <MiB>}``, or ``null`` for "No
limit" -- the sentinel matters, because an absent key means "no choice yet"
(fall back to the env default) while ``null`` means "the user chose No limit".
"No limit" is not "no eviction": it means the store keeps ``DISK_HEADROOM_BYTES``
free on the filesystem instead, so it still evicts when the disk gets tight. The
file is a dot-file at the store root, which is what keeps it out of the tile walk
(``_is_tile_rel``): it is never evicted and never counted.

Size accounting is O(1) on the request path: ``.total`` holds the summed size of
all browsed (unpinned) tiles and ``.pinned`` that of all pinned tiles. ``.total``
is adjusted incrementally when a tile is stored by the proxy and when a job
adopts an already-browsed tile (it becomes pinned). ``.pinned`` is recomputed
from disk whenever pins change (job checkpoint/end) -- never per tile request.
Eviction re-derives both from disk and rewrites them. A tile's mtime is its
last-access time (touched on serve, set on write).

Concurrency: the app runs under gunicorn (``-w 2 -k gthread --threads 4``), i.e.
multiple processes and threads. Writes go through write_file_atomic (temp file +
fsync + os.replace), so a tile is never observed half-written; the last writer
wins with identical bytes. Every write of ``.total`` / ``.pinned`` -- the
``.total`` read-modify-write in particular -- holds ``.total.lock``, a blocking
cross-process lock (``counter_lock``), so two workers' increments never
overwrite each other. Eviction and clearing are guarded by a non-blocking
cross-process flock so only one worker runs either at a time.

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
from contextlib import contextmanager

from nova.config import (BoundedCache, CACHE_DIR, INSTANCE_PATH, OFFLINE_SKY_CACHE_MB,
                         OFFLINE_SKY_DIR)
from nova.helpers import write_file_atomic, try_acquire_file_lock, release_file_lock
from nova.offline_sky_lock import hold_lock

# Free space the store always leaves on its filesystem. A limit set by the user
# never eats into this, and "No limit" means exactly this: fill the disk down to
# the headroom, then evict. Lives here (not in offline_sky_jobs, which imports
# this module) so eviction and downloads judge the disk by the same number.
DISK_HEADROOM_BYTES = 1024 * 1024 * 1024

# A survey with nothing measured yet still needs a size to judge a download by.
# 67 KiB is the DSS2 average; NSNS is larger but its "unknown" is shown as such.
FALLBACK_TILE_BYTES = 67 * 1024

HIPS_SUBDIR = "hips"
TOTAL_FILENAME = ".total"
PINNED_FILENAME = ".pinned"
# Survey metadata at a HiPS root: never evicted, never counted as tiles.
METADATA_FILES = frozenset({"properties", "Moc.fits"})
BASE_PINS_FILENAME = ".base-pins.json"  # instance-level low-res base tiles (never evicted)
SETTINGS_FILENAME = ".settings.json"     # the installation's chosen store limit
JOBS_DIRNAME = "jobs"                    # job state (<job_id>.json)
EVICT_LOCK = ".evict"
PINS_LOCK = ".pins.lock"
COUNTER_LOCK = ".total.lock"   # guards every .total / .pinned write
# A counter update never waits longer than this: it runs on the request path,
# and a lost update is corrected by the next eviction pass.
COUNTER_LOCK_TIMEOUT = 5.0
JOB_PINS_SUFFIX = ".pins"
# Tiles every upstream answered 404 for, per survey: {rel: last 404 time}. A
# download counts these as "not available on the server" -- done, not blocking.
MISSING_FILENAME = ".missing.json"

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


@contextmanager
def counter_lock():
    """Context manager: hold the cross-process lock on the ``.total`` /
    ``.pinned`` counters (blocking, not re-entrant).

    The in-process lock keeps this process's threads from polling the file lock
    against each other; the file lock (offline_sky_lock.hold_lock, as for the
    pins) serialises the gunicorn workers. Innermost lock: it may be taken while
    holding ``pins_lock()``, never the other way round. Raises OSError when the
    store cannot be written and TimeoutError after ``COUNTER_LOCK_TIMEOUT``.
    """
    with _total_lock:
        with hold_lock(os.path.join(hips_root(), COUNTER_LOCK),
                       timeout=COUNTER_LOCK_TIMEOUT):
            yield


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
    """Adjust the browsed total by ``n`` (may be negative). Never raises.

    The read-modify-write holds ``counter_lock``, so concurrent workers' adds all
    land. If the lock cannot be had (an unwritable store, a timeout) the update
    is skipped -- the next eviction pass rewrites the total from disk.
    """
    if n == 0:
        return
    try:
        with counter_lock():
            _set_total(total_bytes() + n)
    except OSError as exc:
        _store_failed(exc)
    except TimeoutError as exc:
        _log.warning("Offline sky: %s; the stored-size counter is corrected by "
                     "the next eviction pass.", exc)


def adjust_browsed_total(n: int) -> None:
    """Public: a tile moved between browsed and pinned (see module docstring)."""
    _add_bytes(n)


def pinned_bytes() -> int:
    """Summed size of pinned tiles on disk, or 0. O(1): reads .pinned."""
    return _read_int(_pinned_path())


def tile_path(slug: str, rel_path: str) -> str:
    return os.path.join(survey_dir(slug), rel_path)


def tile_rel(order: int, npix: int, ext: str) -> str:
    """The HiPS rel path of a tile: ``Norder{o}/Dir{d}/Npix{npix}.{ext}``."""
    return f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}.{ext}"


# ---------------------------------------------------------------------------
# Tiles the upstream does not have (recorded 404s)
#
# A tile every upstream answered 404 for is recorded per survey as
# ``<slug>/.missing.json``: {rel: last 404 time}. A download counts those as
# done -- "not available on the server" -- so a survey with holes in its
# footprint can still reach "ready". Retried at most once a day (the base
# layer's retry rule, unchanged).
# ---------------------------------------------------------------------------

MISSING_RETRY_SECONDS = 24 * 3600


def _missing_path(slug: str) -> str:
    return os.path.join(survey_dir(slug), MISSING_FILENAME)


def read_missing(slug: str) -> dict:
    """{rel: last 404 time} of tiles recorded as missing on every upstream."""
    try:
        with open(_missing_path(slug), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for rel, ts in data.items():
        try:
            out[str(rel)] = float(ts)
        except (TypeError, ValueError):
            continue
    return out


def write_missing(slug: str, record: dict) -> None:
    """Replace the survey's missing record; an empty record removes the file."""
    path = _missing_path(slug)
    if not record:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, record)


def missing_rel_paths(slug: str) -> set:
    """The rel paths recorded as "not available on the server" for ``slug``."""
    return set(read_missing(slug))


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


def read_json(path):
    """Read a JSON file, or ``None`` when it is missing or unreadable."""
    return _read_json(path, None)


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
    _write_counters(total, pinned_total)


# ---------------------------------------------------------------------------
# Job pins: tiles a running job has claimed but not yet handed to a pin set
# ---------------------------------------------------------------------------

def _job_pins_path(job_id: str) -> str:
    return os.path.join(jobs_dir(), f"{job_id}{JOB_PINS_SUFFIX}")


def _read_lines(path: str) -> set:
    """Keys from a claims file: ``rel<TAB>bytes`` lines, or old ``rel`` lines.

    The size is only an accounting hint; the key is what matters, so both
    formats read back as the same set of keys.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return {line.split("\t", 1)[0].strip() for line in handle if line.strip()}
    except (FileNotFoundError, OSError):
        return set()


def _read_claim_sizes(path: str) -> dict:
    """{key: bytes} from a claims file; old ``rel``-only lines yield no size."""
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                key, _sep, size = line.partition("\t")
                key = key.strip()
                if not key:
                    continue
                try:
                    out[key] = int(size)
                except (TypeError, ValueError):
                    out[key] = None
    except (FileNotFoundError, OSError):
        return {}
    return out


def job_pins(job_id: str) -> set:
    """Keys claimed by ``job_id`` (pinned until the job hands them to a manifest)."""
    return _read_lines(_job_pins_path(job_id))


def release_job_pins(job_id: str, keys) -> int:
    """Drop ``keys`` from ``job_id``'s claims file. Returns how many went.

    A trim deletes the tiles *and* releases their claims: leaving the claims
    behind would make the deleted tiles look owned, so ``could_free`` would keep
    reporting bytes that are already gone. Rewrites the file under the pin lock.
    """
    wanted = {str(k) for k in keys}
    if not wanted:
        return 0
    with pins_lock():
        path = _job_pins_path(job_id)
        sizes = _read_claim_sizes(path)
        if not sizes:
            return 0
        removed = [k for k in sizes if k in wanted]
        if not removed:
            return 0
        keep = {k: v for k, v in sizes.items() if k not in wanted}
        try:
            _rewrite_claims_locked(path, keep)
        except OSError:
            return 0
        return len(removed)


def _rewrite_claims_locked(path: str, sizes: dict) -> None:
    """Rewrite a claims file as one ``key<TAB>bytes`` line per key. Pin lock held."""
    payload = "".join(f"{key}\t{int(size or 0)}\n" for key, size in sizes.items())
    write_file_atomic(path, lambda p: _write_bytes(p, payload.encode("utf-8")))


# The keys each job's claims file holds, so a claim can skip a key the job
# already owns without re-reading the file. Validated on every use against the
# file's (inode, size, mtime_ns): a rewrite or delete by another process (trim,
# Remove, reconcile) makes the entry stale and it is re-read. Bounded per the
# codebase rule; only the runner claims, so a handful of jobs is plenty.
_CLAIM_KEYS_CACHE = BoundedCache(16)


def _file_sig(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _claimed_keys_locked(job_id: str) -> set:
    """The keys ``job_id`` already claims. Caller holds pins_lock()."""
    path = _job_pins_path(job_id)
    sig = _file_sig(path)
    cached = _CLAIM_KEYS_CACHE.get(job_id)
    if cached is not None and cached[0] == sig:
        return cached[1]
    keys = _read_lines(path) if sig is not None else set()
    _CLAIM_KEYS_CACHE[job_id] = (sig, keys)
    return keys


def set_claim_sizes(job_id: str, sizes: dict) -> int:
    """Record on-disk sizes in ``job_id``'s claim lines. Returns how many changed.

    A claim is written before its fetch, with size 0 when the tile is not on
    disk yet; the job reports the real size here at each checkpoint and at the
    end. A claim line with a size > 0 is what "on disk" means to the plan's
    status, so this is what lets a fetched tile count as ready without a stat
    per poll. Only keys already claimed are updated.
    """
    if not sizes:
        return 0
    with pins_lock():
        path = _job_pins_path(job_id)
        current = _read_claim_sizes(path)
        changed = 0
        for key, size in sizes.items():
            if key in current and int(current[key] or 0) != int(size or 0):
                current[key] = int(size or 0)
                changed += 1
        if changed:
            try:
                _rewrite_claims_locked(path, current)
            except OSError:
                return 0
        return changed


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


def claim_for_job(job_id: str, key: str, guard=None, size=None):
    """Pin ``key`` for ``job_id`` *before* the job fetches or adopts it.

    Under the pin lock: ``guard()`` (may raise to refuse the claim, e.g. the
    save was deleted), append the key (with its size when known), return the
    tile's on-disk size (None when absent). Once this returns, eviction can no
    longer remove the tile.

    The size in the claim line lets ``status()`` sum the download's bytes
    without stat()ing every tile; an unknown size is written as ``0`` and the
    line still reads back as the key.

    A key the job already claims is not appended again (a resumed or re-planned
    run walks the same tiles), so the file stays at one line per key.
    """
    with pins_lock():
        if guard is not None:
            guard()
        os.makedirs(jobs_dir(), exist_ok=True)
        on_disk = pinned_key_size(key)
        claimed = _claimed_keys_locked(job_id)
        if key in claimed:
            return on_disk
        line_size = on_disk if size is None else size
        path = _job_pins_path(job_id)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{key}\t{int(line_size or 0)}\n")
        claimed.add(key)
        _CLAIM_KEYS_CACHE[job_id] = (_file_sig(path), claimed)
        return on_disk


def job_pin_sizes(job_id: str) -> dict:
    """{key: bytes} for ``job_id``'s claims, from the claim lines."""
    return _read_claim_sizes(_job_pins_path(job_id))


def claimed_bytes(job_id: str) -> int:
    """Sum of the sizes in ``job_id``'s claim lines (0 for unknown sizes)."""
    return sum(v or 0 for v in job_pin_sizes(job_id).values())


def take_job_pins_locked(job_id: str) -> set:
    """Remove ``job_id``'s claim file and return its keys. Caller holds pins_lock()."""
    keys = job_pins(job_id)
    try:
        os.remove(_job_pins_path(job_id))
    except OSError:
        pass
    return keys


def pinned_rel_paths() -> set:
    """Rel paths eviction must skip: base tiles, plus every tile a running job
    has claimed."""
    return base_pinned_rel_paths() | all_job_pins()


def pinned_on_disk_keys() -> set:
    """Pinned keys whose tile is on disk -- what "ready" counts. No stat() per key.

    Base pins are trusted as on disk: ``reconcile_pins_with_disk`` drops any
    whose file is absent, at runner start and when a job finishes. A job claim
    counts only when its line records a size > 0 (``set_claim_sizes``); a claim
    written before its fetch, or one whose file has gone, reads 0 and so counts
    as still to fetch.
    """
    out = base_pinned_rel_paths()
    try:
        names = os.listdir(jobs_dir())
    except FileNotFoundError:
        return out
    for name in names:
        if name.endswith(JOB_PINS_SUFFIX) and not name.startswith("."):
            sizes = _read_claim_sizes(os.path.join(jobs_dir(), name))
            out |= {key for key, size in sizes.items() if size}
    return out


def reconcile_pins_with_disk() -> dict:
    """Make the pin records agree with the files on disk. Returns the counts.

    * A base pin whose file is absent is dropped: the base layer is no longer
      fetched, so a registered-but-never-fetched tile must not count as there.
    * Every job claim's recorded size is set to the file's real size, or 0 when
      the file is absent (the tile is still to fetch; the job fetches it on its
      next run). Duplicate claim lines collapse to one in the rewrite.

    Runs at runner start (once per runner process) and when a download
    finishes -- never per poll. One stat() per pinned key.
    """
    result = {"base_dropped": 0, "claims_absent": 0}
    if not os.access(hips_root(), os.W_OK):
        return result
    with pins_lock():
        base = base_pinned_rel_paths()
        present = {key for key in base if pinned_key_size(key) is not None}
        if len(present) != len(base):
            _write_json(_base_pins_path(), {"paths": sorted(present)})
            result["base_dropped"] = len(base) - len(present)
        try:
            names = os.listdir(jobs_dir())
        except FileNotFoundError:
            names = []
        for name in names:
            if not name.endswith(JOB_PINS_SUFFIX) or name.startswith("."):
                continue
            path = os.path.join(jobs_dir(), name)
            sizes = _read_claim_sizes(path)
            changed = False
            for key, recorded in sizes.items():
                actual = pinned_key_size(key)
                if actual is None:
                    result["claims_absent"] += 1
                    actual = 0
                if int(recorded or 0) != actual:
                    sizes[key] = actual
                    changed = True
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = sum(1 for line in handle if line.strip())
            except OSError:
                lines = len(sizes)
            if changed or lines != len(sizes):
                try:
                    _rewrite_claims_locked(path, sizes)
                except OSError:
                    pass
    return result


def measured_pinned_bytes() -> int:
    """Sum the pinned tiles' on-disk sizes, straight from the pin set.

    This, not the ``.pinned`` sidecar, is the truth about how much the pinned
    (downloaded) side of the store occupies. ``.pinned`` is a cache that only a
    pass rewrites, so between a pass being needed and its running it can read
    high or low; the over-limit warning the user sees must not be built from a
    number a pending pass is about to change. The pin set (base pins plus every
    running job's claims) is small and local, so measuring it is a bounded loop
    of stat() calls -- cheap enough for a status response, which is the only
    place that needs the authoritative value.
    """
    total = 0
    for key in pinned_rel_paths():
        size = pinned_key_size(key)
        if size:
            total += size
    return total


def refresh_pinned_bytes() -> int:
    """Recompute ``.pinned`` from disk (union of all pins, each tile once)."""
    total = measured_pinned_bytes()
    _write_counters(None, total)
    return total


# ---------------------------------------------------------------------------
# Measured tile sizes: per order, for the download estimate
# ---------------------------------------------------------------------------

_SIZE_CACHE = {}
_SIZE_CACHE_LOCK = threading.Lock()


def measured_avg_tile_bytes(slug: str):
    """(mean on-disk tile size, measured?) for a survey; fallback 67 KiB.

    The fallback is the DSS2 average: a survey with nothing on disk yet still
    needs a number to judge a download against the budget.
    """
    total = 0
    count = 0
    root = survey_dir(slug)
    for dirpath, _dirs, names in os.walk(root):
        if dirpath == root:
            continue  # metadata lives at the survey root, tiles under Norder*
        for name in names:
            if name.startswith(".") or name in METADATA_FILES or name.startswith("Allsky."):
                continue
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
                count += 1
            except OSError:
                continue
    if count:
        return total // count, True
    return FALLBACK_TILE_BYTES, False


# A per-order mean is trusted only from this many tiles of that order on disk;
# below it the estimate uses FALLBACK_TILE_BYTES for the order.
MIN_SIZE_SAMPLE = 50


def avg_tile_bytes_by_order(slug: str):
    """{order: mean bytes} for the orders with >= MIN_SIZE_SAMPLE tiles on disk.

    Orders with a smaller sample are left out, so the caller falls back to
    ``FALLBACK_TILE_BYTES`` for them. ``None`` when no order qualifies.

    Cached per survey; tile sizes are immutable once fetched, so the cache is
    dropped (``invalidate_size_cache``) only when a download finishes or a
    Remove/trim deletes tiles -- not per browsed tile.
    """
    with _SIZE_CACHE_LOCK:
        if slug in _SIZE_CACHE:
            return _SIZE_CACHE[slug]
    by_order = {}
    root = survey_dir(slug)
    for dirpath, _dirs, names in os.walk(root):
        if dirpath == root:
            continue
        # Tiles live in <root>/Norder<o>/Dir<d>/: the order is the first
        # component below the survey root, not the tile's own directory.
        order = _order_of_dir(os.path.relpath(dirpath, root).split(os.sep)[0])
        if order is None:
            continue
        for name in names:
            if name.startswith(".") or name in METADATA_FILES or name.startswith("Allsky."):
                continue
            try:
                size = os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            bucket = by_order.setdefault(order, [0, 0])
            bucket[0] += size
            bucket[1] += 1
    result = {o: total // n for o, (total, n) in by_order.items()
              if n >= MIN_SIZE_SAMPLE} or None
    with _SIZE_CACHE_LOCK:
        _SIZE_CACHE[slug] = result
    return result


def _order_of_dir(name: str):
    """The order number in a ``Norder<o>`` path component, or None."""
    base = os.path.basename(name)
    if not base.startswith("Norder"):
        return None
    try:
        return int(base[len("Norder"):])
    except ValueError:
        return None


def invalidate_size_cache(slug: str = None) -> None:
    """Drop the measured-size cache (after a download writes new tiles)."""
    with _SIZE_CACHE_LOCK:
        if slug is None:
            _SIZE_CACHE.clear()
        else:
            _SIZE_CACHE.pop(slug, None)


# ---------------------------------------------------------------------------
# The location selection: <store>/.downloads.json
#
# One entry per ticked location: the user it belongs to and the location's
# canonical record_uid. The sky is shared instance-wide, so the selection is
# instance-level too -- but each entry carries its user, so a multi-user
# instance can tell whose tick is whose. No schema change: the file is the
# whole state.
# ---------------------------------------------------------------------------

DOWNLOADS_FILENAME = ".downloads.json"
_DOWNLOADS_LOCK = threading.Lock()


def _downloads_path() -> str:
    return os.path.join(hips_root(), DOWNLOADS_FILENAME)


def read_selection() -> list:
    """The ticked locations: ``[{"user": str, "record_uid": str}, ...]``."""
    data = _read_json(_downloads_path(), None)
    if not isinstance(data, dict):
        return []
    entries = data.get("locations")
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        user = str(entry.get("user") or "").strip()
        uid = str(entry.get("record_uid") or "").strip()
        if user and uid:
            out.append({"user": user, "record_uid": uid})
    return out


def write_selection(entries) -> list:
    """Replace the selection with ``entries``; returns what was written."""
    cleaned = []
    seen = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        user = str(entry.get("user") or "").strip()
        uid = str(entry.get("record_uid") or "").strip()
        if not user or not uid:
            continue
        key = (user, uid)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"user": user, "record_uid": uid})
    with _DOWNLOADS_LOCK:
        os.makedirs(hips_root(), exist_ok=True)
        write_json_atomic(_downloads_path(), {"locations": cleaned})
    return cleaned


# ---------------------------------------------------------------------------
# Deleting a download's tiles
# ---------------------------------------------------------------------------

def delete_tiles(rel_paths) -> dict:
    """Delete ``rel_paths`` unless another pin still claims them.

    The caller must already hold the eviction lock (``with_evict_lock``) if it
    wants the cross-process lock order honoured; the pin lock is taken here. A
    path still claimed by base pins or another download's claims file is kept --
    that is what makes "Remove" exact when two downloads share tiles.

    Returns ``{"deleted": n, "kept": n, "bytes": n}``.
    """
    wanted = {str(p) for p in rel_paths}
    if not wanted:
        return {"deleted": 0, "kept": 0, "bytes": 0}
    with pins_lock():
        still_claimed = base_pinned_rel_paths() | all_job_pins()
        deleted = 0
        kept = 0
        freed = 0
        for key in wanted:
            if key in still_claimed:
                kept += 1
                continue
            slug, _, rel = key.partition("/")
            if not rel:
                continue
            path = os.path.join(hips_root(), slug, *rel.split("/"))
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except OSError:
                kept += 1
                continue
            deleted += 1
            freed += size
        _reconcile_totals_locked()
    return {"deleted": deleted, "kept": kept, "bytes": freed}


# ---------------------------------------------------------------------------
# The store limit: the installation's choice, else the OFFLINE_SKY_CACHE_MB
# startup default
#
# The choice lives in <store>/.settings.json as {"cache_mb": <MiB>}, with null
# meaning "No limit". An absent *key* is not the same as a null value: absent
# means nobody has chosen yet and the env default applies, null means the user
# picked No limit. The parsed value is memoised against the file's (mtime_ns,
# size), so the browse path pays one stat() per request instead of a read, and
# a change made by another gunicorn worker is picked up on the next stat.
# ---------------------------------------------------------------------------

DEFAULT_LIMIT_MB = 2048
SETTINGS_MAX_BYTES = 64 * 1024          # refuse to parse an absurd settings file

_settings_lock = threading.Lock()
_settings_cache = {"stamp": None, "data": {}}


def _settings_path() -> str:
    return os.path.join(hips_root(), SETTINGS_FILENAME)


def _file_stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_ABSENT = ("absent",)      # stamp standing for "the file is not there"


def _read_settings() -> dict:
    """The settings file as a dict, memoised on its (mtime_ns, size).

    An absent, unreadable, oversized or malformed file reads as {} -- i.e. "no
    choice made" -- rather than raising: a corrupt settings file must not stop
    the proxy from serving or the store from evicting.

    The absent case is memoised too (as ``_ABSENT`` rather than None), so a
    store with no settings file does not stat it on every tile request -- while
    a file that later appears still invalidates the memo, because its stamp is a
    real (mtime_ns, size) tuple and no longer equals ``_ABSENT``.
    """
    path = _settings_path()
    stamp = _file_stamp(path)
    memo_key = _ABSENT if stamp is None else stamp
    with _settings_lock:
        if _settings_cache["stamp"] == memo_key:
            return _settings_cache["data"]
    data = {}
    if stamp is not None and stamp[1] <= SETTINGS_MAX_BYTES:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    with _settings_lock:
        _settings_cache["stamp"] = memo_key
        _settings_cache["data"] = data
    return data


def invalidate_settings_cache() -> None:
    """Drop the memoised settings file, so the next read stats it again.

    Called by the writer in this process; other processes notice on their own,
    because the memo is keyed on the file's stamp.
    """
    with _settings_lock:
        _settings_cache["stamp"] = None
        _settings_cache["data"] = {}


def limit_mb():
    """The store limit in MiB, or None for "No limit".

    Precedence: the installation's choice if one was made, else the
    OFFLINE_SKY_CACHE_MB startup default (<= 0 there also meaning "No limit").
    """
    settings = _read_settings()
    if "cache_mb" in settings:
        raw = settings["cache_mb"]
        if raw is None:
            return None                      # the user chose No limit
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return env_limit_mb()            # malformed choice: fall back
        return value if value > 0 else None  # a non-positive limit is No limit
    return env_limit_mb()


def env_limit_mb():
    """OFFLINE_SKY_CACHE_MB as a positive MiB value, or None when uncapped."""
    try:
        value = int(OFFLINE_SKY_CACHE_MB)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT_MB
    return value if value > 0 else None


def set_limit_mb(mb) -> None:
    """Persist the installation's limit choice: ``mb`` MiB, or None for No limit.

    Written atomically, so no reader ever sees a half-written file. Raises OSError
    when the store is not writable -- the caller reports that rather than
    pretending the choice was kept (the in-process memo is only dropped once the
    bytes are on disk).
    """
    if mb is None:
        payload = {"cache_mb": None}
    else:
        value = int(mb)
        payload = {"cache_mb": value if value > 0 else None}
    write_json_atomic(_settings_path(), payload)
    invalidate_settings_cache()


def budget_bytes() -> int:
    """Bytes the whole store (browsed + pinned) may occupy: the tighter of the
    two limits that always apply.

    * the installation's limit, when it has one;
    * what is left on the filesystem once DISK_HEADROOM_BYTES stays free.

    Both bind. "No limit" is not "no eviction": it drops the first constraint and
    keeps the second, so an uncapped store still evicts as the disk fills. A set
    limit is capped by the disk too -- a 25 GB limit on a disk with 3 GB free must
    not be read as permission to fill it.

    ``<= 0`` means the store already overruns (a limit below the pinned size, or a
    disk inside its headroom) and *every* browsed tile must go; it is emphatically
    not "nothing to do", which is what a bare zero would mean to a caller.
    """
    by_disk = disk_free_bytes() - DISK_HEADROOM_BYTES
    mb = limit_mb()
    if mb is None:
        return by_disk
    try:
        by_limit = int(mb) * 1024 * 1024
    except (TypeError, ValueError):
        return by_disk
    return min(by_limit, by_disk)


def status() -> dict:
    """A snapshot for the Configuration page and the jobs. O(1).

    ``pinned`` is read from the ``.pinned`` sidecar, which is maintained on every
    claim, adopt, delete, checkpoint and job finish (``refresh_pinned_bytes`` and
    the accounting helpers), and re-derived from disk at startup and at job
    finish. Measuring every pin on each poll would be ~621 ms at 250k pins; the
    cached figure is the same number, just not recomputed per request.

    ``browsed`` stays the O(1) sidecar read. ``evicting`` is true while a pass
    holds the lock; the page keeps polling until it goes false.

    The caller must not hold the pin lock.
    """
    mb = limit_mb()
    browsed = total_bytes()
    pinned = pinned_bytes()
    return {
        "limit_mb": mb,
        "browsed": browsed,
        "pinned": pinned,
        "used": browsed + pinned,
        "budget": budget_bytes(),
        "free": disk_free_bytes(),
        "headroom": DISK_HEADROOM_BYTES,
        "downloads_available": downloads_available(),
        "evicting": evict_in_progress(),
    }


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


def _scan_tiles():
    """Walk the store once -> (browsed entries, pinned byte total).

    Browsed entries are ``(mtime, full_path, rel, size)``; pinned tiles are
    summed and dropped. Survey metadata, sidecars, locks, job files and
    ``.settings.json`` are skipped by ``_is_tile_rel``. The caller must hold the
    pin lock, or ``pinned_rel_paths()`` could change under the walk.
    """
    root = hips_root()
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
    return entries, pinned_total


def _apply_totals(browsed_bytes: int, pinned_total: int) -> None:
    """Write the reconciled .total/.pinned sidecars (see the callers' notes)."""
    _write_counters(browsed_bytes, pinned_total)


def _write_counters(browsed_bytes, pinned_total) -> None:
    """Set ``.total`` (unless None) and ``.pinned`` under ``counter_lock``, so a
    reconciled figure is never overwritten by an add that read the old one.
    Never raises; without the lock (an unwritable store, a timeout) the sidecars
    are still written, as they were before the lock existed."""
    def write():
        if browsed_bytes is not None:
            _set_total(browsed_bytes)
        _write_int(_pinned_path(), pinned_total)
    try:
        with counter_lock():
            write()
    except (OSError, TimeoutError):
        write()


def evict_browsed() -> dict:
    """Synchronous, oldest-first eviction until pinned + browsed <= the budget.

    Walks the tree (this is the rare, off-request path). The budget is
    ``budget_bytes()``: the chosen limit, or -- for "No limit" -- whatever keeps
    the disk's headroom free. Recomputes the true browsed and pinned byte totals
    from disk every pass and rewrites ``.total`` / ``.pinned``, so any drift from
    the cross-process read-modify-write is self-healed here (under the caller's
    eviction flock). Returns a summary.
    """
    with pins_lock():
        return _evict_browsed_locked()


def _evict_browsed_locked() -> dict:
    entries, pinned_total = _scan_tiles()
    current = sum(e[3] for e in entries)
    # Signed on purpose. A budget at or below zero is not "nothing to do": it
    # means the store already overruns the limit (a limit set below the pinned
    # size) or the disk is inside its headroom, and then every browsed tile goes.
    budget = budget_bytes() - pinned_total  # room left for browsed tiles
    removed = 0
    removed_bytes = 0
    if current > budget:
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
    _apply_totals(current - removed_bytes, pinned_total)
    return {"removed": removed, "bytes": removed_bytes, "total": current - removed_bytes,
            "pinned": pinned_total}


def _clear_browsed_locked() -> dict:
    """Delete every browsed tile, keeping pinned ones and all metadata.

    The same walk as eviction with the budget read as zero, so the result is
    "nothing browsed left" rather than "under the limit". Pinned tiles, survey
    metadata (properties, Moc.fits), the sidecars and the job queue are not
    touched; .total/.pinned are re-derived so a proxy write that raced the walk
    cannot leave a stale count behind.
    """
    entries, pinned_total = _scan_tiles()
    removed = 0
    removed_bytes = 0
    for _mtime, full, _rel, size in entries:
        try:
            os.remove(full)
        except OSError:
            continue
        removed += 1
        removed_bytes += size
    _apply_totals(0, pinned_total)
    return {"removed": removed, "bytes": removed_bytes, "total": 0, "pinned": pinned_total}


def clear_browsed() -> dict:
    """Remove every browsed tile. Pinned tiles are never touched (see module doc)."""
    with pins_lock():
        return _clear_browsed_locked()


_evict_thread_lock = threading.Lock()
_evict_running = False


def empty_pass() -> dict:
    """The summary a pass returns when there is no store directory to pass over.

    Every key a real pass returns, all zero: nothing on disk to remove, nothing
    to count. The folder is deliberately *not* created to be able to say this --
    making the store directory is startup's ``check_dir()`` job, not a button's,
    and a pass that created it would report a healthy, empty store for a path
    that is wrong (a typo in OFFLINE_SKY_DIR, a volume not mounted yet) while
    the real tiles sit somewhere the app is no longer looking.
    """
    return {"removed": 0, "bytes": 0, "total": 0, "pinned": 0}


def with_evict_lock(pass_fn):
    """Run ``pass_fn`` holding the cross-process eviction lock.

    Both passes (evict, clear) take the locks in this order -- ``.evict`` first,
    then the pin lock the pass function takes -- so two of them can never
    deadlock against each other. Returns whatever ``pass_fn`` returned, or None
    when another worker is already holding ``.evict`` (so a caller whose pass
    returns None itself must use a sentinel to tell the two apart, see
    ``_await_eviction_settled``).

    The store directory is read before anything touches it, the way
    ``evict_in_progress`` reads it. The lock file lives *inside* that directory,
    so this is also what keeps a missing folder from being an error: a folder
    that is not there has nothing for a pass to remove and nowhere to put the
    lock, and it reads as an empty pass (``empty_pass()``) rather than a
    FileNotFoundError out of the lock's ``open``. A folder that is there but
    cannot be written raises OSError -- the same failure ``set_limit_mb`` hands
    its caller -- so the route can answer "read_only" instead of a 500.
    """
    root = hips_root()
    if not os.path.isdir(root):
        return empty_pass()
    if not os.access(root, os.W_OK):
        raise OSError(f"offline sky store at {root} is not writable")
    fh = try_acquire_file_lock(_evict_lock_path())
    try:
        if fh is None:
            return None
        return pass_fn()
    finally:
        release_file_lock(fh)


def evict_in_progress() -> bool:
    """True when a pass holds ``.evict`` right now. Never blocks, never raises.

    A non-blocking borrow of the lock: if it is free, the probe takes it and
    releases it at once (nothing else ran in between), and the answer is no. The
    status route reports this so the page knows whether the numbers it is
    showing are settled or still due to change.

    A store directory that does not exist yet cannot be mid-pass, and the lock
    helper would raise trying to open the lock file in it, so that reads as False.
    """
    try:
        fh = try_acquire_file_lock(_evict_lock_path())
    except OSError:
        return False
    try:
        return fh is None
    finally:
        release_file_lock(fh)


def evict_now() -> dict:
    """Run one eviction pass under the cross-process eviction lock.

    Returns the pass summary, or None when another worker is evicting. A missing
    store directory reads as an empty pass; an unwritable one raises OSError
    (both per ``with_evict_lock``).
    """
    return with_evict_lock(evict_browsed)


def clear_now():
    """Run one clear pass under the cross-process eviction lock.

    Returns the pass summary, or None when another worker holds the lock (an
    eviction in flight, or a clear in another worker): the caller then reports
    "busy" instead of pretending the sky was emptied. A missing store directory
    reads as an empty pass -- "nothing to clear" -- with the folder left
    uncreated; an unwritable one raises OSError (both per ``with_evict_lock``).
    """
    return with_evict_lock(clear_browsed)


def maybe_evict_async() -> None:
    """Start a background eviction if pinned + browsed exceeds the budget.

    The budget is ``budget_bytes()``, not the configured limit: "No limit" still
    evicts once the disk is inside its headroom, so the trigger cannot treat an
    uncapped store as always-fine.

    At most one eviction runs at a time: an in-process flag plus a non-blocking
    cross-process flock keep the gunicorn workers from evicting concurrently.
    """
    if total_bytes() + pinned_bytes() <= budget_bytes():
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
        except OSError:
            # The store turned unwritable (or vanished) mid-pass: the pass
            # already did all it could. The next trigger tries again.
            _log.warning("Offline sky: eviction pass skipped -- the tile store is not writable.")
        finally:
            with _evict_thread_lock:
                _evict_running = False

    threading.Thread(target=_run, name="offline-sky-evict", daemon=True).start()
