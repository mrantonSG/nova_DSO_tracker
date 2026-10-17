"""Unit checks for static/js/nova_outlook.js, the browser's Outlook table.

Each test drives the module in node the way the page loads it — plain scripts in
one vm context, the three files the page loads, in order — and asserts a rule the
worker holds to, with the inputs chosen so the event the rule turns on is the
module's one free variable.

Most cases wrap NovaAstro's four decision functions rather than fake them: the
wrapper delegates to the real engine for everything except the value under test,
so the module's own ordering, rounding and row building still run, and `sunEvents`
and `observableDuration` can be pinned exactly. Two things need that:

  - the score is kept only when it is greater than 75, so the boundary test moves
    only max altitude while the other three terms are fixed to known fractions
    (score 75.0 exactly at 55 degrees, 75.1 at 55.5);
  - the dusk 20:00 fallback is on a path no real geometry reaches at these sites:
    on a night with no astronomical dusk (London from 2026-05-23) the twilight
    window is under an hour and the Moon is close, so nothing survives to the
    separation step. The wrapper records the instant moonSeparation() is called
    with, which is the fallback itself.
"""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import pytz

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_dashboard_rows.js", "nova_outlook.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Loads the three files the way the page does and runs each case. A stub, when
# present, replaces NovaAstro with a copy of itself whose four decision functions
# are overridden and whose moonSeparation/moonPhaseAt calls are recorded.
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
const real = context.NovaAstro;

function runOne(c) {
    if (c.stub) {
        const s = Object.assign(Object.create(null), real);
        s._sepCalls = [];
        s.sunEvents = () => ({
            astronomical_dusk: c.stub.dusk === undefined ? '20:00' : c.stub.dusk,
            astronomical_dawn: '05:00', sunset: c.stub.sunset || '21:00', sunrise: '05:00',
        });
        s.moonPhaseAt = () => c.stub.phase;
        s.observableDuration = () => ({ minutes: c.stub.minutes, maxAltitude: c.stub.maxAltitude, from: null, to: null });
        s.moonSeparation = (ra, dec, ms) => { s._sepCalls.push(ms); return c.stub.sep; };
        context.NovaAstro = s;
    }
    try {
        const rows = context.NovaOutlook.opportunities(c.opts);
        return { rows: rows, sepCalls: c.stub ? context.NovaAstro._sepCalls : null };
    } catch (e) {
        return { error: String((e && e.message) || e) };
    } finally {
        context.NovaAstro = real;
    }
}

