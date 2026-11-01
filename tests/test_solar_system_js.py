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

// Every scrollIntoView and focus call, with the element's id
const viewCalls = [];

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
        scrollIntoView() { viewCalls.push(['scrollIntoView', el.id]); },
        focus() { viewCalls.push(['focus', el.id]); },
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

// A storage that refuses every read and write (blocked site data)
const throwing = () => ({ getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } });
if (input.storage && input.storage !== 'throws') {
    // Preset keys, before the page loads
    input.presetStorage = input.storage;
}

let routes = {};
let interval = null;
let frames = 0;
const chartCalls = { create: 0, show: 0, resize: 0, updateNow: 0, destroy: 0 };
const charts = [];

const context = {
    console: { log() {}, warn() {}, error() {} },
    URL: URL, URLSearchParams: URLSearchParams, JSON: JSON,
    localStorage: input.storage === 'throws' ? throwing() : store(), sessionStorage: store(),
    solarI18n: input.i18n,
    solarEquipment: input.equipment,
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
            const chart = { show() { chartCalls.show++; }, updateNow() { chartCalls.updateNow++; },
                            resize() { chartCalls.resize++; }, destroy() { chartCalls.destroy++; } };
            charts.push(chart);
            return chart;
        },
    },
    document: {
        readyState: 'complete',
        documentElement: { lang: 'en' },
        getElementById: $,
        createElement: tag => makeElement(tag),
        createElementNS: (ns, tag) => Object.assign(makeElement(tag), {namespaceURI: ns}),
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
// Calendar builds, counted: NovaSolarCalendar.events() is the build
let builds = 0;
const calendarEvents = context.NovaSolarCalendar.events;
context.NovaSolarCalendar.events = function () { builds++; return calendarEvents.apply(this, arguments); };

const settle = () => new Promise(resolve => setTimeout(resolve, 0));
async function load(tab) {
    if (input.presetStorage) Object.entries(input.presetStorage).forEach(([k, v]) => context.localStorage.setItem(k, v));
    if (tab && input.storage !== 'throws') context.localStorage.setItem('nova_solar_tab', tab);
    if (input.sim && input.storage !== 'throws') { context.localStorage.setItem('simModeActive', 'true'); context.localStorage.setItem('simDate', input.sim); }
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
    resize: chartCalls.resize, intervalSet: interval !== null, builds: builds,
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
async function choose(id, value) { $(id).value = value; await $(id).fire('change'); for (let i = 0; i < 20; i++) await settle(); }
// A select as data: its value and its options' value and text
const select = id => ({value: $(id).value, options: $(id).children.map(o => [o.value, o.textContent])});
async function setFilter(body, hide) {
    $('solar-cal-body-filter').value = body; await $('solar-cal-body-filter').fire('change');
    $('solar-cal-hide-muted').checked = hide; await $('solar-cal-hide-muted').fire('change');
}
const el = id => ({hidden: $(id).hidden, text: $(id).textContent, title: $(id).title});
const stored = key => context.localStorage.getItem(key);
const preset = (key, value) => context.localStorage.setItem(key, value);

const steps = new Function('h', 'return (async () => {' + fs.readFileSync(stepsFile, 'utf8') + '})();');
const views = () => viewCalls.slice();
const chartState = () => Object.assign({instances: charts.length, current: charts.indexOf(charts[charts.length - 1])}, chartCalls);
steps({ load, tick, click, clickNow, flush, changeLocation, online, offline, snap, table, clickRow, setFilter, el, stored, preset, choose, select, views, chartState, $ }).then(result => {
    fs.writeFileSync(outFile, JSON.stringify(result));
}, err => { console.error(err); process.exit(1); });
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, steps, equipment=None, search="", i18n=None, sim=None, storage=None):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "steps.js"
    case.write_text(steps)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"inputs": {"location": LONDON}, "i18n": i18n or I18N,
                               "equipment": equipment if equipment is not None else {"telescopes": [], "cameras": []},
                               "search": search, "sim": sim, "storage": storage}))
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
    "occultation": "OCCULTATION", "daylightAtClosest": "DAYLIGHT",
    "belowThresholdAtClosest": "LOW AT ({first} {first_alt}°, {second} {second_alt}°)",
    "belowThresholdNight": "LOW", "exactTime": "AT {time}",
    "closestApproach": "CLOSEST {date}, {dist} million km",
    "eventHelp": {"opposition": "OPP HELP", "conjunction": "CONJ HELP", "conjunctionMoon": "CONJ MOON HELP",
                  "occultation": "OCC HELP"},
    "calc": {
        "title": "Planetary calculator", "none": "None", "disc": "Disc", "discHelp": "DISC HELP",
        "focalRatio": "Focal ratio", "focalRatioHelp": "FR HELP", "ideal": "ideal f/{ratio}",
        "status": {"onTarget": "on target", "below": "below ideal", "above": "above ideal"},
        "detail": "Detail", "detailHelp": "DETAIL HELP",
        "detailValue": {"fine": "Fine detail", "main": "Main features", "discOnly": "Disc only"},
        "detailPoints": "about {n} resolvable points across",
        "hint": {"disc": "HINT DISC", "onTarget": "HINT ON", "below": "HINT UP", "above": "HINT DOWN",
                 "detail": "HINT DETAIL", "rotation": "HINT ROT", "mosaic": "HINT MOSAIC"},
        "rotationLimit": "Rotation limit", "rotationHelp": "ROT HELP", "minSec": "{min} min {sec} s",
        "mosaic": "Mosaic", "mosaicHelp": "MOSAIC HELP", "fitsOneFrame": "Fits in one frame",
        "panels": "{n} panels ({cols} × {rows})",
        "summary": {
            "sentence": "{level} setup for {body}.",
            "level": {"excellent": "Excellent", "good": "Good", "limited": "Limited"},
            "levelHelp": "LEVEL HELP {alt}",
            "low": "{body} is low tonight (max {alt}°); expect atmospheric blur.",
            "amp": "Use {amp} amplification for the best sampling.",
            "bestAlready": "{body}: no amplification brings this telescope and camera closer to the ideal.",
            "smallDisc": "{body} stays a small disc with any telescope of this size.",
            "fine": "Fine detail possible, focal ratio on target.",
            "main": "Main features possible, focal ratio on target.",
            "moon": "Focal ratio on target.",
        },
        "preview": {
            "aria": "PREVIEW sensor {w} × {h}, {body} {disc} px",
            "phaseHelp": "PHASE HELP", "notToScale": "not to scale (too small)",
            "roi": "A {w} × {h} region of interest (ROI) is enough: more frames per second.",
            "roiHelp": "ROI HELP", "none": "No scale preview.",
        },
        "missing": {"focalLength": "NO FL", "aperture": "NO APERTURE", "pixelSize": "NO PX",
                    "sensorSize": "NO SENSOR", "sensorImplausible": "CHECK SENSOR MM",
                    "notComputable": "NOT COMPUTABLE"},
    },
})

