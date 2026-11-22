"""The Framing tab's lifecycle, in node.

graph_view.js: which tab a URL asks for (tabFromUrl), and showTab — unknown tabs fall back
to the chart, and the Framing tab's shown/hidden hooks fire once per change.
graph_view_chart.js: the hooks themselves — the first show opens the assistant exactly once
(framing URL state, else the saved framing, else defaults), later shows only resume, and
hiding stops the frame overlay loop and the offline panel. No keyboard nudging: the sky
moves only by dragging.

The functions are cut out of the real files by name and run against stand-ins.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GRAPH_VIEW_JS = REPO / "static" / "js" / "graph_view.js"
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
TEMPLATE = REPO / "templates" / "graph_view.html"
NODE = shutil.which("node")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [viewFile, chartFile, outFile] = process.argv.slice(2);
const viewSrc = fs.readFileSync(viewFile, 'utf8');
const chartSrc = fs.readFileSync(chartFile, 'utf8');

function cut(src, start) {
    let i = src.indexOf('{', src.indexOf(')', start)), depth = 0;
    const stack = [];
    for (; i < src.length; i++) {
        const ch = src[i], nx = src[i + 1];
        if (stack[stack.length - 1] === 'tpl') {
            if (ch === '\\') { i++; continue; }
            if (ch === '`') { stack.pop(); continue; }
            if (ch === '$' && nx === '{') { stack.pop(); stack.push(depth); depth++; i++; }
            continue;
        }
        if (ch === '/' && nx === '/') { i = src.indexOf('\n', i); continue; }
        if (ch === '/' && nx === '*') { i = src.indexOf('*/', i) + 1; continue; }
        if (ch === '"' || ch === "'") { for (i++; src[i] !== ch; i++) if (src[i] === '\\') i++; continue; }
        if (ch === '`') { stack.push('tpl'); continue; }
        if (ch === '{') depth++;
        if (ch === '}') {
            depth--;
            if (stack.length && stack[stack.length - 1] === depth) { stack.pop(); stack.push('tpl'); continue; }
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    throw new Error('unterminated declaration at ' + start);
}
function fn(src, name) {
    const hits = [...src.matchAll(new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(', 'g'))];
    if (hits.length !== 1) throw new Error(`${name}: ${hits.length} declarations`);
    return cut(src, hits[0].index);
}
function assigned(src, marker) {
    const at = src.indexOf(marker);
    if (at < 0 || src.indexOf(marker, at + 1) >= 0) throw new Error(`${marker}: not exactly one`);
    return cut(src, at) + ';';
}
const tick = () => new Promise(r => setImmediate(r));

class ClassList {
    constructor() { this.set = new Set(); }
    add(c) { this.set.add(c); } remove(c) { this.set.delete(c); } contains(c) { return this.set.has(c); }
}
function el(id, data) { return { id, classList: new ClassList(), dataset: data || {} }; }

// ---- graph_view.js --------------------------------------------------------
function viewContext(search) {
    const tabs = ['chart', 'notes', 'framing', 'journal'];
    const contents = tabs.map(t => el(t + '-tab'));
    const buttons = tabs.map(t => el('', { tab: t }));
    const calls = [];
    const ctx = {
        console: { log() {}, warn() {} }, URLSearchParams, JSON,
        window: {}, location: { search },
        localStorage: { getItem: () => null, setItem() {} },
        document: {
            getElementById: id => contents.find(c => c.id === id) || null,
            querySelector: sel => {
                const m = /^\.tab-button\[data-tab="([^"]*)"\]$/.exec(sel);
                return m ? buttons.find(b => b.dataset.tab === m[1]) || null : null;
            },
            querySelectorAll: sel => sel === '.tab-content' ? contents : sel === '.tab-button' ? buttons : [],
        },
        loadImagingOpportunities() {}, loadSimbadInfo() {},
    };
    ctx.window.location = ctx.location;
    ctx.window.novaFramingTab = { shown: () => calls.push('shown'), hidden: () => calls.push('hidden') };
    vm.createContext(ctx);
    vm.runInContext(`
        var objectName = 'M42', opportunitiesLoaded = false, simbadLoaded = false;
        ${fn(viewSrc, 'tabFromUrl')}
        var currentTab = null;
        ${assigned(viewSrc, 'window.showTab = function')}
        var showTab = window.showTab;
    `, ctx);
    const active = () => contents.filter(c => c.classList.contains('active')).map(c => c.id);
    return { ctx, calls, active };
}

// ---- graph_view_chart.js --------------------------------------------------
async function chartRun(search, saved, steps) {
    const log = [];
    const listeners = [];
    const framingTab = el('framing-tab');
    const ctx = {
        console: { log() {}, warn() {} }, URLSearchParams, JSON, Promise, encodeURIComponent,
        location: { search },
        NOVA_GRAPH_DATA: { objectName: 'M42' },
        requestAnimationFrame: f => { f(); return 1; },
        window: {
            addEventListener: (t, f) => { if (t === 'keydown') listeners.push(f); },
            removeEventListener: (t, f) => { const k = listeners.indexOf(f); if (k >= 0) listeners.splice(k, 1); },
        },
        document: { getElementById: id => id === 'framing-tab' ? framingTab : null, activeElement: { tagName: 'BODY' } },
        fetch: url => { log.push('fetch ' + url); return Promise.resolve({ json: () => Promise.resolve(saved) }); },
    };
    ctx.__log = s => log.push(s);
    vm.createContext(ctx);
    const api = vm.runInContext(`
        let aladin = null;
        function openFramingAssistant(q) { __log('open ' + JSON.stringify(q === undefined ? null : q)); aladin = { view: { fixLayoutDimensions() { __log('fixLayout'); } } }; }
        function updateFramingChart(r) { __log('updateFramingChart ' + r); }
        function startFrameOverlayLoop() { __log('startFrame'); }
        function stopFrameOverlayLoop() { __log('stopFrame'); }
        function updateScanButtonState() {}
        function setSavedFraming() {}
        ${fn(chartSrc, 'savedFramingQuery')}
        ${chartSrc.slice(chartSrc.indexOf('    let framingPageLoaded = false;'), chartSrc.indexOf('    function flipFraming90()'))}
        ({
            shown: onFramingTabShown, hidden: onFramingTabHidden,
            pageLoaded() { framingPageLoaded = true; if (isFramingTabVisible()) onFramingTabShown(); },
        })
    `, ctx);
    for (const step of steps) {
        if (step === 'activate') framingTab.classList.add('active');
        else if (step === 'deactivate') framingTab.classList.remove('active');
        else if (step === 'key') listeners.forEach(f => f({ key: 'ArrowUp', preventDefault() {} }));
        else api[step]();
        await tick();
        log.push('-- ' + step + ' (keys ' + listeners.length + ')');
    }
    return log;
}

(async () => {
    const out = {};
    out.tabFromUrl = {};
    for (const s of ['', '?tab=framing', '?tab=framing&openframing=1', '?tab=framing&subtab=notes',
                     '?rig=3&ra=83.8&dec=-5.4', '?openframing=1', '?tab=journal', '?tab=notes']) {
        out.tabFromUrl[s] = viewContext(s).ctx.tabFromUrl();
    }
    const v = viewContext('');
    v.ctx.showTab('chart'); v.ctx.showTab('framing'); v.ctx.showTab('framing');
    v.ctx.showTab('notes'); v.ctx.showTab('framing');
    out.hooks = v.calls.slice();
    v.ctx.showTab('no-such-tab');
    out.unknown = { active: v.active(), calls: v.calls.slice(out.hooks.length) };

    const saved = { status: 'found', rig: 35, rig_uid: 'u1', ra: 85.1, dec: -2.5, rotation: 243.5,
                    survey: 'P/DSS2/color', blend: '', blend_op: 0.16, mosaic_cols: 1, mosaic_rows: 1,
                    mosaic_overlap: 10, img_brightness: 0, img_contrast: 0, img_gamma: 1,
                    img_saturation: 0, geo_belt_enabled: true };
    out.beforeLoad = await chartRun('?tab=framing', saved, ['activate', 'shown', 'pageLoaded']);
    out.savedLifecycle = await chartRun('?tab=framing&openframing=1', saved,
        ['activate', 'pageLoaded', 'key', 'deactivate', 'hidden', 'key', 'activate', 'shown', 'key']);
    out.framingUrl = await chartRun('?rig=35&ra=85.1&dec=-2.5', saved, ['activate', 'pageLoaded']);
    out.noSaved = await chartRun('', { status: 'empty' }, ['pageLoaded', 'activate', 'shown']);
    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_tab")
    driver, result = tmp / "driver.cjs", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(GRAPH_VIEW_JS), str(CHART_JS), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


SAVED_QS = ("rig=35&rig_uid=u1&ra=85.1&dec=-2.5&rot=243.5&survey=P%2FDSS2%2Fcolor&blend_op=0.16"
            "&m_cols=1&m_rows=1&m_ov=10&geo_belt=1")


def test_tab_from_url(out):
    assert out["tabFromUrl"] == {
        "": None,
        "?tab=framing": "framing",
        "?tab=framing&openframing=1": "framing",
        "?tab=framing&subtab=notes": "notes",      # dashboard Notes cell
        "?rig=3&ra=83.8&dec=-5.4": "framing",      # framing URL without a tab
        "?openframing=1": "framing",
        "?tab=journal": "journal",
        "?tab=notes": "notes",
    }


def test_show_tab_fires_framing_hooks_once_per_change(out):
    # chart -> framing -> framing (no-op) -> notes -> framing
    assert out["hooks"] == ["shown", "hidden", "shown"]


def test_show_tab_unknown_tab_falls_back_to_chart(out):
    assert out["unknown"] == {"active": ["chart-tab"], "calls": ["hidden"]}


def test_first_show_waits_for_page_load_and_opens_once(out):
    log = out["beforeLoad"]
    assert [l for l in log if l.startswith("open")] == [f"open {json.dumps(SAVED_QS)}"]
    assert log.index("-- shown (keys 0)") < log.index(f"open {json.dumps(SAVED_QS)}")


def test_saved_framing_lifecycle(out):
    # no keydown listener at any point: arrow keys / WASD don't move the frame any more
    assert out["savedLifecycle"] == [
        "-- activate (keys 0)",
        "fetch /api/get_framing/M42",
        f"open {json.dumps(SAVED_QS)}",
        "-- pageLoaded (keys 0)",
        "-- key (keys 0)",
        "-- deactivate (keys 0)",
        "stopFrame",
        "-- hidden (keys 0)",
        "-- key (keys 0)",
        "-- activate (keys 0)",
        "fixLayout", "updateFramingChart false", "startFrame",
        "-- shown (keys 0)",
        "-- key (keys 0)",
    ]


def test_framing_url_opens_with_its_own_state_without_asking_for_the_saved_one(out):
    assert out["framingUrl"] == ["-- activate (keys 0)", "open null", "-- pageLoaded (keys 0)"]


def test_no_saved_framing_opens_defaults(out):
    assert out["noSaved"] == ["-- pageLoaded (keys 0)", "-- activate (keys 0)",
                              "fetch /api/get_framing/M42", 'open ""', "-- shown (keys 0)"]


def test_markup_framing_is_a_tab_not_a_modal():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'data-tab="notes" data-action="show-tab">{{ _(\'Notes\') }}' in html
    assert 'data-tab="framing" data-action="show-tab">{{ _(\'Framing\') }}' in html
    assert "Notes & Framing" not in html
    assert 'id="framing-modal' not in html and "close-framing-assistant" not in html
    framing = html[html.index('<div id="framing-tab" class="tab-content">'):]
    for marker in ('id="project-framing-buttons"', 'id="framing-panel"', 'id="aladin-lite-div"',
                   'id="ra-readout"', 'id="dec-readout"', 'id="result-angle"', 'id="scan-frame-btn"',
                   'id="offline-view-toggle"'):
        assert marker in framing, marker
    notes = html[html.index('<div id="notes-tab" class="tab-content">'):html.index('<div id="rigdata-tab"')]
    assert 'id="project-field-editor"' in notes and 'id="open-in-stellarium"' in notes
    # the Notes tab's "Show Framing" button is gone with its handler; the Framing tab
    # opens from the tab bar or a ?tab=framing / framing URL
    assert "open-framing-assistant" not in html and "Show Framing" not in html
    assert "open-framing-assistant" not in (REPO / "static" / "js" / "graph_view.js").read_text(encoding="utf-8")


def test_no_modal_machinery_left_in_the_chart():
    src = CHART_JS.read_text(encoding="utf-8")
    for gone in ("framingModalController", "ModalController", "framing-modal", "closeFramingAssistant",
                 "isFramingModalOpen", "get('openframing')"):
        assert gone not in src, gone


def _framing_markup():
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index('<div id="framing-tab" class="tab-content">')
    return html[start:html.index("<script src=", start)]


def test_panel_sections_in_order():
    framing = _framing_markup()
    sky, side = framing.index('class="framing-sky"'), framing.index('class="framing-side"')
    assert sky < side
    # the header: FRAMING + help, the Offline view switch, the Rig select, then the FOV chip
    head = ["framing-head", "help_badge('framing_assistant'", 'id="offline-view-toggle"',
            'id="framing-rig-select"', 'id="fov-vs-object"', 'id="framing-result"']
    at = [framing.index(m, side) for m in head]
    assert at == sorted(at), head
    order = ["framing-head", 'id="framing-result"', "{{ _('Frame') }}", "{{ _('Sky') }}",
             "{{ _('Position') }}", "{{ _('Scan') }}", "{{ _('New Target') }}",
             "{{ _('Actions') }}"]
    at = [framing.index(m, side) for m in order]
    assert at == sorted(at), order
    # Scan Frame's controls moved out of Actions into their own section; IDs unchanged.
    assert framing.index('id="scan-frame-btn"') > framing.index("{{ _('Scan') }}")
    assert framing.index('id="scan-frame-btn"') < framing.index("{{ _('Actions') }}")
    assert framing.index('id="scan-catalog-names"') < framing.index("{{ _('New Target') }}")
    # The OFFLINE section is gone with the save-for-offline UI
    assert "{{ _('Offline') }}" not in framing
    assert "framing-offline-slot" not in framing
    # Actions rows: Save Framing alone, then Open/Delete, then the copy actions.
    assert framing.index("{{ _('Actions') }}") < framing.index('id="save-framing-db"')
    assert framing.index('id="save-framing-db"') < framing.index('id="project-framing-buttons"')
    assert framing.index('id="project-framing-buttons"') < framing.index("{{ _('Copy framing URL') }}")
    # The saved-framing group is a row of its own, and disappears when empty.
    assert 'class="framing-buttons framing-buttons-inline"' in framing
    # Image adjustments is the only fold left, and it is folded by default
    assert framing.count('<details class="framing-fold">') == 1 and "<details open" not in framing


def test_removed_controls_are_gone():
    framing = _framing_markup()
    for gone in ('lock-to-object', 'toggle-lock-fov', 'nudge-fov', 'data-dx=', "(1′ {{ _('steps') }})",
                 'Click on the sky to move the FOV center', "Overlap %%", 'framing-controls',
                 'modal-header-bar'):
        assert gone not in framing, gone
    src = CHART_JS.read_text(encoding="utf-8")
    # no keydown handler left at all: the offline panel's capture-phase Escape
    # went with the panel
    assert "keydown" not in src
    for gone in ("offlineSavePanel", "closeOfflineSavePanel", "getOfflineSavePanel",
                 "offline-save-panel", "save-offline-btn"):
        assert gone not in src, gone
    for gone in ("lockToObject", "fovCenter", "applyLockToObject", "nudgeFov", "arrowup", "shiftKey",
                 "pix2world", "recenterViewWhenLocked", "useReadoutAsFovCenter", "drawFovFootprint",
                 "getFrameCenterRaDec", "LockOverlayLoop"):
        assert gone not in src, gone
    view_src = GRAPH_VIEW_JS.read_text(encoding="utf-8")
    for gone in ("toggle-lock-fov", "nudge-fov", "applyLockToObject", "nudgeFov"):
        assert gone not in view_src, gone


def test_framing_markup_has_no_inline_styles():
    assert "style=" not in _framing_markup()


def test_framing_code_sets_no_inline_styles():
    """Framing code sets classes and the hidden attribute; the only style writes are the
    custom properties for the frame overlay's and the scan popup's geometry."""
    import re
    src = CHART_JS.read_text(encoding="utf-8")
    start = src.index("    function framingFovDeg()")
    end = src.index("    // THEME INTEGRATION")
    framing = src[start:end]
    # the chart's own code in between (weather tooltip, clipboard fallback, secondary object)
    for skip in ("weatherTip.style", "ta.style", "secondaryControls.style"):
        framing = framing.replace(skip, "")
    writes = re.findall(r"\.style\.(?!setProperty)\w+\s*=|\.style\.cssText|\.style\[", framing)
    assert writes == []
    props = sorted(set(re.findall(r"style\.setProperty\('(--[\w-]+)'", framing)))
    assert props == ["--frame-rot", "--pane-h", "--pane-w", "--pane-x", "--pane-y", "--popup-x", "--popup-y"]


def test_framing_strings_are_translated():
    src = CHART_JS.read_text(encoding="utf-8")
    for literal in ("'Scan Frame'", "'Scanning…'", "FOV (Rig)", "Tap to reset", "`Rotation: ",
                    "Sending object to Stellarium", "Stellarium view updated", "Are you sure you want",
                    "Error deleting", "+ Add to Database", "'Loading…'"):
        assert literal not in src, literal
