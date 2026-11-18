"""Offline-sky tile proxy and store.

The /hips/ routes proxy survey tiles from the whitelisted upstreams
(nova.offline_sky_sources.SURVEY_SOURCES) into CACHE_DIR/hips and back out to
Aladin. No network: upstream fetches are monkeypatched; the store writes into the
per-test cache dir from conftest.isolated_cache_dir.

Sky tiles are shared across users of the instance, so none of these paths carry
a username/user_id, and the /hips/ routes are public (the app's
load_global_request_context returns early for them).
"""

import json
import os
import time
import uuid

import pytest
import requests

import nova.offline_sky_jobs as jobs
from nova.offline_sky_store import (
    hips_root,
    survey_dir,
    store_file,
    read_file,
    total_bytes,
    evict_browsed,
)

DSS_COLOR = "dss2-color"


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


def _properties_text():
    return (
        "hips_order=9\n"
        "hips_tile_format=jpeg\n"
        "hips_frame=equatorial\n"
        "hips_service_url=https://alasky.cds.unistra.fr/DSS/DSSColor\n"
        "hips_service_url_1=https://skies.esac.esa.int/DSSColor\n"
        "hips_initial_fov=60\n"
    ).encode("utf-8")


def _server_on(monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")


def _save_on(monkeypatch):
    """Store on plus the step-2 "Save for offline" API (OFFLINE_SKY_SAVE_ENABLED)."""
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", True)


# ---------------------------------------------------------------------------
# Route gating and validation
# ---------------------------------------------------------------------------

def test_routes_404_when_store_off(client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "off")
    assert client.get("/hips/dss2-color/properties").status_code == 404
    assert client.get("/hips/dss2-color/Norder3/Dir0/Npix0.jpg").status_code == 404


def test_unknown_slug_404(client, monkeypatch):
    _server_on(monkeypatch)
    assert client.get("/hips/not-a-survey/properties").status_code == 404
    assert client.get("/hips/not-a-survey/Norder3/Dir0/Npix0.jpg").status_code == 404


def test_path_validation(client, monkeypatch):
    _server_on(monkeypatch)
    # order above the survey's max order (9)
    assert client.get("/hips/dss2-color/Norder10/Dir0/Npix0.jpg").status_code == 404
    # Dir must equal (Npix // 10000) * 10000
    assert client.get("/hips/dss2-color/Norder3/Dir1/Npix10000.jpg").status_code == 404
    # ext not in the allowlist (no whitelisted survey uses FITS)
    assert client.get("/hips/dss2-color/Norder3/Dir0/Npix0.fits").status_code == 404
    # a correct Dir/Npix pairing passes validation (fails later at fetch, 404)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", lambda base, url: None)
    assert client.get("/hips/dss2-color/Norder3/Dir10000/Npix12345.jpg").status_code == 404


def test_npix_upper_bound(client, monkeypatch):
    _server_on(monkeypatch)
    calls = []

    def fake(base, url):
        calls.append(url)
        return None

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake)
    # order 3 has 12*4^3 = 768 tiles, so Npix 768 is out of range -> 404, no fetch
    assert client.get("/hips/dss2-color/Norder3/Dir0/Npix768.jpg").status_code == 404
    assert calls == []
    # Npix 767 is the last valid tile at order 3; it reaches the (mocked) fetch
    assert client.get("/hips/dss2-color/Norder3/Dir0/Npix767.jpg").status_code == 404
    assert calls


# ---------------------------------------------------------------------------
# _strip_service_urls edge cases
# ---------------------------------------------------------------------------

def test_strip_service_urls_keeps_blank_and_comments():
    from nova.blueprints.offline_sky import _strip_service_urls
    raw = (
        "\n"
        "   \n"
        "# a comment\n"
        "hips_order = 9\n"
        "hips_service_url = https://a.example/h\n"
        "hips_service_url_1=https://b.example/h\n"
        "hips_service_url_2 = https://c.example/h\n"
        "hips_initial_fov=60\n"
    ).encode("utf-8")
    lines = _strip_service_urls(raw).decode("utf-8").splitlines()
    assert lines[0] == ""                      # blank line kept
    assert lines[1] == "   "                   # whitespace-only line kept
    assert "# a comment" in lines              # comment kept
    assert "hips_order = 9" in lines
    assert "hips_initial_fov=60" in lines
    assert not any(ln.split("=", 1)[0].strip().startswith("hips_service_url") for ln in lines)


# ---------------------------------------------------------------------------
# properties: strip, cache, stale-refresh, status codes
# ---------------------------------------------------------------------------

def test_properties_strips_service_urls_and_caches(client, monkeypatch):
    _server_on(monkeypatch)
    calls = []

    def fake_fetch(base, url):
        calls.append(url)
        return _properties_text()

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake_fetch)

    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.get_data(as_text=True)
    assert "hips_order=9" in body
    assert "hips_tile_format=jpeg" in body
    assert "hips_service_url" not in body          # stripped

    # First upstream (CDS primary) was asked; the stripped copy is on disk.
    assert calls[0] == "https://alasky.cds.unistra.fr/DSS/DSSColor/properties"
    on_disk = read_file(DSS_COLOR, "properties")
    assert b"hips_service_url" not in on_disk


def test_properties_503_when_upstream_down(client, monkeypatch):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", lambda base, url: None)
    assert client.get("/hips/dss2-color/properties").status_code == 503


def test_properties_fresh_served_without_upstream(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "properties", b"hips_order=9\n", count=False)

    def fail(base, url):
        raise AssertionError("fresh properties must not hit upstream")

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fail)
    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200
    assert resp.get_data() == b"hips_order=9\n"


def test_properties_refresh_when_stale(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "properties", b"hips_order=9\n", count=False)
    old = time.time() - 8 * 86400
    os.utime(os.path.join(survey_dir(DSS_COLOR), "properties"), (old, old))

    calls = []
    monkeypatch.setattr(
        "nova.blueprints.offline_sky._fetch_upstream",
        lambda base, url: calls.append(url) or b"hips_order=9\nhips_tile_format=jpeg\n",
    )
    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200
    assert calls                                   # stale -> upstream was tried
    assert "hips_tile_format=jpeg" in resp.get_data(as_text=True)


def test_properties_serve_stale_when_upstream_down(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "properties", b"hips_order=9\n", count=False)
    old = time.time() - 8 * 86400
    os.utime(os.path.join(survey_dir(DSS_COLOR), "properties"), (old, old))

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", lambda base, url: None)
    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200                   # stale copy served, not 503
    assert resp.get_data() == b"hips_order=9\n"


