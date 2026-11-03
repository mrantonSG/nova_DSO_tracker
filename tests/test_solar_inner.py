"""Checks for the Venus and Mercury cards' rules (static/js/nova_solar_inner.js).

The scripts load the way the Solar System page loads them: plain scripts in one vm
context, in the page's order, so nova_solar_inner.js finds NovaAstro, NovaPlanets,
NovaDashboardRows and NovaSolarRows as globals. No DOM, no Flask, no network.

Three outside sources, and what each of them is for:

  * the day's geometry, against an independent 3D reconstruction (the `_GEOM` block,
    the same bench as test_solar_mars.py's). A mirrored or wrongly rotated disc is
    otherwise invisible — every mark sits on the disc and the pole points somewhere
    plausible — so the reconstruction rebuilds the line of sight, the pole, the
    celestial-north sky basis and the sub-solar direction from the engine's public
    outputs, and checks the vectors the card actually drew in all three views.

  * the planet's five quantities, against ephem 4.2 (tests/fixtures/solar_inner.json,
    generated offline by tools/gen_solar_inner_fixture.py). ephem is the library the
    shared planet engine was built against, and it reaches the phase angle by a
    different route: from the illuminated percentage rather than by the law of cosines.

  * the two poles, against a raw JPL Horizons extract checked in under
    tests/fixtures/horizons/ (quantity 17, NP.ang). The IAU/WGCCRE elements are what
    the module turns into the drawn rotation, and this is an outside ephemeris turning
    them into the same angle independently. The same extract pins that the rotation is
    real: both poles sweep the full 0-360 deg over the three years, so a constant or a
    stuck angle would fail.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
FIXTURE = json.loads((REPO / "tests" / "fixtures" / "solar_inner.json").read_text())
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
              "nova_solar_rows.js", "nova_solar_inner.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

LONDON = {"lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London",
          "altitude_threshold": 30, "sampling_interval": 15}
SYDNEY = {"lat": -33.8688, "lon": 151.2093, "timezone": "Australia/Sydney",
          "altitude_threshold": 30, "sampling_interval": 15}
TROMSO = {"lat": 69.6492, "lon": 18.9553, "timezone": "Europe/Oslo",
          "altitude_threshold": 20, "sampling_interval": 15}
# The same place at the coarsest Calculation Precision the settings offer ("Fastest (60
# min samples)"). At that step a run above the threshold can be a single sample, which is
# where the window and the instant have to be read off the same samples or they disagree.
LONDON_SIXTY = {**LONDON, "sampling_interval": 60}
SITES = (LONDON, SYDNEY, LONDON_SIXTY)

# Nights spread over the two bodies' apparitions, and over the ends of the phase range:
# a wide Venus crescent near greatest brilliancy, Venus and Mercury gibbous, both wide
# crescents again, Venus at superior conjunction (the disc wholly lit, which litPath
# answers with nothing) and Venus at inferior conjunction (wholly dark, the whole disc
# drawn as shaded). The fixture's own extremes: phase angles 1.11 and 178.43 deg. The
# last two are there for the coarsest site: Venus crosses London's 30 deg threshold
# between two samples those evenings, so the site's run above it is a single sample —
# the window the card has to show as a time rather than as a range.
NIGHTS = ("2026-11-10", "2027-01-15", "2027-08-12", "2028-05-15", "2028-06-01",
          "2028-02-05", "2028-04-30")

# The reconstruction agrees with the card to ~1e-6 (the residual is the six-decimal
# rounding of the path coordinates). A mirrored disc is off by ~2.0, so 2e-5 is loose
# enough for the engine's noise and tight enough to catch a flip.
TOL = 2e-5

# The tolerances for the ephem fixture. Each is at least twice the measured
# disagreement (recorded in tools/gen_solar_inner_fixture.py's docstring): magnitude
# 0.008, apparent diameter 2.7e-4 of itself, illuminated fraction 4e-4, phase angle
# 0.047 deg (Mercury close to new, where the arccosine is steep), elongation 0.009 deg.
EPHEM_TOL = {"mag": 0.02, "diameterRel": 1e-3, "illuminated": 1e-3,
             "phaseAngleDeg": 0.1, "elongationDeg": 0.02}

# The pole against Horizons NP.ang: measured worst 0.007 deg, median 0.002 deg.
POLE_TOL_DEG = 0.02
POLE_MIN_SPAN_DEG = 300.0

# How far the frame the drawing's position angle defines may sit from the projection of
# the published IAU pole. They are the same pole referred to different equators — J2000
# for the elements, the equator of date for the position angle — so they part company by
# the precession, up to 0.18 deg over 2026-2028. Mars's card has the same gap, measured
# to the same 0.18 deg; nova_mars.js, nova_jupiter.js and nova_saturn.js draw it the same
# way, and nova_solar_inner.js copies them. The IAU pole itself is checked against
# Horizons, to 0.007 deg, in test_the_pole_angle_matches_the_horizons_extract below.
IAU_GAP_MAX_DEG = 0.25

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


# --- the day's geometry ------------------------------------------------------------------

# The independent 3D reconstruction. It uses only the engine's public outputs — the
# body's J2000 place via NovaPlanets.position(), its phase angle via
# NovaPlanets.details(), and the module's two position angles — plus the IAU pole
# elements, which the Horizons test pins separately.
#
# The drawing frame is the module's own definition (a sky direction at position angle th
# is at th - polePA from the body's pole, east on the left), rebuilt from the body's
# place and its pole; a mirrored or wrongly turned disc then disagrees in the vectors the
# card actually drew. The IAU pole is checked against that frame separately, as
# `iauGap`: the two differ by the precession between the J2000 pole the elements are
# referred to and the equator of date the position angle is measured in, which is up to
# 0.18 deg for all three of Mars, Mercury and Venus.
_GEOM = r"""
const DEG = Math.PI / 180;
const sub = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const unit3 = a => { const n = Math.hypot(a[0], a[1], a[2]); return [a[0]/n, a[1]/n, a[2]/n]; };

