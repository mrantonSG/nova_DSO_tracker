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
    read_file, store_file, touch, maybe_evict_async, tile_path,
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


def _rig_image_scale(uid: int, rig_id):
    """(arcsec/px, source) for the caller's rig; raises LimitError.

    Same computation as the framing page's rig list (nova/blueprints/core.py):
    206.265 x camera pixel size (um) / effective focal length (mm), with the
    effective focal length = telescope focal length x reducer/extender factor,
    components resolved by record UID. Falls back to the Rig.image_scale column
    (stored at rig save time) when the components can't give it.
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
        return float(scale), "components"
    if rig.image_scale and rig.image_scale > 0:
        return float(rig.image_scale), "stored"
    raise LimitError("rig_no_image_scale",
                     "this rig has no image scale (camera pixel size and focal length are needed)")


def _limit_response(err, status=400):
    body = {"error": str(err), "code": err.code}
    body.update(err.extra)
    return jsonify(body), status


def _frame_from(src):
    from nova.offline_sky_jobs import validate_frame
    return validate_frame(src.get("ra"), src.get("dec"),
                          src.get("frame_w_deg"), src.get("frame_h_deg"))


def _manifest_summary(manifest: dict) -> dict:
    """One save for the API.

    Saves written by the first step-2 version (before B1) have no
    ``detail_offset`` / frame size / ``partial`` and a job file without
    params, so their job can never run again. They are listed as ``legacy``
    (deletable, nothing else), a non-final status reads as "failed", and
    ``partial`` follows the status when the manifest doesn't say.

    ``legacy_unfinished``: a legacy save that is not ``done`` -- it can never
    run again and covers nothing (see ``store.save_covers``), so the list route
    hides it.
    """
    from nova.offline_sky_jobs import FINAL_STATUSES
    from nova.offline_sky_sources import SURVEY_ID_BY_SLUG
    surveys = manifest.get("surveys", [])
    legacy = "detail_offset" not in manifest
    status = manifest.get("status", "done")
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
        "created": manifest.get("created"),
        "bytes": manifest.get("bytes", 0),
        "tile_count": len(manifest.get("paths", [])),
        "missing": manifest.get("missing", 0),
        "partial": bool(manifest.get("partial", status != "done")),
        "status": status,
        "legacy": legacy,
        "legacy_unfinished": legacy and status != "done",
    }


@offline_sky_bp.route("/api/offline/saves", methods=["GET"])
def offline_saves_list():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_store import list_save_manifests
    saves = []
    for _uuid, manifest in list_save_manifests():
        if manifest.get("user_id") != uid:
            continue
        # An unfinished save from the first step-2 version can never run again
        # (no job params) and holds nothing: hide it rather than show a row that
        # can only be deleted. Its file stays, so a later version could still
        # read it.
        if _manifest_summary(manifest)["legacy_unfinished"]:
            continue
        saves.append(_manifest_summary(manifest))
    saves.sort(key=lambda s: s.get("created", ""), reverse=True)
    return jsonify({"saves": saves})


def _estimate_for(uid, src):
    """Shared by GET estimate and POST saves: (estimate dict) or raises LimitError."""
    from nova.offline_sky_jobs import LimitError, estimate, validate_offset
    from nova.offline_sky_sources import resolve_slugs
    frame = _frame_from(src)
    offset = validate_offset(src.get("detail_offset"))
    slugs = resolve_slugs(src.get("surveys") or [])
    if not slugs:
        raise LimitError("no_surveys", "no whitelisted surveys")
    scale, scale_source = _rig_image_scale(uid, src.get("rig_id"))
    est = estimate(frame, scale, slugs, offset, fetch=True)
    est["image_scale"] = {"arcsec_per_px": scale, "source": scale_source,
                          "rig_id": int(src.get("rig_id"))}
    return est


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
    save_id = str(_uuid.uuid4())
    manifest = {
        "id": save_id,
        "user_id": uid,
        "name": str(body.get("name") or "").strip() or "RA/Dec",
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
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paths": [],
        "bytes": 0,
        "status": "queued",
        "partial": True,
    }
    total = est["total"]
    params = {"ra": frame["ra"], "dec": frame["dec"], "radius_deg": frame["radius_deg"],
              "order": est["requested_order"],
              # What this save reserves under the cap / on disk until it has pinned it.
              "reserve_bytes": total["est_bytes"] + total["adopt_bytes"],
              "reserve_disk_bytes": total["est_bytes"]}
    # Final check + queueing in one step, so concurrent creates can't both fit.
    problem = admit_save(manifest, params, slugs,
                         params["reserve_bytes"], params["reserve_disk_bytes"])
    if problem:
        est.update(fits=False, reject=problem)
        return jsonify({"error": problem["message"], "code": problem["code"],
                        "estimate": est}), 409
    kick()
    return jsonify({"job_id": save_id, "manifest": _manifest_summary(manifest), "estimate": est})


def _owned_manifest(uid, save_id):
    from nova.offline_sky_store import read_save_manifest
    manifest = read_save_manifest(save_id)
    if not manifest or manifest.get("user_id") != uid:
        return None
    return manifest


@offline_sky_bp.route("/api/offline/saves/<save_id>/progress", methods=["GET"])
def offline_saves_progress(save_id: str):
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import ACTIVE_STATUSES, kick, read_job
    manifest = _owned_manifest(uid, save_id)
    if manifest is None:
        return Response(status=404)
    job = read_job(save_id)
    if job and job.get("status") in ACTIVE_STATUSES:
        kick()  # resume after a restart: whoever can take the lock runs it
    return jsonify({"job": job, "manifest": _manifest_summary(manifest)})


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
    try:
        return jsonify(_estimate_for(uid, args))
    except LimitError as err:
        return _limit_response(err)


@offline_sky_bp.route("/api/offline/status", methods=["GET"])
def offline_status():
    uid = _api_user_id()
    if uid is None:
        return Response(status=404)
    from nova.offline_sky_jobs import (
        LimitError, base_complete, cone_max_cached_order, target_order_for_scale)
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
        result[slug] = {
            "base_complete": base_complete(slug),
            "max_cached_order": cone_max_cached_order(slug, frame["ra"], frame["dec"],
                                                      frame["radius_deg"]),
            "target_order": (None if target is None
                             else min(target, effective_max_order(slug, fetch=False))),
        }
    return jsonify({"surveys": result, "survey_ids": {s: SURVEY_ID_BY_SLUG.get(s) for s in result}})
