"""Offline-sky settings: OFFLINE_SKY_UI, OFFLINE_SKY_DIR, and the startup
retirement of the old per-object saves.

Three things are pinned here:

* OFFLINE_SKY_UI resolved against the user mode (auto/on/off, and a typo), and
  the rule that the Framing tab shows its Offline view switch only when that
  resolves on *and* OFFLINE_SKY_STORE == "server".
* OFFLINE_SKY_DIR: the default, a relative path, an absolute path, a missing
  directory (created), and an unwritable one (downloads off, browsing fine).
* The startup migration: the retired save manifests go, their tiles stay on
  disk, and a second run is a no-op.
"""

import json
import logging
import os
import stat

import pytest

import nova.offline_sky_store as store
from nova.offline_sky_store import store_file, retire_save_entries, retired_pins_dir

DSS_COLOR = "dss2-color"


def _manifest(save_id, name="M42", surveys=(DSS_COLOR,), paths=(), missing_paths=()):
    return {"id": save_id, "user_id": 1, "name": name, "ra": 83.8, "dec": -5.4,
            "radius_deg": 1.0, "surveys": list(surveys), "max_order": 5,
            "created": "2026-01-01T00:00:00Z", "bytes": 0,
            "paths": list(paths), "missing_paths": list(missing_paths),
            "status": "done"}


def _write_manifest(manifest):
    os.makedirs(retired_pins_dir(), exist_ok=True)
    with open(os.path.join(retired_pins_dir(), f"{manifest['id']}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh)


def _server_on(monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")


# ---------------------------------------------------------------------------
# OFFLINE_SKY_UI: auto / on / off, and a typo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,single_user,expected", [
    ("", True, True),            # unset, a personal computer: the switch is there
    (None, True, True),
    ("", False, False),          # unset, a multi-user server: not there
    (None, False, False),
    ("   ", False, False),
    ("auto", True, True),        # auto says out loud what empty means
    ("auto", False, False),
    ("on", False, True),         # explicitly enabled on a multi-user instance
    ("ON", False, True),
    ("true", False, True),
    ("1", False, True),
    ("yes", False, True),
    (" YES ", False, True),
    ("off", True, False),        # explicitly disabled on a single-user one
    (" Off ", True, False),
    ("false", True, False),
    ("0", True, False),
    ("no", True, False),
    ("onn", True, True),         # a typo is auto, not a yes
    ("onn", False, False),
    ("enabled", False, False),
    ("2", False, False),
])
def test_the_ui_default_follows_the_user_mode(raw, single_user, expected):
    from nova.config import resolve_offline_sky_ui
    assert resolve_offline_sky_ui(raw, single_user) is expected


def test_an_unknown_ui_value_warns(caplog):
    import nova.config as cfg
    with caplog.at_level(logging.WARNING, logger="nova.config"):
        cfg.warn_unknown_offline_sky_ui("enabled")
    assert "OFFLINE_SKY_UI" in caplog.text and "enabled" in caplog.text
    assert "auto" in caplog.text


def test_a_known_ui_value_does_not_warn(caplog):
    import nova.config as cfg
    with caplog.at_level(logging.WARNING, logger="nova.config"):
        for raw in ("", "auto", "on", "off", "yes", "no"):
            cfg.warn_unknown_offline_sky_ui(raw)
    assert caplog.text == ""


def test_the_env_example_documents_the_ui_switch():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert "\nOFFLINE_SKY_UI=\n" in text
    assert "anything else" in text              # says what a typo does
    assert "OFFLINE_SKY_DIR=" in text
    assert "OFFLINE_SKY_SAVE_ENABLED" not in text.replace(
        "OFFLINE_SKY_SAVE_ENABLED, which are ignored", "")   # only the removal note


def _graph_page(client, monkeypatch, *, ui, store_on=True):
    import nova
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: store_on)
    monkeypatch.setattr(nova, "resolve_offline_sky_ui", lambda raw, single: ui)
    resp = client.get("/graph_dashboard/M42")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_switch_is_shown_when_the_ui_resolves_on(client, monkeypatch):
    page = _graph_page(client, monkeypatch, ui=True)
    assert 'id="offline-view-toggle"' in page
    assert "window.NOVA_OFFLINE_SKY_UI = true;" in page