# Telescopes and cameras as core.py _solar_equipment embeds them, by name. The defaults:
# the C11 (largest aperture) and the ASI462 (smallest pixel); f/10 against the ideal
# f/14.5 makes 1.5x the closest amplification.
EQUIPMENT = {
    "telescopes": [
        {"uid": "t-apo", "name": "Apo", "aperture_mm": 100.0, "focal_length_mm": 550.0},
        {"uid": "t-c11", "name": "C11 <img src=x onerror=alert(1)>", "aperture_mm": 280.0, "focal_length_mm": 2800.0},
        {"uid": "t-bare", "name": "No aperture", "aperture_mm": None, "focal_length_mm": 1000.0},
    ],
    "cameras": [
        {"uid": "c-2600", "name": "ASI2600", "pixel_size_um": 3.76, "sensor_width_mm": 23.5, "sensor_height_mm": 15.7},
        {"uid": "c-462", "name": "ASI462", "pixel_size_um": 2.9, "sensor_width_mm": 5.6, "sensor_height_mm": 3.2},
    ],
}

# Loads the Bodies tab (the body comes from ?body=) and reads the calculator.
CARD_READ = """
    const lines = h.$('solar-calc-result').children.map(r => {
        const [dt, dd] = r.children;
        const isHint = c => c.className.split(' ').indexOf('solar-calc-hint') >= 0;
        const values = dd.children.filter(c => !isHint(c));
        const hints = dd.children.filter(isHint);
        return {key: r.dataset.calc, label: dt.textContent, help: dt.title,
                text: values.map(c => c.textContent).join(''),
                spans: values.map(c => ({text: c.textContent, title: c.title, cls: c.className})),
                hints: hints.map(c => c.textContent), last: dd.children.length ? dd.children[dd.children.length - 1].className : null};
    });
    const sumEl = h.$('solar-calc-summary');
    const summary = {hidden: sumEl.hidden, text: sumEl.children.map(c => c.textContent).join(''),
                     parts: sumEl.children.map(c => ({tag: c.tagName, text: c.textContent, title: c.title,
                                                      cls: c.className, level: c.dataset.level}))};
    const node = el => ({tag: el.tagName, ns: el.namespaceURI || null, attrs: el.attrs, cls: el.className,
                         title: el.title, text: el.textContent, style: el.style, children: el.children.map(node)});
    const preview = {hidden: h.$('solar-calc-preview').hidden, children: h.$('solar-calc-preview').children.map(node)};
    return {preview: preview, summary: summary, title: h.el('solar-calc-title'), card: h.el('solar-calc'), controls: h.el('solar-calc-controls'),
            result: h.el('solar-calc-result'), empty: h.el('solar-calc-empty'),
            tel: h.select('solar-calc-telescope'), cam: h.select('solar-calc-camera'),
            amp: h.select('solar-calc-amp'), lines: lines, bodies: h.table('solar-body')};
"""
CARD = "h.online();\nawait h.load('bodies');\n" + CARD_READ


def _cells(row):
    return {c["col"]: c for c in row["cells"]}


def _lines(got):
    return {line["key"]: line for line in got["lines"]}


