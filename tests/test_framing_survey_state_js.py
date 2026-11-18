"""Survey switching and Offline view, end to end in node.

The audit of the Framing tab's survey switching found the client state drifting
from what Aladin actually shows: the "kept" survey named in a notice was the one
that had just failed, a superseded choice could still post a notice or win the
swap, a failed probe was remembered after the situation changed, and Offline view
could leave a network-backed layer on screen. Each test below pins one of those
cases against the spec:

- Offline view off: a survey that answers is shown; if not, the current layer is
  kept and "<survey> is not responding — keeping <current>" is shown.
- Offline view on: disk only. Not stored -> the current layer is kept with
  "<survey> isn't stored offline — keeping <current>". If the layer on screen is
  still network-backed (Offline view just switched on), it must go: DSS2 (color)
  if stored, else the survey's own empty offline root, with "... showing ...".
- The dropdown always shows the survey actually on screen.
- Only the browser's offline/online events switch Offline view automatically.
- Messages clear when the situation is over; base and blend do not clear each
  other's still-valid message. Same rules for Blend with.

The harness cuts the real functions out of graph_view_chart.js -- every top-level
function and variable reachable from the entry points below -- and runs them
against stand-ins: a fake Aladin (a sequential layer queue like Aladin's own,
success/error callbacks, its "empty view" fallback), a fake /hips/ server, and a
minimal DOM. framing-utils.js is loaded whole.
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
    "survey_not_responding": "{survey} is not responding — keeping {current}.",
    "survey_not_stored_offline": "{survey} isn't stored offline — keeping {current}.",
    "survey_not_stored_offline_showing": "{survey} isn't stored offline — showing {shown}.",
    "survey_not_stored_offline_empty": "{survey} isn't stored offline — showing an empty sky.",
    "survey_not_responding_stored": "{survey} is not responding — showing the stored copy.",
    "survey_server_unreachable": "Sky survey server unreachable.",
    "survey": "Survey",
    "close": "Close",
}

# Entry points the harness drives; everything they reach is cut from the file.
ENTRY = ["setSurvey", "ensureBlendLayer", "setBlendOpacity", "updateSurveyCredit",
         "setOfflineView", "applyOfflineViewToLayers", "wireOfflineSkyUi",
         "showSurveyNotice", "hideSurveyNotice", "surveyReachable"]
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
    const stored = new Set(c.stored || []);          // slugs on disk
    const down = new Set(c.down || []);              // slugs whose upstreams all fail
    const probeDelay = c.probeDelay || {};           // slug -> ms (online and offline)
    const loadFail = new Set(c.loadFail || []);      // slugs whose Aladin load fails although the probe passed
    const loadDelay = c.loadDelay || {};
    const fetches = [], probes = [], created = [], fallback = [], snaps = {};
    const listeners = {};
    // D2: slugs whose properties file is fresh on the server's disk. The plain
    // /properties request is answered from it even with every upstream down;
    // only ?preflight=1 asks the upstreams.
    const propsOnDisk = new Set(c.propsOnDisk || []);
    // D1: slug -> deepest order with a tile at the frame centre on disk (-1:
    // none). A slug not listed has every tile. Asked only with ra/dec.
    const centreOrder = c.centreOrder || {};

    // The fake /hips/ server, from a root URL.
    const parse = url => {
        const m = String(url).match(/\/hips\/(offline\/)?([\w-]+)/);
        return m ? { offline: !!m[1], slug: m[2] } : null;
    };
    const answers = url => {
        const p = parse(url);
        if (!p) return false;
        return p.offline ? stored.has(p.slug) : (!down.has(p.slug) || propsOnDisk.has(p.slug));
    };
    // The server's answer to a pre-flight probe (query included).
    const probeAnswer = url => {
        const [path, query = ''] = String(url).split('?');
        const p = parse(path);
        if (!p) return false;
        const q = new URLSearchParams(query);
        if (!p.offline) return q.get('preflight') ? !down.has(p.slug) : answers(path);
        if (!stored.has(p.slug)) return false;
        if (!q.has('ra') || !q.has('dec') || !(p.slug in centreOrder)) return true;
        return centreOrder[p.slug] >= 0;                 // a tile at the centre, any order
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
            const ok = propertyLess || (answers(layer.url) && !loadFail.has(p.slug));
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
        fetch: async (url, opts) => {
            const root = String(url).split('?')[0].replace(/\/properties$/, '');
            fetches.push(root.replace('http://nova.test', ''));
            probes.push(String(url).replace('http://nova.test', ''));
            const p = parse(root);
            await sleep((p && probeDelay[p.slug]) || 1);
            const ok = probeAnswer(url);
            return { ok, status: ok ? 200 : (p && p.offline ? 404 : 503) };
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
            // What the "Blend with" change handler does before building: a new
            // pick retries a blend choice that failed.
            repickBlend: () => { try { blendFailed = null; } catch (e) {} },
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
        created: created.map(rel), fetches: fetches.slice(), probes: probes.slice(),
        fallback: fallback.slice(),
    });

    const pending = [];
    for (const step of c.steps) {
        if (step.do === 'pick') { dom.sSel.value = step.value; pending.push(api.setSurvey(step.value)); }
        else if (step.do === 'blend') {
            // The real "Blend with" change handler (wireBlendAndConstellationUI).
            dom.bSel.value = step.value;
            api.repickBlend();
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
        else if (step.do === 'store') stored.add(step.slug);
        else if (step.do === 'up') down.delete(step.slug);
        else if (step.do === 'down') down.add(step.slug);
        else if (step.do === 'loadFail') loadFail.add(step.slug);
        else if (step.do === 'clearFetches') { fetches.length = 0; probes.length = 0; }
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

ALL_STORED = ["dss2-color", "nsns-rgb8", "nsns-halpha8"]


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
    # --- Part 2: probes. A failure is never memoised, a success is.
    "p2_repick_retries": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"], "centreOrder": {"nsns-rgb8": -1},
        "steps": _start() + [
            {"do": "pick", "value": RGB},
            {"do": "up", "slug": "nsns-rgb8"},
            {"do": "pick", "value": RGB},
        ],
    },
    "p2_failure_probed_each_time": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"], "centreOrder": {"nsns-rgb8": -1},
        "steps": _start() + [{"do": "clearFetches"},
                             {"do": "pick", "value": RGB}, {"do": "pick", "value": RGB}],
    },
    "p2_success_memoised": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "clearFetches"},
                             {"do": "pick", "value": HA}, {"do": "pick", "value": DSS},
                             {"do": "pick", "value": HA}],
    },
    # A failed blend with nothing shown before: the slider does not re-probe it, a
    # re-pick does.
    "p2_blend_slider": {
        "stored": ALL_STORED, "down": ["dss2-red"],
        "steps": _start() + [
            {"do": "clearFetches"},
            {"do": "blend", "value": DSS_RED},
            {"do": "op", "value": 0.6}, {"do": "op", "value": 0.7},
            {"do": "snap", "name": "dragged"},
            {"do": "blend", "value": DSS_RED},
            {"do": "snap", "name": "repicked"},
            {"do": "up", "slug": "dss2-red"},
            {"do": "blend", "value": DSS_RED},
        ],
    },
    # --- D2: the online pre-flight asks the upstreams, not the server's disk copy
    "d2_props_on_disk_upstreams_down": {
        "stored": ALL_STORED, "down": ["panstarrs-dr1-color"], "propsOnDisk": ["panstarrs-dr1-color"],
        "steps": _start() + [{"do": "pick", "value": PS}],
    },
    "d2_blend_props_on_disk_upstreams_down": {
        "stored": ALL_STORED, "down": ["dss2-red"], "propsOnDisk": ["dss2-red"],
        "steps": _start(blend=HA) + [{"do": "blend", "value": DSS_RED}],
    },
    # --- D1: Offline view's pre-flight asks for the tile at the frame centre
    "d1_properties_only": {
        "stored": ALL_STORED + ["galex-ais-color"], "centreOrder": {"galex-ais-color": -1},
        "rigScale": 1.0,
        "steps": _start(offline=True) + [{"do": "pick", "value": GALEX}],
    },
    "d1_centre_tile_at_target": {
        "stored": ALL_STORED + ["galex-ais-color"], "centreOrder": {"galex-ais-color": 9},
        "rigScale": 1.0,
        "steps": _start(offline=True) + [{"do": "pick", "value": GALEX}],
    },
    "d1_shallow_tile": {
        "stored": ALL_STORED + ["galex-ais-color"], "centreOrder": {"galex-ais-color": 2},
        "rigScale": 1.0,
        "steps": _start(offline=True) + [{"do": "pick", "value": GALEX}],
    },
    # --- Online, not responding but stored at the centre: the stored copy
    "stored_copy_base": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"],
        "steps": _start() + [{"do": "pick", "value": RGB}, {"do": "snap", "name": "shown"},
                             {"do": "clearFetches"}, {"do": "pick", "value": RGB}],
    },
    "stored_copy_not_at_centre": {
        "stored": ALL_STORED, "down": ["nsns-halpha8"], "centreOrder": {"nsns-halpha8": -1},
        "steps": _start() + [{"do": "pick", "value": HA}],
    },
    "stored_copy_blend": {
        "stored": ALL_STORED, "down": ["nsns-halpha8"],
        "steps": _start(blend=RGB) + [{"do": "blend", "value": HA}, {"do": "snap", "name": "shown"},
                                      {"do": "clearFetches"},
                                      {"do": "op", "value": 0.7}, {"do": "op", "value": 0.8}],
    },
    "stored_copy_then_offline_view": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"],
        "steps": _start() + [{"do": "pick", "value": RGB},
                             {"do": "toggle", "on": True}, {"do": "snap", "name": "on"},
                             {"do": "toggle", "on": False}],
    },
    # --- Re-picking the survey already shown: no probe, no message
    "repick_shown": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "pick", "value": HA}, {"do": "pick", "value": PS},
                             {"do": "down", "slug": "panstarrs-dr1-color"},
                             {"do": "clearFetches"}, {"do": "pick", "value": PS}],
    },
    "repick_after_failure": {
        "stored": ALL_STORED, "down": ["panstarrs-dr1-color"],
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "clearFetches"},
                             {"do": "pick", "value": DSS}],
    },
    "d1_reroot": {
        "stored": ALL_STORED + ["panstarrs-dr1-color"], "centreOrder": {"panstarrs-dr1-color": -1},
        "rigScale": 2.0,
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "toggle", "on": True}],
    },
    "d1_blend": {
        "stored": ALL_STORED, "centreOrder": {"nsns-halpha8": -1}, "rigScale": 2.0,
        "steps": _start(offline=True, blend=RGB) + [{"do": "blend", "value": HA}],
    },
    # --- D3: the kept survey is the one still on screen, credited only once shown
    "d3_base": {
        "stored": ALL_STORED,
        "steps": _start() + [
            {"do": "pick", "value": PS}, {"do": "pick", "value": DSS},   # PanSTARRS probed OK once
            {"do": "loadFail", "slug": "panstarrs-dr1-color"},          # ...now Aladin's load fails
            {"do": "pick", "value": PS},
        ],
    },
    "d3_blend": {
        "stored": ALL_STORED,
        "steps": _start(blend=DSS_RED) + [
            {"do": "blend", "value": RGB},
            {"do": "loadFail", "slug": "dss2-red"},
            {"do": "blend", "value": DSS_RED},
        ],
    },
    # --- D7: Blend-only surveys are named by their label, not their ID
    "d7_blend_label": {
        "stored": ALL_STORED,
        "steps": _start(offline=True, blend=RGB) + [{"do": "blend", "value": DSS_RED}],
    },
    # --- D5: superseded choices
    "d5_late_error": {
        "stored": ALL_STORED, "loadDelay": {"panstarrs-dr1-color": 40},
        "steps": _start() + [
            {"do": "pick", "value": PS}, {"do": "pick", "value": DSS},
            {"do": "loadFail", "slug": "panstarrs-dr1-color"},
            {"do": "pick", "value": PS, "wait": 5}, {"do": "pick", "value": HA},
        ],
    },
    "d5_slow_probe": {
        "stored": ALL_STORED, "probeDelay": {"galex-ais-color": 40},
        "steps": _start() + [{"do": "pick", "value": GALEX, "wait": 5}, {"do": "pick", "value": HA}],
    },
    "d5_new_choice_hides": {
        "stored": ALL_STORED + ["galex-ais-color"], "probeDelay": {"galex-ais-color": 40},
        "steps": _start(offline=True) + [
            {"do": "pick", "value": PS},
            {"do": "pick", "value": GALEX, "wait": 5}, {"do": "snap", "name": "during"},
        ],
    },
    # --- D6: the probe memo
    "d6_toggle_resets": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"], "centreOrder": {"nsns-rgb8": -1},
        "steps": _start() + [
            {"do": "pick", "value": RGB},
            {"do": "up", "slug": "nsns-rgb8"},
            {"do": "toggle", "on": True}, {"do": "toggle", "on": False},
            {"do": "pick", "value": RGB},
        ],
    },
    "d6_online_event_resets": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"], "centreOrder": {"nsns-rgb8": -1},
        "steps": [{"do": "wire"}] + _start() + [
            {"do": "pick", "value": RGB},
            {"do": "up", "slug": "nsns-rgb8"},
            {"do": "event", "name": "offline"}, {"do": "event", "name": "online"},
            {"do": "pick", "value": RGB},
        ],
    },
    "d6_offline_not_memoised": {
        "stored": ALL_STORED,
        "steps": _start(offline=True) + [
            {"do": "pick", "value": PS},
            {"do": "store", "slug": "panstarrs-dr1-color"},
            {"do": "pick", "value": DSS}, {"do": "pick", "value": PS},
        ],
    },
    "d6_success_drops_failure": {
        "stored": ALL_STORED, "down": ["nsns-rgb8"], "centreOrder": {"nsns-rgb8": -1},
        "steps": _start() + [
            {"do": "pick", "value": RGB},                 # failure remembered
            {"do": "up", "slug": "nsns-rgb8"},
            {"do": "initialLayer", "value": RGB},         # ...then RGB loads by another route
            {"do": "pick", "value": DSS}, {"do": "pick", "value": RGB},
        ],
    },
    # --- D8: a blend at opacity 0 never keeps the other mode's root
    "d8_blend_op0_toggle_on": {
        "stored": ALL_STORED,
        "steps": _start(blend=HA, op=0) + [{"do": "toggle", "on": True}],
    },
    "d8_blend_op0_toggle_off": {
        "stored": ALL_STORED,
        "steps": _start(offline=True, blend=HA, op=0) + [{"do": "toggle", "on": False}],
    },
    # --- D4: Offline view never keeps a network-backed layer
    "d4_reroot_to_dss": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "toggle", "on": True}],
    },
    "d4_reroot_empty": {
        "stored": ["nsns-rgb8"],
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "toggle", "on": True}],
    },
    "d4_reroot_stored": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "pick", "value": RGB}, {"do": "toggle", "on": True}],
    },
    "d4_offline_pick_keeps": {
        "stored": ALL_STORED,
        "steps": _start(offline=True) + [{"do": "pick", "value": PS}],
    },
    "d4_online_pick_snaps": {
        "stored": ALL_STORED, "down": ["panstarrs-dr1-color"],
        "steps": _start() + [{"do": "pick", "value": PS}],
    },
    "d4_blend_reroot": {
        "stored": ALL_STORED,
        "steps": _start(blend=DSS_RED) + [{"do": "toggle", "on": True}],
    },
    "d4_offline_event": {
        "stored": ALL_STORED,
        "steps": [{"do": "wire"}] + _start() + [{"do": "pick", "value": PS},
                                                {"do": "event", "name": "offline"}],
    },
    # --- Minor
    "blend_credit_after_load": {
        "stored": ALL_STORED, "loadDelay": {"nsns-rgb8": 30},
        "steps": _start() + [{"do": "blend", "value": RGB}],
    },
    "blend_built_once": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "clearFetches"}, {"do": "blend", "value": RGB}],
    },
    "blend_success_keeps_base_notice": {
        "stored": ALL_STORED,
        "steps": _start(offline=True) + [{"do": "pick", "value": PS}, {"do": "blend", "value": HA}],
    },
    "blend_notice_clears_at_opacity_0": {
        "stored": ALL_STORED,
        "steps": _start(offline=True, blend=RGB) + [{"do": "blend", "value": DSS_RED},
                                                    {"do": "op", "value": 0}],
    },
    "base_success_keeps_blend_notice": {
        "stored": ALL_STORED,
        "steps": _start(offline=True, blend=RGB) + [{"do": "blend", "value": DSS_RED},
                                                    {"do": "pick", "value": HA}],
    },
    # --- The renderer keeps the format of the layer it shows
    "format_dss_to_nsns": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "pick", "value": HA}],
    },
    "format_nsns_to_dss": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "pick", "value": HA}, {"do": "pick", "value": DSS}],
    },
    "format_restore_race": {
        # The Framing open: the first layer (DSS2) is still loading when a saved
        # NSNS survey is restored.
        "stored": ALL_STORED,
        "steps": [{"do": "initialLayer", "value": DSS, "wait": 0}, {"do": "pick", "value": HA}],
    },
    "format_blend": {
        "stored": ALL_STORED,
        "steps": _start(blend=RGB) + [{"do": "blend", "value": DSS_RED}],
    },
    # --- Startup
    "page_loaded_offline": {
        "stored": ALL_STORED, "browserOnline": False,
        "steps": [{"do": "wire"}],
    },
    "aladin_fallback_offline": {
        "stored": [],
        "steps": [{"do": "toggle", "on": True}, {"do": "initialLayer", "value": DSS}],
    },
    # --- The spec's normal paths still hold
    "online_switching": {
        "stored": ALL_STORED,
        "steps": _start() + [{"do": "pick", "value": PS}, {"do": "pick", "value": GALEX},
                             {"do": "pick", "value": RGB}, {"do": "pick", "value": DSS}],
    },
    "offline_stored_switching": {
        "stored": ALL_STORED,
        "steps": _start(offline=True) + [{"do": "pick", "value": RGB}, {"do": "pick", "value": HA},
                                         {"do": "pick", "value": DSS}],
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


# --- Part 2: the probe memo ----------------------------------------------------

def _preflights(s, value):
    return [p for p in s["probes"] if p.startswith(_root(value) + "/properties")]


def test_p2_a_re_pick_retries_a_failed_survey_at_once(out):
    """No failure memo: RGB failed, its upstream is back, and picking it again
    straight away loads it (the old 60 s failure memo answered "not responding")."""
    s = _case(out, "p2_repick_retries")
    assert s["base"] == _root(RGB)
    assert s["notice"] is None, s["notice"]


def test_p2_every_pick_of_a_failing_survey_asks_the_server(out):
    s = _case(out, "p2_failure_probed_each_time")
    assert len(_preflights(s, RGB)) == 2, s["probes"]
    assert s["notice"] == f"{_label(RGB)} is not responding — keeping {_label(DSS)}."


def test_p2_a_success_is_still_memoised(out):
    s = _case(out, "p2_success_memoised")
    assert len(_preflights(s, HA)) == 1, s["probes"]
    assert s["base"] == _root(HA)


def test_p2_the_opacity_slider_does_not_re_probe_a_failed_blend(out):
    dragged = _case(out, "p2_blend_slider", "dragged")
    assert len(_preflights(dragged, DSS_RED)) == 1, dragged["probes"]
    assert dragged["notice"] == f"{_label(DSS_RED)} is not responding — keeping {_label(DSS)}."
    repicked = _case(out, "p2_blend_slider", "repicked")
    assert len(_preflights(repicked, DSS_RED)) == 2, "a re-pick asks again"
    final = _case(out, "p2_blend_slider")
    assert final["blend"] == _root(DSS_RED), "the upstream is back: the re-pick loads it"
    assert final["notice"] is None


def test_p2_the_blend_change_handler_clears_the_failed_choice():
    src = CHART_JS.read_text(encoding="utf-8")
    handler = src[src.index("blendSel.addEventListener('change'"):]
    handler = handler[:handler.index("});")]
    assert "blendFailed = null" in handler


# --- D2 ------------------------------------------------------------------------

def test_d2_the_online_preflight_asks_the_upstreams(out):
    """PanSTARRS' properties are fresh on the server's disk but every upstream is
    down. The plain properties request would pass (and the sky would stay black);
    the pre-flight asks with ?preflight=1, gets the 503 and keeps DSS2."""
    s = _case(out, "d2_props_on_disk_upstreams_down")
    assert _root(PS) + "/properties?preflight=1" in s["probes"], s["probes"]
    assert s["base"] == _root(DSS)
    assert s["notice"] == f"{_label(PS)} is not responding — keeping {_label(DSS)}."
    assert s["select"] == DSS


def test_d2_the_blend_preflight_asks_the_upstreams_too(out):
    s = _case(out, "d2_blend_props_on_disk_upstreams_down")
    assert s["blend"] == _root(HA)
    assert s["notice"] == f"{_label(DSS_RED)} is not responding — keeping {_label(HA)}."


# --- D1 ------------------------------------------------------------------------

def test_d1_properties_alone_are_not_stored_offline(out):
    s = _case(out, "d1_properties_only")
    assert s["base"] == _root(DSS, offline=True)
    assert s["notice"] == f"{_label(GALEX)} isn't stored offline — keeping {_label(DSS)}."
    assert s["select"] == DSS


def test_d1_the_offline_preflight_asks_for_the_frame_centre(out):
    s = _case(out, "d1_centre_tile_at_target")
    assert _root(GALEX, offline=True) + "/properties?ra=94.270000&dec=22.520000" in s["probes"], s["probes"]
    assert s["base"] == _root(GALEX, offline=True)
    assert s["notice"] is None


def test_d1_a_tile_at_any_order_is_enough_and_shown_silently(out):
    s = _case(out, "d1_shallow_tile")
    assert s["base"] == _root(GALEX, offline=True)
    assert s["notice"] is None, s["notice"]


# --- Online stored copy -----------------------------------------------------------

def test_a_survey_that_does_not_respond_is_shown_from_its_stored_copy(out):
    s = _case(out, "stored_copy_base", "shown")
    assert s["base"] == _root(RGB, offline=True)
    assert s["notice"] == f"{_label(RGB)} is not responding — showing the stored copy."
    assert s["select"] == RGB
    assert s["offlineView"] is False and s["checked"] is False, "Offline view is left alone"
    assert "Northern Sky Narrowband Survey" in s["credit"], s["credit"]


def test_re_picking_the_stored_copy_does_nothing_and_keeps_its_notice(out):
    s = _case(out, "stored_copy_base")
    assert s["probes"] == [], s["probes"]
    assert s["base"] == _root(RGB, offline=True)
    assert s["notice"] == f"{_label(RGB)} is not responding — showing the stored copy."


def test_no_stored_copy_at_the_centre_keeps_the_layer(out):
    s = _case(out, "stored_copy_not_at_centre")
    assert s["base"] == _root(DSS)
    assert s["notice"] == f"{_label(HA)} is not responding — keeping {_label(DSS)}."


def test_a_blend_that_does_not_respond_is_shown_from_its_stored_copy(out):
    shown = _case(out, "stored_copy_blend", "shown")
    assert shown["blend"] == _root(HA, offline=True)
    assert shown["notice"] == f"{_label(HA)} is not responding — showing the stored copy."
    s = _case(out, "stored_copy_blend")
    assert s["probes"] == [], "the slider does not rebuild a stored-copy blend"
    assert s["blend"] == _root(HA, offline=True)
    assert s["notice"] == f"{_label(HA)} is not responding — showing the stored copy."


def test_the_stored_copy_follows_offline_view_both_ways(out):
    on = _case(out, "stored_copy_then_offline_view", "on")
    assert on["base"] == _root(RGB, offline=True)
    assert on["notice"] is None, "in Offline view a stored survey needs no message"
    off = _case(out, "stored_copy_then_offline_view")
    assert off["base"] == _root(RGB, offline=True)
    assert off["notice"] == f"{_label(RGB)} is not responding — showing the stored copy."


# --- Re-pick of the survey on screen ----------------------------------------------

def test_re_picking_the_survey_on_screen_neither_probes_nor_speaks(out):
    s = _case(out, "repick_shown")
    assert s["probes"] == [] and s["fetches"] == [], (s["probes"], s["fetches"])
    assert s["base"] == _root(PS)
    assert s["notice"] is None


def test_picking_the_kept_survey_after_a_failure_clears_the_failure_notice(out):
    s = _case(out, "repick_after_failure")
    assert s["probes"] == [], s["probes"]
    assert s["base"] == _root(DSS)
    assert s["select"] == DSS
    assert s["notice"] is None, "a new choice: the PanSTARRS notice is over"


def test_d1_rerooting_a_survey_with_only_properties_falls_back_to_dss2(out):
    s = _case(out, "d1_reroot")
    assert s["base"] == _root(DSS, offline=True)
    assert s["notice"] == f"{_label(PS)} isn't stored offline — showing {_label(DSS)}."
    assert s["select"] == DSS


def test_d1_the_blend_preflight_asks_for_the_centre_tile_too(out):
    s = _case(out, "d1_blend")
    assert s["blend"] == _root(RGB, offline=True)
    assert s["notice"] == f"{_label(HA)} isn't stored offline — keeping {_label(RGB)}."


# --- D3 ------------------------------------------------------------------------

def test_d3_a_failed_base_names_the_survey_still_shown(out):
    """PanSTARRS passed its probe once, then Aladin's own load fails. DSS2 is
    still on screen, so the notice keeps DSS2 -- not the survey that failed --
    and the credit and dropdown follow the screen."""
    s = _case(out, "d3_base")
    assert s["base"] == _root(DSS)
    assert s["notice"] == f"{_label(PS)} is not responding — keeping {_label(DSS)}."
    assert s["credit"].startswith("DSS2"), s["credit"]
    assert s["select"] == DSS


def test_d3_a_failed_blend_names_the_blend_still_shown(out):
    s = _case(out, "d3_blend")
    assert s["blend"] == _root(RGB)
    assert s["notice"] == f"{_label(DSS_RED)} is not responding — keeping {_label(RGB)}."
    assert "Northern Sky Narrowband Survey" in s["credit"], s["credit"]
    assert s["blendSelect"] == RGB


# --- D7 ------------------------------------------------------------------------

def test_d7_a_blend_only_survey_is_named_by_its_label(out):
    s = _case(out, "d7_blend_label")
    assert s["notice"] == f"{_label(DSS_RED)} isn't stored offline — keeping {_label(RGB)}."


# --- D5 ------------------------------------------------------------------------

def test_d5_an_abandoned_choice_posts_no_late_notice(out):
    """PanSTARRS is picked and abandoned for H-alpha before its load fails: H-alpha
    is shown and nothing is said about PanSTARRS."""
    s = _case(out, "d5_late_error")
    assert s["base"] == _root(HA)
    assert s["select"] == HA
    assert s["notice"] is None, s["notice"]


def test_d5_a_superseded_preflight_never_wins_the_swap(out):
    """GALEX's probe is slow; H-alpha is picked meanwhile. H-alpha stays on screen:
    GALEX's late probe result is dropped, not swapped in afterwards."""
    s = _case(out, "d5_slow_probe")
    assert s["base"] == _root(HA)
    assert s["select"] == HA
    assert _root(GALEX) not in s["created"], "the superseded choice must not build a layer"


