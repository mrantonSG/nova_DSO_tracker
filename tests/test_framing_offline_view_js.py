"""Offline view is switched by connectivity, never by one survey failing, in node.

graph_view_chart.js: a survey layer's errorCallback (set by createSurveyLayer)
fires whenever an Aladin survey layer fails to load -- one survey, not the network.
It must leave Offline view exactly as it was, because only the browser's own
offline/online events switch that automatically.

The functions are cut out of the real file by name and run against stand-ins, so
what is pinned is the real createSurveyLayer and the real setOfflineView.
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
ctx.window.NOVA_OFFLINE_SKY_UI = true;
ctx.__log = s => log.push(s);
vm.createContext(ctx);

// The real functions, with only their collaborators replaced by stand-ins that
// record what was called. setOfflineView is the real one: the point of the test
// is the checkbox and the remembered user setting it maintains.
const api = vm.runInContext(`
    let offlineViewOn = false;
    let offlineViewUserSetting = null;
    let surveyNoticeDismissed = false;
    let aladin = { createImageSurvey: (id, name, url, frame, maxOrder, opts) => ({ id, opts }) };
    let displayedBaseSurvey = 'P/DSS2/color';
    function applyOfflineViewToLayers() { __log('applyLayers'); }
    function updateScanButtonState() { __log('updateScanButtonState'); }
    function showSurveyNotice() { __log('notice'); }
    function hideSurveyNotice() { __log('hideNotice'); }
    function surveyOptionLabel(v) { return v; }
    function onSurveyLayerShown() {}
    ${fn(src, 'setOfflineView')}
    ${fn(src, 'createSurveyLayer')}
    // A layer's failure, through the errorCallback Aladin calls.
    function failLayer(msg) {
        const layer = createSurveyLayer('base', 'P/GALEXGR6/AIS/color', 'http://nova.test/hips/galex');
        layer.opts.errorCallback(new Error(msg));
    }
    ({ setOfflineView, failLayer,
       state: () => ({ on: offlineViewOn, userSetting: offlineViewUserSetting }) })
`, ctx);

const out = {};
(async () => {
// A layer error with Offline view off and no user setting: nothing may change.
api.failLayer('survey layer failed');
out.afterError = { state: api.state(), checked: toggle.checked, log: log.slice() };

// The user turned it off explicitly; a layer error must not put it back on.
log.length = 0;
api.setOfflineView(false, true);
out.afterUserOff = { state: api.state(), checked: toggle.checked, log: log.slice() };
log.length = 0;
api.failLayer('survey layer failed again');
out.afterErrorPostUserOff = { state: api.state(), checked: toggle.checked, log: log.slice() };

// The real switch still works when connectivity actually changes.
log.length = 0;
api.setOfflineView(true, false);
out.afterConnectivityOff = { state: api.state(), checked: toggle.checked, log: log.slice() };

fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
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
    """One survey failing is not a connectivity fact: the checkbox stays clear, no
    user setting is invented, and nothing is reported."""
    assert out["afterError"] == {
        "state": {"on": False, "userSetting": None},
        "checked": False,
        "log": [],
    }


def test_a_layer_error_does_not_override_the_users_own_setting(out):
    """The user unticked Offline view. A survey then fails to load: the box must
    stay unticked, and the remembered setting must stay false -- not be flipped
    back on by the failure. (Unticking an already-off view records the choice
    but rebuilds nothing.)"""
    assert out["afterUserOff"] == {
        "state": {"on": False, "userSetting": False},
        "checked": False,
        "log": [],
    }
    assert out["afterErrorPostUserOff"] == {
        "state": {"on": False, "userSetting": False},
        "checked": False,
        "log": [],
    }


def test_set_offline_view_still_switches_when_connectivity_changes(out):
    """setOfflineView itself still switches: a call with fromUser false turns the
    view on without overwriting the user's remembered setting. (Whether the
    automatic triggers call it at all is pinned by the tests below.)"""
    assert out["afterConnectivityOff"] == {
        "state": {"on": True, "userSetting": False},
        "checked": True,
        "log": ["applyLayers", "updateScanButtonState"],
    }


def test_a_layer_error_callback_never_calls_set_offline_view():
    """Source check: the layer error path does not switch Offline view."""
    chart = CHART_JS.read_text(encoding="utf-8")
    body = chart[chart.index("function createSurveyLayer("):]
    body = body[:body.index("\n    }") + len("\n    }")]
    assert "errorCallback" in body
    assert "setOfflineView" not in body


# --- The automatic triggers: page opened offline, 'offline', 'online' ---------
#
# The real setOfflineView, autoOfflineView, offlineViewToast and wireOfflineSkyUi,
# each scenario in a fresh context (wireOfflineSkyUi installs its listeners once
# per window). The checkbox's 'change' handler and the window's offline/online
# handlers are captured and fired by hand; toasts are what lands in the stack.

_AUTO_DRIVER = _DRIVER[:_DRIVER.index("const log = [];")] + r"""
function makeEnv(onLine) {
    const log = [], toasts = [], handlers = {};
    const toggle = {
        id: 'offline-view-toggle', checked: false, listeners: {},
        addEventListener(type, f) { this.listeners[type] = f; },
    };
    const stack = { appendChild(el) { toasts.push({ kind: el.className, text: el.__text }); } };
    function makeToast() {
        const el = { className: '', innerHTML: '', __text: '' };
        const text = { set textContent(v) { el.__text = v; } };
        const close = { setAttribute() {} };
        el.querySelector = sel => (sel === '.nova-toast-text' ? text : close);
        return el;
    }
    const ctx = {
        console: { warn: () => {}, log: () => {}, error: () => {} },
        navigator: { onLine },
        document: {
            getElementById: id => (id === 'offline-view-toggle' ? toggle
                                   : id === 'flash-message-container' ? stack : null),
            createElement: makeToast,
        },
        window: { addEventListener(type, f) { handlers[type] = f; } },
    };
    ctx.window.t = k => k;
    ctx.window.NOVA_OFFLINE_SKY = true;
    ctx.window.NOVA_OFFLINE_SKY_UI = true;
    ctx.__log = s => log.push(s);
    vm.createContext(ctx);
    const api = vm.runInContext(`
        let offlineViewOn = false;
        let offlineViewUserSetting = null;
        function offlineUiEnabled() { return !!window.NOVA_OFFLINE_SKY_UI; }
        function applyOfflineViewToLayers() { __log('applyLayers'); }
        function updateScanButtonState() { __log('updateScanButtonState'); }
        ${fn(src, 'setOfflineView')}
        ${fn(src, 'autoOfflineView')}
        ${fn(src, 'offlineViewToast')}
        let offlineStartChecked = false;
        ${fn(src, 'wireOfflineSkyUi')}
        ({ wireOfflineSkyUi,
           state: () => ({ on: offlineViewOn, userSetting: offlineViewUserSetting }) })
    `, ctx);
    api.wireOfflineSkyUi();
    return {
        click(checked) { toggle.checked = checked; toggle.listeners.change(); },
        fire(type) { handlers[type](); },
        snap() {
            const s = { state: api.state(), checked: toggle.checked,
                        log: log.slice(), toasts: toasts.slice() };
            log.length = 0; toasts.length = 0;
            return s;
        },
    };
}

