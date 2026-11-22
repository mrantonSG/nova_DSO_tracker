"""Offline-sky jobs: the low-res base layer and the one job runner.

Base layer
----------
The base is the whole-sky low-res layer: the Allsky preview plus every covered
tile of orders 0..min(OFFLINE_SKY_BASE_ORDER, the survey's own hips_order). A
base job pins what it fetches in the instance-level ``.base-pins.json``, so the
base is never evicted; a tile every mirror answered 404 for is recorded in
``<slug>/.missing.json`` and retried at most once a day.

Downloads
---------
A ``download`` job fetches a survey's planned tiles for the ticked locations'
declination band. Its claims file (``jobs/<id>.pins``) is its *ownership*
record: it is deliberately **not** handed to the base pins on finish, because a
Remove has to delete exactly what this download owns. A removal skips any tile
another claim or a base pin still holds. The same ``.missing.json`` record is
written by a download, so a survey with genuine upstream holes can still reach
"ready" (the tile counts as "not available on the server").

One runner per instance
-----------------------
Under gunicorn there are several processes. Jobs live on disk as
``jobs/<id>.json`` (the queue); any process may enqueue a job or write a cancel
marker ``jobs/<id>.cancel``. Exactly one process -- whoever holds the
``jobs/.runner.lock`` file lock (nova/offline_sky_lock.py) -- runs jobs, one at a
time, oldest first, in a single thread. ``kick()`` tries to take the lock; a
process that cannot just returns, and status is read from disk. The OS drops the
lock when the holder dies, so the next process that takes it resumes every
queued/running/paused job (resume = re-enumerate and skip what is on disk).

Pace and honesty
----------------
All job upstream requests share one RateLimiter (OFFLINE_SKY_JOB_RATE_PER_SEC,
default 8/s, never below MIN_JOB_RATE_PER_SEC): since only one runner exists per
instance, that is the instance's job rate. It is separate from the browsing
proxy's semaphore. A tile is missing only when every upstream of the survey was
asked and answered 404. Anything else that brings no tile -- a network error,
5xx, 429, an open breaker, even mixed with a 404 from another mirror -- pauses
the job, which retries the same tile; it is never counted as done. Final
status: done (``missing`` = N), stopped, cancelled, failed. Any unexpected
error still goes through the finish path (accounting, cancel marker) and ends as
"failed".

Accounting
----------
Before a job fetches or adopts a tile it claims it in ``jobs/<id>.pins``
(store.claim_for_job), so eviction treats the tile as pinned from the moment it
lands on disk; the claims are handed to the base pins when the job ends. An
already-browsed tile that a job adopts moves from ``.total`` to ``.pinned``; a
tile already pinned (the base, or this job's own claims from before a crash) is
not.
"""

import json
import logging
import math
import os
import re
import threading
import time
import traceback

from nova.config import OFFLINE_SKY_BASE_ORDER, OFFLINE_SKY_JOB_RATE_PER_SEC
from nova.offline_sky_facts import allsky_rel, survey_coverage, survey_facts
from nova.offline_sky_lock import try_lock, unlock
from nova.offline_sky_sources import SURVEY_BY_SLUG
import nova.offline_sky_store as store
import nova.hips_healpix as hp

_log = logging.getLogger(__name__)

# Job files named with the old colon-style id "base:<slug>" (f586374b, before
# 7af5f065 renamed it to "base-<slug>"). They fail store.check_name, so only the
# startup migration in drop_legacy_jobs sees them. This exact form only.
_LEGACY_COLON_FILE_RE = re.compile(
    r"^base:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.(json|cancel|pins)$")

# Invalid job file names already warned about in this process: the runner polls
# every IDLE_POLL_SECONDS, and one warning per file is enough.
_warned_names = set()


def _warn_invalid_name(name: str) -> None:
    if name in _warned_names:
        return
    _warned_names.add(name)
    _log.warning("Offline sky: skipping job file with an invalid name: %r", name)

# --- Limits / cost ----------------------------------------------------------
# Keep at least this much free on the disk holding the store. One definition,
# in the store, because eviction and downloads must judge the disk the same way;
# this name stays as the alias the tests and job code already use.
DISK_HEADROOM_BYTES = store.DISK_HEADROOM_BYTES
FALLBACK_TILE_BYTES = 67 * 1024

