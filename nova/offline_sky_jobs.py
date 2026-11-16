"""Background offline-sky jobs: the low-res base layer and per-user saves.

Two kinds of job, both single-threaded passes that enumerate tiles and fetch only
the ones missing from disk, so a pass is cheap to re-run after a restart (resume
== re-run, skipping what is already there):

  * base job  -- per survey, fetches Allsky + every tile of orders 0..N
    (OFFLINE_SKY_BASE_ORDER), then pins those tiles at instance level.
  * save job  -- per user save (UUID), fetches the cone tiles of orders
    N+1..chosen for each chosen survey and writes the save's pin manifest.

Throttling is per process: at most OFFLINE_SKY_JOB_CONCURRENCY job-driven upstream
requests in flight, with OFFLINE_SKY_JOB_DELAY_SECONDS between requests. (The /hips/
proxy keeps its own, separate budget.) Job state is persisted under
CACHE_DIR/hips/jobs/<job_id>.json and re-read when a job is polled/cancelled, which
is how a job survives a process restart: the next poll notices a "running" state
with no live thread and starts a fresh pass.
"""

import os
import threading
import time
import traceback

from nova.config import (
    OFFLINE_SKY_BASE_ORDER,
    OFFLINE_SKY_JOB_CONCURRENCY,
    OFFLINE_SKY_JOB_DELAY_SECONDS,
)
from nova.offline_sky_sources import SURVEY_BY_SLUG
from nova.offline_sky_store import (
    add_base_pins,
    jobs_dir,
    read_file,
    read_save_manifest,
    store_file,
    tile_exists,
    write_save_manifest,
)
import nova.hips_healpix as hp

_JOB_SEMAPHORE = threading.BoundedSemaphore(OFFLINE_SKY_JOB_CONCURRENCY)

# live jobs: job_id -> {cancel: Event, thread: Thread}
_jobs = {}
_jobs_lock = threading.Lock()

_FORMAT_TO_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}


def _tile_rel(order: int, npix: int, ext: str) -> str:
    return f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}.{ext}"


def _allsky_rel(ext: str) -> str:
    return f"Allsky.{ext}"


def _cached_tile_ext(slug: str) -> str:
    """Tile extension for a survey, read from cached properties (no upstream).

    Falls back to the survey's first whitelisted ext when properties are not
    cached yet (the base job has not run, so nothing is on disk anyway).
    """
    src = SURVEY_BY_SLUG[slug]
    props = read_file(slug, "properties")
    if props:
        for line in props.decode("utf-8", "replace").splitlines():
            if line.startswith("hips_tile_format"):
                fmt = line.split("=", 1)[1].strip().lower()
                ext = _FORMAT_TO_EXT.get(fmt)
                if ext and ext in src.exts:
                    return ext
    return src.exts[0]


def _fetch_tile_format_ext(slug: str) -> str:
    """Tile ext, ensuring properties are fetched when possible (base/save path)."""
    src = SURVEY_BY_SLUG[slug]
    props = read_file(slug, "properties")
    if props:
        for line in props.decode("utf-8", "replace").splitlines():
            if line.startswith("hips_tile_format"):
                fmt = line.split("=", 1)[1].strip().lower()
                ext = _FORMAT_TO_EXT.get(fmt)
                if ext and ext in src.exts:
                    return ext
    # No cached properties: fetch them once (also what Aladin does on open).
    _ensure_properties(slug)
    props = read_file(slug, "properties")
    if props:
        for line in props.decode("utf-8", "replace").splitlines():
            if line.startswith("hips_tile_format"):
                fmt = line.split("=", 1)[1].strip().lower()
                ext = _FORMAT_TO_EXT.get(fmt)
                if ext and ext in src.exts:
                    return ext
    return src.exts[0]


def _ensure_properties(slug: str) -> None:
    from nova.blueprints.offline_sky import _serve_properties
    try:
        _serve_properties(slug)
    except Exception:
        pass


def _throttled_fetch(base: str, url: str):
    """One job-driven upstream fetch, inside the job concurrency budget.

    Reuses the /hips/ proxy's fetch (same session, timeouts, circuit breaker);
    on _UpstreamBusy (proxy saturated) or any failure it returns None.
    """
    from nova.blueprints import offline_sky as bp
    with _JOB_SEMAPHORE:
        try:
            return bp._fetch_upstream(base, url)
        except bp._UpstreamBusy:
            return None


