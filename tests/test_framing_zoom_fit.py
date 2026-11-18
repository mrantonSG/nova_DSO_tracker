"""The Framing Assistant's zoom-to-fit: the whole frame, rotated, inside the view.

applyRigFovZoom is extracted from static/js/graph_view_chart.js verbatim and run in
node against a stand-in DOM and a stand-in Aladin that RECORDS the setFov argument.
That last part is the point of this file: tests/test_framing_reference_js.py runs the
same function under a setFov() no-op, because the frozen values it pins are the RA/Dec
readout, Copy RA/Dec, the Save payload, Copy URL and the CSV -- not the fit. The fit
has its own numbers, they move when the frame maths moves, and they are pinned here.

What is pinned:
  * a rotated frame needs the rotated bounding box, not the unrotated one
  * every pane of a mosaic is inside the view, at 0 deg and rotated
  * the fit and the overlay rectangle describe the same rectangle
  * the callers that open a saved framing, and that re-fit on resize, pass the
    rotation through (the regression: both used to default it to 0)

The last four tests read graph_view_chart.js as text. That is deliberate: the two
callers live inside a rAF/ResizeObserver block whose harness would be larger than
the behaviour it proves, and what went wrong there was an omitted argument.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHART = REPO / "static" / "js" / "graph_view_chart.js"
NODE = shutil.which("node")

# Pane sizes used throughout, in arcmin: a 40' x 30' pane, 4:3 like the real rigs.
PANE_W, PANE_H = 40.0, 30.0
MARGIN = 1.06
# The sky view's pixel box. Deliberately NOT 4:3 like the pane: at 4:3 the two
# terms of the fit coincide at 0 and 90 degrees, which would hide a swapped axis.
VIEW_W, VIEW_H = 1000, 600


@pytest.fixture(scope="module")
def chart_src():
    return CHART.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# extraction (same approach as test_framing_reference_js.py)
# ---------------------------------------------------------------------------

_DRIVER = r"""
const fs = require('fs');
const vm = require('vm');
const chartSrc = fs.readFileSync(process.argv[2], 'utf8');

