"""Checks for the Mars card (static/js/nova_solar_mars.js), in node with the real engines.

The scripts load the way the Solar System page loads them: plain scripts in one vm
context, in the page's order, so nova_solar_mars.js finds NovaAstro, NovaPlanets,
NovaMars, NovaMarsFeatures, NovaDashboardRows and NovaSolarRows as globals.

Three things are pinned, all offline:

  * the diagram's orientation, against an independent 3D reconstruction (below). A
    mirrored or mis-rotated disc is otherwise invisible — every marker sits on the disc
    and the pole points somewhere plausible — so the reconstruction rebuilds the line of
    sight, the celestial-north sky basis, the pole, the sub-Earth point and the sub-solar
    point from the engine's public outputs alone, builds each feature's surface point the
    engine's own way, and compares the drawn vectors against it in all three views.

  * the card's own invariants: cmDist's sign is the drawn x's, |(x,y)| == sin(gamma),
    nearLimb == gamma > 75, the ranking, and the top/more split.

  * the central-meridian passages, against an independent 1-minute scan of the engine's
    own cm for the first crossing.

Mars's shared engine (nova_mars.js) is validated in nova-ios against JPL Horizons
(tools/test_mars.cjs, tools/gen_mars_fixture.py). Nothing here needs the network, a
database or Flask.
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
              "nova_solar_rows.js", "nova_mars.js", "nova_mars_features.js", "nova_solar_mars.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

LONDON = {"lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London",
          "altitude_threshold": 30, "sampling_interval": 15}
SYDNEY = {"lat": -33.8688, "lon": 151.2093, "timezone": "Australia/Sydney",
          "altitude_threshold": 30, "sampling_interval": 15}
SITES = (LONDON, SYDNEY)

# Nights spread over Mars's apparitions: a near opposition, a modest one, a far one and
# another opposition.
NIGHTS = ("2026-11-02", "2027-02-14", "2027-09-01", "2029-01-03")

# The reconstruction agrees with the card to ~7e-7 for the pole, the markers and the
# bright limb (the residual is the pole's unexported linear rate term: the harness
# rebuilds the pole from de and polePA, and polePA carries the IAU pole's rate only
# through the engine's own position, while the drawing turns the disc by polePA
# directly). A mirrored disc is off by ~2.0, so 2e-5 is loose enough for the engine's
# noise and tight enough to catch a flip.
TOL = 2e-5

# Loads the page's scripts into one context, then runs the case's body with `input`.
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
    inp.write_text(json.dumps(input_))
    out = tmp_path / "out.json"
    run = subprocess.run([NODE, str(driver), str(case), str(inp), str(out)]
                         + [str(STATIC_JS / name) for name in PAGE_ORDER],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


# The independent 3D reconstruction, ported from the working bench harness. It uses only
# the engine's public outputs: the planet's J2000 RA/Dec via NovaPlanets.position('mars')
# and polePA, sunPA, de, phaseAngleDeg and cm from NovaMars.geometry().
_GEOM = r"""
const DEG = Math.PI / 180;
const sub = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const unit3 = a => { const n = Math.hypot(a[0], a[1], a[2]); return [a[0]/n, a[1]/n, a[2]/n]; };

// T       line of sight, Mars -> Earth
// n_sky   celestial north's projection on the sky plane, e_sky = T x n_sky
// P       the pole, rebuilt from de and polePA
// x_draw  drawing x (right), y_draw drawing y (up, towards celestial north)
function frame(utcMs, g) {
    const p = NovaPlanets.position('mars', utcMs);
    const ra = p.raHours * 15 * DEG, dec = p.decDeg * DEG;
    const T = [-Math.cos(dec)*Math.cos(ra), -Math.cos(dec)*Math.sin(ra), -Math.sin(dec)];
    const nc = [0, 0, 1];
    const n_sky = unit3(sub(nc, T.map(x => x * dot(nc, T))));
    const e_sky = cross(T, n_sky);
    const at = pa => { const r = pa * DEG;
        return [0,1,2].map(i => Math.cos(r)*n_sky[i] + Math.sin(r)*e_sky[i]); };
    const pp = at(g.polePA);
    const P = [0,1,2].map(i => Math.sin(g.de*DEG)*T[i] + Math.cos(g.de*DEG)*pp[i]);
    const y_draw = unit3(sub(P, T.map(x => x * dot(P, T))));
    const x_draw = cross(y_draw, T);
    const draw = v => [dot(v, x_draw), dot(v, y_draw)];
    const s_sun = [0,1,2].map(i => Math.cos(g.phaseAngleDeg*DEG)*T[i]
        + Math.sin(g.phaseAngleDeg*DEG)*at(g.sunPA)[i]);
    return { T: T, P: P, draw: draw, s_sun: s_sun };
}

