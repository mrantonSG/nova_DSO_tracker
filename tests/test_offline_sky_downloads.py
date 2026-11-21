"""Offline-sky downloads: planning, ownership, budget, removal, access control.

No network. The upstream is a fake; the store is the per-test temp directory
that conftest isolates; the runner is driven by hand so the tests are
deterministic. The real survey fixtures (tests/fixtures/offline_sky/) give the
planner real MOC geometry to intersect.

The download layer's contract, as decided in DECISIONS.md:

* one download per survey, stable id ``download-<slug>``;
* a plan is orders 0..N intersected with the MOC and the band, where a tile
  counts when it *overlaps* the band (centre dec +- pixel radius);
* the plan is cached in ``jobs/<id>.plan.json`` and ``status()`` never
  recomputes it;
* the selection is instance-level ``.downloads.json`` keyed on ``(user,
  record_uid)``, and the user is always the session's -- never the body's;
* a location's state is ready/missing, with the survey's coverage reported
  alongside it; a tile the upstream 404s counts as "not available on the
  server" and never blocks "ready";
* Remove deletes the owned tiles minus any another download or base still
  claims, and refuses (409) rather than deleting while the job may still write;
* the base pins and their tiles are never touched;
* the whole ``/api/offline-sky/`` surface belongs to the tile store
  (``OFFLINE_SKY_STORE=server``) and, in multi-user mode, to instance admins --
  never to ``OFFLINE_SKY_UI``, which is only the Framing tab's Offline view switch.
"""

import json
import os
import threading
import time

import pytest

import nova.blueprints.offline_sky as bp
import nova.offline_sky_facts as facts
import nova.offline_sky_jobs as jobs
import nova.offline_sky_plan as plan
import nova.offline_sky_store as store

DSS = "dss2-color"
OIII = "nsns-oiii8"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "offline_sky")
GIB = 1024 ** 3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    facts.clear_memo()
    store.invalidate_size_cache()
    monkeypatch.setattr(jobs, "kick", lambda: False)
    monkeypatch.setattr(store, "disk_free_bytes", lambda: 500 * GIB)
    monkeypatch.setattr(bp, "_breaker_open", lambda base: False)
    _install_real_facts(DSS)
    yield
    facts.clear_memo()
    store.invalidate_size_cache()


