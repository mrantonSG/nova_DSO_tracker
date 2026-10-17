"""The browser's day-chart payload must be the endpoint's payload.

static/js/nova_plot_data.js rebuilds /api/get_plot_data in the page, so the graph
view's day chart needs no round trip. This pins the two together: the shipped
catalogue's objects, the template's northern, southern and equatorial sites, a
winter and a summer night (July is BST, so the DST rendering of the time grid is
covered), each compared field by field against the endpoint through the test
client.

The browser side is fed the way the page feeds it: /api/desktop_inputs?location=…
through the same client, mapped by the same fields graph_view_chart.js's
computeLocalPlotData() maps. Nothing about the inputs is recomputed here.

Tolerances are the engine's, not wishful ones (see tools/compare_astro_engine.js,
which validates static/js/nova_astro.js against the Python reference):
  - objects 0.01 deg: measured worst case 0.001 deg over 112 location-nights.
  - Moon 0.05 deg: the series' worst case is 0.013 deg, so 0.01 would fail, and
    the engine's own pass mark for the Moon is 0.1 deg.
  A position is compared as its altitude and as its angular separation on the sky,
  never as a raw azimuth difference: azimuth is undefined at the poles, so a body
  within a degree of the zenith or the nadir can differ by a tenth of a degree of
  azimuth while sitting a hundredth of a degree apart on the sky. Sky separation is
  the metric tools/compare_astro_engine.js itself validates the engine with.
  - sun-event strings and the transit string: identical, with the one-printed-
    minute slack tests/test_shared_js.py already allows, because the engine's
    published pass mark for twilight is 2 minutes. 'N/A' must always match.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml

from nova import app
from nova.config import astro_context_cache
from nova.models import AstroObject, DbUser, HorizonPoint, Location

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
CONFIG_DEFAULT = REPO / "config_templates" / "config_default.yaml"
NODE = shutil.which("node")

# The shipped catalogue sorted by right ascension, every 17th: a spread over the
# whole sky rather than the first ten alphabetically.
OBJECT_STRIDE = 17
OBJECTS_WANTED = 10

# July puts Europe/London on BST, so the July night exercises a DST window.
NIGHTS = ("2026-01-15", "2026-07-15")

TOLERANCE_DEG = 0.01
MOON_TOLERANCE_DEG = 0.05
SKYGLOW_TOLERANCE_DEG = 1e-9
MINUTE_SLACK = 1

# A mask with real obstructions, so interpolate_horizon() is doing work, and one
# skyglow sector below zero so the max(0, v) floor is exercised.
MASK = [[30.0, 25.0], [90.0, 45.0], [180.0, 30.0], [270.0, 35.0]]
SKYGLOW_SECTORS = [
    {"az_deg": 0.0, "min_alt_deg": -5.0},
    {"az_deg": 180.0, "min_alt_deg": 38.4},
]

SUN_KEYS = ("astronomical_dawn", "sunrise", "transit", "sunset", "astronomical_dusk")

# The location's altitude_threshold: None means "fall through to the global setting,
# then 20", 0 is a valid override. One site each, so all three paths are covered.
THRESHOLDS = {0: None, 1: 35.0, 2: 0.0}

# Loads the three files the way the page does — plain scripts in one context, so
# nova_transit.js and nova_plot_data.js find the globals the page gives them — and
# writes what NovaPlotData.build() makes of each case's inputs.
_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [astroFile, transitFile, plotFile, inFile, outFile] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of [astroFile, transitFile, plotFile]) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => ({
    id: c.id,
    data: context.NovaPlotData.build(c.input),
}))));
"""


def _template():
    return yaml.safe_load(CONFIG_DEFAULT.read_text())


def _objects(template):
    """Every 17th object of the shipped catalogue, in right ascension order."""
    objects = sorted(template["objects"], key=lambda o: o["RA (hours)"])
    return objects[::OBJECT_STRIDE][:OBJECTS_WANTED]


