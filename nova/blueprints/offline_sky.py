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
from nova.config import ADMIN_USERS, APP_VERSION, OFFLINE_SKY_STORE
from nova.offline_sky_facts import allsky_rel, effective_max_order
from nova.offline_sky_sources import SURVEY_BY_SLUG
from nova.offline_sky_store import (
    read_file, store_file, touch, maybe_evict_async, tile_path,
    clear_now, evict_now, set_limit_mb, status as store_status,
    with_evict_lock,
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

# "Absent" upstream bases: a base whose /properties answered a definite 404 (the
# survey is not there -- CDS serves its "Page unavailable" 404 for a whole survey
# it no longer hosts). Such a base is skipped for *tiles* without a request, so a
# survey whose first upstream is gone costs one request per tile, not one wasted
# 404 plus one good fetch.
#
# Unlike the breaker this is not a timer: a 404 does not heal on its own, and a
# 60s window would let the wasted request back in every minute. The mark is
# cleared only by a /properties that succeeds (see _serve_properties), which the
# frontend issues once per framing open -- so an upstream that comes back is
# picked up on the next open, and CDS is used again as soon as it answers.
#
# In-process only (each gunicorn worker keeps its own), like the breaker. A
# definite tile 404 does NOT mark a base absent: one survey can legitimately have
# no tile at a given Npix (NSNS covers Dec >= -16 only), while its neighbours do.
_absent_lock = threading.Lock()
_absent_bases = set()

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
    """
    return _enabled() and nova.offline_sky_ui() and _is_admin()


def _storage_guard():
    """None when the caller may use the storage API, else a response.

    The section is only shown when the Offline view switch is, so a request that
    arrives when it is not is answered 404 -- "no such thing here" -- rather than
    403, which would confirm the endpoint to a stranger. A caller who *is* in the
    right place but lacks the right is told 403.
    """
    if not _enabled() or not nova.offline_sky_ui():
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


def _base_absent(base: str) -> bool:
    with _absent_lock:
        return base in _absent_bases


def _mark_absent(base: str) -> None:
    with _absent_lock:
        _absent_bases.add(base)


def _mark_present(base: str) -> None:
    """An upstream answered /properties: it is there again, so stop skipping it."""
    with _absent_lock:
        _absent_bases.discard(base)


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

    This is also the only place that sees an outcome, so it is where the
    "absent" mark is kept: a /properties 404 means the base does not host that
    survey (skip it for tiles until it answers again), and a /properties 200
    means it does (clear the mark). A tile 404 says nothing about the base --
    one survey can legitimately lack a given tile while its neighbours exist.
    """
    if _breaker_open(base):
        return UPSTREAM_UNAVAILABLE, None
    is_properties = url.endswith("/properties")
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
        if is_properties:
            _mark_present(base)
        return UPSTREAM_OK, resp.content
    if resp.status_code >= 500:
        _breaker_trip(base)
        return UPSTREAM_UNAVAILABLE, None
    if resp.status_code == 429:
        return UPSTREAM_UNAVAILABLE, None
    if is_properties:
        _mark_absent(base)
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


def _went_unavailable(base: str) -> bool:
    """After a _fetch_upstream that returned None: was ``base`` unavailable
    (rather than answering "no such file")?

    _upstream_get trips the breaker on every network error, timeout and 5xx, and
    an already-open breaker is what made it skip the base -- so an open breaker
    right after the call means "unavailable". A definite 4xx leaves it closed.
    (A 429 is unavailable too but does not trip the breaker, so it reads as
    missing here: a throttled request gets the 404 it always got.)
    """
    return _breaker_open(base)


def _miss_response(missing: bool, unavailable: bool) -> Response:
    """No upstream delivered: 503 when it was only unavailability, else 404."""
    if unavailable and not missing:
        return Response(status=503, headers={"Cache-Control": "no-store"})
    return Response(status=404)


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
    # 404 only when an upstream said the tile does not exist; when every upstream
    # that hosts the survey was unavailable (breaker open, network error, 5xx,
    # saturated) the tile may well exist, so that is a 503.
    missing = False
    unavailable = False
    for base in src.upstreams:
        if _base_absent(base):
            continue  # this base does not host the survey: no request, no wasted 404
        try:
            raw = _fetch_upstream(base, f"{base.rstrip('/')}/{rel_path}")
        except _UpstreamBusy:
            unavailable = True
            break  # saturated: fail fast
        if raw is None:
            if _went_unavailable(base):
                unavailable = True
            else:
                missing = True
            continue
        store_file(slug, rel_path, raw)
        maybe_evict_async()
        return _tile_response(raw, rel_path)
    return _miss_response(missing, unavailable)


def _serve_properties(slug: str, cache_only: bool = False) -> Response:
    src = SURVEY_BY_SLUG[slug]
    cached = read_file(slug, "properties")
    if cached is not None:
        try:
            mtime = os.path.getmtime(tile_path(slug, "properties"))
        except OSError:
            mtime = 0.0
        if time.time() - mtime < _PROPERTIES_MAX_AGE_SECONDS:
            # Fresh: serve from disk, no upstream -- unless one of this survey's
            # upstreams is marked absent. Then this request is the re-probe that
            # can clear the mark, so fall through to the upstreams below (the
            # frontend asks once per framing open, which is what makes a
            # recovered upstream -- CDS included -- used again).
            if cache_only or not any(_base_absent(b) for b in src.upstreams):
                return _respond(cached, "text/plain; charset=utf-8", _PROPERTIES_CACHE_CONTROL)
        if cache_only:
            # Offline view: a stale copy is still served from disk.
            return _respond(cached, "text/plain; charset=utf-8", _PROPERTIES_CACHE_CONTROL)
    elif cache_only:
        return Response(status=404)

    # No cache, or stale (older than 7 days): try upstreams first, re-store on
    # success; on failure fall back to the stale disk copy when there is one.
    # Absent bases are tried here too: a 200 clears the mark (in _upstream_get).
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


def _preflight_properties(slug: str) -> Response:
    """The Framing pre-flight for an online survey: ask the upstreams, not the disk.

    _serve_properties answers from a fresh disk copy without contacting anyone,
    so it cannot tell whether the survey can be shown right now. This tries the
    upstreams in order (storing properties when there is no disk copy yet --
    store_file never overwrites) and answers:

    * 200 -- an upstream delivered;
    * 503 -- none delivered and at least one was unavailable (breaker open,
      network error, 5xx, saturated): "not responding";
    * 404 -- every upstream said the survey is not there.

    Breaker and absence marks are respected: an open breaker counts as
    unavailable without a request, and an absent base is skipped. Only when
    *every* upstream is marked absent are they asked again -- a /properties 200
    is the only thing that clears the mark, and with nothing else to try the
    survey could otherwise never come back in this process.
    """
    src = SURVEY_BY_SLUG[slug]
    bases = [b for b in src.upstreams if not _base_absent(b)] or list(src.upstreams)
    missing = False
    unavailable = False
    for base in bases:
        if _breaker_open(base):
            unavailable = True
            continue
        try:
            raw = _fetch_upstream(base, f"{base.rstrip('/')}/properties")
        except _UpstreamBusy:
            unavailable = True
            break
        if raw is None:
            if _went_unavailable(base):
                unavailable = True
            else:
                missing = True
            continue
        data = _strip_service_urls(raw)
        store_file(slug, "properties", data, count=False)
        return _respond(data, "text/plain; charset=utf-8", "no-store")
    if unavailable:
        return Response(status=503, headers={"Cache-Control": "no-store"})
    return Response(status=404, headers={"Cache-Control": "no-store"})


def _offline_centre_stored(slug: str, args) -> bool:
    """True when a tile at the frame centre is on disk at any order (Offline view
    pre-flight): Aladin then shows the best order available there.

    Orders 0 .. the survey's effective max order, deepest first; any accepted
    format. A centre outside the survey's coverage (its MOC) has no tile
    anywhere, so there is nothing to require. Disk only, never upstream.
    """
    from nova.offline_sky_facts import survey_coverage, survey_facts
    from nova.offline_sky_store import tile_exists
    import nova.hips_healpix as hp
    try:
        ra = float(args.get("ra")) % 360.0
        dec = float(args.get("dec"))
    except (TypeError, ValueError):
        return False
    if not (-90.0 <= dec <= 90.0):
        return False
    facts = survey_facts(slug, fetch=False)
    top = max(0, facts.effective_max_order)
    if not survey_coverage(slug, fetch=False).contains(top, hp.radec_to_nest(top, ra, dec)):
        return True
    # Any accepted format: Aladin may request a different one than the facts'
    # first pick (a survey publishing "png jpeg"), and either is what it shows.
    exts = (facts.tile_ext,) + tuple(e for e in SURVEY_BY_SLUG[slug].exts if e != facts.tile_ext)
    for order in range(top, -1, -1):
        npix = hp.radec_to_nest(order, ra, dec)
        stem = f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}."
        if any(tile_exists(slug, stem + ext) for ext in exts):
            return True
    return False


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
        if _base_absent(base):
            continue  # not a re-probe: properties is what clears the mark
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
    if request.args.get("preflight"):
        return _preflight_properties(slug)
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
    resp = _serve_properties(slug, cache_only=True)
    if resp.status_code == 200 and "ra" in request.args and "dec" in request.args:
        # The Framing pre-flight: properties on disk says nothing about the
        # frame (any online browsing stores it), so also ask for a tile at the
        # frame centre. Aladin's own properties request carries no ra/dec.
        if not _offline_centre_stored(slug, request.args):
            return Response(status=404, headers={"Cache-Control": "no-store"})
        resp.headers["Cache-Control"] = "no-store"
    return resp


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
    summary = clear_now()
    if summary is None:
        return jsonify(_storage_payload({"evicting": True, "cleared": False}))
    return jsonify(_storage_payload({"evicting": False, "cleared": True,
                                     "removed": summary["removed"]}))