def test_calculator_for_jupiter_with_the_defaults(tmp_path):
    """The defaults: largest aperture, smallest pixel, the amplification closest to the
    ideal; names as option text only; one line each for the disc, the focal ratio with
    its status, the detail verdict and the rotation limit, each label with its tooltip."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert not got["card"]["hidden"] and not got["controls"]["hidden"] and not got["result"]["hidden"]
    assert got["empty"]["hidden"]
    assert re.fullmatch(r"Planetary calculator · Jupiter \(\d+\.\d″\)", got["title"]["text"])
    assert got["tel"] == {"value": "t-c11", "options": [["t-apo", "Apo"], ["t-c11", "C11 <img src=x onerror=alert(1)>"],
                                                       ["t-bare", "No aperture"]]}
    assert got["cam"] == {"value": "c-462", "options": [["c-2600", "ASI2600"], ["c-462", "ASI462"]]}
    assert got["amp"] == {"value": "1.5", "options": [["1", "None (1×)"], ["1.5", "1.5×"], ["2", "2×"],
                                                      ["2.5", "2.5×"], ["3", "3×"], ["4", "4×"], ["5", "5×"]]}
    lines = _lines(got)
    assert list(lines) == ["disc", "fratio", "detail", "rotation"]
    assert [(l["label"], l["help"]) for l in got["lines"]] == [
        ("Disc", "DISC HELP"), ("Focal ratio", "FR HELP"), ("Detail", "DETAIL HELP"), ("Rotation limit", "ROT HELP")]
    assert re.fullmatch(r"\d+ px", lines["disc"]["text"])
    assert lines["fratio"]["text"] == "f/15.0 · ideal f/14.5 · on target"
    assert re.fullmatch(r"Fine detail · about \d+ resolvable points across", lines["detail"]["text"])
    assert re.fullmatch(r"\d+ min \d+ s", lines["rotation"]["text"])
    # A hint under each value, after it
    assert {k: (l["hints"], l["last"]) for k, l in lines.items()} == {
        "disc": (["HINT DISC"], "solar-calc-hint"), "fratio": (["HINT ON"], "solar-calc-hint solar-calc-hint-fratio"),
        "detail": (["HINT DETAIL"], "solar-calc-hint"), "rotation": (["HINT ROT"], "solar-calc-hint")}


def test_calculator_for_saturn_the_moon_and_venus(tmp_path):
    """Saturn: the disc with the rings' width, and the ring tilt under the Bodies table's
    diameter; the Moon: the mosaic and no verdict; Venus: neither a rotation limit nor a
    mosaic."""
    saturn = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=saturn", i18n=FULL_I18N, sim=SIM_DATE)
    assert re.fullmatch(r"\d+ px · rings \d+ px", _lines(saturn)["disc"]["text"])
    assert list(_lines(saturn)) == ["disc", "fratio", "detail", "rotation"]
    row = next(r for r in saturn["bodies"] if r["body"] == "saturn")
    diam = _cells(row)["diam"]
    assert diam["spans"][1]["cls"] == "solar-sub" and diam["spans"][1]["title"] == "TILT HELP"
    assert re.fullmatch(r"rings [+−]\d+\.\d°", diam["spans"][1]["text"])
    assert all(_cells(r)["diam"]["spans"] == [] for r in saturn["bodies"] if r["body"] != "saturn")

    moon = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=moon", i18n=FULL_I18N, sim=SIM_DATE)
    lines = _lines(moon)
    assert list(lines) == ["disc", "fratio", "mosaic"]
    assert lines["mosaic"]["label"] == "Mosaic" and lines["mosaic"]["help"] == "MOSAIC HELP"
    assert re.fullmatch(r"\d+ panels \(\d+ × \d+\)", lines["mosaic"]["text"])
    assert lines["mosaic"]["hints"] == ["HINT MOSAIC"] and lines["disc"]["hints"] == ["HINT DISC"]

    venus = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=venus", i18n=FULL_I18N, sim=SIM_DATE)
    assert list(_lines(venus)) == ["disc", "fratio", "detail"]

    # Neptune's 2.3" in a 280 mm aperture: about 6 points, the disc only
    neptune = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=neptune", i18n=FULL_I18N, sim=SIM_DATE)
    assert re.fullmatch(r"Disc only · about [1-9] resolvable points across", _lines(neptune)["detail"]["text"])


@pytest.mark.parametrize("missing", ["telescopes", "cameras"])
def test_without_a_telescope_or_a_camera_shows_the_configuration_line(tmp_path, missing):
    equipment = dict(EQUIPMENT, **{missing: []})
    got = _run(tmp_path, CARD, equipment=equipment, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert not got["card"]["hidden"] and not got["empty"]["hidden"]
    assert got["controls"]["hidden"] and got["result"]["hidden"] and got["lines"] == []
    assert got["summary"]["hidden"] and got["summary"]["text"] == ""
    assert got["preview"]["hidden"] and got["preview"]["children"] == []
    assert re.fullmatch(r"Planetary calculator · Jupiter \(\d+\.\d″\)", got["title"]["text"])


def test_choice_is_stored_and_restored(tmp_path):
    """Each select stores its choice by record_uid (the amplification as a number) and
    re-renders; before an amplification is picked, a new telescope or camera gets the
    one closest to the ideal; a picked one stays. On the next load all three come back."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const before = h.stored('nova_solar_calc_amp');
        await h.choose('solar-calc-camera', 'c-2600');      // f/10 vs 18.8: 2x
        const autoAmp = h.select('solar-calc-amp').value;
        await h.choose('solar-calc-amp', '3');
        await h.choose('solar-calc-telescope', 't-apo');    // stays 3x
        const after = {tel: h.stored('nova_solar_calc_telescope'), cam: h.stored('nova_solar_calc_camera'),
                       amp: h.stored('nova_solar_calc_amp'), ampSelect: h.select('solar-calc-amp').value,
                       fratio: h.$('solar-calc-result').children[1].children[1].children
                           .filter(c => c.className.indexOf('solar-calc-hint') < 0).map(c => c.textContent).join('')};
        h.$('solar-calc-telescope').children = []; h.$('solar-calc-camera').children = []; h.$('solar-calc-amp').children = [];
        await h.load('bodies');
    """ + CARD_READ.replace("return {", "return {before: before, autoAmp: autoAmp, after: after, "),
               equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert got["before"] is None and got["autoAmp"] == "2"
    assert got["after"] == {"tel": "t-apo", "cam": "c-2600", "amp": "3", "ampSelect": "3",
                            "fratio": "f/16.5 · ideal f/18.8 · on target"}
    assert (got["tel"]["value"], got["cam"]["value"], got["amp"]["value"]) == ("t-apo", "c-2600", "3")
    assert got["amp"]["options"][0] == ["1", "None (1×)"] and len(got["amp"]["options"]) == 7


@pytest.mark.parametrize("storage, tel, cam, amp", [
    ({"nova_solar_calc_telescope": "t-deleted", "nova_solar_calc_camera": "c-foreign",
      "nova_solar_calc_amp": "1.75"}, "t-c11", "c-462", "1.5"),
    ({"nova_solar_calc_telescope": "t-bare", "nova_solar_calc_amp": "nonsense"}, "t-bare", "c-462", "1"),
    ({"nova_solar_calc_amp": "5"}, "t-c11", "c-462", "5"),
    ("throws", "t-c11", "c-462", "1.5"),
])
def test_unknown_stored_choice_falls_back_to_the_default(tmp_path, storage, tel, cam, amp):
    """A uid no longer in the list (deleted, another user's) or an amplification not in
    the list gives the default; a telescope without an aperture gets none (1x); a
    storage that throws on every access still renders the defaults."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N,
               sim=None if storage == "throws" else SIM_DATE, storage=storage)
    assert (got["tel"]["value"], got["cam"]["value"], got["amp"]["value"]) == (tel, cam, amp)
    assert not got["result"]["hidden"] and len(got["lines"]) == 4


