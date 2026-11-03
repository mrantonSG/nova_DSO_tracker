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
The download is bounded, not only the value: the response is streamed and abandoned
the moment it passes GRS_MAX_BYTES, so a host that answers with something enormous
costs the instance a few kilobytes and a closed connection, not its memory.

Two workers do not fetch at the same time: the fetch holds try_acquire_file_lock
(nova/helpers.py) across the whole request. That lock is fcntl's advisory lock, so on
Windows -- which has no fcntl -- it is a no-op and takes no handle: both workers may
fetch, and a write that collides with a reader is caught, leaving the previous value
in place for the next refresh to replace.

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
GRS_READ_CHUNK = 8192           # bytes per read while the body is streamed in
GRS_MAX_AGE_SECONDS = 24 * 60 * 60
GRS_STAMP_SLACK = 24 * 60 * 60  # a fetch time this far ahead is a clock that is behind
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
    A bool is rejected: it is an int in Python, and `true` is not a longitude. An int
    too large for a float (10**400, which JSON can carry) is no number either, and
    float() raises on it rather than returning an infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
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

    The date is returned as the day it names, not as written: Python's `\\d` and int()
    both accept Unicode decimal digits that the browser's `\\d` does not, so '٢٠٢٦-06-01'
    would pass here and then be refused by the card.
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

    return {"schema": GRS_SCHEMA, "longitude_ii": lon, "date": day.isoformat(),
            "drift_deg_per_month": drift, "source": source[:GRS_SOURCE_MAX]}


def _cached_at(stored, now=None) -> float:
    """The file's own fetch time, or 0.0 when it is missing, unreadable, non-finite
    (JSON carries NaN and Infinity), or more than a day ahead -- a clock that should
    not be trusted to hold the file's age. 0.0 reads as "due for a refresh"."""
    stamp = _real_number(stored.get("fetched_at"))
    if stamp is None:
        return 0.0
    if now is None:
        now = time.time()
    if stamp > now + GRS_STAMP_SLACK:
        return 0.0
    return stamp


def _read_cache():
    """(fetched_at, value) as last written; (0.0, None) when the file is missing,
    unreadable, or does not hold a value the card can use. Never raises."""
    try:
        with open(grs_cache_path(), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if not isinstance(stored, dict):
            return 0.0, None
        try:
            # Re-validated on read: the file is on the instance's own disk and may
            # have been written by an older build, or edited by hand.
            value = validate_grs_value(stored.get("value"))
        except ValueError:
            # An unusable value is no value, and its stamp must not keep the file in
            # place for the day it claims: 0.0 means the next page load replaces it.
            return 0.0, None
        return _cached_at(stored), {"lon": value["longitude_ii"], "date": value["date"],
                                    "drift": value["drift_deg_per_month"],
                                    "origin": "central"}
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


def _read_capped_body(response) -> bytes:
    """The response body, or ValueError as soon as it passes GRS_MAX_BYTES.

    The body is read in chunks and the read is abandoned at the cap, so a host that
    answers with something enormous costs the instance one chunk of memory and a
    closed connection rather than the whole file. A Content-Length that is already
    over the cap is refused before a byte is read at all.
    """
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_bytes = int(str(declared).strip())
        except (TypeError, ValueError):
            declared_bytes = None    # unparsable: the read below still caps it
        if declared_bytes is not None and declared_bytes > GRS_MAX_BYTES:
            raise ValueError(f"Content-Length {declared_bytes} is over {GRS_MAX_BYTES} bytes")

    chunks = []
    total = 0
    for chunk in response.iter_content(GRS_READ_CHUNK):
        if not chunk:
            continue
        total += len(chunk)
        if total > GRS_MAX_BYTES:
            raise ValueError(f"response is over {GRS_MAX_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_and_store() -> None:
    """One fetch. Never raises: every failure keeps the value already on disk."""
    url = grs_url()
    try:
        print(f"[GRS] Fetching the central longitude from {url}")
        # No redirects: a 3xx must not be able to move the request to another host,
        # or downgrade it to plain http. Streamed, so the body is bounded as it
        # arrives and is never buffered whole (see _read_capped_body).
        response = requests.get(url, timeout=GRS_TIMEOUT, allow_redirects=False,
                                stream=True)
        try:
            response.raise_for_status()
            if response.status_code != 200:
                # allow_redirects=False: a 3xx is a failure, not a hop to another host
                # that could be plain http, and not something to parse as a value.
                raise ValueError(f"HTTP {response.status_code}, not 200")
            body = _read_capped_body(response)
        finally:
            # Whatever happened above, including the cap being hit: a streamed
            # response holds its connection until it is read out or closed.
            try:
                response.close()
            except Exception:
                pass                 # a failed close must not change the outcome
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
    Gunicorn workers do not fetch at the same time.

    The once-a-day guard is spent only when a fetch is actually started, never on a
    look at a file that is still fresh: otherwise a value that was a few hours old at
    the first page load would not be replaced until a day after *that*, and the
    instance would run on a day-old value for part of every second day.
    """
    global _last_attempt
    if BACKGROUND_TASKS_DISABLED or app.config.get("TESTING"):
        return
    now = time.time()
    with _attempt_lock:
        if (now - _last_attempt) < GRS_MAX_AGE_SECONDS:
            return
    try:
        if not _needs_refresh(now):
            return
    except Exception as e:
        print(f"[GRS] Could not check the cached longitude: {e}")
        return
    with _attempt_lock:
        # Two requests in flight at once: the first to get here starts the fetch and
        # spends the guard; the second then finds it spent and does nothing.
        if (time.time() - _last_attempt) < GRS_MAX_AGE_SECONDS:
            return
        _last_attempt = time.time()
    try:
        threading.Thread(target=_refresh_locked, name="grs-refresh", daemon=True).start()
    except Exception as e:
        print(f"[GRS] Could not start the refresh thread: {e}")
