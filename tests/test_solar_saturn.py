"""Checks for the Saturn card (static/js/nova_solar_saturn.js), in node with the real engines.

The scripts load the way the Solar System page loads them: plain scripts in one vm context,
in the page's order, so nova_solar_saturn.js finds NovaAstro, NovaPlanets, NovaSaturn,
NovaDashboardRows and NovaSolarRows as globals. Nothing here needs a network, a database or
Flask.

Four things are pinned, all offline:

  * the ring plane's two vectors. `sunVector` and `ringNormal` are built from geometry()'s
    public angles -- sunPA and the phase angle, polePA and B -- and their dot product has to
    come out as sin B' by the spherical law of cosines, which is a check on both of them at
    once and is what the J2000 -> ecliptic-of-date precession in
    NovaSaturn.geometry()'s deltaU cannot feed back into.

  * the shadow outline, against an independent ray trace of the same geometry: for every
    point of the visible hemisphere, does the Sun's ray through it cross the ring plane
    between the B ring's inner edge and the A ring's outer edge? The outline is the module's
    own analytic construction, the trace is the definition it is derived from.

  * the moons, against the engine's own sky-frame positions rotated back into the figure the
    documented way, and the season's closest approaches against a scan an hour at a time.

  * tonight()'s own invariants: the ring edges come from ch. 45's factors of a, radiusRs is
    a / the disc's diameter, the sparkline is geometry()'s own B, and the night is memoised.

Saturn's shared engine (nova_saturn.js) is validated in nova-ios against JPL Horizons and
Meeus's own worked examples (tools/test_saturn.cjs, tools/gen_saturn_fixture.py).
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
              "nova_solar_rows.js", "nova_saturn.js", "nova_solar_saturn.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

LONDON = {"lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London",
          "altitude_threshold": 30, "sampling_interval": 15}
SYDNEY = {"lat": -33.8688, "lon": 151.2093, "timezone": "Australia/Sydney",
          "altitude_threshold": 30, "sampling_interval": 15}
SITES = (LONDON, SYDNEY)

# Nights across the ring cycle: the rings face us south from the March 2025 crossing to the
# one in October 2038, so 2024 and the end of 2038 are the north face, 2032 the widest south
# one, and 2025-05-06 and 2025-06-01 are within a degree of the plane the Sun is in (B' = 0),
# where the shadow comes to nothing. Every night is before the end of 2039, the limit of the
# engine's reference.
NIGHTS = ("2024-06-01", "2025-05-06", "2025-06-01", "2026-11-02", "2032-06-01",
          "2038-06-01", "2038-12-01")

# The night the card was written against: Titan does not cross the disc this season and the
# small moons do.
CARD_NIGHT = "2026-11-02"

# ch. 45's factors of a, the major axis of the outer edge of the outer ring (nova_saturn.js's
# RING_EDGES, from the note under ring_parameters in the chapter).
EDGES = {"duskyInner": 0.5486, "innerInner": 0.6650, "innerOuter": 0.8599,
         "outerInner": 0.8801, "outerOuter": 1.0}

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


# --- tonight's geometry -------------------------------------------------------------------

_GEOMETRY = r"""
const DEG = Math.PI / 180;
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        const opts = { inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null };
        const t = NovaSolarSaturn.tonight(opts);
        const g = t.geometry, r = t.rings;
        out.push({
            night: night, site: loc.timezone,
            B: g.B, Bprime: g.Bprime, aArcsec: g.aArcsec, diameter: t.diameterArcsec,
            radiusRs: r.radiusRs, edges: r.edges,
            face: r.face, sunFace: r.sunFace, sameFace: r.sameFace,
            trend: r.trend, B30: NovaSaturn.geometry(t.instantUtc + 30 * 86400000).B,
            months: r.months.length,
            stepMs: r.months.length > 1 ? r.months[1].utcMs - r.months[0].utcMs : null,
            firstB: r.months[0].B, monthsB: r.months.map(m => m.B),
            monthsBwant: r.months.map(m => NovaSaturn.geometry(m.utcMs).B),
            consts: { strip: NovaSolarSaturn.STRIP_RS, months: NovaSolarSaturn.MONTHS_AHEAD,
                      views: NovaSolarSaturn.VIEWS, labelled: NovaSolarSaturn.LABELLED,
                      mags: NovaSolarSaturn.MOON_MAG, moons: NovaSaturn.MOONS,
                      key45: NovaSaturn.RING_EDGES },
            muted: t.muted, maxAlt: t.maxAlt, threshold: t.threshold, hm: t.hm,
            instantUtc: t.instantUtc,
        });
    }
}
return out;
"""


def test_the_rings_are_tonight_s_own_ring_plane(tmp_path):
    """radiusRs is a / the disc's diameter, the edges are ch. 45's factors of it, the faces
    and the trend follow the signs of B and B', and the sparkline is the engine's own B every
    15 days for two years."""
    cases = _run(tmp_path, _GEOMETRY, nights=list(NIGHTS), sites=list(SITES))
    assert len(cases) == len(NIGHTS) * len(SITES)
    consts = cases[0]["consts"]
    assert consts["key45"] == EDGES, "the engine's ch. 45 factors moved"
    assert consts["views"] == ["normal", "diagonal", "inverted"]
    assert consts["labelled"] == ["Titan", "Rhea", "Tethys", "Dione", "Iapetus"]
    assert consts["strip"] == 14 and consts["months"] == 24
    assert sorted(consts["mags"]) == sorted(consts["moons"]), "one magnitude per moon"
    assert consts["mags"]["Iapetus"] == pytest.approx((10.1 + 11.9) / 2, abs=1e-12)

    for c in cases:
        where = f"{c['night']} @ {c['site']}"
        assert c["radiusRs"] == pytest.approx(c["aArcsec"] / c["diameter"], rel=1e-12), where
        # NovaSaturn's a is the outer A ring's radius in arcsec over the disc's radius, so
        # the outer edge's radius comes out at 375.35 / 165.6 = 2.2666 Saturn radii
        assert c["radiusRs"] == pytest.approx(2.2666, abs=2e-4), where
        want = [(EDGES["duskyInner"], EDGES["innerInner"]), (EDGES["innerInner"], EDGES["innerOuter"]),
                (EDGES["innerOuter"], EDGES["outerInner"]), (EDGES["outerInner"], EDGES["outerOuter"])]
        assert [e["key"] for e in c["edges"]] == ["c", "b", "division", "a"], where
        for e, (lo, hi) in zip(c["edges"], want):
            assert e["inner"] == pytest.approx(c["radiusRs"] * lo, rel=1e-12), (where, e["key"])
            assert e["outer"] == pytest.approx(c["radiusRs"] * hi, rel=1e-12), (where, e["key"])
        assert c["face"] == ("south" if c["B"] < 0 else "north"), where
        assert c["sunFace"] == ("south" if c["Bprime"] < 0 else "north"), where
        assert c["sameFace"] == ((c["B"] < 0) == (c["Bprime"] < 0)), where
        assert c["trend"] == ("opening" if abs(c["B30"]) > abs(c["B"]) else "closing"), where
        assert c["months"] == int(24 * 30 / 15) + 1, where
        assert c["stepMs"] == 15 * 86400000, where
        assert c["firstB"] == c["B"], where
        assert c["monthsB"] == pytest.approx(c["monthsBwant"], rel=0, abs=0), where
        assert c["muted"] == (c["maxAlt"] < c["threshold"]), where

    # The ring cycle is in the sample: B changes sign and comes back near zero
    south = [c for c in cases if c["B"] < 0]
    north = [c for c in cases if c["B"] > 0]
    assert south and north, "the nights chosen do not span both faces of the rings"
    assert min(abs(c["B"]) for c in cases) < 3.0, "no night near the rings edge-on"


# --- the ring plane's two vectors ---------------------------------------------------------

_BPRIME = r"""
const DEG = Math.PI / 180;
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
const norm = a => Math.sqrt(dot(a, a));
const out = [];
for (const night of input.nights) {
    const utcMs = Date.parse(night + 'T20:00:00Z');
    const g = NovaSaturn.geometry(utcMs);
    const S = NovaSolarSaturn._sunVector(g), P = NovaSolarSaturn._ringNormal(g);
    out.push({
        night: night,
        err: dot(S, P) - Math.sin(g.Bprime * DEG),
        sinBprime: Math.sin(g.Bprime * DEG), sph: dot(S, P),
        normS: norm(S) - 1, normP: norm(P) - 1,
        // The two vectors carry the angles they were built from, and nothing else
        sz: S[2] - Math.cos(g.phaseAngleDeg * DEG),
        pz: P[2] - Math.sin(g.B * DEG),
        // Independently: P from B and polePA as the position angle definition has it
        px: P[0] - Math.cos(g.B * DEG) * Math.sin(g.polePA * DEG),
        py: P[1] + Math.cos(g.B * DEG) * Math.cos(g.polePA * DEG),
    });
}
return out;
"""


def test_the_sun_and_the_ring_normal_reproduce_Bprime(tmp_path):
    """Their dot product is sin B' -- the spherical law of cosines, sin B cos ph +
    cos B sin ph cos(sunPA - polePA) -- and each is a unit vector carrying its own angles.
    The check is tighter than Bprime itself is meaningful: 1e-12."""
    for c in _run(tmp_path, _BPRIME, nights=list(NIGHTS)):
        where = c["night"]
        assert abs(c["normS"]) < 1e-12 and abs(c["normP"]) < 1e-12, where
        assert abs(c["sz"]) < 1e-12, where
        assert abs(c["pz"]) < 1e-12 and abs(c["px"]) < 1e-12 and abs(c["py"]) < 1e-12, where
        assert abs(c["err"]) < 1e-12, (
            f"sin B' from the two vectors is off the engine's {where}: "
            f"{c['sph']} vs {c['sinBprime']} ({c['err']:.3e})")


# --- the shadow outline -------------------------------------------------------------------

# The outline is the module's own analytic construction; the trace below is the definition it
# is derived from, evaluated point by point over the whole visible hemisphere. The polygon's
# edge is a chord between two of its 96 steps, so the two disagree in a band a few steps wide
# along the terminator ends of the arc, where the outline also folds over itself by a few
# thousandths of a Saturn radius (documented in ringShadow). The measured worst disagreement
# is 0.5 per cent of the sampled area; 1.5 per cent leaves room for that and still catches a
# wrong edge, a missing band or a mirrored one (each of which moves whole per cent).
SHADOW_TOLERANCE = 0.015

_SHADOW = r"""
const DEG = Math.PI / 180;
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
// The path back to its points: 'M x y L x y ... Z', all rounded to five decimals
const polyOf = d => d.replace(/[MLZ]/g, ' ').trim().split(/\s+/).map(Number)
    .reduce((a, v, i) => (i % 2 ? a[a.length - 1].push(v) : a.push([v]), a), []);