// The card returns SVG; this is back to the card's own frame (y up towards celestial
// north), undoing viewOf: SVG is (sx * x, sy * y).
const fromSvg = (mode, x, y) => {
    const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
    return [sx * x, sy * y];
};
"""


# --- the diagram's orientation -----------------------------------------------------------

_DIAGRAM = _GEOM + r"""
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        const t = NovaSolarMars.tonight({ inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null });
        const g = t.geometry;
        const r = frame(t.instantUtc, g);
        const pa = g.polePA * DEG;
        const sky = v => { const d = r.draw(v);
            return [Math.cos(pa)*d[0] - Math.sin(pa)*d[1], Math.sin(pa)*d[0] + Math.cos(pa)*d[1]]; };
        const err = (a, b) => Math.hypot(a[0]-b[0], a[1]-b[1]);

        const poleWant = sky(r.P);
        const pu = Math.hypot(poleWant[0], poleWant[1]);

        // The body frame fixed by cm itself: the sub-Earth point's body longitude is -cm,
        // then the standard east-longitude construction.
        const w = unit3(sub(r.T, r.P.map(x => x * dot(r.T, r.P))));
        const pw = cross(r.P, w);
        const cmR = g.cm * DEG;
        const xb = [0,1,2].map(i => Math.cos(cmR)*w[i] + Math.sin(cmR)*pw[i]);
        const yb = [0,1,2].map(i => -Math.sin(cmR)*w[i] + Math.cos(cmR)*pw[i]);
        const point3 = {};
        for (const f of t.features) {
            const la = f.lat * DEG, lo = f.lon * DEG;
            point3[f.name] = [0,1,2].map(i => Math.sin(la)*r.P[i]
                + Math.cos(la)*(Math.cos(lo)*xb[i] + Math.sin(lo)*yb[i]));
        }

        const sunWant = sky(r.s_sun);
        const smag = Math.hypot(sunWant[0], sunWant[1]);

        for (const mode of NovaSolarMars.VIEWS) {
            const d = NovaSolarMars.diagram(g, mode, t.features);
            // The bright limb, read off the shaded path the card actually drew. The path
            // is litPath(-u, -k): its first point is (-uy, ux) of the u it was given, so
            // the card's own u is (py, -px), and the lit limb is -u. fromSvg undoes the
            // view, giving the sky-frame direction to compare with the sub-solar one.
            let sun = null;
            if (d.shaded) {
                const tok = d.shaded.trim().split(/\s+/);
                if (tok.length === 20 && tok[0] === 'M') {   // M p A ... A ... Z (phase 0 < |k| < 0.9995)
                    const px = +tok[1], py = +tok[2];
                    const b = fromSvg(mode, -py, px);
                    sun = err(b, [sunWant[0]/smag, sunWant[1]/smag]);
                }
            }
            out.push({
                night: night, site: loc.timezone, mode: mode,
                pole: err(fromSvg(mode, d.pole.x, d.pole.y), [poleWant[0]/pu, poleWant[1]/pu]),
                tilt: Math.abs(Math.hypot(poleWant[0], poleWant[1]) - Math.cos(g.de*DEG)),
                sun: sun,
                markers: d.markers.map(m => ({ name: m.name,
                    err: err(fromSvg(mode, m.x, m.y), sky(point3[m.name])) })),
            });
        }
    }
}
return out;
"""


def test_diagram_orientation_against_the_3d_reconstruction(tmp_path):
    """The pole, every marker and the bright limb, in all three views, reconstructed
    independently from the engine's public outputs. TOL is 2e-5; the measured worst
    disagreement is ~7e-7 for all three (the bright limb is read off the shaded path,
    whose coordinates are rounded to six decimals)."""
    cases = _run(tmp_path, _DIAGRAM, nights=list(NIGHTS), sites=list(SITES))
    assert len(cases) == len(NIGHTS) * len(SITES) * 3

    checked_markers = 0
    for c in cases:
        where = f"{c['night']} @ {c['site']}·{c['mode']}"
        assert c["pole"] <= TOL, (
            f"the pole is drawn off its reconstructed projection at {where}: "
            f"|delta| = {c['pole']:.3e}")
        assert c["tilt"] <= TOL, (
            f"the pole's offset from the top is not cos(De) at {where}: |delta| = {c['tilt']:.3e}")
        assert c["sun"] is not None, (
            f"the disc is full at {where}: the shaded path carries no bright limb to check")
        assert c["sun"] <= TOL, (
            f"the bright limb drawn at {where} is not the sub-solar direction: "
            f"|delta| = {c['sun']:.3e}")
        for m in c["markers"]:
            checked_markers += 1
            assert m["err"] <= TOL, (
                f"the marker for {m['name']} is off its reconstructed surface point at "
                f"{where}: |delta| = {m['err']:.3e}")

    assert checked_markers == sum(len(c["markers"]) for c in cases)


# --- tonight's invariants -----------------------------------------------------------------

_TONIGHT = r"""
const DEG = Math.PI / 180;
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        const t = NovaSolarMars.tonight({ inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null });
        out.push({
            night: night, site: loc.timezone,
            consts: { top: NovaSolarMars.TOP_COUNT, visible: NovaSolarMars.VISIBLE_DEG,
                      near: NovaSolarMars.NEAR_LIMB_DEG },
            rows: t.features.map(f => ({
                name: f.name, tier: f.tier, diameterKm: f.diameterKm,
                x: f.x, y: f.y, gamma: f.gamma, cmDist: f.cmDist, nearLimb: f.nearLimb,
                r: Math.hypot(f.x, f.y), sinGamma: Math.sin(f.gamma*DEG),
            })),
            topNames: t.top.map(f => f.name), moreNames: t.more.map(f => f.name),
            empty: t.empty,
        });
    }
}
return out;
"""


def test_tonight_features_are_consistent_with_their_drawing(tmp_path):
    """cmDist's sign is x's, |(x,y)| == sin(gamma), nearLimb == gamma > 75, and only
    features under 85 deg from the disc centre appear."""
    cases = _run(tmp_path, _TONIGHT, nights=list(NIGHTS), sites=list(SITES))
    for c in cases:
        consts = c["consts"]
        assert 0 < consts["near"] < consts["visible"]
        where = f"{c['night']} @ {c['site']}"
        assert c["empty"] is None and c["rows"], f"no features on the disc at {where}"
        for f in c["rows"]:
            tag = f"{where} {f['name']}"
            assert f["gamma"] < consts["visible"], f"{tag}: gamma {f['gamma']} not under {consts['visible']}"
            assert abs(f["r"] - f["sinGamma"]) < 1e-9, (
                f"{tag}: |(x,y)| = {f['r']} is not sin(gamma) = {f['sinGamma']}")
            assert f["nearLimb"] == (f["gamma"] > consts["near"]), (
                f"{tag}: nearLimb {f['nearLimb']} but gamma = {f['gamma']}")
            if f["x"] > 1e-12:
                assert f["cmDist"] > 0, f"{tag}: x > 0 but cmDist {f['cmDist']}"
            elif f["x"] < -1e-12:
                assert f["cmDist"] < 0, f"{tag}: x < 0 but cmDist {f['cmDist']}"
            else:
                assert abs(f["cmDist"]) < 1e-9, f"{tag}: x == 0 but cmDist {f['cmDist']}"


def test_tonight_ranking_and_the_top_more_split(tmp_path):
    """tier first, then |cmDist|, then diameter, then name; top is the first TOP_COUNT."""
    cases = _run(tmp_path, _TONIGHT, nights=list(NIGHTS), sites=list(SITES))
    for c in cases:
        where = f"{c['night']} @ {c['site']}"
        key = lambda f: (f["tier"], abs(f["cmDist"]), -f["diameterKm"], f["name"])
        ordered = sorted(c["rows"], key=key)
        assert [f["name"] for f in c["rows"]] == [f["name"] for f in ordered], (
            f"{where}: features are not ranked tier, |cmDist|, diameter, name")
        top = c["consts"]["top"]
        assert c["topNames"] == [f["name"] for f in c["rows"]][:top], f"{where}: top split"
        assert c["moreNames"] == [f["name"] for f in c["rows"]][top:], f"{where}: more split"


# --- the central-meridian passages --------------------------------------------------------

_PASSAGES = r"""
const out = [];
const span = 1.1 * (360 / Math.abs(NovaMars.W_RATE)) * 86400000;
const dayNumber = p => Date.UTC(p.year, p.month - 1, p.day) / 86400000;
for (const night of input.nights) {
    for (const loc of input.sites) {
        const t = NovaSolarMars.tonight({ inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null });
        for (const f of t.features.slice(0, input.count)) {
            const target = ((360 - f.lon) % 360 + 360) % 360;    // the cm that puts it on the meridian
            const fd = tt => { const c = NovaMars.geometry(tt).cm;
                return ((c - target + 180) % 360 + 360) % 360 - 180; };
            let crossing = null;
            for (let t0 = t.instantUtc; t0 + 60000 <= t.instantUtc + span; t0 += 60000) {
                if (fd(t0) < 0 && fd(t0 + 60000) >= 0) {
                    let lo = t0, hi = t0 + 60000;
                    while (hi - lo > 1000) { const m = Math.floor((lo + hi) / 2);
                        if (fd(m) < 0) lo = m; else hi = m; }
                    crossing = Math.round((lo + hi) / 2);
                    break;
                }
            }
            const dayWant = crossing === null ? null
                : Math.round(dayNumber(NovaAstro.localParts(crossing, loc.timezone))
                    - dayNumber(NovaAstro.localParts(t.instantUtc, loc.timezone)));
            out.push({
                night: night, site: loc.timezone, name: f.name, lon: f.lon,
                moduleUtc: f.nextUtcMs, moduleDay: f.nextHmDay,
                crossing: crossing, dayWant: dayWant,
                fdAtCrossing: crossing === null ? null : fd(crossing),
                fdAtModule: f.nextUtcMs === null ? null : fd(f.nextUtcMs),
            });
        }
    }
}
return out;
"""


def test_central_meridian_passages_are_the_next_crossing(tmp_path):
    """For a handful of features: the first time after the card's instant when the engine's
    own cm reaches 360 - lon, found independently on a 1-minute scan and bisected, is
    within a minute of the card's answer; and nextHmDay is the local-date difference."""
    cases = _run(tmp_path, _PASSAGES, nights=list(NIGHTS[:2]), sites=list(SITES), count=4)
    assert cases, "no passages were checked"
    for c in cases:
        where = f"{c['night']} @ {c['site']} {c['name']} (lon {c['lon']})"
        assert c["crossing"] is not None, f"{where}: the engine never crosses the target cm"
        assert c["moduleUtc"] is not None, f"{where}: the card returned no passage"
        assert abs(c["fdAtCrossing"]) < 0.02, (
            f"{where}: the sampled crossing is not on the meridian (fd = {c['fdAtCrossing']})")
        # The card's own answer is on the meridian too, and close to the independent one.
        assert abs(c["fdAtModule"]) < 0.6, (
            f"{where}: the card's passage is not on the meridian (fd = {c['fdAtModule']})")
        gap = abs(c["moduleUtc"] - c["crossing"])
        # The card bisects to a minute; the independent scan is bisected to a second, so
        # the two agree to within a minute plus the card's rounding.
        assert gap <= 90000, (
            f"{where}: the card says {c['moduleUtc']}, the independent crossing is "
            f"{c['crossing']} — {gap} ms apart")
        assert c["moduleDay"] == c["dayWant"], (
            f"{where}: nextHmDay {c['moduleDay']} is not the local-date difference "
            f"{c['dayWant']}")


