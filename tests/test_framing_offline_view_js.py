"""Offline view is switched by connectivity, never by one survey failing, in node.

graph_view_chart.js: onSurveyLayerError fires whenever an Aladin survey layer fails
to load -- one survey, not the network. It must leave Offline view exactly as it
was, because only the browser's own offline/online events switch that automatically.

The functions are cut out of the real file by name and run against stand-ins, so
what is pinned is the real onSurveyLayerError and the real setOfflineView.
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

const log = [];
const toggle = { id: 'offline-view-toggle', checked: false };
const notice = null;
const ctx = {
    console: { warn: () => {}, log: () => {}, error: () => {} },
    document: {
        getElementById: id => (id === 'offline-view-toggle' ? toggle : null),
        createElement: () => ({ style: {}, setAttribute() {}, append() {}, addEventListener() {} }),
    },
    window: {},
};
ctx.window.t = k => k;
ctx.window.NOVA_OFFLINE_SKY = true;
ctx.__log = s => log.push(s);
vm.createContext(ctx);

// The real functions, with only their collaborators replaced by stand-ins that
// record what was called. setOfflineView is the real one: the point of the test
// is the checkbox and the remembered user setting it maintains.
const api = vm.runInContext(`
    let offlineViewOn = false;
    let offlineViewUserSetting = null;
    let surveyNoticeDismissed = false;
    let aladin = null;
    let displayedBaseSurvey = 'P/DSS2/color';
    function applyOfflineViewToLayers() { __log('applyLayers'); }
    function updateOfflineViewUi() { __log('updateUi'); }
    function showSurveyNotice() { __log('notice'); }
    function hideSurveyNotice() { __log('hideNotice'); }
    function surveyOptionLabel(v) { return v; }
    function offlineBaseId() { return 'P/DSS2/color'; }
    ${fn(src, 'surveyNoticeLabel')}
    ${fn(src, 'surveyFailureMessage')}
    ${fn(src, 'setOfflineView')}
    ${fn(src, 'onSurveyLayerError')}
    ({ setOfflineView, onSurveyLayerError,
       state: () => ({ on: offlineViewOn, userSetting: offlineViewUserSetting }) })
`, ctx);

const out = {};

// A layer error with Offline view off and no user setting: nothing may change.
api.onSurveyLayerError(new Error('survey layer failed'));
out.afterError = { state: api.state(), checked: toggle.checked, log: log.slice() };

// The user turned it off explicitly; a layer error must not put it back on.
log.length = 0;
api.setOfflineView(false, true);
out.afterUserOff = { state: api.state(), checked: toggle.checked, log: log.slice() };
log.length = 0;
api.onSurveyLayerError(new Error('survey layer failed again'));
out.afterErrorPostUserOff = { state: api.state(), checked: toggle.checked, log: log.slice() };

// The real switch still works when connectivity actually changes.
log.length = 0;
api.setOfflineView(true, false);
out.afterConnectivityOff = { state: api.state(), checked: toggle.checked, log: log.slice() };

fs.writeFileSync(outFile, JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_offline_view")
    driver, result = tmp / "driver.cjs", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_a_layer_error_leaves_offline_view_off(out):
    """One survey failing is not a connectivity fact: the checkbox stays clear and
    no user setting is invented. The notice still fires -- the failure is reported,
    just not as an offline switch."""
    assert out["afterError"] == {
        "state": {"on": False, "userSetting": None},
        "checked": False,
        "log": ["notice"],
    }


def test_a_layer_error_does_not_override_the_users_own_setting(out):
    """The user unticked Offline view. A survey then fails to load: the box must
    stay unticked, and the remembered setting must stay false -- not be flipped
    back on by the failure."""
    assert out["afterUserOff"] == {
        "state": {"on": False, "userSetting": False},
        "checked": False,
        "log": ["hideNotice", "applyLayers", "updateUi"],
    }
    assert out["afterErrorPostUserOff"] == {
        "state": {"on": False, "userSetting": False},
        "checked": False,
        "log": ["notice"],
    }


def test_set_offline_view_still_switches_when_connectivity_changes(out):
    """The switch itself is untouched: it is still what the offline/online events
    and the toggle call. An automatic switch (fromUser false) turns the view on
    without overwriting the user's remembered setting, which stays as they left it
    so that going online restores their own choice."""
    assert out["afterConnectivityOff"] == {
        "state": {"on": True, "userSetting": False},
        "checked": True,
        "log": ["hideNotice", "applyLayers", "updateUi"],
    }


def test_on_survey_layer_error_never_calls_set_offline_view():
    """Source check: the call site that flipped Offline view on a layer error is gone."""
    chart = CHART_JS.read_text(encoding="utf-8")
    body = chart[chart.index("function onSurveyLayerError("):]
    body = body[:body.index("\n    }") + len("\n    }")]
    assert "setOfflineView" not in body
    assert "showSurveyNotice" in body


# ---------------------------------------------------------------------------
# The notice clears itself when the situation it describes is over.
# ---------------------------------------------------------------------------

_NOTICE_DRIVER = r"""
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

