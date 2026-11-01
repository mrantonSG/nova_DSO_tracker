"""
Tests for /api/upper_wind, the jet stream wind behind the Solar System page's seeing line.

Open-Meteo is never called: requests.get is replaced by a fake that records its calls.
The endpoint has its own request and cache (upper_wind_cache); the hybrid forecast,
its Open-Meteo request and /api/get_weather_forecast must stay as they were.
"""
import sys, os
from datetime import datetime, timedelta, UTC

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nova.blueprints.api as api
from nova.config import upper_wind_cache, weather_cache

LAT, LON = 47.5, 13.0
CACHE_KEY = f"upperwind_{round(LAT, 5)}_{round(LON, 5)}"
EMPTY = {"times": [], "w200": [], "w250": [], "w300": [], "unit": "m/s"}


def _payload(hours=3):
    return {
        "hourly_units": {"wind_speed_250hPa": "m/s"},
        "hourly": {
            "time": [f"2026-11-01T{h:02d}:00" for h in range(hours)],
            "wind_speed_200hPa": [10.0 + h for h in range(hours)],
            "wind_speed_250hPa": [20.5, None, 22][:hours],
            "wind_speed_300hPa": [30, 31, "x"][:hours],
        },
    }


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body
        self.text = ""

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeGet:
    """requests.get stand-in: answers with `response` (or raises it) and records each call."""
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture(autouse=True)
def _clean_cache():
    upper_wind_cache.clear()
    yield
    upper_wind_cache.clear()


def _fake(monkeypatch, response):
    fake = _FakeGet(response)
    monkeypatch.setattr(api.requests, "get", fake)
    return fake


def _get(client, lat=LAT, lon=LON):
    return client.get('/api/upper_wind', query_string={'lat': lat, 'lon': lon})


def test_response_shape(client, monkeypatch):
    """Times as ISO UTC with Z; the three levels in m/s, a missing or non-numeric value null."""
    _fake(monkeypatch, _Resp(200, _payload()))
    resp = _get(client)
    assert resp.status_code == 200
    assert resp.get_json() == {
        "times": ["2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z", "2026-11-01T02:00:00Z"],
        "w200": [10.0, 11.0, 12.0],
        "w250": [20.5, None, 22.0],
        "w300": [30.0, 31.0, None],
        "unit": "m/s",
    }


def test_request_parameters(client, monkeypatch):
    """One request: the three levels, m/s, 7 days, UTC, the standard timeout."""
    fake = _fake(monkeypatch, _Resp(200, _payload()))
    _get(client)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.open-meteo.com/v1/forecast"
    assert call["params"] == {
        "latitude": LAT, "longitude": LON,
        "hourly": "wind_speed_200hPa,wind_speed_250hPa,wind_speed_300hPa",
        "wind_speed_unit": "ms", "forecast_days": 7, "timezone": "UTC",
    }
    assert call["timeout"] == api.DEFAULT_HTTP_TIMEOUT


def test_cache_hit_makes_no_second_request(client, monkeypatch):
    """Within 3 hours the same location (to 5 decimals) is served from the cache."""
    fake = _fake(monkeypatch, _Resp(200, _payload()))
    first = _get(client).get_json()
    second = _get(client, lat=LAT + 1e-7).get_json()
    assert len(fake.calls) == 1
    assert first == second
    assert list(upper_wind_cache) == [CACHE_KEY]


