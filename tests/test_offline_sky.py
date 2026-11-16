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
import threading
import time
import uuid

import pytest
import requests

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
# Base job
# ---------------------------------------------------------------------------

def _fast_job(monkeypatch):
    import nova.offline_sky_jobs as jobs
    monkeypatch.setattr(jobs, "OFFLINE_SKY_JOB_DELAY_SECONDS", 0)
    monkeypatch.setattr(jobs, "_fetch_tile_format_ext", lambda slug: "jpg")
    monkeypatch.setattr(jobs, "_throttled_fetch", lambda base, url: b"FAKE")


def test_base_job_enumerates_orders_0_to_N_and_pins(monkeypatch):
    import nova.offline_sky_jobs as jobs
    from nova.offline_sky_store import base_pinned_rel_paths
    _fast_job(monkeypatch)

    jobs._run_base(DSS_COLOR, "base:dss2-color", threading.Event())

    assert jobs.base_complete(DSS_COLOR)
    pins = base_pinned_rel_paths()
    assert f"{DSS_COLOR}/Allsky.jpg" in pins
    assert f"{DSS_COLOR}/Norder0/Dir0/Npix11.jpg" in pins      # last tile of order 0
    assert f"{DSS_COLOR}/Norder3/Dir0/Npix767.jpg" in pins     # last tile of order 3
    assert not any("Norder4" in p for p in pins)               # base stops at order 3


def test_base_complete_when_already_cached(monkeypatch):
    import nova.offline_sky_jobs as jobs
    monkeypatch.setattr(jobs, "_cached_tile_ext", lambda slug: "jpg")
    assert not jobs.base_complete(DSS_COLOR)
    for rel in jobs.base_rel_paths(DSS_COLOR, "jpg"):
        store_file(DSS_COLOR, rel, b"X", count=False)
    assert jobs.base_complete(DSS_COLOR)


# ---------------------------------------------------------------------------
# Save job
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


def test_save_job_enumerates_cone_orders_base_plus_1_to_chosen(monkeypatch):
    import nova.offline_sky_jobs as jobs
    from nova.offline_sky_store import read_save_manifest
    _fast_job(monkeypatch)
    m = _manifest()
    jobs._run_save(m, m["id"], threading.Event())

    saved = read_save_manifest(m["id"])
    assert saved["paths"]
    assert saved["bytes"] > 0
    for p in saved["paths"]:
        assert "/Norder4/" in p or "/Norder5/" in p       # base_order(3)+1 .. chosen(5)
        assert "/Norder3/" not in p and "/Norder6/" not in p


def test_save_job_skips_tiles_already_on_disk(monkeypatch):
    import nova.offline_sky_jobs as jobs
    from nova.offline_sky_store import read_save_manifest
    _fast_job(monkeypatch)
    calls = []
    monkeypatch.setattr(jobs, "_throttled_fetch", lambda base, url: calls.append(url) or b"FAKE")

    rels = jobs.save_rel_paths(DSS_COLOR, "jpg", 83.8, -5.4, 1.0, 5)
    assert rels
    for rel in rels:
        store_file(DSS_COLOR, rel, b"X", count=False)

    m = _manifest()
    jobs._run_save(m, m["id"], threading.Event())
    assert calls == []                                    # resume: nothing fetched again
    assert len(read_save_manifest(m["id"])["paths"]) == len(rels)


def test_save_job_cancel(monkeypatch):
    import nova.offline_sky_jobs as jobs
    from nova.offline_sky_store import read_save_manifest
    _fast_job(monkeypatch)
    cancel = threading.Event()
    cancel.set()
    m = _manifest()
    jobs._run_save(m, m["id"], cancel)
    assert read_save_manifest(m["id"])["status"] == "cancelled"
    assert jobs._read_job_state(m["id"])["status"] == "cancelled"


def test_job_throttle_limits_concurrency(monkeypatch):
    import nova.offline_sky_jobs as jobs
    import nova.blueprints.offline_sky as bp
    jobs._JOB_SEMAPHORE = threading.BoundedSemaphore(2)
    in_flight = []
    peak = [0]
    lock = threading.Lock()

    def fake_fetch(base, url):
        with lock:
            in_flight.append(1)
            peak[0] = max(peak[0], len(in_flight))
        time.sleep(0.01)
        with lock:
            in_flight.pop()
        return b"x"

    monkeypatch.setattr(bp, "_fetch_upstream", fake_fetch)
    threads = [threading.Thread(target=jobs._throttled_fetch, args=("b", f"u{i}")) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] <= 2


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
    assert read_file(DSS_COLOR, rels[0]) is not None   # pinned by the save
    delete_save_manifest(mid)
    evict_browsed()
    assert read_file(DSS_COLOR, rels[0]) is None       # now ordinary and evictable


def test_cone_max_cached_order(monkeypatch):
    import nova.offline_sky_jobs as jobs
    monkeypatch.setattr(jobs, "base_complete", lambda slug: True)
    monkeypatch.setattr(jobs, "_cached_tile_ext", lambda slug: "jpg")
    for order in (4, 5):
        for rel in jobs.save_rel_paths(DSS_COLOR, "jpg", 83.8, -5.4, 1.0, order):
            store_file(DSS_COLOR, rel, b"X", count=False)
    assert jobs.cone_max_cached_order(DSS_COLOR, 83.8, -5.4, 1.0) == 5


# ---------------------------------------------------------------------------
# User API endpoints
# ---------------------------------------------------------------------------

def test_saves_are_owned_per_user(monkeypatch, multi_user_client):
    from nova.offline_sky_store import write_save_manifest, read_save_manifest
    _server_on(monkeypatch)
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


def test_estimate_endpoint_returns_tile_counts(monkeypatch, client):
    _server_on(monkeypatch)
    import nova.offline_sky_jobs as jobs
    monkeypatch.setattr(jobs, "_cached_tile_ext", lambda slug: "jpg")
    resp = client.get("/api/offline/estimate?ra=83.8&dec=-5.4&radius_deg=1.0"
                      "&max_order=5&surveys=dss2-color")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["surveys"][0]["slug"] == DSS_COLOR
    assert data["surveys"][0]["tile_count"] > 0
    assert data["surveys"][0]["est_bytes"] > 0
    assert data["cap_bytes"] > 0


def test_status_endpoint_reports_base_complete_and_order(monkeypatch, client):
    _server_on(monkeypatch)
    import nova.offline_sky_jobs as jobs
    monkeypatch.setattr(jobs, "base_complete", lambda slug: True)
    monkeypatch.setattr(jobs, "_cached_tile_ext", lambda slug: "jpg")
    for rel in jobs.save_rel_paths(DSS_COLOR, "jpg", 83.8, -5.4, 1.0, 4):
        store_file(DSS_COLOR, rel, b"X", count=False)
    resp = client.get("/api/offline/status?ra=83.8&dec=-5.4&radius_deg=1.0&surveys=P/DSS2/color")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["surveys"][DSS_COLOR]["base_complete"] is True
    assert data["surveys"][DSS_COLOR]["max_cached_order"] == 4