@pytest.mark.parametrize("amp, text, hints", [
    ("1", "f/10.0 · ideal f/14.5 · below ideal", ["HINT UP"]),
    ("1.5", "f/15.0 · ideal f/14.5 · on target", ["HINT ON"]),
    ("3", "f/30.0 · ideal f/14.5 · above ideal", ["HINT DOWN"]),
])
def test_focal_ratio_hint_follows_the_status(tmp_path, amp, text, hints):
    """The C11 (f/10) with the ASI462 (ideal f/14.5): the hint under the focal ratio says
    which way to change the amplification, or that it matches the camera."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE,
               storage={"nova_solar_calc_amp": amp})
    fr = _lines(got)["fratio"]
    assert fr["text"] == text and fr["hints"] == hints
    assert fr["spans"][-1]["cls"] == "solar-calc-status"


# On SIM_DATE in London (max altitude tonight): Jupiter 52°, Saturn 40°, Mars 55°,
# Neptune 38°, Venus 4°, the Moon 64°. The defaults: the C11 and the ASI462 at 1.5x, on target.
@pytest.mark.parametrize("body, text", [
    ("jupiter", "Excellent setup for Jupiter. Fine detail possible, focal ratio on target."),
    ("saturn", "Excellent setup for Saturn. Fine detail possible, focal ratio on target."),
    ("mars", "Good setup for Mars. Main features possible, focal ratio on target."),
    ("neptune", "Limited setup for Neptune. Neptune stays a small disc with any telescope of this size."),
    ("venus", "Limited setup for Venus. Venus is low tonight (max 4°); expect atmospheric blur."),
    ("moon", "Excellent setup for Moon. Focal ratio on target."),
])
def test_summary_line_per_body(tmp_path, body, text):
    """The summary comes first, above the values, from the fit and the Bodies table's
    max altitude tonight."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=" + body, i18n=FULL_I18N, sim=SIM_DATE)
    assert not got["summary"]["hidden"] and got["summary"]["text"] == text