def test_d5_a_new_choice_hides_the_old_notice_at_once(out):
    s = _case(out, "d5_new_choice_hides", "during")
    assert s["notice"] is None, "the PanSTARRS notice must go as soon as GALEX is picked"
    assert _case(out, "d5_new_choice_hides")["base"] == _root(GALEX, offline=True)


# --- D6 ------------------------------------------------------------------------

def test_d6_switching_offline_view_forgets_failed_probes(out):
    s = _case(out, "d6_toggle_resets")
    assert s["base"] == _root(RGB), "RGB answers again and must load"
    assert s["notice"] is None


def test_d6_the_online_event_forgets_failed_probes(out):
    s = _case(out, "d6_online_event_resets")
    assert s["base"] == _root(RGB)
    assert s["notice"] is None


def test_d6_offline_probes_are_never_memoised(out):
    """PanSTARRS was not stored a moment ago; now it is. Offline view asks the disk
    again on the next pick instead of repeating the old answer."""
    s = _case(out, "d6_offline_not_memoised")
    assert s["base"] == _root(PS, offline=True)
    assert s["notice"] is None
    assert s["fetches"].count(_root(PS, offline=True)) == 2


def test_d6_a_successful_load_drops_the_failure_memo(out):
    s = _case(out, "d6_success_drops_failure")
    assert s["base"] == _root(RGB)
    assert s["notice"] is None, s["notice"]


