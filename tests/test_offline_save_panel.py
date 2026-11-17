"""The Framing Assistant's "Save for offline" panel (offline sky, piece B2).

Three parts:
- static/js/offline_save_panel.js in node, on a small stand-in DOM holding the
  panel's template ids, with a scripted fetch and manual timers;
- the server side the panel relies on: old-format saves from the first step-2
  version, and the estimate's resolution field;
- the rendered template and the five catalogs.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import nova.offline_sky_jobs as jobs
from nova import offline_sky_store as store

REPO = Path(__file__).resolve().parent.parent
PANEL_JS = REPO / "static" / "js" / "offline_save_panel.js"
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
GRAPH_VIEW_HTML = REPO / "templates" / "graph_view.html"
NODE = shutil.which("node")

# Ids the panel module reads; the template must have every one of them.
PANEL_IDS = ["save-offline-btn", "offline-save-panel", "offline-save-close", "offline-survey-list",
             "offline-detail-select", "offline-detail-scale", "offline-estimate-total",
             "offline-estimate-free", "offline-save-reason", "offline-save-start", "offline-saves-list"]

SURVEYS = {
    "P/DSS2/color": {"slug": "dss2-color", "label": "DSS2 (color)", "maxOrder": 9},
    "https://www.simg.de/nebulae3/dr0_2/oiii8": {"slug": "nsns-oiii8", "label": "NSNS DR0.2 ([OIII])", "maxOrder": 8},
    "P/2MASS/color": {"slug": "2mass-color", "label": "2MASS (color)", "maxOrder": 9},
    "P/SDSS9/color": {"slug": "sdss9-color", "label": "SDSS9 (color)", "maxOrder": 11},
}
DSS_ID, OIII_ID, TWOMASS_ID, SDSS_ID = list(SURVEYS)

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [panelFile, stepsFile, inFile, outFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));

class El {
    constructor(tag, id) {
        this.tagName = tag; this.id = id || ''; this.children = []; this.attrs = {};
        this.listeners = {}; this.hidden = false; this.disabled = false; this.checked = false;
        this.value = ''; this._text = ''; this.className = ''; this.type = ''; this.dataset = {};
    }
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    fire(type) { (this.listeners[type] || []).forEach(fn => fn({ target: this })); }
    click() { if (!this.disabled) this.fire('click'); }
    all() { return [this].concat(...this.children.map(c => c.all())); }
}

const byId = {};
for (const id of input.ids) byId[id] = new El('div', id);
byId['offline-detail-select'].value = '0';
byId['offline-save-panel'].hidden = true;   // the template ships it hidden
const doc = { getElementById: id => byId[id] || null, createElement: tag => new El(tag) };

// Manual timers: nothing runs until the steps say so.
let nextTimer = 1;
const timers = new Map();
const timerApi = {
    setTimeout: (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, ms, kind: 'timeout' }); return id; },
    clearTimeout: id => { timers.delete(id); },
    setInterval: (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, ms, kind: 'interval' }); return id; },
    clearInterval: id => { timers.delete(id); },
};

const calls = [];
let routes = () => ({ status: 404, body: null });
const fakeFetch = (url, opts) => {
    calls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
    const r = routes(url, opts || {});
    return Promise.resolve({ ok: r.status >= 200 && r.status < 300, status: r.status,
                             json: () => r.body === null ? Promise.reject(new Error('no body')) : Promise.resolve(r.body) });
};

const T = input.i18n;
const t = (key, params) => {
    let s = T[key] === undefined ? '[' + key + ']' : T[key];
    if (params) for (const [k, v] of Object.entries(params)) s = s.split('{' + k + '}').join(String(v));
    return s;
};

const confirms = [];
let confirmAnswer = true;
let confirmOpen = false;
const frame = { ra: 11.2, dec: 52.43, frameW: 1.2, frameH: 0.8, rigId: '7' };

const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(panelFile, 'utf8'), ctx, { filename: panelFile });
const P = ctx.window.NovaOfflineSavePanel;

const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
const h = {
    P, $: id => byId[id], calls, timers, frame,
    set routes(fn) { routes = fn; },
    set confirmAnswer(v) { confirmAnswer = v; },
    set confirmOpen(v) { confirmOpen = v; },
    confirms,
    flush,
    activeTimers: () => timers.size,
    // Run every pending timer once (intervals stay registered).
    async runTimers() {
        for (const [id, tm] of Array.from(timers.entries())) {
            if (tm.kind === 'timeout') timers.delete(id);
            tm.fn();
        }
        await flush();
    },
    intervals: () => Array.from(timers.values()).filter(x => x.kind === 'interval').length,
    text: id => byId[id].all().map(e => e._text).join(' '),
    create() {
        return P.create({
            doc, fetch: fakeFetch, t, timers: timerApi, surveys: input.surveys,
            confirm: (msg, opts) => { confirms.push({ msg, opts }); return Promise.resolve(confirmAnswer); },
            getFrame: () => frame, getBaseId: () => input.base, getBlendId: () => input.blend,
            getName: () => 'LDN1295', locale: 'en-US', isConfirmOpen: () => confirmOpen,
        });
    },
    surveyRows: () => byId['offline-survey-list'].children.map(row => ({
        id: row.children[0].value, checked: row.children[0].checked, disabled: row.children[0].disabled,
        note: row.children[3].hidden ? null : row.children[3].textContent,
        size: row.children[2].textContent, cls: row.className,
    })),
    saveRows: () => byId['offline-saves-list'].children.map(row => ({
        text: row.textContent, cls: row.className,
        status: row.children[1] ? row.children[1].children[0].textContent : null,
        tone: row.children[1] ? row.children[1].children[0].getAttribute('data-tone') : null,
        cancelHidden: row.children[1] ? row.children[1].children[1].hidden : null,
        badge: row.children[0] ? row.children[0].children[0].children.length > 1 : null,
    })),
    key(k) { let stopped = false, prevented = false;
        return { e: { key: k, stopPropagation: () => { stopped = true; }, preventDefault: () => { prevented = true; } },
                 get stopped() { return stopped; }, get prevented() { return prevented; } }; },
};

(async () => {
    const steps = new (Object.getPrototypeOf(async function () {}).constructor)('h', fs.readFileSync(stepsFile, 'utf8'));
    const out = await steps(h);
    fs.writeFileSync(outFile, JSON.stringify(out === undefined ? null : out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

I18N = {
    "offline_cancel": "Cancel", "delete": "Delete", "offline_none_saved": "Nothing saved yet.",
    "offline_detail_lighter": "Lighter", "offline_detail_rig": "Matches your rig",
    "offline_detail_sharper": "Sharper",
    "offline_detail_scale": "≈{scale}″/px · your rig {rig}″/px",
    "offline_not_covered": "Not covered here", "offline_max_detail": "Max detail of this survey",
    "offline_tiles_mb": "{tiles} tiles · {mb} MB", "offline_estimating": "Estimating…",
    "offline_reason_over_cap": "OVER CAP", "offline_reason_disk": "DISK",
    "offline_reason_no_surveys": "NO SURVEYS", "offline_reason_no_rig": "NO RIG",
    "offline_reason_rig_scale": "RIG SCALE", "offline_reason_too_large": "TOO LARGE",
    "offline_reason_nothing": "NOTHING", "offline_reason_error": "ERROR",
    "offline_status_waiting": "Waiting", "offline_status_downloading": "Downloading {done} / {total}",
    "offline_status_paused": "Paused – survey server not reachable", "offline_status_done": "Done",
    "offline_status_done_missing": "Done – {count} tiles not available",
    "offline_status_no_space": "Stopped – not enough space", "offline_status_cancelled": "Cancelled",
    "offline_status_failed": "Failed", "offline_partial": "partial",
    "offline_earlier_version": "Saved by an earlier version",
    "offline_delete_confirm": "Delete the offline sky saved for {name}?",
}

MB = 1024 * 1024


def _survey_est(sid, *, tiles=805, est=5 * MB, adopt=0, capped=False, orders=(4, 5, 6, 7, 8, 9, 10, 11, 12, 13)):
    return {"slug": SURVEYS[sid]["slug"], "survey_id": sid, "requested_order": 13,
            "effective_max_order": 17, "capped": capped, "orders": list(orders),
            "by_order": {str(o): {"tiles": 1, "new": 1} for o in orders},
            "tiles_total": tiles, "tile_count": tiles, "avg_tile_bytes": 20000,
            "avg_tile_measured": True, "est_bytes": est, "adopt_bytes": adopt,
            "coverage": {"reason": "MOC", "fraction": 0.5}}


def _estimate(survey_list, reject=None):
    tot = sum(s["tiles_total"] for s in survey_list)
    return {"target_order": 13, "requested_order": 13, "detail_offset": 0,
            "requested_pixel_scale_arcsec": 3.2, "image_scale_arcsec": 4.08,
            "surveys": survey_list,
            "total": {"tile_count": tot, "tiles_total": tot,
                      "est_bytes": sum(s["est_bytes"] for s in survey_list),
                      "adopt_bytes": sum(s["adopt_bytes"] for s in survey_list)},
            "cap_bytes": 2048 * MB, "free_bytes": 2000 * MB, "fits": reject is None, "reject": reject}


# Routes script shared by the cases: every survey covered, DSS + OIII checked.
_ROUTES = r"""
const surveys = (url) => decodeURIComponent((url.split('surveys=')[1] || '')).split(',').filter(Boolean);
const est = (ids, reject) => {
    const list = ids.map(id => Object.assign({}, h.EST[id] || h.EST_DEFAULT, { survey_id: id }));
    const tot = list.reduce((a, s) => a + s.tiles_total, 0);
    return { requested_pixel_scale_arcsec: 3.2, image_scale_arcsec: 4.08, target_order: 13,
             requested_order: 13, surveys: list,
             total: { tiles_total: tot, tile_count: tot, est_bytes: list.reduce((a, s) => a + s.est_bytes, 0),
                      adopt_bytes: 0 },
             free_bytes: 2000 * 1048576, cap_bytes: 2048 * 1048576, reject: reject || null, fits: !reject };
};
h.SAVES = h.SAVES || [];
h.PROGRESS = h.PROGRESS || {};
h.routes = (url, opts) => {
    if (url.startsWith('/api/offline/estimate')) {
        const ids = surveys(url);
        const all = ids.length > 2;
        return { status: 200, body: est(ids, all ? null : h.REJECT) };
    }
    if (url === '/api/offline/saves' && (opts.method || 'GET') === 'GET') return { status: 200, body: { saves: h.SAVES } };
    if (url === '/api/offline/saves' && opts.method === 'POST') return h.POST || { status: 200, body: { job_id: 'new-1' } };
    const m = url.match(/^\/api\/offline\/saves\/([^/]+)\/progress$/);
    if (m) return h.PROGRESS[m[1]] ? { status: 200, body: { job: h.PROGRESS[m[1]] } } : { status: 404, body: null };
    if (/\/cancel$/.test(url)) return { status: 200, body: { cancelled: true } };
    if (opts.method === 'DELETE') return { status: 200, body: { deleted: true } };
    return { status: 404, body: null };
};
"""

pytestmark_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, steps, *, est=None, est_default=None, saves=None, progress=None, reject=None,
         base=DSS_ID, blend=OIII_ID):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    prelude = (
        f"h.EST = {json.dumps(est or {})};\n"
        f"h.EST_DEFAULT = {json.dumps(est_default or _survey_est(DSS_ID))};\n"
        f"h.SAVES = {json.dumps(saves or [])};\n"
        f"h.PROGRESS = {json.dumps(progress or {})};\n"
        f"h.REJECT = {json.dumps(reject)};\n"
    )
    case = tmp_path / "steps.js"
    case.write_text(prelude + _ROUTES + steps)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"ids": PANEL_IDS, "i18n": I18N, "surveys": SURVEYS,
                               "base": base, "blend": blend}))
    out = tmp_path / "out.json"
    run = subprocess.run([NODE, str(driver), str(PANEL_JS), str(case), str(inp), str(out)],
                         capture_output=True, text=True, check=False, env={**os.environ, "TZ": "UTC"})
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def _active_save(**kw):
    s = {"id": "s1", "name": "M42", "surveys": ["dss2-color"], "survey_ids": [DSS_ID],
         "detail_offset": 0, "bytes": 0, "created": "2026-11-16T23:30:12Z", "status": "running",
         "partial": True, "missing": 0, "legacy": False, "max_order": 17, "target_order": 13}
    s.update(kw)
    return s


# ---------------------------------------------------------------------------
# Panel in node
# ---------------------------------------------------------------------------

@pytestmark_node
def test_panel_starts_closed_and_does_nothing_until_opened(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        await h.flush();
        return { hidden: h.$('offline-save-panel').hidden, open: p.isOpen(),
                 expanded: h.$('save-offline-btn').getAttribute('aria-expanded'),
                 calls: h.calls.length, timers: h.activeTimers() };
    """)
    assert got == {"hidden": True, "open": False, "expanded": "false", "calls": 0, "timers": 0}


