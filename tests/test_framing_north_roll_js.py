"""The frame overlay stays at the Angle relative to true north while the view rolls.

Aladin's drag keeps the grabbed sky point under the cursor, which rolls the view:
every drag turns north on screen by about dRA * sin(dec), and loops add up (three
circles at a 180 deg field measured ~40 deg in the browser). The frame overlay is
screen-fixed, so before the fix it stayed at the Angle as if north were up -- the
framed angle no longer matched the sky.

The real code is cut out of graph_view_chart.js (updateScreenFovOverlay, the frame
overlay's animation-frame tick and the north helpers) and run against a fake Aladin
with a real gnomonic projection that rolls on drag like Aladin does. The frame's
on-screen angle minus where north points (the fake's ground truth) must equal the
Angle after any drag sequence -- during the drag and its inertia too -- and the view
must come back to north-up once it is at rest, around an unchanged centre.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
NODE = shutil.which("node")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [chartFile, inFile, outFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const src = fs.readFileSync(chartFile, 'utf8');

function cut(source, start) {
    let i = source.indexOf('{', source.indexOf(')', start)), depth = 0;
    for (; i < source.length; i++) {
        const ch = source[i], nx = source[i + 1];
        if (ch === '/' && nx === '/') { i = source.indexOf('\n', i) - 1; continue; }
        if (ch === '/' && nx === '*') { i = source.indexOf('*/', i) + 1; continue; }
        if (ch === '"' || ch === "'" || ch === '`') { for (i++; source[i] !== ch; i++) if (source[i] === '\\') i++; continue; }
        if (ch === '{') depth++;
        if (ch === '}') { depth--; if (depth === 0) return source.slice(start, i + 1); }
    }
    throw new Error('unterminated function at ' + start);
}
// A function by name; absent ones (older code) are left out.
function fnOpt(name) {
    const hits = [...src.matchAll(new RegExp('\\n    function\\s+' + name + '\\s*\\(', 'g'))];
    if (hits.length > 1) throw new Error(name + ': ' + hits.length + ' declarations');
    return hits.length ? cut(src, hits[0].index + 1) : '';
}
function stmtOpt(name) {
    const m = src.match(new RegExp('\\n    (?:let|const)\\s+' + name + '\\s*=[^;]*;'));
    return m ? m[0] : '';
}

// --- The fake Aladin: gnomonic projection, east left, rolled by `roll` degrees
// (where north points, clockwise from screen-up). A drag moves the sky with the
// cursor and rolls the view by dRA * sin(dec), as Aladin's drag does.
const W = 1000, H = 600, D = Math.PI / 180;
function makeAladin(c) {
    const st = { ra: c.ra, dec: c.dec, roll: 0, fov: c.fov, inertia: [] };
    const scale = () => W / (2 * Math.tan(Math.min(st.fov, 170) * D / 2));   // px per tangent unit
    const al = {
        st,
        getSize: () => [W, H],
        getFov: () => [st.fov, st.fov * H / W],
        getRaDec: () => [st.ra, st.dec],
        getRotation: () => -st.roll,
        setRotation: v => { st.roll = -v; },
        world2pix(ra, dec) {
            const a = (ra - st.ra) * D, d = dec * D, d0 = st.dec * D;
            const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a);
            if (cosc <= 0) return null;
            const xi = Math.cos(d) * Math.sin(a) / cosc;
            const eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a)) / cosc;
            const vx = -xi * scale(), vy = -eta * scale(), r = st.roll * D;
            return [W / 2 + vx * Math.cos(r) - vy * Math.sin(r), H / 2 + vx * Math.sin(r) + vy * Math.cos(r)];
        },
        pix2world(x, y) {
            const r = -st.roll * D, ux = x - W / 2, uy = y - H / 2;
            const vx = ux * Math.cos(r) - uy * Math.sin(r), vy = ux * Math.sin(r) + uy * Math.cos(r);
            const xi = -vx / scale(), eta = -vy / scale(), d0 = st.dec * D;
            const rho = Math.hypot(xi, eta), cc = Math.atan(rho);
            if (rho === 0) return [st.ra, st.dec];
            const dec = Math.asin(Math.cos(cc) * Math.sin(d0) + eta * Math.sin(cc) * Math.cos(d0) / rho);
            const ra = st.ra * D + Math.atan2(xi * Math.sin(cc), rho * Math.cos(d0) * Math.cos(cc) - eta * Math.sin(d0) * Math.sin(cc));
            return [(((ra / D) % 360) + 360) % 360, dec / D];
        },
        drag(dx, dy) {
            const to = al.pix2world(W / 2 - dx, H / 2 - dy);
            let dra = to[0] - st.ra; if (dra > 180) dra -= 360; if (dra < -180) dra += 360;
            st.roll += dra * Math.sin((st.dec + to[1]) / 2 * D);
            st.ra = to[0]; st.dec = to[1];
        },
    };
    if (c.noProjection) { delete al.world2pix; delete al.pix2world; }
    return al;
}

function runCase(c) {
    const props = {};
    const rect = { hidden: true, children: [], style: { setProperty: (k, v) => { props[k] = v; } },
                   appendChild(ch) { this.children.push(ch); return ch; },
                   removeChild(ch) { this.children.splice(this.children.indexOf(ch), 1); },
                   get lastChild() { return this.children[this.children.length - 1]; } };
    const els = {
        'aladin-lite-div': { clientWidth: W, clientHeight: H, addEventListener() {} },
        'screen-fov-rect': rect,
        'framing-rig-select': { selectedIndex: 0, options: [{ dataset: { fovw: '120', fovh: '80' } }] },
        'framing-rotation': { value: String(c.angle) },
    };
    let frames = [];
    const ctx = {
        Math, Number, Array, String, JSON, Object, Error,
        document: { getElementById: id => els[id] || null,
                    createElement: () => ({ style: { setProperty() {} }, className: '', textContent: '' }) },
        window: { addEventListener() {} },
        requestAnimationFrame: f => { frames.push(f); return frames.length; },
        cancelAnimationFrame: () => {},
        console: { log() {}, warn() {}, error() {} },
    };
    vm.createContext(ctx);
    const aladin = makeAladin(c);
    const api = vm.runInContext(`
        let aladin = __aladin;
        let frameRafId = null;
        ${stmtOpt('viewPointerDown')}
        ${stmtOpt('viewStill')}
        ${stmtOpt('VIEW_LEVEL_STILL_FRAMES')}
        function updateReadoutFromCenter() {}
        ${fnOpt('viewNorthOffsetDeg')}
        ${fnOpt('maybeLevelView')}
        ${fnOpt('wireViewPointer')}
        ${fnOpt('updateScreenFovOverlay')}
        ${fnOpt('startFrameOverlayLoop')}
        ({ start: startFrameOverlayLoop,
           pointer: down => { try { viewPointerDown = down; } catch (e) {} } })
    `, Object.assign(ctx, { __aladin: aladin }));

    const tick = () => { const f = frames; frames = []; f.forEach(fn => fn()); };
    const samples = [];
    const sample = label => {
        const rot = parseFloat(props['--frame-rot']);
        samples.push({ label, frameRot: rot, north: aladin.st.roll, ra: aladin.st.ra, dec: aladin.st.dec });
    };
    api.start();
    tick();
    sample('start');
    for (const step of c.steps) {
        if (step.do === 'drag') {
            // A held drag: one move per frame, as the browser delivers them.
            api.pointer(true);
            for (let i = 0; i < step.n; i++) {
                const a = step.circle ? 2 * Math.PI * (i + 1) / step.n : 0;
                const ap = step.circle ? 2 * Math.PI * i / step.n : 0;
                const dx = step.circle ? step.r * (Math.cos(a) - Math.cos(ap)) : step.dx / step.n;
                const dy = step.circle ? step.r * (Math.sin(a) - Math.sin(ap)) : step.dy / step.n;
                aladin.drag(dx, dy);
                tick();
                sample('drag');
            }
            if (!step.hold) api.pointer(false);
        } else if (step.do === 'inertia') {
            for (let i = 0; i < step.n; i++) { aladin.drag(step.dx, step.dy); tick(); sample('inertia'); }
        } else if (step.do === 'frames') {
            for (let i = 0; i < step.n; i++) { tick(); sample(step.label || 'rest'); }
        } else if (step.do === 'release') {
            api.pointer(false);
        }
    }
    return samples;
}

const out = {};
for (const [name, c] of Object.entries(input.cases)) {
    try { out[name] = runCase(c); } catch (e) { out[name] = { error: String(e && e.stack || e) }; }
}
fs.writeFileSync(outFile, JSON.stringify(out));
"""