def test_summary_level_is_bold_with_its_criteria(tmp_path):
    """The translation places the level; the level word is its own bold element with the
    three criteria (and the altitude threshold) in its tooltip; the next step follows."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    parts = got["summary"]["parts"]
    level = [p for p in parts if p["tag"] == "strong"]
    assert level == [{"tag": "strong", "text": "Excellent", "title": "LEVEL HELP 30",
                      "cls": "solar-calc-level", "level": "excellent"}]
    assert parts[-1]["cls"] == "solar-calc-next"
    i18n = json.loads(json.dumps(FULL_I18N))
    i18n["calc"]["summary"]["sentence"] = "Setup für {body}: {level}."
    de = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=i18n, sim=SIM_DATE)
    assert [p["text"] for p in de["summary"]["parts"]][:3] == ["Setup für Jupiter: ", "Excellent", "."]


def test_summary_with_a_wrong_amplification(tmp_path):
    """3x on the C11 with the ASI462 is above the ideal: good, and the step names the best one."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE,
               storage={"nova_solar_calc_amp": "3"})
    assert got["summary"]["text"] == "Good setup for Jupiter. Use 1.5× amplification for the best sampling."


def test_summary_when_the_amplification_is_already_the_best(tmp_path):
    """An f/20 scope with the ASI462 (ideal f/14.5) is above the ideal even without
    amplification, and none is the best: the step says so instead of a useless advice."""
    slow = {"telescopes": [{"uid": "t-slow", "name": "Slow", "aperture_mm": 100.0, "focal_length_mm": 2000.0}],
            "cameras": EQUIPMENT["cameras"]}
    got = _run(tmp_path, CARD, equipment=slow, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert got["amp"]["value"] == "1"
    assert got["summary"]["text"] == ("Good setup for Jupiter. Jupiter: no amplification brings this "
                                      "telescope and camera closer to the ideal.")


def test_summary_with_missing_data(tmp_path):
    """A telescope without an aperture has no focal-ratio status and no detail verdict:
    limited, and no next step to suggest."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE,
               storage={"nova_solar_calc_telescope": "t-bare"})
    assert got["summary"]["text"] == "Limited setup for Jupiter."
    assert [p["level"] for p in got["summary"]["parts"] if p["tag"] == "strong"] == ["limited"]


def _figure(got):
    return next(c for c in got["preview"]["children"] if c["cls"] == "solar-preview-figure")


def _labels(got):
    """The HTML labels over the drawing: (text, class, left, top)."""
    return [(c["text"], c["cls"], c["style"].get("left"), c["style"].get("top"))
            for c in _figure(got)["children"] if c["tag"] == "span"]


def _svg(got):
    """The preview's SVG and its elements in drawing order (nested in the body group)."""
    svg = next(c for c in _figure(got)["children"] if c["tag"] == "svg")
    flat = []
    def walk(n):
        for c in n["children"]:
            flat.append(c)
            walk(c)
    walk(svg)
    return svg, flat


@pytest.mark.parametrize("body", ["jupiter", "saturn", "venus", "moon"])
def test_scale_preview_renders(tmp_path, body):
    """An SVG image in the SVG namespace, named for screen readers with the numbers: the
    sensor labelled with its pixels, the body (Saturn: back ring, globe, front ring;
    Venus: phase with its tooltip; the Moon: the Mosaic line's grid, no ROI); planets
    small enough get the dashed ROI and its hint."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=" + body, i18n=FULL_I18N, sim=SIM_DATE)
    assert not got["preview"]["hidden"]
    svg, flat = _svg(got)
    assert svg["ns"] == "http://www.w3.org/2000/svg" and svg["attrs"]["role"] == "img"
    name = {"jupiter": "Jupiter", "saturn": "Saturn", "venus": "Venus", "moon": "Moon"}[body]
    assert re.fullmatch(r"PREVIEW sensor 1931 × 1103, " + name + r" \d+ px", svg["attrs"]["aria-label"])
    sensor = flat[0]
    assert sensor["tag"] == "rect" and sensor["attrs"]["class"] == "solar-preview-sensor"
    assert (sensor["attrs"]["width"], sensor["attrs"]["height"]) == ("1931", "1103")
    # No SVG text: the pixel label is HTML at the sensor's corner
    assert [n for n in flat if n["tag"] == "text"] == []
    assert _labels(got)[0][:2] == ("1931 × 1103 px", "solar-preview-label")
    vb = [float(v) for v in svg["attrs"]["viewBox"].split()]
    if body == "jupiter":
        # Drawn edge to edge: the sensor's corner is the figure's, level with the summary
        assert vb == pytest.approx([0, 0, 1931, 1103], abs=1e-9)
        assert _labels(got)[0][2:] == ("0%", "0%")
    group = next(n for n in flat if n["tag"] == "g")
    assert group["attrs"]["data-body"] == body
    shapes = [(c["tag"], c["attrs"].get("class")) for c in group["children"]]
    slot = next(c for c in got["preview"]["children"] if c["cls"] == "solar-preview-roi-slot")
    hints = [c for c in _figure(got)["children"] + slot["children"] if "solar-calc-roi" in c["cls"]]
    assert svg["attrs"]["preserveAspectRatio"] == "xMinYMin meet"
    rois = [n for n in flat if n["attrs"].get("class") == "solar-preview-roi"]
    frames = [n for n in flat if n["attrs"].get("class") == "solar-preview-frame"]
    if body == "saturn":
        assert shapes == [("path", "solar-preview-ring"), ("circle", "solar-preview-lit"), ("path", "solar-preview-ring")]
    elif body == "venus":
        assert shapes == [("circle", "solar-preview-unlit"), ("path", "solar-preview-lit"), ("title", None)]
        assert group["children"][2]["text"] == "PHASE HELP"
    elif body == "jupiter":
        assert shapes == [("circle", "solar-preview-lit")]
    if body == "moon":
        mosaic = _lines(got)["mosaic"]["text"]
        n = int(re.match(r"(\d+) panels", mosaic).group(1)) if "panels" in mosaic else 0
        assert len(frames) == (n if n > 1 else 0)
        assert rois == [] and hints == []
    else:
        assert frames == [] and len(rois) == 1
        w, h = rois[0]["attrs"]["width"], rois[0]["attrs"]["height"]
        assert [(d["text"], d["title"]) for d in hints] == [
            (f"A {w} × {h} region of interest (ROI) is enough: more frames per second.", "ROI HELP")]


def test_scale_preview_tiny_disc_is_labelled(tmp_path):
    """Neptune at 550 mm on an APS-C sensor (3.76 µm): drawn at the minimum size with
    "not to scale (too small)" under it; the ROI still from its real size."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=neptune", i18n=FULL_I18N, sim=SIM_DATE,
               storage={"nova_solar_calc_telescope": "t-apo", "nova_solar_calc_camera": "c-2600",
                        "nova_solar_calc_amp": "1"})
    svg, flat = _svg(got)
    labels = _labels(got)
    assert [l[:2] for l in labels] == [("6250 × 4176 px", "solar-preview-label"),
                                       ("not to scale (too small)", "solar-preview-label solar-preview-note")]
    # Centred under the disc: 6250 x 4176 is a little narrower than the 3:2 box, so the
    # drawing fills the box's height and (6250 / 4176) / 1.5 of its width
    assert float(labels[1][2].rstrip("%")) == pytest.approx(50 * (6250 / 4176) / 1.5, abs=1e-3)
    roi = next(n for n in flat if n["attrs"].get("class") == "solar-preview-roi")
    assert (roi["attrs"]["width"], roi["attrs"]["height"]) == ("320", "240")


