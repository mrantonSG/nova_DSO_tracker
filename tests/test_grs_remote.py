"""The centrally fetched Great Red Spot longitude (nova/grs.py).

Pinned here, with no network at all (requests.get is always replaced):

  * the URL is derived from NOVA_CATALOG_URL, with the shipped host as the fallback
    when the key is empty, so no instance needs a key of its own;
  * a response is validated before it is believed -- schema, longitude range, a real
    measurement date that is not in the future, a plausible drift, a size cap -- and
    anything that fails leaves the value already on disk untouched;
  * a fetch happens at most once a day, and only when a request starts a thread; the
    request itself never waits;
  * the cross-worker lock means two Gunicorn workers do not both fetch;
  * the file is replaced atomically, so a reader in the other worker never sees a
    half-written one, and a write that dies leaves the previous file intact;
  * the Solar System route carries the value (or null) to the page.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests as requests_lib

import nova
from nova import grs


# --- helpers ---------------------------------------------------------------

def _payload(**over):
    """A valid file, with any field overridden."""
    base = {"schema": 1, "longitude_ii": 91.0, "date": "2026-06-01",
            "drift_deg_per_month": 1.75,
            "source": "JUPOS via Sky & Telescope; BAA Jupiter Section"}
    base.update(over)
    return base


class _Response:
    """Enough of requests.Response for the module: a status, a body that streams the
    way a real one does, and the calls the module makes on it."""

    def __init__(self, payload=None, status=200, raw=None, headers=None, chunk=8192):
        self.status_code = status
        self.content = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.headers = {} if headers is None else headers
        self.chunk = chunk
        self.served = []            # every piece of body handed out
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests_lib.exceptions.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, size=8192):
        step = min(size, self.chunk)
        for i in range(0, len(self.content), step):
            piece = self.content[i:i + step]
            self.served.append(piece)
            yield piece

    def close(self):
        self.closed = True


class _Endless(_Response):
    """A body that never ends, in pieces no larger than the module's own read size:
    the read has to stop at the cap, not wait for the host to stop."""

    LIMIT = 100_000                 # so a missing cap fails the test instead of hanging

    def __init__(self, piece=1024, **kwargs):
        super().__init__(raw=b"", **kwargs)
        self.piece = piece

    def iter_content(self, size=8192):
        for _ in range(self.LIMIT):
            self.served.append(b"x" * self.piece)
            yield b"x" * self.piece


def _serve(monkeypatch, response=None, error=None):
    """Replace requests.get; return the list of calls it received."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(grs.requests, "get", fake_get)
    return calls


def _fetch(monkeypatch, response=None, error=None):
    """One fetch with requests.get replaced, as the thread would run it."""
    calls = _serve(monkeypatch, response=response, error=error)
    grs._fetch_and_store()
    return calls


def _app(testing=False):
    """The only thing the module asks an app for."""
    return type("App", (), {"config": {"TESTING": testing}})()


def _wait_for(predicate, timeout=5.0):
    """Spin until the fetch thread has done its work (it is deliberately not awaited)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("the fetch thread did not finish in time")


@pytest.fixture(autouse=True)
def _cache_dir(monkeypatch, tmp_path):
    """This module's own cache dir, and a fresh once-a-day guard, per test."""
    monkeypatch.setattr(grs, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(grs, "_last_attempt", 0.0)
    return tmp_path


def _write_cache_file(directory, value, fetched_at=None):
    path = os.path.join(str(directory), grs.GRS_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"fetched_at": time.time() if fetched_at is None else fetched_at,
                   "value": value}, handle)
    return path


def _cache_file_text(directory):
    with open(os.path.join(str(directory), grs.GRS_FILENAME), encoding="utf-8") as handle:
        return handle.read()


# --- 1. the URL ------------------------------------------------------------

def test_url_is_the_catalog_key_when_it_is_set(monkeypatch):
    monkeypatch.setattr(grs, "NOVA_CATALOG_URL", "https://mirror.example.org/packs")
    assert grs.grs_url() == "https://mirror.example.org/packs/grs.json"


