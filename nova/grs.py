"""The Great Red Spot's System II longitude, fetched from the Nova catalog host.

The spot's longitude is not predictable from theory: it drifts slowly and unevenly,
so the Jupiter card predicts its transits from a published measurement. A built-in
default ships in static/js/nova_solar_jupiter.js, but it only changes with a release,
so a self-hosted instance would keep predicting from a stale longitude for as long as
it is not updated. Instead the instance fetches one centrally hosted value:

    {NOVA_CATALOG_URL}/grs.json

The URL comes from NOVA_CATALOG_URL, the key the catalog packs are already fetched
with (nova/helpers.py), and falls back to the same shipped host when it is empty:
there is no key of its own.

The instance's SERVER makes the request, never the browser, so no user data and no
visitor's IP leaves the machine -- the same shape as the catalog fetch and the
skyglow service. It happens at most once a day, in a daemon thread that a request
only starts when the value on disk is old; no page ever waits for it. The last good
value is kept at instance/cache/grs.json, so it survives a restart and an offline
period. A file that is missing, unreachable, malformed, or implausible leaves that
value alone, and a value whose own measurement date is old still shows the card's
age warning: staleness is judged by the date in the value, never by when it arrived.

NOVA_DISABLE_BACKGROUND_TASKS=1 (which tests/conftest.py sets) switches the whole
thing off: nothing is fetched, no thread is started, and the page uses whatever is
already on disk. An instance with no network behaves exactly as it did before this
module existed.

Nothing here touches the database.
"""

import json
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from nova.config import BACKGROUND_TASKS_DISABLED, CACHE_DIR, NOVA_CATALOG_URL
from nova.helpers import release_file_lock, try_acquire_file_lock

# The host the catalog packs are fetched from when NOVA_CATALOG_URL is empty
# (nova/helpers.py:discover_catalog_packs, nova/migration.py).
CATALOG_HOST = "https://catalogs.nova-tracker.com"

GRS_FILENAME = "grs.json"
GRS_SCHEMA = 1

GRS_TIMEOUT = 5                 # seconds; short, the thread is throwaway
GRS_MAX_BYTES = 64 * 1024       # a value this size is a broken or hostile host
GRS_MAX_AGE_SECONDS = 24 * 60 * 60
GRS_DRIFT_MAX = 30.0            # deg a month; the published rate is about 1.75
GRS_SOURCE_MAX = 200            # characters of the file's own attribution

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def grs_url() -> str:
    """The central value's URL, on the same host as the catalog packs."""
    base = (NOVA_CATALOG_URL or CATALOG_HOST).rstrip("/")
    return f"{base}/{GRS_FILENAME}"


def grs_cache_path() -> str:
    """Built per call, so a patched CACHE_DIR (tests) is used."""
    return os.path.join(CACHE_DIR, GRS_FILENAME)


