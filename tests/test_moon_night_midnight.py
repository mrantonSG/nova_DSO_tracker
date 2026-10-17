"""The Moon rule: every night's illumination is measured at 00:00 local on the day
after the night's date.

The night that starts on the evening of D belongs to D, and its Moon is read at the
local midnight between D and D+1, in the location's timezone. This replaced three
different instants that had grown up separately: the helper's fixed "12:00 UTC" of the
date, the Outlook's and the opportunities' local noon, the heatmap's 00:00 of D itself
(the midnight *before* the night), and Ask Nova's 11 PM.

Each test pins the *instant* a call site reads, not merely the value: a moon phase is
right for the wrong reason all too easily, and rounding differences can hide a
six-hour error. tests/test_ai_prefilter_debug_night.py pins the same rule end to end
through its own route.
"""

import inspect
import re
from datetime import date, datetime, timedelta

import ephem
import pytz
import pytest

import modules.astro_calculations as astro
from modules.astro_calculations import (
    MOON_PHASE_CACHE,
    calculate_moon_phase_cached,
    night_midnight_utc,
)

LONDON = pytz.timezone("Europe/London")


@pytest.fixture(autouse=True)
def clear_moon_cache():
    """The helper memoises per (date, tz, lat, lon); a stale entry would hide a change."""
    MOON_PHASE_CACHE.clear()
    yield
    MOON_PHASE_CACHE.clear()


def _phase_at(instant):
    return round(ephem.Moon(instant).phase, 1)


# --- the instant itself ------------------------------------------------------------------

def test_night_midnight_utc_is_local_midnight_of_the_next_day():
    """D = 2026-10-17 in London (BST, +01): 2026-10-18 00:00 +01:00 = 2026-10-17 23:00Z."""
    got = night_midnight_utc("2026-10-17", "Europe/London")
    assert got == datetime(2026, 10, 17, 23, 0, tzinfo=pytz.utc)
    assert got.astimezone(LONDON) == LONDON.localize(datetime(2026, 10, 18, 0, 0))


def test_night_midnight_utc_crosses_the_dst_change_with_the_right_offset():
    """The UK clocks go forward at 01:00 on 2026-03-29. The night of 03-28 ends with a
    midnight still on GMT (00:00Z); the next night's ends on BST (23:00Z the day before).
    The offset must follow D+1, not D."""
    assert night_midnight_utc("2026-03-28", "Europe/London") == datetime(2026, 3, 29, 0, 0, tzinfo=pytz.utc)
    assert night_midnight_utc("2026-03-29", "Europe/London") == datetime(2026, 3, 29, 23, 0, tzinfo=pytz.utc)
    # the two midnights are 23 hours apart, not 24: D+1's offset is what is being read
    assert (night_midnight_utc("2026-03-29", "Europe/London")
            - night_midnight_utc("2026-03-28", "Europe/London")) == timedelta(hours=23)


def test_night_midnight_utc_reads_a_skipped_midnight_with_the_offset_before_it():
    """Santiago's clocks jump 00:00 -> 01:00 on 2025-09-07, so D+1's midnight does not
    exist. pytz's localize() default (is_dst=False) reads it with the offset in force
    before the change (-04), giving 04:00Z; strict localize raises for the same wall time."""
    got = night_midnight_utc("2025-09-06", "America/Santiago")
    assert got == datetime(2025, 9, 7, 4, 0, tzinfo=pytz.utc)
    with pytest.raises(pytz.exceptions.NonExistentTimeError):
        pytz.timezone("America/Santiago").localize(datetime(2025, 9, 7, 0, 0), is_dst=None)


def test_night_midnight_utc_accepts_a_date_object():
    """Callers with a date in hand must not have to format it back to a string."""
    assert night_midnight_utc(date(2026, 10, 17), "UTC") == night_midnight_utc("2026-10-17", "UTC")


def test_helper_without_timezone_keeps_the_noon_utc_instant():
    """tools/gen_astro_fixture.py builds the JS engine's reference this way: no timezone
    means 12:00 UTC of the date, and the fixture therefore needs no regeneration."""
    got = calculate_moon_phase_cached("2026-10-17", 51.509865, -0.118092)
    assert got == _phase_at(pytz.utc.localize(datetime(2026, 10, 17, 12, 0)))
    # and it is not the local midnight value, so the two paths are really different
    assert got != _phase_at(night_midnight_utc("2026-10-17", "Europe/London"))


def test_helper_cache_does_not_collide_between_no_timezone_and_utc():
    """"no timezone" (12:00 UTC of D) and "UTC" (00:00 UTC of D+1) are six hours apart
    and five days of phase apart here; one shared key would silently serve the wrong one."""
    D, LAT, LON = "2026-10-17", 51.509865, -0.118092
    noon_utc = calculate_moon_phase_cached(D, LAT, LON)
    utc_next_midnight = calculate_moon_phase_cached(D, LAT, LON, "UTC")
    assert utc_next_midnight == _phase_at(pytz.utc.localize(datetime(2026, 10, 18, 0, 0)))
    assert noon_utc != utc_next_midnight, "the two key forms collided on one cache entry"
    # both are correct on a second call, i.e. neither overwrote the other
    assert calculate_moon_phase_cached(D, LAT, LON) == noon_utc
    assert calculate_moon_phase_cached(D, LAT, LON, "UTC") == utc_next_midnight