def test_url_falls_back_to_the_shipped_host_when_the_key_is_empty(monkeypatch):
    monkeypatch.setattr(grs, "NOVA_CATALOG_URL", "")
    assert grs.grs_url() == "https://catalogs.nova-tracker.com/grs.json"


def test_url_tolerates_a_trailing_slash(monkeypatch):
    monkeypatch.setattr(grs, "NOVA_CATALOG_URL", "https://mirror.example.org/")
    assert grs.grs_url() == "https://mirror.example.org/grs.json"


def test_there_is_no_key_of_its_own(monkeypatch):
    """The URL follows the catalog key, so an instance that mirrors its packs needs no
    second setting and .env grows nothing."""
    monkeypatch.setattr(grs, "NOVA_CATALOG_URL", "https://mirror.example.org")
    assert grs.grs_url().startswith("https://mirror.example.org/")


# --- 2. validation ---------------------------------------------------------

def test_a_good_file_is_validated_and_normalised():
    assert grs.validate_grs_value(_payload()) == {
        "schema": 1, "longitude_ii": 91.0, "date": "2026-06-01",
        "drift_deg_per_month": 1.75,
        "source": "JUPOS via Sky & Telescope; BAA Jupiter Section"}


def test_a_missing_source_is_kept_as_an_empty_string():
    assert grs.validate_grs_value(_payload(source=None))["source"] == ""


def test_an_overlong_source_is_truncated():
    assert len(grs.validate_grs_value(_payload(source="x" * 500))["source"]) == grs.GRS_SOURCE_MAX


@pytest.mark.parametrize("bad", [
    {"schema": 2}, {"schema": "1"}, {"schema": True}, {"schema": None},
])
def test_a_wrong_schema_is_rejected(bad):
    with pytest.raises(ValueError):
        grs.validate_grs_value(_payload(**bad))


@pytest.mark.parametrize("not_an_object", [[], "91.0", None, 91.0])
def test_a_non_object_is_rejected(not_an_object):
    with pytest.raises(ValueError):
        grs.validate_grs_value(not_an_object)


@pytest.mark.parametrize("bad", [
    {"longitude_ii": 360.0}, {"longitude_ii": -0.1}, {"longitude_ii": 400},
    {"longitude_ii": "91.0"}, {"longitude_ii": None}, {"longitude_ii": True},
    {"longitude_ii": float("nan")}, {"longitude_ii": float("inf")},
])
def test_an_impossible_longitude_is_rejected(bad):
    with pytest.raises(ValueError):
        grs.validate_grs_value(_payload(**bad))


@pytest.mark.parametrize("bad", [
    "2026-02-30", "2026-13-01", "2026-6-1", "01-06-2026", "", None, 20260601,
])
def test_a_date_that_is_not_a_real_day_is_rejected(bad):
    with pytest.raises(ValueError):
        grs.validate_grs_value(_payload(date=bad))


def test_a_date_in_the_future_is_rejected():
    today = datetime.now(timezone.utc).date()
    with pytest.raises(ValueError):
        grs.validate_grs_value(_payload(date=(today + timedelta(days=2)).isoformat()),
                               today=today)


def test_tomorrow_is_accepted_for_a_publish_in_another_timezone():
    today = datetime.now(timezone.utc).date()
    value = grs.validate_grs_value(_payload(date=(today + timedelta(days=1)).isoformat()),
                                   today=today)
    assert value["date"] == (today + timedelta(days=1)).isoformat()


def test_today_and_the_past_are_accepted():
    today = datetime.now(timezone.utc).date()
    for day in (today, today - timedelta(days=400)):
        assert grs.validate_grs_value(_payload(date=day.isoformat()), today=today)


@pytest.mark.parametrize("bad", [
    {"drift_deg_per_month": 1e9}, {"drift_deg_per_month": -1e9},
    {"drift_deg_per_month": "1.75"}, {"drift_deg_per_month": None},
])
def test_an_implausible_drift_is_rejected(bad):
    with pytest.raises(ValueError):
        grs.validate_grs_value(_payload(**bad))


def test_a_negative_drift_within_the_bound_is_accepted():
    # The spot has drifted one way for years, and the published rate is signed.
    assert grs.validate_grs_value(_payload(drift_deg_per_month=-2.0))["drift_deg_per_month"] == -2.0


