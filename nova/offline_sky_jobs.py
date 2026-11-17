"""Offline-sky jobs: what a save covers, what it costs, and the one job runner.

Area, detail, cost
------------------
* Area is the imaging *frame* (whole mosaic extent when a mosaic is set), never
  the Aladin view: ``radius = half-diagonal x FRAME_MARGIN``. Rotation does not
  matter (the half-diagonal bounds any rotation). Requests whose radius exceeds
  MAX_SAVE_RADIUS_DEG are rejected.
* Detail comes from the rig: the target order is the lowest HiPS order whose
  pixel scale (58.63 deg x 3600 / (512 x 2^order)) is <= the rig's image scale
  (arcsec/px). A detail offset (-1, 0, +1) moves it; per survey the order is
  capped at the survey's effective max order (nova/offline_sky_facts.py), and
  the save holds orders OFFLINE_SKY_BASE_ORDER+1 .. capped order. Tiles outside a
  survey's coverage (its MOC) are skipped.
* An estimate counts the tiles not on disk yet; MB use the survey's measured
  average tile size on disk (fallback FALLBACK_TILE_BYTES).

Limits
------
OFFLINE_SKY_CACHE_MB caps the whole store (see nova/offline_sky_store.py). A save
is refused when pinned + reserved + new would exceed it, or when the disk would
drop below DISK_HEADROOM_BYTES free; "reserved" is what queued/running saves still
expect to add (their estimate minus what they have pinned). The final check and
the queueing happen under the store's pin lock, so two concurrent requests can
never both take the same room. Browsed tiles are evictable, so they never block a
save. The same two limits are re-checked before every tile a job fetches; when
one is hit the job stops ("stopped", reason "no_space") and keeps a manifest of
what it has.

One runner per instance
-----------------------
Under gunicorn there are several processes. Jobs live on disk as
``jobs/<id>.json`` (the queue); any process may enqueue a job or write a cancel
marker ``jobs/<id>.cancel``. Exactly one process -- whoever holds the
``jobs/.runner.lock`` file lock (nova/offline_sky_lock.py) -- runs jobs, one at a
time, oldest first, in a single thread. ``kick()`` (called by the API routes)
tries to take the lock; a process that cannot just returns, and status is read
from disk. The OS drops the lock when the holder dies, so the next process that
takes it resumes every queued/running/paused job (resume = re-enumerate and
skip what is on disk).

Pace and honesty
----------------
All job upstream requests share one RateLimiter (OFFLINE_SKY_JOB_RATE_PER_SEC,
about 4/s, never below MIN_JOB_RATE_PER_SEC): since only one runner exists per
instance, that is the instance's job rate. It is separate from the browsing
proxy's semaphore. A tile is missing only when every upstream of the survey was
asked and answered 404. Anything else that brings no tile -- a network error,
5xx, 429, an open breaker, even mixed with a 404 from another mirror -- pauses
the job, which retries the same tile; it is never counted as done. Final
status: done (``missing`` = N), stopped, cancelled, failed. Any unexpected
error still goes through the finish path (manifest, accounting, cancel marker)
and ends as "failed".

Accounting
----------
A save manifest lists every tile the save covers (fetched now or already on
disk); its bytes are summed from disk. Before a job fetches or adopts a tile it
claims it in ``jobs/<id>.pins`` (store.claim_for_job), so eviction treats the
tile as pinned from the moment it lands on disk; the claims are handed over to
the manifest (or the base pins) when the job ends. Manifest writes and claims
check, under the store's pin lock, that the save still exists, so a deleted save
never comes back. The manifest is checkpointed during the job and the final
manifest is written BEFORE the final job status. An already-browsed tile that a
job adopts moves from ``.total`` to ``.pinned``; a tile already pinned (by
another save, the base or this job's own claims from before a crash) is not.
"""

import json
import math
import os
import threading
import time
import traceback
from dataclasses import dataclass, field

from nova.config import OFFLINE_SKY_BASE_ORDER, OFFLINE_SKY_JOB_RATE_PER_SEC
from nova.offline_sky_facts import allsky_rel, survey_coverage, survey_facts
from nova.offline_sky_lock import try_lock, unlock
from nova.offline_sky_sources import SURVEY_BY_SLUG, SURVEY_ID_BY_SLUG
import nova.offline_sky_store as store
import nova.hips_healpix as hp

# --- Area -------------------------------------------------------------------
FRAME_MARGIN = 1.15
# Largest accepted save radius (deg). 10 deg covers a ~17 deg frame diagonal
# (e.g. a 2x2 mosaic of a 135 mm lens on APS-C) with the 15 % margin.
MAX_SAVE_RADIUS_DEG = 10.0

# --- Detail -----------------------------------------------------------------
HIPS_ORDER0_TILE_DEG = 58.63
HIPS_TILE_PX = 512
DETAIL_OFFSETS = (-1, 0, 1)
_MAX_ORDER = 29  # HEALPix limit

# --- Limits / cost ----------------------------------------------------------
# Keep at least this much free on the disk holding the store.
DISK_HEADROOM_BYTES = 1024 * 1024 * 1024
FALLBACK_TILE_BYTES = 67 * 1024