# --- Runner -----------------------------------------------------------------
MIN_JOB_RATE_PER_SEC = 0.1       # a rate <= 0 (or tiny) never switches pacing off
PAUSE_SECONDS = 30.0             # wait before retrying while upstream is unavailable
MAX_PAUSE_SECONDS = 30 * 60      # give up ("failed") after this long paused on one tile
CHECKPOINT_EVERY = 64            # tiles between job-state checkpoints
IDLE_POLL_SECONDS = 2.0          # how often an idle runner looks for new jobs
RUNNER_LOCK_NAME = ".runner.lock"

# Base tiles that every mirror answered 404 for are recorded per survey in
# <slug>/.missing.json ({rel: unix time of the last 404}). They count towards
# "base complete" and are retried at most once per MISSING_RETRY_SECONDS. The
# record now lives in the store, so the planner can read it too; these names
# stay as the aliases the base code and tests already use.
MISSING_FILE = store.MISSING_FILENAME
MISSING_RETRY_SECONDS = store.MISSING_RETRY_SECONDS
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
# Tiles
# ---------------------------------------------------------------------------

def _tile_rel(order: int, npix: int, ext: str) -> str:
    return f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}.{ext}"


def download_job_id(slug: str) -> str:
    """The stable id of a survey's download job (one per survey)."""
    return f"download-{slug}"


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


def measured_avg_tile_bytes(slug: str):
    """(mean on-disk tile size, measured?) for a survey; fallback 67 KB.

    Used by a job to judge whether the next tile still fits under the store's
    budget before it spends an upstream request on it.
    """
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


def _missing_path(slug: str) -> str:
    return store._missing_path(slug)


def read_missing(slug: str) -> dict:
    """{rel: last 404 time} of tiles recorded as missing on every mirror.

    Thin alias for ``store.read_missing``: the record lives in the store so the
    planner can read it without importing this module.
    """
    return store.read_missing(slug)


def _write_missing(slug: str, record: dict) -> None:
    store.write_missing(slug, record)


def base_complete(slug: str) -> bool:
    """Every covered base tile (and the Allsky) is on disk or recorded as missing
    (404 from every mirror). Never contacts upstream."""
    if slug not in SURVEY_BY_SLUG:
        return False
    missing = read_missing(slug)
    return all(store.tile_exists(slug, rel) or rel in missing
               for rel in base_rel_paths(slug, fetch=False))


# ---------------------------------------------------------------------------
# Job files (the queue), cancel markers
# ---------------------------------------------------------------------------

def _job_path(job_id: str) -> str:
    store.check_name(job_id)
    return store._within_store(os.path.join(store.jobs_dir(), f"{job_id}.json"))


