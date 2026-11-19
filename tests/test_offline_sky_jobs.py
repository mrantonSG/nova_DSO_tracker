"""Offline-sky jobs: the base layer, the one job runner, pacing and accounting.

The per-object saves this file used to cover are gone (the Configuration ->
General download page replaces them); what stays is the machinery the downloads
will build on: the runner (lock, resume, cancel, rate limiter), the tile claims,
and the base-layer job.

No network. Upstream is ``nova.blueprints.offline_sky._upstream_get``, replaced
by a fake; the breaker is forced closed; the disk is reported as roomy unless a
test says otherwise. Jobs run through a test-owned ``JobRunner``
(``run_pending``), never the process-wide background thread (``jobs.kick`` is
stubbed).

Survey facts for NSNS [OIII] are the real ``properties`` and ``Moc.fits`` files
(tests/fixtures/offline_sky/), so the base-layer numbers below are what a real
base job would cost.
"""

import os
import subprocess
import sys
import textwrap

import pytest

import nova.blueprints.offline_sky as bp
import nova.offline_sky_facts as facts
import nova.offline_sky_jobs as jobs
import nova.offline_sky_store as store

DSS = "dss2-color"
OIII = "nsns-oiii8"
OIII_ID = "https://www.simg.de/nebulae3/dr0_2/oiii8"
SURVEY_UPSTREAM_OIII = "https://www.simg.de/nebulae3/dr0_2/oiii8"
GIB = 1024 ** 3
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "offline_sky")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    facts.clear_memo()
    kicks = []
    monkeypatch.setattr(jobs, "kick", lambda: kicks.append(1) or False)
    monkeypatch.setattr(store, "disk_free_bytes", lambda: 500 * GIB)
    monkeypatch.setattr(bp, "_breaker_open", lambda base: False)
    _install_real_facts(DSS)   # so job planning never asks upstream for metadata
    yield kicks
    facts.clear_memo()


@pytest.fixture
def runners():
    made = []

    def make(**kw):
        if "rate" not in kw:
            kw.setdefault("limiter", _NoLimit())   # pacing has its own tests
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("pause_seconds", 0.0)
        r = jobs.JobRunner(**kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.release()


class _NoLimit:
    def wait(self):
        pass


class FakeUpstream:
    """Stands in for _upstream_get. ``script(url)`` may return an outcome tuple
    to override the default 200 with ``size`` bytes."""

    def __init__(self, size=1000, script=None):
        self.size = size
        self.script = script
        self.calls = []

    def __call__(self, base, url):
        self.calls.append(url)
        if self.script is not None:
            out = self.script(url)
            if out is not None:
                return out
        return bp.UPSTREAM_OK, b"x" * self.size


@pytest.fixture
def upstream(monkeypatch):
    fake = FakeUpstream()
    monkeypatch.setattr(bp, "_upstream_get", fake)
    return fake


def _install_real_facts(*slugs):
    for slug in slugs:
        for name in ("properties", "Moc.fits"):
            with open(os.path.join(FIXTURES, slug, name), "rb") as fh:
                store.store_file(slug, name, fh.read(), count=False)
    facts.clear_memo()


def _props(order, fmt="jpeg"):
    return (f"hips_order = {order}\nhips_tile_format = {fmt}\n"
            "hips_frame = equatorial\n").encode()


def _moc_bytes(cells):
    import io
    import numpy as np
    from astropy.io import fits
    col = fits.Column(name="UNIQ", format="1K",
                      array=np.array([4 * 4 ** o + n for o, n in cells], dtype=np.int64))
    hdu = fits.BinTableHDU.from_columns([col])
    hdu.header["PIXTYPE"] = "HEALPIX"
    hdu.header["ORDERING"] = "NUNIQ"
    hdu.header["COORDSYS"] = "C"
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(buf)
    return buf.getvalue()


def _run_base_job(runners, slug):
    r = runners()
    r.acquire()
    r.run_pending()
    r.release()
    return jobs.read_job(jobs.base_job_id(slug))


# ---------------------------------------------------------------------------
# 1. Base layer: coverage, effective max order, completeness
# ---------------------------------------------------------------------------

def test_base_job_coverage_aware_with_allsky(upstream, runners):
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(1, 5)]), count=False)   # 1/48 of the sky
    facts.clear_memo()
    rels = jobs.base_rel_paths(OIII)
    assert rels[0] == "Norder3/Allsky.png"
    # order 0: the parent of (1,5); order 1: (1,5); order 2: 4 children; order 3: 16
    assert len(rels) == 1 + 1 + 1 + 4 + 16
    st = jobs.ensure_base_survey(OIII)
    assert st["status"] == "queued"
    assert jobs.ensure_base_survey(OIII)["status"] == "queued"          # not queued twice
    r = runners()
    r.acquire()
    r.run_pending()
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "done"
    assert jobs.base_complete(OIII)
    pins = store.base_pinned_rel_paths()
    assert f"{OIII}/Norder3/Allsky.png" in pins and len(pins) == len(rels)
    assert len(upstream.calls) == len(rels)
    assert jobs.ensure_base_survey(OIII) == {"slug": OIII, "status": "done", "complete": True}