# --- Runner -----------------------------------------------------------------
MIN_JOB_RATE_PER_SEC = 0.1       # a rate <= 0 (or tiny) never switches pacing off
PAUSE_SECONDS = 30.0             # wait before retrying while upstream is unavailable
MAX_PAUSE_SECONDS = 30 * 60      # give up ("failed") after this long paused on one tile
CHECKPOINT_EVERY = 64            # tiles between manifest/state checkpoints
IDLE_POLL_SECONDS = 2.0          # how often an idle runner looks for new jobs
RUNNER_LOCK_NAME = ".runner.lock"

# Base tiles that every mirror answered 404 for are recorded per survey in
# <slug>/.missing.json ({rel: unix time of the last 404}). They count towards
# "base complete" and are retried at most once per MISSING_RETRY_SECONDS.
MISSING_FILE = ".missing.json"
MISSING_RETRY_SECONDS = 24 * 3600
_now = time.time  # wall clock for the persisted missing record (tests patch it)

ACTIVE_STATUSES = ("queued", "running", "paused")
FINAL_STATUSES = ("done", "stopped", "cancelled", "failed")

REASON_NO_SPACE = "no_space"
REASON_UPSTREAM = "upstream_unavailable"
REASON_DELETED = "deleted"


class LimitError(ValueError):
    """A request outside the accepted limits; ``code`` is machine-readable."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# Geometry and detail
# ---------------------------------------------------------------------------

def frame_radius_deg(frame_w_deg: float, frame_h_deg: float) -> float:
    """Save radius for a frame: half the diagonal, plus FRAME_MARGIN."""
    return math.hypot(frame_w_deg, frame_h_deg) / 2.0 * FRAME_MARGIN


def hips_pixel_scale_arcsec(order: int) -> float:
    return HIPS_ORDER0_TILE_DEG * 3600.0 / (HIPS_TILE_PX * (2 ** order))


def target_order_for_scale(image_scale_arcsec: float) -> int:
    """Lowest order whose HiPS pixel scale is <= the rig's image scale."""
    for order in range(0, _MAX_ORDER + 1):
        if hips_pixel_scale_arcsec(order) <= image_scale_arcsec:
            return order
    return _MAX_ORDER


def validate_frame(ra, dec, frame_w_deg, frame_h_deg) -> dict:
    """Normalised frame dict, or LimitError."""
    try:
        ra = float(ra) % 360.0
        dec = float(dec)
        w = float(frame_w_deg)
        h = float(frame_h_deg)
    except (TypeError, ValueError):
        raise LimitError("invalid_parameters", "ra, dec, frame_w_deg and frame_h_deg must be numbers")
    if not all(math.isfinite(v) for v in (ra, dec, w, h)):
        raise LimitError("invalid_parameters", "ra, dec, frame_w_deg and frame_h_deg must be finite")
    if not -90.0 <= dec <= 90.0:
        raise LimitError("invalid_parameters", "dec must be within -90..90")
    if w <= 0 or h <= 0:
        raise LimitError("invalid_parameters", "frame width and height must be > 0")
    radius = frame_radius_deg(w, h)
    if radius > MAX_SAVE_RADIUS_DEG:
        raise LimitError(
            "radius_too_large",
            f"The frame needs a {radius:.1f} deg radius; the limit is {MAX_SAVE_RADIUS_DEG:.0f} deg.",
            radius_deg=radius, max_radius_deg=MAX_SAVE_RADIUS_DEG)
    return {"ra": ra, "dec": dec, "frame_w_deg": w, "frame_h_deg": h, "radius_deg": radius}


def validate_offset(value) -> int:
    if value in (None, ""):
        return 0
    try:
        off = int(value)
    except (TypeError, ValueError):
        raise LimitError("invalid_parameters", "detail_offset must be -1, 0 or 1")
    if off not in DETAIL_OFFSETS:
        raise LimitError("invalid_parameters", "detail_offset must be -1, 0 or 1")
    return off


def _tile_rel(order: int, npix: int, ext: str) -> str:
    return f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}.{ext}"


@dataclass
class SurveyPlan:
    slug: str
    ext: str
    low: int                   # first saved order (base + 1)
    high: int                  # last saved order (requested, capped); < low = nothing
    requested_order: int
    effective_max_order: int
    coverage_reason: str
    coverage_fraction: float
    tiles: dict = field(default_factory=dict)   # order -> [npix in coverage]

    @property
    def capped(self) -> bool:
        return self.high < self.requested_order

    @property
    def orders(self):
        return list(range(self.low, self.high + 1)) if self.high >= self.low else []

    def rel_paths(self):
        for order in self.orders:
            for npix in self.tiles.get(order, []):
                yield _tile_rel(order, npix, self.ext)


def plan_survey(slug: str, ra: float, dec: float, radius_deg: float,
                requested_order: int, fetch: bool = True) -> SurveyPlan:
    facts = survey_facts(slug, fetch)
    cov = survey_coverage(slug, fetch)
    low = OFFLINE_SKY_BASE_ORDER + 1
    high = min(int(requested_order), facts.effective_max_order)
    plan = SurveyPlan(slug=slug, ext=facts.tile_ext, low=low, high=high,
                      requested_order=int(requested_order),
                      effective_max_order=facts.effective_max_order,
                      coverage_reason=cov.reason, coverage_fraction=cov.fraction)
    if high >= low:
        by_order = hp.cone_pixels(ra, dec, radius_deg, low, high)
        for order in range(low, high + 1):
            plan.tiles[order] = [n for n in by_order.get(order, []) if cov.contains(order, n)]
    return plan