# ---------------------------------------------------------------------------
# MOC.fits passthrough (partial-sky coverage, optional metadata)
# ---------------------------------------------------------------------------

def test_moc_served_and_stored(client, monkeypatch):
    _server_on(monkeypatch)
    calls = []

    def fake(base, url):
        calls.append(url)
        return b"\x00SIMPLE  =                    T"  # minimal FITS-ish bytes

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake)
    resp = client.get("/hips/dss2-color/Moc.fits")
    assert resp.status_code == 200
    assert resp.mimetype == "application/fits"
    assert resp.get_data() == b"\x00SIMPLE  =                    T"
    assert read_file(DSS_COLOR, "Moc.fits") == b"\x00SIMPLE  =                    T"
    assert calls[0].endswith("/Moc.fits")


def test_moc_fresh_served_without_upstream(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "Moc.fits", b"\x00SIMPLE", count=False)

    def fail(base, url):
        raise AssertionError("fresh MOC must not hit upstream")

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fail)
    resp = client.get("/hips/dss2-color/Moc.fits")
    assert resp.status_code == 200
    assert resp.get_data() == b"\x00SIMPLE"


def test_moc_404_when_no_upstream(client, monkeypatch):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", lambda base, url: None)
    assert client.get("/hips/dss2-color/Moc.fits").status_code == 404


def test_moc_gated_by_allowed(monkeypatch, mu_client_logged_out):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream",
                        lambda base, url: (_ for _ in ()).throw(AssertionError("fetch called")))
    assert mu_client_logged_out.get("/hips/dss2-color/Moc.fits").status_code == 404


# ---------------------------------------------------------------------------
# tiles: disk-first, fail-over order, 404, headers, concurrency
# ---------------------------------------------------------------------------

def test_tile_served_from_disk_without_upstream(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "Norder3/Dir0/Npix0.jpg", b"FAKEJPEG")

    def fail(base, url):
        raise AssertionError("upstream must not be contacted when the tile is on disk")

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fail)

    resp = client.get("/hips/dss2-color/Norder3/Dir0/Npix0.jpg")
    assert resp.status_code == 200
    assert resp.get_data() == b"FAKEJPEG"
    assert resp.mimetype == "image/jpeg"
    assert resp.headers["Cache-Control"].startswith("public, max-age=31536000")


def test_tile_upstream_failover_order(client, monkeypatch):
    _server_on(monkeypatch)
    calls = []

    def fake_fetch(base, url):
        calls.append(url)
        if len(calls) == 1:
            return None                      # CDS primary fails
        return b"FROM-MIRROR"

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake_fetch)

    resp = client.get("/hips/dss2-color/Norder3/Dir0/Npix5.jpg")
    assert resp.status_code == 200
    assert resp.get_data() == b"FROM-MIRROR"
    assert "alasky.cds.unistra.fr" in calls[0]           # primary first
    assert "alaskybis.cds.unistra.fr" in calls[1]        # then the bis mirror
    assert read_file(DSS_COLOR, "Norder3/Dir0/Npix5.jpg") == b"FROM-MIRROR"


def test_tile_404_when_upstream_down(client, monkeypatch):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", lambda base, url: None)
    assert client.get("/hips/dss2-color/Norder3/Dir0/Npix5.jpg").status_code == 404