def test_helper_with_timezone_uses_the_night_midnight():
    got = calculate_moon_phase_cached("2026-10-17", 51.509865, -0.118092, "Europe/London")
    assert got == _phase_at(night_midnight_utc("2026-10-17", "Europe/London"))


# --- the call sites ----------------------------------------------------------------------
# Every site is checked by reading the source for the instant it builds. That is the
# property at stake, and it stays checkable without driving each route's dependencies.

def _body(func):
    return inspect.getsource(func)


def test_every_site_advances_the_date_before_taking_the_midnight():
    """Each site must build D+1's 00:00, not D's. A missing "+ timedelta(days=1)" is the
    exact bug the heatmap had (it read the midnight before the night).

    generate_dso_notes keeps a 23:00 instant on purpose: it is the Moon-object
    separation, not the illumination, and only the phase moved.
    """
    import nova.blueprints.api as api_mod
    import nova.blueprints.core as core_mod
    import nova.ai.routes as ai_mod

    sites = {
        "nova/__init__.py Outlook": _outlook_moon_source(),
        "core.sun_events": _body(core_mod.sun_events),
        "core.graph_dashboard": _body(core_mod.graph_dashboard),
        "core.get_date_info": _body(core_mod.get_date_info),
        "core.get_imaging_opportunities": _body(core_mod.get_imaging_opportunities),
        "api.api_mobile_status": _body(api_mod.api_mobile_status),
        "api.get_moon_data_for_session": _body(api_mod.get_moon_data_for_session),
        "api.get_yearly_heatmap_chunk": _body(api_mod.get_yearly_heatmap_chunk),
        "ai.generate_dso_notes": _body(ai_mod.generate_dso_notes),
        "ai.get_best_objects": _body(ai_mod.get_best_objects),
        "ai.prefilter_debug": _body(ai_mod.prefilter_debug),
    }
    for name, source in sites.items():
        flat = _flat(source)
        assert "timedelta(days=1)" in flat or "night_midnight_utc" in flat, (
            f"{name} does not advance to D+1 before taking midnight")
        assert "ephem.Date(" not in flat, f"{name} still reads a raw date string"
        if name != "ai.generate_dso_notes":
            assert "time(23, 0)" not in flat, f"{name} still reads 11 PM"


def test_no_site_still_takes_the_moon_at_noon():
    """No moon *illumination* site may keep a local-noon or 12:00-UTC instant."""
    import nova.blueprints.core as core_mod

    for name, source in {"core.graph_dashboard": _body(core_mod.graph_dashboard),
                         "core.get_date_info": _body(core_mod.get_date_info),
                         "core.get_imaging_opportunities": _body(core_mod.get_imaging_opportunities),
                         "nova/__init__.py Outlook": _outlook_moon_source()}.items():
        assert "hour=12" not in source, f"{name} still reads a local noon"


def _outlook_moon_source():
    """The Outlook loop lives inside a function in nova/__init__.py."""
    import nova
    for name in dir(nova):
        obj = getattr(nova, name)
        if inspect.isfunction(obj) and obj.__module__ == "nova":
            src = inspect.getsource(obj)
            if "composite_score" in src and "max_moon_illumination" in src:
                return src
    raise AssertionError("compute_outlook_object_opportunities not found in nova")


def _flat(source):
    """Source with every run of whitespace collapsed to one space, so an assertion does
    not depend on where the formatter happened to wrap the line."""
    return re.sub(r"\s+", " ", source)


def test_outlook_reads_the_night_midnight_not_noon():
    source = _outlook_moon_source()
    flat = _flat(source)
    assert "datetime.combine(d + timedelta(days=1), datetime.min.time())" in flat, (
        "the Outlook loop does not take D+1's local midnight")
    assert "hour=12" not in flat


# --- end to end, one route ---------------------------------------------------------------

def test_sun_events_reports_the_night_midnight(su_client_logged_in):
    """The strip's phase comes from /sun_events; check the value the endpoint returns is
    the D+1-midnight one for a known night and a known location."""
    # conftest's client seeds "Default Test Loc" at lat 50, lon 10, UTC; the autouse
    # fixture has emptied the phase cache, so the route computes rather than serving.
    night = "2026-10-17"

    resp = su_client_logged_in.get(
        "/sun_events", query_string={"location": "Default Test Loc", "sim_date": night})
    assert resp.status_code == 200

    expected = _phase_at(night_midnight_utc(night, "UTC"))
    assert resp.get_json()["phase"] == expected
    assert expected != _phase_at(pytz.utc.localize(datetime(2026, 10, 17, 12, 0))), (
        "this night does not discriminate against the old noon rule")