def _sites(template):
    """The template's northern, southern and equatorial sites, by latitude."""
    sites = template["locations"]
    north = max(sites.items(), key=lambda kv: kv[1]["lat"])
    south = min(sites.items(), key=lambda kv: kv[1]["lat"])
    equator = min(sites.items(), key=lambda kv: abs(kv[1]["lat"]))
    assert len({north[0], south[0], equator[0]}) == 3, (
        "config_default.yaml no longer has a northern, a southern and an equatorial site"
    )
    return [north, south, equator]


def _seed(db_session, tmp_path, monkeypatch):
    """The template's objects and sites in the DB, a skyglow file for one site.

    Returns the seeded Location rows, in _sites() order.
    """
    template = _template()
    user = db_session.query(DbUser).filter_by(username="default").one()

    for obj in _objects(template):
        db_session.add(AstroObject(user_id=user.id, object_name=obj["Object"],
                                   common_name=obj.get("Common Name"),
                                   ra_hours=obj["RA (hours)"], dec_deg=obj["DEC (degrees)"]))

    seeded = []
    for index, (name, site) in enumerate(_sites(template)):
        loc = Location(user_id=user.id, name=name, lat=site["lat"], lon=site["lon"],
                       timezone=site["timezone"], altitude_threshold=THRESHOLDS[index])
        loc.horizon_points = [HorizonPoint(az_deg=az, alt_min_deg=alt) for az, alt in MASK]
        db_session.add(loc)
        seeded.append(loc)
    db_session.commit()

    # One site has a skyglow file; the other two must come back with skyglow null.
    sg_dir = tmp_path / "skyglow"
    sg_dir.mkdir()
    (sg_dir / f"{seeded[1].id}.json").write_text(json.dumps({"skyglow_horizon": SKYGLOW_SECTORS}))
    monkeypatch.setattr(app, "instance_path", str(tmp_path))

    astro_context_cache.clear()   # the routes read locations/objects from this cache
    return seeded


def _browser_inputs(client, loc, obj_name, night):
    """The input object graph_view_chart.js's computeLocalPlotData() would pass.

    Same endpoint, same fields, same mapping: location.* straight through, the
    object row looked up by name, skyglow as the endpoint sends it. The date is
    the page's own day/month/year selection.
    """
    response = client.get('/api/desktop_inputs', query_string={"location": loc.name})
    assert response.status_code == 200, f"desktop_inputs for {loc.name}: HTTP {response.status_code}"
    inputs = response.get_json()

    location = inputs["location"]
    assert location["name"] == loc.name
    wanted = obj_name.lower()
    obj = next(o for o in inputs["objects"] if o["Object"].lower() == wanted)

    return {
        "raHours": obj["ra"], "decDeg": obj["dec"],
        "lat": location["lat"], "lon": location["lon"], "tz": location["timezone"],
        "date": night,
        "threshold": location["altitude_threshold"],
        "interval": location["sampling_interval"],
        "mask": location["horizon_mask"],
        "skyglow": inputs["skyglow"],
    }


def _as_minutes(hhmm):
    parts = hhmm.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _within_a_minute(server, browser):
    a, b = _as_minutes(server), _as_minutes(browser)
    if a is None or b is None:
        return False
    gap = abs(a - b)
    return min(gap, 1440 - gap) <= MINUTE_SLACK


def _series_problems(field, server, browser, tolerance):
    """Length, null positions and values of one series; first problem only."""
    if len(server) != len(browser):
        return [f"{field}: server sent {len(server)} values, browser {len(browser)}"]
    for i, (expected, actual) in enumerate(zip(server, browser)):
        if (expected is None) != (actual is None):
            return [f"{field}[{i}]: server {expected!r}, browser {actual!r}"]
        if expected is None:
            continue
        if abs(expected - actual) > tolerance:
            return [f"{field}[{i}]: server {expected!r}, browser {actual!r}"]
    return []