# --- the feature list --------------------------------------------------------------------

TIER_ONE = {"Syrtis Major", "Mare Acidalium", "Mare Sirenum", "Mare Erythraeum", "Solis Lacus",
            "Sinus Meridiani", "Mare Cimmerium", "Utopia", "Hellas", "Elysium"}


def test_the_feature_list_is_albedo_only_and_tiered(tmp_path):
    """15 to 25 albedo features -- what an amateur telescope or camera shows -- the ten an
    imager identifies first in tier 1, other classical markings in tier 2, and exactly the
    generator's own list (tools/gen_mars_features.py, read without running it)."""
    got = _run(tmp_path, "return NovaMarsFeatures.FEATURES;")
    assert 15 <= len(got) <= 25
    assert {f["type"] for f in got} == {"albedo"}
    assert {f["tier"] for f in got} == {1, 2}
    assert {f["name"] for f in got if f["tier"] == 1} == TIER_ONE
    assert "Nix Olympica" in {f["name"] for f in got if f["tier"] == 2}
    assert len({f["name"] for f in got}) == len(got)
    assert all(-90 <= f["lat"] <= 90 and -180 <= f["lon"] <= 180 for f in got)
    import ast
    tree = ast.parse((REPO / "tools" / "gen_mars_features.py").read_text())
    selection = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                     and getattr(node.targets[0], "id", None) == "SELECTION")
    assert ast.literal_eval(selection) == {f["name"]: f["tier"] for f in got}