# --- D8 ------------------------------------------------------------------------

def test_d8_a_blend_at_opacity_0_leaves_the_network_root_on_offline_view(out):
    s = _case(out, "d8_blend_op0_toggle_on")
    assert s["blend"] in (None, _root(HA, offline=True)), s["blend"]


def test_d8_a_blend_at_opacity_0_leaves_the_offline_root_when_unticked(out):
    s = _case(out, "d8_blend_op0_toggle_off")
    assert s["blend"] in (None, _root(HA)), s["blend"]


# --- D4 ------------------------------------------------------------------------

def test_d4_offline_view_replaces_an_unstored_network_layer_with_stored_dss2(out):
    s = _case(out, "d4_reroot_to_dss")
    assert s["base"] == _root(DSS, offline=True)
    assert s["select"] == DSS, "the dropdown snaps to the survey actually shown"
    assert s["notice"] == f"{_label(PS)} isn't stored offline — showing {_label(DSS)}."


def test_d4_with_nothing_stored_the_survey_shows_its_empty_offline_root(out):
    s = _case(out, "d4_reroot_empty")
    assert s["base"] == _root(PS, offline=True)
    assert s["select"] == PS
    assert s["notice"] == f"{_label(PS)} isn't stored offline — showing an empty sky."
    assert not s["credit"], "an empty sky credits no survey"