def _fetch_tile(slug: str, rel_path: str):
    """Ensure a tile is on disk; return (bytes_written, was_present)."""
    if tile_exists(slug, rel_path):
        return 0, True
    src = SURVEY_BY_SLUG[slug]
    for base in src.upstreams:
        raw = _throttled_fetch(base, f"{base.rstrip('/')}/{rel_path}")
        if raw is None:
            continue
        store_file(slug, rel_path, raw, count=False)
        time.sleep(OFFLINE_SKY_JOB_DELAY_SECONDS)
        return len(raw), False
    return 0, False


# ---------------------------------------------------------------------------
# Job state (on disk)
# ---------------------------------------------------------------------------

def _job_path(job_id: str) -> str:
    return os.path.join(jobs_dir(), f"{job_id}.json")


def _read_job_state(job_id: str):
    import json
    try:
        with open(_job_path(job_id), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError, OSError):
        return None


def _write_job_state(state: dict) -> None:
    from nova.offline_sky_store import write_json_atomic
    os.makedirs(jobs_dir(), exist_ok=True)
    write_json_atomic(_job_path(state["id"]), state)


def _set_status(job_id: str, status: str, **extra) -> None:
    state = _read_job_state(job_id) or {"id": job_id, "kind": "base", "surveys": [],
                                        "done": 0, "total": 0, "bytes": 0}
    state.update(extra)
    state["status"] = status
    state["updated"] = time.time()
    _write_job_state(state)


def _live(job_id: str):
    with _jobs_lock:
        return _jobs.get(job_id)


def _cancel_event(job_id: str) -> threading.Event:
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {"cancel": threading.Event(), "thread": None})
        return job["cancel"]


def _spawn(job_id: str, target) -> None:
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {"cancel": threading.Event(), "thread": None})
        # Reuse the same cancel Event across a resume so a cancel set earlier wins.
        cancel = job["cancel"]
    thread = threading.Thread(target=target, args=(job_id, cancel), name=f"offline-sky-{job_id}", daemon=True)
    with _jobs_lock:
        _jobs[job_id]["thread"] = thread
    thread.start()


def cancel_job(job_id: str) -> bool:
    state = _read_job_state(job_id)
    if not state:
        return False
    if state.get("status") in ("done", "cancelled", "error"):
        return False
    _cancel_event(job_id).set()
    _set_status(job_id, "cancelled")
    return True


def _resume_if_stale(job_id: str, kind: str, target) -> None:
    """Start a pass for a job whose on-disk state is running but has no thread."""
    state = _read_job_state(job_id)
    if not state or state.get("status") not in ("queued", "running"):
        return
    live = _live(job_id)
    if live and live["thread"] is not None and live["thread"].is_alive():
        return
    _spawn(job_id, target)


# ---------------------------------------------------------------------------
# Base layer
# ---------------------------------------------------------------------------

def base_rel_paths(slug: str, ext: str = None):
    """Allsky + every tile of orders 0..OFFLINE_SKY_BASE_ORDER for a survey."""
    if ext is None:
        ext = _cached_tile_ext(slug)
    out = [_allsky_rel(ext)]
    for order in range(0, OFFLINE_SKY_BASE_ORDER + 1):
        for npix in range(hp.npix_order(order)):
            out.append(_tile_rel(order, npix, ext))
    return out


def base_complete(slug: str) -> bool:
    ext = _cached_tile_ext(slug)
    return all(tile_exists(slug, rel) for rel in base_rel_paths(slug, ext))


