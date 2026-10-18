"""Unit checks for static/js/nova_opportunities.js, the graph view's Imaging Opportunities.

Each test drives the module in node the way the page will load it — plain scripts in
one vm context, the four files in order — and asserts a rule
core.get_imaging_opportunities() holds to, with the inputs chosen so the event the
rule turns on is the module's one free variable.

Most cases wrap NovaAstro rather than fake it: the stub is a copy of the real engine
with only the named functions replaced (sunEvents, moonPhaseAt, observableDuration,
moonSeparation), so the module's own filters, rounding and row building still run.
moonSeparation is always recorded, so the instant the module asks for is visible.
Where a rule depends on the night window (from/to, the horizon mask) only sunEvents
is pinned and the real observableDuration runs.
"""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import pytz

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_dashboard_rows.js", "nova_outlook.js", "nova_opportunities.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const args = process.argv.slice(2);
const outFile = args.pop(), inFile = args.pop();
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of args) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
const real = context.NovaAstro, realOutlook = context.NovaOutlook;

function runOne(c) {
    const st = c.stub || {};
    const s = Object.assign(Object.create(null), real);
    s._sepCalls = [];
    if (st.events) s.sunEvents = () => Object.assign({}, st.events);
    if (st.phase !== undefined) s.moonPhaseAt = () => st.phase;
    if (st.dur) s.observableDuration = () => Object.assign({}, st.dur);
    s.moonSeparation = function (ra, dec, ms) {
        s._sepCalls.push(ms);
        if (st.sep === 'NaN') return NaN;
        return st.sep !== undefined ? st.sep : real.moonSeparation.apply(null, arguments);
    };
    context.NovaAstro = s;
    if (c.drop === 'NovaOutlook') delete context.NovaOutlook;
    if (c.drop === '_night') context.NovaOutlook = { opportunities: realOutlook.opportunities };
    try {
        const rows = context.NovaOpportunities.forObject(c.opts);
        return { rows: rows, sepCalls: s._sepCalls };
    } catch (e) {
        return { error: String((e && e.message) || e) };
    } finally {
        context.NovaAstro = real;
        context.NovaOutlook = realOutlook;
    }
}

const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => Object.assign({ id: c.id }, runOne(c)))));
"""


def _run(tmp_path, cases):
    (tmp_path / "opp_driver.js").write_text(_DRIVER)
    (tmp_path / "opp_cases.json").write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(tmp_path / "opp_driver.js"),
         *[str(STATIC_JS / f) for f in ENGINE_FILES],
         str(tmp_path / "opp_cases.json"), str(tmp_path / "opp_out.json")],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: r for r in json.loads((tmp_path / "opp_out.json").read_text())}


def _one(tmp_path, case):
    return _run(tmp_path, [case])[case["id"]]


def _london(y, mo, d, hh, mm):
    return int(pytz.timezone("Europe/London").localize(datetime(y, mo, d, hh, mm)).timestamp() * 1000)


NOW = _london(2026, 1, 15, 12, 30)     # first night 2026-01-15


def _loc(**over):
    loc = {"name": "London", "lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London",
           "altitude_threshold": 0, "sampling_interval": 15, "horizon_mask": []}
    loc.update(over)
    return loc


def _obj(name="M42", **over):
    obj = {"Object": name, "ra": 6.0, "dec": 0.0, "ActiveProject": False}
    obj.update(over)
    return obj


def _settings(**over):
    s = {"min_observable_minutes": 0, "max_moon_illumination": 100, "min_angular_separation": 0,
         "min_max_altitude": 0, "search_horizon_months": 1}
    s.update(over)
    return s


def _case(cid, stub=None, settings=None, loc=None, objects=None, name="M42", now=NOW, **extra):
    opts = {"inputs": {"location": loc if loc is not None else _loc(),
                       "settings": settings if settings is not None else _settings(),
                       "objects": objects if objects is not None else [_obj()]},
            "objectName": name, "nowMs": now}
    opts.update(extra)
    return {"id": cid, "stub": stub, "opts": opts}


def _fixed(minutes=720, max_alt=90, phase=0, sep=180, frm=None, to=None):
    """Every night the same: duration, altitude, Moon and separation pinned."""
    return {"dur": {"minutes": minutes, "maxAltitude": max_alt, "from": frm, "to": to},
            "phase": phase, "sep": sep}


EVENTS = {"astronomical_dusk": "20:00", "astronomical_dawn": "05:00", "sunset": "17:00", "sunrise": "07:00"}


def _route_stars(max_alt, minutes, phase, sep):
    """core.get_imaging_opportunities()'s score and stars, verbatim."""
    MIN_ALTITUDE = 20
    score_alt = max(0, min((max_alt - MIN_ALTITUDE) / (90 - MIN_ALTITUDE), 1))
    score_duration = min(minutes * 60 / 43200, 1)
    score_moon_illum = 1 - min(phase / 100, 1)
    score_moon_sep_dynamic = (1 - (phase / 100)) + (phase / 100) * min(sep / 180, 1)
    composite = 100 * (0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic)
    return composite, int(round((composite / 100) * 4)) + 1


