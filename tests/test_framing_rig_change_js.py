"""A rig change keeps the frame centre: it re-fits the zoom to the new FOV and goes nowhere.

The real applyRigChangeKeepingCentre is cut out of graph_view_chart.js and run in
node against a recording Aladin stand-in. Jumping back to the object is what
"Recenter to object" and the first open of the tab are for, so a rig change must
not call gotoRaDec or gotoObject, and the centre must be where it was. The rig
select's data-action in graph_view.js must reach this function, not
updateFramingChart(true), which does recentre.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHART_JS = REPO / "static" / "js" / "graph_view_chart.js"
VIEW_JS = REPO / "static" / "js" / "graph_view.js"
NODE = shutil.which("node")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [chartFile, outFile] = process.argv.slice(2);
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
    const hits = [...src.matchAll(new RegExp('function\\s+' + name + '\\s*\\(', 'g'))];
    if (hits.length !== 1) throw new Error(`${name}: ${hits.length} declarations`);
    return cut(src, hits[0].index);
}

const els = {
    'framing-rig-select': {
        selectedIndex: 1,
        options: [{ dataset: { fovw: '41.2', fovh: '27.5' } },
                  { dataset: { fovw: '120.0', fovh: '80.0' } }],
    },
    'framing-rotation': { valueAsNumber: 33, value: '33', title: '' },
    'rotation-value': { textContent: '' },
    'result-angle': { value: '' },
};
const calls = [];
const centre = { ra: 285.6, dec: 10.4 };     // where the user panned to, not the object
const ctx = {
    Number, Math, parseFloat, String,
    document: { getElementById: id => els[id] || null },
    window: { t: (k, p) => k + ':' + (p ? p.angle : '') },
    aladin: {
        gotoRaDec: (...a) => calls.push(['gotoRaDec', ...a]),
        gotoObject: (...a) => calls.push(['gotoObject', ...a]),
        setFov: (...a) => calls.push(['setFov', ...a]),
        getRaDec: () => [centre.ra, centre.dec],
    },
    applyRigFovZoom: (...a) => calls.push(['applyRigFovZoom', ...a]),
    showFrameOverlay: () => calls.push(['showFrameOverlay']),
    updateResultDiffers: () => calls.push(['updateResultDiffers']),
};
vm.createContext(ctx);
const run = vm.runInContext(`${fn('applyRigChangeKeepingCentre')}; applyRigChangeKeepingCentre`, ctx);
run();
fs.writeFileSync(outFile, JSON.stringify({
    calls,
    centre: ctx.aladin.getRaDec(),
    angle: els['result-angle'].value,
    badge: els['rotation-value'].textContent,
}));
"""


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_rig_change_keeps_the_centre_and_goes_nowhere(tmp_path):
    driver = tmp_path / "driver.cjs"
    out = tmp_path / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(out.read_text())

    names = [c[0] for c in r["calls"]]
    assert "gotoRaDec" not in names and "gotoObject" not in names
    # The new rig's FOV, at the current rotation.
    assert ["applyRigFovZoom", 120.0, 80.0, 33] in r["calls"]
    assert "showFrameOverlay" in names and "updateResultDiffers" in names
    assert r["centre"] == [285.6, 10.4]
    # The angle readout is the slider's value, as before.
    assert r["angle"] == "33°" and r["badge"] == "33°"


def test_rig_select_is_wired_to_the_centre_keeping_path():
    src = VIEW_JS.read_text(encoding="utf-8")
    m = re.search(r"case 'update-framing-rig':(.*?)break;", src, re.S)
    assert m, "the rig select's data-action case is missing"
    block = m.group(1)
    assert "applyRigChangeKeepingCentre" in block
    assert "updateFramingChart(" not in block