@pytestmark_node
@pytest.mark.parametrize("how", ["x", "toggle", "escape"])
def test_close_paths_hide_the_panel_and_stop_polling(tmp_path, how):
    got = _run(tmp_path, """
        const p = h.create();
        p.toggle();                               // the "Save for offline" button
        await h.runTimers();                      // estimate debounce
        const before = { hidden: h.$('offline-save-panel').hidden, intervals: h.intervals(),
                         expanded: h.$('save-offline-btn').getAttribute('aria-expanded') };
        let key = null;
        if (HOW === 'x') h.$('offline-save-close').click();
        else if (HOW === 'toggle') p.toggle();
        else { key = h.key('Escape'); p.handleKeydown(key.e); }
        const nCalls = h.calls.length;
        await h.flush();
        return { before, hidden: h.$('offline-save-panel').hidden, open: p.isOpen(),
                 timers: h.activeTimers(), expanded: h.$('save-offline-btn').getAttribute('aria-expanded'),
                 stopped: key ? key.stopped : null, callsAfterClose: h.calls.length - nCalls };
    """.replace("HOW", json.dumps(how)), saves=[_active_save()],
        progress={"s1": {"status": "running", "done": 1, "total": 9}})
    assert got["before"] == {"hidden": False, "intervals": 1, "expanded": "true"}, "polling ran while open"
    assert got["hidden"] is True and got["open"] is False
    assert got["timers"] == 0, "every timer stops with the panel"
    assert got["expanded"] == "false"
    assert got["callsAfterClose"] == 0
    if how == "escape":
        assert got["stopped"] is True, "Escape must not also close the modal"


