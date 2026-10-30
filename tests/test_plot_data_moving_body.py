"""static/js/nova_plot_data.js with a moving body (the Solar System page's chart).

build() and range() take an optional altAzAt(utcMs) -> {alt, az} in place of a fixed
RA/Dec. The Moon is the check: nova_plot_data.js draws its own Moon series from
NovaAstro.moonAltAz(), and NovaPlanets' Moon is NovaAstro's Moon throughout, so
the object series fed with NovaPlanets.altAz('moon', ...) must be the Moon series,
sample for sample. Without altAzAt nothing changes; tests/test_plot_data_parity.py
pins that path against the endpoints.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

FILES = ("nova_astro.js", "nova_transit.js", "nova_planets.js", "nova_plot_data.js")
# The same engine on both sides, so only float noise may differ.
TOLERANCE_DEG = 1e-9

SITE = {"lat": 51.4769, "lon": -0.0005, "tz": "Europe/London"}

_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [outFile, ...files] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
vm.runInContext(`
    const site = %SITE%;
    const moon = t => NovaPlanets.altAz('moon', t, site.lat, site.lon);
    const day = NovaPlotData.build(Object.assign({
        date: '2026-07-15', threshold: 20, interval: 15, mask: [], skyglow: null, altAzAt: moon
    }, site));
    const month = NovaPlotData.range(Object.assign({ year: 2026, month: 3, altAzAt: moon }, site));
    var result = { day: day, month: month };
`, context);
fs.writeFileSync(outFile, JSON.stringify(context.result));
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def moon_payloads(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("moving_body")
    driver = tmp / "driver.js"
    driver.write_text(_DRIVER.replace("%SITE%", json.dumps(SITE)))
    out = tmp / "out.json"
    run = subprocess.run(
        [NODE, str(driver), str(out)] + [str(STATIC_JS / name) for name in FILES],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def _same_series(name, got, want):
    assert len(got) == len(want), name
    for k, (a, b) in enumerate(zip(got, want)):
        if a is None or b is None:
            assert a is None and b is None, f"{name}[{k}]: {a!r} vs {b!r}"
            continue
        assert abs(a - b) <= TOLERANCE_DEG, f"{name}[{k}]: {a} vs {b}"


def test_build_with_the_moon_as_the_body_is_the_moon_series(moon_payloads):
    day = moon_payloads["day"]
    assert len(day["times"]) > 90  # 96 samples plus the two sentinels
    _same_series("object_alt", day["object_alt"], day["moon_alt"])
    _same_series("object_az", day["object_az"], day["moon_az"])
    assert day["transit_time"] is None, "a fixed-star transit makes no sense for a moving body"
    assert any(a is not None and a > 0 for a in day["object_alt"]), "the Moon should rise that night"


def test_range_with_the_moon_as_the_body_is_the_moon_series(moon_payloads):
    month = moon_payloads["month"]
    assert len(month["dates"]) == 31
    _same_series("object_alt", month["object_alt"], month["moon_alt"])


_BAD_TRACK_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [outFile, ...files] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
vm.runInContext(`
    const site = %SITE%;
    const tracks = {
        nanAlt: t => ({ alt: NaN, az: 120 }),
        nanAz: t => ({ alt: 10, az: NaN }),
        missing: t => ({}),
        infinite: t => ({ alt: Infinity, az: 0 }),
    };
    const tryIt = f => { try { f(); return null; } catch (e) { return String(e.message); } };
    var result = {};
    for (const name of Object.keys(tracks)) {
        result[name] = {
            build: tryIt(() => NovaPlotData.build(Object.assign({
                date: '2026-07-15', threshold: 20, interval: 15, mask: [], skyglow: null,
                altAzAt: tracks[name] }, site))),
            range: tryIt(() => NovaPlotData.range(Object.assign({
                year: 2026, month: 3, altAzAt: tracks[name] }, site))),
        };
    }
`, context);
fs.writeFileSync(outFile, JSON.stringify(context.result));
"""


def test_non_finite_track_throws_a_clear_error(tmp_path):
    """A moving body's altAzAt answering a non-finite alt or az is an error in build()
    and range(), naming altAzAt, rather than NaN reaching the chart."""
    driver = tmp_path / "driver.js"
    driver.write_text(_BAD_TRACK_DRIVER.replace("%SITE%", json.dumps(SITE)))
    out = tmp_path / "out.json"
    run = subprocess.run(
        [NODE, str(driver), str(out)] + [str(STATIC_JS / name) for name in FILES],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    got = json.loads(out.read_text())
    assert set(got) == {"nanAlt", "nanAz", "missing", "infinite"}
    for name, errors in got.items():
        for fn in ("build", "range"):
            assert errors[fn] is not None, f"{name}: {fn}() did not throw"
            assert "altAzAt returned a non-finite alt/az" in errors[fn], f"{name}: {fn}(): {errors[fn]}"