def test_breaker_skips_failed_base_and_retries_after_expiry(monkeypatch):
    from nova.blueprints import offline_sky as bp
    bp._breaker_until.clear()
    base = "https://alasky.cds.unistra.fr/DSS/DSSColor"

    def boom(url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(bp._session, "get", boom)
    assert bp._fetch_upstream(base, base + "/Norder3/Dir0/Npix0.jpg") is None
    assert bp._breaker_open(base) is True

    # while open, no socket attempt
    calls = []
    monkeypatch.setattr(bp._session, "get",
                        lambda url, **kw: calls.append(url) or _Resp(200, b"x"))
    assert bp._fetch_upstream(base, base + "/Norder3/Dir0/Npix1.jpg") is None
    assert calls == []

    # after the 60s window: tried again
    bp._breaker_until[base] = 0.0
    assert bp._fetch_upstream(base, base + "/Norder3/Dir0/Npix2.jpg") == b"x"
    assert calls == [base + "/Norder3/Dir0/Npix2.jpg"]


def test_breaker_trips_on_5xx(monkeypatch):
    from nova.blueprints import offline_sky as bp
    bp._breaker_until.clear()
    base = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    monkeypatch.setattr(bp._session, "get", lambda url, **kw: _Resp(503))
    assert bp._fetch_upstream(base, base + "/Norder3/Dir0/Npix0.jpg") is None
    assert bp._breaker_open(base) is True


# ---------------------------------------------------------------------------
# "Absent" upstreams: a /properties 404 skips that base for tiles, so a survey
# whose first upstream no longer hosts it costs one request per tile, not two.
# ---------------------------------------------------------------------------

def _reset_absent(monkeypatch):
    """The proxy's module-level upstream state, cleared for this test.

    The autouse isolated_upstream_state fixture already resets it around every test;
    this keeps the intent explicit at the top of each absence-mark test (and works
    when one of them is run on its own).
    """
    from nova.blueprints import offline_sky as bp
    monkeypatch.setattr(bp, "_absent_bases", set())
    monkeypatch.setattr(bp, "_breaker_until", {})
    return bp


def test_properties_404_marks_the_base_absent(monkeypatch):
    bp = _reset_absent(monkeypatch)
    base = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    monkeypatch.setattr(bp._session, "get", lambda url, **kw: _Resp(404))
    assert bp._fetch_upstream(base, base + "/properties") is None
    assert bp._base_absent(base) is True


def test_a_tile_404_does_not_mark_the_base_absent(monkeypatch):
    """One survey can legitimately have no tile at a Npix while its neighbours do
    (NSNS covers Dec >= -16 only), so a tile 404 must not disable the base."""
    bp = _reset_absent(monkeypatch)
    base = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    monkeypatch.setattr(bp._session, "get", lambda url, **kw: _Resp(404))
    assert bp._fetch_upstream(base, base + "/Norder3/Dir0/Npix5.jpg") is None
    assert bp._base_absent(base) is False


def test_absent_base_is_skipped_for_tiles_without_a_request(client, monkeypatch):
    """The measured symptom: while CDS serves its "Page unavailable" 404 for a
    survey, every tile used to cost a wasted CDS request plus a good mirror fetch.
    With the mark, the tile goes straight to the mirror."""
    bp = _reset_absent(monkeypatch)
    _server_on(monkeypatch)
    cds = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp(200, b"FROM-MIRROR")

    monkeypatch.setattr(bp._session, "get", fake_get)
    # CDS answered 404 for this survey's properties earlier in the session.
    bp._mark_absent(cds)

    resp = client.get("/hips/dss2-color/Norder3/Dir0/Npix5.jpg")
    assert resp.status_code == 200
    assert resp.get_data() == b"FROM-MIRROR"
    assert [u for u in calls if u.startswith(cds)] == [], \
        "the absent base is skipped entirely: no wasted 404 request first"
    assert calls, "the next upstream was asked and served"


def test_the_first_mirror_404ing_still_falls_through_to_cds(client, monkeypatch):
    """The requested case: the survey's *first* upstream answers 404 (a mirror that
    no longer hosts it) and CDS answers. The mirror is marked absent and CDS
    serves -- and the mark is per base, not per survey, so the other mirrors stay
    in play for later tiles."""
    import dataclasses
    bp = _reset_absent(monkeypatch)
    _server_on(monkeypatch)
    mirror = "https://skies.esac.esa.int/DSSColor"
    cds = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    # Put the mirror first, so the 404 comes from the first upstream in the list.
    src = bp.SURVEY_BY_SLUG["dss2-color"]
    monkeypatch.setitem(bp.SURVEY_BY_SLUG, "dss2-color",
                        dataclasses.replace(src, upstreams=(mirror, cds)))
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url.startswith(mirror):
            return _Resp(404)      # this mirror no longer hosts the survey
        return _Resp(200, b"FROM-CDS")

    monkeypatch.setattr(bp._session, "get", fake_get)
    bp._mark_absent(mirror)        # as its /properties 404 would have

    resp = client.get("/hips/dss2-color/Norder3/Dir0/Npix5.jpg")
    assert resp.status_code == 200
    assert resp.get_data() == b"FROM-CDS"
    assert [u for u in calls if u.startswith(mirror)] == [], \
        "CDS served the tile; the absent mirror was not asked"
    assert [u for u in calls if u.startswith(cds)], "CDS was asked and served"


def test_a_properties_200_clears_the_absent_mark(client, monkeypatch):
    """What makes CDS used again when it is back: the frontend asks /properties
    once per framing open, and a 200 there clears the mark. Goes through the real
    _upstream_get (via _session.get), which is where the marking lives."""
    bp = _reset_absent(monkeypatch)
    _server_on(monkeypatch)
    base = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    bp._mark_absent(base)
    assert bp._base_absent(base) is True

    monkeypatch.setattr(bp._session, "get",
                        lambda url, **kw: _Resp(200, b"hips_order=9\nhips_tile_format=jpeg\n"))
    assert client.get("/hips/dss2-color/properties").status_code == 200
    assert bp._base_absent(base) is False, "a 200 from the base means it is there again"


def test_fresh_properties_reprobes_while_a_base_is_absent(client, monkeypatch):
    """A fresh on-disk properties copy is normally served without touching upstream,
    which would leave a marked-absent base marked for ever. While any of the
    survey's upstreams is absent, /properties goes to the upstreams instead, so the
    mark can clear (this is the per-framing-open re-probe)."""
    bp = _reset_absent(monkeypatch)
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "properties", b"hips_order=9\n", count=False)
    base = "https://alasky.cds.unistra.fr/DSS/DSSColor"
    bp._mark_absent(base)

    calls = []
    monkeypatch.setattr(bp._session, "get", lambda url, **kw: calls.append(url) or _Resp(200, b"x"))
    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200
    assert calls, "a marked-absent upstream forces the re-probe despite a fresh cache"
    assert bp._base_absent(base) is False, "the 200 cleared the mark"


def test_fresh_properties_untouched_when_no_base_is_absent(client, monkeypatch):
    """The normal path is unchanged: nothing absent means the fresh cache is served
    with no upstream request at all."""
    bp = _reset_absent(monkeypatch)
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "properties", b"hips_order=9\n", count=False)

    def fail(url, **kw):
        raise AssertionError("fresh properties must not hit upstream")

    monkeypatch.setattr(bp._session, "get", fail)
    assert client.get("/hips/dss2-color/properties").status_code == 200


def test_saturated_semaphore_fails_fast(monkeypatch):
    from nova.blueprints import offline_sky as bp
    bp._breaker_until.clear()
    # Patch the acquire timeout down so the test doesn't wait 15s; exercise the
    # real semaphore by filling every slot first.
    monkeypatch.setattr(bp, "_FETCH_SEMAPHORE_TIMEOUT", 0.05)
    for _ in range(6):
        assert bp._FETCH_SEMAPHORE.acquire(blocking=False) is True
    calls = []
    monkeypatch.setattr(bp._session, "get",
                        lambda url, **kw: calls.append(url) or _Resp(200, b"x"))
    try:
        with pytest.raises(bp._UpstreamBusy):
            bp._fetch_upstream("https://alasky.cds.unistra.fr/DSS/DSSColor",
                               "https://alasky.cds.unistra.fr/DSS/DSSColor/Norder3/Dir0/Npix0.jpg")
        assert calls == []                      # never reached the socket
    finally:
        for _ in range(6):
            bp._FETCH_SEMAPHORE.release()


def test_disk_hit_never_touches_the_semaphore(client, monkeypatch):
    from nova.blueprints import offline_sky as bp
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "Norder3/Dir0/Npix0.jpg", b"FAKEJPEG")
    acquired = []
    monkeypatch.setattr(bp._FETCH_SEMAPHORE, "acquire",
                        lambda *a, **kw: acquired.append(True) or False)
    monkeypatch.setattr(bp, "_fetch_upstream",
                        lambda base, url: (_ for _ in ()).throw(AssertionError("fetch called")))
    resp = client.get("/hips/dss2-color/Norder3/Dir0/Npix0.jpg")
    assert resp.status_code == 200
    assert resp.get_data() == b"FAKEJPEG"
    assert acquired == []


# ---------------------------------------------------------------------------
# Store: eviction
# ---------------------------------------------------------------------------

def _store_n_tiles(n, size):
    for i in range(n):
        store_file(DSS_COLOR, f"Norder3/Dir0/Npix{i}.jpg", b"x" * size)
    return [f"Norder3/Dir0/Npix{i}.jpg" for i in range(n)]


def _age_tiles(rels, base_mtime):
    for i, rel in enumerate(rels):
        os.utime(os.path.join(survey_dir(DSS_COLOR), rel),
                 (base_mtime - 100 + i, base_mtime - 100 + i))