@pytestmark_node
def test_escape_is_ignored_when_closed_or_under_a_confirm(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        const k1 = h.key('Escape');
        const r1 = p.handleKeydown(k1.e);         // closed: the modal handles Escape
        p.open();
        h.confirmOpen = true;
        const k2 = h.key('Escape');
        const r2 = p.handleKeydown(k2.e);         // a confirm dialog on top owns it
        const stillOpen = p.isOpen();
        h.confirmOpen = false;
        const k3 = h.key('Enter');
        const r3 = p.handleKeydown(k3.e);
        return { r1, s1: k1.stopped, r2, s2: k2.stopped, stillOpen, r3, open: p.isOpen() };
    """)
    assert got == {"r1": False, "s1": False, "r2": False, "s2": False, "stillOpen": True,
                   "r3": False, "open": True}


@pytestmark_node
def test_late_responses_after_close_are_dropped(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        p.close();                                // saves list request still in flight
        await h.flush();
        return { rows: h.$('offline-saves-list').children.length, timers: h.activeTimers() };
    """, saves=[_active_save()])
    assert got == {"rows": 0, "timers": 0}


@pytestmark_node
def test_detail_choices_map_to_offsets(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        const offsets = [];
        const last = () => h.calls.filter(c => c.url.startsWith('/api/offline/estimate')).slice(-1)[0].url;
        offsets.push(last().match(/detail_offset=(-?\\d+)/)[1]);
        for (const v of ['-1', '1', '0']) {
            h.$('offline-detail-select').value = v;
            h.$('offline-detail-select').fire('change');
            await h.runTimers();
            offsets.push(last().match(/detail_offset=(-?\\d+)/)[1]);
        }
        h.$('offline-detail-select').value = '-1';
        h.$('offline-detail-select').fire('change');
        await h.runTimers();
        h.$('offline-save-start').click();
        await h.flush();
        const post = h.calls.filter(c => c.method === 'POST')[0];
        return { offsets, postOffset: post.body.detail_offset, postSurveys: post.body.surveys,
                 choices: h.P.DETAIL_CHOICES.map(c => [c.value, c.offset]),
                 map: ['-1', '0', '1', 'x'].map(v => h.P.offsetForValue(v)) };
    """)
    assert got["offsets"] == ["0", "-1", "1", "0"], "default is 'Matches your rig' (0)"
    assert got["postOffset"] == -1
    assert got["postSurveys"] == [DSS_ID, OIII_ID], "current base + blend pre-checked"
    assert got["choices"] == [["-1", -1], ["0", 0], ["1", 1]]
    assert got["map"] == [-1, 0, 1, 0]


@pytestmark_node
def test_estimate_per_survey_total_free_space_and_resolution(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        return { rows: h.surveyRows(), total: h.$('offline-estimate-total').textContent,
                 free: h.$('offline-estimate-free').textContent,
                 scale: h.$('offline-detail-scale').textContent,
                 saveDisabled: h.$('offline-save-start').disabled,
                 reasonHidden: h.$('offline-save-reason').hidden };
    """, est={OIII_ID: _survey_est(OIII_ID, tiles=200, est=MB, capped=True),
              TWOMASS_ID: _survey_est(TWOMASS_ID, tiles=0)})
    rows = {r["id"]: r for r in got["rows"]}
    assert rows[DSS_ID]["checked"] and rows[DSS_ID]["size"] == "805 tiles · 5.0 MB"
    assert rows[OIII_ID]["checked"] and rows[OIII_ID]["note"] == "Max detail of this survey"
    assert rows[OIII_ID]["size"] == "200 tiles · 1.0 MB"
    assert rows[TWOMASS_ID] == {"id": TWOMASS_ID, "checked": False, "disabled": True,
                                "note": "Not covered here", "size": "",
                                "cls": "offline-survey-row is-disabled"}
    assert not rows[SDSS_ID]["checked"] and not rows[SDSS_ID]["disabled"] and rows[SDSS_ID]["note"] is None
    assert got["total"] == "1005 tiles · 6.0 MB"
    assert got["free"] == "2000 MB"
    assert got["scale"] == "≈3.2″/px · your rig 4.1″/px"
    assert got["saveDisabled"] is False and got["reasonHidden"] is True


