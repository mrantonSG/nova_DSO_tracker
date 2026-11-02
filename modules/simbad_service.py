"""SIMBAD access with mirror failover and bounded timeouts.

Per-worker host health cache. Probes use the TAP sync endpoint that the
scan and astroquery paths actually call. No socket.setdefaulttimeout.

This module must not import from nova.* — it is safe to import from
modules.nova_data_fetcher's standalone __main__ self-test.
"""

import threading
import time

import requests
from astroquery.simbad import Simbad, conf as simbad_conf

try:
    from pyvo.dal import DALServiceError as _DALServiceError
except Exception:  # pragma: no cover - pyvo ships with astroquery
    _DALServiceError = None

# Local default; callers may pass their own timeout.
DEFAULT_HTTP_TIMEOUT = 10

# Strasbourg first, then mirrors. Order matters only within a health group.
HOSTS = [
    "simbad.cds.unistra.fr",
    "simbad.harvard.edu",
    "simbad.idia.ac.za",
]
for _host in HOSTS:
    try:
        if _host not in simbad_conf.servers_list:
            simbad_conf.servers_list.append(_host)
    except Exception:  # pragma: no cover - defensive
        pass

PROBE_QUERY = "SELECT TOP 1 main_id FROM basic"
PROBE_TIMEOUT = (3, 5)
QUERY_TIMEOUT = (3, 8)
UP_TTL = 60
DOWN_TTL = 900
TOTAL_BUDGET = 20.0

_lock = threading.Lock()
_probe_locks = {host: threading.Lock() for host in HOSTS}
# host -> (is_up: bool, checked_at: float)
_health = {}

_HOST_FAILURES = [requests.exceptions.RequestException, ConnectionError, TimeoutError]
if _DALServiceError is not None:
    _HOST_FAILURES.append(_DALServiceError)
_HOST_FAILURES = tuple(_HOST_FAILURES)


class SimbadUnavailableError(RuntimeError):
    """Raised when every known SIMBAD host failed."""


class _TimeoutSession(requests.Session):
    """requests.Session that always applies a default timeout."""

    def __init__(self, timeout=QUERY_TIMEOUT):
        super().__init__()
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, **kwargs)


def _set_health(host, is_up):
    with _lock:
        _health[host] = (is_up, time.monotonic())


def mark_up(host):
    _set_health(host, True)


def mark_down(host):
    _set_health(host, False)


def _health_snapshot():
    with _lock:
        return dict(_health)


def _is_fresh(host, now, health=None):
    if health is None:
        health = _health_snapshot()
    entry = health.get(host)
    if entry is None:
        return False
    is_up, checked_at = entry
    ttl = UP_TTL if is_up else DOWN_TTL
    return (now - checked_at) < ttl


def _probe(host):
    """Return True if host serves the TAP sync endpoint with a CSV header."""
    url = f"https://{host}/simbad/sim-tap/sync"
    try:
        r = requests.post(
            url,
            data=[("REQUEST", "doQuery"), ("LANG", "ADQL"),
                  ("FORMAT", "csv"), ("QUERY", PROBE_QUERY)],
            timeout=PROBE_TIMEOUT,
        )
        if r.status_code != 200:
            return False
        ctype = (r.headers.get("content-type") or "").lower()
        if "csv" not in ctype and "text/plain" not in ctype:
            return False
        first = r.text.splitlines()[0] if r.text else ""
        return "main_id" in first.lower()
    except requests.exceptions.RequestException:
        return False


def _ordered_hosts():
    """Hosts ordered by cached health: known-up, unknown, known-down."""
    now = time.monotonic()
    health = _health_snapshot()
    up, unknown, down = [], [], []
    for host in HOSTS:
        if _is_fresh(host, now, health):
            (up if health[host][0] else down).append(host)
        else:
            unknown.append(host)
    return up + unknown + down