def test_base_respects_effective_max_order():
    store.store_file(OIII, "properties", _props(2, "png"), count=False)
    facts.clear_memo()
    rels = jobs.base_rel_paths(OIII)
    assert not any(r.startswith("Norder3/Dir") for r in rels)       # orders 0..2 only
    assert len(rels) == 1 + 12 + 48 + 192


def test_base_not_complete_while_a_covered_tile_is_absent():
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(1, 5)]), count=False)
    facts.clear_memo()
    rels = jobs.base_rel_paths(OIII)
    for rel in rels[:-1]:
        store.store_file(OIII, rel, b"t", count=False)
    assert not jobs.base_complete(OIII)
    store.store_file(OIII, rels[-1], b"t", count=False)
    assert jobs.base_complete(OIII)


def test_base_complete_counts_missing_and_retries_once_a_day(upstream, runners, monkeypatch):
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(1, 5)]), count=False)
    facts.clear_memo()
    gone = jobs.base_rel_paths(OIII)[5]
    clock = [1_000_000.0]
    monkeypatch.setattr(jobs, "_now", lambda: clock[0])
    upstream.script = lambda url: (bp.UPSTREAM_MISSING, None) if url.endswith(gone) else None

    # First pass: every mirror says 404 -> recorded, and the base is complete.
    assert jobs.ensure_base_survey(OIII)["status"] == "queued"
    state = _run_base_job(runners, OIII)
    assert state["status"] == "done" and state["missing"] == 1
    assert jobs.read_missing(OIII) == {gone: clock[0]}
    assert not store.tile_exists(OIII, gone)
    assert jobs.base_complete(OIII)

    # Modal opened again within the day: nothing queued, upstream not asked.
    upstream.calls.clear()
    clock[0] += 23 * 3600
    assert jobs.ensure_base_survey(OIII) == {"slug": OIII, "status": "done", "complete": True}
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "done"
    # Even a base job run inside the day skips the recorded tile.
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    assert _run_base_job(runners, OIII)["missing"] == 1
    assert upstream.calls == []

    # A day after the 404: one retry, still 404 -> timestamp refreshed.
    clock[0] = 1_000_000.0 + 24 * 3600
    st = jobs.ensure_base_survey(OIII)
    assert st == {"slug": OIII, "status": "queued", "complete": True}
    _run_base_job(runners, OIII)
    assert [u for u in upstream.calls] == [f"{SURVEY_UPSTREAM_OIII}/{gone}"]
    assert jobs.read_missing(OIII) == {gone: clock[0]}
    assert jobs.ensure_base_survey(OIII)["status"] == "done"     # next retry tomorrow

    # Next day the tile exists: fetched, pinned, record cleared.
    upstream.calls.clear()
    upstream.script = None
    clock[0] += 24 * 3600
    assert jobs.ensure_base_survey(OIII)["status"] == "queued"
    _run_base_job(runners, OIII)
    assert upstream.calls == [f"{SURVEY_UPSTREAM_OIII}/{gone}"]
    assert store.tile_exists(OIII, gone)
    assert f"{OIII}/{gone}" in store.base_pinned_rel_paths()
    assert jobs.read_missing(OIII) == {}
    assert jobs.base_complete(OIII)