const inPoly = (P, x, y) => {
    let c = false;
    for (let i = 0, j = P.length - 1; i < P.length; j = i++) {
        const xi = P[i][0], yi = P[i][1], xj = P[j][0], yj = P[j][1];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c;
    }
    return c;
};
// The view, as the other cards' one line has it: SVG is (sx x, sy y)
const toSvg = (mode, x, y) => [mode === 'normal' ? x : -x, mode === 'inverted' ? y : -y];

const out = [];
for (const night of input.nights) {
    const opts = { inputs: { location: input.site }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null };
    const t = NovaSolarSaturn.tonight(opts);
    const g = t.geometry, rb = t.rings.radiusRs;
    const pa = g.polePA * DEG, b = g.B * DEG;
    // The sky frame's own axes, rebuilt from the position angle definition: with x east,
    // y south and z towards the Earth, the direction at position angle P is (sin P, -cos P)
    const east = [-Math.cos(pa), -Math.sin(pa), 0], pole = [Math.sin(pa), -Math.cos(pa), 0];
    const zhat = cross(east, pole);
    // The Sun's direction, rebuilt from its own position angle and the phase angle
    const ph = g.phaseAngleDeg * DEG, spa = g.sunPA * DEG;
    const S = [Math.sin(spa) * Math.sin(ph), -Math.cos(spa) * Math.sin(ph), Math.cos(ph)];
    const P = [Math.cos(b) * Math.sin(pa), -Math.cos(b) * Math.cos(pa), Math.sin(b)];
    const rIn = rb * 0.6650, rOut = rb * 1.0;
    const dS = dot(S, P);
    // The definition: G is shadowed when the Sun's ray through it crosses the ring plane
    // between the two edges, the crossing lying between the Sun and G -- the ring plane
    // cuts the Sun-Saturn line at the planet's centre, so a ring element that shadows G is
    // one the ray meets on its way in, not one beyond it.
    const traced = G => {
        const q = -dot(G, P) / dS;
        if (q <= 0) return false;
        const Q = [G[0] + q * S[0], G[1] + q * S[1], G[2] + q * S[2]];
        const rho = Math.sqrt(dot(Q, Q));
        return rho >= rIn && rho <= rOut;
    };

    for (const mode of NovaSolarSaturn.VIEWS) {
        const d = NovaSolarSaturn.diagram(g, mode, t.moons, rb);
        const path = d.shadow ? polyOf(d.shadow) : null;
        // A point of the visible hemisphere at (u, v) of the figure: its projection is
        // (u, v) in Saturn's own frame, so the drawn one is view(u, v)
        let want = 0, got = 0, bad = 0, inner = 0, offDisc = 0;
        for (let i = 0; i < input.rings; i++) {
            const rho = Math.sqrt((i + 0.5) / input.rings);
            for (let j = 0; j < input.angles; j++) {
                const th = 2 * Math.PI * j / input.angles;
                const u = rho * Math.cos(th), v = rho * Math.sin(th);
                const G = [Math.sqrt(Math.max(0, 1 - rho * rho)) * zhat[0] + u * east[0] + v * pole[0],
                           Math.sqrt(Math.max(0, 1 - rho * rho)) * zhat[1] + u * east[1] + v * pole[1],
                           Math.sqrt(Math.max(0, 1 - rho * rho)) * zhat[2] + u * east[2] + v * pole[2]];
                const a = traced(G), c = path ? inPoly(path, ...toSvg(mode, u, v)) : false;
                if (a) want++;
                if (c) got++;
                if (a !== c) bad++;
            }
        }
        const n = input.rings * input.angles;
        if (path) {
            // The path is written to five decimals, so a point on the limb can read a hair
            // outside it: the outline is on the globe, and 1e-5 of a radius is 0.0004 of a
            // pixel at the page's scale.
            for (const p of path) if (Math.hypot(p[0], p[1]) > 1 + 1e-5) offDisc++;
        }
        out.push({ night: night, mode: mode, null: !path, n: n, want: want / n, got: got / n,
                   bad: bad / n, offDisc: offDisc, B: g.B, Bprime: g.Bprime,
                   radiusRs: rb, dx: d.north, inside: inner });
    }
}
return out;
"""


def test_the_shadow_outline_is_the_ray_trace(tmp_path):
    """Every point of the visible hemisphere, against the definition the outline is derived
    from, in all three views: the drawn band, where it exists, is the set of globe points
    whose Sun ray meets the rings."""
    cases = _run(tmp_path, _SHADOW, nights=list(NIGHTS), site=LONDON, rings=80, angles=360)
    assert len(cases) == len(NIGHTS) * 3
    checked = 0
    for c in cases:
        where = f"{c['night']}·{c['mode']}"
        if c["null"]:
            # The rings edge-on to the Sun: there is no band to draw. The trace is allowed
            # the sliver its own sampling puts within the flattened band.
            assert c["want"] < 0.005, (
                f"no shadow is drawn at {where} but the trace finds {c['want']:.4f} of the "
                "visible hemisphere in one")
            continue
        checked += 1
        assert c["offDisc"] == 0, f"the outline at {where} leaves the disc"
        assert c["want"] > 0.001, f"a shadow is drawn at {where} but the trace finds none"
        assert c["bad"] <= SHADOW_TOLERANCE, (
            f"the outline and the trace disagree over {c['bad']:.4f} of the disc at {where} "
            f"(B = {c['B']:.3f}, B' = {c['Bprime']:.3f}): drawn {c['got']:.4f}, traced "
            f"{c['want']:.4f}")
    assert checked >= 6, "too few nights with a shadow to be checking anything"


_SHADOW_UNIT = r"""
const DEG = Math.PI / 180;
const g = (B, Bprime, polePA, sunPA, ph) => ({ B: B, Bprime: Bprime, polePA: polePA,
    sunPA: sunPA, phaseAngleDeg: ph });