def _cancel_path(job_id: str) -> str:
    store.check_name(job_id)
    return store._within_store(os.path.join(store.jobs_dir(), f"{job_id}.cancel"))


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
            try:
                job_id = store.check_name(name[:-5])
            except ValueError:
                _warn_invalid_name(name)
                continue
            state = read_job(job_id)
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
    job's end) and the tile claims of a job with no state at all (a crash between
    the claim and its state file, or a state deleted by hand). Left behind, those
    claims pin tiles nothing covers: nothing would ever release them. Runner only.

    Write-ahead is safe here: ``claim_for_job`` creates the claims file and the
    job state under the same pin lock, so a state file is never momentarily
    absent for a job that is being created.
    """
    try:
        names = os.listdir(store.jobs_dir())
    except FileNotFoundError:
        return
    for name in names:
        try:
            if name.endswith(".pins"):
                stale = not os.path.exists(_job_path(name[:-len(".pins")]))
            elif name.endswith(".cancel"):
                state = read_job(name[:-len(".cancel")])
                stale = not state or state.get("status") not in ACTIVE_STATUSES
            else:
                continue
        except ValueError:
            _warn_invalid_name(name)
            continue
        if stale:
            try:
                os.remove(os.path.join(store.jobs_dir(), name))
            except OSError:
                pass


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
        """Once per runner process: reconcile the pins with the disk and rewrite
        the pin totals. Reads nothing from upstream: a survey's metadata is
        fetched when a plan first needs it."""
        if self._reconciled:
            return
        self._reconciled = True
        # Pins must agree with the disk before any status is trusted: a base
        # pin without its file is dropped, a claim's size is re-read (0 = still
        # to fetch). Once per runner process, i.e. at startup.
        try:
            counts = store.reconcile_pins_with_disk()
            print(f"[STARTUP] Offline sky: pins reconciled with disk: "
                  f"{counts['base_dropped']} base pin(s) without a file dropped, "
                  f"{counts['claims_absent']} claimed tile(s) not on disk.")
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
            try:
                job_id = store.check_name(state.get("id"))
            except ValueError:
                # The id inside the job file is not a valid job id.
                _log.warning("Offline sky: skipping job with an invalid id: %r",
                             state.get("id"))
                continue
            self.run_job(job_id)
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
            elif state.get("kind") == "download":
                self._run_download(state)
        except Exception as exc:
            # _run_base already routes errors through _finish; this is the last
            # resort when _finish itself fails. The job's claims file stays, so
            # its tiles stay pinned until the base job runs again.
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
                # Re-read the limit per tile (the store memoises it on the
                # settings file's mtime), so a limit lowered while a job runs is
                # honoured instead of the one captured at start. Under "No limit"
                # this budget already folds in the disk headroom, which is why the
                # disk is not checked again here.
                if pinned_now + added + avg[slug] > store.budget_bytes():
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

    # --- download ---
    def _run_download(self, state: dict) -> None:
        """Fetch a download's planned tiles; the claims stay as its ownership.

        A download differs from the base in three ways: its plan is the band
        intersection (not the whole sky), its claims file is its ownership
        record and is *not* handed to the base pins on finish, and its Allsky
        tile is owned by the download even though it is never counted as a
        grid tile. A finished download leaves ``jobs/<id>.pins`` in place; a
        Remove deletes the owned tiles and the claims file.
        """
        from nova import offline_sky_plan as plan_mod

        slug = state["params"]["survey"]
        job_id = state["id"]
        plan = plan_mod.read_plan(job_id)
        if not plan:
            # No plan means the selection changed or the plan was lost; the
            # route re-plans before enqueueing, so this is a defensive failure.
            self._finish(state, "failed", "no_plan", [], [], lambda *_: False)
            return
        tiles = plan.get("tiles") or []
        items = [(slug, rel) for rel in tiles]
        prefix = f"{slug}/"
        # A tile the upstream does not have is recorded in <slug>/.missing.json
        # and retried at most once a day, exactly as the base layer does. Until
        # its retry is due the job counts it as missing without asking upstream,
        # so a re-run of a finished download does not re-ask for known 404s.
        now = _now()
        recent = {prefix + rel for rel, ts in store.read_missing(slug).items()
                  if now - ts < MISSING_RETRY_SECONDS}

        flushed = [0]

        def record_sizes(covered):
            # A claim line is written before its fetch (size 0 when absent);
            # record the real sizes of the tiles covered since the last flush,
            # so the plan's "on disk" set (claim size > 0) sees them.
            new = covered[flushed[0]:]
            flushed[0] = len(covered)
            store.set_claim_sizes(job_id, {key: store.pinned_key_size(key) or 0
                                           for key in new})

        def checkpoint(covered, missing):
            # The claims file is the ownership record; refresh the cached totals
            # so status() sees the download's bytes without walking the pins.
            record_sizes(covered)
            store.refresh_pinned_bytes()

        covered, missing = [], []
        status, reason, error = "done", None, None
        try:
            self._process(state, items, covered, missing, checkpoint,
                          skip_missing=recent)
        except _Stop as stop:
            status, reason = stop.status, stop.reason
        except Exception as exc:
            traceback.print_exc()
            status, reason, error = "failed", "error", str(exc)

        def finalize(_covered, _missing, _status):
            # Record this run's 404s so the next plan counts them as "not
            # available on the server" and a finished download can reach
            # "ready". A tile found on a later run is forgotten again.
            if _covered or _missing:
                record = store.read_missing(slug)
                for key in _covered:
                    record.pop(key[len(prefix):], None)
                for key in _missing:
                    if key not in recent:      # skipped ones were not re-asked
                        record[key[len(prefix):]] = now
                store.write_missing(slug, record)
            record_sizes(_covered)
            # The job is done with the pins: make every record agree with disk.
            store.reconcile_pins_with_disk()
            store.refresh_pinned_bytes()
            # New tiles on disk: the per-order size figures are re-measured.
            store.invalidate_size_cache(slug)
            return False

        self._finish(state, status, reason, covered, missing, finalize, error)

    def _finish(self, state, status, reason, covered, missing, finalize, error=None):
        """Pins first (atomic), then the final status. Never the other way round.

        Runs for every outcome, unexpected errors included: the job's claims are
        handed over, the totals are re-derived when needed and the cancel marker
        is removed.
        """
        recount = False
        try:
            recount = finalize(covered, missing, status)
        except _Stop as stop:  # cancelled while we ran
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
        over_budget = store.total_bytes() + store.pinned_bytes() > store.budget_bytes()
        if recount or over_budget:
            # Eviction also re-derives .total/.pinned from disk: needed when a
            # claimed tile ended up on disk without being handed to the base
            # pins (it is browsed now).
            try:
                store.evict_browsed()
            except Exception:
                traceback.print_exc()
        state.update(status=status, reason=reason, missing=len(set(missing)))
        if error:
            state["error"] = error
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

    Called when a job is queued and at startup. A process that cannot take the
    lock does nothing: the holder runs the job, and status is read from disk.
    The first time this process becomes the runner, it drops any legacy job
    that is not a download before starting, so nothing from an older version
    fetches on its own.
    """
    with _kick_lock:
        if _RUNNER.running:
            return True
        if not _RUNNER.acquire():
            return False
        drop_legacy_jobs()
        _RUNNER.start()
        return True