def _ensure_host_usable(host):
    """Probe only when the cache is stale. Returns True if the host is usable."""
    now = time.monotonic()
    if _is_fresh(host, now):
        with _lock:
            return _health[host][0]
    with _probe_locks[host]:
        now = time.monotonic()
        if _is_fresh(host, now):
            with _lock:
                return _health[host][0]
        ok = _probe(host)
        _set_health(host, ok)
        return ok


def current_host():
    """Last known-good host, read from cache only. Defaults to Strasbourg."""
    now = time.monotonic()
    health = _health_snapshot()
    for host in HOSTS:
        if _is_fresh(host, now, health) and health[host][0]:
            return host
    return HOSTS[0]


def is_available_cached():
    """True unless every host with fresh health evidence is down."""
    now = time.monotonic()
    health = _health_snapshot()
    known = [host for host in HOSTS if _is_fresh(host, now, health)]
    if not known:
        return True
    return any(health[host][0] for host in known)


def make_simbad_instance(host, row_limit=1):
    """Build a per-call Simbad instance bound to host with a default timeout."""
    simbad = Simbad()
    simbad.ROW_LIMIT = row_limit
    # Set the session before the first .tap access; the tap property builds
    # TAPService(baseurl=..., session=self._session) lazily.
    simbad._session = _TimeoutSession(QUERY_TIMEOUT)
    simbad.server = host
    return simbad


def query_object(object_name, fields=("main_id",), row_limit=1):
    """Query SIMBAD with host failover. Returns the astropy table.

    Raises SimbadUnavailableError when no host could serve the query.
    """
    deadline = time.monotonic() + TOTAL_BUDGET
    last_exc = None
    tried = 0
    for host in _ordered_hosts():
        if time.monotonic() >= deadline:
            break
        if not _ensure_host_usable(host):
            continue
        tried += 1
        try:
            simbad = make_simbad_instance(host, row_limit=row_limit)
            if fields:
                simbad.add_votable_fields(*fields)
            result = simbad.query_object(object_name)
            mark_up(host)
            return result
        except _HOST_FAILURES as exc:
            last_exc = exc
            mark_down(host)
    if tried == 0:
        raise SimbadUnavailableError("No reachable SIMBAD host")
    if last_exc is not None:
        raise SimbadUnavailableError(f"SIMBAD hosts failed: {last_exc}") from last_exc
    raise SimbadUnavailableError("SIMBAD request budget exhausted")


def tap_csv(adql, timeout=DEFAULT_HTTP_TIMEOUT):
    """Run ADQL against the first healthy host and return the CSV text.

    Tries the next host on failure. Raises SimbadUnavailableError when all fail.
    """
    deadline = time.monotonic() + TOTAL_BUDGET
    last_exc = None
    tried = 0
    for host in _ordered_hosts():
        if time.monotonic() >= deadline:
            break
        if not _ensure_host_usable(host):
            continue
        tried += 1
        try:
            r = requests.post(
                f"https://{host}/simbad/sim-tap/sync",
                data=[("REQUEST", "doQuery"), ("LANG", "ADQL"),
                      ("FORMAT", "csv"), ("QUERY", adql)],
                timeout=timeout,
            )
            ctype = (r.headers.get("content-type") or "").lower()
            text = r.text or ""
            first = text.splitlines()[0].strip() if text else ""
            if not r.ok or ("csv" not in ctype and "text/plain" not in ctype) \
                    or not first or "," not in first:
                last_exc = RuntimeError(
                    f"{host}: HTTP {r.status_code}, content-type {ctype!r}")
                mark_down(host)
                continue
            mark_up(host)
            return text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            mark_down(host)
    if tried == 0:
        raise SimbadUnavailableError("No reachable SIMBAD host")
    if last_exc is not None:
        raise SimbadUnavailableError(f"SIMBAD hosts failed: {last_exc}") from last_exc
    raise SimbadUnavailableError("SIMBAD request budget exhausted")
