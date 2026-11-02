"""The Jupiter card's Great Red Spot and the moons' drawn states, in node with the real
engines (static/js/nova_solar_jupiter.js).

The scripts load the way the Solar System page loads them: plain scripts in one vm
context, in the page's order, so nova_solar_jupiter.js finds its engines as globals.

Pinned here, offline:

  * a GRS transit is the instant System II's central meridian (NovaJupiter.geometry()'s
    own cm.II) reaches the spot's drifted longitude, inside the night window and with
    Jupiter at or above the threshold; the spot is then on the drawing's centre line, and
    an hour later it has moved towards the western limb -- right in the normal view
    (north up, east left);
  * the default and the user's own longitude, what makes an override unusable, and when
    the value is old enough to warn about;
  * each moon's drawn state follows from where it is: eclipsed in Jupiter's shadow,
    behind when the disc hides it, visible otherwise.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
              "nova_solar_rows.js", "nova_jupiter.js", "nova_solar_jupiter.js")

LONDON = {"lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15}

# 2 Nov 2026 20:00 UTC, the page's clock; the nights are simulated dates
NOW_MS = 1793649600000

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [bodyFile, inFile, outFile, ...files] = process.argv.slice(2);
const context = { console: console };
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
context.input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const result = vm.runInContext('(function () {' + fs.readFileSync(bodyFile, 'utf8') + '})()', context);
fs.writeFileSync(outFile, JSON.stringify(result));
"""


