"""Offline-sky saves (piece B1): frame area, rig-driven detail, estimate, limits,
the single job runner, pacing, honesty and accounting. No network.

Upstream is ``nova.blueprints.offline_sky._upstream_get``, replaced by a fake;
the breaker is forced closed; the disk is reported as roomy unless a test says
otherwise. Jobs run through a test-owned ``JobRunner`` (``run_pending``), never
the process-wide background thread (``jobs.kick`` is stubbed).

Survey facts for DSS2 colour and NSNS [OIII] are the real ``properties`` and
``Moc.fits`` files (tests/fixtures/offline_sky/), so the LDN1295 numbers below are
what a real save would cost.
"""

import os
import subprocess
import sys
import textwrap
from math import atan, degrees

import pytest

import nova.blueprints.offline_sky as bp
import nova.hips_healpix as hp
import nova.offline_sky_facts as facts
import nova.offline_sky_jobs as jobs
import nova.offline_sky_store as store

DSS = "dss2-color"
OIII = "nsns-oiii8"
DSS_ID = "P/DSS2/color"
OIII_ID = "https://www.simg.de/nebulae3/dr0_2/oiii8"
SURVEY_UPSTREAM_OIII = "https://www.simg.de/nebulae3/dr0_2/oiii8"
GIB = 1024 ** 3
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "offline_sky")

# LDN1295 with an Askar 50P-class 190 mm lens + ASI2600MC Pro (23.5 x 15.7 mm, 3.76 um).
LDN_RA, LDN_DEC = 11.2, 52.43
LDN_W_ARCMIN, LDN_H_ARCMIN = 425.0, 284.0


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


def _ldn_frame():
    return jobs.validate_frame(LDN_RA, LDN_DEC, LDN_W_ARCMIN / 60, LDN_H_ARCMIN / 60)


def _small_frame(w=1.0, h=0.7):
    return jobs.validate_frame(LDN_RA, LDN_DEC, w, h)


def _make_rig(db, *, pixel_um=3.76, image_scale_col=None, name="Askar 50P + ASI2600MC Pro"):
    from nova.models import Component, DbUser, Rig
    from nova.record_links import sync_rig_links
    user = db.query(DbUser).filter_by(username="default").one()
    tel = Component(user_id=user.id, kind="telescope", name="Askar 50P",
                    aperture_mm=50, focal_length_mm=190)
    cam = Component(user_id=user.id, kind="camera", name="ASI2600MC Pro",
                    sensor_width_mm=23.5, sensor_height_mm=15.7, pixel_size_um=pixel_um)
    db.add_all([tel, cam])
    db.flush()
    rig = Rig(user_id=user.id, rig_name=name, telescope_id=tel.id, camera_id=cam.id,
              image_scale=image_scale_col)
    db.add(rig)
    sync_rig_links(db, rig)
    db.commit()
    return rig.id


def _default_uid(db):
    from nova.models import DbUser
    return db.query(DbUser).filter_by(username="default").one().id


def _save_on(monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", True)


def _query(rig_id, surveys, frame=None, offset=0):
    f = frame or {"ra": LDN_RA, "dec": LDN_DEC,
                  "frame_w_deg": LDN_W_ARCMIN / 60, "frame_h_deg": LDN_H_ARCMIN / 60}
    return (f"ra={f['ra']}&dec={f['dec']}&frame_w_deg={f['frame_w_deg']}"
            f"&frame_h_deg={f['frame_h_deg']}&rig_id={rig_id}&detail_offset={offset}"
            f"&surveys={','.join(surveys)}")


def _enqueue_save(save_id, frame, order, slugs, user_id=1):
    store.write_save_manifest({"id": save_id, "user_id": user_id, "name": "T",
                               "surveys": list(slugs), "paths": [], "bytes": 0,
                               "status": "queued", "created": "2026-01-01T00:00:00Z"})
    return jobs.enqueue(save_id, "save", {"ra": frame["ra"], "dec": frame["dec"],
                                          "radius_deg": frame["radius_deg"], "order": order},
                        slugs)


def _plan_rels(slug, frame, order):
    plan = jobs.plan_survey(slug, frame["ra"], frame["dec"], frame["radius_deg"], order, fetch=False)
    return list(plan.rel_paths())


# ---------------------------------------------------------------------------
# 1. Area = the frame
# ---------------------------------------------------------------------------

def test_radius_is_half_diagonal_with_margin():
    f = _ldn_frame()
    w, h = LDN_W_ARCMIN / 60, LDN_H_ARCMIN / 60
    assert f["radius_deg"] == pytest.approx((w * w + h * h) ** 0.5 / 2 * 1.15)
    assert 4.8 < f["radius_deg"] < 5.0


def test_radius_limit_rejected(monkeypatch, client):
    _save_on(monkeypatch)
    resp = client.get("/api/offline/estimate?" + _query(
        1, [DSS_ID], frame={"ra": 10, "dec": 50, "frame_w_deg": 15, "frame_h_deg": 10}))
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "radius_too_large"
    assert body["max_radius_deg"] == jobs.MAX_SAVE_RADIUS_DEG


# ---------------------------------------------------------------------------
# 2. Detail from the rig
# ---------------------------------------------------------------------------

def test_hips_pixel_scale_and_target_order():
    assert jobs.hips_pixel_scale_arcsec(0) == pytest.approx(412.24, rel=1e-4)
    assert jobs.hips_pixel_scale_arcsec(9) == pytest.approx(0.805, rel=1e-3)
    assert jobs.target_order_for_scale(4.08) == 7      # 3.22"/px is the first <= 4.08
    assert jobs.target_order_for_scale(3.221) == 7     # order 7 = 3.2206"/px
    assert jobs.target_order_for_scale(3.22) == 8
    assert jobs.target_order_for_scale(500) == 0


def test_rig_image_scale_from_components_then_stored(client, db_session, monkeypatch):
    from nova import app
    from nova.models import DbUser
    rid = _make_rig(db_session)
    rid_no_px = _make_rig(db_session, pixel_um=None, image_scale_col=2.5, name="stored")
    uid = db_session.query(DbUser).filter_by(username="default").one().id
    with app.test_request_context():
        scale, source = bp._rig_image_scale(uid, rid)
        assert source == "components"
        assert scale == pytest.approx(206.265 * 3.76 / 190)       # 4.08"/px
        scale2, source2 = bp._rig_image_scale(uid, rid_no_px)
        assert (scale2, source2) == (2.5, "stored")
        with pytest.raises(jobs.LimitError) as err:
            bp._rig_image_scale(uid + 999, rid)                   # another user's rig
        assert err.value.code == "rig_not_found"


def test_ldn1295_fov_matches_rig():
    w = degrees(2 * atan(11.75 / 190)) * 60
    h = degrees(2 * atan(7.85 / 190)) * 60
    assert w == pytest.approx(LDN_W_ARCMIN, abs=1)
    assert h == pytest.approx(LDN_H_ARCMIN, abs=1)


# ---------------------------------------------------------------------------
# 3. Estimate -- the LDN1295 case (reported numbers)
# ---------------------------------------------------------------------------

def test_ldn1295_estimate(client, db_session, monkeypatch, capsys):
    _save_on(monkeypatch)
    _install_real_facts(DSS, OIII)
    rid = _make_rig(db_session)
    resp = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID, OIII_ID]))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()

    assert d["image_scale"]["source"] == "components"
    assert d["target_order"] == 7 and d["requested_order"] == 7
    by = {s["slug"]: s for s in d["surveys"]}
    assert by[DSS]["orders"] == [4, 5, 6, 7] and not by[DSS]["capped"]
    assert by[OIII]["orders"] == [4, 5, 6] and by[OIII]["capped"]      # hips_order = 6
    assert by[OIII]["effective_max_order"] == 6
    assert d["total"]["tile_count"] == by[DSS]["tile_count"] + by[OIII]["tile_count"]
    assert 100 < d["total"]["tile_count"] < 5000                        # thousands at most
    assert d["fits"] is True and d["reject"] is None

    with capsys.disabled():
        print("\nLDN1295  RA 11.2  Dec +52.43  frame 425'x284'  radius "
              f"{d['radius_deg']:.3f} deg  scale {d['image_scale_arcsec']:.3f}\"/px  "
              f"target order {d['target_order']}")
        for s in d["surveys"]:
            per = "  ".join(f"N{o}={v['tiles']}" for o, v in s["by_order"].items())
            print(f"  {s['slug']:<11} orders {s['orders']}  {per}  total {s['tile_count']}  "
                  f"~{s['est_bytes'] / 1e6:.1f} MB @ {s['avg_tile_bytes'] // 1024} KiB/tile "
                  f"({'measured' if s['avg_tile_measured'] else 'fallback'})  "
                  f"coverage {s['coverage']['reason']}")
        print(f"  TOTAL {d['total']['tile_count']} tiles  ~{d['total']['est_bytes'] / 1e6:.1f} MB")