def test_a_whole_number_too_large_for_a_float_is_not_a_number():
    """JSON can carry 10**400 as an integer literal, and float() raises on it rather
    than returning an infinity: that is "not a number", not a crash."""
    assert grs._real_number(10 ** 400) is None
    assert grs._real_number(-(10 ** 400)) is None


@pytest.mark.parametrize("bad", [
    {"longitude_ii": 10 ** 400}, {"drift_deg_per_month": -(10 ** 400)},
])
def test_an_integer_too_large_for_a_float_is_rejected_as_a_value(bad):
    with pytest.raises(ValueError):
        grs.validate_grs_value(_payload(**bad))


def test_a_date_in_non_ascii_digits_is_normalised_to_the_day_it_names():
    """Python's \\d and int() both take Unicode decimal digits, the browser's \\d does
    not, so the day is stored as written in ASCII rather than as it arrived: the card
    then reads the same date the server validated."""
    value = grs.validate_grs_value(_payload(date="٢٠٢٦-06-01"))
    assert value["date"] == "2026-06-01"
    assert value["date"].isascii()


# --- 3. one fetch ----------------------------------------------------------

def test_a_good_response_is_validated_cached_and_served(monkeypatch):
    # conftest points NOVA_CATALOG_URL at a non-resolvable host; the shipped host is
    # the fallback for an empty key (see the three URL tests above).
    monkeypatch.setattr(grs, "NOVA_CATALOG_URL", "")
    calls = _fetch(monkeypatch, response=_Response(_payload()))
    assert [c["url"] for c in calls] == ["https://catalogs.nova-tracker.com/grs.json"]
    assert grs.read_grs_value() == {"lon": 91.0, "date": "2026-06-01",
                                    "drift": 1.75, "origin": "central"}


def test_the_request_is_short_and_never_follows_a_redirect(monkeypatch):
    calls = _fetch(monkeypatch, response=_Response(_payload()))
    assert calls[0]["timeout"] == grs.GRS_TIMEOUT
    assert calls[0]["allow_redirects"] is False


def test_the_cached_file_mirrors_the_wire_format(monkeypatch, _cache_dir):
    _fetch(monkeypatch, response=_Response(_payload()))
    stored = json.loads(_cache_file_text(_cache_dir))
    assert stored["value"]["longitude_ii"] == 91.0
    assert isinstance(stored["fetched_at"], float)


def test_the_cached_file_holds_the_wire_format_not_the_page_shape(monkeypatch, _cache_dir):
    """The page shape is derived on read, so the file can be read and hand-edited by eye."""
    _fetch(monkeypatch, response=_Response(_payload(source="provenance")))
    stored = json.loads(_cache_file_text(_cache_dir))
    assert stored["value"]["source"] == "provenance"
    assert "origin" not in stored["value"] and "lon" not in stored["value"]


