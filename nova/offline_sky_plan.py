"""Offline-sky download planning: the tile set for a survey, order and band.

A download covers orders 0..N intersected with the survey's MOC and the
declination band visible from the ticked locations. A tile counts when it
*overlaps* the band -- its centre decl plus or minus half its angular size --
not only when its centre falls inside. A centre-only test would drop the tiles
that straddle the band edge, leaving a ragged edge in the downloaded sky.

The band is the union of the visible bands of the ticked locations. A location
at latitude ``lat`` sees declinations from ``lat - 90`` to ``lat + 90`` (clamped
to the poles). The union of those ranges is a single interval, so a plan is
just "everything inside this band".

The plan is cached per ``(survey, order, band)`` in ``jobs/<id>.plan.json``, so
``status()`` never recomputes it. A re-plan on a detail or location change
keeps the same download id and replaces the plan file.

A tile the upstream does not have is *not* a hole the user can fill: the store
records every tile all upstreams answered 404 for in ``<slug>/.missing.json``,
and a plan counts those as done -- "not available on the server" -- so a survey
with genuine holes in its footprint can still reach "ready".
"""

import math
import os
import threading

from nova.config import BoundedCache
from nova.offline_sky_facts import allsky_rel, survey_coverage, survey_facts
import nova.offline_sky_store as store
import nova.hips_healpix as hp

# A tile's half-size at order o is half its angular side; the overlap test uses
# the pixel's own angular size, which is what "centre dec +- pixel radius" means.
_RADIUS_CACHE = {}

# Per-process cache of "the claim keys a band plans", keyed by
# (survey, order, band). A location's status needs its own band's tile set; the
# cache keeps repeated polls from re-scanning the sky. ``status()`` reads the
# cached plan, it never recomputes one. Bounded: the codebase rule for a
# process-wide cache, and the key space only grows when a user changes a
# detail level or ticks a location, so eight entries is plenty.
_PLAN_KEYS_CACHE = BoundedCache(8)
_PLAN_KEYS_LOCK = threading.Lock()


def _pixel_radius_deg(order):
    """Half the angular side of a tile at ``order``, in degrees."""
    radius = _RADIUS_CACHE.get(order)
    if radius is None:
        # The pixel's angular size is 4*pi/npix steradians; its linear side is
        # sqrt(4*pi/npix) radians. Half of that is the radius in degrees.
        npix = hp.npix_order(order)
        radius = math.degrees(math.sqrt(4.0 * math.pi / npix) / 2.0)
        _RADIUS_CACHE[order] = radius
    return radius


def visible_band(lat):
    """The declination range a location at ``lat`` can see, clamped to the poles."""
    if lat is None:
        return None
    return max(-90.0, float(lat) - 90.0), min(90.0, float(lat) + 90.0)


def union_band(lats):
    """The union of the visible bands of ``lats``, or None when there are none."""
    bands = [visible_band(lat) for lat in lats if lat is not None]
    if not bands:
        return None
    return min(lo for lo, _ in bands), max(hi for _, hi in bands)


def tile_overlaps_band(order, npix, band):
    """True when a tile's declination range overlaps ``band``."""
    if band is None:
        return False
    lo, hi = band
    nside = hp.order_to_nside(order)
    theta, _phi = hp.pix2ang_nest(nside, npix)
    dec = 90.0 - math.degrees(theta)
    radius = _pixel_radius_deg(order)
    return dec + radius >= lo and dec - radius <= hi


def plan_tiles(survey, order, band):
    """The rel paths of every tile in orders 0..N that overlap ``band``.

    Includes the Allsky tile, which every download owns. The order is clamped
    to the survey's effective maximum, so "Fine" on a survey capped at order 5
    simply plans order 5.
    """
    facts = survey_facts(survey)
    coverage = survey_coverage(survey)
    ext = facts.tile_ext
    order = min(int(order), facts.effective_max_order)
    paths = [allsky_rel(ext)]
    for o in range(order + 1):
        for npix in range(hp.npix_order(o)):
            if coverage.contains(o, npix) and tile_overlaps_band(o, npix, band):
                paths.append(store.tile_rel(o, npix, ext))
    return paths