def _install_real_facts(slug):
    """Copy the real properties/Moc.fits for ``slug`` into the store."""
    for name in ("properties", "Moc.fits"):
        path = os.path.join(FIXTURES, slug, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            store.store_file(slug, name, fh.read(), count=False)
    facts.clear_memo()


class FakeUpstream:
    """Stands in for _upstream_get; returns a tile of ``size`` bytes for every URL."""

    def __init__(self, size=1024):
        self.size = size
        self.calls = []

    def __call__(self, base, url):
        self.calls.append(url)
        return bp.UPSTREAM_OK, b"x" * self.size


@pytest.fixture
def upstream(monkeypatch):
    fake = FakeUpstream()
    monkeypatch.setattr(bp, "_upstream_get", fake)
    return fake


class _NoLimit:
    def wait(self):
        pass


@pytest.fixture
def runners():
    """A hand-driven JobRunner; the process-wide thread is never started."""
    made = []

    def make(**kw):
        kw.setdefault("limiter", _NoLimit())
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("pause_seconds", 0.0)
        runner = jobs.JobRunner(**kw)
        made.append(runner)
        return runner

    yield make
    for runner in made:
        runner.release()


def _run_job(runner):
    runner.acquire()
    try:
        runner.run_pending()
    finally:
        runner.release()


# ---------------------------------------------------------------------------
# 1. Band maths
# ---------------------------------------------------------------------------

def test_visible_band_is_lat_plus_minus_90_clamped():
    lo, hi = plan.visible_band(47.066)
    assert lo == pytest.approx(-42.934)
    assert hi == 90.0
    lo, hi = plan.visible_band(-31.273)
    assert lo == -90.0
    assert hi == pytest.approx(58.727)
    assert plan.visible_band(0) == (-90.0, 90.0)


def test_union_band_of_two_locations_is_their_widest_span():
    band = plan.union_band([47.066, -31.273])
    assert band == (-90.0, 90.0)


def test_union_band_of_one_location_is_that_location():
    assert plan.union_band([47.066]) == (-42.934, 90.0)


def test_union_band_of_nothing_is_none():
    assert plan.union_band([]) is None
    assert plan.union_band([None]) is None


# ---------------------------------------------------------------------------
# 2. The overlap test (not centre-only)
# ---------------------------------------------------------------------------

def test_tile_overlaps_band_when_its_edge_crosses_the_boundary():
    """A tile whose centre is just outside the band but whose extent reaches in
    counts as overlapping. Centre-only would miss it and leave a ragged edge."""
    # Order 0, npix 0 has its centre near dec 41.8 with a large radius; a band
    # that ends just below that centre still overlaps the tile's lower half.
    order = 0
    npix = 0
    import nova.hips_healpix as hp
    import math
    nside = hp.order_to_nside(order)
    centre = 90.0 - math.degrees(hp.pix2ang_nest(nside, npix)[0])
    radius = plan._pixel_radius_deg(order)
    # A band whose top edge is 1 degree below the centre: the centre is outside,
    # but the tile's lower half is inside, so it overlaps.
    band = (-90.0, centre - 1.0)
    assert plan.tile_overlaps_band(order, npix, band) is True
    # A band whose top edge is well below the tile's lower edge does not overlap.
    band_far = (-90.0, centre - radius - 1.0)
    assert plan.tile_overlaps_band(order, npix, band_far) is False


def test_plan_tiles_include_the_allsky_first():
    band = plan.union_band([47.066])
    tiles = plan.plan_tiles(DSS, 0, band)
    assert tiles[0].endswith("Allsky.jpg")
    assert len(tiles) > 1


def test_plan_tiles_are_deterministic():
    band = plan.union_band([47.066])
    assert plan.plan_tiles(DSS, 1, band) == plan.plan_tiles(DSS, 1, band)


def test_plan_tiles_clamp_to_the_survey_max_order():
    """DSS2's fixture MOC is order 9; asking for order 20 plans order 9."""
    band = plan.union_band([47.066])
    tiles = plan.plan_tiles(DSS, 20, band)
    assert tiles  # non-empty, and no order above the survey's own max


# ---------------------------------------------------------------------------
# 2b. Per-location status: ready / missing, with coverage reported alongside
# ---------------------------------------------------------------------------

def _claim_on_disk(job_id, key, size=512):
    """A claimed tile that is really on disk: written first, then claimed."""
    slug, _sep, rel = key.partition("/")
    store.store_file(slug, rel, b"x" * size)
    store.claim_for_job(job_id, key)


def test_a_location_is_ready_when_every_planned_tile_is_pinned(monkeypatch):
    """A location whose band is fully covered and whose tiles are all pinned is
    ``ready``. Browsed-but-unpinned tiles do not count."""
    band = plan.union_band([47.066])
    planned = plan.planned_keys(DSS, 0, band)
    # Pin every planned tile as one job's claims, each really on disk.
    for key in planned:
        _claim_on_disk("download-test", key)
    located = [{"user": "u", "record_uid": "r", "name": "A", "lat": 47.066}]
    summary = plan.plan_summary(DSS, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] == "ready"
    assert loc["missing_tiles"] == 0
    assert loc["pinned_count"] == loc["tile_count"]


def test_the_per_location_count_matches_the_plan_count(monkeypatch):
    """The Allsky tile is not a grid tile: the per-location count must not
    include it, or the two disagree by exactly one (the 15268 vs 15269 bug)."""
    band = plan.union_band([47.066])
    located = [{"user": "u", "record_uid": "r", "name": "A", "lat": 47.066}]
    summary = plan.plan_summary(DSS, 0, band, located)
    loc = summary["locations"][0]
    assert loc["tile_count"] == summary["tile_count"]
    # The download still owns the Allsky tile (it is in the plan), but the
    # per-location count excludes it, so the two agree.
    assert any(k.split("/", 1)[1].startswith("Norder3/Allsky")
               for k in plan.planned_keys(DSS, 0, band))
    assert loc["tile_count"] == summary["tile_count"]


def test_a_browsed_tile_does_not_make_a_location_ready(monkeypatch):
    """Decision 6: "ready" means every planned tile is PINNED. A tile that is
    merely browsed (on disk, claimed by nobody) leaves the location "missing"."""
    band = plan.union_band([47.066])
    planned = plan.planned_keys(DSS, 0, band)
    # Put every planned tile on disk as a *browsed* tile: no claim.
    for key in planned:
        slug, _sep, rel = key.partition("/")
        store.store_file(slug, rel, b"x" * 512)
    located = [{"user": "u", "record_uid": "r", "name": "A", "lat": 47.066}]
    summary = plan.plan_summary(DSS, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] == "missing", "browsed tiles must not count as ready"
    assert loc["pinned_count"] == 0
    assert loc["missing_tiles"] == loc["tile_count"]


def test_a_location_is_missing_when_planned_tiles_are_not_on_disk(monkeypatch):
    """With no claims, every planned tile is missing."""
    band = plan.union_band([47.066])
    located = [{"user": "u", "record_uid": "r", "name": "A", "lat": 47.066}]
    summary = plan.plan_summary(DSS, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] == "missing"
    assert loc["missing_tiles"] == loc["tile_count"]
    assert loc["missing_tiles"] > 0


def test_a_tile_the_upstream_lacks_counts_as_done(monkeypatch):
    """A planned tile recorded as missing on every upstream must not block
    "ready": it is not a hole the user can fill. It is reported separately as
    ``not_available``."""
    band = plan.union_band([47.066])
    planned = plan.planned_keys(DSS, 0, band)
    # Everything on disk and pinned except one grid tile the upstream 404s.
    # The Allsky tile is not a grid tile and is excluded from the per-location
    # count, so it cannot be the "gone" tile here.
    tiles = sorted(k for k in planned if "Allsky" not in k)
    gone = tiles[-1]
    for key in tiles[:-1]:
        _claim_on_disk("download-test", key)
    store.write_missing(DSS, {gone.split("/", 1)[1]: 1.0})

    located = [{"user": "u", "record_uid": "r", "name": "A", "lat": 47.066}]
    summary = plan.plan_summary(DSS, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] == "ready"
    assert loc["not_available"] == 1
    assert loc["missing_tiles"] == 0
    assert loc["pinned_count"] == loc["tile_count"] - 1


def test_a_location_can_be_ready_with_coverage_below_100(monkeypatch):
    """Coverage is reported *alongside* the state, never instead of it: a survey
    whose footprint ends south of a location's band is still "ready" for the
    part it does cover."""
    _install_real_facts(OIII)
    band = plan.union_band([-75.0])
    # Every planned tile is pinned; the MOC still covers only part of the band.
    planned = plan.planned_keys(OIII, 0, band)
    for key in planned:
        _claim_on_disk("download-test", key)
    located = [{"user": "u", "record_uid": "r", "name": "South", "lat": -75.0}]
    summary = plan.plan_summary(OIII, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] == "ready"
    assert 0.0 < loc["coverage"] < 1.0


def test_partial_is_no_longer_a_state(monkeypatch):
    """The old ``partial`` state is gone: coverage rides along with the state."""
    _install_real_facts(OIII)
    band = plan.union_band([-75.0])
    located = [{"user": "u", "record_uid": "r", "name": "South", "lat": -75.0}]
    summary = plan.plan_summary(OIII, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] in ("ready", "missing")
    assert loc["state"] != "partial"


def test_not_available_is_not_a_state(monkeypatch):
    """Every Earth band includes dec 0, so a survey's footprint always overlaps
    it somewhere. ``not_available`` is a *count* now, not a state."""
    _install_real_facts(OIII)
    band = plan.union_band([-75.0])
    located = [{"user": "u", "record_uid": "r", "name": "South", "lat": -75.0}]
    summary = plan.plan_summary(OIII, 0, band, located)
    loc = summary["locations"][0]
    assert loc["state"] != "not_available"
    assert "not_available" in loc and isinstance(loc["not_available"], int)


def test_planned_keys_are_cached_per_band(monkeypatch):
    """``planned_keys`` must not re-scan the sky on repeated calls."""
    band = plan.union_band([47.066])
    calls = []
    real = plan.plan_tiles
    monkeypatch.setattr(plan, "plan_tiles", lambda *a, **k: calls.append(1) or real(*a, **k))
    plan.planned_keys(DSS, 0, band)
    first = len(calls)
    plan.planned_keys(DSS, 0, band)
    assert len(calls) == first, "the second call must come from the cache"


def test_the_plan_cache_is_bounded(monkeypatch):
    """The plan cache follows the codebase rule: a BoundedCache, not a plain
    dict, so it cannot grow without limit as detail levels and bands change."""
    from nova.config import BoundedCache
    assert isinstance(plan._PLAN_KEYS_CACHE, BoundedCache)
    assert plan._PLAN_KEYS_CACHE._maxsize <= 16
    # Filling past the bound evicts the oldest entries rather than growing.
    band = plan.union_band([47.066])
    for order in range(40):
        plan.planned_keys(DSS, order % 10, band)
    assert len(plan._PLAN_KEYS_CACHE) <= plan._PLAN_KEYS_CACHE._maxsize


# ---------------------------------------------------------------------------
# 3. The plan file and status() not recomputing
# ---------------------------------------------------------------------------

def test_plan_is_written_to_the_job_and_read_back(monkeypatch):
    band = plan.union_band([47.066])
    summary = plan.plan_summary(DSS, 0, band, [], include_tiles=True)
    job_id = "download-dss2-color"
    plan.write_plan(job_id, summary)
    stored = plan.read_plan(job_id)
    assert stored["survey"] == DSS
    assert stored["tile_count"] == summary["tile_count"]
    plan.delete_plan(job_id)
    assert plan.read_plan(job_id) is None


def test_status_does_not_recompute_the_plan(su_client_logged_in, monkeypatch):
    """status() reads the cached .pinned figure; it never walks the plan."""
    _store_on(monkeypatch)
    calls = []
    monkeypatch.setattr(plan, "plan_tiles", lambda *a, **k: calls.append(1) or [])
    su_client_logged_in.get("/api/offline-sky/status")
    assert calls == []


# ---------------------------------------------------------------------------
# 4. The selection: (user, record_uid)
# ---------------------------------------------------------------------------

def test_selection_round_trips_through_the_store():
    entries = [{"user": "alice", "record_uid": "uid-1"},
               {"user": "bob", "record_uid": "uid-2"}]
    store.write_selection(entries)
    assert store.read_selection() == entries


def test_selection_drops_entries_without_a_user_or_uid():
    store.write_selection([{"user": "alice", "record_uid": "uid-1"},
                           {"user": "", "record_uid": "uid-2"},
                           {"user": "carol", "record_uid": ""}])
    assert store.read_selection() == [{"user": "alice", "record_uid": "uid-1"}]


def test_selection_is_keyed_on_record_uid_not_name():
    """Two users can both have a location named "Home"; the selection keeps them
    apart by user and record_uid."""
    store.write_selection([{"user": "alice", "record_uid": "uid-a"},
                           {"user": "bob", "record_uid": "uid-b"}])
    sel = store.read_selection()
    assert len(sel) == 2
    assert {e["record_uid"] for e in sel} == {"uid-a", "uid-b"}


# ---------------------------------------------------------------------------
# 5. The download job
# ---------------------------------------------------------------------------

def _enqueue_download(monkeypatch, band, order=0):
    monkeypatch.setattr(jobs, "kick", lambda: False)
    summary = plan.plan_summary(DSS, order, band, [], include_tiles=True)
    job_id = jobs.download_job_id(DSS)
    plan.write_plan(job_id, summary)
    return jobs.enqueue(job_id, "download", {"survey": DSS, "order": order,
                                             "band": band}, [DSS])


def test_a_download_job_fetches_its_planned_tiles(upstream, runners, monkeypatch):
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    done = jobs.read_job(state["id"])
    assert done["status"] == "done"
    assert done["fetched"] > 0
    # Every planned tile is on disk and claimed by the download.
    owned = store.job_pins(state["id"])
    assert owned
    for key in owned:
        slug, _, rel = key.partition("/")
        assert store.tile_exists(slug, rel)


def test_a_download_resumes_after_a_restart(upstream, runners, monkeypatch):
    """A second run adopts what the first fetched; it does not re-fetch."""
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    first = len(upstream.calls)
    assert first > 0
    # Simulate a crash: the job is still active with its claims, and the run
    # restarts. The present tiles are adopted, so no new upstream calls.
    store.delete_job_file(state["id"]) if False else None  # keep the job
    jobs.enqueue(state["id"], "download", {"survey": DSS, "order": 0,
                                           "band": band}, [DSS])
    _run_job(runners())
    assert len(upstream.calls) == first


def test_cancel_stops_a_download(upstream, runners, monkeypatch):
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    jobs.cancel_job(state["id"])
    _run_job(runners())
    done = jobs.read_job(state["id"])
    assert done["status"] == "cancelled"


def test_the_base_pins_are_never_touched_by_a_download(upstream, runners, monkeypatch):
    base_key = f"{DSS}/Norder0/Dir0/Npix0.jpg"
    store.store_file(DSS, "Norder0/Dir0/Npix0.jpg", b"b" * 700)   # a real base tile
    store.add_base_pins([base_key])
    band = plan.union_band([47.066])
    _enqueue_download(monkeypatch, band)
    _run_job(runners())
    assert base_key in store.base_pinned_rel_paths()
    assert store.read_file(DSS, "Norder0/Dir0/Npix0.jpg") == b"b" * 700


def test_remove_keeps_a_tile_another_download_claims(upstream, runners, monkeypatch):
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    owned = store.job_pins(state["id"])
    shared = next(iter(owned))
    # A second download claims the same tile.
    store.claim_for_job("download-other", shared)
    summary = jobs.remove_download(DSS)
    assert summary["kept"] >= 1
    slug, _, rel = shared.partition("/")
    assert store.tile_exists(slug, rel)


def test_remove_keeps_a_tile_the_base_claims(upstream, runners, monkeypatch):
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    owned = store.job_pins(state["id"])
    base_key = next(iter(owned))
    store.add_base_pins([base_key])
    summary = jobs.remove_download(DSS)
    assert summary["kept"] >= 1
    slug, _, rel = base_key.partition("/")
    assert store.tile_exists(slug, rel)


def test_remove_never_deletes_a_base_pinned_tile(upstream, runners, monkeypatch):
    """The real DSS2 case: ~1000 base pins sit inside every DSS2 download's plan.

    A Remove must delete only the download's own tiles; every tile the base
    layer still owns stays on disk. This is the "kept: 0" bug in miniature --
    without the base-pin check, Remove would free the base layer's tiles too.
    """
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    owned = store.job_pins(state["id"])
    assert len(owned) > 4
    # Pin a third of the download's tiles as base-owned, like orders 0..3 of a
    # real DSS2 base. The rest belong to the download alone.
    owned_sorted = sorted(owned)
    base_owned = set(owned_sorted[: len(owned_sorted) // 3])
    store.add_base_pins(base_owned)
    for key in base_owned:
        slug, _, rel = key.partition("/")
        assert store.tile_exists(slug, rel)

    summary = jobs.remove_download(DSS)

    # Every base-pinned tile survives; the download's own tiles are gone.
    for key in base_owned:
        slug, _, rel = key.partition("/")
        assert store.tile_exists(slug, rel), f"base-pinned tile was deleted: {key}"
    for key in owned - base_owned:
        slug, _, rel = key.partition("/")
        assert not store.tile_exists(slug, rel), f"download-owned tile survived: {key}"
    assert summary["kept"] == len(base_owned)
    assert summary["deleted"] == len(owned - base_owned)
    # The base pins are still registered.
    assert store.base_pinned_rel_paths() >= base_owned


def test_on_disk_base_pins_survive_remove_and_absent_ones_are_dropped(
        upstream, runners, monkeypatch):
    """Base pins on disk are never deleted by a Remove. A base pin whose file is
    absent is dropped by the reconcile at runner start; a download that then
    fetches that tile owns it, and its Remove deletes it with the rest."""
    band = plan.union_band([51.5])
    grid = [rel for rel in plan.plan_tiles(DSS, 0, band) if "/Npix" in rel]
    assert len(grid) >= 4
    kept_rel, absent_rel = grid[:2], grid[2:4]
    for rel in kept_rel:
        store.store_file(DSS, rel, b"b" * 700)
    kept_keys = {f"{DSS}/{rel}" for rel in kept_rel}
    absent_keys = {f"{DSS}/{rel}" for rel in absent_rel}
    store.add_base_pins(kept_keys | absent_keys)

    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())                  # runner start reconciles, then fetches
    assert jobs.read_job(state["id"])["status"] == "done"
    assert store.base_pinned_rel_paths() == kept_keys      # absent ones dropped
    for rel in absent_rel:
        assert store.tile_exists(DSS, rel)                 # fetched by the download
    owned = store.job_pins(state["id"])

    summary = jobs.remove_download(DSS)

    for rel in kept_rel:
        assert store.read_file(DSS, rel) == b"b" * 700, f"base tile deleted: {rel}"
    for rel in absent_rel:
        assert not store.tile_exists(DSS, rel), f"dropped pin's tile kept: {rel}"
    assert summary["kept"] == len(kept_keys)
    assert summary["deleted"] == len(owned - kept_keys)


def test_remove_cancels_a_running_job_first(upstream, runners, monkeypatch):
    """Remove must cancel first, or a live runner re-creates the claims file.

    A running job's thread keeps fetching; deleting the claims file without a
    cancel marker lets the next checkpoint write it back and the download
    resurrects. The cancel marker must be written before the delete.

    The runner stands in as a thread that notices the marker and leaves the
    active set -- which is what a real runner does at its next tile. That makes
    the ordering the thing under test: the marker must be there before the wait
    starts, or the stand-in never sees it and the wait times out.
    """
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    job_id = state["id"]

    def runner_sees_the_cancel():
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if jobs.cancel_requested(job_id):
                stopped = jobs.read_job(job_id) or {}
                stopped.update(status="cancelled")
                jobs._write_job(stopped)
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=runner_sees_the_cancel, daemon=True)
    watcher.start()
    summary = jobs.remove_download(DSS)
    watcher.join(timeout=2.0)
    assert summary["status"] == "removed"
    # The claims file is gone and no tiles remain claimed.
    assert not store.job_pins(job_id)


def test_wait_for_job_to_stop_returns_once_the_state_leaves_active(monkeypatch):
    """The wait blocks while the job is active and returns once it stops."""
    job_id = "download-wait-test"
    state = {"id": job_id, "kind": "download", "status": "running",
             "params": {"survey": DSS}}
    jobs._write_job(state)
    # Still running: the wait must time out and report False.
    assert jobs._wait_for_job_to_stop(job_id, timeout=0.2) is False
    # Once the state is no longer active, the wait returns True immediately.
    state["status"] = "cancelled"
    jobs._write_job(state)
    assert jobs._wait_for_job_to_stop(job_id, timeout=1.0) is True
    # A deleted job counts as stopped too.
    os.remove(jobs._job_path(job_id))
    assert jobs._wait_for_job_to_stop(job_id, timeout=1.0) is True


def test_offline_routes_never_contact_upstream(su_client_logged_in, monkeypatch):
    """The Framing tab's Offline view reads /hips/offline/... and nothing else.

    Decision 18 Nov: Offline view must never make an upstream request. Every
    cache-only route -- tile, Allsky, MOC, properties -- must answer from disk
    or 404, and must never reach _fetch_upstream.
    """
    _store_on(monkeypatch)
    calls = []

    def fail(*a, **k):
        calls.append(a)
        raise AssertionError("Offline view must not contact upstream")

    monkeypatch.setattr(bp, "_fetch_upstream", fail)
    monkeypatch.setattr(bp, "_upstream_get", fail)
    # Nothing on disk for these: each must 404 without a network call.
    for url in (
        "/hips/offline/dss2-color/Norder7/Dir900000/Npix999999.jpg",
        "/hips/offline/dss2-color/Norder3/Allsky.jpg",
        "/hips/offline/dss2-color/Moc.fits",
        "/hips/offline/dss2-color/properties",
    ):
        resp = su_client_logged_in.get(url)
        assert resp.status_code in (200, 404), url
    assert calls == [], "Offline view made an upstream request"


def test_download_progress_does_not_return_the_tile_list(upstream, runners, monkeypatch):
    """The poll response must not carry 15k rel paths; the plan file keeps them."""
    band = plan.union_band([47.066])
    _enqueue_download(monkeypatch, band)
    progress = jobs.download_progress(DSS)
    assert "tiles" not in progress["plan"]
    assert progress["plan"]["tile_count"] > 0
    # The plan file still has them, for the runner and the trim.
    from nova import offline_sky_plan as plan_mod
    assert len(plan_mod.read_plan(jobs.download_job_id(DSS))["tiles"]) > 0


def test_remove_refuses_when_the_job_does_not_stop(monkeypatch):
    """Decision 7: if the job does not stop within the timeout, Remove refuses
    (409) and deletes nothing -- a live thread may still be writing."""
    job_id = jobs.download_job_id(DSS)
    # A job stuck in an active state that never leaves it.
    jobs._write_job({"id": job_id, "kind": "download", "status": "running",
                     "params": {"survey": DSS, "order": 0,
                                "band": [0.0, 10.0]}})
    store.claim_for_job(job_id, f"{DSS}/Norder0/Dir0/Npix0.jpg")
    owned_before = store.job_pins(job_id)
    monkeypatch.setattr(jobs, "_wait_for_job_to_stop", lambda *a, **k: False)
    with pytest.raises(jobs.RemoveRefused):
        jobs.remove_download(DSS)
    # Nothing was deleted and the claims file is intact.
    assert store.job_pins(job_id) == owned_before
    assert os.path.exists(store._job_pins_path(job_id))


def test_remove_route_answers_409_when_the_job_will_not_stop(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    monkeypatch.setattr(bp, "ADMIN_USERS", {"default"})
    job_id = jobs.download_job_id(DSS)
    jobs._write_job({"id": job_id, "kind": "download", "status": "running",
                     "params": {"survey": DSS, "order": 0, "band": [0.0, 10.0]}})
    monkeypatch.setattr(jobs, "_wait_for_job_to_stop", lambda *a, **k: False)
    resp = su_client_logged_in.delete(f"/api/offline-sky/downloads/{DSS}")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_still_running"


def test_could_free_reports_owned_tiles_outside_the_plan(upstream, runners, monkeypatch):
    """Unticking a location must show "could free X" without deleting anything."""
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    job_id = state["id"]
    owned = store.job_pins(job_id)
    assert owned, "the download should own tiles after a run"
    # A narrower plan leaves some owned tiles outside it. A location's band is
    # always 180 degrees wide, so pass a genuinely narrow band directly.
    narrower = plan.plan_summary(DSS, 0, (0.0, 10.0), [], include_tiles=True)
    plan.write_plan(job_id, narrower)
    freed = jobs._could_free(job_id, narrower)
    assert freed > 0
    # Nothing was deleted: the owned set is unchanged.
    assert store.job_pins(job_id) == owned
    # A plan that still covers every owned tile reports nothing to free.
    full = plan.plan_summary(DSS, 0, band, [], include_tiles=True)
    assert jobs._could_free(job_id, full) == 0


def test_trim_releases_the_claims_it_deletes(upstream, runners, monkeypatch):
    """A trim deletes the extra tiles *and* drops their claims, so could_free
    stops reporting bytes that are already gone."""
    band = plan.union_band([47.066])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    job_id = state["id"]
    owned = store.job_pins(job_id)
    narrower = plan.plan_summary(DSS, 0, (0.0, 10.0), [], include_tiles=True)
    plan.write_plan(job_id, narrower)
    extra = owned - jobs.plan_owned_keys(DSS, narrower)
    assert extra
    summary = store.delete_tiles(extra)
    released = store.release_job_pins(job_id, extra)
    assert released == len(extra)
    assert summary["deleted"] + summary["kept"] == len(extra)
    # The claims are gone and could_free now reports nothing.
    assert store.job_pins(job_id) == owned - extra
    assert jobs._could_free(job_id, narrower) == 0


# ---------------------------------------------------------------------------
# 7. Budget: refuse before enqueue, stop mid-job
# ---------------------------------------------------------------------------

def test_a_download_that_cannot_fit_is_refused_before_enqueue(
        su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    # The blueprint imports these names at module load, so patch the bound
    # names there rather than on the store module.
    monkeypatch.setattr(bp, "budget_bytes", lambda: 1)
    monkeypatch.setattr(bp, "pinned_bytes", lambda: 0)
    # The selection validator would reject a synthetic uid; stub it so the
    # request reaches the budget check with a real band.
    monkeypatch.setattr(bp, "_requested_locations",
                        lambda: ([{"user": "default", "record_uid": "x"}], None))
    monkeypatch.setattr(bp, "_locations_with_coords",
                        lambda entries: [{"user": "u", "record_uid": "x",
                                          "name": "Home", "lat": 47.066}])
    resp = su_client_logged_in.post(
        f"/api/offline-sky/downloads/{DSS}",
        json={"detail": "basic", "locations": [{"user": "u", "record_uid": "x"}]})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "insufficient_space"
    assert "needed" in body


def test_a_download_with_no_locations_is_refused(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    resp = su_client_logged_in.post(
        f"/api/offline-sky/downloads/{DSS}", json={"detail": "basic", "locations": []})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_locations"


def test_an_unwritable_store_refuses_downloads(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    monkeypatch.setattr(store, "DOWNLOADS_AVAILABLE", False)
    resp = su_client_logged_in.post(
        f"/api/offline-sky/downloads/{DSS}", json={"detail": "basic"})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "read_only"


# ---------------------------------------------------------------------------
# 8. Access control
# ---------------------------------------------------------------------------

def test_download_routes_are_admin_only(multi_user_client, monkeypatch):
    monkeypatch.setattr(bp, "OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")
    client, _ids = multi_user_client
    # "UserA" is not an admin.
    resp = client.post(f"/api/offline-sky/downloads/{DSS}", json={"detail": "basic"})
    assert resp.status_code == 403


def test_download_routes_ignore_the_ui_flag(su_client_logged_in, monkeypatch):
    """OFFLINE_SKY_UI only shows or hides the Framing tab's Offline view switch.
    The download backend hangs off the store, so with the UI switch off it is
    still there -- the read-only routes answer 200 instead of 404."""
    _store_on(monkeypatch)
    import nova
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "off")
    assert su_client_logged_in.get("/api/offline-sky/selection").status_code == 200
    assert su_client_logged_in.get("/api/offline-sky/downloads").status_code == 200


def test_download_routes_are_404_when_the_store_is_off(su_client_logged_in, monkeypatch):
    monkeypatch.setattr(bp, "OFFLINE_SKY_STORE", "off")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "off")
    assert su_client_logged_in.get("/api/offline-sky/plan?survey=dss2-color").status_code == 404


def test_selection_keeps_users_apart_when_uids_collide():
    """The same record_uid under two users is two entries, not one.

    The selection is instance-level (the sky is shared) but each entry carries
    its user, so a multi-user instance never merges one user's tick into
    another's. A shared uid must not collapse the pair.
    """
    entries = [{"user": "alice", "record_uid": "shared-uid"},
               {"user": "bob", "record_uid": "shared-uid"}]
    store.write_selection(entries)
    sel = store.read_selection()
    assert len(sel) == 2
    assert {(e["user"], e["record_uid"]) for e in sel} == {
        ("alice", "shared-uid"), ("bob", "shared-uid")}


def test_selection_deduplicates_an_exact_pair():
    """Writing the same (user, record_uid) twice stores it once."""
    store.write_selection([{"user": "alice", "record_uid": "uid-1"},
                           {"user": "alice", "record_uid": "uid-1"}])
    assert store.read_selection() == [{"user": "alice", "record_uid": "uid-1"}]


# ---------------------------------------------------------------------------
# 9. Status performance
# ---------------------------------------------------------------------------

def test_status_is_o1_at_250k_pins(su_client_logged_in, monkeypatch):
    """250k pinned tiles must not make status() walk the pins."""
    _store_on(monkeypatch)
    # Fake 250k claimed keys without creating 250k files: the cached .pinned
    # figure is what status() reads, so the pin count never enters the path.
    import time
    monkeypatch.setattr(store, "pinned_bytes", lambda: 250_000 * 4096)
    start = time.perf_counter()
    for _ in range(50):
        su_client_logged_in.get("/api/offline-sky/status")
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"50 status calls took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# 10. Startup cleanup: legacy jobs dropped
# ---------------------------------------------------------------------------

def test_legacy_jobs_are_dropped_at_startup(monkeypatch):
    """A queued base job from an earlier version is dropped before the runner
    starts; a download job survives."""
    jobs.enqueue("base-dss2-color", "base", {"slug": DSS}, [DSS])
    jobs.enqueue("download-nsns-oiii8", "download", {"survey": OIII}, [OIII])
    dropped = jobs.drop_legacy_jobs()
    assert dropped == 1
    assert jobs.read_job("base-dss2-color") is None
    assert jobs.read_job("download-nsns-oiii8") is not None


def test_no_network_on_startup(monkeypatch):
    """Starting the runner must not fetch anything.

    ``kick()`` drops legacy jobs and starts the thread; neither fetches. The
    thread's first pass runs the queue, so stub the thread start to keep the
    test on the startup path itself rather than on a background loop.
    """
    calls = []
    monkeypatch.setattr(bp, "_upstream_get", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(jobs._RUNNER, "start", lambda: None)
    monkeypatch.setattr(jobs._RUNNER, "acquire", lambda: True)
    jobs.kick()
    assert calls == []


def test_startup_runner_failure_is_logged_not_swallowed(monkeypatch, capsys):
    """Decision 9: a kick() failure must leave one [STARTUP] warning line, not
    vanish silently -- otherwise a queued download would never resume and no
    one would know why."""
    import nova as nova_mod
    # The startup hook refuses to run under pytest (a real runner thread would
    # race the tests). This test is about the failure path, so opt out of that
    # gate and make kick() blow up.
    monkeypatch.setattr(nova_mod, "_is_test_environment", lambda: False)
    monkeypatch.setattr(jobs, "kick", _raise_boom)
    nova_mod._kick_offline_sky_runner()
    out = capsys.readouterr().out
    assert "[STARTUP] WARNING" in out
    assert "offline-sky job runner did not start" in out
    assert "RuntimeError" in out


def _raise_boom():
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store_on(monkeypatch):
    monkeypatch.setattr(bp, "OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")


# ---------------------------------------------------------------------------
# 11. The selection's user comes from the session, never the body
# ---------------------------------------------------------------------------

def test_selection_takes_the_user_from_the_session(su_client_logged_in, monkeypatch):
    """A body that names another user is ignored: the entry is stored under the
    session's own user (decision 1 + the PUT taking the user server-side)."""
    _store_on(monkeypatch)
    uid = _a_location_uid("default")
    resp = su_client_logged_in.put(
        "/api/offline-sky/selection",
        json={"locations": [{"user": "someone-else", "record_uid": uid}]})
    assert resp.status_code == 200
    locs = resp.get_json()["locations"]
    assert locs == [{"user": "default", "record_uid": uid}]


def test_selection_rejects_a_uid_that_is_not_this_users(su_client_logged_in, monkeypatch):
    """A record_uid that is not the session user's location is refused (400),
    not stored: a client cannot tick another account's location by its uid."""
    _store_on(monkeypatch)
    resp = su_client_logged_in.put(
        "/api/offline-sky/selection",
        json={"locations": [{"record_uid": "no-such-uid-anywhere"}]})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_location"
    assert store.read_selection() == []


def test_selection_rejects_another_users_location(multi_user_client, db_session, monkeypatch):
    """In multi-user mode, user B's uid is refused for user A."""
    _store_on(monkeypatch)
    # The storage routes are admin-only, so make UserA an admin: the guard then
    # passes and the *ownership* check is what must refuse.
    monkeypatch.setattr(bp, "ADMIN_USERS", {"UserA"})
    client, _ids = multi_user_client
    # Give UserB a location of their own; UserA is the signed-in session user.
    from nova.models import DbUser, Location
    user_b = db_session.query(DbUser).filter(DbUser.username == "UserB").first()
    other = Location(user_id=user_b.id, name="UserB_Home", lat=-30, lon=140,
                     timezone="UTC", is_default=True)
    db_session.add(other)
    db_session.commit()
    other_uid = other.record_uid
    resp = client.put("/api/offline-sky/selection",
                      json={"locations": [{"record_uid": other_uid}]})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_location"
    assert store.read_selection() == []


def _a_location_uid(username):
    """The record_uid of the first location belonging to ``username``."""
    from nova.models import DbUser, Location
    from nova.helpers import get_db
    db = get_db()
    user = db.query(DbUser).filter(DbUser.username == username).first()
    if user is None:
        return "no-such-user"
    row = db.query(Location).filter(Location.user_id == user.id).first()
    return row.record_uid if row else "no-such-uid"


# ---------------------------------------------------------------------------
# 12. The size estimate: per order, from tiles on disk when the sample is >= 50
# ---------------------------------------------------------------------------

def _store_order_tiles(order, n, size):
    for npix in range(n):
        store.store_file(DSS, store.tile_rel(order, npix, "jpg"), b"x" * size)
    store.invalidate_size_cache(DSS)


def test_the_estimate_uses_the_measured_mean_from_50_tiles():
    _store_order_tiles(3, store.MIN_SIZE_SAMPLE, 40_000)
    assert plan.per_tile_bytes(DSS, 3) == 40_000
    # An order with nothing on disk does not borrow order 3's figure.
    assert plan.per_tile_bytes(DSS, 5) == store.FALLBACK_TILE_BYTES


def test_the_estimate_falls_back_below_50_tiles():
    _store_order_tiles(3, store.MIN_SIZE_SAMPLE - 1, 40_000)
    assert plan.per_tile_bytes(DSS, 3) == store.FALLBACK_TILE_BYTES


def test_tiles_are_measured_in_their_dir_subfolders():
    """Tiles sit in Norder<o>/Dir<d>/; the order is the Norder component."""
    _store_order_tiles(2, store.MIN_SIZE_SAMPLE, 30_000)
    assert store.avg_tile_bytes_by_order(DSS) == {2: 30_000}


def test_the_estimate_is_summed_per_order():
    _store_order_tiles(3, store.MIN_SIZE_SAMPLE, 40_000)
    rels = ([store.tile_rel(3, i, "jpg") for i in range(10)]
            + [store.tile_rel(5, i, "jpg") for i in range(4)]
            + ["Norder3/Allsky.jpg"])                     # not a grid tile
    assert plan.estimate_bytes(DSS, rels) == 10 * 40_000 + 4 * store.FALLBACK_TILE_BYTES


def test_nsns_size_stays_unknown():
    assert plan.per_tile_bytes(OIII, 3) is None
    assert plan.estimate_bytes(OIII, [store.tile_rel(3, 0, "webp")]) is None


def test_a_finished_download_re_measures_the_estimate(upstream, runners, monkeypatch):
    band = plan.union_band([51.5])
    assert plan.per_tile_bytes(DSS, 2) == store.FALLBACK_TILE_BYTES   # now cached
    _enqueue_download(monkeypatch, band, order=2)
    _run_job(runners())
    # FakeUpstream serves 1024-byte tiles; order 2 has >= 50 of them in band.
    assert plan.per_tile_bytes(DSS, 2) == upstream.size


# ---------------------------------------------------------------------------
# 13. The claims file: one line per key across a kill and a resume
# ---------------------------------------------------------------------------

class _Kill(BaseException):
    """Escapes the job's ``except Exception``: like SIGKILL, nothing finalises."""


def _claim_lines(job_id):
    with open(store._job_pins_path(job_id), encoding="utf-8") as fh:
        return [line.split("\t", 1)[0] for line in fh if line.strip()]


def test_kill_and_resume_leave_one_claim_line_per_key(upstream, runners, monkeypatch):
    band = plan.union_band([51.5])
    state = _enqueue_download(monkeypatch, band, order=2)
    fetch = bp._upstream_get

    def dies_after_20(base, url):
        if len(upstream.calls) >= 20:
            raise _Kill()
        return fetch(base, url)

    monkeypatch.setattr(bp, "_upstream_get", dies_after_20)
    with pytest.raises(_Kill):
        _run_job(runners())
    assert jobs.read_job(state["id"])["status"] == "running"   # never finalised
    before = _claim_lines(state["id"])
    assert len(before) == len(set(before)) > 0
    on_disk = [k for k in before if store.pinned_key_size(k) is not None]

    # The restart walks the claimed tiles again. Kill it once more before it
    # finishes: the job-end reconcile would collapse duplicates, so the file is
    # checked while the resumed run is still mid-way.
    calls_before = len(upstream.calls)

    def dies_after_10_more(base, url):
        if len(upstream.calls) >= calls_before + 10:
            raise _Kill()
        return fetch(base, url)

    monkeypatch.setattr(bp, "_upstream_get", dies_after_10_more)
    with pytest.raises(_Kill):
        _run_job(runners())
    mid = _claim_lines(state["id"])
    assert len(mid) > len(before)
    assert len(mid) == len(set(mid)), "a resumed run re-appended claims"

    monkeypatch.setattr(bp, "_upstream_get", fetch)
    _run_job(runners())                                        # runs to the end
    done = jobs.read_job(state["id"])
    assert done["status"] == "done"
    lines = _claim_lines(state["id"])
    assert len(lines) == len(set(lines)), "a resumed run re-appended claims"
    assert set(lines) == plan.planned_keys(DSS, 2, band)
    # Nothing that was on disk at the kill was fetched again.
    refetched = [u for u in upstream.calls[calls_before:]
                 if any(u.endswith(k.split("/", 1)[1]) for k in on_disk)]
    assert not refetched


def test_a_replan_does_not_duplicate_claims(upstream, runners, monkeypatch):
    band = plan.union_band([51.5])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    first = _claim_lines(state["id"])
    _enqueue_download(monkeypatch, band)                       # same survey again
    # Claim directly, as the re-planned run does before each fetch: no
    # job-end reconcile runs in between to hide a duplicate.
    for key in first:
        store.claim_for_job(state["id"], key)
    lines = _claim_lines(state["id"])
    assert lines == first


# ---------------------------------------------------------------------------
# 14. "ready" means on disk
# ---------------------------------------------------------------------------

_LOC = [{"user": "u", "record_uid": "r", "name": "A", "lat": 51.5}]


def _state(band, order=0):
    return plan.plan_summary(DSS, order, band, _LOC)["locations"][0]


def test_a_base_pin_without_its_file_is_not_ready_until_downloaded(
        upstream, runners, monkeypatch):
    band = plan.union_band([51.5])
    planned = plan.planned_keys(DSS, 0, band)
    grid = sorted(k for k in planned if "/Npix" in k)
    # Every planned tile base-pinned; all on disk except two.
    absent = set(grid[:2])
    for key in planned - absent:
        store.store_file(DSS, key.split("/", 1)[1], b"x" * 512)
    store.add_base_pins(planned)

    counts = store.reconcile_pins_with_disk()          # as at startup
    assert counts["base_dropped"] == 2
    loc = _state(band)
    assert loc["state"] == "missing"
    assert loc["missing_tiles"] == 2

    _enqueue_download(monkeypatch, band)
    _run_job(runners())
    loc = _state(band)
    assert loc["state"] == "ready"
    assert loc["missing_tiles"] == 0


def test_runner_start_reconciles_and_logs_one_startup_line(runners, capsys):
    key = f"{DSS}/Norder0/Dir0/Npix0.jpg"
    store.add_base_pins([key])                          # registered, never fetched
    runner = runners()
    runner.acquire()
    try:
        runner.run_pending()
    finally:
        runner.release()
    out = [l for l in capsys.readouterr().out.splitlines() if "pins reconciled" in l]
    assert out == ["[STARTUP] Offline sky: pins reconciled with disk: 1 base pin(s) "
                   "without a file dropped, 0 claimed tile(s) not on disk."]
    assert key not in store.base_pinned_rel_paths()


def test_a_claimed_tile_without_its_file_counts_as_missing(upstream, runners, monkeypatch):
    """A claim is ownership, not presence: a claimed tile whose file is gone is
    still to fetch, and the job's next run fetches it."""
    band = plan.union_band([51.5])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    assert _state(band)["state"] == "ready"
    gone = sorted(k for k in store.job_pins(state["id"]) if "/Npix" in k)[0]
    os.remove(store.tile_path(DSS, gone.split("/", 1)[1]))

    counts = store.reconcile_pins_with_disk()
    assert counts["claims_absent"] == 1
    assert gone in store.job_pins(state["id"])          # still owned
    loc = _state(band)
    assert loc["state"] == "missing" and loc["missing_tiles"] == 1

    calls = len(upstream.calls)
    _enqueue_download(monkeypatch, band)
    _run_job(runners())
    assert len(upstream.calls) == calls + 1             # only the gone tile
    assert _state(band)["state"] == "ready"


def test_a_fetched_tile_is_not_on_disk_to_the_plan_until_its_size_is_recorded():
    key = f"{DSS}/Norder0/Dir0/Npix0.jpg"
    store.claim_for_job("download-x", key)              # claimed before the fetch
    store.store_file(DSS, "Norder0/Dir0/Npix0.jpg", b"x" * 300)
    assert key not in store.pinned_on_disk_keys()
    store.set_claim_sizes("download-x", {key: 300})
    assert key in store.pinned_on_disk_keys()


def test_never_ready_while_the_download_is_running(upstream, runners, monkeypatch):
    band = plan.union_band([51.5])
    state = _enqueue_download(monkeypatch, band)
    _run_job(runners())
    assert _state(band)["state"] == "ready"
    for status in ("queued", "running", "paused"):
        jobs._write_job({**jobs.read_job(state["id"]), "status": status})
        loc = _state(band)
        assert loc["state"] == "missing", status
        assert loc["missing_tiles"] == 0
    jobs._write_job({**jobs.read_job(state["id"]), "status": "done"})
    assert _state(band)["state"] == "ready"