@pytest.mark.parametrize("status", [302, 404, 500, 503])
def test_a_non_200_leaves_the_cached_value_alone(monkeypatch, status):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    _fetch(monkeypatch, response=_Response(status=status, raw=b""))
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_redirect_is_not_believed_even_with_a_valid_body(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    _fetch(monkeypatch, response=_Response(_payload(longitude_ii=12.0), status=301))
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_timeout_leaves_the_cached_value_alone(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    _fetch(monkeypatch, error=requests_lib.exceptions.Timeout("too slow"))
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_connection_error_leaves_the_cached_value_alone(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    _fetch(monkeypatch, error=requests_lib.exceptions.ConnectionError("offline"))
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_body_that_is_not_json_leaves_the_cached_value_alone(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    _fetch(monkeypatch, response=_Response(raw=b"<html>not found</html>"))
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_malformed_value_leaves_the_cached_value_alone(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    _fetch(monkeypatch, response=_Response(_payload(longitude_ii=999.0)))
    assert grs.read_grs_value()["lon"] == 77.0


def test_an_oversized_body_is_rejected(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    huge = b'{"schema": 1, "pad": "' + b"x" * grs.GRS_MAX_BYTES + b'"}'
    _fetch(monkeypatch, response=_Response(raw=huge))
    assert grs.read_grs_value()["lon"] == 77.0


def test_the_body_is_streamed_not_buffered_whole(monkeypatch):
    calls = _fetch(monkeypatch, response=_Response(_payload()))
    assert calls[0]["stream"] is True


def test_a_body_that_never_ends_is_cut_off_at_the_cap(monkeypatch):
    """A host that streams without end must not be read to the end: the read stops at
    the cap, the response is closed, and the value already on disk is kept."""
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    response = _Endless()
    _fetch(monkeypatch, response=response)
    assert response.served, "the body was not read as a stream at all"
    assert response.closed, "the streamed response was left open"
    assert len(response.served) <= grs.GRS_MAX_BYTES // response.piece + 1, \
        f"read {len(response.served)} pieces: the cap did not stop the download"
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_declared_size_over_the_cap_is_refused_without_reading_a_byte(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    response = _Response(_payload(), headers={"Content-Length": str(grs.GRS_MAX_BYTES + 1)})
    _fetch(monkeypatch, response=response)
    assert response.served == [], "a declared size over the cap must not be read"
    assert response.closed
    assert grs.read_grs_value()["lon"] == 77.0


def test_a_content_length_header_that_is_not_a_number_falls_back_to_the_cap(monkeypatch):
    """A junk header is not a reason to refuse a good value: the read still caps it."""
    response = _Response(_payload(), headers={"Content-Length": "banana"})
    _fetch(monkeypatch, response=response)
    assert grs.read_grs_value()["lon"] == 91.0


def test_the_response_is_closed_after_a_rejected_one(monkeypatch):
    """The close is in a finally, so a failure on the way does not leak the connection."""
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    response = _Response(raw=b"<html>not found</html>", headers={"Content-Length": "21"})
    _fetch(monkeypatch, response=response)
    assert response.closed
    assert grs.read_grs_value()["lon"] == 77.0


def test_the_first_good_fetch_needs_no_previous_value(monkeypatch):
    assert grs.read_grs_value() is None
    _fetch(monkeypatch, response=_Response(_payload()))
    assert grs.read_grs_value()["origin"] == "central"


def test_no_request_leaves_a_cache_file_behind(monkeypatch, _cache_dir):
    _fetch(monkeypatch, error=requests_lib.exceptions.ConnectionError("offline"))
    assert not os.path.exists(os.path.join(str(_cache_dir), grs.GRS_FILENAME))


# --- 4. offline ------------------------------------------------------------

def test_offline_with_a_cached_value_serves_it(monkeypatch):
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=93.5))
    _fetch(monkeypatch, error=requests_lib.exceptions.ConnectionError("offline"))
    assert grs.read_grs_value() == {"lon": 93.5, "date": "2026-06-01",
                                    "drift": 1.75, "origin": "central"}


def test_offline_with_nothing_cached_serves_nothing(monkeypatch):
    _fetch(monkeypatch, error=requests_lib.exceptions.ConnectionError("offline"))
    assert grs.read_grs_value() is None


def test_a_cache_file_that_is_not_json_serves_nothing(_cache_dir):
    with open(os.path.join(str(_cache_dir), grs.GRS_FILENAME), "w") as handle:
        handle.write("{not json")
    assert grs.read_grs_value() is None


def test_a_cache_file_holding_a_bad_value_serves_nothing(_cache_dir):
    # Edited by hand, or written by an older build: the card must never get it.
    _write_cache_file(str(_cache_dir), _payload(longitude_ii=9999.0))
    assert grs.read_grs_value() is None


def test_a_cache_file_without_a_timestamp_is_still_served(_cache_dir):
    # Its date is what the card judges the age by, not the fetch time.
    path = os.path.join(str(_cache_dir), grs.GRS_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"value": _payload()}, handle)
    assert grs.read_grs_value()["lon"] == 91.0


def test_a_cache_value_from_long_ago_is_still_served(_cache_dir):
    # Staleness is the card's business, and it judges it by the measurement date;
    # the module never withholds an old value.
    _write_cache_file(str(_cache_dir), _payload(date="2020-01-01"))
    assert grs.read_grs_value()["date"] == "2020-01-01"


# --- 4b. the fetch time the file claims ------------------------------------
#
# The value is served whatever the stamp says; the stamp only decides whether the
# file is due to be replaced. A stamp that cannot be believed is treated as 0.0,
# which reads as "due", so the file is refetched rather than kept for a day it did
# not earn. Everything here is a hand-edited file: the module writes time.time().

_BAD_STAMPS = {
    "not a number": '"yesterday"',
    "a NaN literal": "NaN",
    "an Infinity literal": "Infinity",
    "a null": "null",
    "a boolean": "true",
    "a century ahead": "1e18",
}


@pytest.mark.parametrize("label", list(_BAD_STAMPS))
def test_an_unusable_fetch_time_is_due_for_a_refresh(label, _cache_dir):
    # Written out by hand, the way an edited file arrives: json.dump would not produce
    # these literals from a value.
    path = os.path.join(str(_cache_dir), grs.GRS_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"fetched_at": %s, "value": %s}'
                     % (_BAD_STAMPS[label], json.dumps(_payload())))
    assert grs.read_grs_value()["lon"] == 91.0, f"{label}: the value is still served"
    assert grs._needs_refresh(time.time()) is True, f"{label}: the file must be replaced"


def test_a_fetch_time_a_few_hours_ahead_is_believed(_cache_dir):
    # A clock a little ahead of ours is a clock, not a lie: within the slack the file
    # is as fresh as it says.
    _write_cache_file(str(_cache_dir), _payload(), fetched_at=time.time() + 3600)
    assert grs._needs_refresh(time.time()) is False


def test_a_value_that_cannot_be_used_does_not_spend_the_day_it_claims(_cache_dir):
    """A broken value with a fresh stamp is due at once: otherwise a file written by an
    older build, or edited by hand, would keep the card on the built-in default for a
    day before anything replaced it."""
    _write_cache_file(str(_cache_dir), _payload(longitude_ii=9999.0))
    assert grs.read_grs_value() is None
    assert grs._needs_refresh(time.time()) is True


def test_a_broken_value_with_a_fresh_stamp_is_replaced_by_the_next_refresh(monkeypatch, _cache_dir):
    from nova.helpers import try_acquire_file_lock, release_file_lock
    _write_cache_file(str(_cache_dir), _payload(longitude_ii=9999.0))
    calls = _serve(monkeypatch, response=_Response(_payload(longitude_ii=91.0)))
    grs._refresh_locked()
    assert len(calls) == 1, "the file claimed to be fresh; it must still be replaced"
    assert grs.read_grs_value()["lon"] == 91.0
    handle = try_acquire_file_lock(grs.grs_cache_path())
    assert handle is not None, "the fetch kept the lock"
    release_file_lock(handle)


# --- 5. once a day ---------------------------------------------------------

def test_maybe_refresh_does_nothing_under_testing(monkeypatch):
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    calls = _serve(monkeypatch, response=_Response(_payload()))
    grs.maybe_refresh_grs(_app(testing=True))
    time.sleep(0.2)
    assert calls == []


def test_maybe_refresh_does_nothing_when_background_tasks_are_disabled(monkeypatch):
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", True)
    calls = _serve(monkeypatch, response=_Response(_payload()))
    grs.maybe_refresh_grs(_app())
    time.sleep(0.2)
    assert calls == []


def test_maybe_refresh_fetches_when_there_is_nothing_cached(monkeypatch):
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    calls = _serve(monkeypatch, response=_Response(_payload()))
    grs.maybe_refresh_grs(_app())
    _wait_for(lambda: grs.read_grs_value() is not None)
    assert len(calls) == 1
    assert grs.read_grs_value()["lon"] == 91.0


def test_maybe_refresh_returns_before_the_fetch_has_finished(monkeypatch):
    """The page must never wait: the call comes back while the thread is still out."""
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    started = threading.Event()
    release = threading.Event()

    def slow_get(url, **kwargs):
        started.set()
        release.wait(timeout=5)
        return _Response(_payload())

    monkeypatch.setattr(grs.requests, "get", slow_get)
    began = time.time()
    grs.maybe_refresh_grs(_app())
    took = time.time() - began
    assert started.wait(timeout=5), "the fetch thread never ran"
    release.set()
    _wait_for(lambda: grs.read_grs_value() is not None)
    assert took < 1.0, "the request waited for the network"


def test_maybe_refresh_does_not_fetch_again_within_a_day(monkeypatch):
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    calls = _serve(monkeypatch, response=_Response(_payload()))
    grs.maybe_refresh_grs(_app())
    time.sleep(0.2)
    assert calls == [], "a value fetched today must not be fetched again"


def test_maybe_refresh_fetches_again_after_a_day(monkeypatch):
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0),
                      fetched_at=time.time() - (grs.GRS_MAX_AGE_SECONDS + 60))
    calls = _serve(monkeypatch, response=_Response(_payload(longitude_ii=95.0)))
    grs.maybe_refresh_grs(_app())
    _wait_for(lambda: grs.read_grs_value()["lon"] == 95.0)
    assert len(calls) == 1


def test_a_fresh_file_does_not_spend_the_process_guard(monkeypatch):
    """Looking at a file that is still fresh must not buy a day of silence: the guard is
    spent only when a fetch starts, so a value 23 hours old is replaced an hour later in
    the same process, not 24 hours after the first page load."""
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0),
                      fetched_at=time.time() - (23 * 60 * 60))
    calls = _serve(monkeypatch, response=_Response(_payload(longitude_ii=95.0)))
    grs.maybe_refresh_grs(_app())
    time.sleep(0.2)
    assert calls == [], "a 23 hour old value is not due yet"
    assert grs._last_attempt == 0.0, "nothing was fetched, so nothing is spent"

    # An hour later the same process looks again, and the file is over a day old.
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0),
                      fetched_at=time.time() - (grs.GRS_MAX_AGE_SECONDS + 60))
    grs.maybe_refresh_grs(_app())
    _wait_for(lambda: grs.read_grs_value()["lon"] == 95.0)
    assert len(calls) == 1, "the fetch that the fresh file deferred never happened"


