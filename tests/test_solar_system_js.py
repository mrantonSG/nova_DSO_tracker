"""Checks for static/js/solar_system.js, the Solar System page's own script, in node.

The page's scripts run in one vm context with the real engines and a small stand-in
for the DOM: elements are plain objects created on first lookup by id, fetch answers
from a table the case controls, setInterval only records the tick, and the chart is a
stub that counts its calls. Enough to drive the error states and the tab switch, not
to check layout.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js",
              "nova_planets.js", "nova_plot_data.js", "nova_solar_rows.js",
              "nova_solar_calendar.js")

LONDON = {"name": "Home", "lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15, "horizon_mask": []}

I18N = {"error": "CALC ERROR", "inputsError": "INPUTS ERROR", "chartError": "CHART ERROR"}

# The stand-in DOM and the case's steps. `script` is solar_system.js; `steps` is the
# case body, an async function of the helpers below whose return value is the result.
_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const [stepsFile, inFile, outFile, pageFile, ...files] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));

function makeElement(tag, id) {
    const el = {
        tagName: tag, id: id || '', children: [], dataset: {}, attrs: {}, listeners: {},
        hidden: false, textContent: '', title: '', value: '', checked: false, disabled: false,
        style: {}, className: '',
        classList: {
            set: new Set(),
            add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
            contains(c) { return this.set.has(c); },
            toggle(c, on) { if (on === undefined) on = !this.set.has(c); if (on) this.set.add(c); else this.set.delete(c); return on; },
        },
        appendChild(child) { this.children.push(child); return child; },
        setAttribute(k, v) { this.attrs[k] = String(v); },
        addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
        fire(type) { return Promise.all((this.listeners[type] || []).map(fn => fn({}))); },
        scrollIntoView() {},
    };
    Object.defineProperty(el, 'innerHTML', { set() { el.children = []; }, get() { return ''; } });
    return el;
}

const byId = {};
const $ = id => byId[id] || (byId[id] = makeElement('div', id));
const tabButtons = ['bodies', 'calendar'].map(t => { const b = makeElement('button'); b.dataset.tab = t; return b; });
const viewButtons = ['day', 'month', 'year'].map(v => { const b = makeElement('button'); b.dataset.view = v; return b; });
const panel = t => $(t + '-tab').classList.contains('active');

const store = () => {
    const m = new Map();
    return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
};

let routes = {};
let interval = null;
const chartCalls = { create: 0, show: 0, resize: 0 };

const context = {
    console: { log() {}, warn() {}, error() {} },
    URL: URL, URLSearchParams: URLSearchParams, JSON: JSON,
    localStorage: store(), sessionStorage: store(),
    solarI18n: input.i18n,
    setInterval: fn => { interval = fn; return 1; },
    fetch: async url => {
        const path = String(url).split('?')[0];
        const answer = routes[path];
        if (!answer) throw new Error('offline: ' + url);
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(answer)) };
    },
    SolarSystemChart: {
        create: () => {
            chartCalls.create++;
            return { show() { chartCalls.show++; }, updateNow() {}, resize() { chartCalls.resize++; }, destroy() {} };
        },
    },
    document: {
        readyState: 'complete',
        documentElement: { lang: 'en' },
        getElementById: $,
        createElement: tag => makeElement(tag),
        querySelector: () => null,
        querySelectorAll: sel => (sel.indexOf('tab-button') >= 0 ? tabButtons : sel.indexOf('view-button') >= 0 ? viewButtons : []),
        addEventListener() {},
    },
};
context.window = context;
context.window.location = { href: 'http://localhost/solar_system', search: '' };
context.window.history = { replaceState() {} };
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });

const settle = () => new Promise(resolve => setTimeout(resolve, 0));
async function load(tab) {
    if (tab) context.localStorage.setItem('nova_solar_tab', tab);
    vm.runInContext(fs.readFileSync(pageFile, 'utf8'), context, { filename: pageFile });
    for (let i = 0; i < 20; i++) await settle();
}
async function tick() { await interval(); for (let i = 0; i < 20; i++) await settle(); }
async function click(tab) { await tabButtons.find(b => b.dataset.tab === tab).fire('click'); }
async function changeLocation(name) { $('location-select').value = name; await $('location-select').fire('change'); for (let i = 0; i < 20; i++) await settle(); }
const online = () => { routes = { '/get_locations': { locations: ['Home', 'Away'], selected: 'Home' }, '/api/desktop_inputs': input.inputs }; };
const offline = () => { routes = {}; };
const snap = () => ({
    bodiesShown: panel('bodies'), calendarShown: panel('calendar'),
    solarError: $('solar-error').hidden ? null : $('solar-error').textContent,
    tableHidden: $('solar-table-wrapper').hidden,
    calError: $('solar-cal-error').hidden ? null : $('solar-cal-error').textContent,
    calHidden: $('solar-cal-wrapper').hidden,
    bodyRows: $('solar-body').children.length, calRows: $('solar-cal-body').children.length,
    resize: chartCalls.resize, intervalSet: interval !== null,
});

const steps = new Function('h', 'return (async () => {' + fs.readFileSync(stepsFile, 'utf8') + '})();');
steps({ load, tick, click, changeLocation, online, offline, snap }).then(result => {
    fs.writeFileSync(outFile, JSON.stringify(result));
}, err => { console.error(err); process.exit(1); });
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, steps):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "steps.js"
    case.write_text(steps)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"inputs": {"location": LONDON}, "i18n": I18N}))
    out = tmp_path / "out.json"
    run = subprocess.run(
        [NODE, str(driver), str(case), str(inp), str(out), str(STATIC_JS / "solar_system.js")]
        + [str(STATIC_JS / name) for name in PAGE_ORDER],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def test_inputs_failure_on_the_calendar_tab_shows_both_errors_and_recovers(tmp_path):
    """Opened on Calendar with the inputs unreachable: both tabs show the translated
    inputs error with their tables hidden, the tick keeps trying, and once the inputs
    load both tabs are back, the calendar at once (it is shown), the Bodies tab too."""
    got = _run(tmp_path, """
        const out = {};
        h.offline();
        await h.load('calendar');
        out.failed = h.snap();
        await h.tick();
        out.stillOffline = h.snap();
        h.online();
        await h.tick();
        out.recovered = h.snap();
        return out;
    """)
    failed = got["failed"]
    assert failed["calendarShown"] and not failed["bodiesShown"]
    assert failed["calError"] == "INPUTS ERROR" and failed["calHidden"]
    assert failed["solarError"] == "INPUTS ERROR" and failed["tableHidden"]
    assert failed["intervalSet"], "the tick runs even when the first load failed"
    assert got["stillOffline"]["calError"] == "INPUTS ERROR"
    rec = got["recovered"]
    assert rec["calError"] is None and not rec["calHidden"] and rec["calRows"] > 0
    assert rec["solarError"] is None and not rec["tableHidden"] and rec["bodyRows"] > 0


def test_location_failure_shows_both_errors_and_the_next_tick_clears_them(tmp_path):
    """A location whose inputs do not load: the page stays on the previous one, both
    tabs say so, and the next successful refresh clears both."""
    got = _run(tmp_path, """
        const out = {};
        h.online();
        await h.load('calendar');
        out.before = h.snap();
        h.offline();
        await h.changeLocation('Away');
        out.failed = h.snap();
        h.online();
        await h.tick();
        out.after = h.snap();
        return out;
    """)
    assert got["before"]["calError"] is None and got["before"]["calRows"] > 0
    assert got["failed"]["calError"] == "INPUTS ERROR" and got["failed"]["calHidden"]
    assert got["failed"]["solarError"] == "INPUTS ERROR"
    assert got["after"]["calError"] is None and not got["after"]["calHidden"]
    assert got["after"]["solarError"] is None


def test_hidden_calendar_error_clears_when_its_tab_is_shown(tmp_path):
    """Recovery while on Bodies: Bodies clears on the tick; the hidden calendar rebuilds
    and clears the moment its tab is shown."""
    got = _run(tmp_path, """
        const out = {};
        h.offline();
        await h.load('bodies');
        h.online();
        await h.tick();
        out.onBodies = h.snap();
        await h.click('calendar');
        out.onCalendar = h.snap();
        return out;
    """)
    assert got["onBodies"]["solarError"] is None and got["onBodies"]["bodyRows"] > 0
    assert got["onCalendar"]["calError"] is None and got["onCalendar"]["calRows"] > 0


def test_showing_the_bodies_tab_resizes_the_chart(tmp_path):
    """The chart drawn while Bodies was hidden is fitted to its card when Bodies is shown."""
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        const before = h.snap().resize;
        await h.click('bodies');
        return [before, h.snap().resize];
    """)
    assert got == [0, 1]