function cut(src, start) {
    let depth = 0, i = start;
    for (; i < src.length; i++) {
        const ch = src[i], nx = src[i + 1];
        if (ch === '/' && nx === '/') { i = src.indexOf('\n', i); continue; }
        if (ch === '/' && nx === '*') { i = src.indexOf('*/', i) + 1; continue; }
        if (ch === '"' || ch === "'") { for (i++; src[i] !== ch; i++) if (src[i] === '\\') i++; continue; }
        if (ch === '`') { continue; }
        if (ch === '{') depth++;
        if (ch === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
    }
    throw new Error('unterminated declaration at ' + start);
}
function extractFn(name) {
    const re = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(', 'g');
    const hits = [...chartSrc.matchAll(re)];
    if (hits.length !== 1) throw new Error(name + ': ' + hits.length + ' declarations');
    return cut(chartSrc, hits[0].index);
}

// --- stand-in DOM: the ids the fit reads -----------------------------------
class El {
    constructor(id, tag = 'div') {
        this.id = id; this.tagName = tag.toUpperCase(); this._value = '';
        this.dataset = {}; this.children = [];
        this.style = { props: {}, setProperty(k, v) { this.props[k] = v; } };
        this.hidden = false; this.clientWidth = 0; this.clientHeight = 0;
    }
    get value() { return this._value; }
    set value(v) { this._value = String(v); }
    get selectedIndex() { return this._index === undefined ? -1 : this._index; }
    set selectedIndex(i) { this._index = i; }
    get options() { return this._options || []; }
    set options(o) { this._options = o; }
    appendChild(c) { this.children.push(c); return c; }
    removeChild(c) { this.children = this.children.filter(x => x !== c); return c; }
    setAttribute() {} removeAttribute() {}
}
function buildDom(state) {
    const els = {};
    const add = e => { els[e.id] = e; return e; };
    const rigSel = add(new El('framing-rig-select', 'select'));
    rigSel.options = [
        { value: '9999', dataset: { fovw: '60', fovh: '40' } },
        { value: '42', dataset: { fovw: String(state.paneW), fovh: String(state.paneH) } },
    ];
    rigSel.selectedIndex = 1;
    add(new El('mosaic-cols', 'input')).value = String(state.cols);
    add(new El('mosaic-rows', 'input')).value = String(state.rows);
    add(new El('mosaic-overlap', 'input')).value = String(state.overlapPct);
    const host = add(new El('aladin-lite-div'));
    host.clientWidth = state.wpx; host.clientHeight = state.hpx;
    // the overlay's own DOM: the fit calls updateScreenFovOverlay, which reads these
    const rect = add(new El('screen-fov-rect'));
    add(new El('framing-rotation', 'input')).value = String(state.rotation);
    return { els, host, rect };
}

function run() {
    const state = JSON.parse(process.argv[3]);
    const { els } = buildDom(state);

    const setFovCalls = [];
    let currentFov = 1.0;
    const aladin = {
        setFov(v) { setFovCalls.push(v); currentFov = v; },
        getFov() { return [currentFov, currentFov * (state.hpx / state.wpx)]; },
        getRaDec() { return [0, 0]; },
    };
    const ctx = {
        console, Math, Number, String, Array, Object, JSON, isFinite, parseFloat, parseInt,
        document: {
            getElementById: id => els[id] || null,
            createElement: () => new El(''),
        },
    };
    ctx.window = ctx;
    ctx.aladin = aladin;
    vm.createContext(ctx);
    // the two helpers the fit uses, verbatim, plus the fit itself
    vm.runInContext([
        'let aladin = null;',
        extractFn('frameExtentDeg'),
        extractFn('updateScreenFovOverlay'),
        extractFn('applyRigFovZoom'),
        'aladin = window.__aladin;',
        'window.__fit = applyRigFovZoom;',
        'window.__extent = frameExtentDeg;',
        'window.__overlay = updateScreenFovOverlay;',
    ].join('\n'), ctx);
    ctx.window.__aladin = aladin;
    vm.runInContext('aladin = window.__aladin; window.__fit(' + JSON.stringify(state.paneW) +
                    ', ' + JSON.stringify(state.paneH) + ', ' + JSON.stringify(state.rotation) + ', ' +
                    JSON.stringify(state.margin) + ');', ctx);
    const rectStyle = els['screen-fov-rect'] ? els['screen-fov-rect'].style.props : {};
    return { setFov: setFovCalls, rect: { rot: rectStyle['--frame-rot'], paneW: rectStyle['--pane-w'] } };
}
process.stdout.write(JSON.stringify(run()));
"""


def _fit(tmp_path, *, pane_w=PANE_W, pane_h=PANE_H, rotation=0.0, cols=1, rows=1,
         overlap_pct=10.0, wpx=VIEW_W, hpx=VIEW_H, margin=MARGIN):
    driver = tmp_path / "fit_driver.js"
    driver.write_text(_DRIVER, encoding="utf-8")
    state = {"paneW": pane_w, "paneH": pane_h, "rotation": rotation,
             "cols": cols, "rows": rows, "overlapPct": overlap_pct,
             "wpx": wpx, "hpx": hpx, "margin": margin}
    proc = subprocess.run([NODE, str(driver), str(CHART), json.dumps(state)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


pytestmark_node = pytest.mark.skipif(NODE is None, reason="node is required")


# ---------------------------------------------------------------------------
# the maths
# ---------------------------------------------------------------------------

def _expected_fit(pane_w, pane_h, rotation, cols, rows, overlap_pct, wpx, hpx, margin):
    """The fit independently: extent -> rotated bbox -> the taller axis in FOV width."""
    import math
    ov = overlap_pct / 100.0
    fw = pane_w / 60.0 * (1 + (cols - 1) * (1 - ov))
    fh = pane_h / 60.0 * (1 + (rows - 1) * (1 - ov))
    th = math.radians(rotation)
    need_w = abs(fw * math.cos(th)) + abs(fh * math.sin(th))
    need_h = abs(fw * math.sin(th)) + abs(fh * math.cos(th))
    return max(need_w * margin, need_h * margin * (wpx / hpx))


@pytestmark_node
def test_unrotated_single_frame_fits_with_the_margin(tmp_path):
    out = _fit(tmp_path, rotation=0.0)
    got = out["setFov"][-1]
    assert got == pytest.approx(_expected_fit(PANE_W, PANE_H, 0.0, 1, 1, 10.0, VIEW_W, VIEW_H, MARGIN))
    # 40' x 30' = 0.6667 x 0.5 deg; at 0 deg the height term binds:
    # 0.5 * margin * aspect = 0.8833, against 0.6667 * margin = 0.7067
    assert got == pytest.approx(0.5 * MARGIN * (VIEW_W / VIEW_H), rel=1e-6)
    assert got == pytest.approx(0.8833333333, rel=1e-9)


@pytestmark_node
def test_rotated_frame_needs_the_rotated_bounding_box(tmp_path):
    """The regression: a rotated frame was fit as if it were not rotated."""
    unrotated = _fit(tmp_path, rotation=0.0)["setFov"][-1]
    rotated = _fit(tmp_path, rotation=90.0)["setFov"][-1]
    # 90 deg swaps the axes: the width term (0.6667 deg, unrotated) becomes the
    # height term, so the fit opens up to 0.6667 * margin * aspect = 0.9422
    assert rotated == pytest.approx(_expected_fit(PANE_W, PANE_H, 90.0, 1, 1, 10.0, VIEW_W, VIEW_H, MARGIN))
    assert rotated != pytest.approx(unrotated)
    assert unrotated == pytest.approx(0.8833333333, rel=1e-9)
    assert rotated == pytest.approx(1.1777777778, rel=1e-9)


@pytestmark_node
@pytest.mark.parametrize("rotation", [0.0, 45.0, 133.0, 217.5, 315.0])
def test_the_whole_frame_is_inside_the_view_at_any_rotation(tmp_path, rotation):
    """M106's 133 deg is the case that used to run past the edge: check the corners."""
    import math
    out = _fit(tmp_path, rotation=rotation)
    fov_w = out["setFov"][-1]
    fov_h = fov_w * (VIEW_H / VIEW_W)
    ov = 0.10
    fw = PANE_W / 60.0
    fh = PANE_H / 60.0
    th = math.radians(rotation)
    # every corner of the frame, as an offset from the centre, in degrees
    corners = [(sx * fw / 2, sy * fh / 2) for sx in (-1, 1) for sy in (-1, 1)]
    for x, y in corners:
        rx = x * math.cos(th) - y * math.sin(th)
        ry = x * math.sin(th) + y * math.cos(th)
        assert abs(rx) <= fov_w / 2 + 1e-9, f"corner off the left/right edge at {rotation} deg"
        assert abs(ry) <= fov_h / 2 + 1e-9, f"corner off the top/bottom edge at {rotation} deg"


@pytestmark_node
def test_a_mosaic_fits_every_pane(tmp_path):
    """SH 2-240's 2x2: the fit used to size the view to one pane."""
    single = _fit(tmp_path, cols=1, rows=1)["setFov"][-1]
    mosaic = _fit(tmp_path, cols=2, rows=2)["setFov"][-1]
    assert mosaic == pytest.approx(_expected_fit(PANE_W, PANE_H, 0.0, 2, 2, 10.0, VIEW_W, VIEW_H, MARGIN))
    assert mosaic > single


@pytestmark_node
def test_a_rotated_mosaic_fits_every_pane(tmp_path):
    import math
    out = _fit(tmp_path, cols=2, rows=2, rotation=133.0)
    fov_w = out["setFov"][-1]
    fov_h = fov_w * (VIEW_H / VIEW_W)
    ov = 0.10
    # three panes of the 2x2, at their own centres (step = pane * (1 - overlap))
    step_w = PANE_W / 60.0 * (1 - ov)
    step_h = PANE_H / 60.0 * (1 - ov)
    th = math.radians(133.0)
    centres = [(-step_w / 2, -step_h / 2), (step_w / 2, -step_h / 2), (-step_w / 2, step_h / 2), (step_w / 2, step_h / 2)]
    for cx, cy in centres:
        for sx in (-1, 1):
            for sy in (-1, 1):
                x = cx + sx * PANE_W / 60.0 / 2
                y = cy + sy * PANE_H / 60.0 / 2
                rx = x * math.cos(th) - y * math.sin(th)
                ry = x * math.sin(th) + y * math.cos(th)
                assert abs(rx) <= fov_w / 2 + 1e-9
                assert abs(ry) <= fov_h / 2 + 1e-9


@pytestmark_node
@pytest.mark.parametrize("overlap_pct,cols,rows", [(10.0, 3, 1), (0.0, 2, 2), (50.0, 2, 2)])
def test_overlap_shrinks_the_fitted_frame(tmp_path, overlap_pct, cols, rows):
    out = _fit(tmp_path, cols=cols, rows=rows, overlap_pct=overlap_pct)
    assert out["setFov"][-1] == pytest.approx(
        _expected_fit(PANE_W, PANE_H, 0.0, cols, rows, overlap_pct, VIEW_W, VIEW_H, MARGIN))


@pytestmark_node
def test_a_tall_view_fits_the_height_instead(tmp_path):
    """The fit must turn the frame into a FOV *width*; a tall view binds on height."""
    wide = _fit(tmp_path, wpx=1200, hpx=400)["setFov"][-1]
    tall = _fit(tmp_path, wpx=400, hpx=1200)["setFov"][-1]
    assert wide == pytest.approx(_expected_fit(PANE_W, PANE_H, 0.0, 1, 1, 10.0, 1200, 400, MARGIN))
    assert tall == pytest.approx(_expected_fit(PANE_W, PANE_H, 0.0, 1, 1, 10.0, 400, 1200, MARGIN))
    # the 1200x400 view is height-bound (1.59 deg wide), the 400x1200 one is
    # width-bound (0.7067): a tall view already has the vertical room
    assert wide == pytest.approx(1.59, rel=1e-9)
    assert tall == pytest.approx(0.7066666667, rel=1e-9)
    # and in both, getFov()'s height = width * (h/w) holds the 0.5 deg frame
    for fov_w, w, h in ((wide, 1200, 400), (tall, 400, 1200)):
        assert fov_w >= PANE_W / 60.0
        assert fov_w * (h / w) >= PANE_H / 60.0


@pytestmark_node
def test_the_fit_and_the_overlay_describe_the_same_rectangle(tmp_path):
    """One frame source: what is zoomed to and what is drawn are the same rectangle."""
    out = _fit(tmp_path, cols=2, rows=1, rotation=45.0)
    assert out["setFov"], "the fit ran"
    # the overlay records its own rotation; the fit passes the same angle through
    assert out["rect"]["rot"] == "45deg"


@pytestmark_node
def test_frame_extent_is_the_whole_mosaic_not_one_pane(tmp_path):
    """The fit sizes the whole mosaic: one pane grown by every further pane."""
    out = _fit(tmp_path, cols=3, rows=2, overlap_pct=25.0)
    # frameExtentDeg: wDeg * (1 + (cols-1)*(1-overlap))
    ov = 0.25
    want_w = PANE_W / 60.0 * (1 + (3 - 1) * (1 - ov))
    want_h = PANE_H / 60.0 * (1 + (2 - 1) * (1 - ov))
    assert out["setFov"][-1] == pytest.approx(
        max(want_w * MARGIN, want_h * MARGIN * (VIEW_W / VIEW_H)), rel=1e-9)


# ---------------------------------------------------------------------------
# the callers (read as text: the defect was an omitted argument)
# ---------------------------------------------------------------------------

def test_open_with_a_saved_centre_passes_the_rotation(chart_src):
    """Opening a saved framing used to re-fit with rotation defaulted to 0."""
    at = chart_src.index("// A saved centre: the view goes there")
    block = chart_src[at:at + 700]
    assert "parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0" in block, \
        "the open path must read the rotation slider"
    assert re.search(r"applyRigFovZoom\(opt\.dataset\.fovw, opt\.dataset\.fovh, rot\)", block), \
        "the open path must pass the rotation to the fit"


def test_resize_re_fits_with_the_rotation(chart_src):
    at = chart_src.index("new ResizeObserver")
    block = chart_src[at:at + 700]
    assert "framing-rotation" in block, "the resize handler must read the rotation slider"
    assert re.search(r"applyRigFovZoom\(opt\.dataset\.fovw, opt\.dataset\.fovh, rot\)", block), \
        "the resize handler must pass the rotation to the fit"


def test_the_fit_grows_the_frame_by_the_mosaic(chart_src):
    """applyRigFovZoom must size the whole mosaic, not one pane."""
    body = _extract_fn_text(chart_src, "applyRigFovZoom")
    assert "frameExtentDeg" in body, "the fit must go through the frame-extent helper"
    assert "mosaic-cols" in body, "the fit must read the mosaic inputs"
    extent = _extract_fn_text(chart_src, "frameExtentDeg")
    assert "(c - 1) * (1 - ov)" in extent and "(r - 1) * (1 - ov)" in extent
    assert "parseInt(cols, 10) || 1" in extent, "an empty/NaN cols must fall back to 1"


def _extract_fn_text(src, name):
    hits = [m for m in re.finditer(r"(?:async\s+)?function\s+" + name + r"\s*\(", src)]
    assert len(hits) == 1, f"{name}: {len(hits)} declarations"
    start = hits[0].start()
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unterminated " + name)