# ---------------------------------------------------------------------------
# Base layer plan
# ---------------------------------------------------------------------------

def base_rel_paths(slug: str, fetch: bool = False):
    """Allsky (Norder3) + every covered tile of orders 0..min(N, effective max)."""
    facts = survey_facts(slug, fetch)
    cov = survey_coverage(slug, fetch)
    ext = facts.tile_ext
    out = [allsky_rel(ext)]
    top = min(OFFLINE_SKY_BASE_ORDER, facts.effective_max_order)
    for order in range(0, top + 1):
        for npix in range(hp.npix_order(order)):
            if cov.contains(order, npix):
                out.append(_tile_rel(order, npix, ext))
    return out


def _missing_path(slug: str) -> str:
    return os.path.join(store.survey_dir(slug), MISSING_FILE)


def read_missing(slug: str) -> dict:
    """{rel: last 404 time} of base tiles recorded as missing on every mirror."""
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


def _write_missing(slug: str, record: dict) -> None:
    path = _missing_path(slug)
    if not record:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    store.write_json_atomic(path, record)


def missing_due(slug: str) -> list:
    """Recorded-missing base tiles whose once-a-day retry is due."""
    now = _now()
    return [rel for rel, ts in read_missing(slug).items() if now - ts >= MISSING_RETRY_SECONDS]


def base_complete(slug: str) -> bool:
    """Every covered base tile (and the Allsky) is on disk or recorded as missing
    (404 from every mirror). Never contacts upstream."""
    if slug not in SURVEY_BY_SLUG:
        return False
    missing = read_missing(slug)
    return all(store.tile_exists(slug, rel) or rel in missing
               for rel in base_rel_paths(slug, fetch=False))


# ---------------------------------------------------------------------------
# Estimate and limits
# ---------------------------------------------------------------------------

def measured_avg_tile_bytes(slug: str):
    """(mean on-disk tile size, measured?) for a survey; fallback 67 KB."""
    total = 0
    count = 0
    root = store.survey_dir(slug)
    for dirpath, _dirs, names in os.walk(root):
        if dirpath == root:
            continue  # metadata lives at the survey root, tiles under Norder*/
        for name in names:
            if name.startswith(".") or name in store.METADATA_FILES or name.startswith("Allsky."):
                continue
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
                count += 1
            except OSError:
                continue
    if count:
        return total // count, True
    return FALLBACK_TILE_BYTES, False


def estimate(frame: dict, image_scale_arcsec: float, slugs, detail_offset: int = 0,
             fetch: bool = True) -> dict:
    """Per-survey and total cost of saving ``frame`` for ``slugs``, plus budget.

    ``frame`` comes from validate_frame. Tiles already on disk are not counted as
    new; tiles on disk but not pinned yet are reported as ``adopt_bytes`` (they
    become pinned, so they count against the cap, not the disk).
    """
    target = target_order_for_scale(image_scale_arcsec)
    requested = max(0, target + int(detail_offset))
    pinned = store.pinned_rel_paths()
    surveys = []
    tot_new = tot_all = tot_bytes = tot_adopt = 0
    for slug in slugs:
        plan = plan_survey(slug, frame["ra"], frame["dec"], frame["radius_deg"], requested, fetch)
        avg, measured = measured_avg_tile_bytes(slug)
        by_order = {}
        new = 0
        adopt = 0
        all_count = 0
        for order in plan.orders:
            o_total = o_new = 0
            for npix in plan.tiles.get(order, []):
                rel = _tile_rel(order, npix, plan.ext)
                o_total += 1
                size = store.file_size(slug, rel)
                if size is None:
                    o_new += 1
                elif f"{slug}/{rel}" not in pinned:
                    adopt += size
            by_order[str(order)] = {"tiles": o_total, "new": o_new}
            new += o_new
            all_count += o_total
        est = new * avg
        surveys.append({
            "slug": slug,
            "survey_id": SURVEY_ID_BY_SLUG.get(slug),
            "requested_order": requested,
            "effective_max_order": plan.effective_max_order,
            # The resolution this survey really serves for this frame: the
            # requested order capped at what the survey has (see plan.capped).
            "effective_pixel_scale_arcsec": hips_pixel_scale_arcsec(plan.effective_max_order),
            "capped": plan.capped,
            "orders": plan.orders,
            "by_order": by_order,
            "tiles_total": all_count,
            "tile_count": new,
            "avg_tile_bytes": avg,
            "avg_tile_measured": measured,
            "est_bytes": est,
            "adopt_bytes": adopt,
            "coverage": {"reason": plan.coverage_reason, "fraction": plan.coverage_fraction},
        })
        tot_new += new
        tot_all += all_count
        tot_bytes += est
        tot_adopt += adopt

    cap = store.cap_bytes()
    pinned_bytes = store.refresh_pinned_bytes()
    disk_free = store.disk_free_bytes()
    reserved_pin, reserved_disk = reserved_bytes()
    free_under_cap = max(0, cap - pinned_bytes - reserved_pin) if cap > 0 else None
    reject = limit_problem(pinned_bytes + reserved_pin, tot_bytes + tot_adopt,
                           tot_bytes + reserved_disk, disk_free, cap)
    return {
        "frame": frame,
        "radius_deg": frame["radius_deg"],
        "image_scale_arcsec": image_scale_arcsec,
        "target_order": target,
        "detail_offset": int(detail_offset),
        "requested_order": requested,
        # Resolution of the requested detail (before any per-survey cap).
        "requested_pixel_scale_arcsec": hips_pixel_scale_arcsec(requested),
        # ...and what the surveys really serve for this frame, which is the same
        # unless every one of them is capped shallower than the request.
        "effective_pixel_scale_arcsec": max(s["effective_pixel_scale_arcsec"]
                                            for s in surveys) if surveys else None,
        "surveys": surveys,
        "total": {"tile_count": tot_new, "tiles_total": tot_all,
                  "est_bytes": tot_bytes, "adopt_bytes": tot_adopt},
        "cap_bytes": cap,
        "pinned_bytes": pinned_bytes,
        "reserved_bytes": reserved_pin,
        "browsed_bytes": store.total_bytes(),
        "free_under_cap_bytes": free_under_cap,
        # Kept for the existing client: free space under the cap (browsed tiles
        # are evictable, so they count as free).
        "free_bytes": free_under_cap if free_under_cap is not None else disk_free,
        "disk_free_bytes": disk_free,
        "disk_headroom_bytes": DISK_HEADROOM_BYTES,
        "fits": reject is None,
        "reject": reject,
    }