def _run_base(slug: str, job_id: str, cancel: threading.Event) -> None:
    try:
        ext = _fetch_tile_format_ext(slug)
        rels = base_rel_paths(slug, ext)
        _set_status(job_id, "running", kind="base", surveys=[slug], total=len(rels), done=0, bytes=0)
        fetched_bytes = 0
        done = 0
        for rel in rels:
            if cancel.is_set():
                _set_status(job_id, "cancelled", kind="base", surveys=[slug], done=done, bytes=fetched_bytes)
                return
            if tile_exists(slug, rel):
                done += 1
                continue
            raw = _fetch_tile(slug, rel)
            fetched_bytes += raw[0]
            if raw[0] or raw[1]:
                done += 1
            if (done % 64) == 0:
                _set_status(job_id, "running", kind="base", surveys=[slug], done=done, bytes=fetched_bytes)
        # Pin whatever is actually on disk now (instance level, never evicted).
        present = [rel for rel in rels if tile_exists(slug, rel)]
        add_base_pins([f"{slug}/{rel}" for rel in present])
        _set_status(job_id, "done", kind="base", surveys=[slug], done=len(rels),
                    bytes=fetched_bytes, present=len(present))
    except Exception as exc:  # pragma: no cover - defensive
        traceback.print_exc()
        _set_status(job_id, "error", kind="base", surveys=[slug], error=str(exc))
    finally:
        with _jobs_lock:
            _jobs.pop(job_id, None)


def ensure_base_survey(slug: str) -> dict:
    """Start (or resume) a base job for ``slug``; return its current state.

    Idempotent: a complete base returns immediately; an incomplete one starts a
    pass once, and a second call while it runs just reports it.
    """
    if slug not in SURVEY_BY_SLUG:
        return {"slug": slug, "status": "unknown", "complete": False}
    if base_complete(slug):
        return {"slug": slug, "status": "done", "complete": True}
    job_id = f"base:{slug}"
    state = _read_job_state(job_id)
    if state and state.get("status") in ("queued", "running"):
        _resume_if_stale(job_id, "base", lambda jid, c: _run_base(slug, jid, c))
        return {"slug": slug, "status": state.get("status"), "complete": False}
    _set_status(job_id, "running", kind="base", surveys=[slug], total=0, done=0, bytes=0)
    _spawn(job_id, lambda jid, c: _run_base(slug, jid, c))
    return {"slug": slug, "status": "running", "complete": False}


def base_job_progress(slug: str) -> dict:
    state = _read_job_state(f"base:{slug}")
    if not state:
        return {"slug": slug, "status": "idle", "complete": base_complete(slug)}
    return {**state, "slug": slug, "complete": base_complete(slug)}


# ---------------------------------------------------------------------------
# Save jobs
# ---------------------------------------------------------------------------

def save_rel_paths(slug: str, ext: str, ra: float, dec: float, radius_deg: float,
                   max_order: int):
    """Cone tile rel paths (orders base+1..max_order) for one survey."""
    src = SURVEY_BY_SLUG[slug]
    top = min(max_order, src.max_order)
    low = OFFLINE_SKY_BASE_ORDER + 1
    rels = []
    if top < low:
        return rels
    by_order = hp.cone_pixels(ra, dec, radius_deg, low, top)
    for order in range(low, top + 1):
        for npix in by_order.get(order, []):
            rels.append(_tile_rel(order, npix, ext))
    return rels


def _run_save(manifest: dict, job_id: str, cancel: threading.Event) -> None:
    try:
        slugs = [s for s in manifest.get("surveys", []) if s in SURVEY_BY_SLUG]
        ra = float(manifest["ra"])
        dec = float(manifest["dec"])
        radius_deg = float(manifest["radius_deg"])
        max_order = int(manifest["max_order"])
        all_paths = {}
        for slug in slugs:
            ext = _fetch_tile_format_ext(slug)
            all_paths[slug] = save_rel_paths(slug, ext, ra, dec, radius_deg, max_order)
        total = sum(len(v) for v in all_paths.values())
        _set_status(job_id, "running", kind="save", surveys=slugs, total=total, done=0, bytes=0)
        done = 0
        fetched_bytes = 0
        stored = {}  # slug -> [rel]
        for slug, rels in all_paths.items():
            stored[slug] = []
            for rel in rels:
                if cancel.is_set():
                    _finish_save(manifest, job_id, stored, fetched_bytes, "cancelled")
                    return
                if tile_exists(slug, rel):
                    stored[slug].append(rel)
                    done += 1
                    continue
                written, _present = _fetch_tile(slug, rel)
                fetched_bytes += written
                if tile_exists(slug, rel):
                    stored[slug].append(rel)
                done += 1
                if (done % 64) == 0:
                    _set_status(job_id, "running", kind="save", surveys=slugs, total=total,
                                done=done, bytes=fetched_bytes)
        _finish_save(manifest, job_id, stored, fetched_bytes, "done")
    except Exception as exc:  # pragma: no cover - defensive
        traceback.print_exc()
        _set_status(job_id, "error", kind="save", error=str(exc))
    finally:
        with _jobs_lock:
            _jobs.pop(job_id, None)