def test_the_switch_is_hidden_when_the_ui_resolves_off(client, monkeypatch):
    page = _graph_page(client, monkeypatch, ui=False)
    assert 'id="offline-view-toggle"' not in page
    assert "window.NOVA_OFFLINE_SKY_UI = false;" in page
    # ...while the proxy flag itself is untouched: browsing still goes local.
    assert "window.NOVA_OFFLINE_SKY = true;" in page


def test_the_switch_is_hidden_when_the_store_is_off(client, monkeypatch):
    """OFFLINE_SKY_STORE=off means no proxy and no cache-only roots, so the
    switch has nothing to point at even with the UI flag on."""
    page = _graph_page(client, monkeypatch, ui=True, store_on=False)
    assert 'id="offline-view-toggle"' not in page
    assert "window.NOVA_OFFLINE_SKY_UI = false;" in page
    assert "window.NOVA_OFFLINE_SKY = false;" in page


def test_offline_sky_ui_requires_the_store(monkeypatch):
    import nova
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "on")
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: False)
    assert nova.offline_sky_ui() is False
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: True)
    assert nova.offline_sky_ui() is True


def test_the_ui_flag_reads_the_live_user_mode(monkeypatch):
    """auto follows nova.SINGLE_USER_MODE as it is *now*, not as it was at import."""
    import nova
    monkeypatch.setattr(nova, "OFFLINE_SKY_UI_RAW", "")
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: True)
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    assert nova.offline_sky_ui() is True
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", False)
    assert nova.offline_sky_ui() is False


# ---------------------------------------------------------------------------
# OFFLINE_SKY_DIR: default, relative, absolute, missing, unwritable
# ---------------------------------------------------------------------------

def test_the_store_defaults_to_the_instance_cache(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "")
    monkeypatch.setattr(store, "CACHE_DIR", "/tmp/some/cache")
    assert store.hips_root() == os.path.join("/tmp/some/cache", "hips")


def test_a_relative_dir_resolves_against_the_instance(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "sky-tiles")
    monkeypatch.setattr(store, "INSTANCE_PATH", "/srv/nova/instance")
    assert store.hips_root() == os.path.join("/srv/nova/instance", "sky-tiles")


def test_an_absolute_dir_is_used_as_given(monkeypatch):
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", "/mnt/big/hips")
    assert store.hips_root() == "/mnt/big/hips"


def test_a_missing_dir_is_created(monkeypatch, tmp_path):
    target = tmp_path / "deep" / "hips"
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", str(target))
    monkeypatch.setattr(store, "DOWNLOADS_AVAILABLE", True)
    assert store.check_dir() is True
    assert target.is_dir()
    assert store.downloads_available() is True


def test_an_unwritable_dir_switches_downloads_off(monkeypatch, tmp_path, caplog):
    target = tmp_path / "hips"
    target.mkdir()
    os.chmod(target, stat.S_IRUSR | stat.S_IXUSR)      # r-x: not writable
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", str(target))
    monkeypatch.setattr(store, "DOWNLOADS_AVAILABLE", True)
    try:
        with caplog.at_level(logging.WARNING, logger="nova.offline_sky_store"):
            assert store.check_dir() is False
        assert store.downloads_available() is False
        assert "not writable" in caplog.text
        assert "browsing still works" in caplog.text
    finally:
        os.chmod(target, stat.S_IRWXU)


