"""The Scan Frame button's blocked state, in node against a stand-in DOM.

The Framing tab used to explain a blocked Scan Frame with a separate
`#offline-scan-note` line under the controls, which went with the OFFLINE
section. The explanation is now the button's own `title`, which is only reachable
if the button is not switched off: a `disabled` control cannot be focused
(measured in Chromium and WebKit), so the text would be for nobody.

So this pins the two halves that make that work:
  * updateScanButtonState marks the button blocked with aria-disabled + a class
    and NEVER sets `disabled`, for all three blocked reasons;
  * scanFrameForDSOs refuses a blocked click rather than scanning;
  * the busy state is still a real `disabled`, and clearing returns the button
    to plain.
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
const [chartFile, outFile] = process.argv.slice(2);
const src = fs.readFileSync(chartFile, 'utf8');

function cut(source, start) {
    let i = source.indexOf('{', source.indexOf(')', start)), depth = 0;
    const stack = [];
    for (; i < source.length; i++) {
        const ch = source[i], nx = source[i + 1];
        if (stack[stack.length - 1] === 'tpl') {
            if (ch === '\\') { i++; continue; }
            if (ch === '`') { stack.pop(); continue; }
            if (ch === '$' && nx === '{') { stack.pop(); stack.push(depth); depth++; i++; }
            continue;
        }
        if (ch === '/' && nx === '/') { i = source.indexOf('\n', i); continue; }
        if (ch === '/' && nx === '*') { i = source.indexOf('*/', i) + 1; continue; }
        if (ch === '"' || ch === "'") { for (i++; source[i] !== ch; i++) if (source[i] === '\\') i++; continue; }
        if (ch === '`') { stack.push('tpl'); continue; }
        if (ch === '{') depth++;
        if (ch === '}') {
            depth--;
            if (stack.length && stack[stack.length - 1] === depth) { stack.pop(); stack.push('tpl'); continue; }
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error('unterminated declaration at ' + start);
}
function fn(source, name) {
    const hits = [...source.matchAll(new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(', 'g'))];
    if (hits.length !== 1) throw new Error(`${name}: ${hits.length} declarations`);
    return cut(source, hits[0].index);
}

// A stand-in button that records what the real code does to it.
function makeBtn(extra) {
    const attrs = {}, classes = new Set();
    return Object.assign({
        id: 'scan-frame-btn',
        title: '',
        disabled: false,
        dataset: {},
        textContent: 'Scan Frame',
        setAttribute(k, v) { attrs[k] = String(v); },
        getAttribute(k) { return k in attrs ? attrs[k] : null; },
        removeAttribute(k) { delete attrs[k]; },
        classList: {
            add(c) { classes.add(c); },
            remove(c) { classes.delete(c); },
            contains(c) { return classes.has(c); },
        },
        __attrs: attrs, __classes: classes,
    }, extra || {});
}

let btn = makeBtn();
let rigSel = { selectedIndex: 0, options: [{ value: '1', dataset: { fovw: '40', fovh: '30' } }] };
const fetchCalls = [];

const ctx = {
    console: { warn: () => {}, log: () => {}, error: () => {} },
    document: {
        getElementById: id => (id === 'scan-frame-btn' ? btn
            : id === 'framing-rig-select' ? rigSel
            : null),
    },
    window: {},
    fetch: (url) => { fetchCalls.push(url); return Promise.resolve({ json: () => Promise.resolve({ status: 'success', objects: [] }) }); },
};
// The English strings the template injects, so the button's text is asserted in
// words rather than in keys.
const STRINGS = {
    scan_frame: 'Scan Frame',
    scan_scanning: 'Scanning…',
    offline_scan_disabled: 'Scan Frame is unavailable in offline view.',
    scan_fov_too_large: 'FOV too large',
};
ctx.window.t = k => (STRINGS[k] !== undefined ? STRINGS[k] : k);
// a frame centre, so a scan that is NOT refused gets far enough to be noticed
ctx.frameCenter = () => ({ ra: 1, dec: 2 });
ctx.updateScreenFovOverlay = () => {};
ctx.novaAlert = async () => {};
ctx.A = { catalog: () => ({ removeAll() {}, addSources() {} }) };
ctx.aladin = { addCatalog() {} };
vm.createContext(ctx);

const api = vm.runInContext(`
    const MAX_SCAN_FOV_DEG = 8.0;
    let offlineViewOn = false;
    let scanFrameActive = false, scanResultsCatalog = null, scanResultsAll = [];
    function frameCenter() { return { ra: 1, dec: 2 }; }
    function applyMinSizeFilter() {}
    ${fn(src, 'setScanNotice')}
    ${fn(src, 'setScanButtonBlocked')}
    ${fn(src, 'updateScanButtonState')}
    ${fn(src, 'scanFrameForDSOs')}
    ({ updateScanButtonState, scanFrameForDSOs, setScanButtonBlocked,
       setOfflineView: v => { offlineViewOn = v; } })
`, ctx);

function snap() {
    return {
        disabled: btn.disabled,
        ariaDisabled: btn.getAttribute('aria-disabled'),
        blocked: btn.classList.contains('is-blocked'),
        title: btn.title,
    };
}

const out = {};

// 1. Offline view on -> blocked, never disabled
api.setOfflineView(true);
api.updateScanButtonState();
out.offline = snap();

// 2. Back online with a normal rig -> plain again
api.setOfflineView(false);
api.updateScanButtonState();
out.clear = snap();

// 3. A rig whose frame is too large for Scan Frame -> blocked with its own reason
rigSel = { selectedIndex: 0, options: [{ value: '1', dataset: { fovw: '600', fovh: '480' } }] };
api.updateScanButtonState();
out.tooLarge = snap();

// 4. No rig at all -> plain (nothing to explain)
rigSel = { selectedIndex: -1, options: [] };
api.updateScanButtonState();
out.noRig = snap();

// 5. A blocked click is refused: no fetch goes out
api.setOfflineView(true);
api.updateScanButtonState();
api.scanFrameForDSOs();
out.refusedClick = { fetchCalls: fetchCalls.slice(), text: btn.textContent, disabled: btn.disabled };

// 6. The busy state is still a real disabled, and it is cleared afterwards
api.setOfflineView(false);
rigSel = { selectedIndex: 0, options: [{ value: '1', dataset: { fovw: '40', fovh: '30' } }] };
api.updateScanButtonState();
out.beforeScan = snap();
api.scanFrameForDSOs();
out.duringScan = { disabled: btn.disabled, text: btn.textContent,
                   ariaDisabled: btn.getAttribute('aria-disabled'),
                   blocked: btn.classList.contains('is-blocked') };

setTimeout(() => {
    out.afterScan = { disabled: btn.disabled, text: btn.textContent,
                      ariaDisabled: btn.getAttribute('aria-disabled') };
    fs.writeFileSync(outFile, JSON.stringify(out));
}, 50);
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("scan_blocked")
    driver, result = tmp / "driver.cjs", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_offline_view_blocks_the_scan_button_without_disabling_it(out):
    """The whole point: the reason is readable, which needs a focusable control."""
    assert out["offline"] == {
        "disabled": False,
        "ariaDisabled": "true",
        "blocked": True,
        "title": "Scan Frame is unavailable in offline view.",
    }


def test_a_plain_state_clears_every_mark(out):
    assert out["clear"] == {"disabled": False, "ariaDisabled": None, "blocked": False, "title": ""}


def test_a_frame_too_large_blocks_with_its_own_reason(out):
    assert out["tooLarge"]["blocked"] is True
    assert out["tooLarge"]["disabled"] is False
    assert out["tooLarge"]["ariaDisabled"] == "true"
    assert out["tooLarge"]["title"] == "FOV too large"


def test_no_rig_leaves_the_button_plain(out):
    assert out["noRig"] == {"disabled": False, "ariaDisabled": None, "blocked": False, "title": ""}


def test_a_blocked_click_is_refused_and_starts_nothing(out):
    """Not `disabled`, so the click does arrive -- scanFrameForDSOs must refuse it
    rather than let the browser's default suppression be the thing that stops it."""
    assert out["refusedClick"]["fetchCalls"] == [], "a blocked click must not scan"
    assert out["refusedClick"]["text"] == "Scan Frame"
    assert out["refusedClick"]["disabled"] is False


def test_busy_is_still_a_real_disabled_state(out):
    """Scanning has no explanation to read, so it takes the control away properly."""
    assert out["duringScan"]["disabled"] is True
    assert out["duringScan"]["text"] == "Scanning…"
    assert out["afterScan"]["disabled"] is False
    assert out["afterScan"]["text"] == "Scan Frame"


def test_only_the_busy_state_switches_the_button_off():
    """Source check: inside setScanButtonBlocked / updateScanButtonState nothing
    sets `disabled = true`; the only place that does is the scanning branch of
    scanFrameForDSOs, which has no reason to explain."""
    src = CHART_JS.read_text(encoding="utf-8")
    setter = src[src.index("function setScanButtonBlocked("):src.index("function updateScanButtonState(")]
    decider = src[src.index("function updateScanButtonState("):src.index("function applyMinSizeFilter(")]
    assert "disabled = true" not in setter, "the setter must never disable"
    assert "disabled = true" not in decider, "the decider must never disable"
    scan = src[src.index("function scanFrameForDSOs("):]
    scan = scan[:scan.index("function ", 10)]
    assert "btn.disabled = true" in scan, "the busy state still disables"
