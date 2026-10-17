"""The browser's dashboard rows must be the batch endpoint's rows.

static/js/nova_dashboard_rows.js rebuilds /api/get_desktop_data_batch (all pages
together) in the page, so the dashboard can fill its table without the endpoint.
This pins the two together: a spread of the shipped catalogue at the template's
northern, southern and equatorial sites plus a fourth site with a horizon mask and
a skyglow file, a winter night and the night Europe changes to summer time, at
clock times chosen to sit on the edges of the rules (just after a grid sample,
exactly between two, either side of noon and of midnight, inside the skipped DST
hour), live and simulated, with calc_invisible and the skyglow token on and off.

The browser is fed the way the page will feed it: /api/desktop_inputs?location=…
and /api/desktop_library through the same client, the clock frozen at the same
instant on both sides (datetime.now in the batch's module, nowMs in the browser).

Comparison: identical. A difference is allowed only where the browser's own
unrounded value lies within a documented epsilon of the boundary that decides the
printed value, so a few-millidegree engine difference can flip it:
  - EPS_DEG 0.002 deg for object positions: tests/test_plot_data_parity.py measures
    a worst case of 0.001 deg. Azimuth gets EPS_DEG / cos(altitude), since an
    azimuth is that much less certain near the zenith.
  - EPS_MOON 0.05 deg for the Moon separation, test_plot_data_parity's Moon tolerance.
  - EPS_S 2 s for a transit instant against a printed-minute boundary.
  - A twilight (dusk, dawn, sunset, sunrise) may print a different minute only when
    the browser's instant is within EPS_S of that minute boundary. When the night
    window uses that twilight (sunset/sunrise only stand in for a missing dusk/dawn),
    every sample moves: Observable Duration may then differ by up to 2 samples, Max
    Altitude by up to 0.3 deg, and the transit string's dusk-to-dawn filter may differ.
Allowed differences are counted and printed per field; any other difference fails.
"""

import json
import math
import re
import shutil
import subprocess
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from pathlib import Path

import pytest
import pytz
import yaml

import nova.blueprints.api as batch_module
from nova import app
from nova.config import astro_context_cache, nightly_curves_cache
from nova.models import AstroObject, DbUser, HorizonPoint, Location, UiPref
from modules.astro_calculations import calculate_sun_events_cached

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
CONFIG_DEFAULT = REPO / "config_templates" / "config_default.yaml"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js")

# Every 12th object of the shipped catalogue by right ascension: ~29 objects over the
# whole sky, so some never rise at each site and some are circumpolar.
OBJECT_STRIDE = 12

# 2026-03-29 01:00 UTC Europe changes to summer time, inside the 28th's night window.
NIGHTS = ("2026-01-15", "2026-03-28")
# The simulated clock is read from a real "today" on which every wall time exists.
SIM_TODAY = date(2026, 10, 17)

MASK = [[30.0, 25.0], [90.0, 45.0], [180.0, 30.0], [270.0, 35.0]]
SKYGLOW_SECTORS = [
    {"az_deg": 0.0, "min_alt_deg": 24.3},
    {"az_deg": 120.0, "min_alt_deg": 31.0},
    {"az_deg": 240.0, "min_alt_deg": 28.5},
]
MASK_SITE = ("Vienna Mask", 48.2082, 16.3738, "Europe/Vienna")
# Per site: None falls through to the global setting (20), 0 is a valid override.
THRESHOLDS = {"London": None, "Perth": 35.0, "Singapore": 0.0, "Vienna Mask": 20.0}

EPS_DEG = 0.002
EPS_MOON = 0.05
EPS_S = 2.0
NIGHT_EDGE_SAMPLES = 2
NIGHT_EDGE_MAX_ALT = 0.3