def test_maybe_refresh_tries_only_once_per_process_per_day(monkeypatch):
    """A failed fetch must not mean a thread and a request on every page load."""
    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    calls = _serve(monkeypatch, error=requests_lib.exceptions.ConnectionError("offline"))
    grs.maybe_refresh_grs(_app())
    time.sleep(0.2)
    for _ in range(5):
        grs.maybe_refresh_grs(_app())
    time.sleep(0.2)
    assert len(calls) == 1


def test_the_route_started_thread_is_a_daemon(monkeypatch):
    """A shut-down or reloaded worker must never be held open by a fetch."""
    seen = {}
    real_thread = threading.Thread

    class RecordingThread(real_thread):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(grs, "BACKGROUND_TASKS_DISABLED", False)
    monkeypatch.setattr(grs.threading, "Thread", RecordingThread)
    # The thread body itself is replaced: this test is about how the thread is made,
    # and a real one could still be reaching for requests.get when the patch is undone.
    ran = []
    monkeypatch.setattr(grs, "_fetch_and_store", lambda: ran.append(True))
    grs.maybe_refresh_grs(_app())
    _wait_for(lambda: ran)
    assert seen.get("daemon") is True
    assert seen.get("name") == "grs-refresh"


# --- 6. two workers --------------------------------------------------------

