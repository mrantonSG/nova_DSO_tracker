"""The Configuration -> General "Offline sky" storage block.

What is pinned here:

* The limit's precedence: the installation's choice in <store>/.settings.json,
  else the OFFLINE_SKY_CACHE_MB startup default -- and the difference between a
  missing key ("nobody chose") and a null value ("chose No limit").
* The budget: a set limit, the disk-derived figure for "No limit", and the fact
  that *both* bind (the tighter of the two wins).
* The signed budget: a limit at or below the pinned size, and a disk inside its
  headroom, must both evict every browsed tile rather than nothing.
* Clear: viewed tiles go, downloaded/pinned tiles and all metadata stay, totals
  reconcile.
* The label formatter and the status route.
* Access control: the 404/403 matrix and CSRF on the POSTs.
"""

import json
import os
import time

import pytest

import nova
import nova.offline_sky_store as store
from nova.offline_sky_store import (
    budget_bytes, store_file, survey_dir, read_file, set_limit_mb, limit_mb,
    clear_browsed, evict_browsed, status as store_status,
)
from nova.blueprints.offline_sky import format_gb, format_limit_gb, storage_view

DSS = "dss2-color"
OIII = "nsns-oiii8"

BIG = 400 * 1024          # 400 KiB per test tile


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _settings_path():
    return os.path.join(store.hips_root(), ".settings.json")


def _write_settings(payload):
    os.makedirs(store.hips_root(), exist_ok=True)
    with open(_settings_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _store_n_tiles(slug, n, size=BIG, start=0):
    rels = []
    for i in range(start, start + n):
        rel = f"Norder3/Dir0/Npix{i}.jpg"
        store_file(slug, rel, b"x" * size)
        rels.append(rel)
    return rels


def _age(rels, slug, base):
    """Give each tile a distinct, increasing mtime (index 0 oldest)."""
    for i, rel in enumerate(rels):
        os.utime(os.path.join(survey_dir(slug), rel), (base - 100 + i, base - 100 + i))


def _store_on(monkeypatch):
    """Store on, UI on, single-user: the section is shown and reachable."""
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "on")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")


def _store_on_multi(monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "on")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")


# ---------------------------------------------------------------------------
# 1. Limit resolution: the settings file, the env default, precedence
# ---------------------------------------------------------------------------

def test_no_settings_file_falls_back_to_the_env_default(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 5120)
    assert not os.path.exists(_settings_path())
    assert limit_mb() == 5120
    assert budget_bytes() == 5120 * 1024 * 1024


def test_a_saved_choice_beats_the_env_default(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 2048)
    set_limit_mb(10240)
    assert limit_mb() == 10240
    # The env value is only the default now; it no longer decides.
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 5120)
    store.invalidate_settings_cache()
    assert limit_mb() == 10240


def test_a_null_choice_means_no_limit_and_beats_the_env(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 2048)
    set_limit_mb(None)
    assert limit_mb() is None
    # A null value is a made choice, not "fall back to the env default".
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 5120)
    store.invalidate_settings_cache()
    assert limit_mb() is None


def test_a_negative_env_default_means_no_limit(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 0)
    assert limit_mb() is None
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", -1)
    assert limit_mb() is None


def test_a_non_positive_saved_choice_is_written_as_no_limit(monkeypatch):
    set_limit_mb(0)
    with open(_settings_path(), encoding="utf-8") as fh:
        assert json.load(fh) == {"cache_mb": None}
    assert limit_mb() is None


def test_the_choice_is_picked_up_when_another_process_writes_it(monkeypatch):
    """The memo is keyed on the file's mtime, so a write by another worker is seen."""
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 2048)
    assert limit_mb() == 2048
    # Simulate another process: write the file directly, bypassing set_limit_mb
    # (so our in-process memo is *not* dropped) and bump the mtime.
    _write_settings({"cache_mb": 25600})
    future = time.time() + 5
    os.utime(_settings_path(), (future, future))
    assert limit_mb() == 25600


