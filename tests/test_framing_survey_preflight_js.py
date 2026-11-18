"""The Framing tab pre-flights a survey before swapping a layer, in node.

Aladin's own rejection only fires on a 404, so a 503 (the /hips/ proxy with every
upstream down) fails silently *after* the swap was issued: the old layer stays on
screen and the user is never told which survey failed. setSurvey and
ensureBlendLayer therefore probe the layer's own /properties first, and on failure
keep the layer already shown and name the survey in the notice.

The real functions are cut out of graph_view_chart.js by name and run against
stand-ins; only their collaborators (Aladin, the DOM, the notice element) are
replaced. Each case builds its own context, so cases cannot leak into each other.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
NODE = shutil.which("node")

I18N = {"survey_not_responding": "{survey} is not responding — keeping {current}.",
        "survey": "Survey"}
LABELS = {"P/DSS2/color": "DSS2 (color)",
          "P/GALEXGR6/AIS/color": "GALEX (color)",
          "P/SDSS9/color": "SDSS9 (color)"}
ROOTS = {"P/DSS2/color": "https://alasky.cds.unistra.fr/DSS/DSSColor",
         "P/GALEXGR6/AIS/color": "https://alasky.cds.unistra.fr/GALEX/GR6-03-2014/AIS-Color",
         "P/SDSS9/color": "https://alasky.cds.unistra.fr/SDSS/DR9/color"}

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [chartFile, inFile, outFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
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

// The failure TTL, read out of the real file so the harness cannot drift from it.
const ttlMatch = src.match(/const\s+SURVEY_PROBE_FAIL_TTL_MS\s*=\s*([^;]+);/);
if (!ttlMatch) throw new Error('SURVEY_PROBE_FAIL_TTL_MS not found in ' + chartFile);
const TTL = eval(ttlMatch[1]);

// Run one case: a fresh context, the real functions, stand-in collaborators.
async function runCase(c) {
    const log = [], notices = [], swaps = [], removed = [];
    // A controllable clock: the failure TTL is time-based, so a step can advance it.
    let clock = 1000000;
    const Date = { now: () => clock };
    // The selects need options/selectedIndex: ensureBlendLayer reads the selected
    // option's text for Aladin's layer name.
    function makeSelect(id, value) {
        const options = Object.entries(input.labels).map(([v, text]) => ({ value: v, textContent: text }));
        return { id, value, options, selectedIndex: options.findIndex(o => o.value === value) };
    }
    const els = {
        'survey-select': makeSelect('survey-select', c.baseValue),
        'blend-survey-select': makeSelect('blend-survey-select', c.blendValue),
        'blend-opacity': { id: 'blend-opacity', value: String(c.blendOpacity) },
    };
    // A layer already on screen carries its resolved URL as its id, as it does in
    // production (createImageSurvey gets the resolved URL).
    let baseLayer = c.currentBase ? { id: input.roots[c.currentBase] || c.currentBase } : null;
    let blendLayer = c.currentBlend ? { id: input.roots[c.currentBlend] || c.currentBlend } : null;

    const aladin = {
        createImageSurvey(id, name) { return { id, name }; },
        setBaseImageLayer(layer) { swaps.push(layer.id); baseLayer = layer; },
        getBaseImageLayer() { return baseLayer; },
        getOverlayImageLayer: () => blendLayer,
        setOverlayImageLayer(layer) { blendLayer = layer; return layer; },
        removeImageLayer() { removed.push('blend'); blendLayer = null; },
        newImageSurvey: () => ({ id: 'new' }),
    };

    const ctx = {
        console: { warn: () => {}, log: () => {}, error: () => {} },
        AbortController: class { constructor() { this.signal = {}; } abort() {} },
        setTimeout: () => 0, clearTimeout: () => {},
        Promise, Map, Set, JSON, Object, Array, Number, String, Error, RegExp, Date,
        document: { getElementById: id => els[id] || null, querySelector: () => null,
                    createElement: () => ({ style: {}, setAttribute() {}, append() {}, addEventListener() {} }) },
        window: {},
        fetch: (url) => {
            // probeHips appends /properties to the layer's root
            const root = String(url).replace(/\/properties$/, '');
            const value = input.rootToValue[root] || root;
            const ok = c.reachable[value] !== false;
            log.push('probe ' + value + ' -> ' + (ok ? 'ok' : 'fail'));
            return Promise.resolve({ ok, status: ok ? 200 : 503 });
        },
    };
    ctx.window.t = (k, params) => {
        const s = input.i18n[k];
        if (s === undefined) return k;
        return params ? Object.entries(params).reduce(
            (acc, [p, v]) => acc.replace(new RegExp('\\{' + p + '\\}', 'g'), v), s) : s;
    };
    ctx.window.framingUtils = { isCdsUrl: () => false };
    ctx.window.NOVA_OFFLINE_SKY = true;
    vm.createContext(ctx);

    const api = vm.runInContext(`
        const FRAMING_CONFIG = { DEFAULT_FOV_DEG: 3 };
        const CDS_PROBE_TIMEOUT_MS = 4000;
        const SURVEY_PROBE_FAIL_TTL_MS = ${TTL};
        let aladin = __aladin;
        let displayedBaseSurvey = ${JSON.stringify(c.currentBase)};
        let displayedBlendSurvey = ${JSON.stringify(c.currentBlend)};
        let baseSurvey = null;
        let cdsReachable = true, dssMirror = null;
        let __blendSurveyId = ${JSON.stringify(c.currentBlend)};
        let __blendLayerUrl = ${JSON.stringify(c.currentBlend)};
        const surveyReachability = new Map();
        function surveyOptionLabel(v) { return __labels[v] || null; }
        function updateImageAdjustments() {}
        function updateSurveyCredit() {}
        function resolveHipsId(v) { return v.startsWith('http') ? v : __roots[v]; }
        function chooseDisplayedBase(v) { return { survey: v, notice: false }; }
        function onSurveyLayerError() {}
        function showSurveyNotice(msg) { __notice(msg); }
        async function novaAlert() {}
        ${fn(src, 'probeHips')}
        ${fn(src, 'surveyNoticeLabel')}
        ${fn(src, 'surveyUnreachableMessage')}
        ${fn(src, 'surveyProbeUrl')}
        ${fn(src, 'surveyReachable')}
        ${fn(src, 'resetSurveyReachability')}
        ${fn(src, 'setSurvey')}
        ${fn(src, 'ensureBlendLayer')}
        ({ setSurvey, ensureBlendLayer, surveyReachable,
           baseLayer: () => __aladin.getBaseImageLayer(),
           blendLayer: () => __aladin.getOverlayImageLayer('blend'),
           setBaseValue: v => { __selects['survey-select'].value = v; },
           setBlendValue: v => { __selects['blend-survey-select'].value = v; } })
    `, Object.assign(ctx, { __aladin: aladin, __labels: input.labels, __roots: input.roots,
                            __selects: els, __notice: m => notices.push(m) }));

    for (const step of c.steps) {
        // A step can turn a survey reachable (or not) between attempts, and advance
        // the clock, so the TTL is exercised rather than assumed.
        if (step.reachable !== undefined) c.reachable[step.value] = step.reachable;
        if (step.advanceMs) clock += step.advanceMs;
        if (step.kind === 'base') { api.setBaseValue(step.value); await api.setSurvey(step.value); }
        else if (step.kind === 'blend') { api.setBlendValue(step.value); await api.ensureBlendLayer(); }
        else if (step.kind === 'concurrent') {
            // Both start before either settles.
            await Promise.all([api.surveyReachable(step.value), api.surveyReachable(step.value)]);
        }
    }
    return { log, notices, swaps, removed,
             baseLayer: api.baseLayer() ? api.baseLayer().id : null,
             blendLayer: api.blendLayer() ? api.blendLayer().id : null };
}

(async () => {
    const out = {};
    for (const [name, c] of Object.entries(input.cases)) out[name] = await runCase(c);
    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    root_to_value = {url: sid for sid, url in ROOTS.items()}
    cases = {
        # base layer: the requested survey does not answer
        "baseUnreachable": {
            "baseValue": "P/GALEXGR6/AIS/color", "blendValue": "P/DSS2/color", "blendOpacity": 0,
            "currentBase": "P/DSS2/color", "currentBlend": None,
            "reachable": {"P/GALEXGR6/AIS/color": False},
            "steps": [{"kind": "base", "value": "P/GALEXGR6/AIS/color"}],
        },
        # base layer: it answers, so the swap happens
        "baseReachable": {
            "baseValue": "P/GALEXGR6/AIS/color", "blendValue": "P/DSS2/color", "blendOpacity": 0,
            "currentBase": "P/DSS2/color", "currentBlend": None,
            "reachable": {"P/GALEXGR6/AIS/color": True},
            "steps": [{"kind": "base", "value": "P/GALEXGR6/AIS/color"}],
        },
        # blend layer: the requested blend does not answer; the existing one stays
        "blendUnreachable": {
            "baseValue": "P/DSS2/color", "blendValue": "P/SDSS9/color", "blendOpacity": 0.5,
            "currentBase": "P/DSS2/color", "currentBlend": "P/DSS2/color",
            "reachable": {"P/SDSS9/color": False},
            "steps": [{"kind": "blend", "value": "P/SDSS9/color"}],
        },
        # blend layer: it answers, so the swap happens
        "blendReachable": {
            "baseValue": "P/DSS2/color", "blendValue": "P/SDSS9/color", "blendOpacity": 0.5,
            "currentBase": "P/DSS2/color", "currentBlend": "P/DSS2/color",
            "reachable": {"P/SDSS9/color": True},
            "steps": [{"kind": "blend", "value": "P/SDSS9/color"}],
        },
        # the probe is memoised: a second call for the same survey does not re-probe
        "memoised": {
            "baseValue": "P/GALEXGR6/AIS/color", "blendValue": "P/GALEXGR6/AIS/color", "blendOpacity": 0.5,
            "currentBase": "P/DSS2/color", "currentBlend": None,
            "reachable": {"P/GALEXGR6/AIS/color": False},
            "steps": [{"kind": "base", "value": "P/GALEXGR6/AIS/color"},
                      {"kind": "base", "value": "P/GALEXGR6/AIS/color"}],
        },
        # A failure older than the TTL is retried on the next attempt: the survey
        # comes back, and picking it again must load it.
        "failureExpires": {
            "baseValue": "P/GALEXGR6/AIS/color", "blendValue": "P/DSS2/color", "blendOpacity": 0,
            "currentBase": "P/DSS2/color", "currentBlend": None,
            "reachable": {"P/GALEXGR6/AIS/color": False},
            "steps": [{"kind": "base", "value": "P/GALEXGR6/AIS/color"},
                      {"kind": "base", "value": "P/GALEXGR6/AIS/color",
                       "advanceMs": 61000, "reachable": True}],
        },
        # Inside the TTL the failure is still remembered: no second probe, no swap.
        "failureNotYetExpired": {
            "baseValue": "P/GALEXGR6/AIS/color", "blendValue": "P/DSS2/color", "blendOpacity": 0,
            "currentBase": "P/DSS2/color", "currentBlend": None,
            "reachable": {"P/GALEXGR6/AIS/color": False},
            "steps": [{"kind": "base", "value": "P/GALEXGR6/AIS/color"},
                      {"kind": "base", "value": "P/GALEXGR6/AIS/color",
                       "advanceMs": 59000, "reachable": True}],
        },
        # A success is kept for the session: no re-probe even long after the TTL.
        "successKept": {
            "baseValue": "P/GALEXGR6/AIS/color", "blendValue": "P/DSS2/color", "blendOpacity": 0,
            "currentBase": "P/DSS2/color", "currentBlend": None,
            "reachable": {"P/GALEXGR6/AIS/color": True},
            "steps": [{"kind": "base", "value": "P/GALEXGR6/AIS/color"},
                      {"kind": "base", "value": "P/GALEXGR6/AIS/color", "advanceMs": 600000}],
        },
        # Two callers asking at the same moment share one probe: the in-flight promise
        # is handed back rather than a second request going out (the blend layer's
        # several call sites must not each probe).
        "inFlightShared": {
            "baseValue": "P/DSS2/color", "blendValue": "P/GALEXGR6/AIS/color", "blendOpacity": 0.5,
            "currentBase": "P/DSS2/color", "currentBlend": "P/DSS2/color",
            "reachable": {"P/GALEXGR6/AIS/color": False},
            "steps": [{"kind": "concurrent", "value": "P/GALEXGR6/AIS/color"}],
        },
    }
    tmp = tmp_path_factory.mktemp("framing_preflight")
    driver, inp, result = tmp / "driver.cjs", tmp / "in.json", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    inp.write_text(json.dumps({"i18n": I18N, "labels": LABELS, "roots": ROOTS,
                               "rootToValue": root_to_value, "cases": cases}), encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(inp), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_an_unreachable_survey_is_not_swapped_and_is_named(out):
    """GALEX does not answer: the base layer is not swapped (DSS2 stays), and the
    notice names the survey asked for and the one still shown."""
    res = out["baseUnreachable"]
    assert res["swaps"] == [], "no layer swap on a survey that did not answer"
    assert res["baseLayer"] == ROOTS["P/DSS2/color"], "the current base layer is kept"
    assert res["notices"] == ["GALEX (color) is not responding — keeping DSS2 (color)."]


def test_a_reachable_survey_is_swapped(out):
    """The probe passing must not block the normal swap."""
    res = out["baseReachable"]
    assert res["swaps"] == [ROOTS["P/GALEXGR6/AIS/color"]]
    assert res["notices"] == []


def test_an_unreachable_blend_keeps_the_existing_blend_layer(out):
    res = out["blendUnreachable"]
    assert res["blendLayer"] == ROOTS["P/DSS2/color"], "the blend layer already shown is kept"
    assert res["notices"] == ["SDSS9 (color) is not responding — keeping DSS2 (color)."]


def test_a_reachable_blend_is_swapped(out):
    res = out["blendReachable"]
    assert res["blendLayer"] == ROOTS["P/SDSS9/color"]
    assert res["notices"] == []


def test_the_reachability_probe_is_memoised_per_survey(out):
    """Two base changes to the same unreachable survey probe once: the second call
    reuses the answer instead of hitting the network again."""
    res = out["memoised"]
    assert res["log"] == ["probe P/GALEXGR6/AIS/color -> fail"], res["log"]
    assert len(res["notices"]) == 2, "each attempt still tells the user"


def test_a_failed_probe_expires_and_the_survey_is_retried(out):
    """Framing is a tab, opened once per page load. A failure must not be remembered
    for the whole session: after the TTL, picking the survey again probes afresh and
    loads it once the upstream is back."""
    res = out["failureExpires"]
    assert res["log"] == ["probe P/GALEXGR6/AIS/color -> fail",
                          "probe P/GALEXGR6/AIS/color -> ok"], res["log"]
    assert res["swaps"] == [ROOTS["P/GALEXGR6/AIS/color"]], "the retry swaps the layer"
    assert res["notices"] == ["GALEX (color) is not responding — keeping DSS2 (color)."], \
        "only the first attempt failed"


def test_a_failure_inside_the_ttl_is_still_remembered(out):
    """Just under the TTL the answer stands: no second probe, no swap. This is what
    stops the opacity slider re-probing on every drag."""
    res = out["failureNotYetExpired"]
    assert res["log"] == ["probe P/GALEXGR6/AIS/color -> fail"], res["log"]
    assert res["swaps"] == []
    assert len(res["notices"]) == 2


def test_a_successful_probe_is_kept_for_the_session(out):
    """Only failures expire: a survey that answered is known good, so it is never
    re-probed in that session even long after the TTL. The swap still happens per
    request -- the memo saves the probe, not the layer change."""
    res = out["successKept"]
    assert res["log"] == ["probe P/GALEXGR6/AIS/color -> ok"], res["log"]
    assert res["swaps"] == [ROOTS["P/GALEXGR6/AIS/color"]] * 2


def test_an_in_flight_probe_is_shared_not_duplicated(out):
    """Two callers asking at the same moment get the one in-flight promise: one
    request, not two."""
    res = out["inFlightShared"]
    assert res["log"] == ["probe P/GALEXGR6/AIS/color -> fail"], res["log"]
