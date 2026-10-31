"""Checks for static/js/solar_system.js, the Solar System page's own script, in node.

The page's scripts run in one vm context with the real engines and a small stand-in
for the DOM: elements are plain objects created on first lookup by id, fetch answers
from a table the case controls, setInterval only records the tick, and the chart is a
stub that counts its calls. Enough to drive the error states and the tab switch, not
to check layout.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js",
              "nova_planets.js", "nova_plot_data.js", "nova_solar_rows.js",
              "nova_solar_calendar.js", "nova_solar_rigfit.js")

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
$('solar-cal-loading').hidden = true;      // as the template ships it
const tabButtons = ['bodies', 'calendar'].map(t => { const b = makeElement('button'); b.dataset.tab = t; return b; });
const viewButtons = ['day', 'month', 'year'].map(v => { const b = makeElement('button'); b.dataset.view = v; return b; });
const panel = t => $(t + '-tab').classList.contains('active');

const store = () => {
    const m = new Map();
    return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
};

let routes = {};
let interval = null;
let frames = 0;
const chartCalls = { create: 0, show: 0, resize: 0 };

const context = {
    console: { log() {}, warn() {}, error() {} },
    URL: URL, URLSearchParams: URLSearchParams, JSON: JSON,
    localStorage: store(), sessionStorage: store(),
    solarI18n: input.i18n,
    solarRigs: input.rigs,
    setInterval: fn => { interval = fn; return 1; },
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    requestAnimationFrame: fn => setTimeout(() => { frames++; fn(Date.now()); }, 0),
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
context.window.location = { href: 'http://localhost/solar_system' + (input.search || ''), search: input.search || '' };
context.window.history = { replaceState() {} };
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });

const settle = () => new Promise(resolve => setTimeout(resolve, 0));
async function load(tab) {
    if (tab) context.localStorage.setItem('nova_solar_tab', tab);
    if (input.sim) { context.localStorage.setItem('simModeActive', 'true'); context.localStorage.setItem('simDate', input.sim); }
    vm.runInContext(fs.readFileSync(pageFile, 'utf8'), context, { filename: pageFile });
    for (let i = 0; i < 20; i++) await settle();
}
async function tick() { await interval(); for (let i = 0; i < 20; i++) await settle(); }
async function click(tab) { await tabButtons.find(b => b.dataset.tab === tab).fire('click'); for (let i = 0; i < 20; i++) await settle(); }
// The click alone: what the page shows before it has yielded to the browser.
async function clickNow(tab) { await tabButtons.find(b => b.dataset.tab === tab).fire('click'); }
async function flush() { for (let i = 0; i < 20; i++) await settle(); }
async function changeLocation(name) { $('location-select').value = name; await $('location-select').fire('change'); for (let i = 0; i < 20; i++) await settle(); }
const online = () => { routes = { '/get_locations': { locations: ['Home', 'Away'], selected: 'Home' }, '/api/desktop_inputs': input.inputs }; };
const offline = () => { routes = {}; };
const snap = () => ({
    bodiesShown: panel('bodies'), calendarShown: panel('calendar'),
    solarError: $('solar-error').hidden ? null : $('solar-error').textContent,
    tableHidden: $('solar-table-wrapper').hidden,
    calError: $('solar-cal-error').hidden ? null : $('solar-cal-error').textContent,
    calHidden: $('solar-cal-wrapper').hidden,
    calLoading: !$('solar-cal-loading').hidden, frames: frames,
    bodyRows: $('solar-body').children.length, calRows: $('solar-cal-body').children.length,
    resize: chartCalls.resize, intervalSet: interval !== null,
});

// A table body as data: per row its classes, title and dataset.body, per cell its column,
// title and text (its own and its spans', joined), and its spans.
const flat = el => el.textContent + el.children.map(flat).join('');
const table = id => $(id).children.map(tr => ({
    body: tr.dataset.body, title: tr.title, classes: [...tr.classList.set],
    cells: tr.children.map(td => ({
        col: td.dataset.solarCol, title: td.title, text: flat(td),
        spans: td.children.map(c => ({text: flat(c), title: c.title, cls: c.className})),
    })),
}));
async function clickRow(id, index) { await $(id).children[index].fire('click'); for (let i = 0; i < 20; i++) await settle(); }
async function setFilter(body, hide) {
    $('solar-cal-body-filter').value = body; await $('solar-cal-body-filter').fire('change');
    $('solar-cal-hide-muted').checked = hide; await $('solar-cal-hide-muted').fire('change');
}
const el = id => ({hidden: $(id).hidden, text: $(id).textContent, title: $(id).title});
const stored = key => context.localStorage.getItem(key);

const steps = new Function('h', 'return (async () => {' + fs.readFileSync(stepsFile, 'utf8') + '})();');
steps({ load, tick, click, clickNow, flush, changeLocation, online, offline, snap, table, clickRow, setFilter, el, stored, $ }).then(result => {
    fs.writeFileSync(outFile, JSON.stringify(result));
}, err => { console.error(err); process.exit(1); });
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, steps, rigs=None, search="", i18n=None, sim=None):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "steps.js"
    case.write_text(steps)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"inputs": {"location": LONDON}, "i18n": i18n or I18N,
                               "rigs": rigs if rigs is not None else [], "search": search, "sim": sim}))
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


def test_calendar_shows_calculating_and_builds_after_a_frame(tmp_path):
    """The calendar is slow to build: showing its tab paints the translated
    "Calculating..." in place of the table first and builds after a frame; once built,
    the text is gone and the table is back. Shown again unchanged, it does not rebuild."""
    got = _run(tmp_path, """
        const out = {};
        h.online();
        await h.load('bodies');
        await h.clickNow('calendar');
        out.clicked = h.snap();
        await h.flush();
        out.built = h.snap();
        await h.click('bodies');
        await h.clickNow('calendar');
        out.again = h.snap();
        return out;
    """)
    clicked, built, again = got["clicked"], got["built"], got["again"]
    assert clicked["calLoading"] and clicked["calHidden"] and clicked["calRows"] == 0
    assert built["frames"] == 1, "the build waits for one frame"
    assert not built["calLoading"] and not built["calHidden"] and built["calRows"] > 0
    assert built["calError"] is None
    assert not again["calLoading"] and not again["calHidden"] and again["frames"] == 1


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



# ------------------------------------------------------------------ Rig fit, rings, conjunctions, filter

# Simulated at the current wall clock of this date, so the events do not depend on today
SIM_DATE = "2026-10-31"

FULL_I18N = dict(I18N, **{
    "bodies": {"mercury": "Mercury", "venus": "Venus", "mars": "Mars", "jupiter": "Jupiter",
               "saturn": "Saturn", "uranus": "Uranus", "neptune": "Neptune", "moon": "Moon"},
    "rings": "rings {value}", "ringTiltHelp": "TILT HELP",
    "conjunction": "Conjunction {sep}°", "tooCloseToSun": "SUN {elong}",
    "belowThresholdNight": "LOW", "exactTime": "AT {time}",
    "closestApproach": "CLOSEST {date}, {dist} million km",
    "eventHelp": {"opposition": "OPP HELP", "conjunction": "CONJ HELP", "conjunctionMoon": "CONJ MOON HELP"},
    "rigFit": {
        "title": "Rig fit", "ideal": "ideal f/{ratio}", "noneNeeded": "none needed",
        "aboveIdeal": "above ideal", "exactFactor": "EXACT {factor}",
        "rotationLimit": "Rotation limit", "rotationHelp": "ROT HELP", "minSec": "{min} min {sec} s",
        "mosaic": "Mosaic", "mosaicHelp": "MOSAIC HELP", "fitsOneFrame": "Fits in one frame",
        "panels": "{n} panels ({cols} × {rows})",
        "missing": {"noTelescope": "NO TEL", "noCamera": "NO CAM", "focalLength": "NO FL",
                    "aperture": "NO APERTURE", "pixelSize": "NO PX", "sensorSize": "NO SENSOR"},
    },
})

RIGS = [
    {"name": "C11 <img src=x onerror=alert(1)>", "has_telescope": True, "has_camera": True,
     "focal_length_mm": 2800.0, "aperture_mm": 280.0, "factor": None,
     "pixel_size_um": 2.9, "sensor_width_mm": 5.6, "sensor_height_mm": 3.2},
    {"name": "No aperture", "has_telescope": True, "has_camera": True,
     "focal_length_mm": 1000.0, "aperture_mm": None, "factor": None,
     "pixel_size_um": 3.76, "sensor_width_mm": 23.5, "sensor_height_mm": 15.7},
]


# Loads the Bodies tab (the body comes from ?body=) and reads the card and the table.
CARD = """
    h.online();
    await h.load('bodies');
    return {title: h.el('solar-rigfit-title'), card: h.el('solar-rigfit'),
            wrapper: h.el('solar-rigfit-wrapper'), empty: h.el('solar-rigfit-empty'),
            extra: h.el('solar-rigfit-extra'), extraLabel: h.el('solar-rigfit-extra-label'),
            rows: h.table('solar-rigfit-body'), bodies: h.table('solar-body')};