def test_eviction_oldest_first_under_cap(monkeypatch):
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)  # 1 MiB cap
    rels = _store_n_tiles(3, 400 * 1024)  # 3 * 400 KiB = 1.2 MiB > cap
    assert total_bytes() == 3 * 400 * 1024
    _age_tiles(rels, time.time())

    result = evict_browsed()
    assert result["removed"] == 1
    # Npix0 is oldest -> removed; Npix2 (newest) survives
    assert read_file(DSS_COLOR, rels[0]) is None
    assert read_file(DSS_COLOR, rels[2]) is not None
    assert total_bytes() <= 1 * 1024 * 1024


def test_eviction_respects_pin_manifest(monkeypatch):
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    # 4 x 400 KiB; pin the oldest, so the 3 remaining browsed tiles (1.2 MiB)
    # still exceed the 1 MiB cap and the oldest non-pinned one is removed.
    rels = _store_n_tiles(4, 400 * 1024)
    _age_tiles(rels, time.time())

    # A save manifest lists the oldest tile; eviction must skip it.
    from nova.offline_sky_store import write_save_manifest
    write_save_manifest({
        "id": "00000000-0000-0000-0000-000000000001", "user_id": 1, "name": "x",
        "ra": 0.0, "dec": 0.0, "radius_deg": 1.0, "surveys": [DSS_COLOR],
        "max_order": 3, "created": "2026-01-01T00:00:00Z",
        "paths": [f"{DSS_COLOR}/{rels[0]}"], "bytes": 0,
    })

    evict_browsed()
    assert read_file(DSS_COLOR, rels[0]) is not None   # pinned, kept
    assert read_file(DSS_COLOR, rels[1]) is None       # oldest non-pinned removed
    assert read_file(DSS_COLOR, rels[3]) is not None   # newest browsed tile kept


def test_eviction_noop_when_under_cap(monkeypatch):
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 8)
    rels = _store_n_tiles(2, 1000)
    result = evict_browsed()
    assert result["removed"] == 0
    assert read_file(DSS_COLOR, rels[0]) is not None


def test_eviction_reconciles_total(monkeypatch):
    """The evictor recomputes the true on-disk bytes and rewrites .total, so a
    drifted cross-process .total is corrected even when nothing is removed."""
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 8)
    rels = _store_n_tiles(2, 1000)
    # Simulate a cross-process lost update: .total says far too much.
    from nova.offline_sky_store import _set_total
    _set_total(total_bytes() + 999999)
    evict_browsed()                                    # no removal (under cap)
    assert total_bytes() == 2 * 1000                   # reconciled to true disk bytes
    assert read_file(DSS_COLOR, rels[0]) is not None


# ---------------------------------------------------------------------------
# Default off + access control
# ---------------------------------------------------------------------------

def test_default_store_off_404(client):
    # OFFLINE_SKY_STORE defaults to "off"; no _server_on here.
    assert client.get("/hips/dss2-color/properties").status_code == 404
    assert client.get("/hips/dss2-color/Norder3/Dir0/Npix0.jpg").status_code == 404


def test_multi_user_unauth_404_no_fetch(monkeypatch, mu_client_logged_out):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream",
                        lambda base, url: (_ for _ in ()).throw(AssertionError("fetch called")))
    resp = mu_client_logged_out.get("/hips/dss2-color/properties")
    assert resp.status_code == 404          # 404, not a redirect to login


def test_multi_user_guest_404(monkeypatch, mu_client_logged_out):
    _server_on(monkeypatch)
    with mu_client_logged_out.session_transaction() as sess:
        sess["guest_mode"] = True
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream",
                        lambda base, url: (_ for _ in ()).throw(AssertionError("fetch called")))
    assert mu_client_logged_out.get("/hips/dss2-color/properties").status_code == 404


def test_multi_user_auth_200(monkeypatch, multi_user_client):
    _server_on(monkeypatch)
    client, _ids = multi_user_client
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream",
                        lambda base, url: b"hips_order=9\nhips_tile_format=jpeg\n")
    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200
    assert "hips_tile_format=jpeg" in resp.get_data(as_text=True)


def test_single_user_enabled_200_without_login(monkeypatch, client):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream",
                        lambda base, url: b"hips_order=9\nhips_tile_format=jpeg\n")
    resp = client.get("/hips/dss2-color/properties")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Context processor gating (window.NOVA_OFFLINE_SKY flag)
# ---------------------------------------------------------------------------

def _render_offline_flag(client, monkeypatch, *, enabled=True, session_updates=None):
    import nova
    from flask import render_template_string, session
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: enabled)
    with client.application.test_request_context("/"):
        for k, v in (session_updates or {}).items():
            session[k] = v
        return render_template_string("{{ OFFLINE_SKY_ENABLED }}")


def test_inject_offline_sky_disabled(monkeypatch, client):
    assert _render_offline_flag(client, monkeypatch, enabled=False) == "False"


def test_inject_offline_sky_single_user(monkeypatch, client):
    # client is single-user (nova.SINGLE_USER_MODE=True): proxy open, flag on.
    assert _render_offline_flag(client, monkeypatch) == "True"


def test_inject_offline_sky_multi_user_unauth(monkeypatch, mu_client_logged_out):
    assert _render_offline_flag(mu_client_logged_out, monkeypatch) == "False"


def test_inject_offline_sky_multi_user_guest(monkeypatch, mu_client_logged_out):
    with mu_client_logged_out.session_transaction() as sess:
        sess["guest_mode"] = True
    assert _render_offline_flag(mu_client_logged_out, monkeypatch,
                                session_updates={"guest_mode": True}) == "False"


def test_inject_offline_sky_multi_user_auth(monkeypatch, multi_user_client):
    client, _ids = multi_user_client
    with client.session_transaction() as sess:
        uid = sess["_user_id"]
    assert _render_offline_flag(client, monkeypatch,
                                session_updates={"_user_id": uid}) == "True"


# ---------------------------------------------------------------------------
# Cache-only mode (/hips/offline/...)
# ---------------------------------------------------------------------------

def test_cache_only_tile_serves_disk_and_404s_without_fetch(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "Norder3/Dir0/Npix0.jpg", b"FAKEJPEG")

    def fail(base, url):
        raise AssertionError("cache-only must not hit upstream")

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fail)
    assert client.get("/hips/offline/dss2-color/Norder3/Dir0/Npix0.jpg").status_code == 200
    assert client.get("/hips/offline/dss2-color/Norder3/Dir0/Npix5.jpg").status_code == 404