const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => Object.assign({ id: c.id }, runOne(c)))));
"""


def _run(tmp_path, cases):
    (tmp_path / "outlook_driver.js").write_text(_DRIVER)
    (tmp_path / "outlook_cases.json").write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(tmp_path / "outlook_driver.js"),
         *[str(STATIC_JS / f) for f in ENGINE_FILES],
         str(tmp_path / "outlook_cases.json"), str(tmp_path / "outlook_out.json")],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: r for r in json.loads((tmp_path / "outlook_out.json").read_text())}


def _loc(**over):
    loc = {"lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London",
           "altitude_threshold": 0, "sampling_interval": 15, "horizon_mask": []}
    loc.update(over)
    return loc


def _obj(name, **over):
    obj = {"Object": name, "ra": 0.0, "dec": 0.0, "ActiveProject": True, "has_framing": False,
           "common_name": name, "project": "p", "type": "T", "constellation": "C",
           "magnitude": "1", "size": "2", "sb": "3"}
    obj.update(over)
    return obj


def _settings(**over):
    s = {"min_observable_minutes": 0, "max_moon_illumination": 100, "min_angular_separation": 0,
         "min_max_altitude": 0, "search_horizon_months": 1}
    s.update(over)
    return s


def _case(cid, objects, stub, settings=None, sim_date="2026-01-15", now=1771158600000, loc=None):
    return {"id": cid, "stub": stub, "opts": {
        "inputs": {"location": loc or _loc(), "settings": settings or _settings(), "objects": objects},
        "nowMs": now,                       # 2026-02-15 12:30 UTC
        "simDate": sim_date}}


def _london(y, mo, d, hh, mm):
    return int(pytz.timezone("Europe/London").localize(datetime(y, mo, d, hh, mm)).timestamp() * 1000)


# --- the active-project filter ---------------------------------------------------

def test_active_project_only_whatever_enabled_says(tmp_path):
    """The worker's load_outlook_active_objects() takes active_project rows, not
    enabled ones: B is enabled but not a project, C is a project but disabled."""
    objects = [_obj("A"), _obj("B", ActiveProject=False, enabled=True), _obj("C", enabled=False)]
    r = _run(tmp_path, [_case("x", objects, {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180})])
    names = [row["object_name"] for row in r["x"]["rows"]]
    assert set(names) == {"A", "C"}, names
    assert names[:2] == ["A", "C"], "the payload's own object order, per night"


def test_an_object_without_coordinates_is_skipped_not_fatal(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A"), _obj("B", ra=None)],
                              {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180})])
    assert {row["object_name"] for row in r["x"]["rows"]} == {"A"}


# --- the 20:00 dusk fallback -----------------------------------------------------

def test_dusk_missing_uses_local_20_00_for_the_moon(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"dusk": "N/A", "sunset": "21:00", "minutes": 720,
                               "maxAltitude": 90, "phase": 0, "sep": 180})])
    assert r["x"]["sepCalls"][0] == _london(2026, 1, 15, 20, 0)


def test_dusk_present_is_used_not_20_00(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"dusk": "17:23", "sunset": "21:00", "minutes": 720,
                               "maxAltitude": 90, "phase": 0, "sep": 180})])
    assert r["x"]["sepCalls"][0] == _london(2026, 1, 15, 17, 23)


# --- the > 75 boundary -----------------------------------------------------------

@pytest.mark.parametrize("max_alt, kept", [(55.0, False), (55.5, True)])
def test_only_a_score_above_75_is_kept(tmp_path, max_alt, kept):
    # phase 0 and sep 180 fix sm = 1 and ss = 1; minutes 0 fixes sd = 0; sa = (alt-20)/70.
    # alt 55 -> (35/70) = 0.5 -> 100*(0.1 + 0.45 + 0.2) = 75.0 exactly, which is not > 75.
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 0, "maxAltitude": max_alt, "phase": 0, "sep": 180})])
    rows = r["x"]["rows"]
    assert bool(rows) is kept
    if kept:
        assert rows[0]["score"] >= 75.0


# --- stars, half to even ---------------------------------------------------------

def test_a_score_of_87_5_gives_five_stars(tmp_path):
    # phase 25 fixes sm = 0.75, sep 135 fixes ss = 0.75, minutes 720 fixes sd = 0.5,
    # alt 90 fixes sa = 1: 100*(0.2 + 0.075 + 0.3375 + 0.15) = 87.5 exactly, so
    # 87.5/100*4 = 3.5, and Python's round(3.5) is 4 -> 5 stars.
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 720, "maxAltitude": 90, "phase": 25, "sep": 135})])
    row = r["x"]["rows"][0]
    assert row["score"] == 87.5
    assert row["rating_num"] == 5 and row["rating"] == "★★★★★"


# --- per-field rounding ----------------------------------------------------------

def test_fields_round_as_the_server_rounds_them(tmp_path):
    # obs_dur is int(minutes): int(600.5) is 600, a truncation, not a rounding.
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 600.5, "maxAltitude": 90, "phase": 0, "sep": 180})])
    row = r["x"]["rows"][0]
    assert row["obs_dur"] == 600 and isinstance(row["obs_dur"], int)
    # round(x, 1) halves to even: 39.25 -> 39.2 and 0.25 -> 0.2, where f"{x:.1f}" gives 39.3 and 0.3.
    r2 = _run(tmp_path, [_case("y", [_obj("A")],
                               {"minutes": 720, "maxAltitude": 39.25, "phase": 0.25, "sep": 180})])
    row2 = r2["y"]["rows"][0]
    assert row2["max_alt"] == 39.2 and row2["moon_illumination"] == 0.2


# --- the start date --------------------------------------------------------------

@pytest.mark.parametrize("sim_date, first_night", [
    ("2026-03-05", "2026-03-05"),
    ("2026-3-5", "2026-03-05"),        # strptime accepts a one-digit month and day
    ("2026-02-30", "2026-02-15"),      # ValueError -> today at the location (nowMs)
])
def test_start_date_rule(tmp_path, sim_date, first_night):
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180},
                              sim_date=sim_date)])
    assert sorted({row["date"] for row in r["x"]["rows"]})[0] == first_night


def test_start_date_is_today_at_the_location_live(tmp_path):
    # 2026-07-04 23:30 UTC is 00:30 on the 5th in London (BST), so the first night is the 5th.
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180},
                              sim_date=None, now=1783207800000)])
    assert sorted({row["date"] for row in r["x"]["rows"]})[0] == "2026-07-05"


def test_horizon_is_thirty_days_per_month(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180},
                              settings=_settings(search_horizon_months=2))])
    assert len({row["date"] for row in r["x"]["rows"]}) == 60


# --- the row shape ---------------------------------------------------------------

def test_rows_have_the_servers_keys_in_the_servers_order(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180})])
    assert list(r["x"]["rows"][0].keys()) == [
        "object_name", "common_name", "has_framing", "date", "score", "rating", "rating_num",
        "max_alt", "obs_dur", "moon_illumination", "project", "type", "constellation",
        "magnitude", "size", "sb"]


def test_rating_string_matches_rating_num(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")],
                              {"minutes": 720, "maxAltitude": 90, "phase": 0, "sep": 180})])
    row = r["x"]["rows"][0]
    assert row["rating"] == "★" * row["rating_num"] + "☆" * (5 - row["rating_num"])