// A stand-in for #survey-notice inside #aladin-lite-div, enough for show/hide.
const host = { id: 'aladin-lite-div', children: [], appendChild(c) { this.children.push(c); } };
let noticeEl = null;
function makeEl(tag) {
    return {
        tagName: tag, children: [], hidden: true, className: '', id: '',
        _text: '', _listeners: {},
        setAttribute(k, v) { this[k] = v; },
        append(...kids) { kids.forEach(k => this.children.push(k)); },
        addEventListener(ev, f) { (this._listeners[ev] = this._listeners[ev] || []).push(f); },
        get textContent() { return this._text; },
        set textContent(v) { this._text = v; },
        querySelector(sel) {
            if (sel === '.survey-notice-text') return this.children.find(c => c.className === 'survey-notice-text') || null;
            return null;
        },
    };
}
const toggle = { id: 'offline-view-toggle', checked: false };
const surveySelect = { id: 'survey-select', value: 'P/GALEXGR6/AIS/color' };
const els = { 'aladin-lite-div': host, 'offline-view-toggle': toggle, 'survey-select': surveySelect };

const ctx = {
    console: { warn: () => {}, log: () => {}, error: () => {} },
    // The real setSurvey pre-flights through probeHips: every survey answers here,
    // so the swap proceeds and the notice-clearing tail is what the test exercises.
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    setTimeout: () => 0, clearTimeout: () => {},
    fetch: () => Promise.resolve({ ok: true, status: 200 }),
    document: {
        getElementById: id => {
            if (id === 'survey-notice') return noticeEl;
            return els[id] || null;
        },
        createElement: tag => { const e = makeEl(tag); if (tag === 'div') noticeEl = e; return e; },
        createTextNode: t => ({ textContent: t }),
    },
    window: {},
};
ctx.window.t = (k, params) => {
    const s = { survey_not_responding: '{survey} is not responding — keeping {current}.',
                survey_not_stored_offline: "{survey} isn't stored offline — keeping {current}.",
                survey_server_unreachable: 'Sky survey server unreachable. Showing a fallback survey where available.',
                close: 'Close' }[k];
    if (s === undefined) return k;
    return params ? Object.entries(params).reduce(
        (a, [p, v]) => a.replace(new RegExp('\\{' + p + '\\}', 'g'), v), s) : s;
};
ctx.window.NOVA_OFFLINE_SKY = true;
vm.createContext(ctx);

