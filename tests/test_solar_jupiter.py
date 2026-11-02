"""Pins the Solar System page's Galilean calendar rows to the shared Jupiter engine.

static/js/nova_solar_jupiter.js builds the Calendar's Galilean rows by re-deriving the
shadow test in shadowOf() rather than calling
NovaJupiter.events(): the engine refines every boundary crossing of every moon, thousands
a year, and costs about 1.6 s over the six-month window against the Calendar's own 300 ms.
The two must still agree about when two shadows are on the disc and whether Jupiter is
above the site's threshold, so the rows are checked here against the engine's own shadow
events (static/js/nova_jupiter.js, NovaJupiter.events()), boundary for boundary.

All of it runs in node with the real engines and no DOM, the way the page loads them:
plain scripts in one process, globals set before nova_solar_jupiter.js is required. No
network, no database, no Flask app. The module's own window is six months; here it is
bounded to the first sixty days (a fixed night, 2 Nov 2026) and the engine is asked over
the same slice, so the run stays under a few seconds.
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

# 2 Nov 2026 20:00 UTC, the wall clock the page's other tests run at.
NOW_MS = 1793649600000
SLICE_DAYS = 60

LONDON = {"lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15}
SANTIAGO = {"lat": -33.45, "lon": -70.67, "timezone": "America/Santiago",
            "altitude_threshold": 20, "sampling_interval": 15}
SITES = {"London": LONDON, "Santiago": SANTIAGO}

# Loaded in the page's order, each under the global the module looks up at call time.
SITE_SCRIPTS = [
    ("nova_astro.js", "NovaAstro"),
    ("nova_planets.js", "NovaPlanets"),
    ("nova_moon.js", "NovaMoon"),
    ("nova_jupiter.js", "NovaJupiter"),
    ("nova_mars.js", "NovaMars"),
    ("nova_dashboard_rows.js", "NovaDashboardRows"),
    ("nova_solar_rows.js", "NovaSolarRows"),
    ("nova_solar_calendar.js", "NovaSolarCalendar"),
    ("nova_solar_moon.js", "NovaSolarMoon"),
    ("nova_moon_features.js", "NovaMoonFeatures"),
]

# The driver loads the page's scripts under their globals, then the page module last.
_DRIVER = r"""
'use strict';
const fs = require('fs');
process.env.TZ = 'UTC';
const [jsDir, inFile, outFile] = process.argv.slice(2);
const SCRIPTS = %s;
for (const [file, name] of SCRIPTS) global[name] = require(jsDir + file);
const SJ = require(jsDir + 'nova_solar_jupiter.js');
const { NovaAstro: A, NovaPlanets: Pl, NovaJupiter: J, NovaSolarRows: SR,
        NovaDashboardRows: DR, NovaSolarCalendar: Cal } = global;

const MIN = 60000, DAY = 86400000;
const payload = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const out = {};