def test_cache_expires_after_three_hours(client, monkeypatch):
    """A fresh entry expires 3 hours on; an expired one is fetched again."""
    fake = _fake(monkeypatch, _Resp(200, _payload()))
    before = datetime.now(UTC)
    _get(client)
    expires = upper_wind_cache[CACHE_KEY]["expires"]
    assert before + timedelta(hours=3) <= expires <= datetime.now(UTC) + timedelta(hours=3)
    upper_wind_cache[CACHE_KEY]["expires"] = datetime.now(UTC) - timedelta(seconds=1)
    _get(client)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("failure", [
    _Resp(503, None),
    _Resp(200, ValueError("not JSON")),
    _Resp(200, {"hourly": {"time": ["2026-11-01T00:00"], "wind_speed_200hPa": [1]}}),
    api.requests.exceptions.Timeout("slow"),
])
def test_stale_copy_on_failure(client, monkeypatch, failure):
    """An expired entry is still returned when the fetch fails (HTTP error, bad JSON,
    missing level, timeout), and kept for the next try."""
    stale = dict(EMPTY, times=["2026-10-31T00:00:00Z"], w200=[5.0], w250=[6.0], w300=[7.0])
    upper_wind_cache[CACHE_KEY] = {"data": stale, "expires": datetime.now(UTC) - timedelta(hours=1),
                                   "last_err_ts": None}
    _fake(monkeypatch, failure)
    resp = _get(client)
    assert resp.status_code == 200 and resp.get_json() == stale
    assert upper_wind_cache[CACHE_KEY]["data"] == stale


def test_failure_without_cache_is_200_with_empty_arrays(client, monkeypatch):
    _fake(monkeypatch, api.requests.exceptions.ConnectionError("down"))
    resp = _get(client)
    assert resp.status_code == 200
    assert resp.get_json() == EMPTY


def test_error_logged_at_most_once_per_15_minutes(client, monkeypatch, capsys):
    """Every failed call tries once again (no sleeps), but logs only once per 15 minutes."""
    fake = _fake(monkeypatch, api.requests.exceptions.ConnectionError("down"))
    for _ in range(3):
        _get(client)
    assert len(fake.calls) == 3
    assert capsys.readouterr().out.count("[Upper Wind] WARN") == 1
    upper_wind_cache[CACHE_KEY]["last_err_ts"] = datetime.now(UTC) - timedelta(minutes=16)
    _get(client)
    assert capsys.readouterr().out.count("[Upper Wind] WARN") == 1


@pytest.mark.parametrize("query", [
    {}, {"lat": LAT}, {"lon": LON}, {"lat": "abc", "lon": LON}, {"lat": "nan", "lon": LON},
    {"lat": 91, "lon": LON}, {"lat": LAT, "lon": -180.5}, {"lat": "inf", "lon": LON},
])
def test_invalid_coordinates_are_400(client, monkeypatch, query):
    fake = _fake(monkeypatch, _Resp(200, _payload()))
    resp = client.get('/api/upper_wind', query_string=query)
    assert resp.status_code == 400
    assert fake.calls == []


def test_get_weather_forecast_unchanged(client, monkeypatch):
    """Guard: the hybrid forecast's Open-Meteo request asks for the same five variables as
    before (the iOS parity fixture pins it), and /api/get_weather_forecast keeps its shape
    and never touches the upper-wind cache."""
    fake = _fake(monkeypatch, _Resp(200, {"hourly": {"time": []}}))
    api.get_open_meteo_data(LAT, LON)
    assert fake.calls[0]["params"] == {
        "latitude": LAT, "longitude": LON,
        "hourly": "temperature_2m,relative_humidity_2m,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
        "forecast_days": 7, "timezone": "UTC",
    }

    monkeypatch.setattr(api, "get_hybrid_weather_forecast", lambda lat, lon: {
        "init": "2026110100", "dataseries": [{"timepoint": 1, "cloudcover": 2, "seeing": 3, "transparency": 4}]})
    resp = client.get('/api/get_weather_forecast', query_string={'lat': LAT, 'lon': LON, 'tz': 'UTC'})
    assert resp.status_code == 200
    assert resp.get_json() == {"weather_forecast": [{
        "start": "2026-11-01T01:00:00+00:00", "end": "2026-11-01T02:00:00+00:00",
        "cloudcover": 2, "seeing": 3, "transparency": 4,
    }]}
    assert len(upper_wind_cache) == 0
    assert not any(str(k).startswith("upperwind_") for k in weather_cache)