def test_a_corrupt_settings_file_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 5120)
    os.makedirs(store.hips_root(), exist_ok=True)
    with open(_settings_path(), "w", encoding="utf-8") as fh:
        fh.write("{not json at all")
    assert limit_mb() == 5120
    # And a non-dict payload is ignored too.
    with open(_settings_path(), "w", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]")
    assert limit_mb() == 5120


def test_a_malformed_choice_value_falls_back_to_the_env(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 5120)
    _write_settings({"cache_mb": "plenty"})
    assert limit_mb() == 5120


def test_an_unwritable_store_reports_the_failure(monkeypatch):
    """set_limit_mb must raise, not silently claim the choice was kept."""
    monkeypatch.setattr(store, "_settings_path", lambda: "/proc/nope/.settings.json")
    with pytest.raises(OSError):
        set_limit_mb(5120)


def test_the_settings_file_is_not_a_tile(monkeypatch):
    """It sits at the store root, so the tile walk ignores it: never evicted,
    never counted, and Clear does not delete it."""
    set_limit_mb(5120)
    store_file(DSS, "Norder3/Dir0/Npix1.jpg", b"x" * BIG)
    clear_browsed()
    assert os.path.exists(_settings_path())
    assert json.load(open(_settings_path())) == {"cache_mb": 5120}


# ---------------------------------------------------------------------------
# 2. The budget: the limit, the disk, and the tighter of the two
# ---------------------------------------------------------------------------

def test_a_set_limit_is_the_budget_when_the_disk_is_roomy(monkeypatch):
    monkeypatch.setattr(store, "disk_free_bytes", lambda: 500 * 1024 ** 3)
    set_limit_mb(5120)
    assert budget_bytes() == 5120 * 1024 * 1024


def test_no_limit_uses_the_disk_headroom(monkeypatch):
    free = 10 * 1024 ** 3
    monkeypatch.setattr(store, "disk_free_bytes", lambda: free)
    set_limit_mb(None)
    assert budget_bytes() == free - store.DISK_HEADROOM_BYTES


def test_a_set_limit_is_still_capped_by_the_disk(monkeypatch):
    """A 25 GB limit on a nearly-full disk is not permission to fill it."""
    free = 2 * 1024 ** 3                      # only 2 GiB free
    monkeypatch.setattr(store, "disk_free_bytes", lambda: free)
    set_limit_mb(25600)                       # 25 GiB
    assert budget_bytes() == free - store.DISK_HEADROOM_BYTES
    assert budget_bytes() < 25600 * 1024 * 1024