# --- no score cut ----------------------------------------------------------------

def test_no_score_cut_a_zero_score_night_is_kept(tmp_path):
    """Broken variant that fails: Outlook's `if (!(score > 75)) continue` (no rows)."""
    stub = _fixed(minutes=0, max_alt=20, phase=100, sep=0)
    assert _route_stars(20, 0, 100, 0) == (0.0, 1)
    r = _one(tmp_path, _case("x", stub))
    assert len(r["rows"]) == 30
    assert r["rows"][0]["rating"] == "★☆☆☆☆"


# --- every star boundary, halves to even -----------------------------------------

@pytest.mark.parametrize("max_alt, minutes, phase, sep, boundary, stars", [
    (20, 360, 100, 45, 12.5, 1),     # 0.5 -> 0: Math.round (half up) gives 2 stars
    (90, 360, 100, 90, 37.5, 3),     # 1.5 -> 2: Math.floor / trunc gives 2 stars
    (90, 720, 75, 135, 62.5, 3),     # 2.5 -> 2: Math.round gives 4 stars
    (90, 720, 25, 135, 87.5, 5),     # 3.5 -> 4: Math.floor / trunc gives 4 stars
])
def test_star_boundaries_round_half_to_even(tmp_path, max_alt, minutes, phase, sep, boundary, stars):
    """Broken variants that fail: Math.round (12.5, 62.5) and Math.floor (37.5, 87.5)."""
    assert _route_stars(max_alt, minutes, phase, sep) == (boundary, stars)   # exactly on the boundary
    r = _one(tmp_path, _case("x", _fixed(minutes=minutes, max_alt=max_alt, phase=phase, sep=sep)))
    assert r["rows"][0]["rating"] == "★" * stars + "☆" * (5 - stars)


# --- the four filters ------------------------------------------------------------

@pytest.mark.parametrize("setting, value, stub_key, kept, dropped", [
    ("min_observable_minutes", 60, "minutes", 60, 45),       # dropped when duration <  min
    ("min_max_altitude", 30, "max_alt", 30, 29.9),           # dropped when max alt  <  min
    ("max_moon_illumination", 20, "phase", 20, 20.1),        # dropped when Moon     >  max
    ("min_angular_separation", 30, "sep", 30, 29.9),         # dropped when sep      <  min
])
def test_each_filter_drops_a_night_and_keeps_its_boundary(tmp_path, setting, value, stub_key, kept, dropped):
    """Broken variants that fail: the filter removed (`dropped` is kept), or its
    comparison made strict the other way, e.g. `<=` (`kept` is dropped)."""
    settings = _settings(**{setting: value})
    r = _run(tmp_path, [_case("kept", _fixed(**{stub_key: kept}), settings=settings),
                        _case("dropped", _fixed(**{stub_key: dropped}), settings=settings)])
    assert len(r["kept"]["rows"]) == 30
    assert r["dropped"]["rows"] == []


# --- rounding --------------------------------------------------------------------

def test_fields_round_as_the_route_rounds_them(tmp_path):
    """max_alt and moon_illumination are Python floats on the server (round halves to
    even on the exact decimal), moon_separation a numpy.float64 (rint(x * 10) / 10),
    obs_minutes int(...).
    Broken variants that fail: Python's round for the separation (0.15 -> 0.1),
    Math.round(x * 10) / 10 or toFixed for any of the three (0.25 / 39.25 -> .3),
    Math.round for obs_minutes (600.5 -> 601)."""
    expected_sep = {s: float(round(np.float64(s), 1)) for s in (0.15, 0.25, 39.25)}
    assert expected_sep == {0.15: 0.2, 0.25: 0.2, 39.25: 39.2}
    assert round(0.15, 1) == 0.1                      # where Python's round differs
    cases = [_case(f"sep{s}", _fixed(minutes=600.5, max_alt=39.25, phase=0.25, sep=s)) for s in expected_sep]
    r = _run(tmp_path, cases)
    for s, want in expected_sep.items():
        row = r[f"sep{s}"]["rows"][0]
        assert row["moon_separation"] == want, (s, row)
        assert row["max_alt"] == round(39.25, 1) == 39.2
        assert row["moon_illumination"] == round(0.25, 1) == 0.2
        assert row["obs_minutes"] == 600 and isinstance(row["obs_minutes"], int)