// T       line of sight, body -> Earth
// n_sky   celestial north's projection on the sky plane, e_sky = T x n_sky
// at(th)  the sky-plane direction at position angle th, north through east
// draw(v) a J2000 direction in the drawing's own frame, y towards the body's pole
function frame(body, utcMs, pa) {
    const p = NovaPlanets.position(body, utcMs);
    const ra = p.raHours * 15 * DEG, dec = p.decDeg * DEG;
    const T = [-Math.cos(dec)*Math.cos(ra), -Math.cos(dec)*Math.sin(ra), -Math.sin(dec)];
    const nc = [0, 0, 1];
    const n_sky = unit3(sub(nc, T.map(x => x * dot(nc, T))));
    const e_sky = cross(T, n_sky);
    const at = th => { const r = th * DEG;
        return [0,1,2].map(i => Math.cos(r)*n_sky[i] + Math.sin(r)*e_sky[i]); };

    // The body's own north up: y_draw is the sky direction at the pole's position angle,
    // x_draw completes it so that the pair is a right-handed view of the sky with east on
    // the left, which is what the module's fromSky() says.
    const y_draw = at(pa);
    const x_draw = cross(y_draw, T);
    const draw = v => [dot(v, x_draw), dot(v, y_draw)];

    // And where the published IAU pole actually points, for the separate `iauGap` check
    const el = NovaSolarInner.POLES[body];
    const Tt = ((utcMs + 69184) / 86400000 + 2440587.5 - 2451545.0) / 36525;
    const pra = (el.ra0 + el.raRate * Tt) * DEG, pdec = (el.dec0 + el.decRate * Tt) * DEG;
    const P = [Math.cos(pdec)*Math.cos(pra), Math.cos(pdec)*Math.sin(pra), Math.sin(pdec)];
    const iau = unit3(sub(P, T.map(x => x * dot(P, T))));
    const w = cross(iau, y_draw);
    const gap = Math.asin(Math.min(1, Math.hypot(w[0], w[1], w[2]))) / DEG;
    return { T: T, P: P, draw: draw, at: at, n_sky: n_sky, iau: iau, iauGap: gap };
}

// The card returns SVG; this is back to the card's own drawing frame (y up towards the
// body's north pole), undoing viewOf: SVG is (sx * x, sy * y).
const fromSvg = (mode, x, y) => {
    const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
    return [sx * x, sy * y];
};