def test_base_missing_needs_404_from_every_mirror(upstream, runners):
    """DSS has 4 upstreams: a 404 from one while the others are unavailable is
    not missing -- the job pauses and retries the tile. Only once every mirror
    answers 404 is it recorded as missing (and the base complete)."""
    os.remove(store.tile_path(DSS, "Moc.fits"))
    store.store_file(DSS, "Moc.fits", _moc_bytes([(1, 5)]), count=False)
    facts.clear_memo()
    gone = jobs.base_rel_paths(DSS)[5]
    primary = "https://alasky.cds.unistra.fr/DSS/DSSColor/"
    pauses = []

    def script(url):
        if url.endswith(gone):
            if url.startswith(primary) or pauses:          # after one pause: all 404
                return bp.UPSTREAM_MISSING, None
            return bp.UPSTREAM_UNAVAILABLE, None
        return None

    def sleep(_s):
        st = jobs.read_job(jobs.base_job_id(DSS))
        pauses.append((st["status"], st["missing"]))

    upstream.script = script
    jobs.ensure_base_survey(DSS)
    r = runners(sleep=sleep, pause_seconds=1.0)
    r.acquire()
    r.run_pending()
    # One 404 + three unavailable: paused, the tile not counted as missing.
    assert pauses and pauses[0] == ("paused", 0)
    asked = [u for u in upstream.calls if u.endswith(gone)]
    assert len(asked) == 8                                  # 4 mirrors, twice
    state = jobs.read_job(jobs.base_job_id(DSS))
    assert state["status"] == "done" and state["missing"] == 1
    assert list(jobs.read_missing(DSS)) == [gone]
    assert jobs.base_complete(DSS)


def test_missing_record_is_never_evicted(monkeypatch):
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    jobs._write_missing(OIII, {"Norder3/Dir0/Npix1.png": 1.0})
    for i in range(4):
        store.store_file(OIII, f"Norder5/Dir0/Npix{i}.png", b"t" * 400 * 1024)
    store.evict_browsed()
    assert jobs.read_missing(OIII) == {"Norder3/Dir0/Npix1.png": 1.0}


# ---------------------------------------------------------------------------
# 2. One owner per job: lock, cancel from elsewhere, resume after a crash
# ---------------------------------------------------------------------------

_LOCK_CHILD = textwrap.dedent("""
    import importlib.util, sys, time
    spec = importlib.util.spec_from_file_location("lk", sys.argv[1])
    lk = importlib.util.module_from_spec(spec); spec.loader.exec_module(lk)
    fh = lk.try_lock(sys.argv[2])
    print("GOT" if fh else "BUSY", flush=True)
    if fh and sys.argv[3] == "hold":
        sys.stdin.readline()          # hold until the parent says so
""")