# --- the polar caps ----------------------------------------------------------------------

_CAPS = r"""
const g = de => ({ polePA: 20, sunPA: 250, phaseAngleDeg: 30, de: de, cm: 0 });
const pts = d => d.replace(/[MLZ]/g, ' ').trim().split(/\s+/).map(Number)
    .reduce((a, v, i) => (i % 2 ? a[a.length - 1].push(v) : a.push([v]), a), []);
const out = {};
for (const de of [20, -20, 3, -3, 0, 6, -6]) {
    out[de] = {};
    for (const mode of NovaSolarMars.VIEWS) {
        const d = NovaSolarMars.diagram(g(de), mode, []);
        out[de][mode] = { pole: d.pole, caps: d.caps.map(c => ({ pole: c.pole, pts: pts(c.d) })) };
    }
}
return { out: out, both: NovaSolarMars.CAP_BOTH_DE, size: NovaSolarMars.CAP_DEG };
"""


def test_the_polar_cap_tilted_towards_us_is_drawn(tmp_path):
    """North cap when De > 0, south when De < 0, both near 0; a fixed size, on the disc,
    around its own pole, turning with the view."""
    got = _run(tmp_path, _CAPS)
    assert got["size"] == 12 and got["both"] == 5
    want = {"20": ["north"], "-20": ["south"], "6": ["north"], "-6": ["south"],
            "3": ["north", "south"], "-3": ["north", "south"], "0": ["north", "south"]}
    for de, views in got["out"].items():
        for mode, d in views.items():
            assert [c["pole"] for c in d["caps"]] == want[de], (de, mode)
            for c in d["caps"]:
                assert len(c["pts"]) == 36 and all(len(p) == 2 for p in c["pts"])
                assert all(math.hypot(*p) <= 1 + 1e-6 for p in c["pts"]), "on the disc"
                cx = sum(p[0] for p in c["pts"]) / 36
                cy = sum(p[1] for p in c["pts"]) / 36
                sign = 1 if c["pole"] == "north" else -1
                # Its centre lies towards its own pole
                assert cx * d["pole"]["x"] * sign + cy * d["pole"]["y"] * sign > 0.5, (de, mode, c["pole"])
    # At De 20 the whole north cap faces us, its far edge 90 - 20 + 12 = 82 deg from the
    # centre of the disc: none of it is laid on the limb
    north = got["out"]["20"]["normal"]["caps"][0]["pts"]
    assert max(math.hypot(*p) for p in north) == pytest.approx(math.sin(math.radians(82)), abs=1e-4)