def test_scale_preview_without_the_data(tmp_path):
    """A camera without a sensor size: no drawing, one muted line naming the field."""
    no_sensor = {"telescopes": EQUIPMENT["telescopes"],
                 "cameras": [{"uid": "c-x", "name": "X", "pixel_size_um": 2.9, "sensor_width_mm": None,
                              "sensor_height_mm": 3.2}]}
    got = _run(tmp_path, CARD, equipment=no_sensor, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert [c["cls"] for c in got["preview"]["children"]] == ["solar-preview-figure", "solar-preview-roi-slot"]
    assert [(c["tag"], c["cls"], c["text"]) for c in _figure(got)["children"]] == [
        ("p", "solar-calc-preview-missing", "No scale preview. NO SENSOR")]
    assert len(got["lines"]) == 4


def test_preview_box_and_roi_slot_do_not_follow_the_camera(tmp_path):
    """The preview is always a 3:2 box with an ROI slot, whatever the sensor's shape and
    whether there is an ROI (the Moon has none), so the card's height stays put. A 16:9
    sensor fills the box's width: its labels sit in the top (1.5 / 1.78) of the box."""
    wide = {"telescopes": EQUIPMENT["telescopes"],
            "cameras": [{"uid": "c-678", "name": "ASI678", "pixel_size_um": 2.0,
                         "sensor_width_mm": 7.68, "sensor_height_mm": 4.32}]}
    for body in ("jupiter", "moon"):
        got = _run(tmp_path, CARD, equipment=wide, search="?body=" + body, i18n=FULL_I18N, sim=SIM_DATE)
        kinds = [c["cls"] for c in got["preview"]["children"]]
        assert kinds == ["solar-preview-figure", "solar-preview-roi-slot"], body
    got = _run(tmp_path, CARD, equipment=wide, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert _labels(got)[0] == ("3840 × 2160 px", "solar-preview-label", "0%", "0%")


@pytest.mark.parametrize("sw, sh, px, where, top, width", [
    # 16:9 (3840 x 2160): the frame fills the width and 1.5 / 1.778 of the height: the hint
    # goes in the box, at the frame's bottom edge, full width
    (7.68, 4.32, 2.0, "inside", 1.5 / (3840 / 2160), 1.0),
    # APS-C (6250 x 4176, a little narrower than 3:2): the frame fills the height and
    # (6250 / 4176) / 1.5 of the width: the hint goes in the slot, as wide as the frame
    (23.5, 15.7, 3.76, "slot", None, (6250 / 4176) / 1.5),
])
def test_roi_hint_sits_directly_under_the_frame(tmp_path, sw, sh, px, where, top, width):
    cams = {"telescopes": EQUIPMENT["telescopes"],
            "cameras": [{"uid": "c-x", "name": "X", "pixel_size_um": px, "sensor_width_mm": sw, "sensor_height_mm": sh}]}
    got = _run(tmp_path, CARD, equipment=cams, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    slot = next(c for c in got["preview"]["children"] if c["cls"] == "solar-preview-roi-slot")
    inside = [c for c in _figure(got)["children"] if "solar-calc-roi" in c["cls"]]
    below = [c for c in slot["children"] if "solar-calc-roi" in c["cls"]]
    hint = (inside if where == "inside" else below)
    assert len(hint) == 1 and (inside if where == "slot" else below) == []
    hint = hint[0]
    assert float(hint["style"]["width"].rstrip("%")) == pytest.approx(width * 100, abs=1e-3)
    if where == "inside":
        assert "solar-calc-roi-inside" in hint["cls"]
        assert float(hint["style"]["top"].rstrip("%")) == pytest.approx(top * 100, abs=1e-3)
    else:
        assert "top" not in hint["style"]


def test_focal_ratio_hint_always_keeps_its_place(tmp_path):
    """Below, on target, above: one focal-ratio hint element each time, with the two-line
    class, so the status never changes the card's height."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = [];
        for (const a of ['1', '1.5', '5']) {
            await h.choose('solar-calc-amp', a);
            const dd = h.$('solar-calc-result').children[1].children[1];
            out.push(dd.children.filter(c => c.className.indexOf('solar-calc-hint') >= 0)
                .map(c => [c.className, c.textContent]));
        }
        return out;
    """, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert [[t for _, t in hs] for hs in got] == [["HINT UP"], ["HINT ON"], ["HINT DOWN"]]
    assert all("solar-calc-hint-fratio" in hs[0][0] for hs in got)


def test_no_scrolling_or_focus_except_the_calendar_row_click(tmp_path):
    """A refresh, every calculator select and a body selection never scroll or move the
    focus; only a Calendar row click scrolls to the chart."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        await h.tick();
        await h.choose('solar-calc-telescope', 't-apo');
        await h.choose('solar-calc-camera', 'c-2600');
        await h.choose('solar-calc-amp', '2');
        await h.clickRow('solar-body', 0);
        await h.clickRow('solar-body', 3);
        await h.tick();
        const quiet = h.views();
        await h.click('calendar');
        await h.clickRow('solar-cal-body', 0);
        return {quiet: quiet, after: h.views()};
    """, equipment=EQUIPMENT, i18n=FULL_I18N, sim=SIM_DATE)
    assert got["quiet"] == []
    assert got["after"] == [["scrollIntoView", "chart-section"]]


def test_chart_survives_refreshes(tmp_path):
    """The 60 s refresh keeps the same chart and moves its Now line (updateNow, which is
    chart.update('none')) instead of creating or destroying one."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const first = h.chartState();
        await h.tick();
        await h.tick();
        return {first: first, after: h.chartState()};
    """, equipment=EQUIPMENT, i18n=FULL_I18N, sim=SIM_DATE)
    assert got["first"]["instances"] == 1 and got["after"]["instances"] == 1
    assert got["after"]["create"] == 1 and got["after"]["destroy"] == 0
    assert got["after"]["updateNow"] >= got["first"]["updateNow"] + 2
    assert got["after"]["show"] == got["first"]["show"]


def test_implausible_sensor_shows_the_check_line(tmp_path):
    """The ASI678MC with its pixel counts entered as mm (3840 x 2160 "mm"): the preview is
    only the muted check line, no ROI; the Moon's mosaic is a dash with that tooltip; the
    disc and the other values stay."""
    bad = {"telescopes": EQUIPMENT["telescopes"],
           "cameras": [{"uid": "c-678", "name": "ASI 678 MC", "pixel_size_um": 2.0,
                        "sensor_width_mm": 3840.0, "sensor_height_mm": 2160.0}]}
    jup = _run(tmp_path, CARD, equipment=bad, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert [c["cls"] for c in jup["preview"]["children"]] == ["solar-preview-figure", "solar-preview-roi-slot"]
    assert [(c["tag"], c["cls"], c["text"]) for c in _figure(jup)["children"]] == [
        ("p", "solar-calc-preview-missing", "CHECK SENSOR MM")]
    lines = _lines(jup)
    assert re.fullmatch(r"\d+ px", lines["disc"]["text"]) and lines["fratio"]["text"].startswith("f/")
    assert lines["detail"]["text"].startswith("Fine detail") and re.fullmatch(r"\d+ min \d+ s", lines["rotation"]["text"])
    moon = _run(tmp_path, CARD, equipment=bad, search="?body=moon", i18n=FULL_I18N, sim=SIM_DATE)
    assert _lines(moon)["mosaic"]["spans"] == [{"text": "–", "title": "CHECK SENSOR MM", "cls": "solar-missing"}]
    assert [c["text"] for c in _figure(moon)["children"]] == ["CHECK SENSOR MM"]


def test_missing_data_and_absurd_values_show_dashes(tmp_path):
    """A telescope without an aperture: dashes with the field in the tooltip for the
    focal ratio, the verdict and the rotation limit, no status, and still the disc; an
    overflowing focal length: "not computable" dashes, never Infinity or NaN."""
    got = _run(tmp_path, CARD, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE,
               storage={"nova_solar_calc_telescope": "t-bare"})
    lines = _lines(got)
    dash = {"text": "–", "title": "NO APERTURE", "cls": "solar-missing"}
    assert re.fullmatch(r"\d+ px", lines["disc"]["text"])
    assert lines["fratio"]["spans"][0] == dash and lines["fratio"]["text"] == "– · ideal f/14.5"
    assert lines["detail"]["spans"] == [dash] and lines["rotation"]["spans"] == [dash]
    # No status: the focal ratio hint is empty but keeps its place; the other lines keep theirs
    assert lines["fratio"]["hints"] == [""] and lines["detail"]["hints"] == ["HINT DETAIL"]

    huge = dict(EQUIPMENT, telescopes=[{"uid": "t-huge", "name": "Huge", "aperture_mm": 1.0, "focal_length_mm": 1e308}])
    got = _run(tmp_path, CARD, equipment=huge, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE,
               storage={"nova_solar_calc_amp": "5"})
    lines = _lines(got)
    assert lines["disc"]["spans"] == [{"text": "–", "title": "NOT COMPUTABLE", "cls": "solar-missing"}]
    assert lines["fratio"]["spans"][0]["title"] == "NOT COMPUTABLE"
    for line in got["lines"]:
        assert "Infinity" not in line["text"] and "NaN" not in line["text"]


def test_a_choice_re_renders_without_recalculating_the_calendar(tmp_path):
    """Changing a select only redraws the calculator: no calendar build, no chart resize,
    the tables and errors as they were."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const before = h.snap();
        await h.choose('solar-calc-amp', '2');
        await h.choose('solar-calc-telescope', 't-apo');
        const after = h.snap();
        return {before: before, after: after,
                disc: h.$('solar-calc-result').children[0].children[1].children[0].textContent};
    """, equipment=EQUIPMENT, search="?body=jupiter", i18n=FULL_I18N, sim=SIM_DATE)
    assert got["after"] == got["before"]
    assert re.fullmatch(r"\d+ px", got["disc"])


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


def test_moon_conjunction_tooltips_and_occultation(tmp_path):
    """London: Moon-Jupiter on the night of 2026-10-05 is muted with the daylight
    tooltip; Moon-Venus on the night of 2028-03-29 is labelled an occultation, with its
    help, and muted with both altitudes at the closest approach, Moon first."""
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        return h.table('solar-cal-body');
    """, i18n=FULL_I18N, sim=SIM_DATE)

    def row(names, night):
        found = [r for r in got if _cells(r)["body"]["text"] == names and _cells(r)["date"]["text"] == night]
        assert len(found) == 1, names
        return found[0]

    jupiter = row("Moon – Jupiter", "2026-10-05")
    assert "geometrically-impossible" in jupiter["classes"] and jupiter["title"] == "DAYLIGHT"
    assert re.fullmatch(r"Conjunction 0\.\d°", _cells(jupiter)["event"]["text"])
    assert _cells(jupiter)["event"]["title"] == "CONJ MOON HELP"

    venus = row("Moon – Venus", "2028-03-29")
    assert "geometrically-impossible" in venus["classes"]
    assert venus["title"] == "LOW AT (Moon −13°, Venus −13°)"
    assert _cells(venus)["event"]["text"] == "OCCULTATION"
    assert _cells(venus)["event"]["title"] == "OCC HELP"


@pytest.mark.parametrize("stored_body, stored_hide, body, hide", [
    ("saturn", "true", "saturn", True),
    ("moon", "false", "moon", False),
    ("pluto", "yes", "", False),          # unknown values: all bodies, nothing hidden
])
def test_calendar_filter_is_restored_on_load_without_rebuilds(tmp_path, stored_body, stored_hide, body, hide):
    """A fresh page load with stored filter values restores the select and the checkbox
    and shows only the matching rows (the same rows as filtering the full table by hand);
    changing the filter afterwards re-renders without building the calendar again."""
    got = _run(tmp_path, """
        h.preset('nova_solar_cal_body', input_body);
        h.preset('nova_solar_cal_hide_muted', input_hide);
        h.online();
        await h.load('calendar');
        const restored = {select: h.$('solar-cal-body-filter').value, hide: h.$('solar-cal-hide-muted').checked,
                          rows: h.table('solar-cal-body'), builds: h.snap().builds};
        await h.setFilter('', false);
        const all = h.table('solar-cal-body');
        await h.setFilter('jupiter', true);
        await h.setFilter('venus', false);
        await h.setFilter(restored.select, restored.hide);
        const again = h.table('solar-cal-body');
        await h.click('bodies');
        await h.click('calendar');
        await h.tick();
        return {restored: restored, all: all, again: again, builds: h.snap().builds};
    """.replace("input_body", json.dumps(stored_body)).replace("input_hide", json.dumps(stored_hide)),
        i18n=FULL_I18N, sim=SIM_DATE)
    restored = got["restored"]
    assert restored["select"] == body and restored["hide"] is hide
    assert restored["builds"] == 1
    name = FULL_I18N["bodies"].get(body)
    expected = [r for r in got["all"]
                if (not name or name in _cells(r)["body"]["text"].split(" – "))
                and not (hide and "geometrically-impossible" in r["classes"])]
    assert restored["rows"] == expected and got["again"] == expected
    assert 0 < len(expected) <= len(got["all"])
    assert got["builds"] == 1, "filter changes, tab switches and the tick do not rebuild"