@pytestmark_node
def test_uncovered_base_survey_is_unchecked_and_disabled(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        const post = h.calls.filter(c => c.url.startsWith('/api/offline/estimate')).slice(-1)[0].url;
        return { rows: h.surveyRows(), lastSurveys: decodeURIComponent(post.split('surveys=')[1]) };
    """, est={DSS_ID: _survey_est(DSS_ID, tiles=0)})
    rows = {r["id"]: r for r in got["rows"]}
    assert rows[DSS_ID]["checked"] is False and rows[DSS_ID]["disabled"] is True
    assert got["lastSurveys"] == OIII_ID, "the totals leave the uncovered survey out"


@pytestmark_node
@pytest.mark.parametrize("code,reason", [("over_cap", "OVER CAP"), ("disk_headroom", "DISK")])
def test_over_cap_or_disk_disables_save_with_the_reason(tmp_path, code, reason):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        h.$('offline-save-start').click();
        await h.flush();
        return { disabled: h.$('offline-save-start').disabled, reason: h.$('offline-save-reason').textContent,
                 reasonHidden: h.$('offline-save-reason').hidden,
                 posts: h.calls.filter(c => c.method === 'POST').length };
    """, reject={"code": code, "message": "server text"})
    assert got == {"disabled": True, "reason": reason, "reasonHidden": False, "posts": 0}


