"""Same-origin survey-tile proxy for the Framing Assistant.

When OFFLINE_SKY_STORE == "server", every survey tile Aladin requests is served
by this Nova server from /hips/<slug>/... and kept on disk under the store
directory (OFFLINE_SKY_DIR, default instance/cache/hips -- see
nova/offline_sky_store.py). The browser never talks to CDS/mirrors directly for
a proxied survey, and the server-side whitelist in nova/offline_sky_sources.py is
the only thing that decides which upstreams may be contacted -- there is no open
proxy.

When OFFLINE_SKY_STORE != "server" every route returns 404, so the frontend's
store-off path (direct CDS/mirror, as before) is untouched.

The cache-only /hips/offline/<slug>/... variants serve from disk and never
contact upstream: that is what the Framing tab's Offline view reads.

The Configuration -> General "Offline sky" section is served by the small JSON
API at the bottom of this file (/api/offline-sky/...): the store's usage, the
limit the user picks, and clearing the viewed tiles. Those routes share the
store with the tile proxy -- same directory, same locking -- which is why they
live on this blueprint rather than next to the config page.
"""

import os
import threading
import time

import requests
from flask import Blueprint, Response, jsonify, request
from flask_login import current_user

import nova  # module-qualified so runtime reads of nova.SINGLE_USER_MODE stay live (see nova/config.py)
import nova.offline_sky_store as store
from nova.config import ADMIN_USERS, APP_VERSION, OFFLINE_SKY_STORE
from nova.offline_sky_facts import allsky_rel, effective_max_order
from nova.offline_sky_sources import SURVEY_BY_SLUG
from nova.offline_sky_store import (
    read_file, store_file, touch, maybe_evict_async, tile_path,
    clear_now, evict_now, set_limit_mb, status as store_status,
    with_evict_lock, downloads_available, budget_bytes, pinned_bytes,
    read_selection, write_selection, job_pins, delete_tiles,
    refresh_pinned_bytes,
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


def _is_admin() -> bool:
    """True when the caller may change instance-wide store settings.

    Single-user: the one user is the owner. Multi-user: ADMIN_USERS only -- the
    sky store is shared by every account on the instance, so its limit and its
    clear button are not a per-user preference. Same rule as the admin blueprint
    and the telemetry diagnostics route.
    """
    if nova.SINGLE_USER_MODE:
        return True
    return (current_user.is_authenticated
            and (current_user.username or "") in ADMIN_USERS)


def storage_available() -> bool:
    """True when this request may see *and* use the storage section.

    The one rule, used by the routes and by the Configuration page when it
    decides whether to render the section -- so the section is shown exactly
    when the API behind it would answer. Kept separate from ``_storage_guard``,
    which must tell the two refusals apart (404 vs 403).

    The tile store decides it, not ``OFFLINE_SKY_UI``: that flag only shows or
    hides the Framing tab's Offline view switch, while the store exists (and is
    worth a limit and a Clear button) whenever this server keeps tiles on disk.
    """
    return _enabled() and _is_admin()


def _storage_guard():
    """None when the caller may use the storage API, else a response.

    A store that is off has no sky storage at all, so that answers 404 -- "no
    such thing here". A store that is on and a caller who may not run it (a
    multi-user non-admin) answers 403, the same refusal as every other
    admin-only route. ``OFFLINE_SKY_UI`` plays no part here: it gates the
    Framing tab's Offline view switch, not the store.
    """
    if not _enabled():
        return jsonify({"error": "not found"}), 404
    if not _is_admin():
        return jsonify({"error": "Not authorized"}), 403
    return None


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

    A connect timeout trips the breaker on the spot, like every other network
    error: a host that does not answer costs one _CONNECT_TIMEOUT, after which
    its tiles skip it for _BREAKER_SECONDS without a request.
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
    """Disk first; online, a miss is fetched from the survey's upstreams in order.

    A tile no upstream delivers is a 404, whatever the reason: the layer shows
    nothing there, and the next request for it asks again.
    """
    data = read_file(slug, rel_path)
    if data is not None:
        touch(slug, rel_path)
        return _tile_response(data, rel_path)

    if cache_only:
        # Offline view: serve from disk only, never contact upstream.
        return Response(status=404)

    for base in SURVEY_BY_SLUG[slug].upstreams:
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


def _builtin_properties(slug: str) -> bytes:
    """The properties a survey's layer is built from when no copy is on disk and
    no upstream answers: what Nova itself knows about it (the whitelist). Never
    stored, so the first real copy replaces it.

    With it /properties always answers, so a layer never fails to build -- in
    Offline view or online: tiles that are not there simply stay empty.
    """
    src = SURVEY_BY_SLUG[slug]
    fmt = "png" if slug.startswith("nsns-") else "jpeg"
    return (
        f"creator_did          = nova://builtin/{slug}\n"
        f"obs_title            = {src.label}\n"
        f"dataproduct_type     = image\n"
        f"hips_frame           = equatorial\n"
        f"hips_order           = {src.max_order}\n"
        f"hips_order_min       = 0\n"
        f"hips_tile_width      = 512\n"
        f"hips_tile_format     = {fmt}\n"
    ).encode("utf-8")


def _serve_properties(slug: str, cache_only: bool = False) -> Response:
    src = SURVEY_BY_SLUG[slug]
    cached = read_file(slug, "properties")
    if cached is not None:
        try:
            mtime = os.path.getmtime(tile_path(slug, "properties"))
        except OSError:
            mtime = 0.0
        if cache_only or time.time() - mtime < _PROPERTIES_MAX_AGE_SECONDS:
            # Fresh -- or Offline view, which serves even a stale copy from disk.
            return _respond(cached, "text/plain", _PROPERTIES_CACHE_CONTROL)
    elif cache_only:
        return _respond(_builtin_properties(slug), "text/plain", "no-store")

    # No cache, or stale (older than 7 days): try upstreams first, re-store on
    # success; on failure fall back to the stale disk copy, else the built-in one.
    for base in src.upstreams:
        try:
            raw = _fetch_upstream(base, f"{base.rstrip('/')}/properties")
        except _UpstreamBusy:
            break
        if raw is None:
            continue
        data = _strip_service_urls(raw)
        store_file(slug, "properties", data, count=False)
        return _respond(data, "text/plain", _PROPERTIES_CACHE_CONTROL)

    if cached is not None:
        return _respond(cached, "text/plain", _PROPERTIES_CACHE_CONTROL)
    return _respond(_builtin_properties(slug), "text/plain", "no-store")


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
# miss is a 404 and upstream is never contacted. Properties always answer (the
# built-in copy when none is on disk), so the layer itself always builds. ---

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


# --- Storage API (Configuration -> General -> Offline sky) --------------------
#
# Admin-only in multi-user mode and 404 whenever the section would not be shown.
# Every POST is CSRF-protected: this blueprint's /hips/ routes are public by
# design, but these three change instance-wide state.

# The limit selector's presets, in MiB (1 GB = 1024 MB).
LIMIT_PRESETS_MB = (2048, 5120, 10240, 25600)
LIMIT_NONE = "none"


def _storage_csrf_check():
    """Refuse a storage POST without this session's CSRF token, or None to go on.

    Called by each POST *after* ``_storage_guard``, so a caller who may not use
    the API at all is told 404/403 rather than 400 -- the access answer is the
    one that must not leak. Not gated on the user mode (single-user mode is the
    one where a cross-site POST needs no cookie at all), but it does honour
    WTF_CSRF_ENABLED, which the suite switches off for form-posting tests.
    """
    from flask import current_app
    if not current_app.config.get("WTF_CSRF_ENABLED", True):
        return None
    from nova import csrf
    csrf.protect()      # aborts 400 when the token is missing or wrong
    return None


def _storage_payload(extra=None) -> dict:
    """The store snapshot the section renders from, plus any route-specific keys."""
    data = store_status()
    data["limit_presets_mb"] = list(LIMIT_PRESETS_MB)
    if extra:
        data.update(extra)
    return data


# How long a storage response waits for another worker's pass to finish, so it
# can report settled numbers instead of a snapshot taken mid-change. Deliberately
# short: a pass is normally well under a second, and anything longer is better
# handled by the page polling (it keeps the UI responsive and the request cheap).
# The warning itself no longer depends on this wait -- status() measures pinned
# from the pin set, so it is right even while a pass is pending.
_SETTLE_WAIT_SECONDS = 2.0
_SETTLE_POLL_SECONDS = 0.15


def _await_eviction_settled() -> bool:
    """Wait (bounded) for the pass holding ``.evict`` to finish. True if it did.

    Taking the lock and releasing it immediately is the test: acquiring means the
    other pass is done and the sidecars it rewrote are current; timing out means
    it is still running, and the caller reports "evicting" so the page polls.
    The borrowed pass returns a sentinel, so "acquired" is not confused with
    "acquired and the pass returned None".
    """
    acquired = object()
    deadline = time.monotonic() + _SETTLE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if with_evict_lock(lambda: acquired) is acquired:
            return True
        time.sleep(_SETTLE_POLL_SECONDS)
    return False


# --- Size formatting ----------------------------------------------------------
# One formatter, mirrored by formatSkySize in static/js/config_form.js so the
# label the page renders and the label JS rewrites never disagree: 1 GB =
# 1024 MB, one decimal below 10 GB, no trailing ".0", the unit spelled out.

def format_gb(nbytes) -> str:
    """Bytes as a human size: "512 MB" under a gigabyte, else "1.2 GB"/"25 GB"."""
    try:
        value = float(nbytes)
    except (TypeError, ValueError):
        return "0 GB"
    gb = value / (1024 ** 3)
    if 0 < gb < 1:
        return f"{int(round(value / (1024 ** 2)))} MB"
    if gb < 10:
        text = f"{gb:.1f}".rstrip("0").rstrip(".")
        return f"{text or '0'} GB"
    return f"{int(round(gb))} GB"


def format_limit_gb(mb) -> str:
    """A limit in MiB as its selector label: "2 GB", "2.9 GB", "25 GB"."""
    if mb is None:
        return "No limit"
    try:
        return format_gb(int(mb) * 1024 * 1024)
    except (TypeError, ValueError):
        return ""


def storage_view() -> dict:
    """Everything Configuration -> General -> Offline sky needs to render.

    Reads only the O(1) sidecars (plus one statvfs), so it is safe on the config
    page's render path. The numbers are formatted here rather than in the
    template so the section and its JSON refresh agree on every label.
    """
    data = store_status()
    limit_mb = data["limit_mb"]
    presets = list(LIMIT_PRESETS_MB)
    labels = {mb: format_limit_gb(mb) for mb in presets}
    limit_label = format_limit_gb(limit_mb)
    return {
        **data,
        "limit_presets": presets,
        "limit_labels": labels,
        "limit_label": limit_label,
        "limit_is_custom": limit_mb is not None and limit_mb not in presets,
        "limit_exceeded": limit_mb is not None and data["pinned"] > limit_mb * 1024 * 1024,
        "used_label": format_gb(data["used"]),
        "pinned_label": format_gb(data["pinned"]),
    }


@offline_sky_bp.route("/api/offline-sky/status")
def offline_sky_status():
    guard = _storage_guard()
    if guard is not None:
        return guard
    return jsonify(_storage_payload())


@offline_sky_bp.route("/api/offline-sky/limit", methods=["POST"])
def offline_sky_set_limit():
    guard = _storage_guard()
    if guard is not None:
        return guard
    refused = _storage_csrf_check()
    if refused is not None:
        return refused
    if request.is_json:
        raw = (request.get_json(silent=True) or {}).get("limit")
    else:
        raw = request.form.get("limit")
    raw = str(raw if raw is not None else "").strip().lower()
    if raw == LIMIT_NONE:
        mb = None
    else:
        try:
            mb = int(raw)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid limit"}), 400
        if mb <= 0:
            mb = None
    try:
        set_limit_mb(mb)
    except OSError:
        # An unwritable store cannot remember the choice; say so instead of
        # reporting a limit that will be gone on the next request.
        return jsonify({"error": "read_only",
                        **store_status()}), 503
    summary = evict_now()
    if summary is None:
        # Another worker holds the eviction lock. Wait for it (bounded) so the
        # numbers this response reports are the settled ones: a pass is what
        # reconciles .total/.pinned, and the page derives the over-limit warning
        # from them, so answering from a snapshot taken while a pass is pending
        # can show a warning that is not true. Only if the holder is genuinely
        # slow do we hand back a snapshot and let the page poll.
        if _await_eviction_settled():
            summary = True
        else:
            maybe_evict_async()      # no-ops while the lock is held
    return jsonify(_storage_payload({"evicting": summary is None}))


@offline_sky_bp.route("/api/offline-sky/clear", methods=["POST"])
def offline_sky_clear_browsed():
    guard = _storage_guard()
    if guard is not None:
        return guard
    refused = _storage_csrf_check()
    if refused is not None:
        return refused
    try:
        summary = clear_now()
    except OSError:
        # An unwritable store cannot clear anything; the same refusal the limit
        # route gives for a choice it cannot persist (see offline_sky_set_limit).
        return jsonify({"error": "read_only",
                        **store_status()}), 503
    if summary is None:
        return jsonify(_storage_payload({"evicting": True, "cleared": False}))
    return jsonify(_storage_payload({"evicting": False, "cleared": True,
                                     "removed": summary["removed"]}))


# --- Downloads ---------------------------------------------------------------
#
# The four surveys the download page offers. One row per survey; the id is
# stable per survey so a re-plan replaces the previous job. Everything is
# admin-only in multi-user mode and CSRF-protected on every write.

DOWNLOAD_SURVEYS = ("dss2-color", "nsns-rgb8", "nsns-halpha8", "nsns-oiii8")
# The three detail levels, by HEALPix order: ~13" / ~6" / ~3" per pixel at the
# 512-pixel tile width. Each is capped at the survey's own maximum order, so a
# survey that stops at order 5 clamps all three to 5.
DETAIL_ORDERS = {"basic": 5, "standard": 6, "fine": 7}


def _storage_csrf():
    """The CSRF check, under its existing name for the download routes."""
    return _storage_csrf_check()


def _download_guard():
    """Admin + CSRF + writable store, or a refusal response."""
    guard = _storage_guard()
    if guard is not None:
        return guard
    refused = _storage_csrf()
    if refused is not None:
        return refused
    if not downloads_available():
        return jsonify({"error": "read_only", **_storage_payload()}), 409
    return None


def _session_user():
    """The username whose locations this request may touch.

    Single-user mode is always "default" (the app auto-authenticates as it).
    Multi-user is the signed-in user's username. The body never names the user:
    a client cannot tick another account's location.
    """
    if nova.SINGLE_USER_MODE:
        return "default"
    return (current_user.username or "").strip()


def _requested_locations():
    """The selection the client sent, normalised to ``[{"user","record_uid"}]``.

    The user is always the session's, never the body's. A ``record_uid`` that
    does not belong to that user is rejected, not silently dropped, so a client
    cannot tick another account's location by guessing its uid. Returns
    ``(entries, error)``; ``error`` is a refusal response or None.
    """
    payload = request.get_json(silent=True) or {}
    raw = payload.get("locations")
    if raw is None:
        return read_selection(), None
    user = _session_user()
    if not user:
        return None, (jsonify({"error": "authentication required"}), 401)
    from nova.models import Location
    from nova.helpers import get_db
    db = get_db()
    out = []
    seen = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        uid = str(entry.get("record_uid") or "").strip()
        if not uid:
            continue
        key = (user, uid)
        if key in seen:
            continue
        row = (db.query(Location)
               .filter(Location.record_uid == uid)
               .filter(Location.user_id == _db_user_id(user))
               .first())
        if row is None:
            return None, (jsonify({
                "error": "unknown_location",
                "message": f"record_uid {uid!r} is not a location of this user",
            }), 400)
        seen.add(key)
        out.append({"user": user, "record_uid": uid})
    return out, None


def _db_user_id(username: str):
    """The DB user id for ``username``, or None when the user does not exist."""
    from nova.models import DbUser
    from nova.helpers import get_db
    row = get_db().query(DbUser).filter(DbUser.username == username).first()
    return row.id if row else None


def _locations_with_coords(entries):
    """Attach each selected location's name, latitude and longitude.

    The selection stores only ``(user, record_uid)``; the sky needs the
    latitude. A location whose uid no longer exists is skipped rather than
    failing the whole request. ``record_uid`` is unique per user, so the query
    also filters on the user's id: a stale selection from another account
    cannot leak its coordinates into this user's plan.
    """
    from nova.models import Location
    from nova.helpers import get_db
    out = []
    db = get_db()
    for entry in entries:
        user_id = _db_user_id(entry.get("user") or "")
        if user_id is None:
            continue
        row = (db.query(Location)
               .filter(Location.record_uid == entry["record_uid"])
               .filter(Location.user_id == user_id)
               .first())
        if row is None:
            continue
        out.append({**entry, "name": row.name, "lat": row.lat, "lon": row.lon})
    return out


def _detail_order(survey: str, detail: str):
    """The order a detail label means for ``survey``, capped at its maximum."""
    from nova.offline_sky_facts import survey_facts
    facts = survey_facts(survey)
    wanted = DETAIL_ORDERS.get(str(detail or "basic").lower(), DETAIL_ORDERS["basic"])
    return min(wanted, facts.effective_max_order)


def _band_for(located):
    """The union band of the located selection, or None when nothing is ticked."""
    from nova import offline_sky_plan as plan_mod
    return plan_mod.union_band([loc.get("lat") for loc in located])


@offline_sky_bp.route("/api/offline-sky/selection", methods=["GET", "PUT"])
def offline_sky_selection():
    """Read or replace the ticked-location selection."""
    guard = _storage_guard()
    if guard is not None:
        return guard
    if request.method == "PUT":
        refused = _storage_csrf()
        if refused is not None:
            return refused
        entries, error = _requested_locations()
        if error is not None:
            return error
        store.write_selection(entries)
        return jsonify({"locations": store.read_selection()})
    return jsonify({"locations": store.read_selection()})


@offline_sky_bp.route("/api/offline-sky/plan")
def offline_sky_plan():
    """The tile count, size and per-location coverage for a planned download."""
    guard = _storage_guard()
    if guard is not None:
        return guard
    survey = request.args.get("survey", "")
    if survey not in DOWNLOAD_SURVEYS:
        return jsonify({"error": "unknown survey"}), 400
    order = _detail_order(survey, request.args.get("detail"))
    entries, error = _requested_locations()
    if error is not None:
        return error
    located = _locations_with_coords(entries)
    band = _band_for(located)
    from nova import offline_sky_plan as plan_mod
    return jsonify(plan_mod.plan_summary(survey, order, band, located))


@offline_sky_bp.route("/api/offline-sky/downloads")
def offline_sky_downloads():
    """Every download's job state and plan."""
    guard = _storage_guard()
    if guard is not None:
        return guard
    from nova import offline_sky_jobs as jobs
    return jsonify({"downloads": [jobs.download_progress(slug) for slug in DOWNLOAD_SURVEYS]})


@offline_sky_bp.route("/api/offline-sky/downloads/<slug>", methods=["POST"])
def offline_sky_start_download(slug):
    """Plan and queue a download; refuse when it cannot fit the budget."""
    guard = _download_guard()
    if guard is not None:
        return guard
    if slug not in DOWNLOAD_SURVEYS:
        return jsonify({"error": "unknown survey"}), 404
    payload = request.get_json(silent=True) or {}
    order = _detail_order(slug, payload.get("detail"))
    entries, error = _requested_locations()
    if error is not None:
        return error
    located = _locations_with_coords(entries)
    band = _band_for(located)
    if band is None:
        return jsonify({"error": "no_locations",
                        "message": "Tick at least one location first."}), 409
    from nova import offline_sky_plan as plan_mod
    summary = plan_mod.plan_summary(slug, order, band, located)
    needed = summary.get("size_bytes")
    if needed is not None:
        available = budget_bytes() - pinned_bytes()
        if needed > available:
            return jsonify({"error": "insufficient_space", "needed": needed,
                            "available": max(0, available), "plan": summary}), 409
    from nova import offline_sky_jobs as jobs
    return jsonify({"job": jobs.enqueue_download(slug, order, band, located),
                    "plan": summary})


@offline_sky_bp.route("/api/offline-sky/downloads/<slug>/cancel", methods=["POST"])
def offline_sky_cancel_download(slug):
    guard = _download_guard()
    if guard is not None:
        return guard
    if slug not in DOWNLOAD_SURVEYS:
        return jsonify({"error": "unknown survey"}), 404
    from nova import offline_sky_jobs as jobs
    return jsonify({"cancelled": bool(jobs.cancel_job(jobs.download_job_id(slug)))})


@offline_sky_bp.route("/api/offline-sky/downloads/<slug>", methods=["DELETE"])
def offline_sky_remove_download(slug):
    """Remove a download; 409 when its job will not stop in time."""
    guard = _download_guard()
    if guard is not None:
        return guard
    if slug not in DOWNLOAD_SURVEYS:
        return jsonify({"error": "unknown survey"}), 404
    from nova import offline_sky_jobs as jobs
    try:
        removed = jobs.remove_download(slug)
    except jobs.RemoveRefused as exc:
        # The job is still active, so a delete could resurrect it. Nothing was
        # deleted; the client may retry once the runner has stopped.
        return jsonify({"error": "job_still_running", "job": exc.job_id,
                        "message": "The download did not stop in time; try again."}), 409
    return jsonify({"removed": removed, **_storage_payload()})


@offline_sky_bp.route("/api/offline-sky/downloads/<slug>/trim", methods=["POST"])
def offline_sky_trim_download(slug):
    """Delete the download's tiles outside the current band ("could free X")."""
    guard = _download_guard()
    if guard is not None:
        return guard
    if slug not in DOWNLOAD_SURVEYS:
        return jsonify({"error": "unknown survey"}), 404
    from nova import offline_sky_jobs as jobs
    from nova import offline_sky_plan as plan_mod
    job_id = jobs.download_job_id(slug)
    plan = plan_mod.read_plan(job_id)
    if not plan:
        return jsonify({"error": "no_plan"}), 404
    owned = job_pins(job_id)
    # Plan tiles are bare rel paths; a claim key is "<slug>/<rel>". Prefix
    # before comparing, or every owned tile looks outside the plan and the
    # trim deletes the whole download.
    from nova.offline_sky_jobs import plan_owned_keys
    keep = plan_owned_keys(slug, plan)
    extra = owned - keep
    summary = delete_tiles(extra)
    # Release the deleted tiles' claims too, or could_free keeps reporting
    # bytes that are already gone.
    store.release_job_pins(job_id, extra)
    refresh_pinned_bytes()
    store.invalidate_size_cache(slug)
    return jsonify({"trimmed": summary, **_storage_payload()})
