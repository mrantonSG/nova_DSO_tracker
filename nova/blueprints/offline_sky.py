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
from flask import Blueprint, Response, session
from flask_login import current_user

import nova  # module-qualified so runtime reads of nova.SINGLE_USER_MODE stay live (see nova/config.py)
from nova.config import APP_VERSION, OFFLINE_SKY_STORE
from nova.offline_sky_sources import SURVEY_BY_SLUG
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


def _fetch_upstream(base: str, url: str):
    """200-byte body from `base`, or None. Raises _UpstreamBusy when saturated.

    Circuit-breaks on a connection error/timeout/5xx; a 4xx is a real "no such
    tile" and does not trip the breaker. Never redirects.
    """
    if _breaker_open(base):
        return None
    if not _FETCH_SEMAPHORE.acquire(timeout=_FETCH_SEMAPHORE_TIMEOUT):
        raise _UpstreamBusy()
    try:
        try:
            resp = _session.get(
                url,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                allow_redirects=False,
            )
        except requests.RequestException:
            _breaker_trip(base)
            return None
        if resp.status_code != 200:
            if resp.status_code >= 500:
                _breaker_trip(base)
            return None
        return resp.content
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


def _serve_tile(slug: str, rel_path: str) -> Response:
    data = read_file(slug, rel_path)
    if data is not None:
        touch(slug, rel_path)
        return _tile_response(data, rel_path)

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


def _serve_properties(slug: str) -> Response:
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


def _serve_moc(slug: str) -> Response:
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


@offline_sky_bp.route("/hips/<slug>/Allsky.<string:ext>")
def hips_allsky(slug: str, ext: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    ext_l = ext.lower()
    if ext_l not in SURVEY_BY_SLUG[slug].exts:
        return Response(status=404)
    return _serve_tile(slug, f"Allsky.{ext_l}")


@offline_sky_bp.route("/hips/<slug>/Norder<int:order>/Dir<int:dir>/Npix<int:npix>.<string:ext>")
def hips_tile(slug: str, order: int, dir: int, npix: int, ext: str):
    if not _allowed() or slug not in SURVEY_BY_SLUG:
        return Response(status=404)
    src = SURVEY_BY_SLUG[slug]
    ext_l = ext.lower()
    if ext_l not in src.exts:
        return Response(status=404)
    if order > src.max_order:
        return Response(status=404)
    if npix >= 12 * 4 ** order:
        return Response(status=404)
    if dir != (npix // 10000) * 10000:
        return Response(status=404)
    return _serve_tile(slug, f"Norder{order}/Dir{dir}/Npix{npix}.{ext_l}")