def _real_number(value):
    """`value` as a float when it is a finite int or float, else None.
    A bool is rejected: it is an int in Python, and `true` is not a longitude."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _real_date(text):
    """`text` as a date when it is a real 'YYYY-MM-DD' day, else None.
    Rejects 2026-02-30 and 2026-13-01, which a regex alone would let through."""
    match = _DATE_RE.match(text) if isinstance(text, str) else None
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
    except ValueError:
        return None


def validate_grs_value(payload, today=None) -> dict:
    """`payload` as the card needs it, or ValueError naming what is wrong.

    The rules are the ones the file is written to: schema 1, a longitude in 0..360,
    a real measurement date that is not in the future (a day of slack, so a file
    published today in another timezone is not rejected), a plausible drift, and an
    optional attribution. The attribution is kept for the log and for anyone reading
    the cache file; it is never put into the tooltip, whose wording is translated.
    """
    if not isinstance(payload, dict):
        raise ValueError("not a JSON object")
    schema = payload.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != GRS_SCHEMA:
        raise ValueError(f"schema is {schema!r}, expected {GRS_SCHEMA}")

    lon = _real_number(payload.get("longitude_ii"))
    if lon is None:
        raise ValueError("longitude_ii is not a finite number")
    if not 0.0 <= lon < 360.0:
        raise ValueError(f"longitude_ii {lon} is outside 0..360")

    date_text = payload.get("date")
    day = _real_date(date_text)
    if day is None:
        raise ValueError(f"date {date_text!r} is not a real YYYY-MM-DD date")
    if today is None:
        today = datetime.now(timezone.utc).date()
    if day > today + timedelta(days=1):
        raise ValueError(f"date {date_text} is in the future")

    drift = _real_number(payload.get("drift_deg_per_month"))
    if drift is None:
        raise ValueError("drift_deg_per_month is not a finite number")
    if abs(drift) > GRS_DRIFT_MAX:
        raise ValueError(f"drift_deg_per_month {drift} is implausible")

    source = payload.get("source")
    if not isinstance(source, str):
        source = ""

    return {"schema": GRS_SCHEMA, "longitude_ii": lon, "date": date_text,
            "drift_deg_per_month": drift, "source": source[:GRS_SOURCE_MAX]}


def _read_cache():
    """(fetched_at, value) as last written; (0.0, None) when the file is missing,
    unreadable, or does not hold a value the card can use. Never raises."""
    try:
        with open(grs_cache_path(), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if not isinstance(stored, dict):
            return 0.0, None
        try:
            stamp = float(stored.get("fetched_at") or 0.0)
        except (TypeError, ValueError):
            stamp = 0.0
        try:
            # Re-validated on read: the file is on the instance's own disk and may
            # have been written by an older build, or edited by hand.
            value = validate_grs_value(stored.get("value"))
        except ValueError:
            return stamp, None
        return stamp, {"lon": value["longitude_ii"], "date": value["date"],
                       "drift": value["drift_deg_per_month"], "origin": "central"}
    except Exception:
        return 0.0, None


def read_grs_value():
    """The value the page should use, or None when there is none.

    None is the normal state of a fresh instance with no network: the card then
    falls back to its built-in default, exactly as it did before this module.
    """
    return _read_cache()[1]


def _write_cache(value) -> None:
    """Write the value and its fetch time atomically: a temp file in the same
    directory, fsync, then os.replace, so the other Gunicorn worker never reads a
    half-written file. The file mirrors the wire format, so it can be read by eye."""
    path = grs_cache_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    payload = {"fetched_at": time.time(), "value": value}
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".grs.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _fetch_and_store() -> None:
    """One fetch. Never raises: every failure keeps the value already on disk."""
    url = grs_url()
    try:
        print(f"[GRS] Fetching the central longitude from {url}")
        # No redirects: a 3xx must not be able to move the request to another host,
        # or downgrade it to plain http.
        response = requests.get(url, timeout=GRS_TIMEOUT, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            # allow_redirects=False: a 3xx is a failure, not a hop to another host that
            # could be plain http, and not something to parse as a value.
            raise ValueError(f"HTTP {response.status_code}, not 200")
        body = response.content[:GRS_MAX_BYTES + 1]
        if len(body) > GRS_MAX_BYTES:
            raise ValueError(f"response is over {GRS_MAX_BYTES} bytes")
        value = validate_grs_value(json.loads(body.decode("utf-8")))
    except Exception as e:
        # Offline, unreachable, not uploaded yet, malformed, or implausible: keep
        # what is on disk and try again tomorrow.
        print(f"[GRS] Keeping the cached longitude: {e}")
        return
    try:
        _write_cache(value)
    except Exception as e:
        print(f"[GRS] Could not cache the longitude: {e}")
        return
    print(f"[GRS] Central longitude: System II {value['longitude_ii']:.1f}° "
          f"on {value['date']}, drift {value['drift_deg_per_month']:+.2f}°/month")


def _needs_refresh(now: float) -> bool:
    """True when the value on disk is older than a day, or there is none."""
    return (now - _read_cache()[0]) >= GRS_MAX_AGE_SECONDS


def _refresh_locked() -> None:
    """Fetch, holding the cross-worker lock for the whole request. Never raises."""
    handle = None
    try:
        os.makedirs(os.path.dirname(grs_cache_path()), exist_ok=True)
        handle = try_acquire_file_lock(grs_cache_path())
        if handle is None:
            # The other worker is fetching right now; it writes the file for both.
            return
        if not _needs_refresh(time.time()):
            # It finished between the request's check and this thread starting.
            return
        _fetch_and_store()
    except Exception as e:
        print(f"[GRS] Refresh failed: {e}")
    finally:
        release_file_lock(handle)


_last_attempt = 0.0
_attempt_lock = threading.Lock()


def maybe_refresh_grs(app) -> None:
    """Start a fetch when the value on disk is older than a day; return at once.

    Called from the Solar System route. The page is never held up: this only starts
    a daemon thread, and only when this process has not tried within the last day
    and the file is old. The thread then takes the cross-worker lock, so two
    Gunicorn workers cannot fetch at the same time.
    """
    global _last_attempt
    if BACKGROUND_TASKS_DISABLED or app.config.get("TESTING"):
        return
    now = time.time()
    with _attempt_lock:
        if (now - _last_attempt) < GRS_MAX_AGE_SECONDS:
            return
        _last_attempt = now
    try:
        if not _needs_refresh(now):
            return
    except Exception as e:
        print(f"[GRS] Could not check the cached longitude: {e}")
        return
    try:
        threading.Thread(target=_refresh_locked, name="grs-refresh", daemon=True).start()
    except Exception as e:
        print(f"[GRS] Could not start the refresh thread: {e}")
