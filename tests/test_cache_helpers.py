"""Unit tests for the cache helpers: curve busting, fingerprints, file locks,
old-file cleanup and BoundedCache eviction."""
import os
import sys
import time
import types

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nova.helpers as helpers
import nova.workers.heatmap as heatmap_worker
from nova.config import (
    BoundedCache, nightly_curves_cache, observable_objects_cache, astro_context_cache
)
from nova.helpers import (
    bust_object_curves, invalidate_object_caches, heatmap_fingerprint,
    try_acquire_file_lock, release_file_lock,
)


@pytest.fixture(autouse=True)
def clean_caches():
    """Start and end every test with empty module-level caches."""
    nightly_curves_cache.clear()
    observable_objects_cache.clear()
    astro_context_cache.clear()
    try:
        yield
    finally:
        nightly_curves_cache.clear()
        observable_objects_cache.clear()
        astro_context_cache.clear()


def _curve_key(username, object_name, date="2026-09-24", lat=50.0, lon=10.0,
               threshold=20, interval=15):
    # Same format as nova/__init__.py and nova/helpers.py build it
    return (f"{username}_{object_name.lower().replace(' ', '_')}_{date}"
            f"_{lat:.4f}_{lon:.4f}_{threshold}_{interval}")


# --- 1. bust_object_curves ---

def test_bust_object_curves_only_removes_matching_object():
    user, other = "alice", "bob"
    keys = {
        "ngc7000": [_curve_key(user, "NGC 7000"),
                    _curve_key(user, "NGC 7000", date="2026-09-25", lat=48.1234)],
        "ngc7000a": [_curve_key(user, "NGC 7000 A")],
        "m1": [_curve_key(user, "M1")],
        "m13": [_curve_key(user, "M13")],
        "other": [_curve_key(other, "NGC 7000")],
    }
    for group in keys.values():
        for k in group:
            nightly_curves_cache[k] = {"dummy": True}

    bust_object_curves(user, "NGC 7000")
    for k in keys["ngc7000"]:
        assert k not in nightly_curves_cache
    for group in ("ngc7000a", "m1", "m13", "other"):
        for k in keys[group]:
            assert k in nightly_curves_cache, f"{k} should survive NGC 7000 bust"

    bust_object_curves(user, "M1")
    for k in keys["m1"]:
        assert k not in nightly_curves_cache
    for group in ("ngc7000a", "m13", "other"):
        for k in keys[group]:
            assert k in nightly_curves_cache, f"{k} should survive M1 bust"


# --- 2. invalidate_object_caches ---

@pytest.mark.parametrize("object_names", [(), [], ["M1", "NGC 7000"]])
@pytest.mark.parametrize("curves", [False, True])
@pytest.mark.parametrize("outlook", [False, True])
def test_invalidate_object_caches_never_raises_on_empty(object_names, curves, outlook):
    assert len(nightly_curves_cache) == 0
    invalidate_object_caches(1, "alice", object_names, curves=curves, outlook=outlook)


# --- 3. heatmap_fingerprint ---

def _obj(name, ra=10.0, dec=20.0):
    return types.SimpleNamespace(
        object_name=name, ra_hours=ra, dec_deg=dec, common_name=f"{name} common",
        type="Galaxy", active_project=False, constellation="And",
        magnitude=8.0, size=10.0, sb=13.0,
    )


def _fp_args(**overrides):
    args = dict(
        location_id=1, lat=50.0, lon=10.0, tz_name="Europe/Berlin",
        horizon_mask=[[0, 10], [90, 15], [180, 5], [270, 20]],
        altitude_threshold=20, week_start_date="2026-09-21",
        objects=[_obj("M31"), _obj("M42", ra=5.58, dec=-5.4)],
    )
    args.update(overrides)
    return args


def test_heatmap_fingerprint_stable_for_identical_inputs():
    assert heatmap_fingerprint(**_fp_args()) == heatmap_fingerprint(**_fp_args())


def test_heatmap_fingerprint_ignores_mask_order():
    shuffled = [[270, 20], [0, 10], [180, 5], [90, 15]]
    assert heatmap_fingerprint(**_fp_args()) == heatmap_fingerprint(**_fp_args(horizon_mask=shuffled))


def test_heatmap_fingerprint_changes_with_inputs():
    base = heatmap_fingerprint(**_fp_args())
    assert heatmap_fingerprint(**_fp_args(altitude_threshold=25)) != base
    assert heatmap_fingerprint(**_fp_args(week_start_date="2026-09-28")) != base
    moved = [_obj("M31", ra=10.5), _obj("M42", ra=5.58, dec=-5.4)]
    assert heatmap_fingerprint(**_fp_args(objects=moved)) != base


# --- 4. try_acquire_file_lock / release_file_lock ---

@pytest.mark.skipif(not helpers._HAS_FCNTL, reason="fcntl not available")
def test_file_lock_acquire_block_release(tmp_path):
    target = str(tmp_path / "outlook_test.json")

    first = try_acquire_file_lock(target)
    assert first is not None
    try:
        assert try_acquire_file_lock(target) is None
    finally:
        release_file_lock(first)

    again = try_acquire_file_lock(target)
    assert again is not None
    release_file_lock(again)


# --- 5. _cleanup_old_cache_files ---

def test_cleanup_old_cache_files_only_removes_old_heatmap_and_outlook(tmp_path, monkeypatch):
    monkeypatch.setattr(heatmap_worker, "CACHE_DIR", str(tmp_path))

    four_days_ago = time.time() - 4 * 86400
    old_heatmap = tmp_path / "heatmap_old.json"
    old_outlook = tmp_path / "outlook_old.json"
    new_heatmap = tmp_path / "heatmap_new.json"
    telemetry = tmp_path / "telemetry_last.json"
    subdir = tmp_path / "heatmap_dir"

    for f in (old_heatmap, old_outlook, new_heatmap, telemetry):
        f.write_text("{}")
    subdir.mkdir()
    for p in (old_heatmap, old_outlook, telemetry, subdir):
        os.utime(p, (four_days_ago, four_days_ago))

    heatmap_worker._cleanup_old_cache_files()

    assert not old_heatmap.exists()
    assert not old_outlook.exists()
    assert new_heatmap.exists()
    assert telemetry.exists()
    assert subdir.is_dir()


# --- 6. BoundedCache ---

def test_bounded_cache_survives_manual_removal_and_stays_bounded():
    cache = BoundedCache(maxsize=10)
    for i in range(10):
        cache[f"k{i}"] = i
    assert len(cache) == 10

    del cache["k0"]

    for i in range(10, 40):
        cache[f"k{i}"] = i
        assert len(cache) <= 10
    assert "k39" in cache


class _GhostKeyCache(BoundedCache):
    """keys() reports a key that isn't stored, so eviction tries to remove it."""
    def keys(self):
        return ["ghost"] + list(super().keys())


def test_bounded_cache_eviction_tolerates_missing_key():
    # maxsize=20 -> each eviction takes 2 keys: "ghost" plus the oldest real one
    cache = _GhostKeyCache(maxsize=20)
    for i in range(50):
        cache[f"k{i}"] = i
    assert "ghost" not in cache
    assert len(cache) <= 20
    assert "k49" in cache