def test_two_processes_only_one_takes_the_runner_lock(tmp_path):
    lock_mod = os.path.join(os.path.dirname(jobs.__file__), "offline_sky_lock.py")
    lock_file = str(tmp_path / "jobs" / ".runner.lock")
    script = tmp_path / "child.py"
    script.write_text(_LOCK_CHILD)
    holder = subprocess.Popen([sys.executable, "-I", str(script), lock_mod, lock_file, "hold"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "GOT"
        other = subprocess.run([sys.executable, "-I", str(script), lock_mod, lock_file, "try"],
                               capture_output=True, text=True, timeout=30)
        assert other.stdout.strip() == "BUSY"
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=30)
    # The holder exited: the lock is free again (what resume-after-crash relies on).
    again = subprocess.run([sys.executable, "-I", str(script), lock_mod, lock_file, "try"],
                           capture_output=True, text=True, timeout=30)
    assert again.stdout.strip() == "GOT"


def test_two_runners_one_owner_other_only_queues(upstream, runners):
    a = runners()
    b = runners()
    assert a.acquire()
    assert not b.acquire()                        # second "process" can't run jobs
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    assert b.run_pending() == 0
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "queued"
    assert a.run_pending() == 1
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "done"


def test_cancel_from_another_process(upstream, runners):
    """A cancel marker written by another worker stops the job at the next tile
    and leaves no marker behind."""
    calls = [0]

    def script(url):
        calls[0] += 1
        if calls[0] == 3:
            assert jobs.cancel_job(jobs.base_job_id(OIII))   # what a route would do
        return None

    upstream.script = script
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job(jobs.base_job_id(OIII))
    assert state["status"] == "cancelled"
    assert not os.path.exists(jobs._cancel_path(jobs.base_job_id(OIII)))
    assert not jobs.cancel_job(jobs.base_job_id(OIII))       # already final


class _Crash(BaseException):
    pass


def test_resume_after_crash(upstream, runners, monkeypatch):
    """The OS drops the lock when the holder dies; the next runner resumes the
    job and re-enumerates, skipping what is already on disk."""
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(1, 5)]), count=False)
    facts.clear_memo()
    rels = jobs.base_rel_paths(OIII)
    calls = [0]

    def script(url):
        calls[0] += 1
        if calls[0] == 10:
            raise _Crash()
        return None

    upstream.script = script
    jobs.ensure_base_survey(OIII)
    a = runners()
    a.acquire()
    with pytest.raises(_Crash):
        a.run_pending()
    a.release()                                 # the process died; the OS drops its lock
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "running"

    first_pass = set(upstream.calls[:9])
    upstream.script = None
    upstream.calls.clear()
    b = runners()
    assert b.acquire()
    assert b.run_pending() == 1
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "done"
    assert not (set(upstream.calls) & first_pass)          # nothing fetched twice
    assert len(store.base_pinned_rel_paths()) == len(rels)


# ---------------------------------------------------------------------------
# 3. Pause on breaker/busy; rate limit
# ---------------------------------------------------------------------------

def test_breaker_pause_does_not_mark_tile_done(upstream, runners):
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(1, 5)]), count=False)
    facts.clear_memo()
    rels = jobs.base_rel_paths(OIII)
    target = rels[2]
    state = {"fails": 2}
    seen_during_pause = []

    def script(url):
        if url.endswith(target) and state["fails"] > 0:
            state["fails"] -= 1
            return bp.UPSTREAM_UNAVAILABLE, None
        return None

    def sleep(_s):
        js = jobs.read_job(jobs.base_job_id(OIII))
        seen_during_pause.append((js["status"], js["done"]))

    upstream.script = script
    jobs.ensure_base_survey(OIII)
    r = runners(sleep=sleep, pause_seconds=1.0)
    r.acquire()
    r.run_pending()
    # NSNS has one upstream: one "unavailable" answer -> one pause. While paused
    # the tile is not counted, so done stays at the 2 tiles before it.
    assert seen_during_pause and all(x == ("paused", 2) for x in seen_during_pause)
    final = jobs.read_job(jobs.base_job_id(OIII))
    assert final["status"] == "done" and final["missing"] == 0
    assert final["done"] == len(rels)
    assert f"{OIII}/{target}" in store.base_pinned_rel_paths()


def test_long_outage_fails_without_counting_tiles(upstream, runners):
    upstream.script = lambda url: (bp.UPSTREAM_UNAVAILABLE, None)
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s

    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    r = runners(clock=clock, sleep=sleep, pause_seconds=60.0, max_pause_seconds=300.0)
    r.acquire()
    r.run_pending()
    state = jobs.read_job(jobs.base_job_id(OIII))
    assert state["status"] == "failed" and state["reason"] == jobs.REASON_UPSTREAM
    assert state["done"] == 0 and state["fetched"] == 0
    assert store.base_pinned_rel_paths() == set()


def test_rate_limiter_spacing():
    t = [0.0]
    slept = []

    def clock():
        return t[0]

    def sleep(s):
        slept.append(s)
        t[0] += s

    rl = jobs.RateLimiter(4.0, clock, sleep)
    for _ in range(9):
        rl.wait()
    assert t[0] == pytest.approx(2.0)                         # 8 gaps x 0.25 s