def _drop_colon_job_files() -> int:
    """Remove "base:<slug>" job files (.json/.cancel/.pins). Returns how many jobs.

    Base pins are untouched; tiles held only by such a claims file become
    browsed, as drop_legacy_jobs always did for a dropped job's claims.
    """
    try:
        names = os.listdir(store.jobs_dir())
    except OSError:
        return 0
    found = set()
    for name in names:
        match = _LEGACY_COLON_FILE_RE.match(name)
        if not match:
            continue
        try:
            os.remove(os.path.join(store.jobs_dir(), name))
        except OSError:
            continue
        found.add(name[:-(len(match.group(1)) + 1)])
    return len(found)


def drop_legacy_jobs() -> int:
    """Drop queued/running/paused jobs that are not downloads. Returns how many.

    Old base jobs from earlier versions must not fetch on their own: only a
    user's click starts a download. The base's tiles stay pinned in
    ``.base-pins.json`` and are never touched -- the base is not revived. The
    dropped jobs' claims files are removed too, so their tiles stop being
    pinned and become ordinary browsed tiles. One ``[STARTUP]`` line.

    Job files with the old colon-style id ("base:<slug>") fail check_name, so
    list_jobs cannot see them; they are removed first, whatever their status.
    """
    dropped = _drop_colon_job_files()
    for state in list_jobs():
        if state.get("status") not in ACTIVE_STATUSES:
            continue
        if state.get("kind") == "download":
            continue
        job_id = state.get("id")
        if not job_id:
            continue
        try:
            paths = (_job_path(job_id), _cancel_path(job_id),
                     store._job_pins_path(job_id))
        except ValueError:
            _log.warning("Offline sky: skipping job with an invalid id: %r", job_id)
            continue
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass
        dropped += 1
    if dropped:
        print(f"[STARTUP] Offline sky: dropped {dropped} legacy job(s) "
              f"(not downloads); base pins untouched.")
    return dropped


# ---------------------------------------------------------------------------
# Base layer API helpers
# ---------------------------------------------------------------------------

def base_job_id(slug: str) -> str:
    return f"base-{slug}"  # no ':' -- job ids are file names (Windows)


# ---------------------------------------------------------------------------
# Download API helpers
# ---------------------------------------------------------------------------

def enqueue_download(slug: str, order: int, band, locations) -> dict:
    """Plan, store the plan and queue a download. Returns the job state.

    The id is stable per survey, so a second click on the same survey replaces
    the previous job rather than starting a second one. The plan is written
    before the job so the runner never sees a download without one.
    """
    from nova import offline_sky_plan as plan_mod

    summary = plan_mod.plan_summary(slug, order, band, locations, include_tiles=True)
    job_id = download_job_id(slug)
    plan_mod.write_plan(job_id, summary)
    params = {"survey": slug, "order": summary["order"],
              "band": summary["band"], "locations": locations}
    state = enqueue(job_id, "download", params, [slug])
    kick()
    return state


def _plan_without_tiles(plan):
    """The plan summary without its ``tiles`` list.

    The plan file carries every planned rel path (15k+ for a Basic download).
    The poll response must not: it is re-sent on every refresh, and 436 KB per
    poll is not a status payload. The tiles stay in the file, where the job
    runner and the trim read them.
    """
    if not plan:
        return plan
    return {k: v for k, v in plan.items() if k != "tiles"}