def test_cache_only_properties_serves_stale_and_404s(client, monkeypatch):
    _server_on(monkeypatch)
    store_file(DSS_COLOR, "properties", b"hips_order=9\n", count=False)
    old = time.time() - 8 * 86400
    os.utime(os.path.join(survey_dir(DSS_COLOR), "properties"), (old, old))

    def fail(base, url):
        raise AssertionError("cache-only properties must not hit upstream")

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fail)
    # stale but present -> 200 from disk
    assert client.get("/hips/offline/dss2-color/properties").status_code == 200
    # absent -> 404, no upstream
    assert client.get("/hips/offline/allwise-color/properties").status_code == 404


# ---------------------------------------------------------------------------
# Jobs: see tests/test_offline_sky_jobs.py (planning, estimate, limits, runner).
# ---------------------------------------------------------------------------

def _manifest(**kw):
    base = {
        "id": str(uuid.uuid4()), "user_id": 1, "name": "M42",
        "ra": 83.8, "dec": -5.4, "radius_deg": 1.0,
        "surveys": [DSS_COLOR], "max_order": 5,
        "created": "2026-01-01T00:00:00Z", "paths": [], "bytes": 0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Save manifests: the legacy on-disk format, and the estimate's resolution
# (moved here from tests/test_offline_save_panel.py with the panel UI removal:
# the JSON these read and write is what the Configuration download page will
# consume, so the coverage stays).
# ---------------------------------------------------------------------------

LEGACY_ID = "3cd1e599-bded-4310-9983-d557a5d203eb"


def _uid(db_session):
    from nova.models import DbUser
    return db_session.query(DbUser).filter_by(username="default").one().id


def _write_legacy_save(uid, status="cancelled"):
    """A save exactly as the first step-2 version wrote it (the LDN1295 one)."""
    from nova import offline_sky_store as store
    store.write_save_manifest({
        "id": LEGACY_ID, "user_id": uid, "name": "LDN1295",
        "ra": 11.200000000000005, "dec": 52.43333333333334, "radius_deg": 21.564434664667715,
        "surveys": ["dss2-color", "nsns-oiii8"], "max_order": 8,
        "created": "2026-11-16T23:30:12Z",
        "paths": ["dss2-color/Norder4/Dir0/Npix1.jpg"], "bytes": 19583809, "status": status})
    os.makedirs(store.jobs_dir(), exist_ok=True)
    with open(os.path.join(store.jobs_dir(), f"{LEGACY_ID}.json"), "w") as fh:
        json.dump({"id": LEGACY_ID, "kind": "save", "surveys": ["dss2-color", "nsns-oiii8"],
                   "done": 364, "total": 364, "bytes": 19583809, "status": status,
                   "updated": 1794871901.743596}, fh)


def test_legacy_unfinished_save_is_hidden_but_still_deletable(monkeypatch, client, db_session):
    """A legacy save that never finished covers nothing and can never run again,
    so the list hides it. Its file stays, its summary is still readable, and it
    can still be deleted."""
    from nova import offline_sky_store as store
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _write_legacy_save(_uid(db_session))
    resp = client.get("/api/offline/saves")
    assert resp.status_code == 200
    assert resp.get_json()["saves"] == []            # hidden from the list

    # the summary is still there, and still says what it always said
    prog = client.get(f"/api/offline/saves/{LEGACY_ID}/progress")
    assert prog.status_code == 200
    s = prog.get_json()["manifest"]
    assert s["id"] == LEGACY_ID and s["legacy"] is True and s["legacy_unfinished"] is True
    assert s["status"] == "cancelled" and s["partial"] is True
    assert s["detail_offset"] is None
    assert s["survey_ids"] == ["P/DSS2/color", "https://www.simg.de/nebulae3/dr0_2/oiii8"]

    assert client.delete(f"/api/offline/saves/{LEGACY_ID}").status_code == 200
    assert store.read_save_manifest(LEGACY_ID) is None
    assert jobs.read_job(LEGACY_ID) is None
    assert client.get("/api/offline/saves").get_json()["saves"] == []


def test_legacy_done_save_is_still_listed(monkeypatch, client, db_session):
    """Only a legacy save that never finished is hidden: a finished one covers
    its tiles and stays in the list."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _write_legacy_save(_uid(db_session), status="done")
    (s,) = client.get("/api/offline/saves").get_json()["saves"]
    assert s["legacy"] is True and s["legacy_unfinished"] is False and s["status"] == "done"


def test_legacy_save_left_running_reads_as_failed_and_its_job_cannot_crash(
        monkeypatch, client, db_session):
    from nova import offline_sky_store as store
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _write_legacy_save(_uid(db_session), status="running")
    s = client.get(f"/api/offline/saves/{LEGACY_ID}/progress").get_json()["manifest"]
    assert s["status"] == "failed" and s["partial"] is True and s["legacy"] is True
    assert s["legacy_unfinished"] is True
    # Its job file has no params: whoever runs the queue marks it failed, no exception.
    jobs.JobRunner(rate=1000).run_job(LEGACY_ID)
    assert jobs.read_job(LEGACY_ID)["status"] == "failed"
    assert client.delete(f"/api/offline/saves/{LEGACY_ID}").status_code == 200
    assert store.read_save_manifest(LEGACY_ID) is None


def test_new_save_is_not_legacy(monkeypatch, client, db_session):
    from nova import offline_sky_store as store
    _save_on(monkeypatch)
    store.write_save_manifest({"id": "aaaaaaaa-0000-0000-0000-000000000001",
                               "user_id": _uid(db_session),
                               "name": "M42", "surveys": ["dss2-color"], "paths": [], "bytes": 0,
                               "status": "done", "partial": False, "detail_offset": 1,
                               "frame_w_deg": 1.0, "frame_h_deg": 0.7,
                               "created": "2026-11-17T00:00:00Z"})
    (s,) = client.get("/api/offline/saves").get_json()["saves"]
    assert s["legacy"] is False and s["partial"] is False and s["detail_offset"] == 1


def test_estimate_reports_the_requested_resolution(monkeypatch, client, db_session):
    from test_offline_sky_jobs import DSS, DSS_ID as JOBS_DSS_ID, _install_real_facts, _make_rig, _query
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    for off in (-1, 0, 1):
        d = client.get("/api/offline/estimate?" + _query(rid, [JOBS_DSS_ID], offset=off)).get_json()
        assert d["requested_pixel_scale_arcsec"] == pytest.approx(
            jobs.hips_pixel_scale_arcsec(d["requested_order"]))
        assert d["image_scale_arcsec"] > 0


# ---------------------------------------------------------------------------
# Pin ownership, eviction, status
# ---------------------------------------------------------------------------

def test_eviction_skips_base_pins(monkeypatch):
    from nova.offline_sky_store import add_base_pins
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    rels = _store_n_tiles(4, 400 * 1024)  # pin one -> 3 browsed tiles = 1.2 MiB > 1 MiB
    _age_tiles(rels, time.time())
    add_base_pins([f"{DSS_COLOR}/{rels[0]}"])
    evict_browsed()
    assert read_file(DSS_COLOR, rels[0]) is not None   # base-pinned, kept
    assert read_file(DSS_COLOR, rels[1]) is None       # oldest non-pinned removed
    assert read_file(DSS_COLOR, rels[3]) is not None   # newest non-pinned kept


def test_unpinned_tiles_become_evictable_after_delete(monkeypatch):
    from nova.offline_sky_store import write_save_manifest, delete_save_manifest
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 1)
    rels = _store_n_tiles(3, 400 * 1024)
    _age_tiles(rels, time.time())
    mid = "00000000-0000-0000-0000-0000000000ff"
    write_save_manifest(_manifest(id=mid, paths=[f"{DSS_COLOR}/{rels[0]}"]))
    evict_browsed()
    # One cap for the whole store: the pinned 400 KiB counts, so the oldest
    # browsed tile (rels[1]) went to make room; the pinned one stays.
    assert read_file(DSS_COLOR, rels[0]) is not None   # pinned by the save
    assert read_file(DSS_COLOR, rels[1]) is None
    delete_save_manifest(mid)
    assert total_bytes() == 2 * 400 * 1024              # rels[0] is browsed again
    store_file(DSS_COLOR, "Norder3/Dir0/Npix9.jpg", b"x" * 400 * 1024)  # newest
    evict_browsed()
    assert read_file(DSS_COLOR, rels[0]) is None       # now ordinary and evictable (oldest)


def test_cone_max_cached_order(monkeypatch):
    import nova.offline_sky_jobs as jobs
    monkeypatch.setattr(jobs, "base_complete", lambda slug: True)
    for order in (4, 5):
        plan = jobs.plan_survey(DSS_COLOR, 83.8, -5.4, 1.0, order, fetch=False)
        for rel in plan.rel_paths():
            store_file(DSS_COLOR, rel, b"X", count=False)
    assert jobs.cone_max_cached_order(DSS_COLOR, 83.8, -5.4, 1.0) == 5


# ---------------------------------------------------------------------------
# User API endpoints
# ---------------------------------------------------------------------------

def test_saves_are_owned_per_user(monkeypatch, multi_user_client):
    from nova.offline_sky_store import write_save_manifest, read_save_manifest
    _save_on(monkeypatch)
    client, ids = multi_user_client
    aid = "aaaaaaaa-0000-0000-0000-000000000001"
    bid = "bbbbbbbb-0000-0000-0000-000000000002"
    write_save_manifest(_manifest(id=aid, user_id=ids["user_a_id"]))
    write_save_manifest(_manifest(id=bid, user_id=ids["user_b_id"]))

    resp = client.get("/api/offline/saves")
    assert resp.status_code == 200
    assert [s["id"] for s in resp.get_json()["saves"]] == [aid]   # only A's saves

    assert client.get(f"/api/offline/saves/{bid}/progress").status_code == 404
    assert client.delete(f"/api/offline/saves/{bid}").status_code == 404
    assert read_save_manifest(bid) is not None                    # B's save untouched

    assert client.delete(f"/api/offline/saves/{aid}").status_code == 200
    assert read_save_manifest(aid) is None


def test_status_endpoint_reports_base_complete_and_pinned_order(monkeypatch, client):
    """The indicator's number is what is *pinned*, not what is merely on disk:
    browsing fills the same cache, and eviction may take any of it away."""
    _save_on(monkeypatch)
    import nova.offline_sky_jobs as jobs
    from nova.offline_sky_store import write_json_atomic, _base_pins_path
    monkeypatch.setattr(jobs, "base_complete", lambda slug: True)
    frame = jobs.validate_frame(83.8, -5.4, 1.0, 0.8)
    plan = jobs.plan_survey(DSS_COLOR, frame["ra"], frame["dec"], frame["radius_deg"], 4, fetch=False)
    rels = list(plan.rel_paths())
    for rel in rels:
        store_file(DSS_COLOR, rel, b"X", count=False)      # on disk, not pinned
    q = ("/api/offline/status?ra=83.8&dec=-5.4&frame_w_deg=1.0&frame_h_deg=0.8"
         "&surveys=P/DSS2/color")
    resp = client.get(q)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["surveys"][DSS_COLOR]["base_complete"] is True
    assert data["surveys"][DSS_COLOR]["max_pinned_order"] == -1, \
        "tiles that are only cached are not promised offline"

    # Pin the whole cone from order 0 up, which is what a base job plus a save
    # leaves behind, and the order is reported.
    facts = jobs.survey_facts(DSS_COLOR, fetch=False)
    cov = jobs.survey_coverage(DSS_COLOR, fetch=False)
    by = jobs.hp.cone_pixels(frame["ra"], frame["dec"], frame["radius_deg"], 0, 4)
    keys = {f"{DSS_COLOR}/{jobs._tile_rel(o, n, facts.tile_ext)}"
            for o in range(0, 5) for n in by.get(o, []) if cov.contains(o, n)}
    write_json_atomic(_base_pins_path(), {"paths": sorted(keys)})
    data2 = client.get(q).get_json()
    assert data2["surveys"][DSS_COLOR]["max_pinned_order"] == 4
    # Slugs are accepted too (one normaliser for every route), and say the same
    # thing as the id form for the same state.
    resp2 = client.get("/api/offline/status?ra=83.8&dec=-5.4&frame_w_deg=1.0&frame_h_deg=0.8"
                       "&surveys=dss2-color")
    assert resp2.get_json()["surveys"] == data2["surveys"]


# ---------------------------------------------------------------------------
# OFFLINE_SKY_SAVE_ENABLED off: save/base/status API and its UI are switched off,
# the /hips/ proxy, cache-only routes and Offline view toggle stay.
# ---------------------------------------------------------------------------

_SAVE_ID = "aaaaaaaa-0000-0000-0000-000000000001"


def test_api_routes_404_and_no_job_when_save_disabled(monkeypatch, client):
    import nova.offline_sky_jobs as jobs
    from nova.offline_sky_store import write_save_manifest
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", False)
    spawned = []
    monkeypatch.setattr(jobs, "kick", lambda: spawned.append(1))
    monkeypatch.setattr("nova.blueprints.offline_sky._upstream_get",
                        lambda base, url: pytest.fail("no upstream fetch expected"))
    # An existing save must not be reachable either.
    write_save_manifest(_manifest(id=_SAVE_ID, user_id=1))

    cone = "ra=83.8&dec=-5.4&frame_w_deg=1.0&frame_h_deg=0.8&rig_id=1"
    calls = [
        ("get", "/api/offline/saves", None),
        ("post", "/api/offline/saves", {"ra": 83.8, "dec": -5.4, "frame_w_deg": 1.0,
                                        "frame_h_deg": 0.8, "rig_id": 1,
                                        "surveys": ["P/DSS2/color"]}),
        ("get", f"/api/offline/saves/{_SAVE_ID}/progress", None),
        ("post", f"/api/offline/saves/{_SAVE_ID}/cancel", None),
        ("delete", f"/api/offline/saves/{_SAVE_ID}", None),
        ("post", "/api/offline/base/ensure", {"surveys": ["P/DSS2/color"]}),
        ("get", "/api/offline/base/status", None),
        ("get", f"/api/offline/estimate?{cone}&surveys=dss2-color", None),
        ("get", f"/api/offline/status?{cone}&surveys=P/DSS2/color", None),
    ]
    for method, url, body in calls:
        resp = getattr(client, method)(url, json=body) if body else getattr(client, method)(url)
        assert resp.status_code == 404, (method, url)
    assert spawned == []


def test_hips_proxy_unaffected_when_save_disabled(monkeypatch, client):
    _server_on(monkeypatch)
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", False)
    store_file(DSS_COLOR, "Norder3/Dir0/Npix1.jpg", b"TILE")
    assert client.get(f"/hips/{DSS_COLOR}/Norder3/Dir0/Npix1.jpg").data == b"TILE"
    assert client.get(f"/hips/offline/{DSS_COLOR}/Norder3/Dir0/Npix1.jpg").data == b"TILE"


def _graph_page(monkeypatch, client, save_enabled=False):
    import nova
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: True)
    monkeypatch.setattr(nova, "OFFLINE_SKY_SAVE_ENABLED", save_enabled)
    resp = client.get("/graph_dashboard/M42")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.mark.parametrize("save_enabled", [False, True])
def test_graph_page_has_no_offline_save_ui(monkeypatch, client, save_enabled):
    """The Framing tab carries no save-for-offline UI, whatever the save flag says.

    The panel, the button, the chip, the survey checklist, the saved list and the
    indicator slot are all gone from the side panel; only the Offline view switch
    stays, in the FRAMING header, and it is there because the proxy serves the sky
    -- not because anything can be saved from this page.
    """
    page = _graph_page(monkeypatch, client, save_enabled)
    for gone in ('id="save-offline-btn"', 'id="offline-save-panel"', 'id="offline-status-chip"',
                 'id="offline-sky-controls"', 'id="offline-survey-list"', 'id="offline-saves-list"',
                 'id="offline-save-start"', 'id="offline-detail-select"', 'id="offline-estimate-total"',
                 'id="framing-offline-slot"', 'id="offline-scan-note"',
                 'offline_save_panel.js', 'NOVA_OFFLINE_SKY_SAVE'):
        assert gone not in page, gone
    # Kept: the Offline view switch, and the proxy flag it is gated on.
    assert 'id="offline-view-toggle"' in page
    assert "window.NOVA_OFFLINE_SKY = true;" in page


def test_offline_save_ui_is_gone_from_the_frontend():
    """Source check: no page-side code reaches /api/offline/* any more.

    The backend keeps every route (tile store, job runner, pins, save routes)
    behind OFFLINE_SKY_SAVE_ENABLED for the Configuration download page; what must
    not come back is a Framing-tab code path that calls it. The offline *view*
    machinery -- the switch, the cache-only roots, the "not stored offline" notice
    -- is deliberately still there.
    """
    js = os.path.join(os.path.dirname(__file__), "..", "static", "js")
    assert not os.path.exists(os.path.join(js, "offline_save_panel.js")), \
        "the save panel module is deleted"
    chart = open(os.path.join(js, "graph_view_chart.js"), encoding="utf-8").read()
    for gone in ("/api/offline/", "offlineSave", "NovaOfflineSavePanel", "framing-offline-slot",
                 "OFFLINE_BASE_UI_ENABLED", "NOVA_OFFLINE_SKY_SAVE", "offline-scan-note"):
        assert gone not in chart, gone
    view = open(os.path.join(js, "graph_view.js"), encoding="utf-8").read()
    assert "refreshOfflineStatus" not in view
    # ...while the offline view wiring stays.
    assert "function setOfflineView(" in chart and "function wireOfflineSkyUi(" in chart
    assert "offlineHipsRoot" in chart


# ---------------------------------------------------------------------------
# What a save covers: paths and missing paths, and only while it is unfinished
# ---------------------------------------------------------------------------

def _coverage_manifest(save_id, status, paths, missing_paths=(), user_id=1):
    return {"id": save_id, "user_id": user_id, "name": "x", "ra": 0.0, "dec": 0.0,
            "radius_deg": 1.0, "surveys": [DSS_COLOR], "max_order": 3,
            "created": "2026-01-01T00:00:00Z", "bytes": 0,
            "paths": list(paths), "missing_paths": list(missing_paths), "status": status}


def test_a_done_save_covers_its_missing_tiles_too():
    """Tiles every mirror answered 404 for are part of what the save covers, so
    the next save does not ask for them all over again."""
    from nova.offline_sky_store import read_save_manifest, save_covers, write_save_manifest
    rels = _store_n_tiles(2, 1000)
    write_save_manifest(_coverage_manifest("00000000-0000-0000-0000-00000000000d", "done",
                                  [f"{DSS_COLOR}/{rels[0]}"],
                                  [f"{DSS_COLOR}/{rels[1]}"]))
    covers = save_covers(read_save_manifest("00000000-0000-0000-0000-00000000000d"))
    assert covers == {f"{DSS_COLOR}/{rels[0]}", f"{DSS_COLOR}/{rels[1]}"}


@pytest.mark.parametrize("status", ["cancelled", "stopped", "failed"])
def test_an_unfinished_save_covers_nothing(status):
    """Cancel it, or run out of space, and its tiles go back to the browsing
    budget -- they stay on disk, they are just no longer pinned."""
    from nova.offline_sky_store import read_save_manifest, save_covers, write_save_manifest
    write_save_manifest(_coverage_manifest("00000000-0000-0000-0000-00000000000e", status,
                                  [f"{DSS_COLOR}/Norder3/Dir0/Npix1.jpg"]))
    assert save_covers(read_save_manifest("00000000-0000-0000-0000-00000000000e")) == set()


def test_an_unfinished_save_stops_pinning_but_keeps_its_bytes(monkeypatch):
    """The LDN1295 case: a cancelled save's tiles become browsable and count
    towards .total, and nothing is deleted from disk."""
    from nova.offline_sky_store import write_save_manifest, refresh_pinned_bytes
    monkeypatch.setattr("nova.offline_sky_store.OFFLINE_SKY_CACHE_MB", 8)
    rels = _store_n_tiles(2, 1000)
    key0 = f"{DSS_COLOR}/{rels[0]}"
    write_save_manifest(_coverage_manifest("00000000-0000-0000-0000-00000000000f", "cancelled", [key0]))
    assert refresh_pinned_bytes() == 0
    assert total_bytes() == 2000
    assert read_file(DSS_COLOR, rels[0]) is not None      # still on disk
    result = evict_browsed()
    assert result["pinned"] == 0                          # its tile is no longer pinned


def test_a_manifest_without_a_status_still_covers():
    """The first step-2 version wrote no status; its tiles stay pinned (they are
    what those saves spent real bytes on)."""
    from nova.offline_sky_store import save_covers
    assert save_covers({"paths": ["dss2-color/a.jpg"]}) == {"dss2-color/a.jpg"}


# ---------------------------------------------------------------------------
# /api/offline/status: pinned coverage of a frame, three states
# (the header indicator that used to render them is gone; the route it read
# from stays, and is what the Configuration download page will use)
# ---------------------------------------------------------------------------

def test_the_indicator_reads_pinned_coverage_not_disk(monkeypatch, client, db_session):
    """The three states, judged on what is pinned for the current rig and frame."""
    from nova import offline_sky_jobs as jobs
    from nova.blueprints import offline_sky as bp
    monkeypatch.setattr(bp, "OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(bp, "OFFLINE_SKY_SAVE_ENABLED", True)
    monkeypatch.setattr(jobs, "base_complete", lambda slug: True)
    frame = jobs.validate_frame(83.8, -5.4, 1.0, 0.8)
    q = ("/api/offline/status?ra=83.8&dec=-5.4&frame_w_deg=1.0&frame_h_deg=0.8"
         "&surveys=P/DSS2/color&rig_id=1")
    # base only: coverage exists but shallow
    data = client.get(q).get_json()
    assert data["surveys"][DSS_COLOR]["base_complete"] is True
    assert data["surveys"][DSS_COLOR]["max_pinned_order"] <= jobs.OFFLINE_SKY_BASE_ORDER


def test_the_indicator_never_warms_a_base_layer(monkeypatch, client):
    """Splitting the gate: the status route is read-only, and the client calls it
    without the piece-C gate that starts base jobs."""
    from nova.blueprints import offline_sky as bp
    monkeypatch.setattr(bp, "OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(bp, "OFFLINE_SKY_SAVE_ENABLED", True)
    warmed = []
    monkeypatch.setattr("nova.offline_sky_jobs.ensure_base_survey",
                        lambda slug: warmed.append(slug) or {"slug": slug})
    resp = client.get("/api/offline/status?ra=83.8&dec=-5.4&frame_w_deg=1.0&frame_h_deg=0.8"
                      "&surveys=P/DSS2/color")
    assert resp.status_code == 200
    assert warmed == [], "the indicator must not start anything"


def test_a_finished_save_covers_its_frame_without_the_base_layer(monkeypatch, client):
    """The indicator must not need the Allsky preview, nor a "complete" base.

    A frame is covered when its tiles are pinned. `base_complete` also required
    the Allsky file, which nobody may ever have fetched, and gating on it turned
    a frame a save covers into "Not available offline" (M106 with the FRA500 rig).
    """
    from nova import offline_sky_jobs as jobs
    from nova import offline_sky_store as store
    from nova.offline_sky_store import write_json_atomic, _base_pins_path
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", True)

    frame = jobs.validate_frame(184.62, 47.34, 0.687, 0.459)
    # pin what a covering save pins (orders above the base) plus the base orders,
    # with no Allsky anywhere: base_complete stays False
    keys = set()
    facts = jobs.survey_facts(DSS_COLOR, fetch=False)
    cov = jobs.survey_coverage(DSS_COLOR, fetch=False)
    by = jobs.hp.cone_pixels(frame["ra"], frame["dec"], frame["radius_deg"], 0, 6)
    for order in range(0, 7):
        for n in by.get(order, []):
            if cov.contains(order, n):
                keys.add(f"{DSS_COLOR}/{jobs._tile_rel(order, n, facts.tile_ext)}")
    assert keys
    write_json_atomic(_base_pins_path(), {"paths": sorted(keys)})

    assert store.tile_exists(DSS_COLOR, jobs.allsky_rel(facts.tile_ext)) is False
    assert jobs.base_complete(DSS_COLOR) is False, "no Allsky, so never 'complete'"

    order = jobs.covered_pinned_order(DSS_COLOR, frame["ra"], frame["dec"],
                                      frame["radius_deg"])
    assert order == 6, "the pinned tiles cover the frame to order 6"


def test_the_indicator_reads_no_base_layer_field(client, db_session, monkeypatch):
    """The three states come from pinned coverage of this frame only."""
    import os
    from nova import offline_sky_jobs as jobs
    from nova import offline_sky_store as store
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", True)
    monkeypatch.setattr(store, "CACHE_DIR", os.path.abspath("instance/cache"))
    monkeypatch.setattr(jobs, "base_complete", lambda slug: False)   # a hostile base
    from test_offline_sky_jobs import _make_rig
    rid = _make_rig(db_session, name="FRA500 + ASI 585", pixel_um=3.76)

    # the FRA500 M106 save, as it is on disk
    q = ("/api/offline/status?ra=184.74008333333325&dec=47.303719444444425"
         "&frame_w_deg=1.2833718008350588&frame_h_deg=0.7219172710004274"
         f"&rig_id={rid}&surveys=P/DSS2/color")
    st = client.get(q).get_json()["surveys"]["dss2-color"]
    assert st["base_complete"] is False, "the base layer is deliberately absent"
    assert st["max_pinned_order"] >= st["target_order"] >= 0, \
        "a save for this frame at this rig's detail reads as covered"