"""


def _cells(row):
    return {c["col"]: c for c in row["cells"]}


def test_rig_fit_card_for_jupiter(tmp_path):
    """One line per rig, the name as text; the rotation limit column; a rig without an
    aperture shows a dash with the field in the tooltip for the focal ratio, Barlow and
    rotation limit, and still its disc in pixels."""
    got = _run(tmp_path, CARD, rigs=RIGS, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert not got["card"]["hidden"] and not got["wrapper"]["hidden"] and got["empty"]["hidden"]
    assert re.fullmatch(r"Rig fit · Jupiter \(\d+\.\d″\)", got["title"]["text"])
    assert not got["extra"]["hidden"]
    assert got["extraLabel"]["text"] == "Rotation limit" and got["extra"]["title"] == "ROT HELP"
    c11, bare = (_cells(r) for r in got["rows"])
    assert c11["rig"]["text"] == "C11 <img src=x onerror=alert(1)>" and c11["rig"]["spans"] == []
    assert re.fullmatch(r"\d+ px", c11["disc"]["text"])
    assert c11["fratio"]["text"] == "f/10.0 · ideal f/14.5"
    assert c11["barlow"]["text"] == "1.5×" and c11["barlow"]["title"] == "EXACT 1.45×"
    assert re.fullmatch(r"\d+ min \d+ s", c11["extra"]["text"])
    assert re.fullmatch(r"\d+ px", bare["disc"]["text"])
    for col in ("barlow", "extra"):
        assert bare[col]["spans"] == [{"text": "–", "title": "NO APERTURE", "cls": "solar-missing"}], col
    assert bare["fratio"]["spans"][0] == {"text": "–", "title": "NO APERTURE", "cls": "solar-missing"}
    assert bare["fratio"]["text"] == "– · ideal f/18.8"


def test_rig_fit_card_for_saturn_and_the_moon(tmp_path):
    """Saturn: the disc with the rings' width and the ring tilt under the Bodies table's
    diameter; the Moon: the mosaic column instead of the rotation limit; Venus: neither."""
    saturn = _run(tmp_path, CARD, rigs=RIGS, search="?body=saturn", i18n=FULL_I18N, sim=SIM_DATE)
    assert re.fullmatch(r"\d+ px · rings \d+ px", _cells(saturn["rows"][0])["disc"]["text"])
    row = next(r for r in saturn["bodies"] if r["body"] == "saturn")
    diam = _cells(row)["diam"]
    assert diam["spans"][1]["cls"] == "solar-sub" and diam["spans"][1]["title"] == "TILT HELP"
    assert re.fullmatch(r"rings [+−]\d+\.\d°", diam["spans"][1]["text"])
    assert all(_cells(r)["diam"]["spans"] == [] for r in saturn["bodies"] if r["body"] != "saturn")

    moon = _run(tmp_path, CARD, rigs=RIGS, search="?body=moon", i18n=FULL_I18N, sim=SIM_DATE)
    assert moon["extraLabel"]["text"] == "Mosaic" and moon["extra"]["title"] == "MOSAIC HELP"
    c11, bare = (_cells(r)["extra"]["text"] for r in moon["rows"])
    assert re.fullmatch(r"\d+ panels \(\d+ × \d+\)", c11)
    assert bare == "Fits in one frame"

    venus = _run(tmp_path, CARD, rigs=RIGS, search="?body=venus", i18n=FULL_I18N, sim=SIM_DATE)
    assert venus["extra"]["hidden"] and all("extra" not in _cells(r) for r in venus["rows"])


def test_no_active_rig_shows_the_configuration_line(tmp_path):
    got = _run(tmp_path, CARD, rigs=[], search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert not got["card"]["hidden"] and got["wrapper"]["hidden"] and not got["empty"]["hidden"]
    assert got["rows"] == []


def test_conjunction_rows_and_click(tmp_path):
    """A conjunction row names both bodies, brighter first; mag and diameter of both;
    the Sun rule's tooltip; Mars's opposition carries the closest approach; a click
    opens the brighter body."""
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        const rows = h.table('solar-cal-body');
        const i = rows.findIndex(r => r.cells[2].text.startsWith('Conjunction') && r.cells[1].text.indexOf('Moon') < 0);
        const before = h.table('solar-body').find(r => r.classes.indexOf('active-project-row') >= 0).body;
        await h.clickRow('solar-cal-body', i);
        const after = h.table('solar-body').find(r => r.classes.indexOf('active-project-row') >= 0).body;
        return {rows: rows, i: i, before: before, after: after, bodiesShown: h.snap().bodiesShown};
    """, i18n=FULL_I18N, sim=SIM_DATE)
    conj = [r for r in got["rows"] if r["cells"][2]["text"].startswith("Conjunction")]
    assert len(conj) >= 20
    for r in conj:
        c = _cells(r)
        names = c["body"]["text"].split(" – ")
        assert len(names) == 2 and r["body"] == names[0].lower()
        assert re.fullmatch(r"Conjunction \d\.\d°", c["event"]["text"])
        assert c["event"]["title"] == ("CONJ MOON HELP" if "Moon" in names else "CONJ HELP")
        assert re.fullmatch(r"-?\d+\.\d / -?\d+\.\d", c["mag"]["text"])
        assert " / " in c["diam"]["text"]
    sun = [r for r in conj if r["title"].startswith("SUN ")]
    assert sun and all("geometrically-impossible" in r["classes"] for r in sun)
    mars = next(r for r in got["rows"] if r["body"] == "mars" and _cells(r)["event"]["text"] == "opposition")
    assert _cells(mars)["event"]["title"] == "OPP HELP\nCLOSEST 2027-02-19, 101.4 million km"
    clicked = got["rows"][got["i"]]
    assert got["bodiesShown"] and got["after"] == clicked["body"]


