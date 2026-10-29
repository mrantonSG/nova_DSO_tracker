"""
Regression tests for the Open-Meteo / 7Timer! merge in get_hybrid_weather_forecast.

Every 7Timer! block must land at its own init + timepoint (UTC). Blocks that match
no Open-Meteo hour are dropped when Open-Meteo is readable, and kept (at the
correct time) when Open-Meteo is missing or unreadable.

Each block covers its own hour and the two after it: with Open-Meteo those hours take
its seeing and transparency, without it they are copies of the block.
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
ASTRO_INIT_TIME = datetime(2026, 10, 28, 18, tzinfo=UTC)
# timepoint -> seeing; distinct values so each block can be traced
ASTRO_SEEING = {3: 2, 6: 3, 9: 4, 12: 5}


def _utc(day, hour):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def _open_meteo_payload(drop_one_value=False, hours=OM_HOURS):
    times = [(OM_START + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    cc_low = [h * 4 for h in range(hours)]  # distinct cloud values, 0..92 %
    if drop_one_value:
        cc_low = cc_low[:-1]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [10.0 + h for h in range(hours)],
            "relative_humidity_2m": [50 + h for h in range(hours)],
            "cloud_cover_low": cc_low,
            "cloud_cover_mid": [0] * hours,
            "cloud_cover_high": [0] * hours,
        }
    }


def _astro_payload(seeing_by_tp=ASTRO_SEEING):
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
            for tp, seeing in seeing_by_tp.items()
        ],
    }


def _run(monkeypatch, open_meteo, astro=None):
    astro = _astro_payload() if astro is None else astro  # {} = 7Timer! failed
    monkeypatch.setattr(api, "get_open_meteo_data", lambda lat, lon: open_meteo)
    monkeypatch.setattr(api, "get_weather_data_with_retries", lambda lat, lon, product="meteo": astro)
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


def _expected_astro_hours():
    """Every hour the 7Timer! blocks cover -> that block's seeing."""
    return {ASTRO_INIT_TIME + timedelta(hours=tp + h): seeing
            for tp, seeing in ASTRO_SEEING.items() for h in range(3)}


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
    expected = _expected_astro_hours()

    assert sorted(by_time) == sorted(expected)
    for t, seeing in expected.items():
        assert by_time[t]["seeing"] == seeing
        assert by_time[t]["cloudcover"] == 1
        assert by_time[t]["transparency"] == 3


def test_seeing_and_transparency_spread_over_the_three_hours_of_their_block(monkeypatch):
    by_time = _by_time(_run(monkeypatch, _open_meteo_payload()))
    clouds_only = _by_time(_run(monkeypatch, _open_meteo_payload(), astro={}))

    for t, seeing in _expected_astro_hours().items():
        if t < OM_START:
            assert t not in by_time  # 28 21Z..23Z: before Open-Meteo, dropped
            continue
        assert by_time[t]["seeing"] == seeing
        assert by_time[t]["transparency"] == 3
    assert by_time[_utc(29, 9)]["seeing"] == -9999  # past the last block's three hours
    # Open-Meteo keeps the clouds of every hour
    assert {t: b["cloudcover"] for t, b in by_time.items()} == \
        {t: b["cloudcover"] for t, b in clouds_only.items()}


def test_spread_never_overwrites_an_hours_own_value(monkeypatch):
    # Blocks one hour apart: 29 00Z (seeing 3) and 29 01Z (seeing 6)
    by_time = _by_time(_run(monkeypatch, _open_meteo_payload(), astro=_astro_payload({6: 3, 7: 6})))

    assert by_time[_utc(29, 0)]["seeing"] == 3
    assert by_time[_utc(29, 1)]["seeing"] == 6  # its own value, not the 00Z block's
    assert by_time[_utc(29, 2)]["seeing"] == 6  # covered by both: the later block wins
    assert by_time[_utc(29, 3)]["seeing"] == 6
    assert by_time[_utc(29, 4)]["seeing"] == -9999


def test_spread_adds_no_hour_past_the_last_open_meteo_hour(monkeypatch):
    # Open-Meteo 29 00Z..07Z; the 29 06Z block covers 06Z..08Z
    result = _run(monkeypatch, _open_meteo_payload(hours=8))
    by_time = _by_time(result)

    assert by_time[_utc(29, 7)]["seeing"] == ASTRO_SEEING[12]
    assert _utc(29, 8) not in by_time
    assert len(result["dataseries"]) == 8
