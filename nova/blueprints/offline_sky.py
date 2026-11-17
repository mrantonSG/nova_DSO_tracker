"""Same-origin survey-tile proxy for the Framing Assistant.

When OFFLINE_SKY_STORE == "server", every survey tile Aladin requests is served
by this Nova server from /hips/<slug>/... and kept on disk under
instance/cache/hips (see nova/offline_sky_store.py). The browser never talks to
CDS/mirrors directly for a proxied survey, and the server-side whitelist in
nova/offline_sky_sources.py is the only thing that decides which upstreams may
be contacted -- there is no open proxy.

When OFFLINE_SKY_STORE != "server" every route returns 404, so the frontend's
store-off path (direct CDS/mirror, as before) is untouched.
"""

import os
import threading
import time

import requests
from flask import Blueprint, Response, g, jsonify, request, session
from flask_login import current_user

import nova  # module-qualified so runtime reads of nova.SINGLE_USER_MODE stay live (see nova/config.py)
from nova.config import APP_VERSION, OFFLINE_SKY_STORE, OFFLINE_SKY_SAVE_ENABLED
from nova.offline_sky_facts import allsky_rel, effective_max_order
from nova.offline_sky_sources import SURVEY_BY_SLUG, SURVEY_ID_BY_SLUG, SURVEY_SOURCES
from nova.offline_sky_store import (
    entry_match, read_file, save_covered_keys, store_file, touch, maybe_evict_async, tile_path,
)

offline_sky_bp = Blueprint("offline_sky", __name__)

# Own session: throttled, short timeouts, no redirects (a redirect could point
# at an arbitrary host -- the whitelist alone must decide where we go).
_session = requests.Session()
_session.headers["User-Agent"] = f"NovaDSOTracker/{APP_VERSION} (+https://nova-tracker.com)"
_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 6.0

# Concurrency guard: at most 6 in-flight upstream fetches per process. A CDS
# outage must not tie up all gthread workers (gunicorn -w 2 --threads 4) in
# blocking socket reads; requests queue for a slot (up to 15s) instead of failing.
# The circuit breaker below still skips a dead base without taking a slot, so a
# downed upstream can't pile up waiters.
_FETCH_SEMAPHORE = threading.BoundedSemaphore(6)
_FETCH_SEMAPHORE_TIMEOUT = 15.0

# Circuit breaker per upstream base: after a connect error/timeout/5xx, skip that
# base for this long. In-process only (each gunicorn worker keeps its own).
_BREAKER_SECONDS = 60.0
_breaker_lock = threading.Lock()
_breaker_until = {}

# A cached properties file is served from disk for this long before an upstream
# refresh is attempted again.
_PROPERTIES_MAX_AGE_SECONDS = 7 * 86400

_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

# Tiles are immutable; properties can change (mirrors, formats) so cache briefly.
_TILE_CACHE_CONTROL = "public, max-age=31536000, immutable"
_PROPERTIES_CACHE_CONTROL = "public, max-age=300"


class _UpstreamBusy(Exception):
    """No fetch slot free within _FETCH_SEMAPHORE_TIMEOUT; fail fast."""


def _enabled() -> bool:
    return OFFLINE_SKY_STORE == "server"


def _allowed() -> bool:
    """True when the store is on and the caller may use it.

    Single-user: open. Multi-user: a signed-in, non-guest user only. A guest is
    anonymous (no flask-login user; guest_entry only sets session['guest_mode']),
    so current_user.is_authenticated alone already excludes both guest and
    unauthenticated. No redirect, no provisioning, no prefs, no last-seen.
    """
    if not _enabled():
        return False
    if not nova.SINGLE_USER_MODE and not current_user.is_authenticated:
        return False
    return True


def _breaker_open(base: str) -> bool:
    with _breaker_lock:
        return time.time() < _breaker_until.get(base, 0.0)


def _breaker_trip(base: str) -> None:
    with _breaker_lock:
        _breaker_until[base] = time.time() + _BREAKER_SECONDS


# Outcomes of one upstream GET (see _upstream_get).
UPSTREAM_OK = "ok"                 # 200, body returned
UPSTREAM_MISSING = "missing"       # a definite "no such file" (4xx other than 429)
UPSTREAM_UNAVAILABLE = "unavailable"  # breaker open, network error, 5xx, 429: try later