def test_runner_requests_paced_at_4_per_second(upstream, runners):
    _install_real_facts(OIII)               # no metadata fetch on the job path
    t = [0.0]
    stamps = []

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s

    def script(url):
        stamps.append(t[0])
        return None

    upstream.script = script
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    r = runners(rate=4.0, clock=clock, sleep=sleep)
    r.acquire()
    r.run_pending()
    assert len(stamps) > 8
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.25 - 1e-9
    assert (stamps[-1] - stamps[0]) == pytest.approx(0.25 * (len(stamps) - 1))


def test_jobs_do_not_use_the_proxy_semaphore(monkeypatch, runners):
    """The job path calls _upstream_get directly, never _fetch_upstream."""
    _install_real_facts(OIII)               # no metadata fetch on the job path
    monkeypatch.setattr(bp, "_fetch_upstream",
                        lambda *a: pytest.fail("job used the browsing proxy's semaphore"))
    monkeypatch.setattr(bp._session, "get", lambda url, **kw: type(
        "R", (), {"status_code": 200, "content": b"z" * 10})())
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    r = runners()
    r.acquire()
    r.run_pending()
    assert jobs.read_job(jobs.base_job_id(OIII))["status"] == "done"


def test_rate_limiter_never_disabled_by_zero_or_negative():
    for rate in (0, -3, None, "x", float("nan")):
        t = [0.0]

        def sleep(s):
            t[0] += s

        rl = jobs.RateLimiter(rate, lambda: t[0], sleep)
        assert rl.interval == pytest.approx(1.0 / jobs.MIN_JOB_RATE_PER_SEC)
        rl.wait()
        rl.wait()
        assert t[0] == pytest.approx(rl.interval)


def test_removed_job_keys_are_warned(monkeypatch, caplog):
    import logging
    import nova.config as cfg
    monkeypatch.setenv("OFFLINE_SKY_JOB_CONCURRENCY", "2")
    monkeypatch.delenv("OFFLINE_SKY_JOB_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("OFFLINE_SKY_SAVE_ENABLED", raising=False)
    with caplog.at_level(logging.WARNING, logger="nova.config"):
        found = cfg.warn_removed_offline_sky_keys()
    assert "OFFLINE_SKY_JOB_CONCURRENCY" in found
    assert "OFFLINE_SKY_JOB_CONCURRENCY" in caplog.text
    assert "OFFLINE_SKY_JOB_RATE_PER_SEC" in caplog.text
    monkeypatch.delenv("OFFLINE_SKY_JOB_CONCURRENCY")
    assert cfg.warn_removed_offline_sky_keys() == []


def test_the_removed_save_flag_is_warned(monkeypatch, caplog):
    """A leftover OFFLINE_SKY_SAVE_ENABLED in a .env warns instead of silently
    doing nothing."""
    import logging
    import nova.config as cfg
    monkeypatch.setenv("OFFLINE_SKY_SAVE_ENABLED", "true")
    with caplog.at_level(logging.WARNING, logger="nova.config"):
        found = cfg.warn_removed_offline_sky_keys()
    assert "OFFLINE_SKY_SAVE_ENABLED" in found
    assert "OFFLINE_SKY_SAVE_ENABLED" in caplog.text
    assert "Configuration" in caplog.text


# ---------------------------------------------------------------------------
# 4. Space running out mid-job
# ---------------------------------------------------------------------------

def test_cap_reached_mid_job_stops_with_partial_pins(upstream, runners, monkeypatch):
    """The cap is re-checked before every tile; when it is hit the job stops
    ("stopped", reason "no_space") and keeps the tiles it already pinned."""
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(1, 5)]), count=False)
    facts.clear_memo()
    upstream.size = 100 * 1024
    rels = jobs.base_rel_paths(OIII)
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)   # ~10 tiles
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job(jobs.base_job_id(OIII))
    pins = store.base_pinned_rel_paths()
    assert state["status"] == "stopped" and state["reason"] == jobs.REASON_NO_SPACE
    assert 0 < len(pins) < len(rels)
    assert store.pinned_bytes() == len(pins) * 100 * 1024 <= 1024 * 1024