def test_ids_and_slugs_give_same_estimate(client, db_session, monkeypatch):
    _save_on(monkeypatch)
    _install_real_facts(DSS, OIII)
    rid = _make_rig(db_session)
    a = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID, OIII_ID])).get_json()
    b = client.get("/api/offline/estimate?" + _query(rid, [DSS, OIII])).get_json()
    c = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID, DSS, OIII])).get_json()
    for x in (b, c):
        assert [s["slug"] for s in x["surveys"]] == [DSS, OIII]
        assert x["total"] == a["total"]
        assert [s["by_order"] for s in x["surveys"]] == [s["by_order"] for s in a["surveys"]]


def test_estimate_excludes_tiles_on_disk_and_uses_measured_size(client, db_session, monkeypatch):
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    frame = _ldn_frame()
    rels = _plan_rels(DSS, frame, 7)
    for rel in rels[:10]:
        store.store_file(DSS, rel, b"y" * 20000)
    d = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID])).get_json()
    s = d["surveys"][0]
    assert s["tiles_total"] == len(rels)
    assert s["tile_count"] == len(rels) - 10
    assert s["avg_tile_measured"] and s["avg_tile_bytes"] == 20000
    assert s["est_bytes"] == (len(rels) - 10) * 20000
    assert s["adopt_bytes"] == 10 * 20000          # browsed tiles that would become pinned
    for key in ("free_under_cap_bytes", "disk_free_bytes", "cap_bytes", "free_bytes"):
        assert key in d


def test_detail_offset(client, db_session, monkeypatch):
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    lo = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID], offset=-1)).get_json()
    hi = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID], offset=1)).get_json()
    assert lo["surveys"][0]["orders"] == [4, 5, 6]
    assert hi["surveys"][0]["orders"] == [4, 5, 6, 7, 8]
    bad = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID], offset=2))
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# 2b. NSNS: capped at its max order and limited to its coverage
# ---------------------------------------------------------------------------

def test_nsns_capped_and_limited_to_coverage():
    frame = _small_frame(2.0, 1.5)
    centre4 = hp.cone_pixels(frame["ra"], frame["dec"], 0.01, 4, 4)[4][0]
    store.store_file(OIII, "properties", _props(6, "png"), count=False)
    store.store_file(OIII, "Moc.fits", _moc_bytes([(4, centre4)]), count=False)
    facts.clear_memo()

    plan = jobs.plan_survey(OIII, frame["ra"], frame["dec"], frame["radius_deg"], 8, fetch=False)
    assert plan.capped and plan.orders == [4, 5, 6]
    assert plan.ext == "png"                                   # SurveyFacts.tile_ext
    cone = hp.cone_pixels(frame["ra"], frame["dec"], frame["radius_deg"], 4, 6)
    for order in (4, 5, 6):
        kept = plan.tiles[order]
        assert kept and len(kept) < len(cone[order])           # some cone tiles dropped
        for n in kept:
            assert n >> (2 * (order - 4)) == centre4           # all inside the MOC cell
    assert all(rel.endswith(".png") for rel in plan.rel_paths())


# ---------------------------------------------------------------------------
# 4. Limits before start
# ---------------------------------------------------------------------------

def _post_save(client, rid, surveys=(DSS_ID,), frame=None, name="LDN1295", **over):
    f = frame or {"ra": LDN_RA, "dec": LDN_DEC,
                  "frame_w_deg": LDN_W_ARCMIN / 60, "frame_h_deg": LDN_H_ARCMIN / 60}
    return client.post("/api/offline/saves", json={
        "name": name, **f, "rig_id": rid, "surveys": list(surveys), "detail_offset": 0, **over})


def test_save_rejected_over_cap(client, db_session, monkeypatch):
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    rid = _make_rig(db_session)
    resp = _post_save(client, rid)
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "over_cap"
    assert jobs.list_jobs() == [] and store.list_save_manifests() == []


def test_save_rejected_below_disk_headroom(client, db_session, monkeypatch):
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    monkeypatch.setattr(store, "disk_free_bytes", lambda: jobs.DISK_HEADROOM_BYTES + 1024 * 1024)
    rid = _make_rig(db_session)
    resp = _post_save(client, rid)
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "disk_headroom"
    assert jobs.list_jobs() == []


def test_browsed_tiles_do_not_block_a_save(client, db_session, monkeypatch):
    """Browsed tiles are evictable: only pinned + new counts against the cap."""
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 200)
    store.adjust_browsed_total(190 * 1024 * 1024)   # 190 MiB browsed
    rid = _make_rig(db_session)
    resp = _post_save(client, rid)
    assert resp.status_code == 200, resp.get_json()