# (label, night offset in days, wall clock) — live: that wall time on the night or the
# day after; sim: SIM_TODAY at that wall clock, with sim_date = night + offset.
LIVE_CLOCKS = [
    ("after a sample", 0, time(21, 0, 7, 250000)),
    ("between two samples", 0, time(21, 7, 30)),
    ("on a sample", 0, time(22, 0)),
    ("23:59:30", 0, time(23, 59, 30)),
    ("00:01", 1, time(0, 1)),
    ("11:59", 1, time(11, 59)),
    ("12:01", 1, time(12, 1)),
]
SIM_CLOCKS = [
    ("sim between two samples", 0, time(21, 7, 30)),
    ("sim 12:01", 0, time(12, 1)),
    ("sim 00:01", 1, time(0, 1)),
    ("sim 01:30 (London's skipped hour)", 1, time(1, 30)),
    ("sim 02:30 (Vienna's skipped hour)", 1, time(2, 30)),
    ("sim 11:59", 1, time(11, 59)),
]

# Loads the three files the way the page does — plain scripts in one context — and
# writes what each case makes of its inputs. An exception is a result, not a crash.
_DRIVER = """
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
const R = context.NovaDashboardRows;
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => {
    try {
        if (c.kind === 'py') {
            return { id: c.id, data: {
                fixed2: c.values.map(v => R._py.fixed(v, 2)),
                round1: c.values.map(v => R._py.round(v, 1)),
                roundInt: c.values.map(v => R._py.roundInt(v)),
            } };
        }
        return { id: c.id, data: R[c.kind](c.opts) };
    } catch (e) {
        return { id: c.id, error: String(e && e.message || e) };
    }
})));
"""


# --- setup -------------------------------------------------------------------------------

def _template():
    return yaml.safe_load(CONFIG_DEFAULT.read_text())


def _sites(template):
    sites = template["locations"]
    north = max(sites.items(), key=lambda kv: kv[1]["lat"])
    south = min(sites.items(), key=lambda kv: kv[1]["lat"])
    equator = min(sites.items(), key=lambda kv: abs(kv[1]["lat"]))
    assert len({north[0], south[0], equator[0]}) == 3, (
        "config_default.yaml no longer has a northern, a southern and an equatorial site")
    return [(name, s["lat"], s["lon"], s["timezone"]) for name, s in (north, south, equator)]


def _seed(db_session, tmp_path, monkeypatch):
    """The catalogue spread and the four sites in the DB; the mask site's skyglow file."""
    template = _template()
    user = db_session.query(DbUser).filter_by(username="default").one()
    for obj in sorted(template["objects"], key=lambda o: o["RA (hours)"])[::OBJECT_STRIDE]:
        db_session.add(AstroObject(user_id=user.id, object_name=obj["Object"],
                                   common_name=obj.get("Common Name"),
                                   ra_hours=obj["RA (hours)"], dec_deg=obj["DEC (degrees)"]))
    seeded = []
    for name, lat, lon, tz in _sites(template) + [MASK_SITE]:
        loc = Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz,
                       altitude_threshold=THRESHOLDS[name])
        if name == MASK_SITE[0]:
            loc.horizon_points = [HorizonPoint(az_deg=az, alt_min_deg=alt) for az, alt in MASK]
        db_session.add(loc)
        seeded.append(loc)
    db_session.commit()

    sg_dir = tmp_path / "skyglow"
    sg_dir.mkdir()
    mask_loc = next(l for l in seeded if l.name == MASK_SITE[0])
    (sg_dir / f"{mask_loc.id}.json").write_text(json.dumps({"skyglow_horizon": SKYGLOW_SECTORS}))
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    astro_context_cache.clear()
    return seeded


def _set_calc_invisible(db_session, value):
    user = db_session.query(DbUser).filter_by(username="default").one()
    prefs = db_session.query(UiPref).filter_by(user_id=user.id).first()
    blob = json.loads(prefs.json_blob) if prefs and prefs.json_blob else {}
    blob["calc_invisible"] = value
    if prefs:
        prefs.json_blob = json.dumps(blob)
    else:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(blob)))
    db_session.commit()


_FROZEN = [None]


class _FrozenDatetime(datetime):
    """datetime with now() fixed at _FROZEN[0]; everything else is datetime's."""

    @classmethod
    def now(cls, tz=None):
        frozen = _FROZEN[0]
        return frozen.astimezone(tz) if tz else frozen.astimezone(dt_timezone.utc).replace(tzinfo=None)