def test_disk_runs_low_mid_job_stops(upstream, runners, monkeypatch):
    free = [jobs.DISK_HEADROOM_BYTES + 20 * 1024 * 1024]
    upstream.size = 4 * 1024 * 1024

    def disk_free():
        return free[0]

    real_store = store.store_file

    def store_and_shrink(slug, rel, data, count=True):
        wrote = real_store(slug, rel, data, count=count)
        free[0] -= len(data)
        return wrote

    monkeypatch.setattr(store, "disk_free_bytes", disk_free)
    monkeypatch.setattr(store, "store_file", store_and_shrink)
    jobs.enqueue(jobs.base_job_id(OIII), "base", {"slug": OIII}, [OIII])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job(jobs.base_job_id(OIII))
    assert state["status"] == "stopped" and state["reason"] == jobs.REASON_NO_SPACE
    assert free[0] >= jobs.DISK_HEADROOM_BYTES
    assert len(store.base_pinned_rel_paths()) == state["fetched"]


# ---------------------------------------------------------------------------
# 5. Eviction never deletes metadata; pinned tiles count against the cap
# ---------------------------------------------------------------------------

def test_eviction_keeps_properties_and_moc(monkeypatch):
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    store.store_file(DSS, "properties", b"p" * 600 * 1024, count=False)
    store.store_file(DSS, "Moc.fits", b"m" * 600 * 1024, count=False)
    for i in range(4):
        store.store_file(DSS, f"Norder5/Dir0/Npix{i}.jpg", b"t" * 400 * 1024)
    out = store.evict_browsed()
    assert out["removed"] >= 2
    assert store.read_file(DSS, "properties") is not None
    assert store.read_file(DSS, "Moc.fits") is not None
    assert os.path.exists(os.path.join(store.hips_root(), ".total"))


def test_eviction_counts_pinned_against_the_cap(monkeypatch):
    """One cap for the whole store: browsed tiles make room for pinned ones."""
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    rels = [f"Norder5/Dir0/Npix{i}.jpg" for i in range(4)]
    for rel in rels:
        store.store_file(DSS, rel, b"t" * 300 * 1024)
    store.add_base_pins([f"{DSS}/{rels[0]}", f"{DSS}/{rels[1]}"])   # 600 KiB pinned
    out = store.evict_browsed()
    assert out["pinned"] == 600 * 1024
    assert out["total"] + out["pinned"] <= 1024 * 1024
    assert store.read_file(DSS, rels[0]) and store.read_file(DSS, rels[1])


# ---------------------------------------------------------------------------
# 6. Effective max order in proxy validation and the JS map
# ---------------------------------------------------------------------------

