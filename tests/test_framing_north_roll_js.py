"""The frame overlay stays at the Angle relative to true north while the view rolls.

Aladin's drag keeps the grabbed sky point under the cursor, which rolls the view:
every drag turns north on screen by about dRA * sin(dec), and loops add up (three
circles at a 180 deg field measured ~40 deg in the browser). The frame overlay is
screen-fixed, so before the fix it stayed at the Angle as if north were up -- the
framed angle no longer matched the sky.

The Framing panel's "Keep north up" box decides what happens to that roll once the
view is at rest. Both modes are tested, and the frame is checked against north in
every one of them, at every frame:

  off (the default)  nothing is levelled: the sky keeps whatever roll the drag left
                     it in, and the frame sits at Angle + that offset;
  on                 the roll is turned back to north-up over ~250 ms with an
                     ease-out curve, ending exactly at 0 -- a new drag cancels it,
                     ticking the box while rolled starts it once.

The real code is cut out of graph_view_chart.js (updateScreenFovOverlay, the frame
overlay's animation-frame tick, the north helpers, the levelling and the checkbox
wiring) and run against a fake Aladin with a real gnomonic projection that rolls on
drag like Aladin does, a clock that advances one frame step per animation frame, a
stand-in DOM with the checkbox and a stand-in localStorage. The frame's on-screen
angle minus where north points (the fake's ground truth) must equal the Angle after
any drag sequence -- during the drag, its inertia and the straightening too.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
NODE = shutil.which("node")

# The remembered key the panel writes, and the frame step the harness clock moves by.
STORAGE_KEY = "nova.framing.keepNorthUp"
FRAME_MS = 16          # one animation frame at 60 fps: 16 frames is a quarter second
STILL_FRAMES = 6       # VIEW_LEVEL_STILL_FRAMES: the still frames before levelling starts

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
    // The "Keep north up" checkbox: a change listener the real wiring installs, and a
    // click() that sets it and fires the listener the way the browser does.
    const box = { checked: false, listeners: {},
                  addEventListener(type, f) { (this.listeners[type] = this.listeners[type] || []).push(f); },
                  click(on) { this.checked = on; (this.listeners['change'] || []).forEach(f => f()); } };
    const els = {
        'aladin-lite-div': { clientWidth: W, clientHeight: H, addEventListener() {} },
        'screen-fov-rect': rect,
        'framing-rig-select': { selectedIndex: 0, options: [{ dataset: { fovw: '120', fovh: '80' } }] },
        'framing-rotation': { value: String(c.angle) },
        'keep-north-up': box,
    };
    let frames = [];
    // The clock: Date.now() moves by one frame step every animation frame, so the
    // straightening can be measured in milliseconds, not in frame counts.
    let now = 0;
    const FakeDate = class { constructor() { this.t = now; } static now() { return now; } };
    const store = Object.assign({}, c.stored || {});
    const storage = { getItem: k => (k in store ? store[k] : null),
                      setItem: (k, v) => { store[k] = String(v); },
                      removeItem: k => { delete store[k]; } };
    const ctx = {
        Math, Number, Array, String, JSON, Object, Error,
        document: { getElementById: id => els[id] || null,
                    createElement: () => ({ style: { setProperty() {} }, className: '', textContent: '' }) },
        window: { addEventListener() {} },
        requestAnimationFrame: f => { frames.push(f); return frames.length; },
        cancelAnimationFrame: () => {},
        console: { log() {}, warn() {}, error() {} },
        Date: FakeDate,
        localStorage: storage,
    };
    vm.createContext(ctx);
    const aladin = makeAladin(c);
    const api = vm.runInContext(`
        let aladin = __aladin;
        let frameRafId = null;
        ${stmtOpt('viewPointerDown')}
        ${stmtOpt('viewStill')}
        ${stmtOpt('VIEW_LEVEL_STILL_FRAMES')}
        ${stmtOpt('VIEW_LEVEL_MS')}
        ${stmtOpt('KEEP_NORTH_UP_KEY')}
        ${stmtOpt('viewLevelAnim')}
        function updateReadoutFromCenter() {}
        ${fnOpt('viewNorthOffsetDeg')}
        ${fnOpt('keepNorthUpOn')}
        ${fnOpt('startViewLevel')}
        ${fnOpt('stepViewLevel')}
        ${fnOpt('maybeLevelView')}
        ${fnOpt('wireViewPointer')}
        ${fnOpt('wireKeepNorthUp')}
        ${fnOpt('updateScreenFovOverlay')}
        ${fnOpt('startFrameOverlayLoop')}
        ({ start: startFrameOverlayLoop, wire: wireKeepNorthUp,
           pointer: down => { try { viewPointerDown = down; } catch (e) {} },
           box: on => box.click(!!on),
           boxChecked: () => box.checked,
           animating: () => !!viewLevelAnim })
    `, Object.assign(ctx, { __aladin: aladin, box: box }));

    const tick = () => { now += input.frameMs; const f = frames; frames = []; f.forEach(fn => fn()); };
    const samples = [];
    const sample = label => {
        samples.push({ label, frameRot: parseFloat(props['--frame-rot']), north: aladin.st.roll,
                       ra: aladin.st.ra, dec: aladin.st.dec, animating: api.animating(),
                       box: api.boxChecked(), t: now });
    };
    api.wire();                     // page load: the remembered choice goes into the box
    sample('wired');
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
        } else if (step.do === 'box') {
            api.box(step.on);
            sample('box');
        }
    }
    return { samples, stored: store };
}

const out = {};
for (const [name, c] of Object.entries(input.cases)) {
    try { out[name] = runCase(c); } catch (e) { out[name] = { error: String(e && e.stack || e) }; }
}
fs.writeFileSync(outFile, JSON.stringify(out));
"""