def _upstream_get(base: str, url: str):
    """One GET against ``base`` -> (outcome, body_or_None). No semaphore.

    Circuit-breaks on a connection error/timeout/5xx; a 4xx is a real "no such
    tile" and does not trip the breaker (429 "slow down" is reported as
    unavailable without tripping it). An open breaker returns UNAVAILABLE
    without making a request. Never redirects. The offline-sky jobs call this
    directly under their own rate limit; the browsing proxy goes through
    _fetch_upstream, which adds the proxy's concurrency semaphore.
    """
    if _breaker_open(base):
        return UPSTREAM_UNAVAILABLE, None
    try:
        resp = _session.get(
            url,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            allow_redirects=False,
        )
    except requests.RequestException:
        _breaker_trip(base)
        return UPSTREAM_UNAVAILABLE, None
    if resp.status_code == 200:
        return UPSTREAM_OK, resp.content
    if resp.status_code >= 500:
        _breaker_trip(base)
        return UPSTREAM_UNAVAILABLE, None
    if resp.status_code == 429:
        return UPSTREAM_UNAVAILABLE, None
    return UPSTREAM_MISSING, None


def _fetch_upstream(base: str, url: str):
    """200-byte body from `base`, or None. Raises _UpstreamBusy when saturated.

    The browsing proxy's fetch: _upstream_get inside the proxy's own
    concurrency semaphore (an open breaker skips the base without a slot).
    """
    if _breaker_open(base):
        return None
    if not _FETCH_SEMAPHORE.acquire(timeout=_FETCH_SEMAPHORE_TIMEOUT):
        raise _UpstreamBusy()
    try:
        outcome, body = _upstream_get(base, url)
        return body if outcome == UPSTREAM_OK else None
    finally:
        _FETCH_SEMAPHORE.release()


def _strip_service_urls(raw: bytes) -> bytes:
    """Drop every hips_service_url* line so Aladin can't switch off /hips/.

    Properties lines are ``key = value``; the key is everything before ``=``.
    Blank lines, whitespace-only lines and ``#`` comments are kept verbatim.
    """
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        key = line.split("=", 1)[0].strip()
        if key.startswith("hips_service_url"):
            continue
        out.append(line)
    return ("\n".join(out) + "\n").encode("utf-8")


def _respond(data: bytes, mimetype: str, cache_control: str) -> Response:
    return Response(data, mimetype=mimetype, headers={"Cache-Control": cache_control})


def _tile_response(data: bytes, rel_path: str) -> Response:
    ext = rel_path.rsplit(".", 1)[-1]
    return _respond(data, _CONTENT_TYPES.get(ext, "application/octet-stream"),
                    _TILE_CACHE_CONTROL)


def _serve_tile(slug: str, rel_path: str, cache_only: bool = False) -> Response:
    data = read_file(slug, rel_path)
    if data is not None:
        touch(slug, rel_path)
        return _tile_response(data, rel_path)

    if cache_only:
        # Offline view: serve from disk only, never contact upstream.
        return Response(status=404)

    src = SURVEY_BY_SLUG[slug]
    for base in src.upstreams:
        try:
            raw = _fetch_upstream(base, f"{base.rstrip('/')}/{rel_path}")
        except _UpstreamBusy:
            break  # saturated: fail fast
        if raw is None:
            continue
        store_file(slug, rel_path, raw)
        maybe_evict_async()
        return _tile_response(raw, rel_path)
    return Response(status=404)


def _serve_properties(slug: str, cache_only: bool = False) -> Response:
    src = SURVEY_BY_SLUG[slug]
    cached = read_file(slug, "properties")
    if cached is not None:
        try:
            mtime = os.path.getmtime(tile_path(slug, "properties"))
        except OSError:
            mtime = 0.0
        if time.time() - mtime < _PROPERTIES_MAX_AGE_SECONDS:
            # Fresh: serve from disk, no upstream.
            return _respond(cached, "text/plain; charset=utf-8", _PROPERTIES_CACHE_CONTROL)
        if cache_only:
            # Offline view: a stale copy is still served from disk.
            return _respond(cached, "text/plain; charset=utf-8", _PROPERTIES_CACHE_CONTROL)
    elif cache_only:
        return Response(status=404)

    # No cache, or stale (older than 7 days): try upstreams first, re-store on
    # success; on failure fall back to the stale disk copy when there is one.
    for base in src.upstreams:
        try:
            raw = _fetch_upstream(base, f"{base.rstrip('/')}/properties")
        except _UpstreamBusy:
            break
        if raw is None:
            continue
        data = _strip_service_urls(raw)
        store_file(slug, "properties", data, count=False)
        return _respond(data, "text/plain; charset=utf-8", _PROPERTIES_CACHE_CONTROL)

    if cached is not None:
        return _respond(cached, "text/plain; charset=utf-8", _PROPERTIES_CACHE_CONTROL)
    return Response(status=503)