def _ms(aware):
    return (aware - datetime(1970, 1, 1, tzinfo=dt_timezone.utc)) // timedelta(milliseconds=1)


def _batch(client, location, sim_date):
    """Every batch row, walking the pages the way dashboard.js does."""
    rows, offset = [], 0
    while True:
        query = {"offset": offset, "limit": 50, "location": location}
        if sim_date:
            query["sim_date"] = sim_date
        response = client.get('/api/get_desktop_data_batch', query_string=query)
        assert response.status_code == 200, f"batch for {location}: HTTP {response.status_code}"
        page = response.get_json()
        rows += page["results"]
        if not page["has_more"]:
            return rows
        offset += 50


def _case(client, loc, frozen, sim_date, label):
    """Freeze the clock, fetch the browser's inputs and the server's rows."""
    _FROZEN[0] = frozen
    inputs = client.get('/api/desktop_inputs', query_string={"location": loc.name}).get_json()
    library = client.get('/api/desktop_library').get_json()["objects"]
    return {
        "id": f"{loc.name} | {label} | {frozen.isoformat()}" + (f" sim {sim_date}" if sim_date else ""),
        "loc": loc,
        "server": _batch(client, loc.name, sim_date),
        "opts": {"inputs": inputs, "library": library, "nowMs": _ms(frozen), "simDate": sim_date},
    }


def _run_node(tmp_path, cases):
    driver = tmp_path / "rows_driver.js"
    driver.write_text(_DRIVER)
    cases_file = tmp_path / "cases.json"
    results_file = tmp_path / "results.json"
    cases_file.write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(driver), *[str(STATIC_JS / f) for f in ENGINE_FILES],
         str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: r for r in json.loads(results_file.read_text())}


# --- the comparison rule -----------------------------------------------------------------

def _near_rounding(x, decimals, eps):
    """x lies within eps of a halfway point between two printed values."""
    scaled = x * 10 ** decimals
    return abs((scaled - math.floor(scaled)) - 0.5) * 10 ** -decimals < eps


def _az_eps(alt):
    return EPS_DEG / max(math.cos(math.radians(alt)), 0.02)


def _one_step_apart(a, b, step, wrap=None):
    try:
        gap = abs(float(a) - float(b))
    except (TypeError, ValueError):
        return False
    if wrap is not None:
        gap = min(gap, abs(gap - wrap))
    return abs(gap - step) < step / 10


def _near(a, b, eps):
    return a is not None and b is not None and abs(a - b) < eps


def _hhmm_minutes(s):
    try:
        return [int(p.split(":")[0]) * 60 + int(p.split(":")[1]) for p in s.split(", ")]
    except (ValueError, IndexError):
        return None