def per_tile_bytes(survey, order):
    """The per-tile byte figure for ``order``, or None when the size is unknown.

    DSS2: the mean of that order's tiles on disk when there are at least
    ``store.MIN_SIZE_SAMPLE`` of them, otherwise the flat
    ``store.FALLBACK_TILE_BYTES``. No borrowing from a neighbouring order: low
    orders are not representative of high ones. NSNS has not been measured, so
    its figure is None -- the UI shows "size unknown" until step 4.
    """
    if survey.startswith("nsns"):
        return None
    averages = store.avg_tile_bytes_by_order(survey) or {}
    return int(averages.get(int(order), store.FALLBACK_TILE_BYTES))


def _rel_order(rel):
    """The order of a ``Norder<o>/...`` rel path or ``<slug>/Norder<o>/...`` key."""
    for part in rel.split("/"):
        if part.startswith("Norder"):
            try:
                return int(part[len("Norder"):])
            except ValueError:
                return None
    return None


def estimate_bytes(survey, rels):
    """Bytes for the grid tiles ``rels`` (rel paths or claim keys), or None.

    Summed per order: each tile is sized by its own order's figure
    (``per_tile_bytes``), so a Basic plan's orders 0..4 are not priced at
    order 5's average. Paths without an order (the Allsky tile) are skipped.
    """
    if survey.startswith("nsns"):
        return None
    counts = {}
    for rel in rels:
        order = _rel_order(rel)
        if order is None or "/Npix" not in rel:
            continue
        counts[order] = counts.get(order, 0) + 1
    return sum(per_tile_bytes(survey, o) * n for o, n in counts.items())


def planned_keys(survey, order, band):
    """The claim keys (``<slug>/<rel>``) a band plans, cached per band.

    ``status()`` calls this; it must never re-scan the sky on a poll. The cache
    is keyed by ``(survey, order, band)`` and only grows when the user changes
    the detail level or ticks another location -- both user actions, not polls.
    """
    key = (survey, int(order), None if band is None else (float(band[0]), float(band[1])))
    with _PLAN_KEYS_LOCK:
        cached = _PLAN_KEYS_CACHE.get(key)
    if cached is not None:
        return cached
    prefix = f"{survey}/"
    keys = {prefix + rel for rel in plan_tiles(survey, order, band)}
    with _PLAN_KEYS_LOCK:
        _PLAN_KEYS_CACHE[key] = keys
    return keys


def _location_status(survey, order, band, pinned, coverage, missing_upstream,
                     downloading=False):
    """One location's status: ready / missing X, plus the survey's coverage.

    ``ready``  -- every planned tile is PINNED AND ON DISK (browsed does not
                count; a pin whose file is absent does not count either) or
                recorded as "not available on the server" (a 404 from every
                upstream, in ``<slug>/.missing.json``). A tile the upstream does
                not have is not a hole the user can fill, so it must not block
                "ready"; ``not_available`` reports how many there are.
    ``missing`` -- some planned tiles are neither pinned nor recorded missing;
                ``missing_bytes`` is the bytes still to fetch. A tile already on
                disk is adopted by the run rather than fetched, so this is an
                upper bound.

    ``coverage`` is the fraction of this location's band the survey's MOC covers.
    It is reported *alongside* the state, never instead of it: a survey whose
    footprint ends south of the band edge is still "ready" for the part it does
    cover, and the UI reads "ready, 78% of this location's sky". ``partial``
    is no longer a state.

    While the survey's download job is active (``downloading``) the state is
    never "ready": the run may still be fetching or re-checking tiles.
    """
    planned = planned_keys(survey, order, band)
    # The Allsky tile is fetched but is not a grid tile; the plan's top-level
    # tile_count excludes it, so the per-location counts must too, or the two
    # disagree by exactly one (15268 vs 15269).
    allsky = f"{survey}/{allsky_rel(survey_facts(survey).tile_ext)}"
    planned = planned - {allsky}
    have = planned & pinned
    # A planned tile the upstream does not have counts as done. Intersect with
    # the plan so a missing record for a tile outside this band is ignored.
    unavailable = planned & missing_upstream
    done = have | unavailable
    missing = planned - done
    total = len(planned)
    missing_bytes = estimate_bytes(survey, missing)
    lo, hi = band
    covered, total_band = _band_coverage(coverage, lo, hi)
    fraction = (covered / total_band) if total_band else 0.0
    state = "ready" if total and not missing and not downloading else "missing"
    return {
        "state": state,
        "coverage": round(fraction, 4),
        "band": [lo, hi],
        "tile_count": total,
        "pinned_count": len(have),
        "not_available": len(unavailable),
        "missing_tiles": len(missing),
        "missing_bytes": missing_bytes,
    }