const out = {};

// (a) The user ticked and then unticked the switch; the browser then drops
// offline and comes back. Neither event may touch the view.
let env = makeEnv(true);
env.click(true); env.click(false); env.snap();
env.fire('offline');
out.userOffThenOffline = env.snap();
env.fire('online');
out.userOffThenOnline = env.snap();

// (b) No user choice: offline switches on with a note, online switches off with
// a note, and a second 'offline' while already on changes nothing.
env = makeEnv(true);
env.fire('offline');
out.autoOffline = env.snap();
env.fire('offline');
out.autoOfflineAgain = env.snap();
env.fire('online');
out.autoOnline = env.snap();

// (c) Manual clicks: the view switches, no note.
env = makeEnv(true);
env.click(true);
out.manualOn = env.snap();
env.click(false);
out.manualOff = env.snap();

// A manual "on" survives the browser coming back online.
env = makeEnv(true);
env.click(true); env.snap();
env.fire('offline'); env.fire('online');
out.manualOnSurvivesOnline = env.snap();

// A page opened offline starts in Offline view, silently.
env = makeEnv(false);
out.openedOffline = env.snap();

fs.writeFileSync(outFile, JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def auto(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_offline_view_auto")
    driver, result = tmp / "driver.cjs", tmp / "out.json"
    driver.write_text(_AUTO_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


_ON_NOTE = {"kind": "flash-message nova-toast nova-toast--warning",
            "text": "offline_view_auto_on"}
_OFF_NOTE = {"kind": "flash-message nova-toast nova-toast--success",
             "text": "offline_view_auto_off"}
_REBUILD = ["applyLayers", "updateScanButtonState"]


def test_offline_event_leaves_a_manual_off_alone(auto):
    """(a) The user's own "off" stands: the offline event neither switches the
    view nor rebuilds a layer nor shows a note, and neither does online."""
    quiet = {"state": {"on": False, "userSetting": False}, "checked": False,
             "log": [], "toasts": []}
    assert auto["userOffThenOffline"] == quiet
    assert auto["userOffThenOnline"] == quiet


def test_offline_event_without_a_user_choice_switches_on_with_a_note(auto):
    """(b) No choice made: offline switches on once with the note, a repeat
    event rebuilds nothing, and online switches back off with the other note."""
    assert auto["autoOffline"] == {
        "state": {"on": True, "userSetting": None}, "checked": True,
        "log": _REBUILD, "toasts": [_ON_NOTE],
    }
    assert auto["autoOfflineAgain"] == {
        "state": {"on": True, "userSetting": None}, "checked": True,
        "log": [], "toasts": [],
    }
    assert auto["autoOnline"] == {
        "state": {"on": False, "userSetting": None}, "checked": False,
        "log": _REBUILD, "toasts": [_OFF_NOTE],
    }


def test_a_manual_click_shows_no_note(auto):
    """(c) The switch clicked by hand changes the view but never puts up a note."""
    assert auto["manualOn"] == {
        "state": {"on": True, "userSetting": True}, "checked": True,
        "log": _REBUILD, "toasts": [],
    }
    assert auto["manualOff"] == {
        "state": {"on": False, "userSetting": False}, "checked": False,
        "log": _REBUILD, "toasts": [],
    }


def test_a_manual_on_survives_the_browser_coming_back_online(auto):
    assert auto["manualOnSurvivesOnline"] == {
        "state": {"on": True, "userSetting": True}, "checked": True,
        "log": [], "toasts": [],
    }


def test_a_page_opened_offline_starts_in_offline_view_without_a_note(auto):
    """Notes belong to the offline/online events only; the page-load start
    switches silently."""
    assert auto["openedOffline"] == {
        "state": {"on": True, "userSetting": None}, "checked": True,
        "log": _REBUILD, "toasts": [],
    }
