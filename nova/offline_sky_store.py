"""Filesystem tile store for the offline-sky proxy. No database.

Layout under CACHE_DIR/hips/:

    <slug>/properties
    <slug>/Norder<o>/Dir<d>/Npix<n>.<ext>
    <slug>/Allsky.<ext>
    .total                 # summed byte count of browsed tiles (see below)
    .pins.json             # step 3 pin manifest; absent for now (empty pin set)

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
PINS_FILENAME = ".pins.json"  # step 3; not written yet, so the pin set is empty
EVICT_LOCK = ".evict"

_total_lock = threading.Lock()


def hips_root() -> str:
    """Built per call, so a patched CACHE_DIR (tests) is picked up."""
    return os.path.join(CACHE_DIR, HIPS_SUBDIR)


def survey_dir(slug: str) -> str:
    return os.path.join(hips_root(), slug)


def _total_path() -> str:
    return os.path.join(hips_root(), TOTAL_FILENAME)


def _pins_path() -> str:
    return os.path.join(hips_root(), PINS_FILENAME)


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


def pinned_rel_paths() -> set:
    """Rel paths that eviction must skip. Empty until step 3 writes a manifest."""
    try:
        import json
        with open(_pins_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return {str(p) for p in data}
        if isinstance(data, dict) and isinstance(data.get("paths"), list):
            return {str(p) for p in data["paths"]}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return set()


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
            if name in (TOTAL_FILENAME, PINS_FILENAME, EVICT_LOCK) or name == "properties":
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
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