// The bright limb, read off the shaded path the card actually drew. litPath was handed
// -u, so its first point is (-uy, ux) of that u, and the card's own u is (-py, px).
const limbOf = (mode, path) => {
    const tok = path.trim().split(/\s+/);
    if (tok.length !== 20) return null;
    const b = fromSvg(mode, -(+tok[2]), +tok[1]);
    const n = Math.hypot(b[0], b[1]);
    return [b[0]/n, b[1]/n];
};
"""

_DIAGRAM = _GEOM + r"""
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        for (const body of NovaSolarInner.BODIES) {
            const t = NovaSolarInner.tonight(body, { inputs: { location: loc },
                nowMs: Date.parse(night + 'T20:00:00Z'), simDate: null });
            const r = frame(body, t.instantUtc, t.polePA);
            const err = (a, b) => Math.hypot(a[0]-b[0], a[1]-b[1]);

            const nw = r.draw(r.n_sky), nu = Math.hypot(nw[0], nw[1]);
            const gw = r.draw(r.iau), gu = Math.hypot(gw[0], gw[1]);
            // The sub-solar direction: cos i along the line of sight (which projects to
            // nothing) plus sin i at the Sun's position angle
            const sw = r.draw([0,1,2].map(i =>
                Math.sin(t.phaseAngleDeg*DEG) * r.at(t.sunPA)[i]));
            const su = Math.hypot(sw[0], sw[1]);
            // -1 at new, +1 at full: the sign of cos i is which limb is dark
            const k = Math.cos(t.phaseAngleDeg * DEG);

            for (const mode of NovaSolarInner.VIEWS) {
                const d = NovaSolarInner.diagram(t, mode);
                const tok = d.shaded ? d.shaded.trim().split(/\s+/) : [];
                // A crescent is a crescent because it has a terminator edge, not because
                // of how long the path is: litPath's whole-disc answer is a closed
                // two-arc circle of the same twenty numbers
                const crescent = d.terminator !== null;
                const limb = crescent ? limbOf(mode, d.shaded) : null;
                out.push({
                    night: night, site: loc.timezone, body: body, mode: mode,
                    pole: err(fromSvg(mode, d.pole.x, d.pole.y), [gw[0]/gu, gw[1]/gu]),
                    poleUp: err(fromSvg(mode, d.pole.x, d.pole.y), [0, 1]),
                    north: err(fromSvg(mode, d.north.x, d.north.y), [nw[0]/nu, nw[1]/nu]),
                    northAngle: Math.atan2(nw[0]/nu, nw[1]/nu) / DEG, polePA: t.polePA,
                    iauGap: r.iauGap,
                    sun: limb === null ? null : err(limb, [sw[0]/su, sw[1]/su]),
                    empty: d.shaded === null, crescent: crescent,
                    // The terminator's semi-minor axis is |cos i|, and its sweep flag
                    // says which side of the limb the dark region is on
                    rx: crescent ? Math.abs(+tok[12]) : null,
                    sweep: crescent ? +tok[16] : null,
                    k: k, phaseAngleDeg: t.phaseAngleDeg,
                });
            }
        }
    }
}
return out;
"""


def test_the_disc_agrees_with_the_3d_reconstruction(tmp_path):
    """The pole, celestial north and the bright limb, in all three views, rebuilt from the
    engine's public outputs on the module's own definition of the drawing frame, with the
    body's own north up. TOL is 2e-5. The terminator is drawn for the phase angle the card
    reports, and a disc near full or near new is wholly drawn or wholly dark.

    The pole the card draws is up the frame that its own position angle defines; the
    published IAU pole's projection is checked against that frame as iauGap, on a
    tolerance of the precession between J2000 and the equator of date."""
    cases = _run(tmp_path, _DIAGRAM, nights=list(NIGHTS), sites=list(SITES))
    assert len(cases) == len(NIGHTS) * len(SITES) * 2 * 3

    crescents, closed_shut, open_full = 0, 0, 0
    for c in cases:
        where = f"{c['body']} {c['night']} @ {c['site']}·{c['mode']}"
        # The drawn pole is where the IAU pole projects, to the accuracy the frame can
        # carry: see IAU_GAP_MAX_DEG below
        assert c["pole"] <= c["iauGap"] + TOL, (
            f"the pole drawn at {where} is {c['pole']:.3e} from the IAU pole's "
            f"projection where the frame itself is only {c['iauGap']:.3e} off it")
        assert c["iauGap"] <= IAU_GAP_MAX_DEG, (
            f"the frame the position angle defines is {c['iauGap']:.3f} deg off the IAU "
            f"pole at {where} — more than the precession accounts for")
        assert c["poleUp"] <= TOL, f"the pole is not straight up at {where}: {c['poleUp']:.3e}"
        assert c["north"] <= TOL, (
            f"the north arrow is off celestial north at {where}: |delta| = {c['north']:.3e}")
        # In the body's own frame celestial north sits polePA the other way round from
        # the pole: clockwise (to the right) for a pole east of north
        assert ((c["northAngle"] - c["polePA"] + 180) % 360) - 180 == pytest.approx(0, abs=1e-3), where
        # A terminator edge is drawn exactly for the crescent phases
        assert c["crescent"] == (abs(c["k"]) < 0.9995), (
            f"the disc is {'a crescent' if c['crescent'] else 'whole'} where cos i = "
            f"{c['k']} at {where}")

        if c["crescent"]:
            crescents += 1
            assert c["rx"] == pytest.approx(abs(c["k"]), abs=2e-6), (
                f"the terminator's semi-minor axis is {c['rx']} where |cos i| = "
                f"{abs(c['k'])} at {where}")
            # Which side the dark region is on, from the arc's sweep flag
            assert c["sweep"] == (0 if c["k"] < 0 else 1), (
                f"the terminator sweeps {c['sweep']} for cos i = {c['k']} at {where}")
            assert c["sun"] is not None
            assert c["sun"] <= TOL, (
                f"the bright limb drawn at {where} is not the sub-solar direction: "
                f"|delta| = {c['sun']:.3e}")
        else:
            # The whole disc is shaded when the body is unlit, and nothing is shaded
            # when it is fully lit: litPath answers those with a bare circle or nothing
            assert abs(c["k"]) >= 0.9995, (
                f"an open path where the phase is not nearly full or new at {where}: "
                f"cos i = {c['k']}")
            assert c["empty"] == (c["k"] > 0), (
                f"a disc at cos i = {c['k']} should be "
                f"{'unshaded' if c['k'] > 0 else 'fully shaded'} at {where}")
            closed_shut += 1 if c["k"] < 0 else 0
            open_full += 1 if c["k"] > 0 else 0
    # Every branch of the drawing is exercised, or the run is not the one that was meant
    assert crescents >= 20, f"only {crescents} crescent drawings to check a bright limb"
    assert open_full >= 3, (
        f"only {open_full} wholly lit discs: the nights near superior conjunction are "
        f"not doing their job ({NIGHTS})")
    assert closed_shut >= 3, (
        f"only {closed_shut} wholly dark discs: the nights near inferior conjunction are "
        f"not doing their job ({NIGHTS})")


# --- the two poles, against Horizons -----------------------------------------------------

_HORIZONS = r"""
const out = [];
for (const body of input.bodies) {
    for (const row of input.rows[body]) {
        const t = Date.parse(row.date + 'T00:00:00Z');
        out.push({ body: body, date: row.date, want: row.npAngDeg,
                   got: NovaSolarInner._angles(body, t).polePA });
    }
}
return out;
"""


def test_the_pole_angle_matches_the_horizons_extract(tmp_path):
    """Horizons quantity 17 (NP.ang) at 157 dates over 2026-2028 for both bodies, to
    POLE_TOL_DEG — and the angle sweeps the full sky, so a constant would fail."""
    rows = {b: h["rows"] for b, h in FIXTURE["horizons"].items()}
    got = _run(tmp_path, _HORIZONS, bodies=list(rows), rows=rows)
    assert len(got) == sum(len(r) for r in rows.values()) == 2 * 157

    angles, worst = {}, {}
    for c in got:
        diff = abs(((c["got"] - c["want"] + 540) % 360) - 180)
        assert diff <= POLE_TOL_DEG, (
            f"{c['body']} {c['date']}: drawn polePA {c['got']:.4f} deg against Horizons "
            f"NP.ang {c['want']:.4f} deg — off by {diff:.4f} deg")
        angles.setdefault(c["body"], []).append(c["got"])
        worst[c["body"]] = max(worst.get(c["body"], 0.0), diff)

    for body, pa in angles.items():
        assert max(pa) - min(pa) >= POLE_MIN_SPAN_DEG, (
            f"{body}: the pole only spans {max(pa) - min(pa):.1f} deg — the check would "
            f"pass on a constant")
    # Not merely under the tolerance: the module's header claims an order better
    assert max(worst.values()) <= 0.01, (
        f"the worst pole angle is {max(worst.values()):.4f} deg, "
        f"not the claimed 0.007 deg")


# --- the planet's quantities, against ephem ----------------------------------------------

_EPHEM = r"""
const out = [];
for (const body of input.bodies) {
    for (const row of input.rows[body]) {
        const d = NovaPlanets.details(body, Date.parse(row.date + 'T00:00:00Z'));
        out.push({
            body: body, date: row.date,
            mag: d.mag - row.mag,
            diameterRel: (d.diameterArcsec - row.diameterArcsec) / row.diameterArcsec,
            illuminated: d.illuminated - row.illuminated,
            phaseAngleDeg: d.phaseAngleDeg - row.phaseAngleDeg,
            elongationDeg: d.elongationDeg - row.elongationDeg,
            wantElong: row.elongationDeg,
        });
    }
}
return out;
"""


def test_the_planet_quantities_match_the_ephem_fixture(tmp_path):
    """Magnitude, apparent diameter, illuminated fraction, phase angle and signed
    elongation, for both bodies, at every date in the fixture. The phase angle is not
    read from ephem: it is recovered there from the illuminated percentage, so the two
    arrive at it by different routes."""
    rows = FIXTURE["ephem"]["bodies"]
    got = _run(tmp_path, _EPHEM, bodies=list(rows), rows=rows)
    assert len(got) == sum(len(r) for r in rows.values()) == 2 * 157

    for c in got:
        where = f"{c['body']} {c['date']}"
        for key, tol in EPHEM_TOL.items():
            assert abs(c[key]) <= tol, (
                f"{where}: {key} is off by {c[key]:.3e} (tolerance {tol:g})")
    # Not vacuous: the elongation really changes sign over the three years
    for body in rows:
        e = [c["wantElong"] for c in got if c["body"] == body]
        assert min(e) < -5 and max(e) > 5, f"{body}: the fixture's elongation barely moves"


# --- tonight's invariants -----------------------------------------------------------------

_TONIGHT = r"""
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        for (const body of NovaSolarInner.BODIES) {
            const o = { inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'),
                        simDate: null };
            const t = NovaSolarInner.tonight(body, o);
            const site = NovaSolarRows._night.site(o.inputs);
            const win = NovaSolarRows._night.nightWindow(t.date, site, NovaAstro,
                NovaDashboardRows._wall, null);
            const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t.date);
            const noon = NovaDashboardRows._wall.localize(+d[1], +d[2], +d[3], 12, 0,
                site.timezone, NovaAstro, false);
            const times = NovaSolarRows._night.samples(noon, noon + 86400000, site.interval);
            const curve = NovaPlanets.altAzCurve(body, times, loc.lat, loc.lon);
            let dayMax = -90;
            for (const a of curve.alt) if (a > dayMax) dayMax = a;
            // The Sun does not set: its declination and the latitude have the same sign
            // and their magnitudes add past the pole
            const sunDec = NovaAstro.sunPosition(noon).dec * 180 / Math.PI;
            out.push({
                night: night, site: loc.timezone, body: body,
                date: t.date, instantUtc: t.instantUtc, hm: t.hm,
                maxAlt: t.maxAlt, threshold: t.threshold,
                muted: t.muted, mutedReason: t.mutedReason,
                illuminated: t.illuminated, mag: t.mag, scale: t.scale,
                phaseAngleDeg: t.phaseAngleDeg,
                elongationDeg: t.elongationDeg, elongationSide: t.elongationSide,
                best: t.best, daylightOnly: t.daylightOnly,
                winStart: win.start, winEnd: win.end, winKind: win.kind,
                dayMax: dayMax,
                polarDay: Math.abs(loc.lat + sunDec) > 90,
                minScale: NovaSolarInner.RADIUS_MIN_PX / NovaSolarInner.RADIUS_MAX_PX,
                maxScale: 1.0,
            });
        }
    }
}
return out;
"""


def test_tonight_is_self_consistent(tmp_path):
    """The instant is inside the night; the illumination, the phase angle and the sign
    of the elongation are the engine's at that instant; the muted state and its reason
    agree with the highest sample of the night; the scale is on the shared map; the best
    window lies inside the night and is the length it says; and daylightOnly means what
    the card says it means."""
    cases = _run(tmp_path, _TONIGHT, nights=list(NIGHTS), sites=list(SITES))
    assert len(cases) == len(NIGHTS) * len(SITES) * 2

    for c in cases:
        where = f"{c['body']} {c['night']} @ {c['site']}"
        assert c["winStart"] <= c["instantUtc"] <= c["winEnd"], (
            f"{where}: the instant {c['instantUtc']} is outside the night")
        # The illuminated fraction and the phase angle are the same statement
        assert c["illuminated"] == pytest.approx(
            (1 + math.cos(math.radians(c["phaseAngleDeg"]))) / 2, abs=1e-9), where
        assert 0 <= c["illuminated"] <= 1 and 0 <= c["phaseAngleDeg"] <= 180, where
        assert c["elongationSide"] == ("E" if c["elongationDeg"] >= 0 else "W"), where
        assert abs(c["elongationDeg"]) <= 180, where
        assert c["muted"] == (c["maxAlt"] < c["threshold"]), where
        assert c["mutedReason"] == (None if not c["muted"]
                                    else "notUp" if c["maxAlt"] < 0 else "altitude"), where
        assert c["minScale"] <= c["scale"] <= c["maxScale"], where
        # Venus never leaves the negative magnitudes, and neither body is fainter than
        # Neptune
        assert c["mag"] <= (0.5 if c["body"] == "venus" else 6.0), where

        b = c["best"]
        if b is None:
            assert c["instantUtc"] == pytest.approx((c["winStart"] + c["winEnd"]) / 2) \
                or c["maxAlt"] >= 0, where
        else:
            assert c["winStart"] <= b["startUtcMs"] and b["endUtcMs"] <= c["winEnd"], (
                f"{where}: the best window runs outside the night")
            # The instant in the title and the window in the tile are the same samples:
            # the title can never name a time outside the window it is the heading of
            assert b["startUtcMs"] <= c["instantUtc"] <= b["endUtcMs"], (
                f"{where}: the instant {c['hm']} is outside the window "
                f"{b['startHm']}-{b['endHm']} the card shows")
            # The run is as many samples as it says, and only a one-sample run is a
            # window of no width — the card shows that one time instead of a range
            assert b["samples"] >= 1, where
            assert (b["samples"] == 1) == (b["endUtcMs"] == b["startUtcMs"]), (
                f"{where}: {b['samples']} samples but the window runs "
                f"{b['startHm']}-{b['endHm']}")
            assert b["minutes"] == round((b["endUtcMs"] - b["startUtcMs"]) / 60000), where
            # An inner planet east of the Sun is an evening object and one west of it is
            # a morning one: the side has to agree with the sign of the elongation, not
            # merely be one of the two words
            assert b["side"] == ("evening" if c["elongationDeg"] > 0 else "morning"), (
                f"{where}: the window is called {b['side']} but the elongation is "
                f"{c['elongationDeg']:.1f} deg")

        # daylightOnly is "reaches the threshold today but the best window is not in the
        # night" — which is what makes it different from muted
        assert c["daylightOnly"] == (
            c["polarDay"] or (b is None and c["dayMax"] >= c["threshold"])), where


def test_the_disc_scale_is_monotone_and_pinned_at_the_extremes(tmp_path):
    """One scale for both bodies: the published extremes map to the published radii, the
    drawn radius never falls as the disc grows, and Mercury is not drawn as a speck."""
    got = _run(tmp_path, r"""
        const s = d => NovaSolarInner._scaleOf(d);
        const out = { atMin: s(NovaSolarInner.DIAM_MIN_ARCSEC),
                      atMax: s(NovaSolarInner.DIAM_MAX_ARCSEC),
                      min: NovaSolarInner.RADIUS_MIN_PX, max: NovaSolarInner.RADIUS_MAX_PX,
                      scale: [] };
        for (let d = 3; d <= 70; d += 0.25) out.scale.push(s(d));
        out.oversize = s(NovaSolarInner.DIAM_MAX_ARCSEC * 2);
        return out;
    """)
    assert got["atMin"] == pytest.approx(got["min"] / got["max"], abs=1e-12)
    assert got["atMax"] == pytest.approx(1.0, abs=1e-12)
    for a, b in zip(got["scale"], got["scale"][1:]):
        assert b >= a, f"the scale falls from {a} to {b} as the disc grows"
    assert got["scale"][-1] > got["scale"][0], "the scale is constant"
    # Strictly proportional it would sit at 4.5/66.1 of Venus's radius: a speck with an
    # unreadable phase, which is why the map is affine
    assert got["atMin"] / got["atMax"] > 2 * (4.5 / 66.1), (
        "the scale is proportional, not affine: Mercury's disc is drawn as a speck")


# --- the best time tonight ---------------------------------------------------------------

# The card's own rule, one minute at a time: over the whole observing day, keep the
# longest run that is inside the night window and at or above the threshold.
_BEST = r"""
const out = [];
for (const night of input.nights) {
    for (const loc of input.sites) {
        for (const body of NovaSolarInner.BODIES) {
            const o = { inputs: { location: loc }, nowMs: Date.parse(night + 'T20:00:00Z'),
                        simDate: null };
            const t = NovaSolarInner.tonight(body, o);
            const site = NovaSolarRows._night.site(o.inputs);
            const win = NovaSolarRows._night.nightWindow(t.date, site, NovaAstro,
                NovaDashboardRows._wall, null);
            const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t.date);
            const noon = NovaDashboardRows._wall.localize(+d[1], +d[2], +d[3], 12, 0,
                site.timezone, NovaAstro, false);
            const up = [];
            for (let u = noon; u <= noon + 86400000; u += 60000) {
                up.push(u >= win.start && u <= win.end
                    && NovaPlanets.altAz(body, u, loc.lat, loc.lon).alt >= site.threshold);
            }
            let run = null, start = -1;
            for (let i = 0; i <= up.length; i++) {
                const on = i < up.length && up[i];
                if (on && start < 0) start = i;
                if (!on && start >= 0) {
                    if (!run || i - start > run[1] - run[0]) run = [start, i];
                    start = -1;
                }
            }
            out.push({ night: night, site: loc.timezone, body: body,
                       best: t.best, daylightOnly: t.daylightOnly, muted: t.muted,
                       wantStart: run ? noon + run[0] * 60000 : null,
                       wantEnd: run ? noon + run[1] * 60000 : null,
                       win: [win.start, win.end], threshold: site.threshold,
                       interval: site.interval });
        }
    }
}
return out;
"""


def test_the_best_window_is_the_longest_run_above_the_threshold(tmp_path):
    """Independently, minute by minute: the longest run of the observing day that is both
    inside the night window and at or above the threshold. The card's window is the same
    run on its own sampling interval, so it sits inside it and covers all but one step of
    it at each end — and the planet really is up at both ends of the window it names."""
    cases = _run(tmp_path, _BEST, nights=list(NIGHTS), sites=list(SITES))
    assert any(c["best"] for c in cases), "no night had a window at all"
    assert any(c["best"] is None for c in cases), "every night had a window"
    # Both shapes of run are in the set: at the 60-minute site the run is sometimes a
    # single sample, which is the case a range of no width would come from
    assert any(c["best"] and c["best"]["samples"] == 1 for c in cases), (
        "no night produced a one-sample window, so the coarse case is untested")
    assert any(c["best"] and c["best"]["samples"] > 1 for c in cases), (
        "no night produced a window longer than one sample")

    for c in cases:
        where = f"{c['body']} {c['night']} @ {c['site']}"
        b = c["best"]
        if c["wantStart"] is None:
            assert b is None, (
                f"{where}: the card claims {b} where nothing gets above the threshold")
            continue
        assert b is not None, (
            f"{where}: the card found no window where the day runs "
            f"{c['wantStart']}..{c['wantEnd']}")
        # One sampling step of this site: the card's run is the truth's run read off that
        # grid, so it lands within one step of it at each end and never wider
        step = c["interval"] * 60000
        assert c["wantStart"] - step <= b["startUtcMs"] <= c["wantStart"] + step, where
        assert c["wantEnd"] - step <= b["endUtcMs"] <= c["wantEnd"] + step, where
        assert b["startUtcMs"] >= c["win"][0] and b["endUtcMs"] <= c["win"][1], (
            f"{where}: the window is not inside the night")


def test_daylight_only_is_honest_at_a_site_where_the_sun_does_not_set(tmp_path):
    """Tromsø in June: the noon-to-noon window nova_solar_rows.js falls back to is
    sunlight throughout, and there is no night to name. The card must not report a "best
    window" inside it — it says daylight only instead."""
    cases = _run(tmp_path, _TONIGHT, nights=["2027-06-21"], sites=[TROMSO])
    assert len(cases) == 2
    for c in cases:
        where = f"{c['body']} @ {c['site']}"
        assert c["polarDay"] and c["winKind"] == "noon", (
            f"{where}: expected a polar day, got {c['winKind']}")
        assert c["best"] is None, f"{where}: a window was named in broad daylight"
        assert c["daylightOnly"] is True, where