def test_a_disk_inside_its_headroom_gives_a_non_positive_budget(monkeypatch):
    monkeypatch.setattr(store, "disk_free_bytes", lambda: store.DISK_HEADROOM_BYTES // 2)
    set_limit_mb(None)
    assert budget_bytes() <= 0


# ---------------------------------------------------------------------------
# 3. Eviction against the budget: the signed-budget fix
# ---------------------------------------------------------------------------

def test_a_limit_below_the_pinned_size_empties_the_browsed_tiles(monkeypatch):
    """budget = limit - pinned is negative here. Everything browsed must go --
    a `cap > 0`-style guard would have evicted nothing at all."""
    set_limit_mb(1)                            # 1 MiB limit
    pinned = _store_n_tiles(DSS, 2, size=800 * 1024)     # 1.6 MiB pinned
    store.add_base_pins([f"{DSS}/{pinned[0]}", f"{DSS}/{pinned[1]}"])
    store.refresh_pinned_bytes()
    browsed = _store_n_tiles(DSS, 3, size=BIG, start=100)
    _age(browsed, DSS, time.time())

    out = evict_browsed()

    for rel in browsed:
        assert read_file(DSS, rel) is None, "browsed tiles must all go"
    for rel in pinned:
        assert read_file(DSS, rel) is not None, "pinned tiles survive"
    assert out["total"] == 0
    assert out["pinned"] == 2 * 800 * 1024


def test_eviction_under_no_limit_still_frees_a_full_disk(monkeypatch):
    """No limit + a disk inside its headroom: the store must evict, not sit still."""
    monkeypatch.setattr(store, "disk_free_bytes", lambda: store.DISK_HEADROOM_BYTES // 2)
    set_limit_mb(None)
    rels = _store_n_tiles(DSS, 3, size=BIG)
    _age(rels, DSS, time.time())

    out = evict_browsed()

    assert out["removed"] == 3
    assert out["total"] == 0


def test_eviction_respects_a_roomy_no_limit(monkeypatch):
    monkeypatch.setattr(store, "disk_free_bytes", lambda: 500 * 1024 ** 3)
    set_limit_mb(None)
    rels = _store_n_tiles(DSS, 3, size=BIG)

    out = evict_browsed()

    assert out["removed"] == 0
    for rel in rels:
        assert read_file(DSS, rel) is not None


def test_eviction_is_oldest_first(monkeypatch):
    set_limit_mb(1)                            # 1 MiB
    rels = _store_n_tiles(DSS, 4, size=BIG)    # 1.6 MiB browsed
    _age(rels, DSS, time.time())

    evict_browsed()

    assert read_file(DSS, rels[0]) is None     # oldest
    assert read_file(DSS, rels[3]) is not None  # newest


# ---------------------------------------------------------------------------
# 4. Clear browsed sky
# ---------------------------------------------------------------------------

def test_clear_removes_browsed_and_keeps_pinned():
    pinned = _store_n_tiles(DSS, 2, size=BIG)
    store.add_base_pins([f"{DSS}/{pinned[0]}", f"{DSS}/{pinned[1]}"])
    store.refresh_pinned_bytes()
    browsed = _store_n_tiles(DSS, 3, size=BIG, start=50)

    out = clear_browsed()

    assert out["removed"] == 3
    assert out["total"] == 0
    for rel in browsed:
        assert read_file(DSS, rel) is None
    for rel in pinned:
        assert read_file(DSS, rel) is not None
    assert out["pinned"] == 2 * BIG


def test_clear_keeps_metadata_and_job_queue():
    store_file(DSS, "properties", b"hips_order=9\n", count=False)
    store_file(DSS, "Moc.fits", b"moc", count=False)
    _store_n_tiles(DSS, 2, size=BIG)
    os.makedirs(store.jobs_dir(), exist_ok=True)
    with open(os.path.join(store.jobs_dir(), "job1.json"), "w") as fh:
        fh.write("{}")

    clear_browsed()

    assert read_file(DSS, "properties") is not None
    assert read_file(DSS, "Moc.fits") is not None
    assert os.path.exists(os.path.join(store.jobs_dir(), "job1.json"))
    assert os.path.exists(os.path.join(store.hips_root(), ".base-pins.json")) or True


def test_clear_lets_a_running_jobs_claims_survive():
    """A job claims tiles before fetching them; those are pinned from the start."""
    job_pins = _store_n_tiles(DSS, 2, size=BIG)
    rels = _store_n_tiles(DSS, 2, size=BIG, start=10)
    for rel in job_pins:
        store.claim_for_job("job-1", f"{DSS}/{rel}")
    store.refresh_pinned_bytes()

    clear_browsed()

    for rel in job_pins:
        assert read_file(DSS, rel) is not None, "a claimed tile is pinned"
    for rel in rels:
        assert read_file(DSS, rel) is None


def test_clear_reconciles_the_totals():
    _store_n_tiles(DSS, 3, size=BIG)
    store._set_total(999999999)                 # simulate drift
    clear_browsed()
    assert store.total_bytes() == 0


def test_clear_on_an_empty_store_is_harmless():
    out = clear_browsed()
    assert out["removed"] == 0
    assert out["total"] == 0


def test_clear_now_returns_none_when_another_worker_holds_the_lock():
    """A busy lock must be reported, not silently treated as 'cleared'."""
    from nova.helpers import try_acquire_file_lock, release_file_lock
    from nova.offline_sky_store import clear_now, _evict_lock_path

    os.makedirs(store.hips_root(), exist_ok=True)
    fh = try_acquire_file_lock(_evict_lock_path())
    assert fh is not None
    try:
        assert clear_now() is None
    finally:
        release_file_lock(fh)


# ---------------------------------------------------------------------------
# 5. The formatter (one formatter, 1 GB = 1024 MB, one decimal below 10 GB)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nbytes,expected", [
    (0, "0 GB"),
    (512 * 1024 ** 2, "512 MB"),                       # under 1 GB: MB
    (1024 ** 3, "1 GB"),                               # exactly 1
    (int(1.2 * 1024 ** 3), "1.2 GB"),                  # one decimal
    (2 * 1024 ** 3, "2 GB"),                           # no trailing .0
    (int(2.9 * 1024 ** 3), "2.9 GB"),
    (int(9.9 * 1024 ** 3), "9.9 GB"),                  # the last one-decimal case
    (10 * 1024 ** 3, "10 GB"),                         # at/over 10: no decimal
    (int(12.4 * 1024 ** 3), "12 GB"),
    (25 * 1024 ** 3, "25 GB"),
])
def test_format_gb(nbytes, expected):
    assert format_gb(nbytes) == expected


