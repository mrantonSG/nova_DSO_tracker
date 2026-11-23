"""The dashboard header shows the location's calendar date; Ask Nova stays per night.

NovaDashboardRows.statusStrip() is run in node with the process in Asia/Singapore for a
London location, so a date taken from the browser's zone instead of the location's
would show. _renderStatusStrip() and getNovaCacheKey() are cut out of dashboard.js and
run against a stand-in DOM: the header (#date text) must show local_date, while the
Ask Nova cache key keeps the observing night in the format it had when it was read off
the header text (novaCache_<loc>_DD.MM.YYYY), so existing cache entries stay valid.
"""

import json
import shutil
import subprocess
from datetime import date, datetime, time
from pathlib import Path

import pytest
import pytz

from nova.config import astro_context_cache
from nova.models import DbUser, Location
from tests.test_dashboard_rows_parity import _ms

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
ENGINE_FILES = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js")
NODE = shutil.which("node")
LONDON = ("London", 51.5074, -0.1278, "Europe/London")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const args = process.argv.slice(2);
const outFile = args.pop(), inFile = args.pop(), utilsFile = args.pop(), dashFile = args.pop();

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
function fn(src, name) {
    const hits = [...src.matchAll(new RegExp('function\\s+' + name + '\\s*\\(', 'g'))];
    if (hits.length !== 1) throw new Error(`${name}: ${hits.length} declarations`);
    return cut(src, hits[0].index);
}

const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of args) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });

const els = {};
for (const id of ['date', 'time', 'phase', 'dawn', 'dusk']) els[id] = { textContent: '', dataset: {} };
els['location-select'] = { value: 'London' };
els['sim-mode-toggle'] = { checked: false };
els['sim-date-input'] = { value: '' };
const store = { selectedLocation: 'London' };
Object.assign(context, {
    document: { getElementById: id => els[id] || null },
    sessionStorage: { getItem: k => (k in store ? store[k] : null) },
});
const dash = fs.readFileSync(dashFile, 'utf8');
const utils = fs.readFileSync(utilsFile, 'utf8');
const api = vm.runInContext(`${fn(utils, 'formatDateISOtoEuropean')}
${fn(dash, '_renderStatusStrip')}
${fn(dash, 'getNovaCacheKey')}
({ render: _renderStatusStrip, key: getNovaCacheKey, fmt: formatDateISOtoEuropean })`, context);

const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const out = { tzOffsetMin: new Date(cases[0].opts.nowMs).getTimezoneOffset(), cases: {} };
// Before any render the key falls back as it always did.
out.keyBeforeRender = api.key();
for (const c of cases) {
    const strip = context.NovaDashboardRows.statusStrip(c.opts);
    els['sim-mode-toggle'].checked = !!c.opts.simDate;
    els['sim-date-input'].value = c.opts.simDate || '';
    api.render(strip);
    out.cases[c.id] = {
        strip, header: els.date.textContent, key: api.key(),
        // What the header text, and so the key, was before local_date existed.
        oldKey: c.opts.simDate ? `novaCache_London_${c.opts.simDate}`
                               : `novaCache_London_${api.fmt(strip.date)}`,
    };
}
fs.writeFileSync(outFile, JSON.stringify(out));
"""

# id: (location wall date, wall clock, sim date, header, observing night, key)
CASES = {
    "before noon": ("2026-11-23", time(11, 36), None, "23.11.2026", "2026-11-22",
                    "novaCache_London_22.11.2026"),
    "after noon": ("2026-11-23", time(14, 0), None, "23.11.2026", "2026-11-23",
                   "novaCache_London_23.11.2026"),
    # 07:30 on 23.11 in Singapore, the browser's zone here: still 22.11 in London.
    "late evening": ("2026-11-22", time(23, 30), None, "22.11.2026", "2026-11-22",
                     "novaCache_London_22.11.2026"),
    "sim before noon": ("2026-11-23", time(11, 36), "2026-12-01", "01.12.2026", "2026-12-01",
                        "novaCache_London_2026-12-01"),
}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_header_shows_calendar_date_and_cache_key_stays_per_night(client, db_session, tmp_path):
    user = db_session.query(DbUser).filter_by(username="default").one()
    name, lat, lon, tz_name = LONDON
    db_session.add(Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz_name))
    db_session.commit()
    astro_context_cache.clear()
    inputs = client.get('/api/desktop_inputs', query_string={"location": name}).get_json()
    tz = pytz.timezone(tz_name)

    cases = [{"id": cid, "opts": {
        "inputs": inputs, "simDate": sim,
        "nowMs": _ms(tz.localize(datetime.combine(date.fromisoformat(day), clock)))}}
        for cid, (day, clock, sim, *_rest) in CASES.items()]
    (tmp_path / "driver.cjs").write_text(_DRIVER)
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(tmp_path / "driver.cjs"), *[str(STATIC_JS / f) for f in ENGINE_FILES],
         str(STATIC_JS / "dashboard.js"), str(STATIC_JS / "utils.js"),
         str(tmp_path / "cases.json"), str(tmp_path / "out.json")],
        capture_output=True, text=True, timeout=60, env={"TZ": "Asia/Singapore"})
    assert run.returncode == 0, run.stderr
    out = json.loads((tmp_path / "out.json").read_text())

    assert out["tzOffsetMin"] == -480, "node did not run in Asia/Singapore"
    # Fallback before the first render, unchanged: an ISO date from the browser clock.
    assert out["keyBeforeRender"].startswith("novaCache_London_")
    for cid, (_, clock, sim, header, night, key) in CASES.items():
        got = out["cases"][cid]
        assert got["strip"]["date"] == night, cid
        assert got["strip"]["local_date"] == (sim or header_iso(header)), cid
        assert got["strip"]["time"] == clock.strftime("%H:%M"), cid
        assert got["header"] == header, cid
        assert got["key"] == key, cid
        assert got["key"] == got["oldKey"], cid


def header_iso(european):
    d, m, y = european.split(".")
    return f"{y}-{m}-{d}"