def _sky_separation(alt_a, az_a, alt_b, az_b):
    """The angle between two alt/az positions, in degrees.

    The metric the engine itself is validated with (tools/compare_astro_engine.js):
    comparable everywhere on the sky, where a raw azimuth difference is not, because
    azimuth is undefined at the zenith and the nadir.
    """
    a, b = math.radians(alt_a), math.radians(alt_b)
    cos_sep = math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(math.radians(az_a - az_b))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def _pair_problems(field, server, browser, tolerance):
    """One body's position series: null parity, altitude, and separation on the sky."""
    if len(server["alt"]) != len(browser["alt"]) or len(server["az"]) != len(browser["az"]):
        return [f"{field}: server sent {len(server['alt'])} values, browser {len(browser['alt'])}"]
    for i, (s_alt, s_az, b_alt, b_az) in enumerate(zip(server["alt"], server["az"],
                                                       browser["alt"], browser["az"])):
        if (s_alt is None) != (b_alt is None) or (s_az is None) != (b_az is None):
            return [f"{field}[{i}]: server {s_alt!r}/{s_az!r}, browser {b_alt!r}/{b_az!r}"]
        if s_alt is None:
            continue
        d_alt = abs(s_alt - b_alt)
        separation = _sky_separation(s_alt, s_az, b_alt, b_az)
        if d_alt > tolerance or separation > tolerance:
            return [f"{field}[{i}]: server {s_alt!r}/{s_az!r}, browser {b_alt!r}/{b_az!r} "
                    f"(dalt {d_alt:.6f} deg, separation {separation:.6f} deg, "
                    f"tolerance {tolerance} deg)"]
    return []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_plot_data_agrees_with_get_plot_data(client, db_session, tmp_path, monkeypatch):
    seeded = _seed(db_session, tmp_path, monkeypatch)
    objects = [o["Object"] for o in _objects(_template())]

    cases = []
    for loc in seeded:
        for night in NIGHTS:
            year, month, day = night.split("-")
            for obj_name in objects:
                cases.append({
                    "id": f"{obj_name} @ {loc.name} {night}",
                    "object": obj_name,
                    "input": _browser_inputs(client, loc, obj_name, night),
                    "query": {"day": int(day), "month": int(month), "year": int(year),
                              "plot_lat": loc.lat, "plot_lon": loc.lon,
                              "plot_tz": loc.timezone, "plot_loc_name": loc.name},
                })
    assert len(cases) == OBJECTS_WANTED * 3 * len(NIGHTS)

    driver = tmp_path / "plot_driver.js"
    driver.write_text(_DRIVER)
    cases_file = tmp_path / "cases.json"
    results_file = tmp_path / "results.json"
    cases_file.write_text(json.dumps([{"id": c["id"], "input": c["input"]} for c in cases]))

    run = subprocess.run(
        [NODE, str(driver), str(STATIC_JS / "nova_astro.js"),
         str(STATIC_JS / "nova_transit.js"), str(STATIC_JS / "nova_plot_data.js"),
         str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"

    browser = {r["id"]: r["data"] for r in json.loads(results_file.read_text())}
    assert len(browser) == len(cases), "the browser returned a different number of cases"

    problems = []
    sun_exact = sun_total = 0
    transit_exact = 0
    for case in cases:
        got = browser[case["id"]]
        response = client.get(f"/api/get_plot_data/{quote(case['object'])}",
                              query_string=case["query"])
        assert response.status_code == 200, f"{case['id']}: HTTP {response.status_code}"
        server = response.get_json()

        if set(server) != set(got):
            problems.append(f"{case['id']}: keys {sorted(server)} vs {sorted(got)}")
            continue

        for field in ("times", "date", "timezone", "weather_forecast"):
            if server[field] != got[field]:
                problems.append(f"{case['id']} {field}: server {server[field]!r}, browser {got[field]!r}")

        for field, tolerance in (("object", TOLERANCE_DEG), ("moon", MOON_TOLERANCE_DEG)):
            problems += [f"{case['id']} {p}" for p in _pair_problems(
                field,
                {"alt": server[f"{field}_alt"], "az": server[f"{field}_az"]},
                {"alt": got[f"{field}_alt"], "az": got[f"{field}_az"]},
                tolerance,
            )]

        problems += [f"{case['id']} {p}" for p in _series_problems(
            "horizon_mask_alt", server["horizon_mask_alt"], got["horizon_mask_alt"], TOLERANCE_DEG)]

        if (server["skyglow_alt"] is None) != (got["skyglow_alt"] is None):
            problems.append(f"{case['id']} skyglow_alt: server {server['skyglow_alt']!r}, "
                            f"browser {got['skyglow_alt']!r}")
        elif server["skyglow_alt"] is not None:
            problems += [f"{case['id']} {p}" for p in _series_problems(
                "skyglow_alt", server["skyglow_alt"], got["skyglow_alt"], SKYGLOW_TOLERANCE_DEG)]

        for which in ("current", "next"):
            if set(server["sun_events"][which]) != set(got["sun_events"][which]):
                problems.append(f"{case['id']} sun_events.{which}: keys differ")
                continue
            for key in SUN_KEYS:
                expected = server["sun_events"][which][key]
                actual = got["sun_events"][which][key]
                sun_total += 1
                if expected == actual:
                    sun_exact += 1
                elif not (expected != "N/A" and actual != "N/A"
                          and _within_a_minute(expected, actual)):
                    problems.append(f"{case['id']} sun_events.{which}.{key}: "
                                    f"server {expected!r}, browser {actual!r}")

        if server["transit_time"] == got["transit_time"]:
            transit_exact += 1
        elif not (server["transit_time"] != "N/A" and got["transit_time"] != "N/A"
                  and _within_a_minute(server["transit_time"], got["transit_time"])):
            problems.append(f"{case['id']} transit_time: server {server['transit_time']!r}, "
                            f"browser {got['transit_time']!r}")

    assert not problems, (
        f"{len(problems)} of {len(cases)} cases differ between nova_plot_data.js and "
        f"/api/get_plot_data:\n  " + "\n  ".join(problems[:20])
    )
    # The tolerances above are slack, not the expected outcome: on this matrix the
    # engine has been identical to the minute everywhere.
    print(f"\nSun events identical to the minute: {sun_exact}/{sun_total}"
          f"; transits identical: {transit_exact}/{len(cases)}")


# --------------------------------------------------------------------------- month / year

# The month and year charts: NovaPlotData.range() against /api/get_monthly_plot_data,
# one object at local midnight each day. March 2026 has London's change to BST
# (29 March), and the year view takes in October's change back as well. London
# changes at 01:00, never at midnight, so two extra sites put a DST change on
# midnight itself: Havana repeats 00:00-00:59 on 1 November 2026 and skips it on
# 8 March 2026, Santiago skips it on 6 September 2026. A sample read with the
# wrong offset is an hour out, up to 15 degrees of altitude.
MONTH_VIEW = (2026, 3)
YEAR_VIEW = 2026
# A year view is twelve endpoint calls, so it takes every fourth of the ten
# objects (three, still spread in right ascension); the month views take all ten.
YEAR_OBJECT_STRIDE = 4
MIDNIGHT_DST_SITES = (
    ("Havana", 23.1136, -82.3666, "America/Havana", ((2026, 11), (2026, 3))),
    ("Santiago", -33.4489, -70.6693, "America/Santiago", ((2026, 9),)),
)
MONTHLY_KEYS = {"dates", "object_alt", "moon_alt"}

# Loads the three files as the page does and, per case, calls NovaPlotData.range()
# once per month, the way graph_view_chart.js's computeLocalMonthlyPlotData() does.
_RANGE_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [astroFile, transitFile, plotFile, inFile, outFile] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of [astroFile, transitFile, plotFile]) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => ({
    id: c.id,
    blocks: c.months.map(m => context.NovaPlotData.range(Object.assign({}, c.input, { year: c.year, month: m }))),
}))));
"""


def _range_inputs(client, loc, obj_name):
    """What computeLocalMonthlyPlotData() passes to range(), less year and month."""
    response = client.get('/api/desktop_inputs', query_string={"location": loc.name})
    assert response.status_code == 200, f"desktop_inputs for {loc.name}: HTTP {response.status_code}"
    inputs = response.get_json()
    location = inputs["location"]
    assert location["name"] == loc.name
    wanted = obj_name.lower()
    obj = next(o for o in inputs["objects"] if o["Object"].lower() == wanted)
    return {
        "raHours": obj["ra"], "decDeg": obj["dec"],
        "lat": location["lat"], "lon": location["lon"], "tz": location["timezone"],
    }


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_plot_data_range_agrees_with_get_monthly_plot_data(client, db_session, tmp_path, monkeypatch):
    seeded = _seed(db_session, tmp_path, monkeypatch)
    user = db_session.query(DbUser).filter_by(username="default").one()
    edge = []
    for name, lat, lon, tz, views in MIDNIGHT_DST_SITES:
        loc = Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz)
        db_session.add(loc)
        edge.append((loc, views))
    db_session.commit()
    astro_context_cache.clear()

    objects = [o["Object"] for o in _objects(_template())]
    year_objects = objects[::YEAR_OBJECT_STRIDE]

    # Month values as the page sends them: the month select's unpadded value for
    # the month view, the year view's zero-padded '01'..'12'.
    views = []
    for loc in seeded:
        views.append((loc, "month", MONTH_VIEW[0], [str(MONTH_VIEW[1])], objects))
        views.append((loc, "year", YEAR_VIEW, [f"{m:02d}" for m in range(1, 13)], year_objects))
    for loc, months in edge:
        for year, month in months:
            views.append((loc, "month", year, [str(month)], objects))

    cases = []
    for loc, view, year, months, view_objects in views:
        for obj_name in view_objects:
            cases.append({
                "id": f"{obj_name} @ {loc.name} {view} {year}/{','.join(months)}",
                "object": obj_name, "loc": loc, "year": year, "months": months,
                "input": _range_inputs(client, loc, obj_name),
            })
    assert len(year_objects) == 3
    assert len(cases) == (OBJECTS_WANTED * (len(seeded) + sum(len(v) for _, v in edge))
                          + len(year_objects) * len(seeded))

    driver = tmp_path / "range_driver.js"
    driver.write_text(_RANGE_DRIVER)
    cases_file = tmp_path / "range_cases.json"
    results_file = tmp_path / "range_results.json"
    cases_file.write_text(json.dumps([{"id": c["id"], "input": c["input"], "year": c["year"],
                                       "months": c["months"]} for c in cases]))

    run = subprocess.run(
        [NODE, str(driver), str(STATIC_JS / "nova_astro.js"),
         str(STATIC_JS / "nova_transit.js"), str(STATIC_JS / "nova_plot_data.js"),
         str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"

    browser = {r["id"]: r["blocks"] for r in json.loads(results_file.read_text())}
    assert len(browser) == len(cases), "the browser returned a different number of cases"

    problems = []
    days = 0
    worst = {"object_alt": 0.0, "moon_alt": 0.0}
    for case in cases:
        loc = case["loc"]
        blocks = browser[case["id"]]
        if len(blocks) != len(case["months"]):
            problems.append(f"{case['id']}: {len(blocks)} blocks for {len(case['months'])} months")
            continue
        for month, got in zip(case["months"], blocks):
            response = client.get(
                f"/api/get_monthly_plot_data/{quote(case['object'])}",
                query_string={"year": case["year"], "month": month, "plot_lat": loc.lat,
                              "plot_lon": loc.lon, "plot_tz": loc.timezone})
            assert response.status_code == 200, f"{case['id']} month {month}: HTTP {response.status_code}"
            server = response.get_json()
            where = f"{case['id']} [{month}]"

            if set(server) != MONTHLY_KEYS or set(got) != MONTHLY_KEYS:
                problems.append(f"{where}: keys {sorted(server)} vs {sorted(got)}")
                continue
            if server["dates"] != got["dates"]:
                problems.append(f"{where} dates: server {server['dates']!r}, browser {got['dates']!r}")
            days += len(server["dates"])
            for field, tolerance in (("object_alt", TOLERANCE_DEG), ("moon_alt", MOON_TOLERANCE_DEG)):
                problems += [f"{where} {p}" for p in _series_problems(
                    field, server[field], got[field], tolerance)]
                if len(server[field]) == len(got[field]):
                    worst[field] = max([worst[field]] + [
                        abs(a - b) for a, b in zip(server[field], got[field])
                        if a is not None and b is not None])

    assert not problems, (
        f"{len(problems)} month blocks differ between NovaPlotData.range() and "
        f"/api/get_monthly_plot_data:\n  " + "\n  ".join(problems[:20])
    )
    print(f"\nMonthly parity: {len(cases)} views, {days} days; worst object "
          f"{worst['object_alt']:.6f} deg, worst Moon {worst['moon_alt']:.6f} deg")
