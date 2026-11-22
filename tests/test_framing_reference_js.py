"""Reference values for the Framing Assistant's outputs, frozen before the Framing tab move.

The JNow RA/Dec readout and the rotation angle are what the user copies into the ASIAIR,
and they are proven in the field, so they must not change for the same framing. These
tests pin them, together with what Save Framing, Copy framing URL, Copy RA/Dec and
Copy Plan (CSV) produce, for five framings taken from a real instance DB (plain, rotated,
mosaic, off-centre + rotated, near the pole) plus one rotated mosaic derived from the
mosaic case.

How it runs: the real functions are cut out of static/js/graph_view_chart.js by name and
run in node next to the real framing-utils.js, against a small stand-in DOM and a
stand-in Aladin whose view centre is exactly what gotoRaDec set. "Now" is frozen
(JNow depends on it). Opening goes through the real openFramingAssistant, with Aladin
already created (its creation branch is skipped) and the survey / overlay / offline-UI
helpers it calls replaced by no-ops: those do not touch the centre, the rotation or the
readout.

Each framing is opened the way the "Open saved framing" button does it (the real
checkAndShowFramingButton, fed the /api/get_framing response for that row), in two
page states:

  fresh       the saved framing is the first thing opened on the page
  afterPlain  the plain Framing Assistant was opened (and closed) first

Centre sources today (they differ, which is why both states are pinned):
  readout / Copy RA/Dec / Save   getFrameCenterRaDec: Lock on -> Aladin view centre,
                                 else fovCenter, else Aladin view centre
  Copy framing URL               fovCenter, else Aladin view centre
  Copy Plan (CSV)                fovCenter only (does nothing without one); J2000
Opening always ends in applyLockToObject(true), which sets fovCenter to objectCoords
(the object's position, known only once a plain open ran) or else the view centre. Until
the Framing tab move, that put the afterPlain URL and CSV on the object's position while
the readout and Save showed the saved centre; opening a saved centre now clears
objectCoords first, so afterPlain gives the fresh values.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UTILS = REPO / "static" / "js" / "framing-utils.js"
CHART = REPO / "static" / "js" / "graph_view_chart.js"
NODE = shutil.which("node")

NOW_ISO = "2026-11-17T21:00:00.000Z"
ORIGIN = "http://nova.test"

# Option values of #survey-select / #blend-survey-select as the template renders them today.
SURVEY_OPTIONS = [
    "P/DSS2/color", "P/allWISE/color", "P/2MASS/color",
    "https://www.simg.de/nebulae3/dr0_2/ohs8", "https://www.simg.de/nebulae3/dr0_2/hbr8",
    "https://www.simg.de/nebulae3/dr0_2/halpha8", "https://www.simg.de/nebulae3/dr0_2/oiii8",
    "https://www.simg.de/nebulae3/dr0_2/sii8", "https://www.simg.de/nebulae3/dr0_2/rgb8",
    "P/GALEXGR6/AIS/color", "P/PanSTARRS/DR1/color-z-zg-g", "P/SDSS9/color",
]
BLEND_OPTIONS = [""] + SURVEY_OPTIONS + ["P/DSS2/red"]

# Functions taken verbatim from graph_view_chart.js.
EXTRACT = [
    "getPrecessionMatrix", "applyMatrix", "convertJ2000ToJNow",
    "to360", "_parseFramingQueryString", "_ensureInactiveRigOption", "_restoreFramingState",
    "applyRigFovZoom", "showFrameOverlay", "frameCenter", "updateReadout", "updateReadoutFromCenter",
    "savedResultFrom", "setSavedFraming", "updateResultDiffers",
    "copyRaDec", "copyResult", "pad", "formatRA", "formatDec", "buildFramingQuery",
    "copyFramingUrl", "saveFramingToDB", "formatRaCsv", "formatDecCsv", "copyAsiairMosaic",
    "checkAndShowFramingButton", "savedFramingQuery", "openFramingAssistant", "openFramingQueued",
    "openFramingTabFirstTime",
]


def _case(fid, name, obj_ra, obj_dec, rig_id, rig_uid, fovw, fovh, row):
    """row: the /api/get_framing response for that saved framing."""
    return {"id": fid, "objectName": name, "objectRADeg": obj_ra, "objectDECDeg": obj_dec,
            "rig": {"id": rig_id, "uid": rig_uid, "fovw": fovw, "fovh": fovh},
            "framing": dict({"status": "found"}, **row)}


def _row(rig, rig_uid, ra, dec, rotation, survey, blend, blend_op,
         cols=1, rows=1, overlap=10.0):
    return {"rig": rig, "rig_uid": rig_uid, "ra": ra, "dec": dec, "rotation": rotation,
            "survey": survey, "blend": blend, "blend_op": blend_op,
            "mosaic_cols": cols, "mosaic_rows": rows, "mosaic_overlap": overlap,
            "img_brightness": 0.0, "img_contrast": 0.0, "img_gamma": 1.0,
            "img_saturation": 0.0, "geo_belt_enabled": True}


CASES = {
    # saved_framings.id 263 — rot 0, centred on the object
    "plain": _case(263, "NGC 3628", 170.07090833333334, 13.589488888888889,
                   156, "e2466476e25694bc538aa68f4e499b14", "41.21738843762213", "27.53690597720155",
                   _row(156, "e2466476e25694bc538aa68f4e499b14", 170.07090833333334, 13.58948888888889,
                        0.0, "P/DSS2/color", "P/DSS2/color", 0.0)),
    # saved_framings.id 18 — rotated 157.5°, centred on the object
    "rotated": _case(18, "NGC 3718", 173.14523824612, 53.0679222839,
                     63, "676b40ac4db9fa82cb8d2ce02e0cd6c1", "80.78333153407345", "53.97151569963181",
                     _row(63, "676b40ac4db9fa82cb8d2ce02e0cd6c1", 173.14523824612, 53.06792228390002,
                          157.5, "P/DSS2/color", "P/DSS2/color", 0.0)),
    # saved_framings.id 337 — 2×2 mosaic, 15 % overlap, no rig id (rig by uid only)
    "mosaic": _case(337, "SH 2-240", 85.250835, 28.103889,
                    174, "34b57df2c12dcb8474c2ced79f27211b", "207.08363772780712", "138.37265940035056",
                    _row(None, "34b57df2c12dcb8474c2ced79f27211b", 85.275, 28.083333333333332,
                         0.0, "https://www.simg.de/nebulae3/dr0_2/rgb8",
                         "https://www.simg.de/nebulae3/dr0_2/halpha8", 0.5, 2, 2, 15.0)),
    # saved_framings.id 12 — rotated 243.5°, centre ~2.9° off the object, legacy blend URL
    "offcentre": _case(12, "M42", 83.8201, -5.3876,
                       35, "0c2d56e84a30532e825795fc668cfdea", "207.08363772780712", "138.37265940035056",
                       _row(35, "0c2d56e84a30532e825795fc668cfdea", 85.1394052724378, -2.5262532732886163,
                            243.5, "P/DSS2/color", "https://www.simg.de/nebulae3/dr0_1/halpha8", 0.16)),
    # saved_framings.id 262 — Dec +85°, where precession moves RA the most
    "nearpole": _case(262, "C1", 11.81166, 85.245,
                      153, "849a595f9cc6d079a15d3a7a4ba17f74", "149.582042180915", "99.94226454386425",
                      _row(153, "849a595f9cc6d079a15d3a7a4ba17f74", 11.811659999999991, 85.24499999999999,
                           0.0, "https://www.simg.de/nebulae3/dr0_2/rgb8",
                           "https://www.simg.de/nebulae3/dr0_2/hbr8", 0.06)),
    # derived from "mosaic" (not in the DB): 3×2 at 33.5°, 20 % overlap — rotated mosaic CSV
    "mosaic_rotated": _case(-337, "SH 2-240", 85.250835, 28.103889,
                            174, "34b57df2c12dcb8474c2ced79f27211b", "207.08363772780712", "138.37265940035056",
                            _row(None, "34b57df2c12dcb8474c2ced79f27211b", 85.275, 28.083333333333332,
                                 33.5, "https://www.simg.de/nebulae3/dr0_2/rgb8",
                                 "https://www.simg.de/nebulae3/dr0_2/halpha8", 0.5, 3, 2, 20.0)),
}

# The node harness, shared with tests/test_framing_result_js.py: `buildCase(c, opts)` builds
# the stand-in page for one case and returns the extracted framing functions.
HARNESS_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [utilsFile, chartFile, inFile, outFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const chartSrc = fs.readFileSync(chartFile, 'utf8');

// Cut one declaration out of the chart source: from its start to the matching brace,
// skipping strings, template literals and comments.
function cut(src, start) {
    let i = src.indexOf('{', src.indexOf(')', start)), depth = 0;
    const stack = [];   // 'tpl' while inside a template literal's text
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
        if (ch === '"' || ch === "'") {
            for (i++; src[i] !== ch; i++) if (src[i] === '\\') i++;
            continue;
        }
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
function extractFn(name) {
    const re = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(', 'g');
    const hits = [...chartSrc.matchAll(re)];
    if (hits.length !== 1) throw new Error(`${name}: ${hits.length} declarations in graph_view_chart.js`);
    return cut(chartSrc, hits[0].index);
}
function extractAssigned(marker) {
    const at = chartSrc.indexOf(marker);
    if (at < 0 || chartSrc.indexOf(marker, at + 1) >= 0) throw new Error(`${marker}: not exactly one`);
    return cut(chartSrc, at) + ';';
}

const FIXED_NOW = Date.parse(input.nowIso);
class FixedDate extends Date {
    constructor(...a) { if (a.length) super(...a); else super(FIXED_NOW); }
    static now() { return FIXED_NOW; }
}

// --- stand-in DOM -----------------------------------------------------------
class ClassList {
    constructor() { this.set = new Set(); }
    add(c) { this.set.add(c); } remove(c) { this.set.delete(c); } contains(c) { return this.set.has(c); }
    toggle(c, on) { if (on === undefined) on = !this.set.has(c); if (on) this.set.add(c); else this.set.delete(c); return on; }
}
class El {
    constructor(id, tag = 'div') {
        this.id = id; this.tagName = tag.toUpperCase(); this._value = ''; this.checked = false;
        this.textContent = ''; this.title = ''; this.className = ''; this.dataset = {};
        this.style = { setProperty(k, v) { this[k] = v; } };
        this.children = []; this.attrs = {}; this.onclick = null; this.nodeType = 1; this.hidden = false;
        this.classList = new ClassList(); this.listeners = {};
    }
    get value() { return this._value; }
    set value(v) { this._value = String(v); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    addEventListener(type, f) { (this.listeners[type] = this.listeners[type] || []).push(f); }
    removeEventListener(type, f) { this.listeners[type] = (this.listeners[type] || []).filter(g => g !== f); }
    dispatchEvent() { return true; }
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
    removeChild(c) { this.children = this.children.filter(x => x !== c); return c; }
    get lastChild() { return this.children[this.children.length - 1] || null; }
    insertBefore(c, ref) {
        const k = ref ? this.children.indexOf(ref) : -1;
        if (k < 0) this.children.push(c); else this.children.splice(k, 0, c);
        c.parentNode = this; return c;
    }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(x => x !== this); }
    get nextSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; }
    _all(sel) {
        const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
        return this.children.filter(c => m && (m[2] === undefined ? c.hasAttribute(m[1]) : c.getAttribute(m[1]) === m[2]));
    }
    querySelectorAll(sel) { return this._all(sel); }
    querySelector(sel) { return this._all(sel)[0] || null; }
    click() { if (this.onclick) this.onclick(); }
    insertAdjacentElement(where, el) { return el; }
}
// <input type=range>: the browser clamps and snaps an assigned value to min/max/step.
class Range extends El {
    constructor(id, min, max, step, value) { super(id, 'input'); this.min = min; this.max = max; this.step = step; this.value = value; }
    set value(v) {
        let n = parseFloat(v); if (!Number.isFinite(n)) n = (this.min + this.max) / 2;
        n = Math.min(this.max, Math.max(this.min, n));
        n = this.min + Math.round((n - this.min) / this.step) * this.step;
        n = Math.min(this.max, n);
        this._value = String(Number(n.toFixed(10)));
    }
    get value() { return this._value; }
    get valueAsNumber() { return parseFloat(this._value); }
}
// <select>: assigning a value that is not an option leaves nothing selected.
class Select extends El {
    constructor(id, values) {
        super(id, 'select');
        this.options = values.map(v => { const o = new El('', 'option'); o.value = v; return o; });
        this.selectedIndex = this.options.length ? 0 : -1;
    }
    get value() { return this.selectedIndex >= 0 ? this.options[this.selectedIndex].value : ''; }
    set value(v) { this.selectedIndex = this.options.findIndex(o => o.value === String(v)); }
    querySelectorAll(sel) {
        return sel === 'option[data-injected-inactive-rig]' ? this.options.filter(o => o.dataset.injectedInactiveRig) : [];
    }
    appendChild(o) { this.options.push(o); o.remove = () => { this.options = this.options.filter(x => x !== o); }; return o; }
}

/**
 * The stand-in page for one case. opts.aladinReady (default true): Aladin already exists,
 * so openFramingAssistant skips its creation branch; false: Aladin starts null and
 * A.aladin() creates the stand-in after the survey probe: opts.probeDelay ticks, or an
 * array of ticks, one per open.
 */
function buildCase(c, opts = {}) {
    const aladinReady = opts.aladinReady !== false;
    const els = {};
    const add = e => { els[e.id] = e; return e; };
    // rig select: another rig first, so restoring by uid has to pick the right one
    const rigSel = add(new Select('framing-rig-select', []));
    for (const r of [{ id: 9999, uid: 'other-rig', fovw: '60', fovh: '40' }, c.rig]) {
        const o = new El('', 'option'); o.value = String(r.id);
        o.dataset.rigUid = r.uid; o.dataset.fovw = r.fovw; o.dataset.fovh = r.fovh;
        rigSel.options.push(o);
    }
    rigSel.selectedIndex = 0;
    add(new Range('framing-rotation', 0, 360, 0.5, 0));
    add(new El('rotation-value', 'button')).textContent = '0°';
    add(new Select('survey-select', input.surveyOptions));
    add(new Select('blend-survey-select', input.blendOptions));
    add(new Range('blend-opacity', 0, 1, 0.01, 0));
    add(new El('mosaic-cols', 'input')).value = '1';
    add(new El('mosaic-rows', 'input')).value = '1';
    add(new El('mosaic-overlap', 'input')).value = '10';
    add(new Range('img-bright', -1, 1, 0.01, 0));
    add(new Range('img-contrast', -1, 1, 0.01, 0));
    add(new Range('img-gamma', 0.1, 10, 0.1, 1));
    add(new Range('img-sat', -1, 1, 0.01, 0));
    add(new El('show-geo-belt', 'input')).checked = true;
    add(new El('ra-readout', 'output'));
    add(new El('dec-readout', 'output'));
    add(new El('result-angle', 'output'));
    add(new El('result-saved-chip', 'span')).hidden = true;
    add(new El('result-saved-line', 'p')).hidden = true;
    add(new El('project-framing-buttons'));
    const framingTab = add(new El('framing-tab'));
    framingTab.classList.add('active');
    const host = add(new El('aladin-lite-div')); host.clientWidth = 800; host.clientHeight = 600;
    host.querySelector = () => null;

    const clipboard = [], posts = [], log = [];
    let framingResponse = c.framing;
    const ctx = {
        console, URL, URLSearchParams, Date: FixedDate, Math, JSON, Number, String, Array, Object,
        Promise, Error, isFinite, parseFloat, parseInt, encodeURIComponent, setImmediate,
        setTimeout: () => 0, clearTimeout: () => {},
        requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
        navigator: { onLine: true }, Node: { TEXT_NODE: 3 }, Event: class { constructor(type) { this.type = type; } },
        location: { origin: input.origin, pathname: '/graph_dashboard/' + encodeURIComponent(c.objectName), search: '' },
        document: {
            getElementById: id => els[id] || null,
            createElement: tag => { const e = new El('', tag); return e; },
            querySelectorAll: sel => (sel[0] === '#' && els[sel.slice(1)]) ? [els[sel.slice(1)]] : [],
        },
        fetch: (url, opts) => {
            if (url.startsWith('/api/get_framing/')) return Promise.resolve({ json: () => Promise.resolve(framingResponse) });
            posts.push({ url, body: JSON.parse(opts.body) });
            return new Promise(() => {});   // never settles: no alert, no button refresh
        },
    };
    ctx.window = ctx;
    // window.t as base.html defines it, over the real English strings the page passes in
    ctx.novaI18n = input.i18n;
    ctx.t = (k, params) => {
        let str = ctx.novaI18n[k];
        if (str === undefined) return k;
        if (params) Object.entries(params).forEach(([p, v]) => { str = str.replace(new RegExp('\\{' + p + '\\}', 'g'), v); });
        return str;
    };
    ctx.novaAlert = async () => {};
    ctx.novaConfirm = async () => false;
    ctx.NOVA_GRAPH_DATA = { objectName: c.objectName, objectRADeg: c.objectRADeg, objectDECDeg: c.objectDECDeg };
    ctx.__clip = t => clipboard.push(t);
    ctx.__log = s => log.push(s);
    ctx.__probeTicks = opts.probeDelay || 0;
    ctx.__aInitTicks = opts.aInitTicks === undefined ? null : opts.aInitTicks;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(utilsFile, 'utf8'), ctx, { filename: utilsFile });

    const script = `
        const FRAMING_CONFIG = window.framingUtils.CONSTANTS;
        const fu = () => window.framingUtils;
        let aladin = null, fovLayer = null;
        let __view = { ra: 0, dec: 0 };
        const __aladinStandIn = {
            gotoRaDec(ra, dec) { __view = { ra, dec }; },
            getRaDec() { return [__view.ra, __view.dec]; },
            gotoObject() {}, setFov() {}, getFov() { return [1, 1]; },
            on() {}, addOverlay() {}, getBaseImageLayer() { return {}; },
            pix2world(x, y) { return [__view.ra + x / 1000, __view.dec + y / 1000]; },
        };
        const A = {
            aladin() { __log('create'); return __aladinStandIn; },
            HiPS() { return {}; }, graphicOverlay() { return { removeAll() {} }; },
        };
        // Aladin Lite v3's engine promise. null = an Aladin without it (the default,
        // so the frozen tests are untouched); a number = the ticks until it settles.
        if (__aInitTicks !== null) {
            A.init = (async () => {
                for (let k = 0; k < __aInitTicks; k++) await new Promise(r => setImmediate(r));
                __log('init');
            })();
        }
        ${aladinReady ? 'aladin = __aladinStandIn;' : ''}
        function setSurvey() { return Promise.resolve(); }
        function ensureBlendLayer() {} function setBlendOpacity() {} function showSurveyNotice() {}
        function updateScreenFovOverlay() {}
        // applyRigFovZoom grows the frame by the mosaic through this helper, so it
        // has to exist here too (the harness stubs only what the frozen values
        // need; the fit itself is pinned in tests/test_framing_zoom_fit.py).
        function frameExtentDeg(paneW, paneH) { return { w: paneW, h: paneH }; }
        function startFrameOverlayLoop() {} function stopFrameOverlayLoop() {}
        function deleteSavedFraming() {}
        // what openFramingAssistant calls besides the extracted functions
        let geoBeltLayer = null, framingOpening = null, framingPageLoaded = true, savedResult = null;
        let scanFrameActive = false, scanResultsCatalog = null;
        let cdsReachable = true, dssMirror = null, displayedBaseSurvey = 'P/DSS2/color', baseSurvey = null;
        function closeOfflineSavePanel() {} function refreshOfflineSavePanel() {} function updateScanButtonState() {} function applyMinSizeFilter() {}
        function displayOfflineMessage() {} function resetSurveyNotice() {} function resetSurveyReachability() {} function showSurveyLoading() {}
        function hideSurveyLoading() {} function updateSurveyCredit() {} function wireOfflineSkyUi() {}
        function ensureOfflineBaseSurveys() {} function ensureNovaObjectsCatalog() {} function toggleGeoBelt() {}
        function updateFovVsObjectLabel() {} function surveyOptionLabel() { return ''; }
        // the survey-layer state (pinned in tests/test_framing_survey_state_js.py)
        let baseRequest = 0, baseWanted = null, offlineViewOn = false;
        function createSurveyLayer() { return { __nova: { settled: Promise.resolve(true) } }; }
        function guardAladinFallback() {} function keepBaseAfterFailure() {}
        function resolveHipsId(v) { return v; }
        function isFramingTabVisible() { return true; }
        async function probeSurveySources() {
            // a number, or one number per call (a later call can answer first: the CDS
            // probe is memoised once it has answered)
            const n = Array.isArray(__probeTicks) ? (__probeTicks.shift() || 0) : __probeTicks;
            for (let k = 0; k < n; k++) await new Promise(r => setImmediate(r));
            return { cdsReachable: true, dssMirror: null };
        }
        function chooseDisplayedBase(v) { return { survey: v, notice: false }; }
        function copyToClipboard(text, ok) { __clip(text); if (ok) ok(); }
        ${input.extract.map(extractFn).join('\n')}
        ${extractAssigned('window.updateFramingChart = function')}
        ({
            open: openFramingAssistant,
            openQueued: openFramingQueued,
            firstOpen: openFramingTabFirstTime,
            showSaved: checkAndShowFramingButton,
            setSavedFraming, updateReadoutFromCenter,
            view: () => aladin ? aladin.getRaDec() : null,
            moveView: (ra, dec) => aladin.gotoRaDec(ra, dec),
            aladinIsNull: () => aladin === null,
            setOpening: p => { framingOpening = p; },
            copyRaDec, copyResult, copyFramingUrl, saveFramingToDB, copyAsiairMosaic,
        })
    `;
    const api = vm.runInContext(script, ctx, { filename: 'framing-reference-harness.js' });
    return { els, api, clipboard, posts, log, ctx, setResponse: r => { framingResponse = r; } };
}
const settle = () => new Promise(r => setImmediate(r));   // let every pending promise settle
"""