const api = vm.runInContext(`
    const CDS_PROBE_TIMEOUT_MS = 4000;
    const SURVEY_PROBE_FAIL_TTL_MS = 60 * 1000;
    let offlineViewOn = true;          // start in Offline view, as the scenario does
    let offlineViewUserSetting = null;
    let surveyNoticeDismissed = false;
    let aladin = {
        createImageSurvey(id, name) { return { id, name }; },
        setBaseImageLayer() {}, getBaseImageLayer() { return {}; },
    };
    let displayedBaseSurvey = 'P/DSS2/color';
    let displayedBlendSurvey = null;
    let baseSurvey = null;
    const surveyReachability = new Map();
    function surveyOptionLabel(v) {
        return ({ 'P/DSS2/color': 'DSS2 (color)', 'P/GALEXGR6/AIS/color': 'GALEX (color)' })[v] || null;
    }
    function offlineBaseId() { return __selects['survey-select'].value; }
    function applyOfflineViewToLayers() {}
    function updateOfflineViewUi() {}
    function updateImageAdjustments() {}
    function updateSurveyCredit() {}
    function resolveHipsId(v) { return v.startsWith('http') ? v : 'http://nova.test/hips/' + v; }
    function chooseDisplayedBase(v) { return { survey: v, notice: false }; }
    function onSurveyLayerError() {}
    async function novaAlert() {}
    ${fn(src, 'probeHips')}
    ${fn(src, 'surveyNoticeLabel')}
    ${fn(src, 'surveyFailureMessage')}
    ${fn(src, 'surveyProbeUrl')}
    ${fn(src, 'surveyReachable')}
    ${fn(src, 'showSurveyNotice')}
    ${fn(src, 'hideSurveyNotice')}
    ${fn(src, 'setOfflineView')}
    ${fn(src, 'setSurvey')}
    ({ showSurveyNotice, hideSurveyNotice, setOfflineView, setSurvey,
       noticeText: () => {
           const el = document.getElementById('survey-notice');
           if (!el || el.hidden) return null;
           const t = el.querySelector('.survey-notice-text');
           return t ? t.textContent : null;
       },
       setSurveyValue: v => { __selects['survey-select'].value = v; } })
`, Object.assign(ctx, { __selects: els }));

const out = {};
const snap = () => api.noticeText();

// 1. A failure in Offline view shows the offline wording.
api.showSurveyNotice();                       // the notice's own default, decided by mode
out.offlineDefault = snap();

// 2. Unticking Offline view clears it: the situation it described is over.
api.setOfflineView(false, true);
out.afterUntick = snap();

// 3. A survey then loads successfully: the real setSurvey, whose tail clears the
//    notice once the new layer is on.
api.setSurveyValue('P/DSS2/color');
(async () => {
    await api.setSurvey('P/DSS2/color');
    out.afterLoad = snap();

    // 4. And a later failure outside Offline view uses the server wording.
    api.showSurveyNotice();
    out.afterLaterFailure = snap();

    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def notice_out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_notice")
    driver, result = tmp / "notice.cjs", tmp / "out.json"
    driver.write_text(_NOTICE_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_offline_view_default_notice_is_the_not_stored_wording(notice_out):
    """The notice's own default, with no message passed, must be the offline wording
    while Offline view is on -- never the server text."""
    assert notice_out["offlineDefault"] == \
        "GALEX (color) isn't stored offline — keeping DSS2 (color)."
    assert "unreachable" not in notice_out["offlineDefault"]


def test_the_notice_clears_when_offline_view_is_unticked(notice_out):
    """Scenario: notice shown in Offline view -> untick -> gone."""
    assert notice_out["afterUntick"] is None


def test_the_notice_stays_gone_after_a_survey_loads(notice_out):
    """...and the survey loading successfully leaves it gone."""
    assert notice_out["afterLoad"] is None


def test_a_later_failure_outside_offline_view_uses_the_server_wording(notice_out):
    """The clearing must not disable the notice: a failure after leaving Offline view
    still reports, in the wording that fits that mode."""
    assert notice_out["afterLaterFailure"] == \
        "Sky survey server unreachable. Showing a fallback survey where available."


def test_a_successful_base_swap_clears_the_notice_in_the_real_code():
    """Source check: the step above is a stand-in. In the real file setSurvey's tail
    clears the notice once the new layer is on, before it re-shows the CDS-outage one
    -- so a survey that loads leaves no stale failure notice behind."""
    src = CHART_JS.read_text(encoding="utf-8")
    tail = src[src.index("aladin.setBaseImageLayer(newLayer);"):]
    tail = tail[:tail.index("\n    }")]
    assert "hideSurveyNotice();" in tail, "setSurvey does not clear the notice on success"
    assert tail.index("hideSurveyNotice();") < tail.index("if (choice.notice)"), \
        "the clear must come before the CDS-outage notice is re-shown"