def download_progress(slug: str) -> dict:
    """The job state plus its plan summary, or an idle stub.

    ``could_free`` is the bytes the download owns that fall outside its current
    plan -- the "could free X" figure the UI shows when a location is unticked.
    Nothing is deleted until the user asks for a trim. The plan's ``tiles`` list
    is dropped from the response; see ``_plan_without_tiles``.
    """
    from nova import offline_sky_plan as plan_mod

    job_id = download_job_id(slug)
    state = read_job(job_id)
    full = plan_mod.read_plan(job_id)          # needs "tiles" for _could_free
    plan = _plan_without_tiles(full)           # the response drops them
    could_free = _could_free(job_id, full)
    if not state:
        return {"id": job_id, "status": "idle", "plan": plan,
                "could_free": could_free}
    return {**state, "plan": plan, "could_free": could_free}


def plan_owned_keys(slug: str, plan) -> set:
    """The plan's tiles as claim keys (``<slug>/<rel>``).

    The plan stores bare HiPS rel paths; a claim key is prefixed with the
    survey slug. Comparing the two without prefixing would make every owned
    tile look "outside the plan" and a trim would delete the whole download.
    """
    prefix = f"{slug}/"
    return {prefix + rel for rel in (plan.get("tiles") or [])}


def _could_free(job_id: str, plan) -> int:
    """Bytes owned by the download but outside its current plan."""
    if not plan:
        return 0
    owned = store.job_pins(job_id)
    keep = plan_owned_keys(plan.get("survey") or "", plan)
    extra = owned - keep
    if not extra:
        return 0
    sizes = store.job_pin_sizes(job_id)
    total = 0
    for key in extra:
        # A claim line is written before the fetch with size 0; once the tile
        # is on disk its real size is authoritative. Prefer the stat when the
        # recorded size is missing or zero.
        size = sizes.get(key)
        if not size:
            slug, _, rel = key.partition("/")
            size = store.file_size(slug, rel) or 0
        total += size
    return total


def _wait_for_job_to_stop(job_id: str, timeout: float = 10.0) -> bool:
    """Wait until ``job_id`` leaves the active states. True when it stopped.

    ``cancel_job`` only writes a marker; the runner sees it at its next tile.
    A Remove that deletes immediately races that thread and the job resurrects
    a few tiles. This waits for the job's own state to leave the active set,
    so the delete happens after the thread has stopped. A timeout means the
    runner did not stop in time; the caller must not delete then, because a
    live thread may still be writing.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_job(job_id)
        if not state or state.get("status") not in ACTIVE_STATUSES:
            return True
        time.sleep(0.05)
    return False


class RemoveRefused(RuntimeError):
    """Remove could not stop the job in time; nothing was deleted."""

    def __init__(self, job_id: str):
        super().__init__(f"job {job_id} did not stop in time")
        self.job_id = job_id


def remove_download(slug: str) -> dict:
    """Delete a download's owned tiles and its job files. Returns the summary.

    The tiles are deleted unless another download or a base pin still claims
    them (``store.delete_tiles`` skips those). The plan file goes too.

    Raises ``RemoveRefused`` when the job does not stop within the timeout:
    deleting while a live thread may still write would resurrect the download,
    so the caller refuses instead (the route answers 409).
    """
    from nova import offline_sky_plan as plan_mod

    job_id = download_job_id(slug)
    state = read_job(job_id)
    # Cancel first and wait for the job to actually stop. The cancel marker is
    # only seen at the next tile, so deleting while the thread still runs lets
    # it re-create the claims file and fetch a few more tiles after the delete.
    cancel_job(job_id)
    if not _wait_for_job_to_stop(job_id):
        raise RemoveRefused(job_id)
    # Read the owned tiles, then remove *this* download's claims file before
    # deleting. Otherwise delete_tiles sees the job's own claims and keeps
    # every tile as "still claimed by another download".
    owned = store.job_pins(job_id)
    try:
        os.remove(store._job_pins_path(job_id))
    except OSError:
        pass
    summary = store.delete_tiles(owned)
    for path in (_job_path(job_id), _cancel_path(job_id)):
        try:
            os.remove(path)
        except OSError:
            pass
    plan_mod.delete_plan(job_id)
    store.refresh_pinned_bytes()
    store.invalidate_size_cache(slug)
    return {"id": job_id, "status": "removed", "owned": len(owned),
            "deleted": summary["deleted"], "kept": summary["kept"],
            "bytes": summary["bytes"], "had_job": bool(state)}