def _serve_moc(slug: str, cache_only: bool = False) -> Response:
    """MOC.fits: like properties (7-day stale refresh, no cap), but raw bytes
    (no service-URL stripping) and 404 when no upstream has it -- a MOC is
    optional metadata, not the survey itself."""
    src = SURVEY_BY_SLUG[slug]
    cached = read_file(slug, "Moc.fits")
    if cached is not None:
        try:
            mtime = os.path.getmtime(tile_path(slug, "Moc.fits"))
        except OSError:
            mtime = 0.0
        if time.time() - mtime < _PROPERTIES_MAX_AGE_SECONDS:
            return _respond(cached, "application/fits", _PROPERTIES_CACHE_CONTROL)
        if cache_only:
            return _respond(cached, "application/fits", _PROPERTIES_CACHE_CONTROL)
    elif cache_only:
        return Response(status=404)

    for base in src.upstreams:
        try:
            raw = _fetch_upstream(base, f"{base.rstrip('/')}/Moc.fits")
        except _UpstreamBusy:
            break
        if raw is None:
            continue
        store_file(slug, "Moc.fits", raw, count=False)
        return _respond(raw, "application/fits", _PROPERTIES_CACHE_CONTROL)

    if cached is not None:
        return _respond(cached, "application/fits", _PROPERTIES_CACHE_CONTROL)
    return Response(status=404)