// The angles and Bprime have to agree: ringShadow() takes both, and what it makes of the
// angles is D = dot(S, P) = cos B sin(phase) cos(sunPA - polePA) + cos(phase) sin B, which
// is sin Bprime and nothing else. So each case below is built by solving for the sunPA that
// turns its own B, phase angle and Bprime into that D.
const sunFor = (B, Bprime, ph, polePA) => {
    const D = Math.sin(Bprime * DEG);
    const c = (D - Math.cos(ph * DEG) * Math.sin(B * DEG)) / (Math.cos(B * DEG) * Math.sin(ph * DEG));
    return polePA + Math.acos(Math.max(-1, Math.min(1, c))) / DEG;
};
const at = (B, Bprime, ph) => g(B, Bprime, 30, sunFor(B, Bprime, ph, 30), ph);
const view = NovaSolarSaturn._viewOf('normal');
const polyOf = d => d.replace(/[MLZ]/g, ' ').trim().split(/\s+/).map(Number)
    .reduce((a, v, i) => (i % 2 ? a[a.length - 1].push(v) : a.push([v]), a), []);
const real = at(-20, -18, 5);                 // the rings tipped 20 degrees, the Sun 18
const d = NovaSolarSaturn._ringShadow(real, 2.2666, view);
return {
    real: { null: !d, max: d ? Math.max(...polyOf(d).map(p => Math.hypot(p[0], p[1]))) : null },
    edgeOn: NovaSolarSaturn._ringShadow(at(-20, 0, 45), 2.2666, view),
    tooSmall: NovaSolarSaturn._ringShadow(real, 0.9, view),
    tooOpen: NovaSolarSaturn._ringShadow(at(0, 60, 60), 2.2666, view),
};
"""


def test_the_shadow_stops_where_it_cannot_be_drawn(tmp_path):
    """The Sun in the ring plane, no ring outside the globe, and the band too near the Sun's
    ring plane to reach the globe: the three cases ringShadow() answers null for, and a
    plausible one that stays on the disc."""
    got = _run(tmp_path, _SHADOW_UNIT)
    assert got["real"]["null"] is False
    # The path is written to five decimals, so a point at the limb can read a hair outside it
    assert got["real"]["max"] <= 1 + 1e-5, "the outline reaches past the limb"
    assert got["edgeOn"] is None and got["tooSmall"] is None and got["tooOpen"] is None


# --- the moons ----------------------------------------------------------------------------

_MOONS = r"""
const DEG = Math.PI / 180;
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        const opts = { inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null };
        const t = NovaSolarSaturn.tonight(opts);
        const g = t.geometry, rb = t.rings.radiusRs;
        const pa = g.polePA * DEG;
        const east = { x: -Math.cos(pa), y: -Math.sin(pa) }, pole = { x: Math.sin(pa), y: -Math.cos(pa) };
        const raw = NovaSaturn.moons(t.instantUtc);
        // The engine's own list, by name: the figure draws a subset of it (DRAWN, which
        // leaves Hyperion out), so the two cannot be zipped by index
        const rawByName = {};
        raw.forEach(m => { rawByName[m.name] = m; });
        const toSvg = (mode, x, y) => [mode === 'normal' ? x : -x, mode === 'inverted' ? y : -y];
        const rows = t.moons.map(m => ({ name: m.name, labelled: m.labelled, side: m.side,
            inFront: m.inFront, onDisc: m.onDisc, behindDisc: m.behindDisc, r: m.r, dist: m.dist,
            z: m.z, mag: m.mag, brightness: m.brightness,
            rawX: rawByName[m.name].x, rawY: rawByName[m.name].y, rawName: rawByName[m.name].name }));
        const views = {};
        for (const mode of NovaSolarSaturn.VIEWS) {
            const d = NovaSolarSaturn.diagram(g, mode, t.moons, rb);
            views[mode] = d.moons.map(m => {
                const own = rawByName[m.name];
                // The figure's own rotation: place() is the documented one, the sky frame's
                // directions at polePA and polePA - 90 written out
                const px = own.x * east.x + own.y * east.y;
                const py = own.x * pole.x + own.y * pole.y;
                const want = toSvg(mode, px, py);
                const over = Math.abs(want[0]);
                const off = over > NovaSolarSaturn.STRIP_RS;
                // The pull-back is along the moon's own line from the centre, and it is the
                // drawn place that is pulled: the rotation is already in `want`.
                const pulled = off ? [(want[0] < 0 ? -1 : 1) * NovaSolarSaturn.STRIP_RS,
                                      want[1] * (NovaSolarSaturn.STRIP_RS / over)] : want;
                return { name: m.name, x: m.x, y: m.y, off: m.off, inside: m.inside,
                         err: Math.hypot(m.x - want[0], m.y - want[1]),
                         pulledErr: Math.hypot(m.x - pulled[0], m.y - pulled[1]),
                         // How far the drawn place is off the line from the centre, as a
                         // distance: the cross product alone scales with the figure's own size
                         // (a moon drawn at x = 14 and y = 28 has coordinates in the hundreds)
                         straight: off ? Math.abs(m.x * want[1] - m.y * want[0]) /
                                         Math.hypot(want[0], want[1]) : 0 };
            });
        }
        out.push({ night: night, site: loc.timezone, rows: rows, views: views,
                   strip: NovaSolarSaturn.STRIP_RS, drawn: NovaSolarSaturn.DRAWN,
                   // Celestial north, (0, -1) in the sky frame, in the figure's own frame
                   north: views.normal && NovaSolarSaturn.diagram(g, 'normal', [], rb).north,
                   northErr: (() => {
                       const d = NovaSolarSaturn.diagram(g, 'normal', [], rb).north;
                       const w = toSvg('normal', 0 * east.x + (-1) * east.y, 0 * pole.x + (-1) * pole.y);
                       return Math.hypot(d.x - w[0], d.y - w[1]);
                   })(),
                 });
    }
}
return out;
"""


def test_the_moons_are_where_the_engine_puts_them(tmp_path):
    """Each moon's drawn place is the engine's own sky-frame position rotated into the figure
    the documented way, in all three views; a moon past the edge of the strip is pulled back
    to it along its own line; the entry's own states follow z and r; and the figure draws
    the seven of the engine's eight the card's figure is for, Hyperion left out."""
    checked_off = 0
    for c in _run(tmp_path, _MOONS, nights=list(NIGHTS), sites=list(SITES)):
        where = f"{c['night']} @ {c['site']}"
        names = [m["name"] for m in c["rows"]]
        assert names == ["Mimas", "Enceladus", "Tethys", "Dione", "Rhea", "Titan",
                         "Hyperion", "Iapetus"], where
        assert [m["rawName"] for m in c["rows"]] == names, where
        # The diagram's own list is the engine's order with Hyperion left out -- at 24 radii
        # out it is past the strip's edge every night of the cycle -- and it is what the card
        # filters by, so the two have to keep agreeing
        assert c["drawn"] == [n for n in names if n != "Hyperion"], where
        assert all(m["labelled"] == (m["name"] in ["Titan", "Rhea", "Tethys", "Dione", "Iapetus"])
                   for m in c["rows"]), where
        for m in c["rows"]:
            tag = f"{where} {m['name']}"
            assert m["side"] == ("west" if m["rawX"] < 0 else "east"), tag
            assert m["inFront"] == (m["z"] > 0), tag
            assert m["onDisc"] == (m["z"] > 0 and m["r"] < 1), tag
            assert m["behindDisc"] == (not (m["z"] > 0) and m["r"] < 1), tag
            assert m["dist"] == pytest.approx(math.hypot(m["rawX"], m["rawY"], m["z"]), abs=1e-12), tag
            assert m["r"] == pytest.approx(math.hypot(m["rawX"], m["rawY"]), abs=1e-12), tag
            assert m["dist"] >= m["r"] - 1e-12, tag
            assert m["mag"] > 0, tag
            assert m["brightness"] == (None if m["name"] != "Iapetus"
                                       else "bright" if m["rawX"] < 0 else "faint"), tag
        for mode, drawn in c["views"].items():
            assert [m["name"] for m in drawn] == c["drawn"], where
            for m in drawn:
                tag = f"{where}·{mode} {m['name']}"
                # Where it is drawn is where the engine puts it, or that place pulled back
                # to the edge of the strip along its own line from the centre
                assert m["pulledErr"] < 1e-6, (
                    f"{tag}: drawn at ({m['x']}, {m['y']}), the engine's own place is "
                    f"{m['pulledErr']:.3e} away")
                assert m["inside"] == (m["off"] is None), tag
                if m["off"]:
                    checked_off += 1
                    assert m["err"] > 1, f"{tag}: not actually past the edge of the strip"
                    assert abs(m["x"]) == pytest.approx(c["strip"], abs=1e-6), tag
                    assert m["straight"] < 1e-5, tag   # still on its own line from the centre
                else:
                    assert m["err"] < 1e-6, tag
        # Celestial north, the sky frame's (0, -1), turned the same way the moons are
        assert c["northErr"] < 1e-6, f"{where}: celestial north is off its own rotation"
        assert math.hypot(c["north"]["x"], c["north"]["y"]) == pytest.approx(1, abs=1e-6), where
    # Over the nights and sites as a whole: the pull-back is what the far moons' entries
    # show, and it has to have been exercised -- though no longer on every night, now that
    # Hyperion, the moon that was always past the edge, is not drawn
    assert checked_off > 0, "no moon, on any of these nights, was past the edge of the strip"


# --- the season's transits ----------------------------------------------------------------

# The module walks a day at a time, then the best day five minutes at a time, and closes on
# the best sample with a golden section. The scan below is a different rule over the same
# engine: a quarter of an hour at a time over the season, then a golden section over the
# quarter of an hour around its own best sample, which lands on the approach itself.
_SEASON = r"""
const DAY = 86400000, STEP = 15 * 60000;
// A golden section over a window one step wide, where the distance has one minimum
const refine = (f, lo, hi) => {
    let a = lo, b = hi;
    let m1 = b - (b - a) * 0.618034, m2 = a + (b - a) * 0.618034;
    let f1 = f(m1), f2 = f(m2);
    let best = f1 < f2 ? { t: m1, r: f1 } : { t: m2, r: f2 };
    for (let n = 0; n < 60 && b - a > 1000; n++) {
        if (f1 < f2) { b = m2; m2 = m1; f2 = f1; m1 = b - (b - a) * 0.618034; f1 = f(m1); }
        else { a = m1; m1 = m2; f1 = f2; m2 = a + (b - a) * 0.618034; f2 = f(m2); }
        const c = f1 < f2 ? { t: m1, r: f1 } : { t: m2, r: f2 };
        if (c.r < best.r) best = c;
    }
    return best;
};
const out = [];
for (const night of input.nights) {
    const opts = { inputs: { location: input.site }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null };
    const t = NovaSolarSaturn.tonight(opts);
    const start = t.instantUtc;
    const best = {};
    NovaSaturn.MOONS.forEach(n => { best[n] = { r: Infinity, t: start }; });
    for (let u = start; u <= start + 365 * DAY; u += STEP) {
        for (const m of NovaSaturn.moons(u)) if (m.r < best[m.name].r) best[m.name] = { r: m.r, t: u };
    }
    const scan = NovaSaturn.MOONS.map(n => {
        const b = best[n];
        const f = u => NovaSaturn.moon(u, n).r;
        const ref = refine(f, b.t - STEP, b.t + STEP);
        return { name: n, r: ref.r, t: ref.t, coarse: b.r };
    });
    out.push({
        night: night,
        module: t.transits.crossing, titan: t.transits.titan,
        next: t.transits.titanNext, scan: scan,
        // The card's own instant, against the distance either side of it: the approach it
        // reports has to be a minimum, not merely a small sample.
        up: t.transits.crossing.map(x => ({
            name: x.name,
            side: Math.min(NovaSaturn.moon(x.utcMs - 60000, x.name).r,
                           NovaSaturn.moon(x.utcMs + 60000, x.name).r) - x.closestRs,
        })),
        start: start,
    });
}
return out;
"""


def test_the_season_transits_are_the_closest_the_moons_come(tmp_path):
    """An independent scan of the same season, over the engine: the module's closest
    approaches and the moons it lists are the scan's, and the ones clear of the disc are
    left out. Where a moon crosses the line of sight the scan only samples the crossing and
    the module locates it, so there the module's number is allowed to be the deeper of the
    two — and has to be a minimum of the distance either side of it, which the scan's is
    not."""
    cases = _run(tmp_path, _SEASON, nights=list(NIGHTS[:4]) + [CARD_NIGHT], site=LONDON)
    assert cases
    for c in cases:
        where = c["night"]
        scan = {s["name"]: s for s in c["scan"]}
        module = {x["name"]: x for x in c["module"]}
        up = {u["name"]: u["side"] for u in c["up"]}
        assert c["module"] == sorted(c["module"], key=lambda x: x["closestRs"]), where
        for name, s in scan.items():
            if s["r"] < 0.99:                     # clearly crossing: the card must say so
                assert name in module, f"{where}: {name} crosses at {s['r']:.4f} but is not listed"
                m = module[name]
                assert m["closestRs"] >= 0, (where, name)
                assert m["closestRs"] <= s["r"] + 1e-3, (
                    f"{where} {name}: the card's closest approach is {m['closestRs']}, the "
                    f"scan's {s['r']}")
                if s["r"] > 0.05:                 # a miss, not a crossing: the two must agree
                    assert m["closestRs"] == pytest.approx(s["r"], abs=1e-3), (
                        f"{where} {name}: the card's closest approach is {m['closestRs']}, "
                        f"the scan's {s['r']}")
                assert up[name] >= 0, (
                    f"{where} {name}: {m['closestRs']} is not a minimum, the distance is "
                    f"{up[name]} smaller a minute away")
                assert c["start"] <= m["utcMs"] <= c["start"] + 365 * 86400000, (where, name)
            if s["r"] > 1.01:                     # clearly clear of the disc: it must not
                assert name not in module, (
                    f"{where}: {name} is listed at {module.get(name, {}).get('closestRs')} "
                    f"but only comes within {s['r']:.4f}")
        # Titan's own answer is the crossing entry, and its season is named only when this
        # one has none
        if "Titan" in module:
            assert c["titan"] == {"closestRs": module["Titan"]["closestRs"],
                                  "utcMs": module["Titan"]["utcMs"]}, where
            assert c["next"] is None, where
        else:
            assert c["titan"] is None, where
    # The night the card was written against: the small moons cross and Titan does not
    card = next(c for c in cases if c["night"] == CARD_NIGHT)
    assert card["titan"] is None and card["next"] is not None
    assert {x["name"] for x in card["module"]} >= {"Mimas", "Enceladus", "Tethys", "Dione"}


def test_next_titan_season_is_the_first_one_after_tonight(tmp_path):
    """Independently, a season at a time from the same instant, an hour at a time: the first
    season in which Titan comes within a Saturn radius of the centre of the disc, and Titan
    at its closest is where the card says. An hour is fine enough to catch any crossing —
    Titan covers 0.17 radii an hour at its fastest across the disc — and the season the scan
    finds is then scanned a minute at a time, because the card locates the crossing and the
    scan can only sample it."""
    c = _run(tmp_path, _SEASON, nights=[CARD_NIGHT], site=LONDON)[0]
    assert c["next"] is not None, "the card's night has no Titan transit and no next season"
    got = _run(tmp_path, r"""