for (const [name, loc] of Object.entries(payload.sites)) {
    const opts = { inputs: { location: loc }, nowMs: payload.nowMs, simDate: null };
    const res = SJ.calendarRows(opts);
    const win = res.window;
    const sliceEnd = win.start + payload.sliceDays * DAY;
    const oneMin = 60000;                       // scan the slice finely, engine-side
    const thr = loc.altitude_threshold;
    const site = SR._night.site(opts.inputs);
    const W = DR._wall;
    const sun = {}, nights = {};
    const nightOf = t => {
        const d = W.observingNight(A.localParts(t, loc.timezone));
        if (!nights[d]) nights[d] = SR._night.nightWindow(d, site, A, W, sun);
        return nights[d];
    };
    const altAt = t => Pl.altAz('jupiter', t, loc.lat, loc.lon).alt;
    const visible = t => { const nw = nightOf(t); return t >= nw.start && t <= nw.end && altAt(t) >= thr; };

    // The engine's shadow events over the slice (a margin so events crossing the ends are whole).
    const ev = J.events(win.start - 3600000, sliceEnd + 3600000).filter(e => e.type === 'shadow');
    const shadowsAt = t => {
        let n = 0;
        for (const e of ev) if (e.startUtcMs <= t && t <= e.endUtcMs) n++;
        return n;
    };
    // A double: two or more shadows on the disc at once, from the engine's own events.
    const marks = [];
    ev.forEach(e => { marks.push({ t: e.startUtcMs, d: 1 }); marks.push({ t: e.endUtcMs, d: -1 }); });
    marks.sort((x, y) => x.t - y.t || x.d - y.d);
    let depth = 0, open = null;
    const doubles = [];
    for (const m of marks) {
        depth += m.d;
        if (depth >= 2 && open === null) open = m.t;
        else if (depth < 2 && open !== null) { doubles.push({ start: open, end: m.t }); open = null; }
    }
    const inWindow = d => d.start >= win.start && d.end <= sliceEnd;

    // The predicate the rows must follow: two shadows and the night, above the threshold.
    const pred = t => shadowsAt(t) >= 2 && visible(t);
    // The crossing of `pred` between an inside and an outside sample, to five seconds.
    const bisect = (inside, outside) => {
        let a = inside, b = outside;
        while (Math.abs(b - a) > 5000) {
            const m = Math.round((a + b) / 2);
            if (m === a || m === b) break;
            if (pred(m)) a = m; else b = m;
        }
        return Math.round((a + b) / 2);
    };

    const rows = [];
    for (const r of res.rows) {
        if (!(r.utcMs >= win.start && r.endUtcMs <= sliceEnd)) continue;
        const mid = Math.round((r.utcMs + r.endUtcMs) / 2);
        const d = doubles.find(x => x.start <= mid && mid <= x.end) || null;
        let visStart = null, visEnd = null;
        if (d) {
            let s0 = null, e0 = null;
            for (let t = d.start; t <= d.end; t += oneMin) if (pred(t)) { s0 = t; break; }
            for (let t = d.end; t >= d.start; t -= oneMin) if (pred(t)) { e0 = t; break; }
            visStart = s0 !== null ? bisect(s0, s0 - oneMin) : null;
            visEnd = e0 !== null ? bisect(e0, e0 + oneMin) : null;
        }
        const sample = k => Math.round(r.utcMs + (r.endUtcMs - r.utcMs) * k / 10);
        rows.push({
            utcMs: r.utcMs, endUtcMs: r.endUtcMs, minutes: r.minutes, moons: r.moons, night: r.night,
            hm: r.hm, maxAlt: r.maxAlt, muted: r.muted, kind: r.kind, body: r.body, bodies: r.bodies,
            keys: Object.keys(r).sort(),
            obsNight: W.observingNight(A.localParts(r.utcMs, loc.timezone)),
            engineMoonsAtMid: ev.filter(e => e.startUtcMs <= mid && mid <= e.endUtcMs).map(e => e.moon),
            interior: [1, 2, 3, 4, 5, 6, 7, 8, 9].map(k => ({ t: sample(k), n: shadowsAt(sample(k)), alt: altAt(sample(k)) })),
            beforePred: pred(r.utcMs - 2 * oneMin), afterPred: pred(r.endUtcMs + 2 * oneMin),
            engine: d ? { start: d.start, end: d.end } : null,
            startDiff: visStart !== null ? (r.utcMs - visStart) / 1000 : null,
            endDiff: visEnd !== null ? (r.endUtcMs - visEnd) / 1000 : null,
            clippedStart: d ? (visStart - d.start) > oneMin : null,
            clippedEnd: d ? (d.end - visEnd) > oneMin : null,
        });
    }

    // Completeness: every engine double in the slice that is visible by night above the
    // threshold, and at least two minutes long, should be covered by a row.
    const missed = [];
    let visibleDoubles = 0;
    for (const d of doubles.filter(inWindow)) {
        let first = null, last = null;
        for (let t = d.start; t <= d.end; t += oneMin) if (visible(t)) { if (first === null) first = t; last = t; }
        if (first === null) continue;
        visibleDoubles++;
        const covered = t => rows.some(r => r.utcMs - 20000 <= t && t <= r.endUtcMs + 20000);
        for (let t = first; t <= last; t += oneMin) {
            if (visible(t) && !covered(t)) { missed.push({ start: d.start, end: d.end, min: (d.end - d.start) / MIN }); break; }
        }
    }
    out[name] = {
        window: win, sliceEnd: sliceEnd, scanStepMs: SJ.SCAN_STEP_MS,
        moonsOrder: J.MOONS, planetKeys: Object.keys(Cal.events(opts).rows[0]).sort(),
        totalRows: res.rows.length, rows: rows,
        engineShadows: ev.length, doublesInSlice: doubles.filter(inWindow).length,
        visibleDoubles: visibleDoubles, missed: missed,
    };
}
fs.writeFileSync(outFile, JSON.stringify(out));
""" % json.dumps(SITE_SCRIPTS)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _row(r):
    return f"{r['night']} {r['hm']} {'+'.join(r['moons'])} ({r['minutes']} min)"


@pytest.fixture(scope="module")
def calendar(tmp_path_factory):
    """calendarRows() for both sites over the slice, with the engine's events beside it."""
    payload = {"nowMs": NOW_MS, "sliceDays": SLICE_DAYS, "sites": SITES}
    tmp = tmp_path_factory.mktemp("jupiter")
    driver = tmp / "driver.js"
    driver.write_text(_DRIVER)
    inp = tmp / "in.json"
    inp.write_text(json.dumps(payload))
    out = tmp / "out.json"
    run = subprocess.run([NODE, str(driver), str(STATIC_JS) + "/", str(inp), str(out)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def test_the_rows_have_the_calendar_shape(calendar):
    """Two or more names in the engine's moon order, minutes the rounded span, and the
    observing night the row starts on -- the Calendar's own row shape otherwise."""
    for name, site in calendar.items():
        rows = site["rows"]
        assert len(rows) >= 2, f"{name}: only {len(rows)} rows in the slice to check"
        extra = {"endUtcMs", "minutes", "moons"}
        for r in rows:
            assert r["body"] == "jupiter" and r["kind"] == "galilean" and r["bodies"] == ["jupiter"], _row(r)
            assert set(r["keys"]) - set(site["planetKeys"]) == extra, _row(r)
            assert set(site["planetKeys"]) <= set(r["keys"]), _row(r)
            assert r["utcMs"] < r["endUtcMs"], _row(r)
            span_minutes = int((r["endUtcMs"] - r["utcMs"]) / 60000 + 0.5)
            assert r["minutes"] == span_minutes, (
                f"{_row(r)}: minutes {r['minutes']}, span {span_minutes}")
            assert len(r["moons"]) >= 2, _row(r)
            idx = [site["moonsOrder"].index(m) for m in r["moons"]]
            assert all(m in site["moonsOrder"] for m in r["moons"]), _row(r)
            assert idx == sorted(set(idx)), f"{_row(r)}: not in the engine's moon order"
            assert r["night"] == r["obsNight"], (
                f"{_row(r)}: night {r['night']}, observing night {r['obsNight']}")
            assert r["muted"] is False, _row(r)
            assert site["window"]["start"] <= r["utcMs"] and r["endUtcMs"] <= site["sliceEnd"], _row(r)


def test_the_rows_are_the_engines_doubles(calendar):
    """Each row's interior really has two engine shadows on the disc and Jupiter above the
    threshold, the engine's own double is not visible just outside the row, and -- where
    the row is not clipped by the night or the threshold -- its ends are the engine's own
    to within a minute."""
    worst = 0.0
    clipped = []
    for name, site in calendar.items():
        assert site["rows"], f"{name}: no rows to pin"
        scan_s = site["scanStepMs"] / 1000
        for r in site["rows"]:
            assert r["engine"] is not None, f"{_row(r)}: no engine double covers its middle"
            for s in r["interior"]:
                assert s["n"] >= 2, f"{_row(r)}: engine has {s['n']} shadow(s) at {_iso(s['t'])}"
                assert s["alt"] >= SITES[name]["altitude_threshold"], (
                    f"{_row(r)}: Jupiter at {s['alt']:.1f} deg at {_iso(s['t'])}")
            assert not r["beforePred"], f"{_row(r)}: the engine double is visible 2 min before its start"
            assert not r["afterPred"], f"{_row(r)}: the engine double is visible 2 min after its end"
            assert set(r["moons"]) == set(r["engineMoonsAtMid"]), (
                f"{_row(r)}: moons, engine has {'+'.join(r['engineMoonsAtMid'])}")
            if not r["clippedStart"]:
                worst = max(worst, abs(r["startDiff"]))
                assert abs(r["startDiff"]) <= 60, (
                    f"{_row(r)}: starts {r['startDiff']:.0f} s from the engine's crossing")
            else:
                # A start clipped by the night (dusk) or by the threshold begins where
                # that happens, at the same minute-resolution of the engine-side scan.
                assert -scan_s - 60 <= r["startDiff"] <= 60, (
                    f"{_row(r)}: starts {r['startDiff']:.0f} s from the engine's crossing")
            if not r["clippedEnd"]:
                worst = max(worst, abs(r["endDiff"]))
                assert abs(r["endDiff"]) <= 60, (
                    f"{_row(r)}: ends {r['endDiff']:.0f} s from the engine's crossing")
            else:
                # The engine's double runs on past the night (sunrise) or past the
                # threshold: the row ends where the observer's window does, so a small
                # negative difference is expected — the engine's own predicate is sampled
                # a minute apart here — and a large one either way would mean the row ends
                # at the wrong place. edge()'s test is that predicate exactly: two
                # shadows, above the threshold and inside the night window.
                assert -60 <= r["endDiff"] <= 60, (
                    f"{_row(r)}: ends {r['endDiff']:.0f} s from the engine's crossing")
            if r["clippedStart"] or r["clippedEnd"]:
                clipped.append((name, _row(r), r["startDiff"], r["endDiff"]))
    # The module bisects a boundary to ten seconds and the engine's crossing here is
    # resolved to five, so an agreement better than about 15 s is noise; this slice
    # measures 6.6 s at worst, which is the module's own ten-second bisection.
    assert worst <= 15, f"worst unclipped boundary disagreement {worst:.1f} s"
    assert clipped, "no clipped row in the slice to exercise the threshold and the night"


def test_the_engine_doubles_are_all_on_the_calendar(calendar):
    """Soundness is not enough: every double the engine finds in the slice, visible by
    night above the threshold, must be covered by a row. The module scans at a ten-minute
    step, so a double shorter than that can fall between two samples and be missed; its
    header documents one of 24 seconds over the six-month window. Anything of two minutes
    or more must be there."""
    covered = 0
    for name, site in calendar.items():
        covered += site["visibleDoubles"] - len(site["missed"])
        for m in site["missed"]:
            assert m["min"] < 2.0, (
                f"{name}: engine double {_iso(m['start'])} ({m['min']:.1f} min) is not on the calendar")
    assert covered >= 2, "too few engine doubles covered to be a meaningful check"
