"""Scan Frame marker labels, in node against a stand-in Aladin.

The Scan Frame layer's labels used to touch their circles, while the Nova
objects layer sets its labels off by two NBSPs. Both layers now share one padding
helper and one label font; only the colour tells them apart.

The padding must stay display-only: Scan Frame sources carry it in a separate
`label` field (the catalog's labelColumn), and `data.name` stays the clean name
that the popup title, Add to Database prefill, sameTargetName, createNewTarget
and the Wikipedia lookup read.
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
// The shared constants, cut from the real file (three declarations in a row).
function constants(source) {
    const start = source.indexOf('const MARKER_LABEL_PADDING');
    const end = source.indexOf('\n', source.indexOf('const markerLabel'));
    if (start < 0 || end < 0) throw new Error('marker label constants not found');
    return source.slice(start, end);
}

// Stand-in Aladin: catalogs record their options and the sources added to them.
const catalogs = [];
const ctx = {
    console: { warn: () => {}, log: () => {}, error: () => {} },
    window: { t: k => k },
    A: {
        catalog: opts => {
            const c = { opts, sources: [], removeAll() { this.sources = []; },
                        addSources(s) { this.sources.push(...s); } };
            catalogs.push(c);
            return c;
        },
        source: (ra, dec, data) => ({ ra, dec, data }),
    },
    aladin: { addCatalog() {} },
    document: {
        getElementById: id => (
            id === 'scan-min-size' ? { value: '0', disabled: true }
            : id === 'scan-catalog-names' ? { checked: false, disabled: true }
            : null),
    },
    fetch: () => Promise.resolve({ json: () => Promise.resolve({
        status: 'success',
        objects: [
            { name: 'NGC 7000', ra: 314.7, dec: 44.3, otype: 'HII', mag: 4.0, size_arcmin: 120, well_known: true },
            { name: 'IC 5070',  ra: 312.8, dec: 44.4, otype: 'HII', mag: 8.0, size_arcmin: 60,  well_known: true },
        ],
    }) }),
};
vm.createContext(ctx);

const api = vm.runInContext(`
    ${constants(src)}
    let scanFrameActive = false, scanResultsCatalog = null, scanResultsAll = [];
    let novaObjectsCatalog = null;
    function frameCenter() { return { ra: 1, dec: 2 }; }
    function setScanNotice() {}
    function setScanButtonBlocked() {}
    ${fn(src, 'applyMinSizeFilter')}
    ${fn(src, 'scanFrameForDSOs')}
    ${fn(src, 'addNovaObjectsCatalog')}
    ({ scanFrameForDSOs, addNovaObjectsCatalog })
`, ctx);

api.addNovaObjectsCatalog([{ object_name: 'M31', ra_deg: 10.68, dec_deg: 41.27 }]);
api.scanFrameForDSOs();

setTimeout(() => {
    const [nova, scan] = catalogs;
    fs.writeFileSync(outFile, JSON.stringify({
        nova: { opts: nova.opts, sources: nova.sources.map(s => s.data) },
        scan: { opts: scan.opts, sources: scan.sources.map(s => s.data) },
    }));
}, 50);
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("scan_label")
    driver, result = tmp / "driver.cjs", tmp / "out.json"
    driver.write_text(_DRIVER, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver), str(CHART_JS), str(result)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_scan_catalog_labels_from_the_padded_display_field(out):
    assert out["scan"]["opts"]["labelColumn"] == "label"


def test_scan_label_carries_the_padding_and_name_stays_clean(out):
    sources = out["scan"]["sources"]
    assert [s["name"] for s in sources] == ["NGC 7000", "IC 5070"]
    for s in sources:
        assert s["label"] == "  " + s["name"]
        assert " " not in s["name"], "the padding must not reach data.name"


def test_both_layers_pad_their_labels_the_same_way(out):
    """The Nova layer's label column carries the same two-NBSP prefix."""
    nova_label = out["nova"]["sources"][0][out["nova"]["opts"]["labelColumn"]]
    scan_label = out["scan"]["sources"][0][out["scan"]["opts"]["labelColumn"]]
    assert nova_label == "  M31"
    assert nova_label[:2] == scan_label[:2] == "  "


def test_both_layers_share_the_label_font_but_keep_their_own_colour(out):
    assert out["scan"]["opts"]["labelFont"] == out["nova"]["opts"]["labelFont"] == "16px sans-serif"
    assert out["scan"]["opts"]["labelColor"] == "#e8a84c"
    assert out["scan"]["opts"]["color"] == "#e8a84c"
    assert out["nova"]["opts"]["labelColor"] != out["scan"]["opts"]["labelColor"]