def test_a_polar_night_at_the_same_site_is_not_called_daylight(tmp_path):
    """Tromsø in December is the other extreme: the Sun never rises, so the day window is
    dark throughout and nothing about it is daylight. A window may be named or not, but it
    must lie inside the window the card is using."""
    cases = _run(tmp_path, _TONIGHT, nights=["2027-12-21"], sites=[TROMSO])
    assert len(cases) == 2
    for c in cases:
        where = f"{c['body']} @ {c['site']}"
        assert c["winKind"] == "noon", f"{where}: expected a polar night, got {c['winKind']}"
        assert c["daylightOnly"] is False, f"{where}: a polar night is not daylight"
        if c["best"]:
            assert c["winStart"] <= c["best"]["startUtcMs"]
            assert c["best"]["endUtcMs"] <= c["winEnd"]


# --- the error paths ---------------------------------------------------------------------

_ERRORS = r"""
const out = {};
const loc = input.site;
const o = { inputs: { location: loc }, nowMs: Date.parse('2026-11-10T20:00:00Z'),
            simDate: null };
const threw = f => { try { f(); return null; } catch (e) { return String(e.message); } };
out.badBody = threw(() => NovaSolarInner.tonight('earth', o));
out.noLocation = threw(() => NovaSolarInner.tonight('venus', { nowMs: Date.now(), simDate: null }));
out.emptyInputs = threw(() => NovaSolarInner.tonight('venus', { inputs: {}, nowMs: Date.now() }));
out.badNow = threw(() => NovaSolarInner.tonight('venus', { inputs: { location: loc }, nowMs: 'x' }));
out.noNow = threw(() => NovaSolarInner.tonight('venus', { inputs: { location: loc } }));
// A simDate the page cannot parse falls back to the instant, as nova_dashboard_rows.js
// defines it; that is not this module's contract and is checked with the page's rows.
out.noOpts = threw(() => NovaSolarInner.tonight('venus'));
out.badView = threw(() => NovaSolarInner.diagram(
    NovaSolarInner.tonight('venus', o), 'monochrome'));
out.noResult = threw(() => NovaSolarInner.diagram(null, 'normal'));
out.noPhase = threw(() => NovaSolarInner.diagram(
    { polePA: 0, sunPA: 0, phaseAngleDeg: NaN }, 'normal'));
out.badSun = threw(() => NovaSolarInner.diagram(
    { polePA: 0, sunPA: null, phaseAngleDeg: 10 }, 'normal'));
out.badScale = threw(() => NovaSolarInner._scaleOf('66'));
out.ok = NovaSolarInner.tonight('venus', o).body;
// Every view of a real night is a drawing of some kind
const t = NovaSolarInner.tonight('venus', o);
out.views = NovaSolarInner.VIEWS.map(m => {
    const d = NovaSolarInner.diagram(t, m);
    return { mode: m, keys: Object.keys(d).sort(), pole: d.pole, north: d.north };
});
out.bodies = NovaSolarInner.BODIES;
return out;
"""