def test_d4_a_stored_survey_is_simply_rerooted(out):
    s = _case(out, "d4_reroot_stored")
    assert s["base"] == _root(RGB, offline=True)
    assert s["select"] == RGB
    assert s["notice"] is None


def test_d4_a_pick_inside_offline_view_keeps_and_snaps_back(out):
    s = _case(out, "d4_offline_pick_keeps")
    assert s["base"] == _root(DSS, offline=True)
    assert s["notice"] == f"{_label(PS)} isn't stored offline — keeping {_label(DSS)}."
    assert s["select"] == DSS


def test_d4_an_online_pick_that_fails_snaps_the_dropdown_back(out):
    s = _case(out, "d4_online_pick_snaps")
    assert s["base"] == _root(DSS)
    assert s["notice"] == f"{_label(PS)} is not responding — keeping {_label(DSS)}."
    assert s["select"] == DSS


def test_d4_an_unstored_network_blend_is_removed_on_offline_view(out):
    s = _case(out, "d4_blend_reroot")
    assert s["base"] == _root(DSS, offline=True)
    assert s["blend"] is None
    assert s["notice"] == f"{_label(DSS_RED)} isn't stored offline — keeping {_label(DSS)}."


def test_d4_the_browser_offline_event_reroots_too(out):
    s = _case(out, "d4_offline_event")
    assert s["offlineView"] is True and s["checked"] is True
    assert s["base"] == _root(DSS, offline=True)
    assert s["select"] == DSS