const DAY = 86400000;
const start = NovaSolarSaturn.tonight({ inputs: { location: input.site },
    nowMs: Date.parse(input.night + 'T20:00:00Z'), simDate: null }).instantUtc;
const seasons = [];
for (let y = 0; y < 16; y++) {
    const s = start + y * 365 * DAY;
    let r = Infinity, t = s;
    for (let u = s; u <= s + 365 * DAY; u += 3600000) {
        const m = NovaSaturn.moon(u, 'Titan');
        if (m.r < r) { r = m.r; t = u; }
    }
    seasons.push({ year: y, r: r, t: t });
}
const first = seasons.filter(s => s.r < 1)[0] || null;
let near = null;
if (first) {
    let r = Infinity, t = first.t;
    for (let u = first.t - 2 * DAY; u <= first.t + 2 * DAY; u += 60000) {
        const m = NovaSaturn.moon(u, 'Titan');
        if (m.r < r) { r = m.r; t = u; }
    }
    near = { r: r, t: t };
}
return { seasons: seasons, first: first ? first.year : null, near: near };
""", night=CARD_NIGHT, site=LONDON)
    assert got["first"] is not None, "Titan crosses the disc in none of sixteen seasons"
    want = c["next"]
    # No earlier season has one, and the card's is the scan's: the same crossing, to within
    # the minute scan's own step
    assert all(s["r"] > 1 for s in got["seasons"] if s["year"] < got["first"])
    assert abs(want["utcMs"] - got["near"]["t"]) <= 3600000, (
        f"the card's next season is {want['utcMs']}, the scan's is {got['near']['t']}")
    assert want["closestRs"] == pytest.approx(got["near"]["r"], abs=5e-3)


# --- tonight's memoisation ----------------------------------------------------------------

_CACHE = r"""
const opts = { inputs: { location: input.site }, nowMs: Date.parse(input.night + 'T20:00:00Z'), simDate: null };
const a = NovaSolarSaturn.tonight(opts);
const b = NovaSolarSaturn.tonight(opts);
const other = { inputs: { location: input.other }, nowMs: opts.nowMs, simDate: null };
const c = NovaSolarSaturn.tonight(other);
const rows = NovaSolarSaturn.calendarRows(opts);
const rows2 = NovaSolarSaturn.calendarRows(opts);
return { same: a === b, other: c !== a,
         rowsSame: rows.rows === rows2.rows, rows: rows.rows.length,
         window: rows.window, monthsAhead: NovaSolarSaturn.MONTHS_AHEAD,
         settled: a.rings.months.length };