@pytestmark_node
def test_no_survey_checked_disables_save(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        for (const row of h.$('offline-survey-list').children) {
            if (row.children[0].checked) { row.children[0].checked = false; row.children[0].fire('change'); }
        }
        await h.runTimers();
        return { disabled: h.$('offline-save-start').disabled, reason: h.$('offline-save-reason').textContent };
    """)
    assert got == {"disabled": True, "reason": "NO SURVEYS"}


@pytestmark_node
def test_refused_save_shows_the_reason(tmp_path):
    got = _run(tmp_path, """
        h.POST = { status: 409, body: { code: 'over_cap', error: 'x' } };
        const p = h.create();
        p.open();
        await h.runTimers();
        h.$('offline-save-start').click();
        await h.flush();
        return { disabled: h.$('offline-save-start').disabled, reason: h.$('offline-save-reason').textContent };
    """)
    assert got == {"disabled": True, "reason": "OVER CAP"}


@pytestmark_node
def test_no_order_numbers_are_rendered(tmp_path):
    """Requested/target order 13 and survey max 17 (in the estimate and in the
    saves) must not show up anywhere in the panel."""
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        return { text: h.text('offline-save-panel') + ' ' + ['offline-survey-list', 'offline-detail-scale',
                 'offline-estimate-total', 'offline-estimate-free', 'offline-save-reason',
                 'offline-saves-list'].map(h.text).join(' ') };
    """, saves=[_active_save(status="done", bytes=4 * MB, partial=False),
                _active_save(id="s2", status="cancelled", bytes=2 * MB, detail_offset=1)])
    text = got["text"]
    assert "805 tiles" in text and "M42" in text   # the panel did render
    assert "13" not in text and "17" not in text
    assert not re.search(r"order", text, re.I)


@pytestmark_node
def test_status_texts_for_every_state(tmp_path):
    got = _run(tmp_path, """
        const jobs = [
            { status: 'queued' }, { status: 'running', done: 0, total: 0 },
            { status: 'running', done: 123, total: 805 },
            { status: 'paused', reason: 'upstream_unavailable', done: 5, total: 805 },
            { status: 'done', missing: 0 }, { status: 'done', missing: 4 },
            { status: 'stopped', reason: 'no_space' }, { status: 'cancelled' },
            { status: 'failed', reason: 'error' },
        ];
        const t = (k, p) => { let s = { offline_status_waiting: 'Waiting',
            offline_status_downloading: 'Downloading {done} / {total}',
            offline_status_paused: 'Paused – survey server not reachable', offline_status_done: 'Done',
            offline_status_done_missing: 'Done – {count} tiles not available',
            offline_status_no_space: 'Stopped – not enough space', offline_status_cancelled: 'Cancelled',
            offline_status_failed: 'Failed' }[k];
            for (const [a, b] of Object.entries(p || {})) s = s.split('{' + a + '}').join(String(b)); return s; };
        return jobs.map(j => h.P.statusText(t, j).text);
    """)
    assert got == ["Waiting", "Waiting", "Downloading 123 / 805",
                   "Paused – survey server not reachable", "Done", "Done – 4 tiles not available",
                   "Stopped – not enough space", "Cancelled", "Failed"]