def plan_summary(survey, order, band, locations, include_tiles=False):
    """The plan's counts, bytes and per-location status for the API.

    ``tiles`` is the full rel-path list (15k+ entries for a Basic download).
    It is written to the plan file, but excluded from the API response by
    default: a 436 KB poll response would be re-sent on every refresh. Pass
    ``include_tiles=True`` when the caller needs the list itself.

    ``locations`` is the *located* selection (each entry carries ``lat``). Each
    gets its own status against its own band: a location in the south cannot be
    "ready" for a survey whose footprint never reaches it.
    """
    facts = survey_facts(survey)
    coverage = survey_coverage(survey)
    effective_order = min(int(order), facts.effective_max_order)
    tiles = plan_tiles(survey, effective_order, band)
    # The Allsky tile is not a grid tile; count and size only the grid tiles.
    grid_count = len(tiles) - 1
    total = estimate_bytes(survey, tiles)
    # "Pinned" is base tiles plus every download's claims, counted only when on
    # disk (pinned_on_disk_keys: no stat per poll). A browsed tile is on
    # disk but claimed by nobody, so it is *not* in this set -- which is what
    # decision 6 asks for: "ready" counts pinned tiles only, and a browsed tile
    # does not make a location ready. "missing" is therefore an upper bound: a
    # browsed tile the plan wants counts as missing here, and the download's run
    # adopts it without fetching (the run reports those as ``present``).
    # A tile the upstream does not have is recorded in the store's missing file;
    # it counts as done, so it never blocks "ready".
    pinned = store.pinned_on_disk_keys()
    from nova import offline_sky_jobs as jobs_mod
    job = jobs_mod.read_job(jobs_mod.download_job_id(survey))
    downloading = bool(job) and job.get("status") in jobs_mod.ACTIVE_STATUSES
    # The store records missing tiles as bare rel paths; the plan's claim keys
    # carry the survey prefix. Add it so the intersection in _location_status
    # matches.
    missing_upstream = {f"{survey}/{rel}" for rel in store.missing_rel_paths(survey)}
    per_location = []
    for loc in locations:
        lat = loc.get("lat")
        lb = visible_band(lat)
        if lb is None:
            per_location.append({**loc, "state": "no_band", "coverage": 0.0})
            continue
        status = _location_status(survey, effective_order, lb, pinned, coverage,
                                  missing_upstream, downloading)
        per_location.append({**loc, **status})
    summary = {
        "survey": survey,
        "order": effective_order,
        "max_order": facts.effective_max_order,
        "band": list(band) if band else None,
        "tile_count": grid_count,
        "size_bytes": total,
        "size_known": total is not None,
        "locations": per_location,
    }
    if include_tiles:
        summary["tiles"] = tiles
    return summary


def _band_coverage(coverage, lo, hi):
    """(covered, total) order-4 cells whose centre is in ``[lo, hi]`` and in the MOC."""
    order = 4
    nside = hp.order_to_nside(order)
    covered = 0
    total = 0
    for npix in range(hp.npix_order(order)):
        theta, _phi = hp.pix2ang_nest(nside, npix)
        dec = 90.0 - math.degrees(theta)
        if dec < lo or dec > hi:
            continue
        total += 1
        if coverage.contains(order, npix):
            covered += 1
    return covered, total


# ---------------------------------------------------------------------------
# The plan file
# ---------------------------------------------------------------------------

def plan_path(job_id):
    return os.path.join(store.jobs_dir(), f"{job_id}.plan.json")


def read_plan(job_id):
    return store.read_json(plan_path(job_id))


def write_plan(job_id, summary):
    os.makedirs(store.jobs_dir(), exist_ok=True)
    store.write_json_atomic(plan_path(job_id), summary)


def delete_plan(job_id):
    try:
        os.remove(plan_path(job_id))
    except OSError:
        pass