"""


def test_tonight_is_memoised_for_the_night(tmp_path):
    """The card is asked for tonight on every control of the tab: the same night and site
    gives the same object back, another site does not."""
    got = _run(tmp_path, _CACHE, night=CARD_NIGHT, site=LONDON, other=SYDNEY)
    assert got["same"] is True and got["other"] is True
    assert got["rowsSame"] is True


# --- the Calendar rows --------------------------------------------------------------------

_CALENDAR = r"""
const DAY = 86400000;
const out = [];
for (const night of input.nights) {
    const opts = { inputs: { location: input.site }, nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null };
    const r = NovaSolarSaturn.calendarRows(opts);
    out.push({
        night: night, window: r.window, ahead: NovaSolarSaturn.MONTHS_AHEAD,
        rows: r.rows.map(x => ({ utcMs: x.utcMs, kind: x.kind, body: x.body, bodies: x.bodies,
            night: x.night, mag: x.mag, diameter: x.diameterArcsec, sep: x.sepDeg,
            other: x.other, moment: x.moment, occultation: x.occultation,
            ringTilt: x.ringTiltDeg, closest: x.closestApproach, muted: x.muted,
            // Independently: is Iapetus's x a local minimum at that instant, and is it west?
            x: NovaSaturn.moon(x.utcMs, 'Iapetus').x,
            day: NovaSaturn.moon(x.utcMs - 2 * DAY, 'Iapetus').x,
            day2: NovaSaturn.moon(x.utcMs + 2 * DAY, 'Iapetus').x,
            r: NovaSaturn.moon(x.utcMs, 'Iapetus').r })),
    });
}
return out;
"""


def test_the_calendar_rows_are_iapetus_s_western_elongations(tmp_path):
    """One row per western elongation in the next 24 months -- Iapetus at its furthest west,
    which is the one night of its 79-day cycle worth a row -- in order, and 79.33 days
    apart, with Saturn's own magnitude and ring tilt."""
    for c in _run(tmp_path, _CALENDAR, nights=list(NIGHTS), site=LONDON):
        where = c["night"]
        assert c["ahead"] == 24, where
        assert c["rows"], f"{where}: no Iapetus row in two years"
        assert [r["utcMs"] for r in c["rows"]] == sorted(r["utcMs"] for r in c["rows"]), where
        for r in c["rows"]:
            tag = f"{where} {r['night']}"
            assert r["kind"] == "iapetusWest" and r["body"] == "saturn", tag
            assert r["bodies"] == ["saturn"], tag
            assert r["sep"] is None and r["other"] is None and r["moment"] is None, tag
            assert r["occultation"] is False and r["closest"] is None, tag
            # Saturn's own magnitude and disc, which the row carries for the calendar. Its
            # magnitude is negative around opposition (-0.01 on 2032-06-11) and about +1 at
            # conjunction, so the bound is the range, not the sign.
            assert -1 < r["mag"] < 2 and r["diameter"] > 0, tag
            assert r["x"] < 0, f"{tag}: Iapetus is not west"
            assert r["x"] < r["day"] and r["x"] < r["day2"], (
                f"{tag}: not a local minimum (x {r['x']:.4f}, -2d {r['day']:.4f}, "
                f"+2d {r['day2']:.4f})")
            # Iapetus's orbit is 59.1 Saturn radii: at elongation it is drawn as a chevron at
            # the edge of the strip, and its distance is what the entry carries
            assert 55 < r["r"] < 62, f"{tag}: Iapetus's elongation is {r['r']} radii"
            assert r["muted"] in (True, False), tag
        # 79.33 d is the mean of the engine's own elongations (nova_saturn.js says so in its
        # header, after Meeus ch. 46); the elongations themselves run 78.06 to 81.68 days
        # apart over 553 of them from 2024 to 2040, because the light-time correction and the
        # ring-plane geometry move the moment Iapetus stands furthest west
        gaps = [b["utcMs"] - a["utcMs"] for a, b in zip(c["rows"], c["rows"][1:])]
        for g in gaps:
            assert 78.0 <= g / 86400000 <= 81.7, f"{where}: two elongations {g / 86400000} d apart"
        assert 8 <= len(c["rows"]) <= 10, f"{where}: {len(c['rows'])} rows in two years"