def _allowed(field, server_value, browser_value, raw, ctx):
    """Why a difference is allowed, or None."""
    alt, az = raw["alt"], raw["az"]
    obst_now_edge = (_near(alt, ctx["threshold"], EPS_DEG)
                     or _near(alt, raw["reqNow"], EPS_DEG + raw["slopeNow"] * _az_eps(alt)))
    obst_11_edge = (_near(raw["alt11"], ctx["threshold"], EPS_DEG)
                    or _near(raw["alt11"], raw["req11"], EPS_DEG + raw["slope11"] * _az_eps(raw["alt11"])))

    if field == "Altitude Current" and _one_step_apart(server_value, browser_value, 0.01) \
            and _near_rounding(alt, 2, EPS_DEG):
        return "rounding edge"
    if field == "Azimuth Current":
        if _one_step_apart(server_value, browser_value, 0.01, wrap=360) and _near_rounding(az, 2, _az_eps(alt)):
            return "rounding edge"
        if min(az, 360 - az) < _az_eps(alt):
            return "north wrap"
    if field == "Altitude 11PM" and _one_step_apart(server_value, browser_value, 0.01) \
            and _near_rounding(raw["alt11"], 2, EPS_DEG):
        return "rounding edge"
    if field == "Azimuth 11PM":
        eps = _az_eps(raw["alt11"])
        if _one_step_apart(server_value, browser_value, 0.01, wrap=360) and _near_rounding(raw["az11"], 2, eps):
            return "rounding edge"
        if min(raw["az11"], 360 - raw["az11"]) < eps:
            return "north wrap"
    if field == "Trend" and abs(abs(raw["delta"]) - 0.01) < 2 * EPS_DEG:
        return "0.01 edge"
    if field == "is_obstructed_now" and obst_now_edge:
        return "threshold/mask edge"
    if field == "is_obstructed_at_11pm" and obst_11_edge:
        return "threshold/mask edge"
    if field == "below_skyglow_floor":
        sg = raw["sgNow"]
        if obst_now_edge:
            return "threshold/mask edge"
        if sg and (sg["tieMargin"] < 2 * _az_eps(alt) or _near(alt, sg["floor"], EPS_DEG)):
            return "skyglow edge"
    if field == "below_skyglow_floor_11pm":
        if obst_11_edge:
            return "threshold/mask edge"
        if ctx["differs"] & {"Altitude 11PM", "Azimuth 11PM"}:
            return "follows a printed 11 PM difference"
    if field == "Transit Time":
        if ctx["night_edge"]:
            return "night edge"
        s_min, b_min = _hhmm_minutes(server_value), _hhmm_minutes(browser_value)
        instants = raw["transit"]
        if s_min and b_min and len(s_min) == len(b_min) == len(instants):
            if all(min(abs(a - b), 1440 - abs(a - b)) <= 1 for a, b in zip(s_min, b_min)) and \
                    all(min(t / 1000 % 60, 60 - t / 1000 % 60) < EPS_S
                        for (t, a, b) in zip(instants, s_min, b_min) if a != b):
                return "minute edge"
        twilights = [ctx["window"]["dusk"], ctx["window"]["dawn"]] if ctx["window"] else []
        if any(abs(c["utcMs"] - t) < (60 + EPS_S) * 1000
               for c in raw["transitCrossings"] for t in twilights):
            return "dusk/dawn edge"
    if field == "Observable Duration (min)":
        gap = abs(server_value - browser_value)
        if gap <= raw["nearSamples"] * ctx["interval"]:
            return "threshold/mask edge"
        if ctx["night_edge"] and gap <= (NIGHT_EDGE_SAMPLES + raw["nearSamples"]) * ctx["interval"]:
            return "night edge"
    if field == "Max Altitude (°)":
        if _one_step_apart(server_value, browser_value, 0.1) and _near_rounding(raw["maxAltitude"], 1, EPS_DEG):
            return "rounding edge"
        if ctx["night_edge"] and abs(server_value - browser_value) <= NIGHT_EDGE_MAX_ALT:
            return "night edge"
    if field == "Angular Separation (°)" and _one_step_apart(server_value, browser_value, 1) \
            and _near_rounding(raw["sep"], 0, EPS_MOON):
        return "rounding edge"
    return None