@pytestmark_node
def test_progress_polls_and_cancel_only_while_active(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();                       // estimate + list
        const first = h.saveRows();
        await h.runTimers();                       // one poll tick
        const polled = h.saveRows();
        // Close and reopen: polling resumes and the row keeps updating.
        p.close();
        const closedTimers = h.activeTimers();
        h.PROGRESS.s1 = { status: 'paused', reason: 'upstream_unavailable', done: 200, total: 805 };
        p.open();
        await h.runTimers();
        await h.runTimers();
        const reopened = h.saveRows();
        // The job finishes: the list reloads, polling stops.
        h.PROGRESS.s1 = { status: 'done', done: 805, total: 805, missing: 3 };
        h.SAVES = [Object.assign({}, h.SAVES[0], { status: 'done', missing: 3, partial: false })];
        await h.runTimers();
        await h.runTimers();
        return { first, polled, closedTimers, reopened, done: h.saveRows(), intervals: h.intervals() };
    """, saves=[_active_save(), _active_save(id="s9", name="NGC 7000", status="cancelled", partial=True)],
        progress={"s1": {"status": "running", "done": 123, "total": 805}})
    assert got["first"][0]["status"] == "Waiting", "the list alone has no progress numbers yet"
    assert got["polled"][0]["status"] == "Downloading 123 / 805"
    assert got["polled"][0]["cancelHidden"] is False
    assert got["polled"][1]["status"] == "Cancelled" and got["polled"][1]["cancelHidden"] is True
    assert got["polled"][1]["badge"] is True, "cancelled save is marked partial"
    assert got["closedTimers"] == 0
    assert got["reopened"][0]["status"] == "Paused – survey server not reachable"
    assert got["reopened"][0]["tone"] == "warning"
    assert got["done"][0]["status"] == "Done – 3 tiles not available"
    assert got["done"][0]["cancelHidden"] is True
    assert got["intervals"] == 0


@pytestmark_node
def test_cancel_button_posts_cancel(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        const row = h.$('offline-saves-list').children[0];
        row.children[1].children[1].click();       // Cancel
        await h.flush();
        return h.calls.filter(c => c.method === 'POST').map(c => c.url);
    """, saves=[_active_save()], progress={"s1": {"status": "running", "done": 1, "total": 9}})
    assert got == ["/api/offline/saves/s1/cancel"]