def test_a_held_lock_stops_this_worker_fetching(monkeypatch):
    from nova.helpers import release_file_lock, try_acquire_file_lock
    calls = _serve(monkeypatch, response=_Response(_payload()))
    handle = try_acquire_file_lock(grs.grs_cache_path())
    assert handle is not None, "the test could not take the lock"
    try:
        grs._refresh_locked()
    finally:
        release_file_lock(handle)
    assert calls == [], "the other worker is fetching; this one must not"


def test_the_lock_is_released_after_a_fetch(monkeypatch):
    from nova.helpers import release_file_lock, try_acquire_file_lock
    _serve(monkeypatch, response=_Response(_payload()))
    grs._refresh_locked()
    assert grs.read_grs_value() is not None
    handle = try_acquire_file_lock(grs.grs_cache_path())
    assert handle is not None, "the fetch kept the lock"
    release_file_lock(handle)


def test_the_lock_is_released_after_a_failed_fetch(monkeypatch):
    from nova.helpers import release_file_lock, try_acquire_file_lock
    _serve(monkeypatch, error=requests_lib.exceptions.ConnectionError("offline"))
    grs._refresh_locked()
    handle = try_acquire_file_lock(grs.grs_cache_path())
    assert handle is not None, "the failed fetch kept the lock"
    release_file_lock(handle)