def test_proxy_rejects_orders_above_effective_max(client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    facts.clear_memo()
    store.store_file(OIII, "Norder6/Dir0/Npix1.png", b"T")
    assert client.get(f"/hips/{OIII}/Norder6/Dir0/Npix1.png").status_code == 200
    monkeypatch.setattr(bp, "_fetch_upstream", lambda *a: pytest.fail("no upstream for N7"))
    assert client.get(f"/hips/{OIII}/Norder7/Dir0/Npix1.png").status_code == 404


def test_build_js_map_uses_effective_max_order():
    from nova.offline_sky_sources import build_js_map
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    facts.clear_memo()
    m = build_js_map()
    assert m[OIII_ID]["maxOrder"] == 6
    assert m["P/DSS2/color"]["maxOrder"] == 9    # no properties cached: whitelist


def test_removed_surveys_are_gone_from_the_whitelist():
    """DSS2 (blue) and 2MASS (H) were removed: every upstream they had 404s, and no
    registered mirror hosts them. A whitelist entry with no working upstream is a
    survey the proxy can only answer 503 for."""
    from nova.offline_sky_sources import SURVEY_SOURCES, SURVEY_BY_SLUG
    assert "P/DSS2/blue" not in SURVEY_SOURCES
    assert "P/2MASS/H" not in SURVEY_SOURCES
    assert "dss2-blue" not in SURVEY_BY_SLUG
    assert "2mass-h" not in SURVEY_BY_SLUG


def test_cds_stays_first_for_every_survey_it_hosts():
    """CDS is the preferred source: it leads each survey's upstream list, so a
    healthy CDS is used and a recovered one is picked up again."""
    from nova.offline_sky_sources import SURVEY_SOURCES
    for sid, src in SURVEY_SOURCES.items():
        if sid.startswith("https://"):        # NSNS is its own single host
            continue
        assert src.upstreams[0].startswith("https://alasky.cds.unistra.fr/"), \
            f"{sid} should try CDS first, got {src.upstreams[0]}"


def test_every_cds_survey_has_a_non_cds_fallback():
    """Each CDS-hosted survey needs at least one upstream outside CDS, so it still
    loads while CDS is down (the outage that made every survey but DSS2 fail)."""
    from nova.offline_sky_sources import SURVEY_SOURCES
    for sid, src in SURVEY_SOURCES.items():
        if sid.startswith("https://"):
            continue
        outside = [u for u in src.upstreams if ".cds.unistra.fr" not in u]
        assert outside, f"{sid} has no non-CDS upstream: {src.upstreams}"


def test_resolve_slug_accepts_ids_and_slugs():
    from nova.offline_sky_sources import resolve_slug, resolve_slugs
    assert resolve_slug("P/DSS2/color") == resolve_slug(DSS) == DSS
    assert resolve_slug("nope") is None and resolve_slug("") is None
    assert resolve_slugs(f"P/DSS2/color,{DSS},{OIII_ID},x") == [DSS, OIII]


# ---------------------------------------------------------------------------
# 7. Pin bookkeeping: what the runner sweeps and reconciles
# ---------------------------------------------------------------------------

def test_a_pins_file_with_no_job_state_is_swept(upstream, runners):
    """A crash between the claim and its state file leaves a claims file that
    pins tiles nothing covers and nothing would ever release."""
    os.makedirs(store.jobs_dir(), exist_ok=True)
    orphan = store._job_pins_path("ghost")
    with open(orphan, "w", encoding="utf-8") as fh:
        fh.write("dss2-color/Norder3/Dir0/Npix1.jpg\n")
    assert store.all_job_pins()                        # it counts while it exists
    r = runners()
    r.acquire()
    r.run_pending()
    assert not os.path.exists(orphan)
    assert store.all_job_pins() == set()


def test_the_orphan_sweep_keeps_a_jobs_claims_while_its_state_exists(upstream, runners):
    """The sweep only drops claims whose job state is gone entirely; a job that
    still has state keeps its claims (write-ahead: the state exists first)."""
    os.makedirs(store.jobs_dir(), exist_ok=True)
    store.write_json_atomic(jobs._job_path("live"),
                            {"id": "live", "kind": "base", "status": "done", "params": {"slug": OIII},
                             "surveys": [OIII], "total": 0, "done": 0, "bytes": 0})
    key = "dss2-color/Norder3/Dir0/Npix1.jpg"
    store.claim_for_job("live", key)
    r = runners()
    r.acquire()
    r.run_pending()
    assert os.path.exists(jobs._job_path("live"))
    assert key in store.all_job_pins()


def test_a_stale_cancel_marker_is_swept(upstream, runners):
    """A cancel that raced a job's end leaves a marker for a job that is not
    active: the sweep drops it."""
    os.makedirs(store.jobs_dir(), exist_ok=True)
    store.write_json_atomic(jobs._job_path("done1"),
                            {"id": "done1", "kind": "base", "status": "done",
                             "params": {"slug": OIII}, "surveys": [OIII],
                             "total": 0, "done": 0, "bytes": 0})
    marker = jobs._cancel_path("done1")
    with open(marker, "w", encoding="ascii") as fh:
        fh.write("1")
    r = runners()
    r.acquire()
    r.run_pending()
    assert not os.path.exists(marker)


def test_the_runner_reconciles_pins_once_at_start(upstream, runners, monkeypatch):
    """Totals on disk are re-derived from the pins at the runner's first pass."""
    calls = []
    real = store.refresh_pinned_bytes
    monkeypatch.setattr(store, "refresh_pinned_bytes",
                        lambda: (calls.append(1), real())[1])
    r = runners()
    r.acquire()
    r.run_pending()
    first = len(calls)
    assert first >= 1
    r.run_pending()                                    # a second pass does not repeat it
    assert len(calls) == first