@pytestmark_node
def test_saved_list_rows_and_delete_with_confirmation(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        const rows = h.saveRows();
        const del = () => h.$('offline-saves-list').children[1].children[1].children[2];
        h.confirmAnswer = false;
        del().click();
        await h.flush();
        const afterNo = h.calls.filter(c => c.method === 'DELETE').length;
        h.confirmAnswer = true;
        h.SAVES = [h.SAVES[0]];
        del().click();
        await h.flush();
        return { rows, confirms: h.confirms, afterNo,
                 deletes: h.calls.filter(c => c.method === 'DELETE').map(c => c.url),
                 after: h.saveRows().length };
    """, saves=[
        _active_save(id="a", name="M31", status="done", bytes=int(12.5 * MB), detail_offset=-1, partial=False,
                     surveys=["dss2-color", "nsns-oiii8"], survey_ids=[DSS_ID, OIII_ID]),
        # The cancelled LDN1295 save from the first step-2 version: legacy, no detail choice.
        {"id": "3cd1e599", "name": "LDN1295", "surveys": ["dss2-color", "nsns-oiii8"],
         "survey_ids": [DSS_ID, OIII_ID], "detail_offset": None, "max_order": 8, "target_order": None,
         "bytes": 19583809, "created": "2026-11-16T23:30:12Z", "status": "cancelled",
         "partial": True, "missing": 0, "legacy": True},
    ])
    a, ldn = got["rows"]
    assert a["text"].startswith("M31") and "DSS2 (color), NSNS DR0.2 ([OIII]) · Lighter · 12.5 MB · 11/16/2026" in a["text"]
    assert a["status"] == "Done" and a["badge"] is False
    assert ldn["text"].startswith("LDN1295partial")
    assert "Saved by an earlier version · 18.7 MB" in ldn["text"]
    assert ldn["status"] == "Cancelled" and ldn["cancelHidden"] is True
    assert got["confirms"][0]["msg"] == "Delete the offline sky saved for LDN1295?"
    assert got["confirms"][0]["opts"]["danger"] is True
    assert got["afterNo"] == 0
    assert got["deletes"] == ["/api/offline/saves/3cd1e599"]
    assert got["after"] == 1


@pytestmark_node
def test_empty_saved_list(tmp_path):
    got = _run(tmp_path, """
        const p = h.create();
        p.open();
        await h.runTimers();
        return h.saveRows();
    """)
    assert got == [{"text": "Nothing saved yet.", "cls": "offline-saves-empty", "status": None,
                    "tone": None, "cancelHidden": None, "badge": None}]


# ---------------------------------------------------------------------------
# graph_view_chart.js wiring (source checks: the chart IIFE needs the full page)
# ---------------------------------------------------------------------------

def _fn_body(src, name):
    i = src.index(f"function {name}(")
    j = src.index("\n    }\n", i)
    return src[i:j]


def test_chart_closes_panel_on_open_and_when_the_framing_tab_hides():
    src = CHART_JS.read_text(encoding="utf-8")
    assert "closeOfflineSavePanel();   // the panel starts closed on every open" in _fn_body(src, "openFramingAssistant")
    assert _fn_body(src, "onFramingTabHidden").count("closeOfflineSavePanel();") == 1
    # Escape: the panel's handler runs in the capture phase, so it closes only the panel
    assert "window.addEventListener('keydown', (e) => { if (offlineSavePanel) offlineSavePanel.handleKeydown(e); }, true);" in src


def test_piece_c_stays_off_behind_its_own_gate():
    src = CHART_JS.read_text(encoding="utf-8")
    assert "const OFFLINE_BASE_UI_ENABLED = false;" in src
    assert "function offlineBaseUiEnabled() { return offlineSaveEnabled() && OFFLINE_BASE_UI_ENABLED; }" in src
    for fn in ("ensureOfflineBaseSurveys", "refreshOfflineMarks", "refreshOfflineStatus"):
        assert _fn_body(src, fn).split("\n")[1].strip() == "if (!offlineBaseUiEnabled()) return;", fn


def test_no_inline_styles_in_offline_js():
    for path in (PANEL_JS, CHART_JS):
        src = path.read_text(encoding="utf-8")
        start = src.find("OFFLINE SKY") if path == CHART_JS else 0
        section = src[start:]
        if path == CHART_JS:
            section = section[:section.index("function updateOfflineViewUi")] + \
                section[section.index("function refreshOfflineStatus"):]
        assert ".style." not in section and "style.cssText" not in section and "style=" not in section, path


# ---------------------------------------------------------------------------
# Server side
# ---------------------------------------------------------------------------

LEGACY_ID = "3cd1e599-bded-4310-9983-d557a5d203eb"


def _save_on(monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_SAVE_ENABLED", True)


def _uid(db_session):
    from nova.models import DbUser
    return db_session.query(DbUser).filter_by(username="default").one().id


def _write_legacy(uid, status="cancelled"):
    """A save exactly as the first step-2 version wrote it (the LDN1295 one)."""
    store.write_save_manifest({
        "id": LEGACY_ID, "user_id": uid, "name": "LDN1295",
        "ra": 11.200000000000005, "dec": 52.43333333333334, "radius_deg": 21.564434664667715,
        "surveys": ["dss2-color", "nsns-oiii8"], "max_order": 8,
        "created": "2026-11-16T23:30:12Z",
        "paths": ["dss2-color/Norder4/Dir0/Npix1.jpg"], "bytes": 19583809, "status": status})
    os.makedirs(store.jobs_dir(), exist_ok=True)
    with open(os.path.join(store.jobs_dir(), f"{LEGACY_ID}.json"), "w") as fh:
        json.dump({"id": LEGACY_ID, "kind": "save", "surveys": ["dss2-color", "nsns-oiii8"],
                   "done": 364, "total": 364, "bytes": 19583809, "status": status,
                   "updated": 1794871901.743596}, fh)


def test_legacy_unfinished_save_is_hidden_but_still_deletable(monkeypatch, client, db_session):
    """A legacy save that never finished covers nothing and can never run again,
    so the list hides it. Its file stays, its summary is still readable, and it
    can still be deleted."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _write_legacy(_uid(db_session))
    resp = client.get("/api/offline/saves")
    assert resp.status_code == 200
    assert resp.get_json()["saves"] == []            # hidden from the list

    # the summary is still there, and still says what it always said
    prog = client.get(f"/api/offline/saves/{LEGACY_ID}/progress")
    assert prog.status_code == 200
    s = prog.get_json()["manifest"]
    assert s["id"] == LEGACY_ID and s["legacy"] is True and s["legacy_unfinished"] is True
    assert s["status"] == "cancelled" and s["partial"] is True
    assert s["detail_offset"] is None
    assert s["survey_ids"] == ["P/DSS2/color", "https://www.simg.de/nebulae3/dr0_2/oiii8"]

    assert client.delete(f"/api/offline/saves/{LEGACY_ID}").status_code == 200
    assert store.read_save_manifest(LEGACY_ID) is None
    assert jobs.read_job(LEGACY_ID) is None
    assert client.get("/api/offline/saves").get_json()["saves"] == []


def test_legacy_done_save_is_still_listed(monkeypatch, client, db_session):
    """Only a legacy save that never finished is hidden: a finished one covers
    its tiles and stays in the list."""
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _write_legacy(_uid(db_session), status="done")
    (s,) = client.get("/api/offline/saves").get_json()["saves"]
    assert s["legacy"] is True and s["legacy_unfinished"] is False and s["status"] == "done"


def test_legacy_save_left_running_reads_as_failed_and_its_job_cannot_crash(monkeypatch, client, db_session):
    _save_on(monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda: False)
    _write_legacy(_uid(db_session), status="running")
    s = client.get(f"/api/offline/saves/{LEGACY_ID}/progress").get_json()["manifest"]
    assert s["status"] == "failed" and s["partial"] is True and s["legacy"] is True
    assert s["legacy_unfinished"] is True
    # Its job file has no params: whoever runs the queue marks it failed, no exception.
    jobs.JobRunner(rate=1000).run_job(LEGACY_ID)
    assert jobs.read_job(LEGACY_ID)["status"] == "failed"
    assert client.delete(f"/api/offline/saves/{LEGACY_ID}").status_code == 200
    assert store.read_save_manifest(LEGACY_ID) is None


def test_new_save_is_not_legacy(monkeypatch, client, db_session):
    _save_on(monkeypatch)
    store.write_save_manifest({"id": "aaaaaaaa-0000-0000-0000-000000000001", "user_id": _uid(db_session),
                               "name": "M42", "surveys": ["dss2-color"], "paths": [], "bytes": 0,
                               "status": "done", "partial": False, "detail_offset": 1,
                               "frame_w_deg": 1.0, "frame_h_deg": 0.7,
                               "created": "2026-11-17T00:00:00Z"})
    (s,) = client.get("/api/offline/saves").get_json()["saves"]
    assert s["legacy"] is False and s["partial"] is False and s["detail_offset"] == 1


def test_estimate_reports_the_requested_resolution(monkeypatch, client, db_session):
    from test_offline_sky_jobs import DSS, DSS_ID as JOBS_DSS_ID, _install_real_facts, _make_rig, _query
    _save_on(monkeypatch)
    _install_real_facts(DSS)
    rid = _make_rig(db_session)
    for off in (-1, 0, 1):
        d = client.get("/api/offline/estimate?" + _query(rid, [JOBS_DSS_ID], offset=off)).get_json()
        assert d["requested_pixel_scale_arcsec"] == pytest.approx(
            jobs.hips_pixel_scale_arcsec(d["requested_order"]))
        assert d["image_scale_arcsec"] > 0


# ---------------------------------------------------------------------------
# Template + catalogs
# ---------------------------------------------------------------------------

def _graph_page(monkeypatch, client, save_enabled=True):
    import nova
    monkeypatch.setattr(nova, "offline_sky_enabled", lambda: True)
    monkeypatch.setattr(nova, "OFFLINE_SKY_SAVE_ENABLED", save_enabled)
    resp = client.get("/graph_dashboard/M42")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _offline_markup(page):
    i = page.index('<div id="offline-sky-controls"')
    j = page.index('</details>', i)   # the end of the folded OFFLINE section
    return page[i:j]


def test_template_panel_ships_closed_with_every_id(monkeypatch, client):
    page = _graph_page(monkeypatch, client)
    markup = _offline_markup(page)
    assert re.search(r'<section id="offline-save-panel"[^>]*\bhidden\b', markup)
    assert 'aria-expanded="false"' in markup
    for id_ in PANEL_IDS:
        assert f'id="{id_}"' in markup, id_
    assert '<script src="/static/js/offline_save_panel.js' in page
    assert page.index('<script src="/static/js/offline_save_panel.js') < \
        page.index('<script src="/static/js/graph_view_chart.js')


def test_template_detail_choices_and_no_order_numbers(monkeypatch, client):
    markup = _offline_markup(_graph_page(monkeypatch, client))
    opts = re.findall(r'<option value="(-?\d)"( selected)?>([^<]+)</option>', markup)
    assert opts == [("-1", "", "Lighter"), ("0", " selected", "Matches your rig"), ("1", "", "Sharper")]
    assert not re.search(r"order|Suggested", markup, re.I)


def test_template_offline_markup_has_no_inline_styles(monkeypatch, client):
    page = _graph_page(monkeypatch, client)
    assert "style=" not in _offline_markup(page)
    assert ".offline-save-panel {" not in page, "panel CSS lives in graph_view.css"


def test_template_save_ui_absent_when_flag_off(monkeypatch, client):
    page = _graph_page(monkeypatch, client, save_enabled=False)
    assert 'id="offline-save-panel"' not in page
    assert "offline_status_downloading" not in page


NEW_MSGIDS = [
    "Lighter", "Matches your rig", "Sharper", "≈{scale}″/px · your rig {rig}″/px", "Not covered here",
    "Max detail of this survey", "{tiles} tiles · {mb} MB", "Estimating…",
    "This is more than the space set aside for offline sky.",
    "Not enough free disk space on this computer.", "Choose at least one survey.", "Choose a rig first.",
    "This rig has no image scale. Add the camera pixel size and the focal length.",
    "This frame is too large to save for offline.", "There is nothing to save for these surveys here.",
    "The estimate is not available right now.", "Waiting", "Downloading {done} / {total}",
    "Paused – survey server not reachable", "Done", "Done – {count} tiles not available",
    "Stopped – not enough space", "Cancelled", "Failed", "partial", "Saved by an earlier version",
    "Delete the offline sky saved for {name}?", "Estimate",
]


def test_new_strings_are_used_by_the_template():
    src = GRAPH_VIEW_HTML.read_text(encoding="utf-8")
    for msgid in NEW_MSGIDS:
        assert f"_('{msgid}')" in src, msgid


@pytest.mark.parametrize("lang", ["de", "fr", "es", "zh", "ja"])
def test_new_strings_translated_in_every_locale(lang):
    from babel.messages.pofile import read_po
    with open(REPO / "translations" / lang / "LC_MESSAGES" / "messages.po", "rb") as fh:
        cat = read_po(fh)
    for msgid in NEW_MSGIDS:
        msg = cat.get(msgid)
        assert msg is not None and msg.string and msg.string != msgid, (lang, msgid)
        assert not msg.fuzzy, (lang, msgid)
        for ph in re.findall(r"\{\w+\}", msgid):
            assert ph in msg.string, (lang, msgid, ph)