# --- the best time tonight ---------------------------------------------------------------

_BEST = r"""
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        const opts = { inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null };
        const t = NovaSolarMars.tonight(opts);
        const site = NovaSolarRows._night.site(opts.inputs);
        const win = NovaSolarRows._night.nightWindow(t.date, site, NovaAstro, NovaDashboardRows._wall, null);
        // Independently: every minute of the night with Mars at or above the threshold
        let best = null;
        const f = t.top[0];
        for (let u = win.start; f && u <= win.end; u += 60000) {
            if (NovaPlanets.altAz('mars', u, loc.lat, loc.lon).alt < site.threshold) continue;
            const d = Math.abs(((f.lon + NovaMars.geometry(u).cm + 540) % 360) - 180);
            if (!best || d < best.d) best = { u: u, d: d };
        }
        out.push({ night: night, site: loc.timezone, best: t.best, top: f ? f.name : null,
                   want: best, muted: t.muted, step: NovaSolarMars.BEST_STEP_MIN,
                   alt: t.best ? NovaPlanets.altAz('mars', t.best.utcMs, loc.lat, loc.lon).alt : null,
                   threshold: site.threshold,
                   hm: t.best ? NovaAstro.formatHM(t.best.utcMs, loc.timezone) : null });
    }
}
return out;
"""


def test_best_tonight_is_the_top_feature_nearest_the_meridian(tmp_path):
    cases = _run(tmp_path, _BEST, nights=list(NIGHTS), sites=list(SITES))
    assert any(c["best"] for c in cases) and any(c["best"] is None for c in cases)
    for c in cases:
        where = f"{c['night']} @ {c['site']}"
        if c["want"] is None:
            assert c["best"] is None, f"{where}: Mars never reaches the threshold"
            continue
        b = c["best"]
        assert b["name"] == c["top"] and b["hm"] == c["hm"], where
        assert c["alt"] >= c["threshold"], f"{where}: Mars below the threshold then"
        # Sampled every BEST_STEP_MIN minutes: within one step's turn of the planet
        # (about 1.2 deg) of the minute-by-minute answer
        assert b["cmDist"] <= c["want"]["d"] + 360 / 24.6 / 60 * c["step"], where