def test_store_file_is_non_fatal_when_the_dir_is_unwritable(monkeypatch, tmp_path, caplog):
    """The request path serves the bytes it fetched even when they cannot be
    kept: store_file reports the failure instead of raising."""
    target = tmp_path / "hips"
    target.mkdir()
    os.chmod(target, stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", str(target))
    monkeypatch.setattr(store, "_store_failure_logged", False)
    try:
        with caplog.at_level(logging.WARNING, logger="nova.offline_sky_store"):
            assert store_file(DSS_COLOR, "properties", b"x", count=False) is False
        assert "cannot write to the tile store" in caplog.text
    finally:
        os.chmod(target, stat.S_IRWXU)


def test_an_unwritable_store_still_serves_tiles(client, monkeypatch, tmp_path):
    """A tile fetched from upstream is returned even when the store cannot keep
    it; the route is not a 500."""
    import requests
    from nova.blueprints import offline_sky as bp
    target = tmp_path / "hips"
    target.mkdir()
    os.chmod(target, stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setattr(bp, "OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr(store, "OFFLINE_SKY_DIR", str(target))
    monkeypatch.setattr(store, "_store_failure_logged", False)

    class _R:
        status_code = 200
        content = b"TILEBYTES"

    monkeypatch.setattr(bp._session, "get", lambda url, **kw: _R())
    try:
        resp = client.get(f"/hips/{DSS_COLOR}/Norder3/Dir0/Npix1.jpg")
        assert resp.status_code == 200 and resp.data == b"TILEBYTES"
    finally:
        os.chmod(target, stat.S_IRWXU)


# ---------------------------------------------------------------------------
# The startup migration: entries go, tiles stay, a second run is a no-op
# ---------------------------------------------------------------------------

def test_the_migration_releases_the_old_saves_and_keeps_their_tiles(capsys):
    """A save that pinned a tile is retired: the tile stays on disk (and in
    .total, not .pinned) so it can be evicted like any browsed tile."""
    rel = "Norder3/Dir0/Npix1.jpg"
    store_file(DSS_COLOR, rel, b"t" * 1000)
    # A save job's claim is what pinned the tile (the store has no other way to
    # pin a per-object save), so claim it the way a running job did.
    os.makedirs(store.jobs_dir(), exist_ok=True)
    save_id = "00000000-0000-0000-0000-00000000000a"
    store.claim_for_job(save_id, f"{DSS_COLOR}/{rel}")
    _write_manifest(_manifest(save_id, paths=[f"{DSS_COLOR}/{rel}"]))
    store.refresh_pinned_bytes()                        # the sidecar the API reads
    assert store.pinned_bytes() > 0                     # it was pinned before

    retired = retire_save_entries()

    assert retired == 1
    assert not os.path.exists(retired_pins_dir())       # the folder is gone
    # The tile is still on disk, now as an ordinary browsed tile.
    assert store.read_file(DSS_COLOR, rel) == b"t" * 1000
    assert store.pinned_bytes() == 0
    assert store.total_bytes() >= 1000
    # One line per entry (name, surveys, tile count) then the summary.
    out = capsys.readouterr().out
    assert "M42" in out and DSS_COLOR in out
    assert "retired 1 saved entry" in out


def test_a_second_migration_run_is_a_no_op(capsys):
    _write_manifest(_manifest("00000000-0000-0000-0000-00000000000b"))
    assert retire_save_entries() == 1
    capsys.readouterr()                                 # drop the first run's lines
    assert retire_save_entries() == 0
    assert capsys.readouterr().out == ""


def test_the_migration_is_a_no_op_with_nothing_to_migrate(capsys):
    assert retire_save_entries() == 0
    assert capsys.readouterr().out == ""


def test_the_migration_removes_the_job_files_of_a_retired_entry():
    """A queued or paused save job has nothing left to write to, so its state,
    marker and claims go with the manifest."""
    import nova.offline_sky_jobs as jobs
    save_id = "00000000-0000-0000-0000-00000000000c"
    _write_manifest(_manifest(save_id))
    os.makedirs(store.jobs_dir(), exist_ok=True)
    store.write_json_atomic(jobs._job_path(save_id),
                            {"id": save_id, "kind": "save", "status": "paused",
                             "params": {}, "surveys": [DSS_COLOR], "total": 0,
                             "done": 0, "bytes": 0})
    with open(jobs._cancel_path(save_id), "w", encoding="ascii") as fh:
        fh.write("1")
    store.claim_for_job(save_id, f"{DSS_COLOR}/Norder3/Dir0/Npix1.jpg")

    assert retire_save_entries() == 1
    assert jobs.read_job(save_id) is None
    assert not os.path.exists(jobs._cancel_path(save_id))
    assert store.job_pins(save_id) == set()
    assert store.all_job_pins() == set()                # nothing left pinning tiles


def test_the_migration_leaves_the_base_pins_alone():
    """The base layer's pins are instance-level, not a save's: the migration
    releases what the manifests pinned and nothing else."""
    base_rel = "Norder3/Dir0/Npix1.jpg"
    save_rel = "Norder4/Dir0/Npix9.jpg"
    store_file(DSS_COLOR, base_rel, b"b" * 1000)
    store_file(DSS_COLOR, save_rel, b"s" * 2000)
    store.add_base_pins([f"{DSS_COLOR}/{base_rel}"])
    save_id = "00000000-0000-0000-0000-00000000000d"
    os.makedirs(store.jobs_dir(), exist_ok=True)
    store.claim_for_job(save_id, f"{DSS_COLOR}/{save_rel}")   # how a save pinned it
    _write_manifest(_manifest(save_id, paths=[f"{DSS_COLOR}/{save_rel}"]))
    store.refresh_pinned_bytes()
    assert store.pinned_bytes() == 3000                 # both tiles pinned before

    assert retire_save_entries() == 1

    # The base pin survives the migration, byte for byte...
    assert f"{DSS_COLOR}/{base_rel}" in store.base_pinned_rel_paths()
    assert store.pinned_bytes() == 1000                 # only the base tile now
    # ...and the save's tile is browsed again (still on disk, evictable).
    assert store.read_file(DSS_COLOR, save_rel) == b"s" * 2000
    assert store.total_bytes() == 2000


def test_eviction_never_evicts_a_base_pin_after_the_migration(monkeypatch):
    """End to end: after the migration, a tight cap evicts browsed tiles but
    leaves every base-pinned tile on disk."""
    base_rel = "Norder3/Dir0/Npix1.jpg"
    browsed = ["Norder3/Dir0/Npix2.jpg", "Norder3/Dir0/Npix3.jpg", "Norder3/Dir0/Npix4.jpg"]
    store_file(DSS_COLOR, base_rel, b"b" * 400 * 1024)
    for rel in browsed:
        store_file(DSS_COLOR, rel, b"s" * 400 * 1024)
    store.add_base_pins([f"{DSS_COLOR}/{base_rel}"])
    save_id = "00000000-0000-0000-0000-00000000000e"
    os.makedirs(store.jobs_dir(), exist_ok=True)
    store.claim_for_job(save_id, f"{DSS_COLOR}/{browsed[0]}")   # a save pinned one of them
    _write_manifest(_manifest(save_id, paths=[f"{DSS_COLOR}/{browsed[0]}"]))
    store.refresh_pinned_bytes()
    assert store.pinned_bytes() == 800 * 1024           # base + the save's tile

    assert retire_save_entries() == 1
    assert store.pinned_bytes() == 400 * 1024           # the save's tile released
    monkeypatch.setattr(store, "OFFLINE_SKY_CACHE_MB", 1)   # 1 MiB: must evict

    out = store.evict_browsed()

    assert out["removed"] >= 1                          # browsed tiles went
    assert out["pinned"] == 400 * 1024                  # the base pin is still counted
    assert store.read_file(DSS_COLOR, base_rel) is not None   # and still on disk
    # Exactly the pinned tile is kept; the rest of the browsed bytes were freed.
    assert out["total"] + out["pinned"] <= 1024 * 1024