def _run(tmp_path, body, **input_):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "body.js"
    case.write_text(body)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(dict(input_, site=LONDON, nowMs=NOW_MS)))
    out = tmp_path / "out.json"
    run = subprocess.run([NODE, str(driver), str(case), str(inp), str(out)]
                         + [str(STATIC_JS / name) for name in PAGE_ORDER],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


_TRANSITS = r"""
const SJ = NovaSolarJupiter, R = NovaSolarRows._night, W = NovaDashboardRows._wall;
const out = [];
for (const night of input.nights) {
    const opts = { inputs: { location: input.site }, nowMs: input.nowMs, simDate: night };
    const g = SJ.grs(opts, null);
    const ref = SJ.grsReference(null);
    const site = R.site(opts.inputs);
    const check = (x, date) => {
        const win = R.nightWindow(date, site, NovaAstro, W, null);
        const ge = NovaJupiter.geometry(x.utcMs);
        const lon = SJ.grsLongitude(ref, x.utcMs);
        const off = ((ge.cm.II - lon + 540) % 360) - 180;
        const at = SJ.diagram(ge, 'normal', [], lon).grs;
        const later = SJ.diagram(NovaJupiter.geometry(x.utcMs + 3600000), 'normal', [],
                                 SJ.grsLongitude(ref, x.utcMs + 3600000)).grs;
        return { night: x.night, date: date, utcMs: x.utcMs, hm: x.hm, off: off,
                 hmWant: NovaAstro.formatHM(x.utcMs, site.tz),
                 inWindow: x.utcMs >= win.start && x.utcMs <= win.end,
                 alt: NovaPlanets.altAz('jupiter', x.utcMs, site.lat, site.lon).alt,
                 at: at, later: later };
    };
    out.push({ night: night, ref: ref, stale: g.stale, ageDays: g.ageDays,
               tonight: g.tonight.map(x => check(x, night)),
               next: g.next.map(x => check(x, x.night)),
               threshold: site.threshold, nextNights: SJ.GRS_NEXT_NIGHTS });
}
return out;
"""


def test_grs_transits_put_the_spot_on_the_central_meridian(tmp_path):
    cases = _run(tmp_path, _TRANSITS, nights=["2026-11-07", "2026-12-16"])
    for c in cases:
        assert c["tonight"], f"{c['night']}: a transit tonight, by the scan of these nights"
        assert c["next"], f"{c['night']}: transits on the next nights"
        assert [x["utcMs"] for x in c["next"]] == sorted(x["utcMs"] for x in c["next"])
        assert all(x["night"] > c["night"] for x in c["next"])
        for x in c["tonight"] + c["next"]:
            where = f"{c['night']} {x['night']} {x['hm']}"
            assert x["night"] == x["date"]
            assert abs(x["off"]) < 0.05, f"{where}: CM II is {x['off']} deg off the spot"
            assert x["inWindow"], f"{where}: outside the night window"
            assert x["alt"] >= c["threshold"], f"{where}: Jupiter below the threshold"
            assert x["hm"] == x["hmWant"]
            # On the centre line, south of the equator (down), on the side facing us; an
            # hour later towards the western limb, which is right with north up
            assert x["at"]["visible"] and abs(x["at"]["x"]) < 0.002 and x["at"]["y"] > 0
            assert x["later"]["visible"] and x["later"]["x"] > 0.3


_REFERENCE = r"""
const SJ = NovaSolarJupiter;
const opts = night => ({ inputs: { location: input.site }, nowMs: input.nowMs, simDate: night });
const ref = o => SJ.grsReference(o);
return {
    constants: { grs: SJ.GRS, stale: SJ.GRS_STALE_DAYS },
    plain: ref(null), user: ref({ lon: 300.25, date: '2026-10-01' }),
    wrapped: ref({ lon: 360, date: '2026-10-01' }),
    bad: [ref({ lon: 400, date: '2026-10-01' }), ref({ lon: -1, date: '2026-10-01' }),
          ref({ lon: NaN, date: '2026-10-01' }), ref({ lon: '91', date: '2026-10-01' }),
          ref({ lon: 91, date: '2026-02-30' }), ref({ lon: 91, date: '1.6.2026' }), ref({})],
    // The drift: 1.75 deg a month from the reference date, wrapping at 360
    atRef: SJ.grsLongitude(ref(null), Date.UTC(2026, 5, 1)),
    yearOn: SJ.grsLongitude(ref(null), Date.UTC(2027, 5, 1)),
    wrap: SJ.grsLongitude(ref({ lon: 359, date: '2026-10-01' }), Date.UTC(2026, 11, 1)),
    fresh: SJ.grs(opts('2026-11-02'), null), old: SJ.grs(opts('2026-12-09'), null),
    mine: SJ.grs(opts('2026-12-09'), { lon: 100, date: '2026-12-01' }),
};
"""


def test_grs_reference_drift_and_staleness(tmp_path):
    got = _run(tmp_path, _REFERENCE)
    grs = got["constants"]["grs"]
    assert (grs["lon"], grs["date"], grs["driftPerMonth"]) == (91.0, "2026-06-01", 1.75)
    assert got["constants"]["stale"] == 183
    assert got["plain"] == {"lon": 91.0, "date": "2026-06-01", "user": False}
    assert got["user"] == {"lon": 300.25, "date": "2026-10-01", "user": True}
    assert got["wrapped"] == {"lon": 0, "date": "2026-10-01", "user": True}
    assert all(b == got["plain"] for b in got["bad"]), got["bad"]
    assert got["atRef"] == pytest.approx(91.0, abs=1e-9)
    month = 365.25 / 12                                   # days
    assert got["yearOn"] == pytest.approx(91.0 + 1.75 * 365 / month, abs=1e-6)
    assert got["wrap"] == pytest.approx(359 + 1.75 * 61 / month - 360, abs=1e-6), "wraps past 360"
    # 2 Nov 2026 is 154 days after the default's date, 9 Dec 191: past six months
    assert (got["fresh"]["ageDays"], got["fresh"]["stale"], got["fresh"]["user"]) == (154, False, False)
    assert (got["old"]["ageDays"], got["old"]["stale"]) == (191, True)
    assert (got["mine"]["ageDays"], got["mine"]["stale"], got["mine"]["user"]) == (8, False, True)
    assert (got["mine"]["refLon"], got["mine"]["refDate"]) == (100, "2026-12-01")


_STATES = r"""
const SJ = NovaSolarJupiter;
const out = [];
for (const night of input.nights) {
    const t = SJ.tonight({ inputs: { location: input.site }, nowMs: input.nowMs, simDate: night });
    for (const mode of SJ.VIEWS) {
        const d = SJ.diagram(t.geometry, mode, t.moons);
        d.moons.forEach((m, i) => out.push({ night: night, mode: mode, name: m.name, state: m.state,
            front: t.moons[i].front, onDisc: t.moons[i].onDisc, behindDisc: t.moons[i].behindDisc,
            eclipsed: t.moons[i].eclipsed, z: t.moons[i].z,
            inside: Math.hypot(t.moons[i].x, t.moons[i].y) < 1 }));
    }
}
return out;
"""


def test_each_moons_drawn_state_follows_where_it_is(tmp_path):
    rows = _run(tmp_path, _STATES, nights=["2026-11-07", "2026-11-14", "2026-12-09", "2026-12-16"])
    for r in rows:
        # On the disc only in front of it; hidden behind it is the other side of the planet
        assert r["front"] == (r["z"] > 0), r
        assert r["onDisc"] == (r["z"] > 0 and r["inside"]), r
        assert r["behindDisc"] == (r["z"] <= 0 and r["inside"]), r
        assert not (r["onDisc"] and r["behindDisc"]), r
        want = "eclipsed" if r["eclipsed"] else "behind" if r["behindDisc"] else "visible"
        assert r["state"] == want, r
    assert any(r["behindDisc"] for r in rows), "an occulted moon among the nights"
    states = {r["state"] for r in rows}
    assert states == {"visible", "behind", "eclipsed"}, "every state is exercised"


_SHADOW = r"""
const SJ = NovaSolarJupiter;
// The Sun 10 deg from the line of sight, due east (position angle 90): S = (sin 10, 0, cos 10)
// in the sky frame (x east, y south, z towards the Earth).
const S = [Math.sin(10 * Math.PI / 180), 0, Math.cos(10 * Math.PI / 180)];
const moon = { name: 'io', x: 0.3, y: 0.2, z: 5, rj: Math.hypot(0.3, 0.2, 5) };
const behind = { name: 'io', x: 0.3, y: 0.2, z: -5, rj: Math.hypot(0.3, 0.2, 5) };
return { sh: SJ._shadowOf(moon, S), front: SJ._moonState(moon, S), back: SJ._moonState(behind, S), S: S };
"""


def test_a_shadow_is_drawn_where_it_lands_on_the_sphere(tmp_path):
    """Worked by hand: a moon at (0.3, 0.2, 5) Jupiter radii, the Sun at phase 10 deg due
    east, S = (0.173648, 0, 0.984808). Along the Sun line the moon is at
    a = 0.3 * 0.173648 + 5 * 0.984808 = 4.976133; off it, p = m - a S =
    (-0.564096, 0.2, 0.099465), |p| = 0.606711. The ray meets the sunlit hemisphere at
    q = p + sqrt(1 - 0.606711^2) S = p + 0.794923 S = (-0.426060, 0.2, 0.882311), |q| = 1.
    The Earth sees (x, y) = (-0.426060, 0.2): 0.138 radius from the plane point p."""
    got = _run(tmp_path, _SHADOW)
    sh = got["sh"]
    assert sh["onDisc"] is True and sh["eclipsed"] is False
    assert sh["x"] == pytest.approx(-0.426060, abs=1e-6)
    assert sh["y"] == pytest.approx(0.2, abs=1e-12)
    # Independently: the point is on the sphere, on the moon's Sun ray, on the lit side
    S = got["S"]
    z = math.sqrt(1 - sh["x"] ** 2 - sh["y"] ** 2)
    q = (sh["x"], sh["y"], z)
    d = [m - c for m, c in zip((0.3, 0.2, 5), q)]
    n = math.sqrt(sum(c * c for c in d))
    assert all(abs(c / n - s) < 1e-9 for c, s in zip(d, S)), "q - m runs along the Sun line"
    assert sum(c * s for c, s in zip(q, S)) > 0, "on the sunlit side"
    assert got["front"]["shadow"] == {"x": sh["x"], "y": sh["y"]}
    # The same place behind the planet is hidden by it, not on it
    assert (got["front"]["onDisc"], got["front"]["behindDisc"]) == (True, False)
    assert (got["back"]["onDisc"], got["back"]["behindDisc"], got["back"]["front"]) == (False, True, False)


_MERGE = r"""
const SJ = NovaSolarJupiter, MIN = 60000;
// Night of 10 Dec 2026 at its highest, Jupiter well above the threshold: the shadows are
// placed round that instant, and the engine's phenomena are replaced by them alone.
const T = SJ.tonight({ inputs: { location: input.site }, nowMs: input.nowMs, simDate: '2026-12-10' }).instantUtc;
const at = (moon, a, b) => ({ moon: moon, type: 'shadow', startUtcMs: T + a * MIN, endUtcMs: T + b * MIN });
const made = [
    at('io', -60, 30),          // A
    at('europa', -20, 60),      // B: overlaps A -> one double
    at('ganymede', 40, 80),     // C: overlaps B only, starts after B
    at('callisto', 100, 130),   // D: alone
];
const real = NovaJupiter.events;
NovaJupiter.events = (s, e) => made.filter(x => x.startUtcMs >= s && x.startUtcMs < e);
let next;
try { next = SJ.nextNights({ inputs: { location: input.site }, nowMs: input.nowMs, simDate: '2026-12-09' }); }
finally { NovaJupiter.events = real; }
return { T: T, made: made, shadows: next.shadows };
"""


def test_the_next_nights_merge_drops_no_shadow(tmp_path):
    """Four shadows on one night: Io's and Europa's overlap and are merged into one double,
    Ganymede's overlaps only Europa's and starts after it, Callisto's is alone. Each shadow
    is either its own entry or one half of a double; none is lost to the merge."""
    got = _run(tmp_path, _MERGE)
    T, rows = got["T"], got["shadows"]
    minute = 60000
    assert [(r["moons"], r["double"]) for r in rows] == [
        (["io", "europa"], True), (["ganymede"], False), (["callisto"], False)]
    assert rows[0]["utcMs"] == T - 60 * minute and rows[0]["minutes"] == 120
    covered = [m for r in rows for m in r["moons"]]
    assert sorted(covered) == sorted(x["moon"] for x in got["made"]), "every shadow is listed"
