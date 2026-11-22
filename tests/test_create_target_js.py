"""createNewTarget: the object gets the scan's position, the framing gets the frame's.

Entry A ("Make this my target" on a scanned object) has two positions that are not
the same: where the scanned object is, and where the frame currently points. The
object must be created at the first and the framing saved at the second, so the
framing the user was composing is what gets stored -- and both must come from one
snapshot, so a view that moves mid-flight cannot split them.

The real createNewTarget and framingPayload are cut out of graph_view_chart.js and
run in node against a stand-in DOM and a recording fetch. buildFramingQuery is not
stubbed: the object-open URL is read back from a stub window.location that records
the assignment, so the J2000 centre in the URL is checked on the real builder.

The error paths run in the same driver: a framing that will not save (alert, and the
new target is opened anyway), a create request that never reaches the server (alert,
no navigation), a 409 on some other object (opened with the current frame, nothing
saved), and the busy state (the trigger button is disabled while the request runs,
a second click is ignored, and the button comes back after an error).
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
    throw new Error('unterminated at ' + start);
}
function fn(name) {
    const hits = [...src.matchAll(new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(', 'g'))];
    if (hits.length !== 1) throw new Error(`${name}: ${hits.length} declarations`);
    return cut(src, hits[0].index);
}

class El {
    constructor(id, tag = 'div') {
        this.id = id; this.tagName = tag.toUpperCase(); this._value = ''; this.checked = false;
        this.options = []; this.selectedIndex = -1; this.dataset = {}; this.hidden = false;
        this.disabled = false;
    }
    get value() { return this._value; }
    set value(v) { this._value = String(v); }
}

const els = {};
function add(e) { els[e.id] = e; return e; }
const rigSel = add(new El('framing-rig-select', 'select'));
rigSel.options = [{ value: '156', dataset: { rigUid: 'rig-uid', fovw: '41.2', fovh: '27.5' } }];
rigSel.selectedIndex = 0;
add(new El('framing-rotation', 'input')).value = '157.5';
add(new El('survey-select', 'select')).value = input.survey;
add(new El('blend-survey-select', 'select')).value = '';
add(new El('blend-opacity', 'input')).value = '0';
add(new El('mosaic-cols', 'input')).value = '2';
add(new El('mosaic-rows', 'input')).value = '1';
add(new El('mosaic-overlap', 'input')).value = '15';
add(new El('img-bright', 'input')).value = '0';
add(new El('img-contrast', 'input')).value = '0.2';
add(new El('img-gamma', 'input')).value = '1';
add(new El('img-sat', 'input')).value = '0';
add(new El('show-geo-belt', 'input')).checked = true;
const btn = add(new El('new-target-confirm', 'button'));

const posts = [], assign = [];
const ctx = {
    console: { warn: () => {}, log: () => {}, error: () => {} },
    URL, URLSearchParams, Math, JSON, Number, String, Array, Object, Promise, Error, isFinite,
    parseFloat, parseInt, encodeURIComponent, sessionStorage: { getItem: () => null },
    document: { getElementById: id => els[id] || null },
    window: {
        location: { set href(v) { assign.push(v); }, get href() { return assign[assign.length - 1]; } },
        // Aladin's live centre: what frameCenter() reads at click time.
        __liveCenter: { ra: input.frameRa, dec: input.frameDec },
    },
    novaI18n: input.i18n,
    fetch: (url, opts) => {
        const body = opts && opts.body ? JSON.parse(opts.body) : null;
        posts.push({ url, body });
        const reply = input.replies[url] !== undefined ? input.replies[url] : { status: 'success' };
        const answer = r => ({ ok: !(r.__status >= 400), json: () => Promise.resolve(r) });
        // __throw: the request never reaches the server (offline, server gone).
        if (reply.__throw) return Promise.reject(new TypeError('Failed to fetch'));
        // __defer: the answer is held back until the driver calls ctx.__answer(reply).
        if (reply.__defer) return new Promise(res => { ctx.__answer = r => res(answer(r)); });
        return Promise.resolve(answer(reply));
    },
};
ctx.t = (k, params) => {
    let s = ctx.novaI18n[k];
    if (s === undefined) return k;
    if (params) Object.entries(params).forEach(([p, v]) => { s = s.replace(new RegExp('\\{' + p + '\\}', 'g'), v); });
    return s;
};
ctx.novaAlert = async () => {};
ctx.NOVA_GRAPH_DATA = { objectName: input.currentObject };
ctx.window.NOVA_GRAPH_DATA = ctx.NOVA_GRAPH_DATA;
ctx.window.t = ctx.t;
ctx.window.novaAlert = ctx.novaAlert;
vm.createContext(ctx);

const script = `
    let aladin = {};   // buildFramingQuery only checks it is truthy
    let liveCenter = window.__liveCenter;
    let createTargetBusy = false;   // graph_view_chart.js declares it beside createNewTarget
    const __alerts = [];
    function frameCenter() { return { ra: liveCenter.ra, dec: liveCenter.dec }; }
    function fu() { return { normalizeAngle: a => ((a % 360) + 360) % 360 }; }
    function novaAlert(m) { __alerts.push(m); }
    ${fn('sameTargetName')}
    ${fn('framingPayload')}
    ${fn('buildFramingQuery')}
    ${fn('goToObjectFraming')}
    ${fn('createNewTarget')}
    ({
        createNewTarget, goToObjectFraming, framingPayload, sameTargetName,
        alerts: () => __alerts,
        setLiveCenter: (ra, dec) => { liveCenter = { ra, dec }; },
        // A successful create stays busy because the page navigates away; the next
        // scenario starts on a fresh page.
        freshPage: () => { createTargetBusy = false; },
    })
`;
const api = vm.runInContext(script, ctx);
const settle = () => new Promise(r => setImmediate(r));

(async () => {
    const out = {};

    // 1. Entry A: the scanned object's position vs the frame's.
    await api.createNewTarget(input.name,
        { ra: input.scanRa, dec: input.scanDec },
        { ra: input.frameRa, dec: input.frameDec },
        { type: 'Dark Nebula', size: '42.50' });
    await settle();
    out.create = posts.find(p => p.url === '/api/create_object').body;
    out.framing = posts.find(p => p.url === '/api/save_framing').body;
    out.url = assign[assign.length - 1];
    out.alerts = api.alerts().slice();

    // 2. The 409 on the object already open: no navigation, a notice instead.
    api.freshPage();
    posts.length = 0; assign.length = 0;
    input.replies['/api/create_object'] = { status: 'error', code: 'already_exists',
                                            object_name: input.currentObject, __status: 409 };
    await api.createNewTarget(input.currentObject, { ra: 1, dec: 2 }, { ra: 1, dec: 2 }, {});
    await settle();
    out.selfCollision = { url: assign.slice(), alerts: api.alerts().slice(),
                          saves: posts.filter(p => p.url === '/api/save_framing').length };

    // Each further scenario starts on a fresh page with its own replies.
    const scenario = (replies) => {
        api.freshPage();
        posts.length = 0; assign.length = 0;
        input.replies = replies;
        btn.disabled = false; delete btn.dataset.busy;
        return api.alerts().length;           // alerts before this scenario
    };
    const report = (alertsFrom) => ({
        url: assign.slice(),
        alerts: api.alerts().slice(alertsFrom),
        creates: posts.filter(p => p.url === '/api/create_object').length,
        saves: posts.filter(p => p.url === '/api/save_framing').map(p => p.body),
        btnDisabled: btn.disabled,
        btnBusy: btn.dataset.busy === undefined ? null : btn.dataset.busy,
    });
    const created = { status: 'success', object_name: 'VDB 1', record_uid: 'u1' };

    // 3. The object is created but its framing does not save: say so, open it anyway.
    let a = scenario({ '/api/create_object': created,
                       '/api/save_framing': { status: 'error', message: 'disk full', __status: 500 } });
    await api.createNewTarget('vdb 1', { ra: 10, dec: 20 }, { ra: 10, dec: 20 }, {}, btn);
    await settle();
    out.framingFails = report(a);

    // 4. The create request itself fails (offline): an alert, no navigation, no save,
    //    and the button is usable again.
    a = scenario({ '/api/create_object': { __throw: true } });
    await api.createNewTarget('vdb 1', { ra: 10, dec: 20 }, { ra: 10, dec: 20 }, {}, btn);
    await settle();
    out.createFails = report(a);

    // 5. A 409 on another object: open that one with the CURRENT frame, save nothing.
    a = scenario({ '/api/create_object': { status: 'error', code: 'already_exists',
                                           object_name: 'NGC 7000', __status: 409 } });
    api.setLiveCenter(314.7, 44.5);
    await api.createNewTarget('ngc7000', { ra: 1, dec: 2 }, { ra: 1, dec: 2 }, {}, btn);
    await settle();
    out.otherCollision = report(a);

    // 6. A 409 on the open object under another spelling ("M 42" page, "M42" stored).
    a = scenario({ '/api/create_object': { status: 'error', code: 'already_exists',
                                           object_name: 'M42', __status: 409 } });
    const openedAs = ctx.NOVA_GRAPH_DATA.objectName;
    ctx.NOVA_GRAPH_DATA.objectName = 'M 42';
    await api.createNewTarget('m42', { ra: 1, dec: 2 }, { ra: 1, dec: 2 }, {}, btn);
    await settle();
    ctx.NOVA_GRAPH_DATA.objectName = openedAs;
    out.selfCollisionSpelling = report(a);

    // 7. Busy: disabled while the request runs, a second click ignored, back after an error.
    a = scenario({ '/api/create_object': { __defer: true } });
    const first = api.createNewTarget('vdb 1', { ra: 10, dec: 20 }, { ra: 10, dec: 20 }, {}, btn);
    await settle();
    const during = { disabled: btn.disabled, busy: btn.dataset.busy };
    await api.createNewTarget('vdb 1', { ra: 10, dec: 20 }, { ra: 10, dec: 20 }, {}, btn);
    during.creates = posts.filter(p => p.url === '/api/create_object').length;
    ctx.__answer({ status: 'error', message: 'boom', __status: 500 });
    await first;
    await settle();
    out.busy = { during, after: report(a) };

    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run(tmp_path, payload):
    driver = tmp_path / "driver.cjs"
    inp = tmp_path / "in.json"
    out = tmp_path / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    inp.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(inp), str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_entry_a_separates_object_position_from_frame_centre(tmp_path):
    result = _run(tmp_path, {
        "name": "Barnard 143",
        "scanRa": 285.0, "scanDec": 9.9,          # the scanned object
        "frameRa": 285.6, "frameDec": 10.4,       # where the frame actually points
        "currentObject": "NGC 3628",
        "survey": "P/DSS2/color",
        # What the server answers: the canonical name, which the framing then uses.
        "replies": {"/api/create_object": {"status": "success",
                                           "object_name": "BARNARD 143",
                                           "record_uid": "abc123"}},
        "i18n": {
            "target_name_required": "Enter a name for the new target.",
            "target_create_error": "Could not create the target",
            "target_framing_not_saved": "The target was created, but the framing could not be saved.",
            "target_is_current": "This is already the open target. Use Save Framing to keep this view.",
            "target_already_exists": "{name} is already in your library.",
        },
    })

    # The object is created at the scanned position, in hours.
    assert result["create"]["name"] == "Barnard 143"
    assert result["create"]["ra"] == pytest.approx(285.0 / 15)
    assert result["create"]["dec"] == pytest.approx(9.9)
    assert result["create"]["type"] == "Dark Nebula"
    assert result["create"]["size"] == "42.50"

    # The framing is saved at the FRAME centre, in degrees, not the object's.
    assert result["framing"]["ra"] == pytest.approx(285.6)
    assert result["framing"]["dec"] == pytest.approx(10.4)
    assert result["framing"]["ra"] != result["create"]["ra"] * 15
    # ...and it carries the whole frame state, not just the centre.
    assert result["framing"]["rotation"] == pytest.approx(157.5)
    assert result["framing"]["mosaic_cols"] == 2
    assert result["framing"]["mosaic_overlap"] == pytest.approx(15)
    assert result["framing"]["img_contrast"] == pytest.approx(0.2)
    assert result["framing"]["geo_belt_enabled"] is True

    # The saved framing's name is the server's, and the URL carries the FRAME centre.
    assert result["framing"]["object_name"] == "BARNARD 143"
    assert "ra=285.6" in result["url"] and "dec=10.4" in result["url"]
    assert "tab=framing" in result["url"] and "openframing=1" in result["url"]
    assert "BARNARD%20143" in result["url"]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_collision_with_the_open_target_does_not_navigate(tmp_path):
    result = _run(tmp_path, {
        "name": "M42",
        "scanRa": 83.8, "scanDec": -5.4,
        "frameRa": 83.8, "frameDec": -5.4,
        "currentObject": "M42",
        "survey": "P/DSS2/color",
        "replies": {},
        "i18n": {
            "target_name_required": "Enter a name for the new target.",
            "target_create_error": "Could not create the target",
            "target_framing_not_saved": "The target was created, but the framing could not be saved.",
            "target_is_current": "This is already the open target. Use Save Framing to keep this view.",
            "target_already_exists": "{name} is already in your library.",
        },
    })

    assert result["selfCollision"]["url"] == []       # no navigation, no reload
    assert result["selfCollision"]["saves"] == 0      # and no framing write
    assert any("already the open target" in a for a in result["selfCollision"]["alerts"])


_I18N = {
    "target_name_required": "Enter a name for the new target.",
    "target_create_error": "Could not create the target",
    "target_framing_not_saved": "The target was created, but the framing could not be saved.",
    "target_is_current": "This is already the open target. Use Save Framing to keep this view.",
    "target_already_exists": "{name} is already in your library.",
}


@pytest.fixture
def error_paths(tmp_path):
    return _run(tmp_path, {
        "name": "Barnard 143",
        "scanRa": 285.0, "scanDec": 9.9,
        "frameRa": 285.6, "frameDec": 10.4,
        "currentObject": "NGC 3628",
        "survey": "P/DSS2/color",
        "replies": {"/api/create_object": {"status": "success",
                                           "object_name": "BARNARD 143",
                                           "record_uid": "abc123"}},
        "i18n": _I18N,
    })


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_framing_save_failure_still_opens_the_new_target(error_paths):
    r = error_paths["framingFails"]
    assert r["creates"] == 1
    assert len(r["saves"]) == 1 and r["saves"][0]["object_name"] == "VDB 1"
    assert r["alerts"] == [_I18N["target_framing_not_saved"]]
    assert len(r["url"]) == 1 and "/graph_dashboard/VDB%201?" in r["url"][0]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_create_request_failure_alerts_and_stays(error_paths):
    r = error_paths["createFails"]
    assert r["alerts"] == [_I18N["target_create_error"]]
    assert r["url"] == []                  # no navigation
    assert r["saves"] == []                # and no framing write
    assert r["btnDisabled"] is False and r["btnBusy"] is None


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_collision_with_another_object_opens_it_with_the_current_frame(error_paths):
    r = error_paths["otherCollision"]
    assert r["saves"] == []                # nothing saved on either object
    assert r["alerts"] == ["NGC 7000 is already in your library."]
    assert len(r["url"]) == 1
    url = r["url"][0]
    assert url.startswith("/graph_dashboard/NGC%207000?")
    # The frame on screen at that moment, not the position that was sent.
    assert "ra=314.700000" in url and "dec=44.500000" in url
    assert "rig=156" in url and "rot=158" in url and "m_cols=2" in url
    assert "tab=framing" in url and "openframing=1" in url
    assert r["btnDisabled"] is False


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_collision_with_the_open_target_under_another_spelling(error_paths):
    r = error_paths["selfCollisionSpelling"]
    assert r["url"] == []
    assert r["saves"] == []
    assert r["alerts"] == [_I18N["target_is_current"]]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_button_is_disabled_while_the_create_runs(error_paths):
    b = error_paths["busy"]
    assert b["during"]["disabled"] is True
    assert b["during"]["busy"] == "1"
    assert b["during"]["creates"] == 1     # the second click sent nothing
    after = b["after"]
    assert after["btnDisabled"] is False and after["btnBusy"] is None
    assert after["url"] == []
    assert after["alerts"] == [_I18N["target_create_error"] + ": boom"]