def test_save_queues_job_and_kicks_runner(client, db_session, monkeypatch, _env):
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    resp = _post_save(client, rid)
    assert resp.status_code == 200
    body = resp.get_json()
    job = jobs.read_job(body["job_id"])
    assert job["status"] == "queued" and job["kind"] == "save"
    assert job["params"]["order"] == 7 and job["surveys"] == [DSS]
    m = store.read_save_manifest(body["job_id"])
    assert m["frame_w_deg"] == pytest.approx(LDN_W_ARCMIN / 60) and m["status"] == "queued"
    assert _env == [1]                                          # kick() called once


# ---------------------------------------------------------------------------
# Running a save: manifest, accounting, statuses
# ---------------------------------------------------------------------------

def test_save_job_runs_to_done(upstream, runners):
    frame = _small_frame()
    job = _enqueue_save("s1", frame, 6, [DSS])
    r = runners()
    assert r.acquire()
    assert r.run_pending() == 1
    state = jobs.read_job("s1")
    rels = _plan_rels(DSS, frame, 6)
    assert len(rels) >= 10                                # a real plan, not 0 == 0
    assert state["status"] == "done" and state["missing"] == 0
    assert state["done"] == state["total"] == len(rels) == state["fetched"]
    # One request per tile, to the first mirror, for exactly the planned tiles.
    primary = jobs.SURVEY_BY_SLUG[DSS].upstreams[0].rstrip("/")
    assert upstream.calls == [f"{primary}/{rel}" for rel in rels]
    for rel in rels:                                      # every tile really on disk
        assert store.read_file(DSS, rel) == b"x" * 1000
    m = store.read_save_manifest("s1")
    assert m["paths"] == sorted(f"{DSS}/{r_}" for r_ in rels)
    assert m["bytes"] == len(rels) * 1000 and m["partial"] is False and m["status"] == "done"
    assert store.pinned_bytes() == len(rels) * 1000
    assert store.total_bytes() == 0                       # fetched tiles are pinned, not browsed
    assert store.job_pins("s1") == set()                  # claims handed to the manifest
    assert not os.path.exists(jobs._cancel_path("s1"))