def test_it_throws_on_bad_input(tmp_path):
    """An unknown body, a missing location, a non-finite instant, an unknown view and a
    missing result are all errors that name the module that raised them; and every view of
    a real night draws a disc with its pole straight up in the body's own frame."""
    got = _run(tmp_path, _ERRORS, site=LONDON)
    for key in ("badBody", "noLocation", "emptyInputs", "badNow", "noNow",
                "noOpts", "badView", "noResult", "noPhase", "badSun", "badScale"):
        assert got[key], f"{key} did not throw"
        # The module's own errors name it; a missing location is the night rules' error,
        # raised by nova_solar_rows.js one layer down, and names that instead.
        assert "nova_solar" in got[key], f"{key}: unhelpful message {got[key]!r}"
    assert got["ok"] == "venus"
    assert got["bodies"] == ["venus", "mercury"]
    assert {v["mode"] for v in got["views"]} == {"normal", "diagonal", "inverted"}
    for v in got["views"]:
        assert v["keys"] == ["north", "pole", "shaded", "terminator"], v
        # The pole is straight up in the figure's own frame, which is SVG's -y except in
        # the inverted view
        assert v["pole"] == {"x": 0, "y": 1 if v["mode"] == "inverted" else -1}, v
        assert math.hypot(v["north"]["x"], v["north"]["y"]) == pytest.approx(1, abs=1e-6)


# --- the module is Nova-side rules code ---------------------------------------------------

def test_the_module_is_not_one_of_the_shared_engines():
    """nova_solar_inner.js is Nova-side rules code, not an engine shared with nova-ios
    byte for byte, so it must not be pinned by tests/test_shared_js.py."""
    pinned = (REPO / "tests" / "test_shared_js.py").read_text()
    assert "nova_solar_inner.js" not in pinned