_REFERENCE_MAIN = r"""
async function runOne(c, variant) {
    const { els, api, clipboard, posts } = buildCase(c);
    if (variant === 'afterPlain') await api.open('');
    api.showSaved();
    await settle();
    const openBtn = els['project-framing-buttons'].children
        .find(b => b.textContent === input.i18n.open_saved_framing);
    if (!openBtn) throw new Error('no Open saved framing button');
    openBtn.onclick();
    await settle();

    const view = api.view();
    const out = {
        readout: { ra: els['ra-readout'].value, dec: els['dec-readout'].value },
        rotationBadge: els['rotation-value'].textContent,
        rotationSlider: els['framing-rotation'].value,
        rig: els['framing-rig-select'].value,
        frameCenter: { ra: view[0], dec: view[1] },
        viewCenter: { ra: view[0], dec: view[1] },
    };
    clipboard.length = 0; api.copyRaDec(); out.copyRaDec = clipboard[0];
    api.saveFramingToDB(); out.save = posts.find(p => p.url === '/api/save_framing').body;
    clipboard.length = 0; api.copyFramingUrl(); out.copyUrl = clipboard[0];
    clipboard.length = 0; await api.copyAsiairMosaic(); out.csv = clipboard[0];
    // The Result block (not part of REFERENCE: checked against the values above)
    out.result = { angle: els['result-angle'].value, chipHidden: els['result-saved-chip'].hidden,
                   chip: els['result-saved-chip'].textContent, lineHidden: els['result-saved-line'].hidden };
    for (const k of ['ra', 'dec', 'angle', 'all']) { clipboard.length = 0; api.copyResult(k); out.result['copy_' + k] = clipboard[0]; }
    return out;
}

(async () => {
    const out = {};
    for (const [key, c] of Object.entries(input.cases)) {
        out[key] = {};
        for (const variant of input.variants) out[key][variant] = await runOne(c, variant);
    }
    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

_DRIVER = HARNESS_JS + _REFERENCE_MAIN


def framing_i18n():
    """The English novaI18n strings the framing code uses, as the templates define them."""
    import re
    keys = {}
    for tpl in (REPO / "templates" / "base.html", REPO / "templates" / "graph_view.html"):
        text = tpl.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:window\.novaI18n\.)?(\w+)\s*[:=]\s*\{\{ _\((['\"])(.*?)\2\) \| tojson \}\}", text):
            keys[m.group(1)] = m.group(3).replace("\\'", "'")
    return keys


def run_driver(main_js, payload, tmp):
    driver, inp, out = tmp / "driver.cjs", tmp / "in.json", tmp / "out.json"
    driver.write_text(HARNESS_JS + main_js, encoding="utf-8")
    inp.write_text(json.dumps(dict({
        "nowIso": NOW_ISO, "origin": ORIGIN, "surveyOptions": SURVEY_OPTIONS,
        "blendOptions": BLEND_OPTIONS, "extract": EXTRACT, "i18n": framing_i18n(),
    }, **payload)), encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(UTILS), str(CHART), str(inp), str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    out = run_driver(_REFERENCE_MAIN, {"cases": CASES, "variants": ["fresh", "afterPlain"]},
                     tmp_path_factory.mktemp("framing_reference"))
    # The Result block is checked against the pinned values in its own test below
    results_block = {k: {v: out[k][v].pop("result") for v in out[k]} for k in out}
    return out, results_block


# Captured from the code before the Framing tab move. Do not regenerate to make a failing
# test pass: a difference here is a change in what the user copies into the ASIAIR.
# Changed on purpose with the move: Copy framing URL ends in "&tab=framing", and in the
# afterPlain state Copy URL, Plan CSV and fovCenter now equal the fresh values (a saved
# centre no longer gives way to the object position an earlier plain open left behind).
# Framing tab step 2: one centre source (the view centre). "fovCenter" was renamed to
# "viewCenter" (Aladin's view centre, same numbers); no guarded value changed.
REFERENCE = {'mosaic': {'afterPlain': {'copyRaDec': '05:42:47.76 +28:05:42.4',
                           'copyUrl': 'http://nova.test/graph_dashboard/SH%202-240?rig=174&rig_uid=34b57df2c12dcb8474c2ced79f27211b&ra=85.275000&dec=28.083333&rot=0&survey=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Frgb8&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhalpha8&blend_op=0.5&m_cols=2&m_rows=2&m_ov=15&tab=framing',
                           'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), '
                                  'Pane height (arcmins), Overlap, Row, Column\n'
                                  'SH_2-240_Rot0_P1, 05hr 34\' 31", 27º 06\' 11", 0.00, 207.08, '
                                  '138.37, 15%, 1, 1\n'
                                  'SH_2-240_Rot0_P2, 05hr 47\' 41", 27º 06\' 11", 0.00, 207.08, '
                                  '138.37, 15%, 1, 2\n'
                                  'SH_2-240_Rot0_P3, 05hr 34\' 23", 29º 03\' 49", 0.00, 207.08, '
                                  '138.37, 15%, 2, 1\n'
                                  'SH_2-240_Rot0_P4, 05hr 47\' 49", 29º 03\' 49", 0.00, 207.08, '
                                  '138.37, 15%, 2, 2\n',
                           'viewCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                           'frameCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                           'readout': {'dec': '+28:05:42.4', 'ra': '05:42:47.76'},
                           'rig': '174',
                           'rotationBadge': '0°',
                           'rotationSlider': '0',
                           'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/halpha8',
                                    'blend_op': 0.5,
                                    'dec': 28.083333333333332,
                                    'geo_belt_enabled': True,
                                    'img_brightness': 0,
                                    'img_contrast': 0,
                                    'img_gamma': 1,
                                    'img_saturation': 0,
                                    'mosaic_cols': 2,
                                    'mosaic_overlap': 15,
                                    'mosaic_rows': 2,
                                    'object_name': 'SH 2-240',
                                    'ra': 85.275,
                                    'rig': '174',
                                    'rotation': 0,
                                    'survey': 'https://www.simg.de/nebulae3/dr0_2/rgb8'}},
            'fresh': {'copyRaDec': '05:42:47.76 +28:05:42.4',
                      'copyUrl': 'http://nova.test/graph_dashboard/SH%202-240?rig=174&rig_uid=34b57df2c12dcb8474c2ced79f27211b&ra=85.275000&dec=28.083333&rot=0&survey=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Frgb8&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhalpha8&blend_op=0.5&m_cols=2&m_rows=2&m_ov=15&tab=framing',
                      'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane '
                             'height (arcmins), Overlap, Row, Column\n'
                             'SH_2-240_Rot0_P1, 05hr 34\' 31", 27º 06\' 11", 0.00, 207.08, 138.37, '
                             '15%, 1, 1\n'
                             'SH_2-240_Rot0_P2, 05hr 47\' 41", 27º 06\' 11", 0.00, 207.08, 138.37, '
                             '15%, 1, 2\n'
                             'SH_2-240_Rot0_P3, 05hr 34\' 23", 29º 03\' 49", 0.00, 207.08, 138.37, '
                             '15%, 2, 1\n'
                             'SH_2-240_Rot0_P4, 05hr 47\' 49", 29º 03\' 49", 0.00, 207.08, 138.37, '
                             '15%, 2, 2\n',
                      'viewCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                      'frameCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                      'readout': {'dec': '+28:05:42.4', 'ra': '05:42:47.76'},
                      'rig': '174',
                      'rotationBadge': '0°',
                      'rotationSlider': '0',
                      'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/halpha8',
                               'blend_op': 0.5,
                               'dec': 28.083333333333332,
                               'geo_belt_enabled': True,
                               'img_brightness': 0,
                               'img_contrast': 0,
                               'img_gamma': 1,
                               'img_saturation': 0,
                               'mosaic_cols': 2,
                               'mosaic_overlap': 15,
                               'mosaic_rows': 2,
                               'object_name': 'SH 2-240',
                               'ra': 85.275,
                               'rig': '174',
                               'rotation': 0,
                               'survey': 'https://www.simg.de/nebulae3/dr0_2/rgb8'}}},
 'mosaic_rotated': {'afterPlain': {'copyRaDec': '05:42:47.76 +28:05:42.4',
                                   'copyUrl': 'http://nova.test/graph_dashboard/SH%202-240?rig=174&rig_uid=34b57df2c12dcb8474c2ced79f27211b&ra=85.275000&dec=28.083333&rot=34&survey=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Frgb8&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhalpha8&blend_op=0.5&m_cols=3&m_rows=2&m_ov=20&tab=framing',
                                   'csv': 'Pane, RA, DEC, Position Angle (East), Pane width '
                                          '(arcmins), Pane height (arcmins), Overlap, Row, Column\n'
                                          'SH_2-240_Rot34_P1, 05hr 28\' 16", 28º 50\' 17", 34.00, '
                                          '207.08, 138.37, 20%, 1, 1\n'
                                          'SH_2-240_Rot34_P2, 05hr 38\' 48", 27º 18\' 51", 34.00, '
                                          '207.08, 138.37, 20%, 1, 2\n'
                                          'SH_2-240_Rot34_P3, 05hr 49\' 04", 25º 47\' 24", 34.00, '
                                          '207.08, 138.37, 20%, 1, 3\n'
                                          'SH_2-240_Rot34_P4, 05hr 32\' 47", 30º 22\' 36", 34.00, '
                                          '207.08, 138.37, 20%, 2, 1\n'
                                          'SH_2-240_Rot34_P5, 05hr 43\' 26", 28º 51\' 09", 34.00, '
                                          '207.08, 138.37, 20%, 2, 2\n'
                                          'SH_2-240_Rot34_P6, 05hr 53\' 46", 27º 19\' 43", 34.00, '
                                          '207.08, 138.37, 20%, 2, 3\n',
                                   'viewCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                                   'frameCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                                   'readout': {'dec': '+28:05:42.4', 'ra': '05:42:47.76'},
                                   'rig': '174',
                                   'rotationBadge': '34°',
                                   'rotationSlider': '33.5',
                                   'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/halpha8',
                                            'blend_op': 0.5,
                                            'dec': 28.083333333333332,
                                            'geo_belt_enabled': True,
                                            'img_brightness': 0,
                                            'img_contrast': 0,
                                            'img_gamma': 1,
                                            'img_saturation': 0,
                                            'mosaic_cols': 3,
                                            'mosaic_overlap': 20,
                                            'mosaic_rows': 2,
                                            'object_name': 'SH 2-240',
                                            'ra': 85.275,
                                            'rig': '174',
                                            'rotation': 33.5,
                                            'survey': 'https://www.simg.de/nebulae3/dr0_2/rgb8'}},
                    'fresh': {'copyRaDec': '05:42:47.76 +28:05:42.4',
                              'copyUrl': 'http://nova.test/graph_dashboard/SH%202-240?rig=174&rig_uid=34b57df2c12dcb8474c2ced79f27211b&ra=85.275000&dec=28.083333&rot=34&survey=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Frgb8&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhalpha8&blend_op=0.5&m_cols=3&m_rows=2&m_ov=20&tab=framing',
                              'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), '
                                     'Pane height (arcmins), Overlap, Row, Column\n'
                                     'SH_2-240_Rot34_P1, 05hr 28\' 16", 28º 50\' 17", 34.00, '
                                     '207.08, 138.37, 20%, 1, 1\n'
                                     'SH_2-240_Rot34_P2, 05hr 38\' 48", 27º 18\' 51", 34.00, '
                                     '207.08, 138.37, 20%, 1, 2\n'
                                     'SH_2-240_Rot34_P3, 05hr 49\' 04", 25º 47\' 24", 34.00, '
                                     '207.08, 138.37, 20%, 1, 3\n'
                                     'SH_2-240_Rot34_P4, 05hr 32\' 47", 30º 22\' 36", 34.00, '
                                     '207.08, 138.37, 20%, 2, 1\n'
                                     'SH_2-240_Rot34_P5, 05hr 43\' 26", 28º 51\' 09", 34.00, '
                                     '207.08, 138.37, 20%, 2, 2\n'
                                     'SH_2-240_Rot34_P6, 05hr 53\' 46", 27º 19\' 43", 34.00, '
                                     '207.08, 138.37, 20%, 2, 3\n',
                              'viewCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                              'frameCenter': {'dec': 28.083333333333332, 'ra': 85.275},
                              'readout': {'dec': '+28:05:42.4', 'ra': '05:42:47.76'},
                              'rig': '174',
                              'rotationBadge': '34°',
                              'rotationSlider': '33.5',
                              'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/halpha8',
                                       'blend_op': 0.5,
                                       'dec': 28.083333333333332,
                                       'geo_belt_enabled': True,
                                       'img_brightness': 0,
                                       'img_contrast': 0,
                                       'img_gamma': 1,
                                       'img_saturation': 0,
                                       'mosaic_cols': 3,
                                       'mosaic_overlap': 20,
                                       'mosaic_rows': 2,
                                       'object_name': 'SH 2-240',
                                       'ra': 85.275,
                                       'rig': '174',
                                       'rotation': 33.5,
                                       'survey': 'https://www.simg.de/nebulae3/dr0_2/rgb8'}}},
 'nearpole': {'afterPlain': {'copyRaDec': '00:50:09.95 +85:23:28.6',
                             'copyUrl': 'http://nova.test/graph_dashboard/C1?rig=153&rig_uid=849a595f9cc6d079a15d3a7a4ba17f74&ra=11.811660&dec=85.245000&rot=0&survey=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Frgb8&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhbr8&blend_op=0.06&tab=framing',
                             'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), '
                                    'Pane height (arcmins), Overlap, Row, Column\n'
                                    'C1_Rot0_P1, 00hr 47\' 15", 85º 14\' 42", 0.00, 149.58, 99.94, '
                                    '10%, 1, 1\n',
                             'viewCenter': {'dec': 85.24499999999999, 'ra': 11.811659999999991},
                             'frameCenter': {'dec': 85.24499999999999, 'ra': 11.811659999999991},
                             'readout': {'dec': '+85:23:28.6', 'ra': '00:50:09.95'},
                             'rig': '153',
                             'rotationBadge': '0°',
                             'rotationSlider': '0',
                             'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/hbr8',
                                      'blend_op': 0.06,
                                      'dec': 85.24499999999999,
                                      'geo_belt_enabled': True,
                                      'img_brightness': 0,
                                      'img_contrast': 0,
                                      'img_gamma': 1,
                                      'img_saturation': 0,
                                      'mosaic_cols': 1,
                                      'mosaic_overlap': 10,
                                      'mosaic_rows': 1,
                                      'object_name': 'C1',
                                      'ra': 11.811659999999991,
                                      'rig': '153',
                                      'rotation': 0,
                                      'survey': 'https://www.simg.de/nebulae3/dr0_2/rgb8'}},
              'fresh': {'copyRaDec': '00:50:09.95 +85:23:28.6',
                        'copyUrl': 'http://nova.test/graph_dashboard/C1?rig=153&rig_uid=849a595f9cc6d079a15d3a7a4ba17f74&ra=11.811660&dec=85.245000&rot=0&survey=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Frgb8&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhbr8&blend_op=0.06&tab=framing',
                        'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane '
                               'height (arcmins), Overlap, Row, Column\n'
                               'C1_Rot0_P1, 00hr 47\' 15", 85º 14\' 42", 0.00, 149.58, 99.94, 10%, '
                               '1, 1\n',
                        'viewCenter': {'dec': 85.24499999999999, 'ra': 11.811659999999991},
                        'frameCenter': {'dec': 85.24499999999999, 'ra': 11.811659999999991},
                        'readout': {'dec': '+85:23:28.6', 'ra': '00:50:09.95'},
                        'rig': '153',
                        'rotationBadge': '0°',
                        'rotationSlider': '0',
                        'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/hbr8',
                                 'blend_op': 0.06,
                                 'dec': 85.24499999999999,
                                 'geo_belt_enabled': True,
                                 'img_brightness': 0,
                                 'img_contrast': 0,
                                 'img_gamma': 1,
                                 'img_saturation': 0,
                                 'mosaic_cols': 1,
                                 'mosaic_overlap': 10,
                                 'mosaic_rows': 1,
                                 'object_name': 'C1',
                                 'ra': 11.811659999999991,
                                 'rig': '153',
                                 'rotation': 0,
                                 'survey': 'https://www.simg.de/nebulae3/dr0_2/rgb8'}}},
 'offcentre': {'afterPlain': {'copyRaDec': '05:41:54.54 -02:30:50.4',
                              'copyUrl': 'http://nova.test/graph_dashboard/M42?rig=35&rig_uid=0c2d56e84a30532e825795fc668cfdea&ra=85.139405&dec=-2.526253&rot=244&survey=P%2FDSS2%2Fcolor&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhalpha8&blend_op=0.16&tab=framing',
                              'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), '
                                     'Pane height (arcmins), Overlap, Row, Column\n'
                                     'M42_Rot244_P1, 05hr 40\' 33", -02º 31\' 35", 244.00, 207.08, '
                                     '138.37, 10%, 1, 1\n',
                              'viewCenter': {'dec': -2.5262532732886163, 'ra': 85.1394052724378},
                              'frameCenter': {'dec': -2.5262532732886163, 'ra': 85.1394052724378},
                              'readout': {'dec': '-02:30:50.4', 'ra': '05:41:54.54'},
                              'rig': '35',
                              'rotationBadge': '244°',
                              'rotationSlider': '243.5',
                              'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/halpha8',
                                       'blend_op': 0.16,
                                       'dec': -2.5262532732886163,
                                       'geo_belt_enabled': True,
                                       'img_brightness': 0,
                                       'img_contrast': 0,
                                       'img_gamma': 1,
                                       'img_saturation': 0,
                                       'mosaic_cols': 1,
                                       'mosaic_overlap': 10,
                                       'mosaic_rows': 1,
                                       'object_name': 'M42',
                                       'ra': 85.1394052724378,
                                       'rig': '35',
                                       'rotation': 243.5,
                                       'survey': 'P/DSS2/color'}},
               'fresh': {'copyRaDec': '05:41:54.54 -02:30:50.4',
                         'copyUrl': 'http://nova.test/graph_dashboard/M42?rig=35&rig_uid=0c2d56e84a30532e825795fc668cfdea&ra=85.139405&dec=-2.526253&rot=244&survey=P%2FDSS2%2Fcolor&blend=https%3A%2F%2Fwww.simg.de%2Fnebulae3%2Fdr0_2%2Fhalpha8&blend_op=0.16&tab=framing',
                         'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane '
                                'height (arcmins), Overlap, Row, Column\n'
                                'M42_Rot244_P1, 05hr 40\' 33", -02º 31\' 35", 244.00, 207.08, '
                                '138.37, 10%, 1, 1\n',
                         'viewCenter': {'dec': -2.5262532732886163, 'ra': 85.1394052724378},
                         'frameCenter': {'dec': -2.5262532732886163, 'ra': 85.1394052724378},
                         'readout': {'dec': '-02:30:50.4', 'ra': '05:41:54.54'},
                         'rig': '35',
                         'rotationBadge': '244°',
                         'rotationSlider': '243.5',
                         'save': {'blend': 'https://www.simg.de/nebulae3/dr0_2/halpha8',
                                  'blend_op': 0.16,
                                  'dec': -2.5262532732886163,
                                  'geo_belt_enabled': True,
                                  'img_brightness': 0,
                                  'img_contrast': 0,
                                  'img_gamma': 1,
                                  'img_saturation': 0,
                                  'mosaic_cols': 1,
                                  'mosaic_overlap': 10,
                                  'mosaic_rows': 1,
                                  'object_name': 'M42',
                                  'ra': 85.1394052724378,
                                  'rig': '35',
                                  'rotation': 243.5,
                                  'survey': 'P/DSS2/color'}}},
 'plain': {'afterPlain': {'copyRaDec': '11:21:41.14 +13:26:31.2',
                          'copyUrl': 'http://nova.test/graph_dashboard/NGC%203628?rig=156&rig_uid=e2466476e25694bc538aa68f4e499b14&ra=170.070908&dec=13.589489&rot=0&survey=P%2FDSS2%2Fcolor&blend=P%2FDSS2%2Fcolor&blend_op=0&tab=framing',
                          'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane '
                                 'height (arcmins), Overlap, Row, Column\n'
                                 'NGC_3628_Rot0_P1, 11hr 20\' 17", 13º 35\' 22", 0.00, 41.22, '
                                 '27.54, 10%, 1, 1\n',
                          'viewCenter': {'dec': 13.58948888888889, 'ra': 170.07090833333334},
                          'frameCenter': {'dec': 13.58948888888889, 'ra': 170.07090833333334},
                          'readout': {'dec': '+13:26:31.2', 'ra': '11:21:41.14'},
                          'rig': '156',
                          'rotationBadge': '0°',
                          'rotationSlider': '0',
                          'save': {'blend': 'P/DSS2/color',
                                   'blend_op': 0,
                                   'dec': 13.58948888888889,
                                   'geo_belt_enabled': True,
                                   'img_brightness': 0,
                                   'img_contrast': 0,
                                   'img_gamma': 1,
                                   'img_saturation': 0,
                                   'mosaic_cols': 1,
                                   'mosaic_overlap': 10,
                                   'mosaic_rows': 1,
                                   'object_name': 'NGC 3628',
                                   'ra': 170.07090833333334,
                                   'rig': '156',
                                   'rotation': 0,
                                   'survey': 'P/DSS2/color'}},
           'fresh': {'copyRaDec': '11:21:41.14 +13:26:31.2',
                     'copyUrl': 'http://nova.test/graph_dashboard/NGC%203628?rig=156&rig_uid=e2466476e25694bc538aa68f4e499b14&ra=170.070908&dec=13.589489&rot=0&survey=P%2FDSS2%2Fcolor&blend=P%2FDSS2%2Fcolor&blend_op=0&tab=framing',
                     'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane '
                            'height (arcmins), Overlap, Row, Column\n'
                            'NGC_3628_Rot0_P1, 11hr 20\' 17", 13º 35\' 22", 0.00, 41.22, 27.54, '
                            '10%, 1, 1\n',
                     'viewCenter': {'dec': 13.58948888888889, 'ra': 170.07090833333334},
                     'frameCenter': {'dec': 13.58948888888889, 'ra': 170.07090833333334},
                     'readout': {'dec': '+13:26:31.2', 'ra': '11:21:41.14'},
                     'rig': '156',
                     'rotationBadge': '0°',
                     'rotationSlider': '0',
                     'save': {'blend': 'P/DSS2/color',
                              'blend_op': 0,
                              'dec': 13.58948888888889,
                              'geo_belt_enabled': True,
                              'img_brightness': 0,
                              'img_contrast': 0,
                              'img_gamma': 1,
                              'img_saturation': 0,
                              'mosaic_cols': 1,
                              'mosaic_overlap': 10,
                              'mosaic_rows': 1,
                              'object_name': 'NGC 3628',
                              'ra': 170.07090833333334,
                              'rig': '156',
                              'rotation': 0,
                              'survey': 'P/DSS2/color'}}},
 'rotated': {'afterPlain': {'copyRaDec': '11:34:03.05 +52:55:09.5',
                            'copyUrl': 'http://nova.test/graph_dashboard/NGC%203718?rig=63&rig_uid=676b40ac4db9fa82cb8d2ce02e0cd6c1&ra=173.145238&dec=53.067922&rot=158&survey=P%2FDSS2%2Fcolor&blend=P%2FDSS2%2Fcolor&blend_op=0&tab=framing',
                            'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), '
                                   'Pane height (arcmins), Overlap, Row, Column\n'
                                   'NGC_3718_Rot158_P1, 11hr 32\' 35", 53º 04\' 05", 158.00, '
                                   '80.78, 53.97, 10%, 1, 1\n',
                            'viewCenter': {'dec': 53.06792228390002, 'ra': 173.14523824612},
                            'frameCenter': {'dec': 53.06792228390002, 'ra': 173.14523824612},
                            'readout': {'dec': '+52:55:09.5', 'ra': '11:34:03.05'},
                            'rig': '63',
                            'rotationBadge': '158°',
                            'rotationSlider': '157.5',
                            'save': {'blend': 'P/DSS2/color',
                                     'blend_op': 0,
                                     'dec': 53.06792228390002,
                                     'geo_belt_enabled': True,
                                     'img_brightness': 0,
                                     'img_contrast': 0,
                                     'img_gamma': 1,
                                     'img_saturation': 0,
                                     'mosaic_cols': 1,
                                     'mosaic_overlap': 10,
                                     'mosaic_rows': 1,
                                     'object_name': 'NGC 3718',
                                     'ra': 173.14523824612,
                                     'rig': '63',
                                     'rotation': 157.5,
                                     'survey': 'P/DSS2/color'}},
             'fresh': {'copyRaDec': '11:34:03.05 +52:55:09.5',
                       'copyUrl': 'http://nova.test/graph_dashboard/NGC%203718?rig=63&rig_uid=676b40ac4db9fa82cb8d2ce02e0cd6c1&ra=173.145238&dec=53.067922&rot=158&survey=P%2FDSS2%2Fcolor&blend=P%2FDSS2%2Fcolor&blend_op=0&tab=framing',
                       'csv': 'Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane '
                              'height (arcmins), Overlap, Row, Column\n'
                              'NGC_3718_Rot158_P1, 11hr 32\' 35", 53º 04\' 05", 158.00, 80.78, '
                              '53.97, 10%, 1, 1\n',
                       'viewCenter': {'dec': 53.06792228390002, 'ra': 173.14523824612},
                       'frameCenter': {'dec': 53.06792228390002, 'ra': 173.14523824612},
                       'readout': {'dec': '+52:55:09.5', 'ra': '11:34:03.05'},
                       'rig': '63',
                       'rotationBadge': '158°',
                       'rotationSlider': '157.5',
                       'save': {'blend': 'P/DSS2/color',
                                'blend_op': 0,
                                'dec': 53.06792228390002,
                                'geo_belt_enabled': True,
                                'img_brightness': 0,
                                'img_contrast': 0,
                                'img_gamma': 1,
                                'img_saturation': 0,
                                'mosaic_cols': 1,
                                'mosaic_overlap': 10,
                                'mosaic_rows': 1,
                                'object_name': 'NGC 3718',
                                'ra': 173.14523824612,
                                'rig': '63',
                                'rotation': 157.5,
                                'survey': 'P/DSS2/color'}}}}


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("variant", ["fresh", "afterPlain"])
def test_framing_outputs_match_reference(results, case, variant):
    assert results[0][case][variant] == REFERENCE[case][variant]


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("variant", ["fresh", "afterPlain"])
def test_result_block_shows_and_copies_the_pinned_values(results, case, variant):
    """The Result block's RA/Dec are the readout, its angle is the rotation badge, and its
    Copy buttons copy exactly those (Copy angle: the number only, for the ASIAIR field)."""
    ref, res = REFERENCE[case][variant], results[1][case][variant]
    ra, dec, badge = ref["readout"]["ra"], ref["readout"]["dec"], ref["rotationBadge"]
    assert res["angle"] == badge
    assert res["copy_ra"] == ra and res["copy_dec"] == dec
    assert res["copy_angle"] == badge.rstrip("°")
    assert res["copy_all"] == f"RA {ra}\nDec {dec}\nAngle {badge}"
    # the saved framing was just opened: marked saved, no difference
    assert (res["chipHidden"], res["chip"], res["lineHidden"]) == (False, "Saved framing", True)