def test_manifest_lists_present_and_new_and_adopts_browsed(upstream, runners):
    frame = _small_frame()
    rels = _plan_rels(DSS, frame, 6)
    for rel in rels[:5]:
        store.store_file(DSS, rel, b"b" * 3000)            # browsed via the proxy
    assert store.total_bytes() == 5 * 3000
    _enqueue_save("s2", frame, 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    m = store.read_save_manifest("s2")
    assert len(m["paths"]) == len(rels)                     # present + fetched
    assert m["bytes"] == 5 * 3000 + (len(rels) - 5) * 1000  # from disk
    assert len(upstream.calls) == len(rels) - 5
    assert store.total_bytes() == 0                         # adopted tiles left .total
    assert store.pinned_bytes() == m["bytes"]

    # Deleting the save hands the tiles back to "browsed".
    store.delete_save_manifest("s2")
    assert store.total_bytes() == m["bytes"]
    assert store.pinned_bytes() == 0


def test_totals_with_overlapping_saves(upstream, runners):
    frame = _small_frame()
    _enqueue_save("a", frame, 5, [DSS])
    _enqueue_save("b", frame, 6, [DSS])     # superset of a's tiles
    r = runners()
    r.acquire()
    r.run_pending()
    b = store.read_save_manifest("b")
    a = store.read_save_manifest("a")
    assert store.pinned_bytes() == b["bytes"]
    store.delete_save_manifest("b")
    # a's tiles stay pinned; only b's extra tiles become browsed.
    assert store.pinned_bytes() == a["bytes"]
    assert store.total_bytes() == b["bytes"] - a["bytes"]
    assert store.evict_browsed()["total"] == b["bytes"] - a["bytes"]   # matches a full recount


def test_manifest_written_before_final_status(upstream, runners, monkeypatch):
    events = []
    real_manifest = store.write_save_manifest
    real_job = jobs._write_job

    def spy_manifest(m):
        real_manifest(m)
        events.append(("manifest", m["status"]))

    def spy_job(state):
        if state["status"] == "done":
            on_disk = store.read_save_manifest(state["id"])
            events.append(("status-done", on_disk["status"], on_disk["bytes"]))
        real_job(state)

    monkeypatch.setattr(store, "write_save_manifest", spy_manifest)
    monkeypatch.setattr(jobs, "_write_job", spy_job)
    _enqueue_save("s3", _small_frame(), 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    final_manifest = max(i for i, e in enumerate(events) if e == ("manifest", "done"))
    done_status = [i for i, e in enumerate(events) if e[0] == "status-done"]
    assert done_status and final_manifest < done_status[0]
    _, status_on_disk, bytes_on_disk = events[done_status[0]]
    assert status_on_disk == "done" and bytes_on_disk > 0    # no 0.0 MB race


def test_missing_tiles_are_counted(upstream, runners):
    frame = _small_frame()
    rels = _plan_rels(DSS, frame, 6)
    gone = {rels[3], rels[7]}
    upstream.script = lambda url: ((bp.UPSTREAM_MISSING, None)
                                   if any(url.endswith(g) for g in gone) else None)
    _enqueue_save("s4", frame, 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job("s4")
    m = store.read_save_manifest("s4")
    assert state["status"] == "done" and state["missing"] == 2
    assert state["done"] == len(rels)
    assert m["missing"] == 2 and m["missing_paths"] == sorted(f"{DSS}/{g}" for g in gone)
    assert len(m["paths"]) == len(rels) - 2


def test_missing_on_one_mirror_but_present_on_another_is_fetched(upstream, runners):
    frame = _small_frame()
    upstream.script = lambda url: ((bp.UPSTREAM_MISSING, None)
                                   if "alasky.cds" in url and "alaskybis" not in url else None)
    _enqueue_save("s5", frame, 5, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    assert jobs.read_job("s5")["missing"] == 0


# ---------------------------------------------------------------------------
# 6. Pause on breaker/busy; rate limit
# ---------------------------------------------------------------------------

def test_breaker_pause_does_not_mark_tile_done(upstream, runners):
    frame = _small_frame()
    rels = _plan_rels(DSS, frame, 5)
    target = rels[2]
    state = {"fails": 4}
    seen_during_pause = []

    def script(url):
        if url.endswith(target) and state["fails"] > 0:
            state["fails"] -= 1
            return bp.UPSTREAM_UNAVAILABLE, None
        return None

    def sleep(_s):
        js = jobs.read_job("p1")
        seen_during_pause.append((js["status"], js["done"]))

    upstream.script = script
    _enqueue_save("p1", frame, 5, [DSS])
    r = runners(sleep=sleep, pause_seconds=1.0)
    r.acquire()
    r.run_pending()
    # DSS has 4 upstreams: one pass of 4 "unavailable" answers -> one pause.
    # While paused the tile is not counted: done stays at the 2 tiles before it.
    assert seen_during_pause and all(x == ("paused", 2) for x in seen_during_pause)
    final = jobs.read_job("p1")
    assert final["status"] == "done" and final["missing"] == 0
    assert final["done"] == len(rels)
    assert f"{DSS}/{target}" in store.read_save_manifest("p1")["paths"]


def test_long_outage_fails_without_counting_tiles(upstream, runners):
    frame = _small_frame()
    upstream.script = lambda url: (bp.UPSTREAM_UNAVAILABLE, None)
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s

    _enqueue_save("p2", frame, 5, [DSS])
    r = runners(clock=clock, sleep=sleep, pause_seconds=60.0, max_pause_seconds=300.0)
    r.acquire()
    r.run_pending()
    state = jobs.read_job("p2")
    assert state["status"] == "failed" and state["reason"] == jobs.REASON_UPSTREAM
    assert state["done"] == 0 and state["fetched"] == 0
    m = store.read_save_manifest("p2")
    assert m["paths"] == [] and m["partial"] is True and m["status"] == "failed"


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
    frame = _small_frame()
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
    _enqueue_save("r1", frame, 5, [DSS])
    r = runners(rate=4.0, clock=clock, sleep=sleep)
    r.acquire()
    r.run_pending()
    assert len(stamps) > 8
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.25 - 1e-9
    assert (stamps[-1] - stamps[0]) == pytest.approx(0.25 * (len(stamps) - 1))


def test_jobs_do_not_use_the_proxy_semaphore(monkeypatch, runners):
    """The job path calls _upstream_get directly, never _fetch_upstream."""
    monkeypatch.setattr(bp, "_fetch_upstream",
                        lambda *a: pytest.fail("job used the browsing proxy's semaphore"))
    monkeypatch.setattr(bp._session, "get", lambda url, **kw: type(
        "R", (), {"status_code": 200, "content": b"z" * 10})())
    _enqueue_save("r2", _small_frame(), 5, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    assert jobs.read_job("r2")["status"] == "done"


# ---------------------------------------------------------------------------
# 4b. Space running out mid-job
# ---------------------------------------------------------------------------

def test_cap_reached_mid_job_stops_with_partial_manifest(upstream, runners, monkeypatch):
    upstream.size = 100 * 1024
    frame = _small_frame()
    rels = _plan_rels(DSS, frame, 6)
    assert len(rels) > 12
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)   # ~10 tiles
    _enqueue_save("c1", frame, 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job("c1")
    m = store.read_save_manifest("c1")
    assert state["status"] == "stopped" and state["reason"] == jobs.REASON_NO_SPACE
    assert 0 < len(m["paths"]) < len(rels)
    assert m["partial"] is True and m["status"] == "stopped"
    assert m["bytes"] == len(m["paths"]) * 100 * 1024 <= 1024 * 1024
    # Stopped for space, but its job still has the params a re-run needs: it
    # keeps what it got pinned, so the re-run resumes instead of re-fetching.
    assert store.pinned_bytes() == m["bytes"]


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
    _enqueue_save("c2", _small_frame(), 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job("c2")
    assert state["status"] == "stopped" and state["reason"] == jobs.REASON_NO_SPACE
    assert free[0] >= jobs.DISK_HEADROOM_BYTES
    assert len(store.read_save_manifest("c2")["paths"]) == state["fetched"]


# ---------------------------------------------------------------------------
# 5. One owner per job: lock, cancel from elsewhere, resume after a crash
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
    _enqueue_save("o1", _small_frame(), 5, [DSS])  # ...but can queue them
    assert b.run_pending() == 0
    assert jobs.read_job("o1")["status"] == "queued"
    assert a.run_pending() == 1
    assert jobs.read_job("o1")["status"] == "done"


def test_cancel_from_another_process(upstream, runners):
    frame = _small_frame()
    calls = [0]

    def script(url):
        calls[0] += 1
        if calls[0] == 3:
            assert jobs.cancel_job("x1")          # what the other worker's route does
        return None

    upstream.script = script
    _enqueue_save("x1", frame, 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job("x1")
    m = store.read_save_manifest("x1")
    assert state["status"] == "cancelled"
    assert len(m["paths"]) == 3 and m["partial"] is True and m["status"] == "cancelled"
    assert not os.path.exists(jobs._cancel_path("x1"))
    assert not jobs.cancel_job("x1")                 # already final


def test_cancel_route_writes_marker_only(client, db_session, monkeypatch, upstream):
    _save_on(monkeypatch)
    _enqueue_save("aaaaaaaa-0000-0000-0000-000000000001", _small_frame(), 5, [DSS],
                  user_id=_default_uid(db_session))
    resp = client.post("/api/offline/saves/aaaaaaaa-0000-0000-0000-000000000001/cancel")
    assert resp.get_json() == {"cancelled": True}
    assert os.path.exists(jobs._cancel_path("aaaaaaaa-0000-0000-0000-000000000001"))
    assert jobs.read_job("aaaaaaaa-0000-0000-0000-000000000001")["status"] == "queued"  # runner's call
    assert upstream.calls == []


class _Crash(BaseException):
    pass


def test_resume_after_crash(upstream, runners):
    frame = _small_frame(3.0, 2.0)
    rels = _plan_rels(DSS, frame, 7)
    assert len(rels) > 80
    calls = [0]

    def script(url):
        calls[0] += 1
        if calls[0] == 70:                     # after one checkpoint (64)
            raise _Crash()
        return None

    upstream.script = script
    browsed = "Norder9/Dir0/Npix1.jpg"                   # unrelated, outside the plan
    store.store_file(DSS, browsed, b"b" * 5000)
    assert store.total_bytes() == 5000
    _enqueue_save("k1", frame, 7, [DSS])
    a = runners()
    a.acquire()
    with pytest.raises(_Crash):
        a.run_pending()
    a.release()                                 # the process died; the OS drops its lock
    state = jobs.read_job("k1")
    assert state["status"] == "running"
    assert len(store.read_save_manifest("k1")["paths"]) == 64   # checkpointed

    first_pass = set(upstream.calls[:69])
    upstream.script = None
    upstream.calls.clear()
    b = runners()
    assert b.acquire()
    assert b.run_pending() == 1
    final = jobs.read_job("k1")
    assert final["status"] == "done"
    assert not (set(upstream.calls) & first_pass)          # nothing fetched twice
    m = store.read_save_manifest("k1")
    assert len(m["paths"]) == len(rels)
    # The 5 tiles fetched after the checkpoint and before the crash were never in
    # .total: the resumed job must not subtract them (it used to, leaving 0).
    assert store.total_bytes() == 5000
    assert store.pinned_bytes() == m["bytes"] == len(rels) * 1000
    assert store.evict_browsed()["total"] == 5000           # matches a full recount


def test_delete_while_running_does_not_resurrect_manifest(upstream, runners):
    frame = _small_frame()
    calls = [0]

    def script(url):
        calls[0] += 1
        if calls[0] == 2:
            jobs.cancel_job("d1")
            store.delete_save_manifest("d1")
        return None

    upstream.script = script
    _enqueue_save("d1", frame, 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    assert store.read_save_manifest("d1") is None
    # The save is gone: no job state, cancel marker or claims file is left behind.
    assert jobs.read_job("d1") is None
    assert not os.path.exists(jobs._cancel_path("d1"))
    assert store.job_pins("d1") == set()
    assert store.pinned_bytes() == 0
    assert store.total_bytes() == 2 * 1000      # reconciled: the 2 fetched tiles are browsed now


def test_progress_route_kicks_runner_for_active_job(client, db_session, monkeypatch, _env):
    _save_on(monkeypatch)
    sid = "aaaaaaaa-0000-0000-0000-000000000002"
    _enqueue_save(sid, _small_frame(), 5, [DSS], user_id=_default_uid(db_session))
    resp = client.get(f"/api/offline/saves/{sid}/progress")
    assert resp.status_code == 200
    assert resp.get_json()["job"]["status"] == "queued"
    assert _env == [1]


# ---------------------------------------------------------------------------
# 8. Base layer job
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


def _run_base_job(runners, slug):
    r = runners()
    r.acquire()
    r.run_pending()
    r.release()
    return jobs.read_job(jobs.base_job_id(slug))


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


def test_save_one_mirror_404_others_breaker_open_is_not_missing(upstream, runners, monkeypatch):
    """A save tile with a 404 from one mirror and every other mirror skipped
    (breaker open) pauses like unavailable; after the outage limit the job
    fails without recording the tile as missing."""
    frame = _small_frame()
    rels = _plan_rels(DSS, frame, 5)
    target = rels[1]
    primary = jobs.SURVEY_BY_SLUG[DSS].upstreams[0]
    monkeypatch.setattr(bp, "_breaker_open", lambda base: base != primary)
    upstream.script = lambda url: (bp.UPSTREAM_MISSING, None) if url.endswith(target) else None
    t = [0.0]

    def sleep(s):
        t[0] += s

    _enqueue_save("m1", frame, 5, [DSS])
    r = runners(clock=lambda: t[0], sleep=sleep, pause_seconds=60.0, max_pause_seconds=300.0)
    r.acquire()
    r.run_pending()
    state = jobs.read_job("m1")
    assert state["status"] == "failed" and state["reason"] == jobs.REASON_UPSTREAM
    assert state["missing"] == 0 and state["done"] == 1
    m = store.read_save_manifest("m1")
    assert m["missing"] == 0 and m["missing_paths"] == []
    assert len(m["paths"]) == 1


def test_missing_record_is_never_evicted(monkeypatch):
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    jobs._write_missing(OIII, {"Norder3/Dir0/Npix1.png": 1.0})
    for i in range(4):
        store.store_file(OIII, f"Norder5/Dir0/Npix{i}.png", b"t" * 400 * 1024)
    store.evict_browsed()
    assert jobs.read_missing(OIII) == {"Norder3/Dir0/Npix1.png": 1.0}


# ---------------------------------------------------------------------------
# Review fixes: immediate pins, delete race, error finish, reservation, misc
# ---------------------------------------------------------------------------

def test_fetched_tiles_are_pinned_before_the_first_checkpoint(upstream, runners, monkeypatch):
    """An eviction pass while a job runs (well before tile 64) must not delete
    or count as browsed any tile the job has already fetched."""
    frame = _small_frame()
    rels = _plan_rels(DSS, frame, 6)
    assert 6 < len(rels) < jobs.CHECKPOINT_EVERY
    calls = [0]
    seen = {}

    def script(url):
        calls[0] += 1
        if calls[0] == 6:                       # 5 tiles fetched, no checkpoint yet
            with monkeypatch.context() as mp:
                mp.setattr(store, "cap_bytes", lambda: 1)   # evict every unpinned tile
                seen.update(store.evict_browsed())
        return None

    upstream.script = script
    _enqueue_save("e1", frame, 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    assert seen["removed"] == 0 and seen["total"] == 0 and seen["pinned"] == 5 * 1000
    for rel in rels:
        assert store.read_file(DSS, rel) is not None
    m = store.read_save_manifest("e1")
    assert m["bytes"] == len(rels) * 1000
    assert store.total_bytes() == 0 and store.pinned_bytes() == m["bytes"]


def test_delete_during_manifest_write_does_not_resurrect(upstream, runners, monkeypatch):
    """A DELETE that lands while the runner is writing the manifest waits for
    the write and then removes it; the save never comes back."""
    import threading
    import time as _time
    real_write = store.write_save_manifest
    deleter = []

    def slow_write(manifest):
        if not deleter:
            t = threading.Thread(target=store.delete_save_manifest, args=(manifest["id"],))
            deleter.append(t)
            t.start()
            _time.sleep(0.3)                     # unlocked, the delete would land here
        real_write(manifest)

    _enqueue_save("w1", _small_frame(), 5, [DSS])
    monkeypatch.setattr(store, "write_save_manifest", slow_write)   # the runner's writes
    r = runners()
    r.acquire()
    r.run_pending()
    deleter[0].join(timeout=10)
    assert store.read_save_manifest("w1") is None
    assert store.pinned_bytes() == 0
    fetched = jobs.read_job("w1")["fetched"] * 1000
    assert fetched > 0 and store.total_bytes() == fetched      # released to browsed
    assert store.evict_browsed() == {"removed": 0, "bytes": 0, "total": fetched, "pinned": 0}


def test_unexpected_error_runs_the_finish_path(upstream, runners, monkeypatch):
    """A crash inside the job (here: the store raising after writing tile 3)
    still ends with a failed job, a failed partial manifest, no cancel marker,
    no claims file and totals that match the disk."""
    real_store = store.store_file
    n = [0]

    def store_then_raise(slug, rel, data, count=True):
        wrote = real_store(slug, rel, data, count=count)
        n[0] += 1
        if n[0] == 3:
            jobs.cancel_job("f1")                # a marker left lying around
            raise RuntimeError("disk on fire")
        return wrote

    monkeypatch.setattr(store, "store_file", store_then_raise)
    _enqueue_save("f1", _small_frame(), 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    state = jobs.read_job("f1")
    assert state["status"] == "failed" and state["reason"] == "error"
    assert "disk on fire" in state["error"]
    m = store.read_save_manifest("f1")
    assert m["status"] == "failed" and m["partial"] is True and len(m["paths"]) == 2
    assert not os.path.exists(jobs._cancel_path("f1"))
    assert store.job_pins("f1") == set()
    # Tile 3 is on disk but in no manifest: it is browsed now, and counted so.
    # The two tiles the failed save got are pinned still -- its job resumed-able.
    assert store.pinned_bytes() == m["bytes"] == 2 * 1000
    assert store.total_bytes() == 1000


def test_error_before_any_tile_still_marks_manifest_failed(upstream, runners, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("bad plan")

    monkeypatch.setattr(jobs, "plan_survey", boom)
    _enqueue_save("f2", _small_frame(), 6, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    assert jobs.read_job("f2")["status"] == "failed"
    m = store.read_save_manifest("f2")
    assert m["status"] == "failed" and m["partial"] is True


def test_cap_check_counts_queued_saves(client, db_session, monkeypatch):
    """A second save is refused when the first one's (queued) estimate already
    takes the room under the cap."""
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    est = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID])).get_json()
    need = est["total"]["est_bytes"] + est["total"]["adopt_bytes"]
    assert need > 2 * 1024 * 1024
    mib = (need * 3 // 2) // (1024 * 1024) + 1             # room for 1.5 saves
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", mib)
    first = _post_save(client, rid)
    assert first.status_code == 200, first.get_json()
    second = _post_save(client, rid)
    assert second.status_code == 409
    assert second.get_json()["code"] == "over_cap"
    assert len(jobs.list_jobs()) == 1 and len(store.list_save_manifests()) == 1
    # The estimate reports the reservation too.
    again = client.get("/api/offline/estimate?" + _query(rid, [DSS_ID])).get_json()
    assert again["reserved_bytes"] == need and again["fits"] is False


def test_admit_save_is_atomic_across_concurrent_creates(monkeypatch):
    import threading
    import time as _time
    monkeypatch.setattr(store, "cap_bytes", lambda: 10 * 1024 * 1024)
    real_free = store.disk_free_bytes

    def slow_free():
        _time.sleep(0.1)                         # widen the check-then-write window
        return real_free()

    monkeypatch.setattr(store, "disk_free_bytes", slow_free)
    results = {}
    barrier = threading.Barrier(2)

    def create(sid):
        manifest = {"id": sid, "user_id": 1, "paths": [], "status": "queued"}
        params = {"ra": 0, "dec": 0, "radius_deg": 1, "order": 6,
                  "reserve_bytes": 6 * 1024 * 1024, "reserve_disk_bytes": 6 * 1024 * 1024}
        barrier.wait()
        results[sid] = jobs.admit_save(manifest, params, [DSS], 6 * 1024 * 1024, 6 * 1024 * 1024)

    threads = [threading.Thread(target=create, args=(sid,)) for sid in ("c-a", "c-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(v is None for v in results.values()) == [False, True]
    assert len(jobs.list_jobs()) == 1 and len(store.list_save_manifests()) == 1


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
    with caplog.at_level(logging.WARNING, logger="nova.config"):
        found = cfg.warn_removed_offline_sky_keys()
    assert found == ["OFFLINE_SKY_JOB_CONCURRENCY"]
    assert "OFFLINE_SKY_JOB_CONCURRENCY" in caplog.text
    assert "OFFLINE_SKY_JOB_RATE_PER_SEC" in caplog.text
    monkeypatch.delenv("OFFLINE_SKY_JOB_CONCURRENCY")
    assert cfg.warn_removed_offline_sky_keys() == []


def test_stale_cancel_markers_and_deleted_save_jobs_are_swept(upstream, runners):
    _enqueue_save("z1", _small_frame(), 5, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()
    assert jobs.read_job("z1")["status"] == "done"
    # A cancel that raced the end of the job, and a marker with no job at all.
    for jid in ("z1", "nojob"):
        with open(jobs._cancel_path(jid), "w") as fh:
            fh.write("1")
    # A finished save job whose save was deleted by a route that saw it active.
    os.remove(os.path.join(store.pins_dir(), "z1.json"))
    r.run_pending()
    assert not os.path.exists(jobs._cancel_path("z1"))
    assert not os.path.exists(jobs._cancel_path("nojob"))
    assert jobs.read_job("z1") is None


def test_delete_route_removes_job_files_of_finished_save(client, db_session, monkeypatch,
                                                         upstream, runners):
    _save_on(monkeypatch)
    sid = "aaaaaaaa-0000-0000-0000-000000000003"
    _enqueue_save(sid, _small_frame(), 5, [DSS], user_id=_default_uid(db_session))
    r = runners()
    r.acquire()
    r.run_pending()
    with open(jobs._cancel_path(sid), "w") as fh:
        fh.write("1")                           # stale marker
    resp = client.delete(f"/api/offline/saves/{sid}")
    assert resp.get_json() == {"deleted": True}
    assert jobs.read_job(sid) is None and not os.path.exists(jobs._cancel_path(sid))
    assert store.read_save_manifest(sid) is None and store.pinned_bytes() == 0


# ---------------------------------------------------------------------------
# 7. Eviction never deletes metadata
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
# 9. Piece A follow-ups: effective max order in proxy validation and JS map
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
    assert m[DSS_ID]["maxOrder"] == 9            # no properties cached: whitelist


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
    assert resolve_slug(DSS_ID) == resolve_slug(DSS) == DSS
    assert resolve_slug("nope") is None and resolve_slug("") is None
    assert resolve_slugs(f"{DSS_ID},{DSS},{OIII_ID},x") == [DSS, OIII]


# ---------------------------------------------------------------------------
# 10. JS: one Cancel handler; the client sends the frame, not the view
# ---------------------------------------------------------------------------

def _js():
    with open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                           "graph_view_chart.js"), encoding="utf-8") as fh:
        return fh.read()


def test_js_single_cancel_handler():
    # Since B2 the panel lives in offline_save_panel.js: one Cancel button per
    # saved row with one click handler; the chart has no cancel code left.
    src = _js()
    assert "cancelOfflineSave" not in src
    with open(os.path.join(os.path.dirname(__file__), "..", "static", "js",
                           "offline_save_panel.js"), encoding="utf-8") as fh:
        panel = fh.read()
    assert panel.count("cancel.addEventListener('click', function () { cancelSave(s.id); });") == 1
    assert panel.count("/cancel'") == 1


def test_js_sends_frame_and_rig_not_view():
    src = _js()
    start = src.index("function offlineSaveFrame()")
    body = src[start:src.index("function setOfflineView(")]
    assert "getFov" not in body                     # nothing about the Aladin view size
    assert "frame_w_deg" in body and "rig_id" in body
    assert "radius_deg" not in src[src.index("// OFFLINE SKY: offline view toggle"):]


# ---------------------------------------------------------------------------
# Pin bookkeeping: what the runner sweeps and reconciles (item 9)
# ---------------------------------------------------------------------------

def test_a_pins_file_with_no_job_state_is_swept(upstream, runners):
    """A crash between the claim and its state file leaves a claims file that
    pins tiles no save covers and nothing would ever release."""
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
    store.write_save_manifest({"id": "live", "user_id": 1, "name": "T", "surveys": [DSS],
                               "paths": [], "bytes": 0, "status": "done",
                               "created": "2026-01-01T00:00:00Z"})
    store.write_json_atomic(jobs._job_path("live"),
                            {"id": "live", "kind": "save", "status": "done", "params": {},
                             "surveys": [DSS], "total": 0, "done": 0, "bytes": 0})
    key = "dss2-color/Norder3/Dir0/Npix1.jpg"
    store.claim_for_job("live", key)
    r = runners()
    r.acquire()
    r.run_pending()
    assert os.path.exists(jobs._job_path("live"))
    assert key in store.all_job_pins()


def test_the_runner_reconciles_pins_once_at_start(upstream, runners, monkeypatch):
    """Totals on disk are re-derived from the manifests at the runner's first
    pass, so an upgrade that un-pins a cancelled save's tiles is reflected."""
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


def test_a_cancelled_save_pins_nothing_but_keeps_its_tiles(upstream, runners):
    """The LDN1295 case end to end: tiles it fetched stay on disk, unpinned."""
    upstream.size = 1000
    frame = _small_frame()
    _enqueue_save("k1", frame, 6, [DSS])
    r = runners()
    r.acquire()

    real_store = store.store_file
    n = [0]

    def store_then_cancel(slug, rel, data, count=True):
        wrote = real_store(slug, rel, data, count=count)
        n[0] += 1
        if n[0] == 3:
            jobs.cancel_job("k1")
        return wrote

    import unittest.mock as _mock
    with _mock.patch.object(store, "store_file", store_then_cancel):
        r.run_pending()
    state = jobs.read_job("k1")
    assert state["status"] == "cancelled"
    m = store.read_save_manifest("k1")
    assert len(m["paths"]) >= 2
    assert store.pinned_bytes() == 0                   # covers nothing
    assert store.total_bytes() == len(m["paths"]) * 1000
    for key in m["paths"]:
        assert store.pinned_key_size(key) is not None   # nothing deleted


# ---------------------------------------------------------------------------
# Items 2-5: one entry per (user, rig, object), and the explicit no-op refusal
# ---------------------------------------------------------------------------

def test_saving_the_same_object_for_the_same_rig_merges(client, db_session, monkeypatch):
    """Item 3: a second save adds its surveys to the entry, not a new row."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    first = _post_save(client, rid, [DSS_ID])
    assert first.status_code == 200, first.get_json()
    entry_id = first.get_json()["manifest"]["id"]

    second = _post_save(client, rid, [DSS_ID, OIII_ID])
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["manifest"]["id"] == entry_id, "merged into the same entry"

    manifest = store.read_save_manifest(entry_id)
    assert manifest is not None
    # the merged entry covers both surveys and names both saves
    assert set(manifest["surveys"]) >= {DSS, OIII}
    assert len(manifest.get("merged_saves") or []) == 2

    # and the list shows one row, not two
    saves = client.get("/api/offline/saves?name=LDN1295").get_json()["saves"]
    assert len(saves) == 1


def test_a_save_with_nothing_new_is_refused(client, db_session, monkeypatch, upstream, runners):
    """Item 2, server side: no new survey and no missing tile is a 4xx, not a
    silent no-op merge. Nothing is written and no job is created."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    assert _post_save(client, rid, [DSS_ID]).status_code == 200
    r = runners()
    r.acquire()
    r.run_pending()                             # the first save finishes
    assert store.read_save_manifest(store.list_save_manifests()[0][0])["status"] == "done"
    before = len(jobs.list_jobs())

    again = _post_save(client, rid, [DSS_ID])
    assert again.status_code == 409
    assert again.get_json()["code"] == "nothing_new"
    assert len(jobs.list_jobs()) == before, "no job created"
    assert len(store.list_save_manifests()) == 1, "no second manifest"


def test_a_save_adding_a_survey_is_not_refused(client, db_session, monkeypatch):
    """The refusal is only for a genuine no-op: a new survey is real work."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    _install_real_facts(OIII)
    rid = _make_rig(db_session)
    assert _post_save(client, rid, [DSS_ID]).status_code == 200
    assert _post_save(client, rid, [DSS_ID, OIII_ID]).status_code == 200


def test_the_saved_list_is_filtered_by_object_and_counts_the_others(client, db_session, monkeypatch):
    """Item 4: only this object's saves, plus how many belong elsewhere."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    _post_save(client, rid, [DSS_ID], name="LDN1295")
    _post_save(client, rid, [DSS_ID], name="M106", ra=184.6, dec=47.3)

    only = client.get("/api/offline/saves?name=LDN1295").get_json()
    assert [s["name"] for s in only["saves"]] == ["LDN1295"]
    assert only["others"] == 1

    everything = client.get("/api/offline/saves").get_json()
    assert len(everything["saves"]) == 2 and everything["others"] == 0


def test_a_save_records_the_rig_it_was_made_for(client, db_session, monkeypatch):
    """Item 5: the entry carries rig_id and rig_name, so a row can say which rig."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    resp = _post_save(client, rid, [DSS_ID])
    assert resp.status_code == 200
    manifest = resp.get_json()["manifest"]
    assert manifest["rig_id"] == rid
    assert manifest["rig_name"] == "Askar 50P + ASI2600MC Pro"
    assert manifest["save_ids"], "the entry names its saves"


def test_different_rigs_do_not_merge(client, db_session, monkeypatch):
    """Two rigs, same object: two entries, so each can report its own detail."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    rid1 = _make_rig(db_session, name="Rig one")
    rid2 = _make_rig(db_session, name="Rig two", pixel_um=2.4)
    assert _post_save(client, rid1, [DSS_ID]).status_code == 200
    assert _post_save(client, rid2, [DSS_ID]).status_code == 200
    saves = client.get("/api/offline/saves").get_json()["saves"]
    assert len(saves) == 2
    assert {s["rig_name"] for s in saves} == {"Rig one", "Rig two"}


def test_a_running_job_does_not_overwrite_another_saves_tiles(upstream, runners, _env):
    """The merge is a union: two jobs on one entry keep both sets of tiles."""
    upstream.size = 1000
    frame = _small_frame()
    entry = {"id": "E1", "user_id": 1, "name": "T", "surveys": [DSS],
             "paths": ["dss2-color/Norder3/Dir0/Npix777.jpg"], "bytes": 1000,
             "created": "2026-01-01T00:00:00Z", "status": "queued", "partial": True,
             "merged_saves": ["j1", "j2"]}
    store.write_save_manifest(entry)
    store.store_file(DSS, "Norder3/Dir0/Npix777.jpg", b"x" * 1000)
    for jid in ("j1", "j2"):
        store.write_save_manifest({"id": jid, "user_id": 1, "name": "T", "surveys": [DSS],
                                   "entry_id": "E1", "paths": [], "bytes": 0,
                                   "created": "2026-01-01T00:00:00Z", "status": "queued"})
        jobs.enqueue(jid, "save", {"ra": frame["ra"], "dec": frame["dec"],
                                   "radius_deg": frame["radius_deg"], "order": 6}, [DSS])
    r = runners()
    r.acquire()
    r.run_pending()

    merged = store.read_save_manifest("E1")
    assert "dss2-color/Norder3/Dir0/Npix777.jpg" in merged["paths"], \
        "the tile the entry already had survived both jobs"
    assert len(merged["paths"]) > 1
    # both jobs finished; the entry's own status is the last job's to write
    assert jobs.read_job("j1")["status"] == "done" and jobs.read_job("j2")["status"] == "done"


def test_a_deleted_entry_stops_its_jobs(upstream, runners, _env):
    """Deleting the entry is the same as deleting a save: the job stops rather
    than recreating what the user removed."""
    upstream.size = 1000
    frame = _small_frame()
    store.write_save_manifest({"id": "E2", "user_id": 1, "name": "T", "surveys": [DSS],
                               "paths": [], "bytes": 0, "created": "2026-01-01T00:00:00Z",
                               "status": "queued", "partial": True, "merged_saves": ["j3"]})
    store.write_save_manifest({"id": "j3", "user_id": 1, "name": "T", "surveys": [DSS],
                               "entry_id": "E2", "paths": [], "bytes": 0,
                               "created": "2026-01-01T00:00:00Z", "status": "queued"})
    jobs.enqueue("j3", "save", {"ra": frame["ra"], "dec": frame["dec"],
                                "radius_deg": frame["radius_deg"], "order": 6}, [DSS])
    assert store.read_save_manifest("E2")["merged_saves"] == ["j3"]

    r = runners()
    r.acquire()
    store.delete_save_manifest("E2")        # the user removes the entry
    r.run_pending()
    # The job stops as deleted: its state and claims are dropped, and the entry
    # is not recreated by the run.
    assert jobs.read_job("j3") is None
    assert store.job_pins("j3") == set()
    assert store.read_save_manifest("E2") is None
    # the run stops at the guard (the entry is gone), and its own file records
    # that; its tiles are pinned by nothing and no run recreates the entry
    assert store.read_save_manifest("j3")["status"] == "cancelled"
    assert store.pinned_bytes() == 0
    assert store.read_save_manifest("E2") is None


def test_a_legacy_entry_merges_only_on_name_and_identical_geometry(client, db_session, monkeypatch):
    """The M106 pair, as it exists on disk: two rig-less entries for one object
    and one frame become a single row, automatically, with both surveys. A
    rig-less entry for a different frame keeps its own row."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    uid = store_read_uid(db_session)

    def legacy(save_id, ra, dec, w, h, surveys):
        store.write_save_manifest({
            "id": save_id, "user_id": uid, "name": "M106",
            "ra": ra, "dec": dec, "radius_deg": 1.0,
            "frame_w_deg": w, "frame_h_deg": h,
            "surveys": list(surveys),
            "max_order": 11, "created": "2026-11-17T10:00:00Z",
            "paths": [], "bytes": 0, "status": "done"})

    legacy("L1", 184.6194, 47.3437, 0.6870, 0.4589, ["dss2-color", "nsns-halpha8"])
    legacy("L2", 184.6194, 47.3437, 0.6870, 0.4589, ["dss2-color", "nsns-rgb8"])
    legacy("L3", 184.6194, 47.3437, 1.2000, 0.8000, ["dss2-color"])   # another frame

    # The fold happens on the runner's first pass (once per process); the list
    # is what the user then sees.
    r = jobs.JobRunner(rate=1000, limiter=_NoLimit(), sleep=lambda s: None)
    r.acquire()
    r.run_pending()
    r.release()

    rows = client.get("/api/offline/saves?name=M106").get_json()["saves"]
    assert len(rows) == 2, "the pair is one entry; the odd frame is another"
    merged = [r for r in rows if len(r["surveys"]) > 1][0]
    assert set(merged["surveys"]) == {DSS, "nsns-halpha8", "nsns-rgb8"}, "surveys unioned"
    assert merged["rig_id"] is None and merged["rig_name"] is None, \
        "a rig-less entry shows no rig and claims no match"
    assert set(merged["save_ids"]) == {"L1", "L2"}


def test_a_rig_tagged_save_never_merges_into_a_legacy_entry(client, db_session, monkeypatch):
    """The rig-less entry is superseded, not absorbed: a save that names a rig
    gets its own entry, so its match claim is never wrong."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    store.write_save_manifest({
        "id": "L9", "user_id": store_read_uid(db_session), "name": "M106",
        "ra": LDN_RA, "dec": LDN_DEC, "radius_deg": 1.0, "surveys": ["dss2-color"],
        "max_order": 11, "created": "2026-11-17T10:00:00Z",
        "paths": [], "bytes": 0, "status": "done"})
    resp = _post_save(client, rid, [DSS_ID], name="M106")
    assert resp.status_code == 200
    assert resp.get_json()["manifest"]["rig_id"] == rid
    rows = client.get("/api/offline/saves?name=M106").get_json()["saves"]
    assert len(rows) == 2, "the legacy entry keeps its own row (superseded, not merged)"


def _store_read_uid(db):
    from nova.models import DbUser
    return db.query(DbUser).filter_by(username="default").one().id


store_read_uid = _store_read_uid


def test_the_progress_row_shows_each_saves_job_status(client, db_session, monkeypatch):
    """The row is the entry; the status is its least-advanced save. Merging must
    not leave a row showing "Waiting" while a job is actually running."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _install_real_facts(DSS)
    _install_real_facts(OIII)
    rid = _make_rig(db_session)
    first = _post_save(client, rid, [DSS_ID])
    entry_id = first.get_json()["manifest"]["id"]
    second = _post_save(client, rid, [DSS_ID, OIII_ID])
    assert second.status_code == 200

    row = client.get(f"/api/offline/saves/{entry_id}/progress").get_json()
    assert row["manifest"]["id"] == entry_id
    assert row["manifest"]["status"] in ("queued", "running"), \
        "a queued job makes the entry that far along"
    assert row["job"] is not None, "the entry answers with one of its saves' jobs"

    listed = client.get("/api/offline/saves?name=LDN1295").get_json()["saves"]
    assert len(listed) == 1 and listed[0]["status"] in ("queued", "running")