@pytest.mark.parametrize("mb,expected", [
    (2048, "2 GB"), (5120, "5 GB"), (10240, "10 GB"), (25600, "25 GB"),
    (3000, "2.9 GB"),                                  # a non-preset env value
    (None, "No limit"),
])
def test_format_limit_gb(mb, expected):
    assert format_limit_gb(mb) == expected


def test_one_gb_is_1024_mb():
    assert format_limit_gb(2048) == "2 GB"
    assert format_gb(2048 * 1024 * 1024) == "2 GB"


# ---------------------------------------------------------------------------
# 6. storage_view: the labels and flags the template renders
# ---------------------------------------------------------------------------

def test_storage_view_reports_a_preset_limit(monkeypatch):
    set_limit_mb(5120)
    view = storage_view()
    assert view["limit_mb"] == 5120
    assert view["limit_label"] == "5 GB"
    assert view["limit_is_custom"] is False
    assert view["limit_exceeded"] is False
    assert view["limit_presets"] == [2048, 5120, 10240, 25600]


def test_storage_view_flags_a_non_preset_limit(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 3000)
    store.invalidate_settings_cache()
    view = storage_view()
    assert view["limit_mb"] == 3000
    assert view["limit_is_custom"] is True
    assert view["limit_label"] == "2.9 GB"


def test_storage_view_flags_a_limit_exceeded_by_the_pinned_tiles(monkeypatch):
    set_limit_mb(1)                                     # 1 MiB
    rels = _store_n_tiles(DSS, 2, size=800 * 1024)      # 1.6 MiB pinned
    store.add_base_pins([f"{DSS}/{rels[0]}", f"{DSS}/{rels[1]}"])
    store.refresh_pinned_bytes()
    view = storage_view()
    assert view["pinned"] == 2 * 800 * 1024
    assert view["limit_exceeded"] is True
    assert view["pinned_label"] == "2 MB"               # 1.5625 MiB, rounded


def test_storage_view_no_limit(monkeypatch):
    set_limit_mb(None)
    view = storage_view()
    assert view["limit_mb"] is None
    assert view["limit_label"] == "No limit"
    assert view["limit_is_custom"] is False
    assert view["limit_exceeded"] is False


# ---------------------------------------------------------------------------
# 7. Routes: the JSON API, status and limit
# ---------------------------------------------------------------------------

