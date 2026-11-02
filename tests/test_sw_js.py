"""Checks for static/sw.js, the service worker, in node.

The worker runs in a vm context with a stand-in `self` that records its listeners, a
`fetch` the case controls and a one-entry cache for /m. Each case dispatches one fetch
event and reports whether the worker answered it (respondWith) and with what.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SW = REPO / "static" / "sw.js"
NODE = shutil.which("node")

ORIGIN = "http://localhost:5001"

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const [swFile, inFile, outFile] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));

const listeners = {};
const ctx = {
    console, URL, Response, Promise,
    self: {
        location: new URL(cases.origin + '/sw.js'),
        addEventListener: (type, fn) => { listeners[type] = fn; },
        skipWaiting: () => {},
        clients: { claim: () => {} },
    },
    caches: {
        open: async () => ({ addAll: async () => {} }),
        keys: async () => [],
        match: async (url) => (url === '/m' ? new Response('cached /m', { status: 200 }) : undefined),
    },
};
let networkUp = true;
ctx.fetch = async (req) => {
    if (!networkUp) throw new TypeError('Failed to fetch');
    return new Response('network ' + req.url, { status: 200 });
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(swFile, 'utf8'), ctx, { filename: swFile });

(async () => {
    const results = [];
    for (const c of cases.requests) {
        networkUp = c.networkUp;
        let answer = null;
        const request = { method: c.method, url: c.url, mode: c.mode || 'cors' };
        listeners.fetch({ request, respondWith: (p) => { answer = p; } });
        if (answer === null) { results.push({ responded: false }); continue; }
        const res = await answer;
        results.push({ responded: true, status: res.status, body: await res.text() });
    }
    fs.writeFileSync(outFile, JSON.stringify(results));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def run_sw(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("sw")
    driver = tmp / "driver.cjs"
    driver.write_text(_DRIVER, encoding="utf-8")

    def run(requests):
        inp, out = tmp / "in.json", tmp / "out.json"
        inp.write_text(json.dumps({"origin": ORIGIN, "requests": requests}), encoding="utf-8")
        proc = subprocess.run([NODE, str(driver), str(SW), str(inp), str(out)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(out.read_text(encoding="utf-8"))

    return run


def test_cross_origin_requests_are_not_answered(run_sw):
    """Survey and CDS requests go straight to the browser, up or down."""
    results = run_sw([
        {"method": "GET", "url": "https://alasky.cds.unistra.fr/DSS/DSSColor/properties", "networkUp": False},
        {"method": "GET", "url": "https://www.simg.de/nebulae3/dr0_2/rgb8/properties", "networkUp": True},
        {"method": "GET", "url": "http://localhost:5002/m", "networkUp": False, "mode": "navigate"},
    ])
    assert results == [{"responded": False}] * 3


def test_non_get_requests_are_not_answered(run_sw):
    results = run_sw([
        {"method": "POST", "url": ORIGIN + "/api/save_framing", "networkUp": True},
        {"method": "PUT", "url": ORIGIN + "/api/x", "networkUp": False},
        {"method": "DELETE", "url": ORIGIN + "/api/x", "networkUp": True},
    ])
    assert results == [{"responded": False}] * 3


def test_same_origin_get_unchanged(run_sw):
    """Network first; offline navigation gets the cached /m; other offline GETs a 503."""
    results = run_sw([
        {"method": "GET", "url": ORIGIN + "/static/css/mobile.css", "networkUp": True},
        {"method": "GET", "url": ORIGIN + "/m/objects", "networkUp": False, "mode": "navigate"},
        {"method": "GET", "url": ORIGIN + "/api/data", "networkUp": False},
    ])
    assert results[0] == {"responded": True, "status": 200, "body": "network " + ORIGIN + "/static/css/mobile.css"}
    assert results[1] == {"responded": True, "status": 200, "body": "cached /m"}
    assert results[2] == {"responded": True, "status": 503, "body": ""}
