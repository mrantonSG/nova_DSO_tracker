"""Filesystem tile store for the offline-sky proxy. No database.

Layout under CACHE_DIR/hips/:

    <slug>/properties
    <slug>/Norder<o>/Dir<d>/Npix<n>.<ext>
    <slug>/Allsky.<ext>
    .total                 # summed byte count of browsed tiles (see below)
    .base-pins.json        # instance-level base tiles (orders 0..N), never evicted
    pins/<uuid>.json       # per-user save manifests (name, cone, tile list, bytes)
    jobs/<job_id>.json     # job state (base/save progress, resume, cancel)

Sky tiles are not user-specific: the cache is shared across every user of the
instance, which is the one deliberate exception to the "cache keys include
username" rule.

Size accounting is O(1) on the request path: ``.total`` holds the summed size of
all browsed tiles and is updated on store/evict, never recomputed by walking the
tree. A tile's mtime is its last-access time (touched on serve, set on write).
Eviction walks the tree once, in the background, only when the cap is exceeded.

Concurrency: the app runs under gunicorn (``-w 2 -k gthread --threads 4``), i.e.
multiple processes and threads. Writes go through write_file_atomic (temp file +
fsync + os.replace), so a tile is never observed half-written; the last writer
wins with identical bytes. The ``.total`` read-modify-write is serialised by an
in-process lock and is best-effort across processes (a torn increment only makes
the cap a hair off). Eviction is guarded by a non-blocking cross-process flock so
only one worker evicts at a time.
"""

import os
import threading
import time

from nova.config import CACHE_DIR, OFFLINE_SKY_CACHE_MB
from nova.helpers import write_file_atomic, try_acquire_file_lock, release_file_lock

HIPS_SUBDIR = "hips"
TOTAL_FILENAME = ".total"
BASE_PINS_FILENAME = ".base-pins.json"  # instance-level low-res base tiles (never evicted)
PINS_DIRNAME = "pins"                    # per-user save manifests (<uuid>.json)
JOBS_DIRNAME = "jobs"                    # job state (<job_id>.json)
EVICT_LOCK = ".evict"

_total_lock = threading.Lock()


def hips_root() -> str:
    """Built per call, so a patched CACHE_DIR (tests) is picked up."""
    return os.path.join(CACHE_DIR, HIPS_SUBDIR)


def survey_dir(slug: str) -> str:
    return os.path.join(hips_root(), slug)


def _total_path() -> str:
    return os.path.join(hips_root(), TOTAL_FILENAME)


def _base_pins_path() -> str:
    return os.path.join(hips_root(), BASE_PINS_FILENAME)


def pins_dir() -> str:
    return os.path.join(hips_root(), PINS_DIRNAME)


def jobs_dir() -> str:
    return os.path.join(hips_root(), JOBS_DIRNAME)


def _evict_lock_path() -> str:
    return os.path.join(hips_root(), EVICT_LOCK)


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


def total_bytes() -> int:
    """Summed size of browsed tiles, or 0. O(1): reads the .total sidecar."""
    try:
        with open(_total_path(), "r", encoding="ascii") as handle:
            return int(handle.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _set_total(n: int) -> None:
    write_file_atomic(_total_path(), lambda p: _write_bytes(p, str(max(0, n)).encode("ascii")))


def _add_bytes(n: int) -> None:
    if n <= 0:
        return
    with _total_lock:
        _set_total(total_bytes() + n)


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


def touch(slug: str, rel_path: str) -> None:
    """Record a serve as last access (mtime). Never raises."""
    try:
        os.utime(tile_path(slug, rel_path), None)
    except OSError:
        pass


def store_file(slug: str, rel_path: str, data: bytes, *, count: bool = True) -> None:
    """Write hips/<slug>/<rel_path> atomically and, for tiles, add to the total."""
    path = tile_path(slug, rel_path)
    if os.path.exists(path):
        return  # another process stored it already; don't double-count
    write_file_atomic(path, lambda p: _write_bytes(p, data))
    if count:
        _add_bytes(len(data))


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
    path = os.path.join(pins_dir(), f"{uuid}.json")
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


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


def pinned_rel_paths() -> set:
    """Rel paths eviction must skip: base tiles plus every save manifest's tiles."""
    pinned = base_pinned_rel_paths()
    for _uuid, manifest in list_save_manifests():
        paths = manifest.get("paths")
        if isinstance(paths, list):
            pinned |= {str(p) for p in paths}
    return pinned


def cap_bytes() -> int:
    """The browsed-tile cap, in bytes (OFFLINE_SKY_CACHE_MB MiB)."""
    try:
        return int(OFFLINE_SKY_CACHE_MB) * 1024 * 1024
    except (TypeError, ValueError):
        return 2048 * 1024 * 1024


def evict_browsed() -> dict:
    """Synchronous, oldest-first eviction until browsed total <= the cap.

    Walks the tree (this is the rare, off-request path). Skips the properties
    file, the sidecar files, and anything in the pin manifest. Recomputes the
    true browsed byte total from disk every pass and rewrites ``.total`` to it,
    so any drift from the cross-process read-modify-write of the sidecar is
    self-healed here (under the caller's eviction flock). Returns a summary.
    """
    root = hips_root()
    cap = cap_bytes()
    pinned = pinned_rel_paths()
    entries = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name in (TOTAL_FILENAME, BASE_PINS_FILENAME, EVICT_LOCK) or name == "properties":
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            # Manifests and job state are metadata, not tiles; never evict them.
            if rel.split(os.sep, 1)[0] in (PINS_DIRNAME, JOBS_DIRNAME):
                continue
            if rel in pinned:
                continue
            try:
                st = os.stat(full)
            except FileNotFoundError:
                continue
            entries.append((st.st_mtime, full, rel, st.st_size))

    current = sum(e[3] for e in entries)
    removed = 0
    removed_bytes = 0
    if cap > 0 and current > cap:
        entries.sort(key=lambda e: e[0])  # oldest mtime first
        for _mtime, full, _rel, size in entries:
            if current - removed_bytes <= cap:
                break
            try:
                os.remove(full)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            removed += 1
            removed_bytes += size

    # Always reconcile .total to the true on-disk browsed bytes (even when no
    # tile was removed), so a lost cross-process increment is corrected.
    _set_total(current - removed_bytes)
    return {"removed": removed, "bytes": removed_bytes, "total": current - removed_bytes}


_evict_thread_lock = threading.Lock()
_evict_running = False


def maybe_evict_async() -> None:
    """Start a background eviction if browsed total exceeds the cap.

    At most one eviction runs at a time: an in-process flag plus a non-blocking
    cross-process flock keep the gunicorn workers from evicting concurrently.
    """
    if total_bytes() <= cap_bytes():
        return
    global _evict_running
    with _evict_thread_lock:
        if _evict_running:
            return
        _evict_running = True

    def _run():
        global _evict_running
        fh = try_acquire_file_lock(_evict_lock_path())
        try:
            if fh is None:
                return  # another worker is already evicting
            evict_browsed()
        finally:
            release_file_lock(fh)
            with _evict_thread_lock:
                _evict_running = False

    threading.Thread(target=_run, name="offline-sky-evict", daemon=True).start()
