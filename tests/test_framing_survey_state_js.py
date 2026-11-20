"""Survey loading in the Framing tab, end to end in node.

One rule: tiles always come from disk first; online, only missing tiles are
fetched; Offline view only stops the network. A tile that cannot be fetched
stays empty. The server always answers a survey's properties (the built-in copy
when none is on disk), so a layer always builds. On the client that means:

- picking a survey always builds it on the root the current mode gives it
  (/hips/<slug> online, /hips/offline/<slug> in Offline view) -- no pre-flight
  request, no stored-copy shortcut, no fallback survey, no notice;
- switching Offline view rebuilds both layers on the new root, whatever they
  show at that moment;
- only the browser's offline/online events switch Offline view automatically;
- a blend taken off (or replaced) while it loads is dropped when it lands.

The harness cuts the real functions out of graph_view_chart.js -- every top-level
function and variable reachable from the entry points below -- and runs them
against stand-ins: a fake Aladin (a sequential layer queue like Aladin's own,
success/error callbacks, its "empty view" fallback), a fake fetch that records
any request the client makes on its own, and a minimal DOM. framing-utils.js is
loaded whole.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
UTILS_JS = REPO / "static" / "js" / "framing-utils.js"
NODE = shutil.which("node")

DSS = "P/DSS2/color"
DSS_RED = "P/DSS2/red"
PS = "P/PanSTARRS/DR1/color-z-zg-g"
GALEX = "P/GALEXGR6/AIS/color"
RGB = "https://www.simg.de/nebulae3/dr0_2/rgb8"
HA = "https://www.simg.de/nebulae3/dr0_2/halpha8"

SURVEYS = {  # value -> (slug, label, in base select, in blend select)
    DSS: ("dss2-color", "DSS2 (color)", True, True),
    DSS_RED: ("dss2-red", "DSS2 (red)", False, True),
    PS: ("panstarrs-dr1-color", "Pan-STARRS (color)", True, False),
    GALEX: ("galex-ais-color", "GALEX (color)", True, False),
    RGB: ("nsns-rgb8", "NSNS DR0.2 (True Color)", True, True),
    HA: ("nsns-halpha8", "NSNS DR0.2 (H-alpha)", True, True),
}

I18N = {
    "survey_server_unreachable": "Sky survey server unreachable.",
    "close": "Close",
}

# Entry points the harness drives; everything they reach is cut from the file.
ENTRY = ["setSurvey", "ensureBlendLayer", "setBlendOpacity", "updateSurveyCredit",
         "setOfflineView", "applyOfflineViewToLayers", "wireOfflineSkyUi",
         "showSurveyNotice", "hideSurveyNotice"]
# Optional entry points (present once the fix is in; absent on the old code).
OPTIONAL = ["guardAladinFallback", "createSurveyLayer"]
# Collaborators outside this feature: stand-ins.
STUBS = ["updateScanButtonState", "updateFramingChart", "novaAlert", "framingFovDeg"]

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [chartFile, utilsFile, inFile, outFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const src = fs.readFileSync(chartFile, 'utf8');
const utilsSrc = fs.readFileSync(utilsFile, 'utf8');

// --- Cutting the real code -------------------------------------------------
// Scan from `start` to the end of one declaration: the closing brace of a
// function body, or the `;` ending a statement, at bracket depth 0. Strings,
// comments and template literals are skipped.
function scanEnd(source, start, mode) {
    let depth = 0, i = start;
    if (mode === 'fn') i = source.indexOf('{', source.indexOf(')', start));
    const stack = [];
    for (; i < source.length; i++) {
        const ch = source[i], nx = source[i + 1];
        if (stack[stack.length - 1] === 'tpl') {
            if (ch === '\\') { i++; continue; }
            if (ch === '`') { stack.pop(); continue; }
            if (ch === '$' && nx === '{') { stack.pop(); stack.push(depth); depth++; i++; }
            continue;
        }
        if (ch === '/' && nx === '/') { i = source.indexOf('\n', i) - 1; continue; }
        if (ch === '/' && nx === '*') { i = source.indexOf('*/', i) + 1; continue; }
        if (ch === '"' || ch === "'") { for (i++; source[i] !== ch; i++) if (source[i] === '\\') i++; continue; }
        if (ch === '`') { stack.push('tpl'); continue; }
        if (ch === '{' || ch === '(' || ch === '[') depth++;
        if (ch === '}' || ch === ')' || ch === ']') {
            depth--;
            if (ch === '}' && stack.length && stack[stack.length - 1] === depth) { stack.pop(); stack.push('tpl'); continue; }
            if (mode === 'fn' && depth === 0) return i + 1;
        }
        if (mode === 'stmt' && ch === ';' && depth === 0) return i + 1;
    }
    throw new Error('unterminated declaration at ' + start);
}
// Every top-level (4-space) function and let/const of the IIFE.
const decls = new Map();
for (const m of src.matchAll(/\n    (?:async\s+)?function\s+(\w+)\s*\(/g)) {
    const start = m.index + 1;
    if (decls.has(m[1])) throw new Error('duplicate function ' + m[1]);
    decls.set(m[1], src.slice(start, scanEnd(src, start, 'fn')));
}
for (const m of src.matchAll(/\n    (?:let|const)\s+(\w+)\s*=/g)) {
    const start = m.index + 1;
    if (!decls.has(m[1])) decls.set(m[1], src.slice(start, scanEnd(src, start, 'stmt')));
}
// `let a = 1, b = 2;` style lines declare several names: map each to the line.
for (const m of src.matchAll(/\n    let\s+(\w+)\s*=[^\n;]*,\s*(\w+)\s*=/g)) {
    if (!decls.has(m[2])) decls.set(m[2], decls.get(m[1]));
}
const stripComments = s => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
const stubs = new Set(input.stubs);
const wanted = new Set();
const queue = [...input.entry, ...input.optional.filter(n => decls.has(n))];
for (const n of input.entry) if (!decls.has(n)) throw new Error('missing entry point ' + n);
while (queue.length) {
    const n = queue.shift();
    if (wanted.has(n) || stubs.has(n) || !decls.has(n)) continue;
    wanted.add(n);
    for (const id of stripComments(decls.get(n)).match(/\b[A-Za-z_$][\w$]*\b/g) || []) {
        if (!wanted.has(id) && decls.has(id) && !stubs.has(id)) queue.push(id);
    }
}
// Keep file order (consts before the functions that read them at call time).
const seen = new Set();
const body = [...decls.entries()]
    .filter(([n, code]) => wanted.has(n) && !seen.has(code) && seen.add(code))
    .sort((a, b) => src.indexOf(a[1]) - src.indexOf(b[1]))
    .map(([, code]) => code).join('\n');

// --- The minimal DOM ---------------------------------------------------------
function makeDom() {
    const all = [];
    function el(tag, props) {
        const e = {
            tagName: tag, id: '', className: '', hidden: false, children: [], dataset: {},
            _text: '', _listeners: {}, attrs: {},
            setAttribute(k, v) { this.attrs[k] = v; },
            removeAttribute(k) { delete this.attrs[k]; },
            append(...k) { k.forEach(c => this.children.push(c)); },
            appendChild(c) { this.children.push(c); return c; },
            replaceChildren(...k) { this.children = k; this._text = ''; },
            remove() {},
            addEventListener(ev, f) { (this._listeners[ev] = this._listeners[ev] || []).push(f); },
            querySelector(sel) {
                const cls = sel.startsWith('.') ? sel.slice(1) : null;
                const walk = n => { for (const c of n.children || []) { if (cls && c.className === cls) return c; const r = walk(c); if (r) return r; } return null; };
                return walk(this);
            },
            classList: { add() {}, remove() {} },
            get textContent() { return this._text + (this.children || []).map(c => c.textContent || '').join(''); },
            set textContent(v) { this._text = String(v); this.children = []; },
        };
        Object.assign(e, props || {});
        all.push(e);
        return e;
    }
    function select(id, values) {
        const options = values.map(v => ({ value: v, textContent: input.surveys[v][1], dataset: {} }));
        let value = values[0];
        return el('select', {
            id, options,
            get value() { return value; },
            set value(v) { value = options.some(o => o.value === v) ? v : ''; },
            get selectedIndex() { return options.findIndex(o => o.value === value); },
        });
    }
    const base = Object.keys(input.surveys).filter(v => input.surveys[v][2]);
    const blend = Object.keys(input.surveys).filter(v => input.surveys[v][3]);
    const host = el('div', { id: 'aladin-lite-div' });
    const sSel = select('survey-select', base);
    const bSel = select('blend-survey-select', blend);
    const op = el('input', { id: 'blend-opacity', value: '0' });
    // The image-adjustment sliders, at their template defaults.
    el('input', { id: 'img-bright', value: '0' });
    el('input', { id: 'img-contrast', value: '0' });
    el('input', { id: 'img-gamma', value: '1' });
    el('input', { id: 'img-sat', value: '0' });
    const toggle = el('input', { id: 'offline-view-toggle', checked: false });
    const document = {
        getElementById: id => all.find(e => e.id === id) || null,
        querySelector(sel) {
            const m = sel.match(/^#([\w-]+) option\[value='(.*)'\]$/);
            if (!m) return null;
            const s = all.find(e => e.id === m[1]);
            return s && s.options ? s.options.find(o => o.value === m[2]) || null : null;
        },
        createElement: tag => el(tag),
        createTextNode: t => ({ textContent: String(t) }),
    };
    return { document, host, sSel, bSel, op, toggle };
}

// --- One case ------------------------------------------------------------------
const sleep = ms => new Promise(r => setTimeout(r, ms));
const slugOf = v => input.surveys[v][0];

async function runCase(c) {
    const dom = makeDom();
    const loadFail = new Set(c.loadFail || []);      // slugs whose Aladin load fails
    const loadDelay = c.loadDelay || {};
    const fetches = [], created = [], fallback = [], snaps = {};
    const listeners = {};

    const parse = url => {
        const m = String(url).match(/\/hips\/(offline\/)?([\w-]+)/);
        return m ? { offline: !!m[1], slug: m[2] } : null;
    };

    // The fake Aladin: layers are added one after another (Aladin awaits every
    // queued layer before adding the next), a failed layer never replaces the one
    // shown, and an Aladin view that never got a layer falls back to alaskybis.
    //
    // Aladin's own order when a layer loads (View.addImageLayer): the renderer
    // gets the new layer in its slot (wasm.addHiPS), then the layer's
    // successCallback runs, and only then is the layer put in Aladin's layer list
    // (_addLayer). getImageLayer(name) returns the listed layer before the one
    // being queried. Every setter on a layer (setOpacity, setBrightness, ...) ends
    // in _updateMetadata, which writes that layer's metadata -- its tile format
    // included -- into the renderer slot it was added to.
    const screen = { base: null, blend: null };      // Aladin's layer list
    const queried = {};                              // imageLayersBeingQueried
    const renderer = {};                             // slot -> tile format the renderer uses
    let chain = Promise.resolve(), everAdded = false;
    function queueLayer(slot, layer) {
        chain = chain.then(async () => {
            const p = parse(layer.url) || {};
            await sleep(loadDelay[p.slug] || 2);
            const o = layer.opts || {};
            // Aladin skips /properties only when it has every value it would read
            // there, and only for a frame name it parses (z.fromString: icrs, j2000,
            // gal -- not 'equatorial').
            const frameKnown = /^\s*(icrs|j2000|gal)/i.test(o.cooFrame || layer.frame || '');
            const propertyLess = !!(o.imgFormat && o.tileSize && layer.maxOrder && frameKnown);
            // The server always answers a survey's properties: only an Aladin-side
            // failure (loadFail) keeps a layer from loading.
            const ok = propertyLess || !loadFail.has(p.slug);
            if (ok) {
                layer.layer = slot; layer.added = true;
                renderer[slot] = layer.fmt;                       // wasm.addHiPS
                if (o.successCallback) o.successCallback(layer);
                screen[slot] = layer; everAdded = true;           // _addLayer
                if (queried[slot] === layer) delete queried[slot];
            } else {
                if (queried[slot] === layer) delete queried[slot];
                if (o.errorCallback) o.errorCallback(new Error('layer failed'));
                if (!everAdded && slot === 'base') aladin.setBaseImageLayer('https://alaskybis.cds.unistra.fr/DSS/DSSColor/');
            }
        });
    }
    const aladin = {
        createImageSurvey(id, name, url, frame, maxOrder, opts) {
            const o = opts || {};
            const fmt = o.imgFormat || (/\/nsns-/.test(url) ? 'png' : 'jpeg');
            const meta = function () { if (this.added) renderer[this.layer] = this.fmt; };
            const layer = { id, name, url, frame, maxOrder, opts: o, fmt, added: false, layer: null,
                            setOpacity(v) { this.opacity = v; meta.call(this); },
                            setBrightness: meta, setContrast: meta, setGamma: meta, setSaturation: meta };
            created.push(layer);
            return layer;
        },
        setBaseImageLayer(layer) {
            if (typeof layer === 'string') { fallback.push(layer); return null; }
            queried.base = layer; queueLayer('base', layer); return layer;
        },
        setOverlayImageLayer(layer, name) { queried[name || 'overlay'] = layer; queueLayer(name || 'overlay', layer); return layer; },
        getBaseImageLayer: () => screen.base || queried.base || null,
        getRaDec: () => c.centre || [94.27, 22.52],
        getOverlayImageLayer: name => screen[name || 'overlay'] || queried[name || 'overlay'] || null,
        removeImageLayer(name) { screen[name] = null; },
    };

    const navigator = { onLine: c.browserOnline !== false };
    // The rig select and the page's rig list (image scale in arcsec/px).
    dom.rigSel = { id: 'framing-rig-select', options: [{ value: '7', dataset: {} }], selectedIndex: 0, value: '7' };
    const getEl = dom.document.getElementById;
    dom.document.getElementById = id => (id === 'framing-rig-select' ? dom.rigSel : getEl(id));
    const window = {
        location: { origin: 'http://nova.test' },
        availableRigs: c.rigScale === undefined ? [] : [{ rig_id: 7, image_scale: c.rigScale }],
        NOVA_OFFLINE_SKY: true,
        NOVA_OFFLINE_SKY_UI: true,
        NOVA_OFFLINE_SKY_SURVEYS: {},
        addEventListener(ev, f) { (listeners[ev] = listeners[ev] || []).push(f); },
        t: (k, params) => {
            const s = input.i18n[k];
            if (s === undefined) return k;
            return params ? Object.entries(params).reduce((a, [p, v]) => a.split('{' + p + '}').join(v), s) : s;
        },
    };
    for (const [v, [slug, label]] of Object.entries(input.surveys)) {
        window.NOVA_OFFLINE_SKY_SURVEYS[v] = { root: '/hips/' + slug, slug, maxOrder: 9, label };
    }
    const ctx = {
        window, navigator, document: dom.document, URL, Promise, Map, Set, WeakMap, JSON, Object, Array,
        Number, String, Error, RegExp, Math, Date, Symbol, Boolean, Event: class { constructor(t) { this.type = t; } },
        console: { warn: () => {}, log: () => {}, error: () => {}, info: () => {} },
        AbortController: class { constructor() { this.signal = {}; } abort() {} },
        setTimeout, clearTimeout,
        // Any request the client makes on its own (a pre-flight would be one).
        fetch: async (url) => {
            fetches.push(String(url).replace('http://nova.test', ''));
            return { ok: true, status: 200 };
        },
        sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    };
    ctx.window.document = ctx.document;
    vm.createContext(ctx);
    vm.runInContext(utilsSrc, ctx);

    const stubCode = input.stubs.map(n => `function ${n}() {}`).join('\n');
    const api = vm.runInContext(`
        ${stubCode}
        ${body}
        aladin = __aladin;
        if (typeof guardAladinFallback === 'function') guardAladinFallback(aladin);
        ({
            setSurvey, ensureBlendLayer, setBlendOpacity, updateSurveyCredit,
            setOfflineView, wireOfflineSkyUi,
            createSurveyLayer: typeof createSurveyLayer === 'function' ? createSurveyLayer : null,
            resolveHipsId,
            offlineViewOn: () => offlineViewOn,
        })
    `, Object.assign(ctx, { __aladin: aladin }));

    const rel = l => (l ? String(l.url).replace('http://nova.test', '') : null);
    const noticeText = () => {
        const n = dom.document.getElementById('survey-notice');
        if (!n || n.hidden) return null;
        const t = n.querySelector('.survey-notice-text');
        return t ? t.textContent : null;
    };
    const snap = () => ({
        base: rel(screen.base), blend: rel(screen.blend),
        // The tile format the renderer uses for each slot, against the format of
        // the layer Aladin lists there.
        rendererBase: renderer.base || null, baseFormat: screen.base ? screen.base.fmt : null,
        rendererBlend: renderer.blend || null, blendFormat: screen.blend ? screen.blend.fmt : null,
        select: dom.sSel.value, blendSelect: dom.bSel.value,
        notice: noticeText(),
        credit: (dom.document.getElementById('survey-credit') || { textContent: null }).textContent,
        offlineView: api.offlineViewOn(), checked: dom.toggle.checked,
        blendLayersCreated: created.filter(l => l.opts && l.opts.__slot === 'blend' || l.__slot === 'blend').length,
        created: created.map(rel), fetches: fetches.slice(),
        fallback: fallback.slice(),
    });

    const pending = [];
    for (const step of c.steps) {
        if (step.do === 'pick') { dom.sSel.value = step.value; pending.push(api.setSurvey(step.value)); }
        else if (step.do === 'blend') {
            // The real "Blend with" change handler (wireBlendAndConstellationUI).
            dom.bSel.value = step.value;
            if (dom.bSel.value && dom.op.value === '0') dom.op.value = '0.5';
            pending.push(api.ensureBlendLayer());
            api.setBlendOpacity(Number(dom.op.value || 0));
            api.updateSurveyCredit();
        } else if (step.do === 'op') {
            dom.op.value = String(step.value);
            api.setBlendOpacity(step.value);
            api.updateSurveyCredit();
        } else if (step.do === 'toggle') { dom.toggle.checked = step.on; api.setOfflineView(step.on, true); }
        else if (step.do === 'event') { navigator.onLine = step.name === 'online'; (listeners[step.name] || []).forEach(f => f({ type: step.name })); }
        else if (step.do === 'wire') api.wireOfflineSkyUi();
        else if (step.do === 'loadFail') loadFail.add(step.slug);
        else if (step.do === 'clearCreated') created.length = 0;
        else if (step.do === 'initialLayer') {
            // The Framing open's first layer, outside setSurvey.
            const url = api.resolveHipsId(step.value);
            if (api.createSurveyLayer) aladin.setBaseImageLayer(api.createSurveyLayer('base', step.value, url));
            else aladin.setBaseImageLayer(aladin.createImageSurvey(url, step.value, url, 'equatorial', 9, {}));
        } else if (step.do === 'snap') { snaps[step.name] = snap(); continue; }
        await sleep(step.wait === undefined ? 60 : step.wait);
    }
    await Promise.allSettled(pending);
    await sleep(60);
    snaps.final = snap();
    return snaps;
}

(async () => {
    const out = {};
    for (const [name, c] of Object.entries(input.cases)) {
        try { out[name] = await runCase(c); }
        catch (e) { out[name] = { error: String(e && e.stack || e) }; }
    }
    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

def _start(offline=False, blend=None, op=None):
    """Steps that bring the sky to DSS2 (and optionally a blend) through the real code."""
    steps = [{"do": "pick", "value": DSS}]
    if blend:
        steps += [{"do": "blend", "value": blend}]
        if op is not None:
            steps += [{"do": "op", "value": op}]
    if offline:
        steps += [{"do": "toggle", "on": True}]
    return steps


CASES = {
    # --- Every pick builds the survey on the current mode's root
    "online_switching": {
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "pick", "value": GALEX},
                             {"do": "pick", "value": RGB}, {"do": "pick", "value": DSS}],
    },
    "offline_switching": {
        # PanSTARRS has nothing on disk: it is still built (empty), not refused.
        "steps": _start(offline=True) + [{"do": "pick", "value": RGB}, {"do": "pick", "value": PS},
                                         {"do": "pick", "value": GALEX}, {"do": "pick", "value": DSS}],
    },
    # The reported scenario: Offline view switched on while the sky is still
    # loading, then off again.
    "toggle_while_loading": {
        "loadDelay": {"dss2-color": 40},
        "steps": [{"do": "pick", "value": DSS, "wait": 5},
                  {"do": "toggle", "on": True, "wait": 5},
                  {"do": "toggle", "on": False}],
    },
    # --- The switch rebuilds both layers on the new root, unconditionally
    "toggle_rebuilds_both": {
        "steps": _start(blend=RGB) + [{"do": "clearCreated"},
                                      {"do": "toggle", "on": True}, {"do": "snap", "name": "on"},
                                      {"do": "clearCreated"},
                                      {"do": "toggle", "on": False}],
    },
    # A layer that failed in one mode is built again in the other: nothing is
    # remembered about it.
    "toggle_rebuilds_after_a_failed_load": {
        "loadFail": ["panstarrs-dr1-color"],
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "clearCreated"},
                             {"do": "toggle", "on": True}],
    },
    "blend_op0_toggle_on": {
        "steps": _start(blend=HA, op=0) + [{"do": "toggle", "on": True}],
    },
    "blend_op0_toggle_off": {
        "steps": _start(offline=True, blend=HA, op=0) + [{"do": "toggle", "on": False}],
    },
    # A blend still loading when it is taken off (opacity 0, then the switch)
    # lands afterwards: dropped, not left on screen.
    "blend_late_landing": {
        "loadDelay": {"nsns-halpha8": 40},
        "steps": _start() + [{"do": "blend", "value": HA, "wait": 5},
                             {"do": "op", "value": 0, "wait": 5},
                             {"do": "toggle", "on": True}],
    },
    "offline_event": {
        "steps": [{"do": "wire"}] + _start() + [{"do": "pick", "value": PS},
                                                {"do": "event", "name": "offline"}],
    },
    "online_event_restores_the_users_setting": {
        "steps": [{"do": "wire"}] + _start() + [{"do": "event", "name": "offline"},
                                                {"do": "event", "name": "online"}],
    },
    # --- Blend bookkeeping
    "blend_credit_after_load": {
        "loadDelay": {"nsns-rgb8": 30},
        "steps": _start() + [{"do": "blend", "value": RGB}],
    },
    "blend_built_once": {
        "steps": _start() + [{"do": "clearCreated"}, {"do": "blend", "value": RGB},
                             {"do": "op", "value": 0.7}, {"do": "op", "value": 0.8}],
    },
    # --- The renderer keeps the format of the layer it shows
    "format_dss_to_nsns": {
        "steps": _start() + [{"do": "pick", "value": HA}],
    },
    "format_nsns_to_dss": {
        "steps": _start() + [{"do": "pick", "value": HA}, {"do": "pick", "value": DSS}],
    },
    "format_restore_race": {
        # The Framing open: the first layer (DSS2) is still loading when a saved
        # NSNS survey is restored.
        "steps": [{"do": "initialLayer", "value": DSS, "wait": 0}, {"do": "pick", "value": HA}],
    },
    "format_blend": {
        "steps": _start(blend=RGB) + [{"do": "blend", "value": DSS_RED}],
    },
    # --- Startup
    "page_loaded_offline": {
        "browserOnline": False,
        "steps": [{"do": "wire"}],
    },
    "aladin_fallback_offline": {
        "loadFail": ["dss2-color"],
        "steps": [{"do": "toggle", "on": True}, {"do": "initialLayer", "value": DSS}],
    },
}


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_survey_state")
    driver, inp, result = tmp / "driver.cjs", tmp / "in.json", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    inp.write_text(json.dumps({
        "i18n": I18N, "surveys": SURVEYS, "cases": CASES,
        "entry": ENTRY, "optional": OPTIONAL, "stubs": STUBS,
    }), encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(UTILS_JS), str(inp), str(result)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def _case(out, name, snap="final"):
    res = out[name]
    assert "error" not in res, res.get("error")
    return res[snap]


def _root(value, offline=False):
    return ("/hips/offline/" if offline else "/hips/") + SURVEYS[value][0]


def _label(value):
    return SURVEYS[value][1]

# --- Every pick builds the survey on the current mode's root --------------------

def test_online_every_pick_is_shown_on_the_proxy_root(out):
    s = _case(out, "online_switching")
    assert s["created"] == [_root(v) for v in (DSS, PS, GALEX, RGB, DSS)]
    assert s["base"] == _root(DSS) and s["select"] == DSS
    assert s["notice"] is None


def test_offline_every_pick_is_shown_on_the_cache_only_root(out):
    """Nothing is refused in Offline view: a survey with nothing on disk is built
    like any other and shows empty sky."""
    s = _case(out, "offline_switching")
    assert s["created"][-4:] == [_root(v, offline=True) for v in (RGB, PS, GALEX, DSS)]
    assert s["base"] == _root(DSS, offline=True) and s["select"] == DSS
    assert s["notice"] is None


def test_the_client_makes_no_request_of_its_own(out):
    """No pre-flight, no stored-copy probe: Aladin's own requests are the only
    ones, in every case."""
    for name in CASES:
        assert _case(out, name)["fetches"] == [], name


def test_no_survey_ever_gets_a_notice(out):
    for name in CASES:
        assert _case(out, name)["notice"] is None, name


# --- The Offline view switch ------------------------------------------------------

def test_switching_offline_view_off_while_loading_ends_on_the_online_root(out):
    """The reported case: switch on while the sky loads, switch off -- no message,
    and the layer is back on /hips/, where missing tiles are fetched."""
    s = _case(out, "toggle_while_loading")
    assert s["offlineView"] is False and s["checked"] is False
    assert s["base"] == _root(DSS)
    assert s["notice"] is None
    assert s["created"] == [_root(DSS), _root(DSS, offline=True), _root(DSS)]


def test_the_switch_rebuilds_base_and_blend_on_the_new_root(out):
    on = _case(out, "toggle_rebuilds_both", "on")
    assert on["created"] == [_root(DSS, offline=True), _root(RGB, offline=True)]
    assert on["base"] == _root(DSS, offline=True) and on["blend"] == _root(RGB, offline=True)
    off = _case(out, "toggle_rebuilds_both")
    assert off["created"] == [_root(DSS), _root(RGB)]
    assert off["base"] == _root(DSS) and off["blend"] == _root(RGB)


def test_the_switch_rebuilds_a_survey_whose_last_load_failed(out):
    s = _case(out, "toggle_rebuilds_after_a_failed_load")
    assert s["created"] == [_root(PS, offline=True)]
    assert s["select"] == PS, "the dropdown keeps the choice"


def test_a_blend_at_opacity_0_is_taken_off_on_offline_view(out):
    assert _case(out, "blend_op0_toggle_on")["blend"] is None


def test_a_blend_at_opacity_0_is_taken_off_when_unticked(out):
    assert _case(out, "blend_op0_toggle_off")["blend"] is None


def test_a_blend_that_lands_after_it_was_taken_off_is_dropped(out):
    s = _case(out, "blend_late_landing")
    assert s["blend"] is None, s["blend"]


def test_the_browser_offline_event_moves_the_layer_to_the_cache_only_root(out):
    s = _case(out, "offline_event")
    assert s["offlineView"] is True and s["checked"] is True
    assert s["base"] == _root(PS, offline=True)
    assert s["select"] == PS


def test_the_online_event_restores_the_users_own_setting(out):
    s = _case(out, "online_event_restores_the_users_setting")
    assert s["offlineView"] is False and s["checked"] is False
    assert s["base"] == _root(DSS)


# --- Blend bookkeeping -------------------------------------------------------------

def test_the_blend_credit_follows_the_blend_once_it_is_shown(out):
    s = _case(out, "blend_credit_after_load")
    assert s["blend"] == _root(RGB)
    assert "Northern Sky Narrowband Survey" in s["credit"], s["credit"]


def test_a_blend_change_builds_the_layer_once(out):
    """The change handler calls ensureBlendLayer twice, and the opacity slider on
    every move: one layer."""
    s = _case(out, "blend_built_once")
    assert s["created"] == [_root(RGB)], s["created"]


# --- Renderer format ---------------------------------------------------------------

@pytest.mark.parametrize("name,fmt", [("format_dss_to_nsns", "png"),
                                      ("format_nsns_to_dss", "jpeg"),
                                      ("format_restore_race", "png")])
def test_the_renderer_draws_the_new_base_in_its_own_tile_format(out, name, fmt):
    """Regression: confirming a base layer applied the image adjustments to the
    layer Aladin still listed (the old one) while the renderer already held the
    new one, so the old layer's tile format overwrote the new layer's -- NSNS
    tiles were requested as .jpg (404) and the sky stayed black."""
    s = _case(out, name)
    assert s["baseFormat"] == fmt
    assert s["rendererBase"] == fmt, f"renderer draws {s['rendererBase']} tiles for a {fmt} survey"


def test_the_renderer_draws_the_new_blend_in_its_own_tile_format(out):
    s = _case(out, "format_blend")
    assert s["blendFormat"] == "jpeg"
    assert s["rendererBlend"] == "jpeg", s["rendererBlend"]


# --- Startup -------------------------------------------------------------------

def test_a_page_loaded_offline_starts_in_offline_view(out):
    s = _case(out, "page_loaded_offline")
    assert s["offlineView"] is True
    assert s["checked"] is True


def test_aladins_empty_view_fallback_never_reaches_the_network_in_offline_view(out):
    """The first layer fails in Offline view; Aladin then tries its own alaskybis
    DSS fallback. That must not go out."""
    s = _case(out, "aladin_fallback_offline")
    assert s["fallback"] == [], s["fallback"]