def _validate_tile(slug: str, order: int, dir: int, npix: int, ext: str):
    """Return the rel path for a validated tile request, or None when invalid."""
    if slug not in SURVEY_BY_SLUG:
        return None
    src = SURVEY_BY_SLUG[slug]
    ext_l = ext.lower()
    if ext_l not in src.exts:
        return None
    # Effective max order: the lower of the whitelist and the survey's own
    # hips_order (cached properties only; never fetched on a tile request).
    if order > effective_max_order(slug, fetch=False):
        return None
    if npix >= 12 * 4 ** order:
        return None
    if dir != (npix // 10000) * 10000:
        return None
    return f"Norder{order}/Dir{dir}/Npix{npix}.{ext_l}"


@offline_sky_bp.route("/hips/<slug>/properties")
def hips_properties(slug: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    return _serve_properties(slug)


@offline_sky_bp.route("/hips/<slug>/Moc.fits")
def hips_moc(slug: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    return _serve_moc(slug)


# HiPS 1.0 keeps the Allsky preview in Norder3/ (what Aladin requests); there is
# no Allsky at the survey root, so no root route.
@offline_sky_bp.route("/hips/<slug>/Norder3/Allsky.<string:ext>")
def hips_allsky(slug: str, ext: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    ext_l = ext.lower()
    if ext_l not in SURVEY_BY_SLUG[slug].exts:
        return Response(status=404)
    return _serve_tile(slug, allsky_rel(ext_l))


@offline_sky_bp.route("/hips/<slug>/Norder<int:order>/Dir<int:dir>/Npix<int:npix>.<string:ext>")
def hips_tile(slug: str, order: int, dir: int, npix: int, ext: str):
    if not _allowed():
        return Response(status=404)
    rel = _validate_tile(slug, order, dir, npix, ext)
    if rel is None:
        return Response(status=404)
    return _serve_tile(slug, rel)


# --- Cache-only variants (offline view). Same validation and disk layout, but a
# miss is a 404 and upstream is never contacted. ---

@offline_sky_bp.route("/hips/offline/<slug>/properties")
def hips_offline_properties(slug: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    return _serve_properties(slug, cache_only=True)


@offline_sky_bp.route("/hips/offline/<slug>/Moc.fits")
def hips_offline_moc(slug: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    return _serve_moc(slug, cache_only=True)


@offline_sky_bp.route("/hips/offline/<slug>/Norder3/Allsky.<string:ext>")
def hips_offline_allsky(slug: str, ext: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    ext_l = ext.lower()
    if ext_l not in SURVEY_BY_SLUG[slug].exts:
        return Response(status=404)
    return _serve_tile(slug, allsky_rel(ext_l), cache_only=True)


@offline_sky_bp.route("/hips/offline/<slug>/Norder<int:order>/Dir<int:dir>/Npix<int:npix>.<string:ext>")
def hips_offline_tile(slug: str, order: int, dir: int, npix: int, ext: str):
    if not _allowed():
        return Response(status=404)
    rel = _validate_tile(slug, order, dir, npix, ext)
    if rel is None:
        return Response(status=404)
    return _serve_tile(slug, rel, cache_only=True)


# ---------------------------------------------------------------------------
# Offline-sky user API (saves, jobs, status).
#
# These live under /api/offline/ (not /hips/), so they go through the normal
# request context: g.db_user is set, and multi-user auth/guest rules apply.
# ---------------------------------------------------------------------------

def _api_user_id():
    """The DB user id for the caller, or None when not permitted.

    Single-user: the default user. Multi-user: a signed-in, non-guest user only.
    None (every /api/offline/* route 404s) while OFFLINE_SKY_SAVE_ENABLED is off.
    """
    if not _enabled() or not OFFLINE_SKY_SAVE_ENABLED:
        return None
    if not nova.SINGLE_USER_MODE and not current_user.is_authenticated:
        return None
    db_user = getattr(g, "db_user", None)
    return db_user.id if db_user is not None else None


def _rig_scale(uid: int, rig_id):
    """(arcsec/px, source, rig) for the caller's rig; raises LimitError.

    Same computation as the framing page's rig list (nova/blueprints/core.py):
    206.265 x camera pixel size (um) / effective focal length (mm), with the
    effective focal length = telescope focal length x reducer/extender factor,
    components resolved by record UID. Falls back to the Rig.image_scale column
    (stored at rig save time) when the components can't give it.

    The rig row comes back too, so a save can record which rig it was made for.
    """
    from nova.helpers import get_db, _compute_rig_metrics_from_components
    from nova.models import Rig
    from nova.offline_sky_jobs import LimitError
    from nova.record_links import components_for_rig
    try:
        rid = int(rig_id)
    except (TypeError, ValueError):
        raise LimitError("rig_required", "rig_id is required")
    db = get_db()
    rig = db.query(Rig).filter_by(id=rid, user_id=uid).one_or_none()
    if rig is None:
        raise LimitError("rig_not_found", "rig not found")
    rc = components_for_rig(db, rig)
    _efl, _fr, scale, _fov = _compute_rig_metrics_from_components(
        rc.telescope, rc.camera, rc.reducer_extender)
    if scale and scale > 0:
        return float(scale), "components", rig
    if rig.image_scale and rig.image_scale > 0:
        return float(rig.image_scale), "stored", rig
    raise LimitError("rig_no_image_scale",
                     "this rig has no image scale (camera pixel size and focal length are needed)")


def _rig_image_scale(uid: int, rig_id):
    """(arcsec/px, source) -- _rig_scale without the rig row."""
    scale, source, _rig = _rig_scale(uid, rig_id)
    return scale, source


def _limit_response(err, status=400):
    body = {"error": str(err), "code": err.code}
    body.update(err.extra)
    return jsonify(body), status


def _frame_from(src):
    from nova.offline_sky_jobs import validate_frame
    return validate_frame(src.get("ra"), src.get("dec"),
                          src.get("frame_w_deg"), src.get("frame_h_deg"))


def _save_status(manifest: dict):
    """A save's own status: what its job says, else what its file says.

    The job is the authority while it exists -- it is the thing that knows
    whether tiles are still being fetched.
    """
    from nova.offline_sky_jobs import read_job
    job = read_job(manifest.get("id"))
    return (job or {}).get("status") or manifest.get("status")


def _entry_status(manifest: dict, saved=()) -> str:
    """One status for an entry, from the saves that make it up.

    An entry is as far along as its least-advanced save: while any of its jobs
    can still run the entry stays active (so the panel keeps polling), and
    otherwise anything that stopped short keeps it from reading as ``done``.
    """
    from nova.offline_sky_jobs import ACTIVE_STATUSES
    order = ("queued", "running", "paused")
    saves = [manifest] + [s for s in saved if s.get("id") != manifest.get("id")]
    statuses = [s for s in (_save_status(m) for m in saves) if s]
    active = [s for s in statuses if s in ACTIVE_STATUSES]
    if active:
        return order[min(order.index(s) for s in active)]
    for status in statuses:
        if status != "done":
            return status
    return "done" if statuses else None


def _entry_summary(manifest: dict) -> dict:
    """One entry for the API: the merged manifest in the shape the panel reads.

    An entry is a manifest file like any other; ``merged_saves`` names every save
    folded into it (see ``store.merge_into_entry``). ``status`` here is the
    manifest's own; the routes that list or poll entries replace it with
    ``_entry_status``, which also looks at each save's job.
    """
    from nova.offline_sky_jobs import FINAL_STATUSES
    from nova.offline_sky_sources import SURVEY_ID_BY_SLUG
    surveys = manifest.get("surveys", [])
    legacy = "detail_offset" not in manifest
    status = manifest.get("status") or ("failed" if legacy else "done")
    if legacy and status not in FINAL_STATUSES:
        status = "failed"
    return {
        "id": manifest.get("id"),
        "name": manifest.get("name"),
        "ra": manifest.get("ra"),
        "dec": manifest.get("dec"),
        "radius_deg": manifest.get("radius_deg"),
        "frame_w_deg": manifest.get("frame_w_deg"),
        "frame_h_deg": manifest.get("frame_h_deg"),
        "surveys": surveys,
        "survey_ids": [SURVEY_ID_BY_SLUG.get(s) for s in surveys],
        "max_order": manifest.get("max_order"),
        "target_order": manifest.get("target_order"),
        "detail_offset": manifest.get("detail_offset"),
        "image_scale_arcsec": manifest.get("image_scale_arcsec"),
        "rig_id": manifest.get("rig_id"),
        "rig_name": manifest.get("rig_name"),
        "created": manifest.get("created"),
        "bytes": manifest.get("bytes", 0),
        "tile_count": len(save_covered_keys(manifest)),
        "missing": manifest.get("missing", 0),
        "partial": bool(manifest.get("partial", status != "done")),
        "status": status,
        "legacy": legacy,
        "legacy_unfinished": legacy and status != "done",
        "save_ids": list(manifest.get("merged_saves") or []),
    }


def _manifest_summary(manifest: dict) -> dict:
    """One save's manifest as the API's entry shape (see ``_entry_summary``)."""
    return _entry_summary(manifest)


def _tracked_entries(uid):
    """[(entry manifest, [save manifests])] for one user's live entries.

    A save that was folded into another entry (``entry_id`` naming a different
    file) is recorded, not listed: the entry is its row. A standalone save is its
    own entry. A save whose entry has been deleted stops matching anything and is
    ignored -- which is what keeps the list showing exactly what exists.
    """
    from nova.offline_sky_store import list_save_manifests
    manifests = [m for _uuid, m in list_save_manifests() if m.get("user_id") == uid]
    entries = {}
    folded = {}       # entry id -> [save manifests]
    for manifest in manifests:
        owner = manifest.get("entry_id") or manifest.get("id")
        if owner != manifest.get("id"):
            folded.setdefault(owner, []).append(manifest)
            continue
        entries[manifest.get("id")] = manifest
    records = []
    for entry_id, entry in entries.items():
        records.append((entry, folded.get(entry_id, [])))
    return records


def _entry_records(uid):
    """The list-relevant records: hide an unfinished save from before B1 (it can
    never run again and covers nothing), keep everything else."""
    out = []
    for manifest, saved in _tracked_entries(uid):
        summary = _entry_summary(manifest)
        if summary["legacy_unfinished"] and manifest.get("status") == "cancelled":
            continue
        out.append((manifest, saved, summary))
    return out


@offline_sky_bp.route("/api/offline/saves", methods=["GET"])
def offline_saves_list():
    """The caller's entries, for one object when ``?name=`` is given.

    ``others`` counts entries for the caller's other objects, so the panel can
    say how many are saved elsewhere without fetching them.
    """
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    wanted = request.args.get("name")
    saves, others = [], 0
    for manifest, saved, summary in _entry_records(uid):
        if wanted is not None and summary["name"] != wanted:
            others += 1
            continue
        summary["status"] = _entry_status(manifest, saved)
        summary["partial"] = bool(summary["partial"] or summary["status"] != "done")
        summary["missing"] = max([summary["missing"]] + [s.get("missing", 0) for s in saved])
        saves.append(summary)
    saves.sort(key=lambda s: s.get("created", ""), reverse=True)
    return jsonify({"saves": saves, "others": others})


def _estimate_for(uid, src, fetch=True):
    """Shared by GET estimate and POST saves: (estimate dict) or raises LimitError.

    ``fetch``: read survey metadata from disk only. The panel's first call for a
    frame asks with ``fetch=False``, so it answers immediately even when a
    survey's properties are stale; the panel then asks again with the default and
    updates in place. Nothing is planned beyond what a cached file allows.
    """
    from nova.offline_sky_jobs import LimitError, estimate, validate_offset
    from nova.offline_sky_sources import resolve_slugs
    frame = _frame_from(src)
    offset = validate_offset(src.get("detail_offset"))
    slugs = resolve_slugs(src.get("surveys") or [])
    if not slugs:
        raise LimitError("no_surveys", "no whitelisted surveys")
    scale, scale_source, rig = _rig_scale(uid, src.get("rig_id"))
    est = estimate(frame, scale, slugs, offset, fetch=fetch)
    est["image_scale"] = {"arcsec_per_px": scale, "source": scale_source,
                          "rig_id": int(src.get("rig_id")), "rig_name": rig.rig_name}
    est["provisional"] = (not fetch) and _provisional(est)
    return est


def _provisional(est) -> bool:
    """True when this estimate leaned on a fallback because a file was not read.

    A survey whose MOC could not be read is planned as the whole sky; a survey
    whose properties were missing is described from the whitelist. Both make the
    numbers change once the real file is read, so the panel says so instead of
    showing them as final.
    """
    from nova.offline_sky_facts import survey_facts
    for survey in est.get("surveys", []):
        reason = (survey.get("coverage") or {}).get("reason") or ""
        if "assuming full sky" in reason or "no MOC" in reason:
            return True
        try:
            if not survey_facts(survey["slug"], fetch=False).properties_found:
                return True
        except Exception:
            return True
    return False


@offline_sky_bp.route("/api/offline/saves", methods=["POST"])
def offline_saves_create():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    import uuid as _uuid
    from nova.offline_sky_jobs import LimitError, admit_save, kick

    body = request.get_json(silent=True) or {}
    try:
        est = _estimate_for(uid, body)
    except LimitError as err:
        return _limit_response(err)
    if est["reject"]:
        return jsonify({"error": est["reject"]["message"], "code": est["reject"]["code"],
                        "estimate": est}), 409

    frame = est["frame"]
    slugs = [s["slug"] for s in est["surveys"]]
    name = str(body.get("name") or "").strip() or "RA/Dec"
    rig_id = est["image_scale"]["rig_id"]
    rig_name = est["image_scale"].get("rig_name")
    total = est["total"]
    params = {"ra": frame["ra"], "dec": frame["dec"], "radius_deg": frame["radius_deg"],
              "order": est["requested_order"],
              # What this save reserves under the cap / on disk until it has pinned it.
              "reserve_bytes": total["est_bytes"] + total["adopt_bytes"],
              "reserve_disk_bytes": total["est_bytes"]}

    # The entry this save belongs to: same user, same object, same rig (or, for
    # entries from before rigs were recorded, the same frame). Saving the same
    # object for the same rig again adds surveys to that entry instead of making
    # a second row.
    probe = {"user_id": uid, "name": name, "rig_id": rig_id, "ra": frame["ra"],
             "dec": frame["dec"], "frame_w_deg": frame["frame_w_deg"],
             "frame_h_deg": frame["frame_h_deg"]}
    entries = [m for m, _saved, _sum in _entry_records(uid)]
    target = entry_match(probe, entries)

    if target is not None:
        added = [s for s in slugs if s not in set(target.get("surveys") or [])]
        # Nothing new to download and no survey to add: refuse, rather than merge
        # a no-op. No manifest is touched and no job is created, so the panel can
        # show "Already saved" and Save stays disabled.
        if not added:
            want = _covered_keys_for(frame, est, slugs)
            if not (want - save_covered_keys(target)):
                return jsonify({"error": "already saved", "code": "nothing_new",
                                "save_id": target.get("id"), "estimate": est}), 409
        # Every job this POST creates is recorded on the entry under the pin
        # lock, so the runner can fold each one in without losing the others.
        save_id = str(_uuid.uuid4())
        return _admit_into_entry(target, save_id, slugs, params, uid, name, frame,
                                 est, rig_id, added, rig_name)

    save_id = str(_uuid.uuid4())
    manifest = {
        "id": save_id,
        "user_id": uid,
        "name": name,
        "ra": frame["ra"],
        "dec": frame["dec"],
        "frame_w_deg": frame["frame_w_deg"],
        "frame_h_deg": frame["frame_h_deg"],
        "radius_deg": frame["radius_deg"],
        "surveys": slugs,
        "max_order": est["requested_order"],
        "target_order": est["target_order"],
        "detail_offset": est["detail_offset"],
        "image_scale_arcsec": est["image_scale_arcsec"],
        "rig_id": rig_id,
        "rig_name": rig_name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paths": [],
        "bytes": 0,
        "status": "queued",
        "partial": True,
        "merged_saves": [save_id],
    }
    # Final check + queueing in one step, so concurrent creates can't both fit.
    problem = admit_save(manifest, params, slugs,
                         params["reserve_bytes"], params["reserve_disk_bytes"])
    if problem:
        est.update(fits=False, reject=problem)
        return jsonify({"error": problem["message"], "code": problem["code"],
                        "estimate": est}), 409
    kick()
    return jsonify({"job_id": save_id, "manifest": _manifest_summary(manifest), "estimate": est})


def _covered_keys_for(frame, est, slugs) -> set:
    """Every tile this save would cover, as "<slug>/<rel>" (no upstream calls).

    Used to tell "already saved" from "a few tiles are still missing": the same
    plan the job will fetch, so the two agree to the tile.
    """
    from nova.offline_sky_jobs import plan_survey
    keys = set()
    for slug in slugs:
        plan = plan_survey(slug, frame["ra"], frame["dec"], frame["radius_deg"],
                           est["requested_order"], fetch=False)
        keys |= {f"{slug}/{rel}" for rel in plan.rel_paths()}
    return keys


def _admit_into_entry(entry, save_id, slugs, params, uid, name, frame, est, rig_id, added,
                      rig_name=None):
    """Queue this save as part of an existing entry.

    One job, for the surveys being added (what the entry already covers needs no
    new tiles). The entry's save-id list is extended under the pin lock, so two
    concurrent creates both end up recorded and neither job loses the other's
    tiles.
    """
    from nova.offline_sky_jobs import admit_save, kick
    from nova.offline_sky_store import (add_entry_saves, merge_into_entry, read_save_manifest,
                                        write_save_manifest)
    manifest = {
        "id": save_id, "user_id": uid, "name": name,
        "ra": frame["ra"], "dec": frame["dec"],
        "frame_w_deg": frame["frame_w_deg"], "frame_h_deg": frame["frame_h_deg"],
        "radius_deg": frame["radius_deg"],
        "surveys": added or slugs,
        "max_order": est["requested_order"], "target_order": est["target_order"],
        "detail_offset": est["detail_offset"],
        "image_scale_arcsec": est["image_scale_arcsec"], "rig_id": rig_id,
        "rig_name": rig_name, "entry_id": entry.get("id"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paths": [], "bytes": 0, "status": "queued", "partial": True,
    }
    problem = admit_save(manifest, params, added or slugs,
                         params["reserve_bytes"], params["reserve_disk_bytes"])
    if problem:
        est.update(fits=False, reject=problem)
        return jsonify({"error": problem["message"], "code": problem["code"],
                        "estimate": est}), 409
    add_entry_saves(entry.get("id"), [save_id])
    # Fold the new save into the entry now, so the list shows the merged surveys
    # straight away (the job adds its tiles when it finishes).
    fresh = read_save_manifest(entry.get("id")) or entry
    merge_into_entry(fresh, manifest)
    write_save_manifest(fresh)
    kick()
    return jsonify({"job_id": save_id, "save_id": save_id, "entry_id": entry.get("id"),
                    "manifest": _entry_summary(fresh), "estimate": est})


def _owned_manifest(uid, save_id):
    from nova.offline_sky_store import read_save_manifest
    manifest = read_save_manifest(save_id)
    if not manifest or manifest.get("user_id") != uid:
        return None
    return manifest


def _entry_for_save(uid, save_id):
    """(entry manifest, [save manifests]) for the entry a save belongs to."""
    for manifest, saved, _summary in _entry_records(uid):
        if manifest.get("id") == save_id or save_id in (manifest.get("merged_saves") or []):
            return manifest, saved
    return None, []


@offline_sky_bp.route("/api/offline/saves/<save_id>/progress", methods=["GET"])
def offline_saves_progress(save_id: str):
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import ACTIVE_STATUSES, kick, read_job
    # A save merged into an entry has its own file, but the row is the entry, so
    # answer about the entry and let the caller's id find it either way.
    entry, saved = _entry_for_save(uid, save_id)
    row_id = entry.get("id") if entry else save_id
    manifest = _owned_manifest(uid, row_id)
    if manifest is None:
        return Response(status=404)
    job = read_job(save_id)
    if job is None and entry:
        # The entry answers for whichever of its saves is still running (the
        # latest one asked for), so the row keeps showing progress after a merge.
        for other in reversed(saved):
            job = read_job(other.get("id"))
            if job:
                break
    if job and job.get("status") in ACTIVE_STATUSES:
        kick()  # resume after a restart: whoever can take the lock runs it
    summary = _manifest_summary(manifest)
    if entry and not summary["legacy"]:
        # An entry is as far along as its least-advanced save -- the panel polls
        # on that, so a merged entry whose newest job runs still reads active.
        summary["status"] = _entry_status(manifest, saved)
        summary["partial"] = bool(summary["partial"] or summary["status"] != "done")
    summary["id"] = row_id
    return jsonify({"job": job, "manifest": summary})


@offline_sky_bp.route("/api/offline/saves/<save_id>/cancel", methods=["POST"])
def offline_saves_cancel(save_id: str):
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import cancel_job, kick
    if _owned_manifest(uid, save_id) is None:
        return Response(status=404)
    cancelled = cancel_job(save_id)
    if cancelled:
        kick()
    return jsonify({"cancelled": cancelled})


@offline_sky_bp.route("/api/offline/saves/<save_id>", methods=["DELETE"])
def offline_saves_delete(save_id: str):
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import cancel_job, delete_job_file, kick
    from nova.offline_sky_store import delete_save_manifest, maybe_evict_async
    if _owned_manifest(uid, save_id) is None:
        return Response(status=404)
    # A running job is the runner's to finish: ask it to stop, then drop the
    # manifest (the runner sees it gone and won't recreate it).
    active = cancel_job(save_id)
    delete_save_manifest(save_id)
    if active:
        kick()
    else:
        delete_job_file(save_id)
    maybe_evict_async()
    return jsonify({"deleted": True})


@offline_sky_bp.route("/api/offline/base/ensure", methods=["POST"])
def offline_base_ensure():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import ensure_base_survey, kick
    from nova.offline_sky_sources import resolve_slugs
    slugs = resolve_slugs((request.get_json(silent=True) or {}).get("surveys") or [])
    out = [ensure_base_survey(slug) for slug in slugs]
    if any(not s.get("complete") for s in out):
        kick()
    return jsonify({"surveys": out})


@offline_sky_bp.route("/api/offline/base/status", methods=["GET"])
def offline_base_status():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import base_complete
    return jsonify({"surveys": {s.slug: {"complete": base_complete(s.slug)}
                                for s in SURVEY_SOURCES.values()}})


@offline_sky_bp.route("/api/offline/estimate", methods=["GET"])
def offline_estimate():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import LimitError
    args = request.args.to_dict()
    args["surveys"] = request.args.get("surveys", "")
    fetch = request.args.get("fetch", "1") not in ("0", "false", "no")
    try:
        return jsonify(_estimate_for(uid, args, fetch=fetch))
    except LimitError as err:
        return _limit_response(err)


@offline_sky_bp.route("/api/offline/status", methods=["GET"])
def offline_status():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import (
        LimitError, base_complete, covered_pinned_order, target_order_for_scale)
    from nova.offline_sky_facts import effective_max_order
    from nova.offline_sky_sources import resolve_slugs
    try:
        frame = _frame_from(request.args)
    except LimitError as err:
        return _limit_response(err)
    target = None
    if request.args.get("rig_id"):
        try:
            scale, _source = _rig_image_scale(uid, request.args.get("rig_id"))
            target = target_order_for_scale(scale)
        except LimitError:
            target = None
    result = {}
    for slug in resolve_slugs(request.args.get("surveys", "")):
        pinned_order = covered_pinned_order(slug, frame["ra"], frame["dec"],
                                            frame["radius_deg"])
        result[slug] = {
            # Kept for information only: the indicator does not read it. A save
            # pins the tiles for its own frame, so the base layer being partial
            # says nothing about this frame.
            "base_complete": base_complete(slug),
            # What survives with the network down: pinned, not merely cached.
            "max_pinned_order": pinned_order,
            "target_order": (None if target is None
                             else min(target, effective_max_order(slug, fetch=False))),
        }
    return jsonify({"surveys": result, "survey_ids": {s: SURVEY_ID_BY_SLUG.get(s) for s in result}})
