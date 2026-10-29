"""
Regression tests for the Open-Meteo / 7Timer! merge in get_hybrid_weather_forecast.

Every 7Timer! block must land at its own init + timepoint (UTC). Blocks that match
no Open-Meteo hour are dropped when Open-Meteo is readable, and kept (at the
correct time) when Open-Meteo is missing or unreadable.
"""
import sys, os
from datetime import datetime, timedelta, UTC

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nova.blueprints.api as api
from nova.config import weather_cache

LAT, LON = 47.5, 13.0
CACHE_KEY = f"hybrid_{round(LAT, 5)}_{round(LON, 5)}"

OM_START = datetime(2026, 10, 29, 0, tzinfo=UTC)
OM_HOURS = 24

ASTRO_INIT = "2026102818"
# timepoint -> seeing; distinct values so each block can be traced
ASTRO_SEEING = {3: 2, 6: 3, 9: 4, 12: 5}


def _utc(day, hour):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def _open_meteo_payload(drop_one_value=False):
    times = [(OM_START + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(OM_HOURS)]
    cc_low = [h * 4 for h in range(OM_HOURS)]  # distinct cloud values, 0..92 %
    if drop_one_value:
        cc_low = cc_low[:-1]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [10.0 + h for h in range(OM_HOURS)],
            "relative_humidity_2m": [50 + h for h in range(OM_HOURS)],
            "cloud_cover_low": cc_low,
            "cloud_cover_mid": [0] * OM_HOURS,
            "cloud_cover_high": [0] * OM_HOURS,
        }
    }


def _astro_payload():
    return {
        "product": "astro",
        "init": ASTRO_INIT,
        "dataseries": [
            {
                "timepoint": tp,
                "cloudcover": 1,
                "seeing": seeing,
                "transparency": 3,
                "lifted_index": 2,
                "rh2m": 5,
                "wind10m": {"direction": "N", "speed": 2},
                "temp2m": 8,
                "prec_type": "none",
            }
            for tp, seeing in ASTRO_SEEING.items()
        ],
    }


def _run(monkeypatch, open_meteo):
    monkeypatch.setattr(api, "get_open_meteo_data", lambda lat, lon: open_meteo)
    monkeypatch.setattr(api, "get_weather_data_with_retries", lambda lat, lon, product="meteo": _astro_payload())
    weather_cache.pop(CACHE_KEY, None)
    try:
        return api.get_hybrid_weather_forecast(LAT, LON)
    finally:
        weather_cache.pop(CACHE_KEY, None)


def _abs_times(result):
    init = datetime.strptime(result["init"], "%Y%m%d%H").replace(tzinfo=UTC)
    return [init + timedelta(hours=int(b["timepoint"])) for b in result["dataseries"]]


def _by_time(result):
    return dict(zip(_abs_times(result), result["dataseries"]))


def _expected_astro_times():
    init = datetime.strptime(ASTRO_INIT, "%Y%m%d%H").replace(tzinfo=UTC)
    return {init + timedelta(hours=tp): seeing for tp, seeing in ASTRO_SEEING.items()}


def test_open_meteo_readable_drops_block_before_first_hour(monkeypatch):
    result = _run(monkeypatch, _open_meteo_payload())
    times = _abs_times(result)
    by_time = _by_time(result)

    assert _utc(28, 21) not in times
    assert by_time[_utc(29, 0)]["seeing"] == ASTRO_SEEING[6]
    assert by_time[_utc(29, 3)]["seeing"] == ASTRO_SEEING[9]
    assert by_time[_utc(29, 6)]["seeing"] == ASTRO_SEEING[12]
    assert len(times) == len(set(times))
    assert len(times) == OM_HOURS


@pytest.mark.parametrize("open_meteo", [
    pytest.param(_open_meteo_payload(drop_one_value=True), id="unreadable"),
    pytest.param(None, id="missing"),
])
def test_fallback_places_blocks_at_7timer_init_plus_timepoint(monkeypatch, open_meteo):
    result = _run(monkeypatch, open_meteo)
    by_time = _by_time(result)
    expected = _expected_astro_times()

    assert sorted(by_time) == sorted(expected)
    for t, seeing in expected.items():
        assert by_time[t]["seeing"] == seeing