def _same(a, b):
    """Equal as JSON values: JS has one number type, so 45 and 45.0 are the same."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _compare(case, result, allowed, problems):
    if "error" in result:
        problems.append(f"{case['id']}: browser threw: {result['error']}")
        return
    data = result["data"]
    server, browser, raws = case["server"], data["rows"], data["raw"]
    if [r["Object"] for r in server] != [r["Object"] for r in browser]:
        problems.append(f"{case['id']}: row order differs")
        return

    loc = case["opts"]["inputs"]["location"]
    events = data["night"]["sunEvents"]
    expected = calculate_sun_events_cached(data["night"]["date"], loc["timezone"], loc["lat"], loc["lon"])
    # A twilight string may differ only where the browser's instant is within EPS_S
    # of the minute it is truncated to; then the whole night's samples move. Sunset
    # and sunrise only move them on a night whose window uses them, i.e. when the
    # astronomical dusk (for sunset) or dawn (for sunrise) is N/A.
    window_uses = {
        "astronomical_dusk": True,
        "astronomical_dawn": True,
        "sunset": "N/A" in (events["astronomical_dusk"], expected["astronomical_dusk"]),
        "sunrise": "N/A" in (events["astronomical_dawn"], expected["astronomical_dawn"]),
    }
    night_edge = False
    for key in ("astronomical_dusk", "astronomical_dawn", "sunset", "sunrise"):
        if events[key] == expected[key]:
            continue
        instant = events.get(f"{key}_utc")
        seconds = instant / 1000 % 60 if instant is not None else None
        if seconds is not None and min(seconds, 60 - seconds) < EPS_S:
            night_edge = night_edge or window_uses[key]
            allowed.setdefault((key, "minute edge"), []).append(
                f"{case['id']}: server {expected[key]!r}, browser {events[key]!r}")
        else:
            problems.append(f"{case['id']} {key}: server {expected[key]!r}, browser {events[key]!r}")
    ctx = {
        "threshold": loc["altitude_threshold"], "interval": loc["sampling_interval"],
        "window": data["night"]["window"], "night_edge": night_edge,
    }

    for s_row, b_row, raw in zip(server, browser, raws):
        name = s_row["Object"]
        if set(s_row) != set(b_row):
            problems.append(f"{case['id']} {name}: keys {sorted(set(s_row) ^ set(b_row))} differ")
            continue
        ctx["differs"] = {k for k in s_row if not _same(s_row[k], b_row[k])}
        for field in sorted(ctx["differs"]):
            reason = None if raw["prefilter"] else _allowed(field, s_row[field], b_row[field], raw, ctx)
            if reason:
                allowed.setdefault((field, reason), []).append(
                    f"{case['id']} {name}: server {s_row[field]!r}, browser {b_row[field]!r}")
            else:
                problems.append(f"{case['id']} {name} {field}: server {s_row[field]!r}, "
                                f"browser {b_row[field]!r}")


# --- tests -------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_dashboard_rows_agree_with_the_batch(client, db_session, tmp_path, monkeypatch):
    seeded = _seed(db_session, tmp_path, monkeypatch)
    monkeypatch.setattr(batch_module, "datetime", _FrozenDatetime)
    monkeypatch.setenv("NASA_EARTHDATA_TOKEN", "test-token")
    nightly_curves_cache.clear()   # its key covers every input that differs between cases

    cases = []
    for loc in seeded:
        tz = pytz.timezone(loc.timezone)
        for night in NIGHTS:
            night_day = date.fromisoformat(night)
            for label, plus, clock in LIVE_CLOCKS:
                frozen = tz.localize(datetime.combine(night_day + timedelta(days=plus), clock))
                cases.append(_case(client, loc, frozen, None, label))
            for label, plus, clock in SIM_CLOCKS:
                frozen = tz.localize(datetime.combine(SIM_TODAY, clock))
                sim_date = (night_day + timedelta(days=plus)).isoformat()
                cases.append(_case(client, loc, frozen, sim_date, label))

            frozen = tz.localize(datetime.combine(night_day, LIVE_CLOCKS[0][2]))
            _set_calc_invisible(db_session, True)
            cases.append(_case(client, loc, frozen, None, "calc_invisible"))
            _set_calc_invisible(db_session, False)
            if loc.name == MASK_SITE[0]:
                monkeypatch.delenv("NASA_EARTHDATA_TOKEN")
                cases.append(_case(client, loc, frozen, None, "no skyglow token"))
                monkeypatch.setenv("NASA_EARTHDATA_TOKEN", "test-token")

    per_site_night = len(LIVE_CLOCKS) + len(SIM_CLOCKS) + 1
    assert len(cases) == len(seeded) * len(NIGHTS) * per_site_night + len(NIGHTS)

    results = _run_node(tmp_path, [{"id": c["id"], "kind": "explain", "opts": c["opts"]} for c in cases])

    allowed, problems = {}, []
    rows_compared = 0
    for case in cases:
        _compare(case, results[case["id"]], allowed, problems)
        rows_compared += len(case["server"])

    # The matrix must reach every row shape and both flags, or it proves less than it says.
    every_row = [r for c in cases for r in c["server"]]
    assert any(r.get("is_geometrically_impossible") for r in every_row)
    assert any(r.get("is_obstructed_now") for r in every_row)
    assert any(r.get("is_obstructed_at_11pm") for r in every_row)
    assert any(r.get("below_skyglow_floor") for r in every_row)
    assert any(r.get("below_skyglow_floor_11pm") for r in every_row)
    assert {r["Trend"] for r in every_row} >= {"↑", "↓", "–"}

    print(f"\n{len(cases)} cases, {rows_compared} rows compared field by field")
    for (field, reason), where in sorted(allowed.items()):
        print(f"  allowed: {field} ({reason}): {len(where)}, e.g. {where[0]}")
    assert not problems, f"{len(problems)} difference(s):\n" + "\n".join(problems[:40])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_exact_instant_moves_only_the_live_position(client, db_session, tmp_path, monkeypatch):
    """instant 'exact' changes Altitude/Azimuth Current and Trend and nothing else;
    on a grid sample it is the snapped row exactly."""
    seeded = _seed(db_session, tmp_path, monkeypatch)
    monkeypatch.setattr(batch_module, "datetime", _FrozenDatetime)
    live_fields = {"Altitude Current", "Azimuth Current", "Trend"}

    cases = []
    for loc in seeded:
        tz = pytz.timezone(loc.timezone)
        for label, clock in (("on", time(22, 0)), ("off", time(22, 6, 41))):
            opts = _case(client, loc, tz.localize(datetime.combine(date(2026, 1, 15), clock)),
                         None, label)["opts"]
            for mode in ("snapped", "exact"):
                cases.append({"id": f"{loc.name} {label} {mode}", "kind": "rows",
                              "opts": dict(opts, instant=mode)})
    results = _run_node(tmp_path, cases)

    moved = 0
    for loc in seeded:
        for label in ("on", "off"):
            snapped = results[f"{loc.name} {label} snapped"]["data"]
            exact = results[f"{loc.name} {label} exact"]["data"]
            for s_row, e_row in zip(snapped, exact):
                differs = {k for k in s_row if s_row[k] != e_row[k]}
                if label == "on":
                    assert not differs, f"{loc.name} {s_row['Object']}: {differs}"
                else:
                    assert differs <= live_fields, f"{loc.name} {s_row['Object']}: {differs}"
                    moved += bool(differs)
    assert moved, "6m41s off the grid moved no position at all"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("site, night, kind", [
    (("Santiago", -33.45, -70.67, "America/Santiago"), "2026-04-04", "ambiguous"),
    (("Nuuk", 64.18, -51.72, "America/Nuuk"), "2026-03-28", "non-existent"),
])
def test_an_impossible_11pm_fails_like_the_batch(client, db_session, tmp_path, monkeypatch, site, night, kind):
    """get_utc_time_for_local_11pm_on() localizes with is_dst=None: when 23:00 is
    repeated or skipped the batch fails for the whole table, and so must the browser."""
    name, lat, lon, tz_name = site
    user = db_session.query(DbUser).filter_by(username="default").one()
    loc = Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz_name)
    db_session.add(loc)
    db_session.commit()
    astro_context_cache.clear()
    monkeypatch.setattr(batch_module, "datetime", _FrozenDatetime)
    _FROZEN[0] = pytz.timezone(tz_name).localize(datetime.combine(date.fromisoformat(night), time(20, 0)))

    response = client.get('/api/get_desktop_data_batch', query_string={"location": name})
    assert response.status_code == 500

    opts = {
        "inputs": client.get('/api/desktop_inputs', query_string={"location": name}).get_json(),
        "library": client.get('/api/desktop_library').get_json()["objects"],
        "nowMs": _ms(_FROZEN[0]), "simDate": None,
    }
    result = _run_node(tmp_path, [{"id": "x", "kind": "rows", "opts": opts}])["x"]
    assert kind in result.get("error", ""), result


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_python_number_printing(tmp_path):
    """f"{x:.2f}", round(x, 1) and round(x) as Python does them, halves to even,
    where toFixed() and Math.round() round them up."""
    values = [0.125, 0.375, 2.675, 1.005, 0.25, 0.75, 2.5, 3.5, -2.5, -0.125, -0.0, 0.0,
              -0.004, 45.125, 359.995, 359.9951, 89.99999, 1e-9, 12.345, 0.05, 0.15, 0.35]
    values += [k / 8 for k in range(-40, 41)] + [k / 1000 for k in range(0, 2000, 7)]
    data = _run_node(tmp_path, [{"id": "py", "kind": "py", "values": values}])["py"]["data"]

    assert data["fixed2"] == [f"{v:.2f}" for v in values]
    assert data["round1"] == [round(v, 1) for v in values]
    assert data["roundInt"] == [round(v) for v in values]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_skyglow_flags_read_the_positions_the_batch_reads(client, db_session, tmp_path, monkeypatch):
    """The 11 PM skyglow test compares the two-decimal strings, the current one the
    unrounded position. A floor placed between an object's printed and unrounded
    altitude tells the two apart, which no catalogue position does by chance."""
    seeded = _seed(db_session, tmp_path, monkeypatch)
    monkeypatch.setattr(batch_module, "datetime", _FrozenDatetime)
    monkeypatch.setenv("NASA_EARTHDATA_TOKEN", "test-token")
    loc = next(l for l in seeded if l.name == "London")
    frozen = pytz.timezone(loc.timezone).localize(datetime.combine(date(2026, 1, 15), time(21, 0, 7)))
    sg_file = tmp_path / "skyglow" / f"{loc.id}.json"

    def _by_object(rows):
        return {r["Object"]: r for r in rows}

    def _by_object_raw(data):
        return {r["Object"]: raw for r, raw in zip(data["rows"], data["raw"])}

    def run(floor):
        # One sector, so every azimuth reads this floor.
        sg_file.write_text(json.dumps({"skyglow_horizon": [{"az_deg": 180.0, "min_alt_deg": floor}]}))
        case = _case(client, loc, frozen, None, f"floor {floor}")
        result = _run_node(tmp_path, [{"id": case["id"], "kind": "explain", "opts": case["opts"]}])
        data = result[case["id"]]["data"]
        return _by_object(case["server"]), _by_object(data["rows"]), _by_object_raw(data)

    server, browser, raw = run(-90.0)
    checked = 0
    for printed_key, raw_key, flag in (("Altitude 11PM", "alt11", "below_skyglow_floor_11pm"),
                                       ("Altitude Current", "alt", "below_skyglow_floor")):
        # An object whose printed altitude agrees on both sides and sits at least
        # 0.003 deg (three times the engine's worst error) from its unrounded value.
        name = next(n for n, r in raw.items()
                    if not r["prefilter"] and server[n][printed_key] == browser[n][printed_key]
                    and 0.003 <= abs(r[raw_key] - float(browser[n][printed_key])) <= 0.005)
        printed, exact = float(browser[name][printed_key]), raw[name][raw_key]
        floor = (printed + exact) / 2
        s, b, _ = run(floor)
        expected = (printed if raw_key == "alt11" else exact) < floor
        assert s[name][flag] is expected, f"{name} {flag}: the batch's rule is not the one documented"
        assert b[name][flag] is s[name][flag], f"{name} {flag}: server {s[name][flag]}, browser {b[name][flag]}"
        checked += 1
    assert checked == 2


def test_index_loads_the_engine_before_dashboard_js(client):
    """The dashboard finds the three engine scripts as globals when fetchData runs,
    so the page must load them, in dependency order, before dashboard.js (all deferred,
    so they also run in that order)."""
    html = client.get('/').get_data(as_text=True)
    scripts = re.findall(r'<script[^>]*\ssrc="[^"]*/js/([^"/?]+\.js)\?v=[^"]*"[^>]*>', html)
    order = [name for name in scripts if name in ENGINE_FILES + ("dashboard.js",)]
    assert order == list(ENGINE_FILES) + ["dashboard.js"]