def reserved_bytes():
    """(pinned, disk) bytes that queued/running/paused saves still expect to add.

    Per job: its estimate at create time (params ``reserve_bytes`` /
    ``reserve_disk_bytes``) minus what it has pinned / fetched so far (as of
    its last checkpoint, so this errs on the side of reserving too much).
    """
    pin = disk = 0
    for job in list_jobs():
        if job.get("kind") != "save" or job.get("status") not in ACTIVE_STATUSES:
            continue
        params = job.get("params") or {}
        try:
            pin += max(0, int(params.get("reserve_bytes", 0)) - int(job.get("added", 0)))
            disk += max(0, int(params.get("reserve_disk_bytes", 0)) - int(job.get("bytes", 0)))
        except (TypeError, ValueError):
            continue
    return pin, disk


def admit_save(manifest: dict, params: dict, slugs, add_pinned: int, add_disk: int):
    """Re-check the limits and queue a save as one step; None, or the problem.

    Runs under the store's pin lock, so two concurrent creates are serialised:
    the second one sees the first one's job in reserved_bytes().
    """
    with store.pins_lock():
        pinned = store.refresh_pinned_bytes()
        r_pin, r_disk = reserved_bytes()
        problem = limit_problem(pinned + r_pin, add_pinned, add_disk + r_disk,
                                store.disk_free_bytes(), store.cap_bytes())
        if problem:
            return problem
        store.write_save_manifest(manifest)
        enqueue(manifest["id"], "save", params, slugs)
    return None


def limit_problem(pinned_bytes: int, add_pinned: int, add_disk: int, disk_free: int, cap: int):
    """None when a save fits, else {"code", "message"}."""
    if cap > 0 and pinned_bytes + add_pinned > cap:
        return {"code": "over_cap",
                "message": "Saved sky would exceed the offline cache limit (OFFLINE_SKY_CACHE_MB)."}
    if disk_free - add_disk < DISK_HEADROOM_BYTES:
        return {"code": "disk_headroom",
                "message": "Not enough free disk space for this save."}
    return None


# ---------------------------------------------------------------------------
# Job files (the queue), cancel markers
# ---------------------------------------------------------------------------

def _job_path(job_id: str) -> str:
    return os.path.join(store.jobs_dir(), f"{job_id}.json")


def _cancel_path(job_id: str) -> str:
    return os.path.join(store.jobs_dir(), f"{job_id}.cancel")