IC443 = {"ra": 94.27, "dec": 22.52}
CIRCLES = [{"do": "drag", "circle": True, "r": 300, "n": 48}] * 3

CASES = {}
for fov in (180, 30, 2):
    for angle in (0, 37.5):
        CASES[f"circles_{fov}_{angle}"] = {**IC443, "fov": fov, "angle": angle, "steps": CIRCLES + [
            {"do": "frames", "n": 10}]}
        CASES[f"straight_{fov}_{angle}"] = {**IC443, "fov": fov, "angle": angle, "steps": [
            {"do": "drag", "dx": 800, "dy": 0, "n": 30},
            {"do": "drag", "dx": -300, "dy": 450, "n": 20},
            {"do": "frames", "n": 10}]}
CASES.update({
    # Held still with the button down: no levelling under the user's hand.
    "held": {**IC443, "fov": 30, "angle": 12, "steps": [
        {"do": "drag", "dx": 800, "dy": 200, "n": 20, "hold": True},
        {"do": "frames", "n": 12, "label": "held"},
        {"do": "release"},
        {"do": "frames", "n": 10}]},
    # Inertia after release: the view keeps moving, so it is not levelled until it stops.
    "inertia": {**IC443, "fov": 30, "angle": 12, "steps": [
        {"do": "drag", "dx": 800, "dy": 0, "n": 20},
        {"do": "inertia", "dx": 6, "dy": 1, "n": 15},
        {"do": "frames", "n": 10}]},
    # Near the pole the north probe goes south of the centre instead.
    "pole": {"ra": 10.0, "dec": 89.99995, "fov": 2, "angle": 20, "steps": [
        {"do": "drag", "dx": 0, "dy": 0, "n": 1},
        {"do": "frames", "n": 1}]},
    # An Aladin without the projection calls: Aladin's own roll angle is used.
    "fallback": {**IC443, "fov": 30, "angle": 25, "noProjection": True, "steps": [
        {"do": "frames", "n": 1}]},
})


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_north_roll")
    driver, inp, result = tmp / "driver.cjs", tmp / "in.json", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    inp.write_text(json.dumps({"cases": CASES}), encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(inp), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def _samples(out, name):
    res = out[name]
    assert not isinstance(res, dict), res.get("error")
    return res


def _wrap(deg):
    return ((deg + 180) % 360) - 180


def _relative_to_north(s):
    """The frame's angle on screen measured from where north points."""
    return _wrap(s["frameRot"] - s["north"])


@pytest.mark.parametrize("name", sorted(n for n in CASES if n.startswith(("circles_", "straight_"))))
def test_the_frame_keeps_its_angle_relative_to_north_through_any_drag(out, name):
    samples = _samples(out, name)
    angle = CASES[name]["angle"]
    for s in samples:
        assert abs(_relative_to_north(s) - _wrap(angle)) < 1e-6, (s["label"], s)


@pytest.mark.parametrize("name", ["circles_180_0", "circles_30_37.5", "straight_2_37.5", "straight_180_0"])
def test_the_drag_really_rolled_the_view(out, name):
    """Guards the fake: without a roll there would be nothing to test."""
    rolls = [abs(s["north"]) for s in _samples(out, name) if s["label"] == "drag"]
    assert max(rolls) > 0.2, max(rolls)


@pytest.mark.parametrize("name", sorted(n for n in CASES if n.startswith(("circles_", "straight_"))))
def test_at_rest_the_view_is_north_up_and_the_frame_at_the_angle(out, name):
    samples = _samples(out, name)
    last = samples[-1]
    assert abs(last["north"]) < 1e-9, last
    assert abs(_wrap(last["frameRot"]) - _wrap(CASES[name]["angle"])) < 1e-6, last


def test_levelling_turns_the_view_around_its_centre(out):
    samples = _samples(out, "circles_180_0")
    rest = [s for s in samples if s["label"] == "rest"]
    assert len({(round(s["ra"], 9), round(s["dec"], 9)) for s in rest}) == 1


def test_no_levelling_while_the_sky_is_held(out):
    samples = _samples(out, "held")
    held = [s for s in samples if s["label"] == "held"]
    assert all(abs(s["north"]) > 0.5 for s in held), [s["north"] for s in held]
    assert all(abs(_relative_to_north(s) - 12) < 1e-6 for s in held)
    assert abs(samples[-1]["north"]) < 1e-9


def test_no_levelling_while_the_view_is_still_coasting(out):
    samples = _samples(out, "inertia")
    coasting = [s for s in samples if s["label"] == "inertia"]
    assert all(abs(s["north"]) > 0.5 for s in coasting)
    assert all(abs(_relative_to_north(s) - 12) < 1e-6 for s in coasting)
    assert abs(samples[-1]["north"]) < 1e-9


def test_at_the_pole_the_frame_angle_is_still_measured(out):
    for s in _samples(out, "pole"):
        assert abs(_relative_to_north(s) - 20) < 1e-3, s


def test_without_projection_calls_aladins_roll_angle_is_used(out):
    s = _samples(out, "fallback")[-1]
    assert abs(_wrap(s["frameRot"]) - 25) < 1e-9, s