# --- Minor ---------------------------------------------------------------------

def test_the_blend_credit_follows_the_blend_once_it_is_shown(out):
    s = _case(out, "blend_credit_after_load")
    assert s["blend"] == _root(RGB)
    assert "Northern Sky Narrowband Survey" in s["credit"], s["credit"]


def test_a_blend_change_builds_the_layer_once(out):
    s = _case(out, "blend_built_once")
    assert s["created"].count(_root(RGB)) == 1, s["created"]
    assert s["fetches"].count(_root(RGB)) <= 1, s["fetches"]


def test_a_blend_success_does_not_hide_the_base_notice(out):
    s = _case(out, "blend_success_keeps_base_notice")
    assert s["blend"] == _root(HA, offline=True)
    assert s["notice"] == f"{_label(PS)} isn't stored offline — keeping {_label(DSS)}."


def test_a_blend_notice_clears_when_the_blend_goes_to_opacity_0(out):
    """At opacity 0 no blend is asked for: the message about it is over."""
    assert _case(out, "blend_notice_clears_at_opacity_0")["notice"] is None


def test_a_base_success_does_not_hide_the_blend_notice(out):
    s = _case(out, "base_success_keeps_blend_notice")
    assert s["base"] == _root(HA, offline=True)
    assert s["notice"] == f"{_label(DSS_RED)} isn't stored offline — keeping {_label(RGB)}."


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


# --- Normal paths ----------------------------------------------------------------

def test_online_switching_still_shows_every_survey(out):
    s = _case(out, "online_switching")
    assert s["base"] == _root(DSS) and s["select"] == DSS and s["notice"] is None


def test_offline_switching_between_stored_surveys_is_silent(out):
    s = _case(out, "offline_stored_switching")
    assert s["base"] == _root(DSS, offline=True) and s["notice"] is None