def read_job(job_id: str):
    try:
        with open(_job_path(job_id), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_job(state: dict) -> None:
    os.makedirs(store.jobs_dir(), exist_ok=True)
    state["updated"] = time.time()
    store.write_json_atomic(_job_path(state["id"]), state)


def delete_job_file(job_id: str) -> None:
    """Remove a job's state, cancel marker and tile claims (not for a running job)."""
    for path in (_job_path(job_id), _cancel_path(job_id), store._job_pins_path(job_id)):
        try:
            os.remove(path)
        except OSError:
            pass


def list_jobs():
    out = []
    try:
        names = os.listdir(store.jobs_dir())
    except FileNotFoundError:
        return out
    for name in names:
        if name.endswith(".json"):
            state = read_job(name[:-5])
            if state:
                out.append(state)
    return out


def enqueue(job_id: str, kind: str, params: dict, surveys) -> dict:
    """Write a queued job file. Any process may call this; only the runner runs it."""
    state = {"id": job_id, "kind": kind, "status": "queued", "reason": None,
             "created": time.time(), "params": params, "surveys": list(surveys),
             "total": 0, "done": 0, "fetched": 0, "present": 0, "missing": 0,
             "bytes": 0}
    try:
        os.remove(_cancel_path(job_id))  # a stale marker must not cancel a new job
    except OSError:
        pass
    _write_job(state)
    return state


def cancel_job(job_id: str) -> bool:
    """Ask the runner to cancel (works from any process). False if not active."""
    state = read_job(job_id)
    if not state or state.get("status") not in ACTIVE_STATUSES:
        return False
    os.makedirs(store.jobs_dir(), exist_ok=True)
    with open(_cancel_path(job_id), "w", encoding="ascii") as handle:
        handle.write(str(time.time()))
    return True


def cancel_requested(job_id: str) -> bool:
    return os.path.exists(_cancel_path(job_id))


def sweep_stale_job_files() -> None:
    """Drop cancel markers of jobs that are not active (a cancel that raced the
    job's end), the state of save jobs whose save is gone, and the tile claims of
    a job with no state at all (a crash between the claim and its state file, or
    a state deleted by hand). Left behind, those claims pin tiles no save covers:
    nothing would ever release them. Runner only.

    Write-ahead is safe here: ``claim_for_job`` creates the claims file and the
    job state under the same pin lock, so a state file is never momentarily
    absent for a job that is being created.
    """
    try:
        names = os.listdir(store.jobs_dir())
    except FileNotFoundError:
        return
    for name in names:
        if name.endswith(".pins"):
            base = name[:-len(".pins")]
            if not os.path.exists(_job_path(base)):
                try:
                    os.remove(os.path.join(store.jobs_dir(), name))
                except OSError:
                    pass
        elif name.endswith(".cancel"):
            state = read_job(name[:-len(".cancel")])
            if not state or state.get("status") not in ACTIVE_STATUSES:
                try:
                    os.remove(os.path.join(store.jobs_dir(), name))
                except OSError:
                    pass
        elif name.endswith(".json"):
            state = read_job(name[:-5])
            if (state and state.get("kind") == "save"
                    and state.get("status") in FINAL_STATUSES
                    and store.read_save_manifest(state["id"]) is None):
                delete_job_file(state["id"])


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

class RateLimiter:
    """At most ``rate`` acquisitions per second (evenly spaced), thread-safe.

    ``rate`` is clamped to at least MIN_JOB_RATE_PER_SEC: zero, a negative or a
    non-number never turns the limit off.
    """

    def __init__(self, rate: float, clock=time.monotonic, sleep=time.sleep):
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            rate = MIN_JOB_RATE_PER_SEC
        if not math.isfinite(rate) or rate < MIN_JOB_RATE_PER_SEC:
            rate = MIN_JOB_RATE_PER_SEC
        self.interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            at = now if self._next is None else max(now, self._next)
            if at > now:
                self._sleep(at - now)
            self._next = at + self.interval


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

class _Stop(Exception):
    def __init__(self, status: str, reason=None):
        super().__init__(status)
        self.status = status
        self.reason = reason


class JobRunner:
    """Runs queued jobs while holding the instance-wide runner lock."""

    def __init__(self, rate=None, clock=time.monotonic, sleep=time.sleep,
                 pause_seconds=None, max_pause_seconds=None, limiter=None):
        # ``limiter`` (anything with .wait()) replaces the RateLimiter -- tests only.
        self.rate = limiter if limiter is not None else RateLimiter(
            OFFLINE_SKY_JOB_RATE_PER_SEC if rate is None else rate, clock, sleep)
        self.clock = clock
        self.sleep = sleep
        self.pause_seconds = PAUSE_SECONDS if pause_seconds is None else pause_seconds
        self.max_pause_seconds = MAX_PAUSE_SECONDS if max_pause_seconds is None else max_pause_seconds
        self._fh = None
        self._thread = None
        self._stop = threading.Event()
        self._reconciled = False   # pin totals rewritten once per process (see reconcile_pins)

    # --- lock ---
    @staticmethod
    def lock_path() -> str:
        return os.path.join(store.jobs_dir(), RUNNER_LOCK_NAME)

    @property
    def owns_lock(self) -> bool:
        return self._fh is not None

    def acquire(self) -> bool:
        if self._fh is None:
            self._fh = try_lock(self.lock_path())
        return self._fh is not None

    def release(self) -> None:
        unlock(self._fh)
        self._fh = None

    def reconcile_pins(self) -> None:
        """Rewrite the pin totals from disk, once per runner process.

        Tiles that only an unfinished save held (cancelled, or stopped with no
        space) are not pinned any more, so ``.total``/``.pinned`` are stale after
        an upgrade until something recomputes them. Cheap, and only once per
        process -- callers that need it up to date now (the save and estimate
        routes) still call ``refresh_pinned_bytes`` themselves.
        """
        if self._reconciled:
            return
        self._reconciled = True
        # Entries that predate the rig field were one per save; fold the ones that
        # describe the same object and frame into a single row (the M106 pair),
        # then re-derive the totals once for both passes.
        try:
            store.reconcile_entries()
        except Exception:
            traceback.print_exc()
        store.refresh_pinned_bytes()

    # --- loop ---
    def pending(self):
        jobs = [j for j in list_jobs() if j.get("status") in ACTIVE_STATUSES]
        jobs.sort(key=lambda j: (j.get("created") or 0, j.get("id")))
        return jobs

    def run_pending(self) -> int:
        """Run every active job once, oldest first. Only while holding the lock."""
        if not self.owns_lock:
            return 0
        sweep_stale_job_files()
        self.reconcile_pins()
        n = 0
        for state in self.pending():
            if self._stop.is_set():
                break
            self.run_job(state["id"])
            n += 1
        return n

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        def loop():
            while not self._stop.is_set():
                try:
                    self.run_pending()
                except Exception:  # pragma: no cover - defensive, keep the runner alive
                    traceback.print_exc()
                self._stop.wait(IDLE_POLL_SECONDS)

        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="offline-sky-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self.release()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- one job ---
    def run_job(self, job_id: str) -> None:
        state = read_job(job_id)
        if not state or state.get("status") not in ACTIVE_STATUSES:
            return
        try:
            if state.get("kind") == "base":
                self._run_base(state)
            else:
                self._run_save(state)
        except Exception as exc:
            # _run_save/_run_base already route errors through _finish; this is
            # the last resort when _finish itself fails. The job's claims file
            # stays, so its tiles stay pinned until the save is deleted or the
            # base job runs again.
            traceback.print_exc()
            state.update(status="failed", reason="error", error=str(exc))
            for step in (lambda: _write_job(state), lambda: os.remove(_cancel_path(job_id))):
                try:
                    step()
                except Exception:
                    pass

    # --- tile fetch ---
    def _fetch(self, slug: str, rel: str):
        """-> (outcome, nbytes).

        outcome: "fetched" | "present" | "missing" | "unavailable". "missing"
        only when every upstream of the survey was asked and answered 404; a
        skipped (open-breaker) or unavailable mirror makes it "unavailable".
        """
        from nova.blueprints import offline_sky as bp
        upstreams = SURVEY_BY_SLUG[slug].upstreams
        n_missing = 0
        for base in upstreams:
            if bp._breaker_open(base):
                continue  # no request made, no rate token spent
            self.rate.wait()
            outcome, body = bp._upstream_get(base, f"{base.rstrip('/')}/{rel}")
            if outcome == bp.UPSTREAM_OK and body is not None:
                if store.store_file(slug, rel, body, count=False):
                    return "fetched", len(body)
                return "present", 0  # another process stored it meanwhile
            if outcome == bp.UPSTREAM_MISSING:
                n_missing += 1
        if upstreams and n_missing == len(upstreams):
            return "missing", 0
        return "unavailable", 0

    def _pause(self, state: dict, since: float) -> None:
        """Wait out an upstream outage; raise _Stop on cancel or when too long."""
        if self.clock() - since >= self.max_pause_seconds:
            raise _Stop("failed", REASON_UPSTREAM)
        if state.get("status") != "paused":
            state.update(status="paused", reason=REASON_UPSTREAM)
            _write_job(state)
        waited = 0.0
        step = min(1.0, self.pause_seconds) if self.pause_seconds > 0 else 0.0
        while waited < self.pause_seconds:
            if cancel_requested(state["id"]):
                raise _Stop("cancelled")
            self.sleep(step)
            waited += step
        if cancel_requested(state["id"]):
            raise _Stop("cancelled")

    def _process(self, state: dict, items, covered: list, missing: list, on_checkpoint,
                 skip_missing=frozenset(), claim_guard=None):
        """Fetch/adopt every (slug, rel) of a job; raises _Stop to end early.

        ``covered`` / ``missing`` are the caller's lists, filled in place with
        "<slug>/<rel>" keys, so a stopped or cancelled job still knows exactly
        what it has for its partial manifest. ``skip_missing`` holds keys known
        to be missing and not due for a retry: counted as missing, not fetched.
        Every tile is claimed for the job (store.claim_for_job) before it is
        looked at, so it is pinned before it is fetched; ``claim_guard`` runs
        under the pin lock with each claim and may raise _Stop.
        """
        job_id = state["id"]
        cap = store.cap_bytes()
        pinned_keys = store.pinned_rel_paths()       # includes this job's own checkpoint
        pinned_now = store.refresh_pinned_bytes()
        added = 0                                    # bytes this run moved into pinned
        avg = {}
        state.update(status="running", reason=None, total=len(items), done=0,
                     fetched=0, present=0, missing=0, bytes=0, added=0)
        _write_job(state)
        for i, (slug, rel) in enumerate(items):
            if cancel_requested(job_id):
                raise _Stop("cancelled")
            key = f"{slug}/{rel}"
            paused_since = None
            size = store.claim_for_job(job_id, key, claim_guard)
            while True:
                if size is None and key in skip_missing:
                    missing.append(key)
                    state["missing"] += 1
                    break
                if size is not None:
                    if key not in pinned_keys:
                        # A browsed tile becomes pinned: move it between totals.
                        store.adjust_browsed_total(-size)
                        pinned_keys.add(key)
                        added += size
                        state["added"] = added
                    covered.append(key)
                    state["present"] += 1
                    break
                if slug not in avg:
                    avg[slug] = measured_avg_tile_bytes(slug)[0]
                if cap > 0 and pinned_now + added + avg[slug] > cap:
                    raise _Stop("stopped", REASON_NO_SPACE)
                if store.disk_free_bytes() - avg[slug] < DISK_HEADROOM_BYTES:
                    raise _Stop("stopped", REASON_NO_SPACE)
                outcome, nbytes = self._fetch(slug, rel)
                if outcome == "fetched":
                    pinned_keys.add(key)
                    added += nbytes
                    state["added"] = added
                    covered.append(key)
                    state["fetched"] += 1
                    state["bytes"] += nbytes
                    break
                if outcome == "present":
                    size = store.file_size(slug, rel)
                    continue  # adopt it on the next pass of this loop
                if outcome == "missing":                   # 404 from every mirror
                    missing.append(key)
                    state["missing"] += 1
                    break
                # unavailable: pause and retry this same tile; not done.
                if paused_since is None:
                    paused_since = self.clock()
                self._pause(state, paused_since)
                size = store.file_size(slug, rel)
            if state.get("status") == "paused":
                state.update(status="running", reason=None)
                _write_job(state)
            state["done"] = i + 1
            if (i + 1) % CHECKPOINT_EVERY == 0:
                on_checkpoint(covered, missing)
                _write_job(state)

    # --- save ---
    def _run_save(self, state: dict) -> None:
        job_id = state["id"]
        p = state["params"]
        manifest_path = os.path.join(store.pins_dir(), f"{job_id}.json")

        def save_exists():
            """Claim guard: runs under the pin lock, so a delete can't slip in.

            What must exist is where this save's tiles belong: the entry it was
            merged into, or -- for a save that is its own entry -- its own file.
            """
            own = store.read_save_manifest(job_id)
            target = (own.get("entry_id") if own else None) or job_id
            if store.read_save_manifest(target) is None:
                raise _Stop("cancelled", REASON_DELETED)

        def write_manifest(covered, missing, status, final=False):
            """Write what this save has so far, under the pin lock.

            The save's own manifest is the record of what its job did; the entry
            it belongs to (``entry_id``) gets the union of the two. A standalone
            save is its own entry, so both writes land on one file. ``final``:
            keep the pins on the entry and drop the job's claims file, returning
            True when a claimed tile is on disk but now pinned by nothing.
            """
            leftover = False
            current_in = {"paths": sorted(set(covered)), "missing_paths": sorted(set(missing)),
                          "missing": len(set(missing)), "status": status,
                          "partial": status != "done"}
            with store.pins_lock():
                current = store.read_save_manifest(job_id)
                if current is None:
                    if final:
                        store.take_job_pins_locked(job_id)
                    raise _Stop("cancelled", REASON_DELETED)  # save deleted meanwhile
                entry_id = current.get("entry_id") or job_id
                current.update(current_in)
                current["bytes"] = sum(store.pinned_key_size(k) or 0
                                       for k in store.save_covered_keys(current))
                store.write_save_manifest(current)
                if entry_id != job_id:
                    entry = store.read_save_manifest(entry_id)
                    if entry is None:
                        if final:
                            store.take_job_pins_locked(job_id)
                        raise _Stop("cancelled", REASON_DELETED)  # entry deleted meanwhile
                    store.merge_into_entry(entry, current_in)
                    store.write_save_manifest(entry)
                if final:
                    claimed = store.take_job_pins_locked(job_id)
                    loose = claimed - store.pinned_rel_paths()
                    # Only a tile that is on disk *and* now pinned by nothing else
                    # moved from pinned to browsed without being handed on. A tile
                    # the base already pins was never this job's to release.
                    leftover = any(
                        store.pinned_key_size(k) is not None
                        and k not in store.base_pinned_rel_paths()
                        for k in loose
                    )
                store.refresh_pinned_bytes()
            return leftover

        covered, missing = [], []
        status, reason, error = "done", None, None
        try:
            save_exists()
            items = []
            for slug in state.get("surveys", []):
                if slug not in SURVEY_BY_SLUG:
                    continue
                plan = plan_survey(slug, p["ra"], p["dec"], p["radius_deg"], p["order"], fetch=True)
                items.extend((slug, rel) for rel in plan.rel_paths())
            self._process(state, items, covered, missing,
                          lambda c, m: write_manifest(c, m, "running"),
                          claim_guard=save_exists)
        except _Stop as stop:
            status, reason = stop.status, stop.reason
        except Exception as exc:
            traceback.print_exc()
            status, reason, error = "failed", "error", str(exc)
        self._finish(state, status, reason, covered, missing,
                     lambda c, m, st: write_manifest(c, m, st, final=True), error)

    # --- base ---
    def _run_base(self, state: dict) -> None:
        slug = state["params"]["slug"]
        job_id = state["id"]
        prefix = f"{slug}/"
        now = _now()
        # Recorded-missing tiles are retried at most once a day; until then the
        # job counts them as missing without asking upstream.
        recent = {prefix + rel for rel, ts in read_missing(slug).items()
                  if now - ts < MISSING_RETRY_SECONDS}

        def pin(covered, missing, final=False):
            leftover = False
            with store.pins_lock():
                store.add_base_pins(covered)
                if final:
                    claimed = store.take_job_pins_locked(job_id)
                    loose = claimed - store.pinned_rel_paths()
                    leftover = any(store.pinned_key_size(k) is not None for k in loose)
                store.refresh_pinned_bytes()
            record = read_missing(slug)
            for key in covered:                      # found after all: forget it
                record.pop(key[len(prefix):], None)
            for key in missing:                      # 404 from every mirror (again)
                if key not in recent:                # skipped ones were not re-asked
                    record[key[len(prefix):]] = now
            _write_missing(slug, record)
            return leftover

        covered, missing = [], []
        status, reason, error = "done", None, None
        try:
            items = [(slug, rel) for rel in base_rel_paths(slug, fetch=True)]
            self._process(state, items, covered, missing, pin, skip_missing=recent)
        except _Stop as stop:
            status, reason = stop.status, stop.reason
        except Exception as exc:
            traceback.print_exc()
            status, reason, error = "failed", "error", str(exc)
        self._finish(state, status, reason, covered, missing,
                     lambda c, m, _st: pin(c, m, final=True), error)

    def _finish(self, state, status, reason, covered, missing, finalize, error=None):
        """Manifest first (atomic), then the final status. Never the other way round.

        Runs for every outcome, unexpected errors included: the manifest gets
        the final status, the job's claims are handed over, the totals are
        re-derived when needed and the cancel marker is removed. A save deleted
        while the job ran leaves no job file behind.
        """
        recount = False
        try:
            recount = finalize(covered, missing, status)
        except _Stop as stop:  # the save was deleted while we ran
            status, reason = stop.status, stop.reason
        except Exception as exc:
            traceback.print_exc()
            status, reason, error = "failed", "error", error or str(exc)
            try:
                recount = finalize(covered, missing, status)
            except _Stop as stop:
                reason = stop.reason if stop.reason == REASON_DELETED else reason
            except Exception:
                traceback.print_exc()
        over_cap = (store.cap_bytes() > 0
                    and store.total_bytes() + store.pinned_bytes() > store.cap_bytes())
        if recount or over_cap or reason == REASON_DELETED:
            # Eviction also re-derives .total/.pinned from disk: needed after a
            # delete raced the job, or when a claimed tile ended up on disk
            # without being handed to the manifest (it is browsed now).
            try:
                store.evict_browsed()
            except Exception:
                traceback.print_exc()
        state.update(status=status, reason=reason, missing=len(set(missing)))
        if error:
            state["error"] = error
        if reason == REASON_DELETED:
            delete_job_file(state["id"])  # the save is gone: no state, no marker
            return
        _write_job(state)
        try:
            os.remove(_cancel_path(state["id"]))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Process-wide runner
# ---------------------------------------------------------------------------

_RUNNER = JobRunner()
_kick_lock = threading.Lock()


def kick() -> bool:
    """Make sure *some* process runs the queue. True if this process is the runner.

    Called by the API routes (never at import, never while the save flag is
    off). A process that cannot take the lock does nothing: the holder runs the
    job, and status is read from disk.
    """
    with _kick_lock:
        if _RUNNER.running:
            return True
        if not _RUNNER.acquire():
            return False
        _RUNNER.start()
        return True


# ---------------------------------------------------------------------------
# Base layer API helpers
# ---------------------------------------------------------------------------

def base_job_id(slug: str) -> str:
    return f"base-{slug}"  # no ':' -- job ids are file names (Windows)


def ensure_base_survey(slug: str) -> dict:
    """Queue a base job for ``slug`` unless complete or already queued/running."""
    if slug not in SURVEY_BY_SLUG:
        return {"slug": slug, "status": "unknown", "complete": False}
    complete = base_complete(slug)
    # Complete (missing tiles included) and no missing tile due for its daily
    # retry: nothing to do -- opening the modal again never re-asks upstream.
    if complete and not missing_due(slug):
        return {"slug": slug, "status": "done", "complete": True}
    job_id = base_job_id(slug)
    state = read_job(job_id)
    if state and state.get("status") in ACTIVE_STATUSES:
        return {"slug": slug, "status": state["status"], "complete": complete}
    enqueue(job_id, "base", {"slug": slug}, [slug])
    return {"slug": slug, "status": "queued", "complete": complete}


def base_job_progress(slug: str) -> dict:
    state = read_job(base_job_id(slug))
    if not state:
        return {"slug": slug, "status": "idle", "complete": base_complete(slug)}
    return {**state, "slug": slug, "complete": base_complete(slug)}


# ---------------------------------------------------------------------------
# Status (read-only)
# ---------------------------------------------------------------------------

def cone_max_cached_order(slug: str, ra: float, dec: float, radius_deg: float) -> int:
    """Highest order whose covered cone tiles are all on disk; -1 without a base.

    The base (orders 0..N) is whole-sky, so a complete base counts as order N
    for any cone; higher orders are checked against the covered cone tiles, up
    to the survey's effective max order. Never contacts upstream.
    """
    if slug not in SURVEY_BY_SLUG or not base_complete(slug):
        return -1
    facts = survey_facts(slug, fetch=False)
    best = min(OFFLINE_SKY_BASE_ORDER, facts.effective_max_order)
    for order in range(OFFLINE_SKY_BASE_ORDER + 1, facts.effective_max_order + 1):
        plan = plan_survey(slug, ra, dec, radius_deg, order, fetch=False)
        rels = [_tile_rel(order, n, plan.ext) for n in plan.tiles.get(order, [])]
        if not rels or not all(store.tile_exists(slug, rel) for rel in rels):
            break
        best = order
    return best