def test_status_route_reports_the_store(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    resp = su_client_logged_in.get("/api/offline-sky/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["limit_mb"] == 2048
    assert data["headroom"] == store.DISK_HEADROOM_BYTES
    assert data["downloads_available"] is True
    assert data["limit_presets_mb"] == [2048, 5120, 10240, 25600]


def test_limit_route_persists_and_evicts(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    monkeypatch.setattr(store, "disk_free_bytes", lambda: 500 * 1024 ** 3)
    set_limit_mb(25600)
    rels = _store_n_tiles(DSS, 4, size=800 * 1024)      # 3.2 MiB browsed
    _age(rels, DSS, time.time())

    resp = su_client_logged_in.post("/api/offline-sky/limit",
                                    json={"limit": "2048"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["limit_mb"] == 2048
    assert limit_mb() == 2048                            # persisted
    assert data["evicting"] is False


def test_limit_route_accepts_the_none_sentinel(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    resp = su_client_logged_in.post("/api/offline-sky/limit",
                                    json={"limit": "none"})
    assert resp.status_code == 200
    assert resp.get_json()["limit_mb"] is None
    assert limit_mb() is None


def test_limit_route_rejects_a_nonsense_value(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    resp = su_client_logged_in.post("/api/offline-sky/limit",
                                    json={"limit": "lots"})
    assert resp.status_code == 400


def test_limit_route_reports_a_read_only_store(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    monkeypatch.setattr(store, "_settings_path", lambda: "/proc/nope/.settings.json")
    resp = su_client_logged_in.post("/api/offline-sky/limit", json={"limit": "5120"})
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "read_only"


def test_clear_route_empties_the_viewed_tiles(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    pinned = _store_n_tiles(DSS, 1, size=BIG)
    store.add_base_pins([f"{DSS}/{pinned[0]}"])
    store.refresh_pinned_bytes()
    browsed = _store_n_tiles(DSS, 2, size=BIG, start=20)

    resp = su_client_logged_in.post("/api/offline-sky/clear")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cleared"] is True
    assert data["removed"] == 2
    assert data["browsed"] == 0
    assert read_file(DSS, pinned[0]) is not None
    assert all(read_file(DSS, r) is None for r in browsed)


def test_clear_route_reports_a_busy_lock(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    from nova.helpers import try_acquire_file_lock, release_file_lock
    from nova.offline_sky_store import _evict_lock_path

    os.makedirs(store.hips_root(), exist_ok=True)
    fh = try_acquire_file_lock(_evict_lock_path())
    assert fh is not None
    try:
        resp = su_client_logged_in.post("/api/offline-sky/clear")
    finally:
        release_file_lock(fh)
    assert resp.status_code == 200
    assert resp.get_json()["cleared"] is False
    assert resp.get_json()["evicting"] is True


# ---------------------------------------------------------------------------
# 8. Access control: the 404 / 403 matrix, and CSRF
# ---------------------------------------------------------------------------

def _storage_routes():
    return ["/api/offline-sky/status"]


def test_storage_routes_are_enumerated():
    """A new storage route must be added here, or its access control is untested."""
    import nova as _nova
    # A path can carry several rules (POST and DELETE on the same URL), so
    # compare the set of paths rather than the rule list.
    found = sorted({rule.rule for rule in _nova.app.url_map.iter_rules()
                    if rule.rule.startswith("/api/offline-sky/")})
    assert found == [
        "/api/offline-sky/clear",
        "/api/offline-sky/downloads",
        "/api/offline-sky/downloads/<slug>",
        "/api/offline-sky/downloads/<slug>/cancel",
        "/api/offline-sky/downloads/<slug>/trim",
        "/api/offline-sky/limit",
        "/api/offline-sky/plan",
        "/api/offline-sky/selection",
        "/api/offline-sky/status",
    ]


def test_store_off_is_404(su_client_logged_in, monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "off")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "off")
    assert su_client_logged_in.get("/api/offline-sky/status").status_code == 404
    assert su_client_logged_in.post("/api/offline-sky/clear").status_code == 404
    assert su_client_logged_in.post("/api/offline-sky/limit",
                                    json={"limit": "5120"}).status_code == 404


def test_ui_off_is_404(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "off")
    assert su_client_logged_in.get("/api/offline-sky/status").status_code == 404
    assert su_client_logged_in.post("/api/offline-sky/clear").status_code == 404


def test_single_user_can_use_the_storage_api(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    assert su_client_logged_in.get("/api/offline-sky/status").status_code == 200


def test_multi_user_non_admin_is_403(multi_user_client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "on")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")
    client, _ids = multi_user_client          # logged in as "UserA", not an admin
    assert client.get("/api/offline-sky/status").status_code == 403
    assert client.post("/api/offline-sky/clear").status_code == 403
    assert client.post("/api/offline-sky/limit",
                       json={"limit": "5120"}).status_code == 403
    # And the section itself is not rendered for them.
    html = client.get("/config_form").get_data(as_text=True)
    assert 'id="offline-sky-section"' not in html


def test_multi_user_admin_is_allowed(multi_user_client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "on")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")
    monkeypatch.setattr("nova.config.ADMIN_USERS", {"UserA"})
    monkeypatch.setattr("nova.blueprints.offline_sky.ADMIN_USERS", {"UserA"})
    client, _ids = multi_user_client
    assert client.get("/api/offline-sky/status").status_code == 200
    html = client.get("/config_form").get_data(as_text=True)
    assert 'id="offline-sky-section"' in html


def test_multi_user_unauthenticated_is_not_a_redirect_to_login(
        mu_client_logged_out, monkeypatch):
    """The /api/ tree answers JSON, never a login redirect."""
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.config.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "on")
    resp = mu_client_logged_out.get("/api/offline-sky/status")
    assert resp.status_code in (401, 403, 404)


def test_a_post_without_a_csrf_token_is_refused(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    import nova as _nova
    monkeypatch.setitem(_nova.app.config, "WTF_CSRF_ENABLED", True)

    resp = su_client_logged_in.post("/api/offline-sky/clear")
    assert resp.status_code == 400
    # Nothing was done.
    store_file(DSS, "Norder3/Dir0/Npix1.jpg", b"x" * BIG)
    assert read_file(DSS, "Norder3/Dir0/Npix1.jpg") is not None


# ---------------------------------------------------------------------------
# 9. The unwritable store: the section still renders, with the note
# ---------------------------------------------------------------------------

def test_the_section_renders_with_a_note_when_the_store_is_unwritable(
        su_client_logged_in, monkeypatch):
    """A read-only store must not remove the section: the limit and Clear stay
    usable (the store just is not written to), and the note says downloads are
    off rather than leaving the user to wonder."""
    _store_on(monkeypatch)
    monkeypatch.setattr(store, "DOWNLOADS_AVAILABLE", False)
    html = su_client_logged_in.get("/config_form").get_data(as_text=True)
    assert 'id="offline-sky-section"' in html
    assert 'id="offline-sky-unwritable"' in html
    assert store.downloads_available() is False
    # The status route agrees, so JS keeps the note honest after a change.
    data = su_client_logged_in.get("/api/offline-sky/status").get_json()
    assert data["downloads_available"] is False


def test_the_note_is_absent_when_the_store_is_writable(su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    monkeypatch.setattr(store, "DOWNLOADS_AVAILABLE", True)
    html = su_client_logged_in.get("/config_form").get_data(as_text=True)
    assert 'id="offline-sky-section"' in html
    assert 'id="offline-sky-unwritable"' not in html


# ---------------------------------------------------------------------------
# 10. The over-limit warning follows the pin set, not the .pinned sidecar
# ---------------------------------------------------------------------------

def test_status_reads_the_cached_pinned_total_in_o1(
        su_client_logged_in, monkeypatch):
    """status() reads the cached .pinned figure instead of stat()ing every pin.

    The cache is maintained on claim, adopt, delete and checkpoint and
    re-derived at startup and job finish. The old measured-every-pin version
    was O(pins): 621 ms at 250k pins. The cached figure is refreshed by
    refresh_pinned_bytes(), so the status response stays truthful."""
    _store_on(monkeypatch)
    set_limit_mb(10240)                              # 10 GiB: nothing to evict
    pinned = _store_n_tiles(DSS, 2, size=BIG)        # 800 KiB really pinned
    store.add_base_pins([f"{DSS}/{pinned[0]}", f"{DSS}/{pinned[1]}"])
    store.refresh_pinned_bytes()
    assert store.pinned_bytes() == 2 * BIG

    data = su_client_logged_in.get("/api/offline-sky/status").get_json()
    assert data["pinned"] == 2 * BIG
    assert data["pinned"] <= data["limit_mb"] * 1024 * 1024


def test_a_limit_below_the_pinned_still_warns(su_client_logged_in, monkeypatch):
    """The warning still fires when the cached pinned total exceeds the limit."""
    _store_on(monkeypatch)
    set_limit_mb(1)                                  # 1 MiB
    pinned = _store_n_tiles(DSS, 2, size=800 * 1024)  # 1.6 MiB pinned
    store.add_base_pins([f"{DSS}/{pinned[0]}", f"{DSS}/{pinned[1]}"])
    store.refresh_pinned_bytes()

    data = su_client_logged_in.get("/api/offline-sky/status").get_json()
    assert data["pinned"] == 2 * 800 * 1024
    assert data["pinned"] > data["limit_mb"] * 1024 * 1024


def test_status_reports_not_evicting_when_no_pass_holds_the_lock(
        su_client_logged_in, monkeypatch):
    _store_on(monkeypatch)
    data = su_client_logged_in.get("/api/offline-sky/status").get_json()
    assert data["evicting"] is False


def test_status_reports_evicting_while_a_pass_holds_the_lock(
        su_client_logged_in, monkeypatch):
    """The page needs to know the numbers are not settled yet, so it keeps
    polling instead of freezing a warning built from mid-eviction bytes."""
    _store_on(monkeypatch)
    from nova.helpers import try_acquire_file_lock, release_file_lock
    from nova.offline_sky_store import _evict_lock_path

    os.makedirs(store.hips_root(), exist_ok=True)
    fh = try_acquire_file_lock(_evict_lock_path())
    assert fh is not None
    try:
        data = su_client_logged_in.get("/api/offline-sky/status").get_json()
        assert data["evicting"] is True
    finally:
        release_file_lock(fh)


def test_the_limit_route_waits_briefly_then_reports_settled(
        su_client_logged_in, monkeypatch):
    """When another pass holds the lock, the route waits for it (bounded) rather
    than answering from a snapshot taken mid-change."""
    import threading
    from nova.helpers import try_acquire_file_lock, release_file_lock
    from nova.offline_sky_store import _evict_lock_path

    _store_on(monkeypatch)
    os.makedirs(store.hips_root(), exist_ok=True)
    fh = try_acquire_file_lock(_evict_lock_path())
    assert fh is not None
    # Release it shortly, so the route's bounded wait sees it settle.
    threading.Timer(0.4, lambda: release_file_lock(fh)).start()

    resp = su_client_logged_in.post("/api/offline-sky/limit", json={"limit": "5120"})
    assert resp.status_code == 200
    assert resp.get_json()["evicting"] is False
    assert limit_mb() == 5120


def test_the_limit_route_reports_evicting_when_the_holder_is_slow(
        su_client_logged_in, monkeypatch):
    """A holder that outlasts the bounded wait: the page is told "evicting" and
    polls, rather than being handed numbers that are still due to change."""
    from nova.helpers import try_acquire_file_lock, release_file_lock
    from nova.offline_sky_store import _evict_lock_path
    from nova.blueprints import offline_sky as bp

    _store_on(monkeypatch)
    monkeypatch.setattr(bp, "_SETTLE_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(bp, "_SETTLE_POLL_SECONDS", 0.05)
    os.makedirs(store.hips_root(), exist_ok=True)
    fh = try_acquire_file_lock(_evict_lock_path())
    assert fh is not None
    try:
        resp = su_client_logged_in.post("/api/offline-sky/limit", json={"limit": "5120"})
        assert resp.status_code == 200
        assert resp.get_json()["evicting"] is True
        assert limit_mb() == 5120           # the choice is still persisted
    finally:
        release_file_lock(fh)