def test_a_second_worker_does_not_fetch_what_the_first_just_wrote(monkeypatch):
    """The check runs twice: once in the request, once under the lock, so a fetch
    that finished while this thread was starting is not repeated."""
    _write_cache_file(grs.CACHE_DIR, _payload(longitude_ii=77.0))
    calls = _serve(monkeypatch, response=_Response(_payload(longitude_ii=95.0)))
    grs._refresh_locked()
    assert calls == []


# --- 7. the atomic write ---------------------------------------------------

def test_a_write_that_dies_leaves_the_previous_file_intact(monkeypatch, _cache_dir):
    _write_cache_file(str(_cache_dir), _payload(longitude_ii=77.0))
    before = _cache_file_text(_cache_dir)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(grs.json, "dump", boom)
    with pytest.raises(OSError):
        grs._write_cache(grs.validate_grs_value(_payload(longitude_ii=99.0)))

    assert _cache_file_text(_cache_dir) == before
    assert grs.read_grs_value()["lon"] == 77.0
    assert [n for n in os.listdir(str(_cache_dir)) if n.startswith(".grs.")] == [], \
        "a failed write must not leave its temp file behind"


def test_a_write_creates_the_cache_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(grs, "CACHE_DIR", str(tmp_path / "does" / "not" / "exist"))
    grs._write_cache(grs.validate_grs_value(_payload()))
    assert grs.read_grs_value()["lon"] == 91.0


def test_a_reader_never_sees_a_half_written_file(monkeypatch, _cache_dir):
    """The write goes to a temp file and is renamed, so a reader opening the path
    sees either the whole old file or the whole new one."""
    _write_cache_file(str(_cache_dir), _payload(longitude_ii=77.0))
    seen = []
    failures = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                value = grs.read_grs_value()
                if value is None:
                    failures.append("read a half-written file")
                    return
                seen.append(value["lon"])
            except Exception as e:      # noqa: BLE001 - recorded, not swallowed
                failures.append(repr(e))
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        for lon in (10.0, 20.0, 30.0, 40.0, 50.0):
            grs._write_cache(grs.validate_grs_value(_payload(longitude_ii=lon)))
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not failures, failures[0]
    assert seen, "the reader never ran"


# --- 8. the route ----------------------------------------------------------
# (What the route renders into the page is checked in test_solar_jupiter_page.py,
# with the rest of the template's assertions.)

def test_the_route_checks_for_a_refresh_before_rendering(client, monkeypatch):
    asked = []
    monkeypatch.setattr("nova.blueprints.core.maybe_refresh_grs", lambda app: asked.append(app))
    assert client.get("/solar_system").status_code == 200
    assert len(asked) == 1


def test_the_route_survives_an_unreadable_cache(client, _cache_dir):
    # A directory where the file should be: the route must still answer.
    os.makedirs(os.path.join(str(_cache_dir), grs.GRS_FILENAME))
    assert client.get("/solar_system").status_code == 200
    assert grs.read_grs_value() is None


def test_the_whole_test_run_is_gated_off(client, monkeypatch):
    """tests/conftest.py sets NOVA_DISABLE_BACKGROUND_TASKS=1, the switch the module
    header names. This is the same pin tests/test_background_tasks_disabled.py puts
    on telemetry: a page render in a test run must not reach the network."""
    assert nova.config.BACKGROUND_TASKS_DISABLED is True
    monkeypatch.setattr(grs.requests, "get",
                        lambda *a, **k: pytest.fail("the page render made an HTTP call"))
    assert client.get("/solar_system").status_code == 200
    assert grs.maybe_refresh_grs(client.application) is None