def test_row_keys_and_types(tmp_path):
    """Broken variant that fails: any renamed or extra key (e.g. Outlook's obs_dur / score)."""
    row = _one(tmp_path, _case("x", _fixed(frm=None, to=None)))["rows"][0]
    assert list(row) == ["date", "obs_minutes", "from_time", "to_time", "max_alt",
                         "moon_illumination", "moon_separation", "rating"]
    assert row["date"] == "2026-01-15"


# --- from / to -------------------------------------------------------------------

def test_from_to_are_na_when_nothing_is_observable(tmp_path):
    """min_observable_minutes 0 lets a night with no observable sample through.
    Broken variant that fails: formatting a null sample (prints 'NaN:NaN')."""
    r = _one(tmp_path, _case("x", _fixed(minutes=0, max_alt=50, frm=None, to=None)))
    row = r["rows"][0]
    assert (row["from_time"], row["to_time"], row["obs_minutes"]) == ("N/A", "N/A", 0)


def test_from_to_are_the_first_and_last_observable_sample(tmp_path):
    """A circumpolar object is above a 0 degree threshold all night: from is dusk, to
    the last sample at or before dawn. On 2026-03-28 the clocks change overnight:
    the route prints 04:00 (dusk's GMT offset, carried by pytz), the wall clock reads
    05:00. The night after, both ends are BST.
    Broken variant that fails: NovaAstro.formatHM (the true wall clock, 05:00)."""
    stub = {"events": EVENTS}
    obj = [_obj(ra=0.0, dec=89.0)]
    r = _run(tmp_path, [
        _case("jan", stub, objects=obj),
        _case("dst", stub, objects=obj, now=_london(2026, 3, 28, 12, 30)),
    ])
    jan = r["jan"]["rows"][0]
    assert (jan["date"], jan["from_time"], jan["to_time"], jan["obs_minutes"]) == ("2026-01-15", "20:00", "05:00", 37 * 15)
    dst, after = r["dst"]["rows"][0], r["dst"]["rows"][1]
    assert (dst["date"], dst["from_time"], dst["to_time"]) == ("2026-03-28", "20:00", "04:00")
    assert dst["obs_minutes"] == 33 * 15             # 20:00 GMT to 04:00 UTC: the night is an hour short
    assert (after["date"], after["from_time"], after["to_time"]) == ("2026-03-29", "20:00", "05:00")


# --- the separation instant ------------------------------------------------------

def test_dusk_missing_uses_local_20_00_for_the_moon(tmp_path):
    """Broken variant that fails: the night window's start (sunset, 17:00 here)."""
    stub = dict(_fixed(), events=dict(EVENTS, astronomical_dusk="N/A"))
    r = _one(tmp_path, _case("x", stub))
    assert r["sepCalls"][0] == _london(2026, 1, 15, 20, 0)


def test_dusk_present_is_used_not_20_00(tmp_path):
    """Broken variant that fails: always 20:00."""
    stub = dict(_fixed(), events=dict(EVENTS, astronomical_dusk="17:23"))
    r = _one(tmp_path, _case("x", stub))
    assert r["sepCalls"][0] == _london(2026, 1, 15, 17, 23)


# --- the nights ------------------------------------------------------------------

def test_first_night_is_todays_calendar_date_not_the_observing_night(tmp_path):
    """2026-07-04 23:30 UTC is 00:30 on the 5th in London (BST): the route's
    datetime.now(local_tz).date() is the 5th, although the observing night is the 4th.
    A sim date in opts is ignored.
    Broken variants that fail: NovaAstro.observingDate (the 4th), honouring
    opts.simDate (2026-03-05)."""
    r = _one(tmp_path, _case("x", _fixed(), now=1783207800000, simDate="2026-03-05"))
    assert r["rows"][0]["date"] == "2026-07-05"


def test_horizon_is_thirty_days_per_month_in_date_order(tmp_path):
    """Broken variants that fail: months * 31, `i <= months * 30`, or a reversed order."""
    r = _one(tmp_path, _case("x", _fixed(), settings=_settings(search_horizon_months=2)))
    dates = [row["date"] for row in r["rows"]]
    assert len(dates) == 60 and dates == sorted(dates)
    assert (dates[0], dates[-1]) == ("2026-01-15", "2026-03-15")


