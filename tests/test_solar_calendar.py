"""Checks for static/js/nova_solar_calendar.js, the Solar System page's Calendar tab.

The unit cases drive the module in node the way the page loads it: plain scripts in one
vm context, in the page's order, so NovaSolarCalendar finds NovaAstro, NovaPlanets,
NovaDashboardRows and NovaSolarRows as globals. The engines are the real ones.

The parity cases rebuild the same events with ephem (a daily scan, then a refinement to
a minute) for 2024-2034 and compare them with the module: the instants, the observing
night each falls in, and that night's maximum altitude, from London and Singapore.
"""

import json
import math
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import ephem
import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js",
              "nova_planets.js", "nova_solar_rows.js", "nova_solar_calendar.js")
OPPOSITION_BODIES = ["mars", "jupiter", "saturn", "uranus", "neptune"]
ELONGATION_BODIES = ["mercury", "venus"]

LONDON = {"lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15}
SINGAPORE = {"lat": 1.3521, "lon": 103.8198, "timezone": "Asia/Singapore",
             "altitude_threshold": 20, "sampling_interval": 15}

# Loads the page's scripts into one context, then runs the case's body, which sets
# `result` from `input`; the driver writes `result` as JSON.
_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [bodyFile, inFile, outFile, ...files] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
context.input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
vm.runInContext(fs.readFileSync(bodyFile, 'utf8'), context, { filename: 'case.js' });
fs.writeFileSync(outFile, JSON.stringify(context.result));
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, body, payload):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "case.js"
    case.write_text(body)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(payload))
    out = tmp_path / "out.json"
    run = subprocess.run(
        [NODE, str(driver), str(case), str(inp), str(out)]
        + [str(STATIC_JS / name) for name in PAGE_ORDER],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def _loc(base=LONDON, **overrides):
    loc = dict(base)
    loc.update(overrides)
    return {"location": loc}


def _ms(*args, tz="UTC"):
    """UTC milliseconds of a wall time in tz."""
    return int(datetime(*args, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def _events(tmp_path, inputs, now_ms, sim_date=None):
    return _run(tmp_path, """
        result = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: input.simDate});
    """, {"inputs": inputs, "nowMs": now_ms, "simDate": sim_date})


# 2026-10-31 20:00 GMT: an evening in London, the night of 2026-10-31.
AUTUMN_EVENING_MS = _ms(2026, 10, 31, 20, 0)


def test_only_planet_events_in_time_order(tmp_path):
    """Oppositions of the outer planets, greatest elongations of Mercury and Venus, no
    Moon; in time order, all inside the window."""
    got = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)
    rows = got["rows"]
    assert got["date"] == "2026-10-31"
    assert [r["utcMs"] for r in rows] == sorted(r["utcMs"] for r in rows)
    for r in rows:
        assert got["window"]["start"] <= r["utcMs"] <= got["window"]["end"]
        if r["body"] in OPPOSITION_BODIES:
            assert r["kind"] == "opposition"
        else:
            assert r["body"] in ELONGATION_BODIES
            assert r["kind"] in ("elongationEast", "elongationWest")
    kinds = {(r["body"], r["kind"]) for r in rows}
    # 26 months hold an opposition of every outer planet and both elongations of each inner one
    assert {(b, "opposition") for b in OPPOSITION_BODIES} <= kinds
    assert {(b, k) for b in ELONGATION_BODIES for k in ("elongationEast", "elongationWest")} <= kinds
    assert got["window"]["start"] == _ms(2026, 10, 1, 12, 0, tz="Europe/London")
    assert got["window"]["end"] == _ms(2028, 10, 31, 12, 0, tz="Europe/London")


def test_window_starts_thirty_days_before_the_night(tmp_path):
    """Mars's opposition, 2027-02-19 15:38 UTC, is in the window of the night of
    2027-03-21 (local noon 30 days back is 12:00 on 2027-02-19) and not in the next
    night's. daysFromNow counts nights, so it is -30."""
    def mars(sim):
        got = _events(tmp_path, _loc(), _ms(2026, 11, 1, 20, 0), sim)
        return [r for r in got["rows"] if r["body"] == "mars"]

    inside = mars("2027-03-21")
    assert len(inside) == 1
    assert inside[0]["night"] == "2027-02-19" and inside[0]["daysFromNow"] == -30
    assert abs(inside[0]["utcMs"] - _ms(2027, 2, 19, 15, 38)) < 3600000
    assert mars("2027-03-22") == []


def test_night_time_and_days_follow_the_location_zone(tmp_path):
    """Each event's night is its local date, or the day before when the local hour is
    before 12; hm and hmDay are its local time and day relative to that night;
    daysFromNow is that night minus the current one."""
    for loc in (LONDON, SINGAPORE):
        got = _events(tmp_path, _loc(loc), AUTUMN_EVENING_MS)
        current = datetime.strptime(got["date"], "%Y-%m-%d").date()
        tz = ZoneInfo(loc["timezone"])
        before_noon = 0
        for r in got["rows"]:
            local = datetime.fromtimestamp(r["utcMs"] / 1000, tz)
            night = local.date() - timedelta(days=1 if local.hour < 12 else 0)
            assert r["night"] == night.isoformat(), r
            assert r["hm"] == local.strftime("%H:%M"), r
            assert r["hmDay"] == (local.date() - night).days
            assert r["daysFromNow"] == (night - current).days
            before_noon += local.hour < 12
        assert before_noon >= 3, "the noon rule decided some rows"


def test_muted_is_max_altitude_below_threshold(tmp_path):
    """muted exactly when that night's max altitude is below the threshold; never dropped."""
    counts = []
    for threshold in (-90, 20, 89.9):
        got = _events(tmp_path, _loc(altitude_threshold=threshold), AUTUMN_EVENING_MS)
        assert got["threshold"] == threshold
        for r in got["rows"]:
            assert r["muted"] is (r["maxAlt"] < threshold)
        counts.append((len(got["rows"]), sum(r["muted"] for r in got["rows"])))
    assert counts[0][1] == 0 and counts[2][1] == counts[2][0]
    assert counts[0][0] == counts[1][0] == counts[2][0]


def test_max_altitude_is_the_bodies_tab_nightly_max(tmp_path):
    """The same night window and sampling as the Bodies tab's month chart: each row's
    maxAlt is NovaSolarRows.nightlyMax() for its night, exactly."""
    got = _run(tmp_path, """
        const r = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = r.rows.map(x => {
            const m = NovaSolarRows.nightlyMax({inputs: input.inputs, body: x.body,
                year: +x.night.slice(0, 4), month: +x.night.slice(5, 7)});
            return [x.maxAlt, m.max_alt[m.dates.indexOf(x.night)]];
        });
    """, {"inputs": _loc(SINGAPORE), "nowMs": AUTUMN_EVENING_MS})
    assert len(got) >= 15
    for mine, month_chart in got:
        assert mine == month_chart


def test_simulation_and_the_noon_rule(tmp_path):
    """Simulating today's date at the current wall clock is live; before local noon the
    night is still yesterday's, also in simulation."""
    live = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)
    sim = _events(tmp_path, _loc(), AUTUMN_EVENING_MS, "2026-10-31")
    assert sim == live

    morning = _ms(2026, 11, 1, 11, 0, tz="Europe/London")
    assert _events(tmp_path, _loc(), morning)["date"] == "2026-10-31"
    assert _events(tmp_path, _loc(), morning, "2027-05-10")["date"] == "2027-05-09"


def test_elongation_column_and_event_sides(tmp_path):
    """Mercury and Venus carry the (unsigned) elongation, positive at an eastern and
    negative at a western greatest elongation; the outer planets carry none and are
    within a few degrees of 180 at opposition."""
    got = _run(tmp_path, """
        const r = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = r.rows.map(x => [x.body, x.kind, x.elongationDeg, NovaPlanets.details(x.body, x.utcMs).elongationDeg]);
    """, {"inputs": _loc(), "nowMs": AUTUMN_EVENING_MS})
    for body, kind, shown, signed in got:
        if kind == "opposition":
            assert shown is None and abs(signed) > 170, body
        else:
            assert shown == pytest.approx(abs(signed))
            assert (signed > 0) is (kind == "elongationEast"), (body, kind)
            assert (17 if body == "mercury" else 45) < shown < (28 if body == "mercury" else 48)


def test_bad_location_throws(tmp_path):
    got = _run(tmp_path, """
        const tries = [{}, {location: Object.assign({}, input.loc, {lat: null})},
                       {location: Object.assign({}, input.loc, {sampling_interval: 0})}];
        result = tries.map(inputs => {
            try { NovaSolarCalendar.events({inputs: inputs, nowMs: input.nowMs, simDate: null}); return null; }
            catch (e) { return String(e.message); }
        });
    """, {"loc": LONDON, "nowMs": AUTUMN_EVENING_MS})
    assert all(msg and msg.startswith("nova_solar_rows:") for msg in got)


# ------------------------------------------------------------------ ephem parity

PARITY_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
PARITY_END = datetime(2035, 1, 1, tzinfo=timezone.utc)
# Measured worst difference 0.39 h (Uranus); the margin covers the engines' arcminute.
INSTANT_TOLERANCE_H = 1.0
MAX_ALT_TOLERANCE_DEG = 0.1
_EPHEM_EPOCH = ephem.Date("1970/1/1")


def _ephem_ms(d):
    return (float(d) - float(_EPHEM_EPOCH)) * 86400000


def _ephem_events():
    """The events found with ephem: oppositions where the apparent ecliptic longitudes
    of date differ by 180 deg, greatest elongations at the maximum of the apparent
    separation from the Sun; daily scan, then a minute's refinement."""
    sun = ephem.Sun()

    def lon(body, t):
        body.compute(ephem.Date(t))
        return float(ephem.Ecliptic(body, epoch=ephem.Date(t)).lon)

    def from_opposition(body, t):    # planet - Sun - 180 deg, wrapped to -pi..pi
        x = lon(body, t) - lon(sun, t) - math.pi
        return (x + math.pi) % (2 * math.pi) - math.pi

    def separation(body, t):
        body.compute(ephem.Date(t))
        sun.compute(ephem.Date(t))
        return float(ephem.separation(body, sun))

    minute = 1 / 1440
    start, end = float(ephem.Date(PARITY_START)), float(ephem.Date(PARITY_END))
    out = []
    for name in OPPOSITION_BODIES:
        body = getattr(ephem, name.capitalize())()
        t, f0 = start, from_opposition(body, start)
        while t < end:
            f1 = from_opposition(body, t + 1)
            if abs(f0) < 1 and abs(f1) < 1 and (f0 < 0) != (f1 < 0):
                lo, hi = t, t + 1
                while hi - lo > minute:
                    mid = (lo + hi) / 2
                    if (from_opposition(body, mid) < 0) == (f0 < 0):
                        lo = mid
                    else:
                        hi = mid
                out.append((name, "opposition", _ephem_ms((lo + hi) / 2)))
            f0, t = f1, t + 1
    for name in ELONGATION_BODIES:
        body = getattr(ephem, name.capitalize())()
        a, b, t = separation(body, start), separation(body, start + 1), start + 2
        while t < end:
            c = separation(body, t)
            if b > a and b >= c:
                lo, hi = t - 2, t
                while hi - lo > minute:
                    m1, m2 = hi - (hi - lo) * 0.618034, lo + (hi - lo) * 0.618034
                    if separation(body, m1) > separation(body, m2):
                        hi = m2
                    else:
                        lo = m1
                tm = (lo + hi) / 2
                east = (lon(body, tm) - lon(sun, tm)) % (2 * math.pi) < math.pi
                out.append((name, "elongationEast" if east else "elongationWest", _ephem_ms(tm)))
            a, b, t = b, c, t + 1
    return out


@pytest.fixture(scope="module")
def ephem_events():
    return _ephem_events()


def _ephem_max_alt(body_name, times, loc):
    obs = ephem.Observer()
    obs.lat, obs.lon = str(loc["lat"]), str(loc["lon"])
    obs.elevation, obs.pressure = 0, 0
    body = getattr(ephem, body_name.capitalize())()
    best = -90.0
    for t in times:
        obs.date = ephem.Date(datetime.fromtimestamp(t / 1000, timezone.utc))
        body.compute(obs)
        best = max(best, math.degrees(float(body.alt)))
    return best


@pytest.mark.parametrize("loc", [LONDON, SINGAPORE], ids=["london", "singapore"])
def test_events_match_ephem(tmp_path, ephem_events, loc):
    """2024-2034: the same events as ephem, each within an hour; the observing night is
    the one ephem's instant falls in (events within an hour of local noon, where the two
    may straddle the rule, are counted and skipped); that night's maximum altitude
    within 0.1 deg of ephem at the same sample times."""
    got = _run(tmp_path, """
        const found = [];
        for (const body of NovaSolarCalendar.BODIES) {
            for (const e of NovaSolarCalendar.search(body, input.start, input.end)) found.push([body, e.kind, e.utcMs]);
        }
        // Calendars 26 months apart, so every event of the span lies in one of them
        const N = NovaSolarRows._night, A = NovaAstro, W = NovaDashboardRows._wall;
        const s = N.site(input.inputs);
        const rows = [];
        for (const sim of input.sims) {
            for (const r of NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: sim}).rows) {
                const nw = N.nightWindow(r.night, s, A, W, null);
                rows.push({body: r.body, kind: r.kind, utcMs: r.utcMs, night: r.night, maxAlt: r.maxAlt,
                           times: N.samples(nw.start, nw.end, s.interval)});
            }
        }
        result = {found: found, rows: rows};
    """, {"inputs": _loc(loc), "nowMs": AUTUMN_EVENING_MS,
          "start": int(PARITY_START.timestamp() * 1000), "end": int(PARITY_END.timestamp() * 1000),
          "sims": ["2024-01-31", "2026-01-31", "2028-01-31", "2030-01-31", "2032-01-31", "2033-01-31"]})

    found = got["found"]
    assert len(found) == len(ephem_events) >= 120
    tz = ZoneInfo(loc["timezone"])
    near_noon = checked = 0
    for name, kind, ref_ms in ephem_events:
        candidates = [f for f in found if f[0] == name and f[1] == kind and abs(f[2] - ref_ms) < 20 * 86400000]
        assert len(candidates) == 1, (name, kind, ref_ms)
        utc_ms = candidates[0][2]
        assert abs(utc_ms - ref_ms) / 3600000 <= INSTANT_TOLERANCE_H, (name, kind)

        # The calendars scan from other start times, so their refinement may land up to
        # a minute away from search()'s
        row = next(r for r in got["rows"]
                   if r["body"] == name and r["kind"] == kind and abs(r["utcMs"] - utc_ms) <= 120000)
        local = datetime.fromtimestamp(ref_ms / 1000, tz)
        noon = local.replace(hour=12, minute=0, second=0, microsecond=0)
        if abs((local - noon).total_seconds()) < INSTANT_TOLERANCE_H * 3600:
            near_noon += 1
        else:
            night = local.date() - timedelta(days=1 if local.hour < 12 else 0)
            assert row["night"] == night.isoformat(), (name, kind)
        assert abs(row["maxAlt"] - _ephem_max_alt(name, row["times"], loc)) <= MAX_ALT_TOLERANCE_DEG, (name, kind)
        checked += 1
    assert checked == len(ephem_events)
    assert checked - near_noon >= 100, "most nights were compared"