def _finish_save(manifest, job_id, stored, fetched_bytes, status):
    paths = []
    for slug, rels in stored.items():
        paths.extend(f"{slug}/{rel}" for rel in rels)
    manifest["paths"] = sorted(paths)
    manifest["bytes"] = fetched_bytes
    manifest["status"] = status
    write_save_manifest(manifest)
    _set_status(job_id, status, kind="save", surveys=manifest.get("surveys", []),
                total=sum(len(v) for v in stored.values()), done=sum(len(v) for v in stored.values()),
                bytes=fetched_bytes)


def start_save_job(manifest: dict) -> str:
    """Persist the manifest and start a save pass. Returns the job id (manifest id)."""
    job_id = manifest["id"]
    manifest.setdefault("paths", [])
    manifest.setdefault("bytes", 0)
    write_save_manifest(manifest)
    _set_status(job_id, "running", kind="save", surveys=manifest.get("surveys", []),
                total=0, done=0, bytes=0)
    _spawn(job_id, lambda jid, c: _run_save(manifest, jid, c))
    return job_id


def save_job_progress(job_id: str):
    state = _read_job_state(job_id)
    manifest = read_save_manifest(job_id)
    if not state and not manifest:
        return None
    if state and state.get("status") in ("queued", "running"):
        _resume_if_stale(job_id, "save",
                         lambda jid, c: _run_save(manifest, jid, c))
    return {"job": state, "manifest": manifest}


# ---------------------------------------------------------------------------
# Status (read-only, from the store)
# ---------------------------------------------------------------------------

def cone_max_cached_order(slug: str, ra: float, dec: float, radius_deg: float) -> int:
    """Highest order fully cached for the cone, or -1 when the base is incomplete.

    ``base`` (orders 0..N) is whole-sky, so a complete base counts as order N for
    any cone; higher orders are checked against the cone's tiles on disk only.
    """
    if slug not in SURVEY_BY_SLUG:
        return -1
    if not base_complete(slug):
        return -1
    src = SURVEY_BY_SLUG[slug]
    ext = _cached_tile_ext(slug)
    best = OFFLINE_SKY_BASE_ORDER
    for order in range(OFFLINE_SKY_BASE_ORDER + 1, src.max_order + 1):
        by_order = hp.cone_pixels(ra, dec, radius_deg, order, order)
        rels = [_tile_rel(order, npix, ext) for npix in by_order.get(order, [])]
        if not rels or not all(tile_exists(slug, rel) for rel in rels):
            break
        best = order
    return best


def avg_cached_tile_size(slug: str) -> int:
    """Mean on-disk tile size for a survey, falling back to 67 KiB when uncached."""
    from nova.offline_sky_store import survey_dir
    try:
        root = survey_dir(slug)
        total = 0
        count = 0
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if name == "properties":
                    continue
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                    count += 1
                except OSError:
                    continue
        if count:
            return total // count
    except OSError:
        pass
    return 67 * 1024


def estimate_save(ra: float, dec: float, radius_deg: float, slugs, max_order: int) -> dict:
    """Tile counts and byte estimates for a prospective save, plus cache budget."""
    from nova.offline_sky_store import cap_bytes, total_bytes
    surveys = []
    for slug in slugs:
        if slug not in SURVEY_BY_SLUG:
            continue
        ext = _cached_tile_ext(slug)
        rels = save_rel_paths(slug, ext, ra, dec, radius_deg, max_order)
        avg = avg_cached_tile_size(slug)
        surveys.append({"slug": slug, "tile_count": len(rels),
                        "est_bytes": int(len(rels) * avg)})
    cap = cap_bytes()
    return {"surveys": surveys, "free_bytes": max(0, cap - total_bytes()), "cap_bytes": cap}