# --- the horizon mask ------------------------------------------------------------

def test_horizon_mask_is_applied_and_max_alt_is_taken_before_it(tmp_path):
    """An object on the equator is up only between azimuth 90 and 270; a mask of 80
    degrees across the south hides all of it. max_alt is the same either way.
    Broken variants that fail: the mask not passed (minutes > 0), the maximum taken
    over the observable samples only (max_alt 0)."""
    stub = {"events": EVENTS}
    masked = _loc(horizon_mask=[[90.0, 80.0], [270.0, 80.0]])
    r = _run(tmp_path, [_case("open", stub), _case("masked", stub, loc=masked)])
    open_row, masked_row = r["open"]["rows"][0], r["masked"]["rows"][0]
    assert open_row["obs_minutes"] > 0
    assert (masked_row["obs_minutes"], masked_row["from_time"], masked_row["to_time"]) == (0, "N/A", "N/A")
    assert masked_row["max_alt"] == open_row["max_alt"] > 30


# --- the object ------------------------------------------------------------------

def test_object_is_found_ignoring_case_whatever_its_flags(tmp_path):
    """get_ra_dec() looks the name up lowercased in g.objects_map, which holds every
    object of the user. Broken variant that fails: an exact-case match (throws)."""
    objects = [_obj("NGC 1", ra=1.0, dec=1.0), _obj("M42", enabled=False, ActiveProject=False)]
    r = _one(tmp_path, _case("x", _fixed(), objects=objects, name="m42"))
    assert len(r["rows"]) == 30


# --- the throws (the page falls back to the route) --------------------------------

_THROWS = {
    "location_empty": dict(loc={}),
    "no_settings": dict(settings={}),
    "objects_not_a_list": dict(objects={"M42": _obj()}),
    "lat_not_finite": dict(loc=_loc(lat=None)),
    "threshold_missing": dict(loc={k: v for k, v in _loc().items() if k != "altitude_threshold"}),
    "mask_not_a_list": dict(loc=_loc(horizon_mask="0,0")),
    "criterion_a_string": dict(settings=_settings(min_observable_minutes="60")),
    "months_below_one": dict(settings=_settings(search_horizon_months=0)),
    "object_not_found": dict(name="M31"),
    "object_twice": dict(objects=[_obj("M42"), _obj("m42")]),
    "ra_missing": dict(objects=[_obj(ra=None)]),
    "dec_missing": dict(objects=[_obj(dec=None)]),
    "now_missing": dict(now=None),
}
_THROWS.update({f"no_{k}": dict(settings={kk: vv for kk, vv in _settings().items() if kk != k})
                for k in _settings()})
# Cases whose inputs block is null rather than a dict (_case would fill in the default).
_NULLED = {"location_null": "location", "settings_null": "settings"}
_THROWS.update({cid: {} for cid in _NULLED})


@pytest.mark.parametrize("cid", sorted(_THROWS))
def test_throws_its_own_error(tmp_path, cid):
    """Each guard throws a 'nova_opportunities:' error, not whatever happens next.
    Broken variant that fails, per case: that guard removed (rows come back, or a
    TypeError / the engine's own error without the prefix)."""
    if cid in _NULLED:
        case = _case(cid, _fixed())
        case["opts"]["inputs"][_NULLED[cid]] = None
    else:
        case = _case(cid, _fixed(), **_THROWS[cid])
    r = _one(tmp_path, case)
    assert "error" in r, r.get("rows", [])[:1]
    assert r["error"].startswith("nova_opportunities:"), r["error"]


@pytest.mark.parametrize("drop", ["NovaOutlook", "_night"])
def test_throws_without_the_outlook_engine(tmp_path, drop):
    """Broken variant that fails: no engine check (a TypeError instead)."""
    case = dict(_case("x", _fixed()), drop=drop)
    r = _one(tmp_path, case)
    assert r["error"].startswith("nova_opportunities:"), r["error"]


def test_throws_when_the_engine_returns_no_separation(tmp_path):
    """Broken variant that fails: NaN treated as 'not below the minimum' (rows kept)
    or skipped (no rows, no error)."""
    r = _one(tmp_path, _case("x", dict(_fixed(), sep="NaN")))
    assert r["error"].startswith("nova_opportunities:"), r.get("rows", [])[:1]