def test_calendar_filter_hides_rows_and_is_remembered(tmp_path):
    """A body keeps its own events and its conjunctions; "hide" drops the muted rows;
    both are stored and come back on the next load; nothing is recalculated."""
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        const all = h.table('solar-cal-body');
        await h.setFilter('jupiter', false);
        const jupiter = h.table('solar-cal-body');
        await h.setFilter('', true);
        const visible = h.table('solar-cal-body');
        await h.setFilter('nonsense', false);
        const reset = h.table('solar-cal-body');
        await h.setFilter('saturn', true);
        return {all: all, jupiter: jupiter, visible: visible, reset: reset,
                stored: [h.stored('nova_solar_cal_body'), h.stored('nova_solar_cal_hide_muted')]};
    """, i18n=FULL_I18N, sim=SIM_DATE)
    assert 0 < len(got["jupiter"]) < len(got["all"])
    assert all("Jupiter" in r["cells"][1]["text"] for r in got["jupiter"])
    assert any(" – " in r["cells"][1]["text"] for r in got["jupiter"])
    assert 0 < len(got["visible"]) < len(got["all"])
    assert all("geometrically-impossible" not in r["classes"] for r in got["visible"])
    assert len(got["reset"]) == len(got["all"])
    assert got["stored"] == ["saturn", "true"]