IC443 = {"ra": 94.27, "dec": 22.52}
CIRCLES = [{"do": "drag", "circle": True, "r": 300, "n": 48}] * 3
STRAIGHT = [{"do": "drag", "dx": 800, "dy": 0, "n": 30},
            {"do": "drag", "dx": -300, "dy": 450, "n": 20}]
ON = {STORAGE_KEY: "1"}
# Long enough for the still frames, the ~250 ms straightening and the frames after it.
REST = {"do": "frames", "n": 30}

CASES = {}
for fov in (180, 30, 2):
    for angle in (0, 37.5):
        for mode, stored in (("on", ON), ("off", {})):
            CASES[f"circles_{fov}_{angle}_{mode}"] = {**IC443, "fov": fov, "angle": angle,
                                                      "stored": stored, "steps": CIRCLES + [REST]}
            CASES[f"straight_{fov}_{angle}_{mode}"] = {**IC443, "fov": fov, "angle": angle,
                                                       "stored": stored, "steps": STRAIGHT + [REST]}
CASES.update({
    # Held still with the button down: no levelling under the user's hand.
    "held": {**IC443, "fov": 30, "angle": 12, "stored": ON, "steps": [
        {"do": "drag", "dx": 800, "dy": 200, "n": 20, "hold": True},
        {"do": "frames", "n": 12, "label": "held"},
        {"do": "release"},
        {"do": "frames", "n": 30, "label": "after"}]},
    # Inertia after release: the view keeps moving, so it is not levelled until it stops.
    "inertia": {**IC443, "fov": 30, "angle": 12, "stored": ON, "steps": [
        {"do": "drag", "dx": 800, "dy": 0, "n": 20},
        {"do": "inertia", "dx": 6, "dy": 1, "n": 15},
        {"do": "frames", "n": 30, "label": "after"}]},
    # Near the pole the north probe goes south of the centre instead.
    "pole": {"ra": 10.0, "dec": 89.99995, "fov": 2, "angle": 20, "steps": [
        {"do": "drag", "dx": 0, "dy": 0, "n": 1},
        {"do": "frames", "n": 1}]},
    # An Aladin without the projection calls: Aladin's own roll angle is used.
    "fallback": {**IC443, "fov": 30, "angle": 25, "noProjection": True, "steps": [
        {"do": "frames", "n": 1}]},
    # The straightening on its own: released, at rest, then turned back to north-up.
    "level": {**IC443, "fov": 30, "angle": 12, "stored": ON, "steps": [
        {"do": "drag", "dx": 700, "dy": 0, "n": 20},
        {"do": "frames", "n": 40, "label": "rest"}]},
    # A new drag while the view is on its way to north-up cancels it; the view keeps
    # the roll it had reached, and a later release straightens it again.
    "cancel_midway": {**IC443, "fov": 30, "angle": 12, "stored": ON, "steps": [
        {"do": "drag", "dx": 700, "dy": 0, "n": 20},
        {"do": "frames", "n": 14, "label": "levelling"},
        {"do": "drag", "dx": -120, "dy": 0, "n": 6, "hold": True},
        {"do": "frames", "n": 10, "label": "held"},
        {"do": "release"},
        {"do": "frames", "n": 40, "label": "after"}]},
    # Ticking the box while the sky sits rolled: straightens once, smoothly.
    "tick_while_rolled": {**IC443, "fov": 30, "angle": 12, "steps": [
        {"do": "drag", "dx": 700, "dy": 0, "n": 20},
        {"do": "frames", "n": 20, "label": "rolled"},
        {"do": "box", "on": True},
        {"do": "frames", "n": 40, "label": "levelling"}]},
    # Unticking it mid-curve: the view stops where it stands and stays rolled.
    "tick_off_midway": {**IC443, "fov": 30, "angle": 12, "stored": ON, "steps": [
        {"do": "drag", "dx": 700, "dy": 0, "n": 20},
        {"do": "frames", "n": 12, "label": "levelling"},
        {"do": "box", "on": False},
        {"do": "frames", "n": 30, "label": "after"}]},
})


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_north_roll")
    driver, inp, result = tmp / "driver.cjs", tmp / "in.json", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    inp.write_text(json.dumps({"cases": CASES, "frameMs": FRAME_MS}), encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(inp), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def _case(out, name):
    res = out[name]
    assert "error" not in res, res["error"]
    return res


def _samples(out, name):
    return _case(out, name)["samples"]


def _wrap(deg):
    return ((deg + 180) % 360) - 180


def _framed(samples):
    """The samples that carry a frame overlay: the 'wired' one is taken at page load,
    before the overlay loop has drawn anything, so it has no frame angle to measure."""
    return [s for s in samples if s["frameRot"] is not None]


def _relative_to_north(s):
    """The frame's angle on screen measured from where north points."""
    return _wrap(s["frameRot"] - s["north"])


def _mode(name):
    return name.rsplit("_", 1)[1]


def _animation(samples):
    """The samples the straightening ran over, and the frame that ended it."""
    runs, run = [], []
    for s in samples:
        if s["animating"]:
            run.append(s)
        elif run:
            runs.append((run, s))
            run = []
    if run:
        runs.append((run, None))
    return runs


def _rolled(names):
    return [n for n in names if n.startswith(("circles_", "straight_"))]


@pytest.mark.parametrize("name", sorted(_rolled(CASES)))
def test_the_frame_keeps_its_angle_relative_to_north_through_any_drag(out, name):
    """Both modes, every frame: drag, inertia, the straightening and the rest after it."""
    samples = _framed(_samples(out, name))
    angle = CASES[name]["angle"]
    for s in samples:
        assert abs(_relative_to_north(s) - _wrap(angle)) < 1e-6, (s["label"], s)


@pytest.mark.parametrize("name", ["circles_180_0_on", "circles_30_37.5_off",
                                  "straight_2_37.5_on", "straight_180_0_off"])
def test_the_drag_really_rolled_the_view(out, name):
    """Guards the fake: without a roll there would be nothing to test."""
    rolls = [abs(s["north"]) for s in _samples(out, name) if s["label"] == "drag"]
    assert max(rolls) > 0.2, max(rolls)


@pytest.mark.parametrize("name", sorted(n for n in _rolled(CASES) if _mode(n) == "on"))
def test_at_rest_the_view_is_north_up_and_the_frame_at_the_angle(out, name):
    samples = _samples(out, name)
    last = samples[-1]
    assert abs(last["north"]) < 1e-9, last
    assert abs(_wrap(last["frameRot"]) - _wrap(CASES[name]["angle"])) < 1e-6, last


@pytest.mark.parametrize("name", sorted(n for n in _rolled(CASES) if _mode(n) == "off"))
def test_at_rest_the_view_keeps_the_roll_the_drag_left_it_in(out, name):
    """Nothing is levelled with the box off -- not during the drag, not after it either."""
    samples = _samples(out, name)
    drag = [s for s in samples if s["label"] == "drag"]
    rest = [s for s in samples if s["label"] == "rest"]
    assert any(abs(s["north"]) > 0.2 for s in drag), [s["north"] for s in drag]
    left = drag[-1]["north"]
    for s in rest:
        assert abs(s["north"] - left) < 1e-9, s
        assert not s["animating"], s
        assert abs(_relative_to_north(s) - _wrap(CASES[name]["angle"])) < 1e-6, s


def test_levelling_turns_the_view_around_its_centre(out):
    """The centre does not move while the view is turned -- the frame stays on the sky."""
    samples = _samples(out, "level")
    centres = {(round(s["ra"], 9), round(s["dec"], 9)) for s in samples if s["label"] == "rest"}
    assert len(centres) == 1, centres


def test_no_levelling_while_the_sky_is_held(out):
    samples = _samples(out, "held")
    held = [s for s in samples if s["label"] == "held"]
    assert all(abs(s["north"]) > 0.5 for s in held), [s["north"] for s in held]
    assert all(abs(_relative_to_north(s) - 12) < 1e-6 for s in held)
    assert all(not s["animating"] for s in held)
    assert abs(samples[-1]["north"]) < 1e-9


def test_no_levelling_while_the_view_is_still_coasting(out):
    samples = _samples(out, "inertia")
    coasting = [s for s in samples if s["label"] == "inertia"]
    assert all(abs(s["north"]) > 0.5 for s in coasting)
    assert all(abs(_relative_to_north(s) - 12) < 1e-6 for s in coasting)
    assert abs(samples[-1]["north"]) < 1e-9


def test_the_straightening_takes_about_a_quarter_second_and_eases_out(out):
    samples = _samples(out, "level")
    rest = [s for s in samples if s["label"] == "rest"]
    runs = _animation(rest)
    assert len(runs) == 1, "expected exactly one straightening"
    run, end = runs[0]
    assert end is not None, "the straightening never finished"
    # About 250 ms from the frame that started it to the frame that ended it.
    assert 230 <= end["t"] - run[0]["t"] <= 290, (run[0]["t"], end["t"])
    # It starts from where the view was rolled and ends exactly at north-up. The frame
    # that armed the curve (run[0]) has not stepped yet, so it still holds the rolled
    # value: the curve is measured from there.
    from_deg = abs(run[0]["north"])
    assert from_deg > 0.2, from_deg
    assert abs(end["north"]) < 1e-9, end
    # Ease-out: every step is smaller than the one before it, and the whole way is
    # covered in many small steps -- no jump to north-up.
    path = [s["north"] for s in run] + [end["north"]]
    steps = [abs(b - a) for a, b in zip(path, path[1:])]
    assert len(steps) > 8, len(steps)
    assert max(steps) < 0.35 * from_deg, (max(steps), from_deg)
    assert all(b <= a + 1e-12 for a, b in zip(steps, steps[1:])), steps
    assert all(abs(s["north"]) <= from_deg + 1e-9 for s in run), [s["north"] for s in run]


def test_the_view_is_levelled_only_after_it_has_come_to_rest(out):
    """Release is not enough: the still frames come first, then the straightening."""
    samples = _samples(out, "level")
    rest = [s for s in samples if s["label"] == "rest"]
    assert not any(s["animating"] for s in rest[:STILL_FRAMES]), "levelled while the view was still moving"
    assert _animation(rest)[0][0][0] is rest[STILL_FRAMES], "the straightening did not start after the still frames"


def test_a_new_drag_cancels_the_straightening(out):
    samples = _samples(out, "cancel_midway")
    levelling = [s for s in samples if s["label"] == "levelling"]
    assert any(s["animating"] for s in levelling), "the straightening never started"
    held = [s for s in samples if s["label"] == "held"]
    assert all(not s["animating"] for s in held), "the straightening survived the new drag"
    # Wherever the curve had got to, that roll is simply kept: no snap, no further pull.
    assert all(abs(s["north"] - held[0]["north"]) < 1e-9 for s in held[1:]), [s["north"] for s in held]
    assert all(abs(_relative_to_north(s) - 12) < 1e-6 for s in held)
    # And once the view is at rest again, it is straightened again -- exactly.
    after = [s for s in samples if s["label"] == "after"]
    assert abs(after[-1]["north"]) < 1e-9, after[-1]
    assert abs(_wrap(after[-1]["frameRot"]) - 12) < 1e-6, after[-1]


def test_ticking_the_box_while_rolled_straightens_the_view_once(out):
    samples = _samples(out, "tick_while_rolled")
    rolled = [s for s in samples if s["label"] == "rolled"]
    assert not rolled[0]["box"], "the box was already ticked on a fresh page"
    assert all(not s["animating"] for s in rolled), "levelled before the box was ticked"
    assert abs(rolled[-1]["north"]) > 0.2, rolled[-1]
    box_at = [s for s in samples if s["label"] == "box"][0]
    assert box_at["box"], "the tick did not reach the box"
    assert abs(box_at["north"]) == abs(rolled[-1]["north"]), "the tick itself moved the view"
    levelling = [s for s in samples if s["label"] == "levelling"]
    runs = _animation(levelling)
    assert len(runs) == 1 and runs[0][0][0] is levelling[0], "the tick did not start one smooth straightening"
    assert abs(runs[0][1]["north"]) < 1e-9, runs[0][1]
    assert all(not s["animating"] for s in levelling if s["t"] > runs[0][1]["t"]), "it straightened twice"
    assert abs(samples[-1]["north"]) < 1e-9


def test_ticking_it_off_mid_curve_stops_where_the_view_stands(out):
    samples = _samples(out, "tick_off_midway")
    levelling = [s for s in samples if s["label"] == "levelling"]
    assert any(s["animating"] for s in levelling), "the straightening never started"
    after = [s for s in samples if s["label"] == "after"]
    assert all(not s["animating"] for s in after)
    assert abs(after[0]["north"]) > 0.2, after[0]          # still rolled, wherever it got to
    assert all(abs(s["north"] - after[0]["north"]) < 1e-9 for s in after), [s["north"] for s in after]
    assert all(abs(_relative_to_north(s) - 12) < 1e-6 for s in after)


def test_the_choice_is_remembered_in_local_storage(out):
    """One key, nova.framing.keepNorthUp: read at page load, written on every change."""
    # The stored value goes into the box at page load: the 'wired' sample is taken
    # right after the wiring, before anything else could have set it.
    assert _samples(out, "circles_30_37.5_on")[0]["box"] is True, "the stored choice was not read back"
    assert _samples(out, "circles_30_37.5_off")[0]["box"] is False, "a fresh page opened with the box on"
    # ... and it is what decides the behaviour: only the stored-on page levelled itself.
    assert _samples(out, "circles_30_37.5_on")[-1]["north"] == 0
    assert abs(_samples(out, "circles_30_37.5_off")[-1]["north"]) > 0.2, "a fresh page levelled without being asked"
    ticked = _case(out, "tick_while_rolled")["stored"]
    assert ticked == {STORAGE_KEY: "1"}, ticked
    unticked = _case(out, "tick_off_midway")["stored"]
    assert unticked == {STORAGE_KEY: "0"}, unticked
    assert _case(out, "circles_30_37.5_off")["stored"] == {}, "untouched storage was written to"


def test_at_the_pole_the_frame_angle_is_still_measured(out):
    for s in _framed(_samples(out, "pole")):
        assert abs(_relative_to_north(s) - 20) < 1e-3, s


def test_without_projection_calls_aladins_roll_angle_is_used(out):
    s = _samples(out, "fallback")[-1]
    assert abs(_wrap(s["frameRot"]) - 25) < 1e-9, s
