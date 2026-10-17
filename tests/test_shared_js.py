"""Drift guards for the JavaScript Nova shares with nova-ios.

static/js/nova_astro.js and static/js/nova_transit.js are developed in the nova-ios
repo (src/*.js) and copied in here byte-identical. Nothing in this repo may edit them:
a change lands in nova-ios first, is copied across unchanged, and the pinned digest
below moves in the same breath. The first two tests are that contract.

nova_transit.js is a port of calculate_transit_time() in modules/astro_calculations.py,
so the third test pins the two together: the transit it reports for a spread of the
shipped catalogue must be the string the server computes for the same night, or the
desktop chart and the rest of Nova would disagree about the same object.

No page loads nova_transit.js yet; these tests are the only thing reading it.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from modules.astro_calculations import calculate_transit_time

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
CONFIG_DEFAULT = REPO / "config_templates" / "config_default.yaml"

# The bytes of the shared copies, as they sit in nova-ios today.
SHARED_WITH_NOVA_IOS = {
    "nova_astro.js": "c6c70e9d667255160d006779f4e9a5f80656a67357ad9afcd34fd93367722b2c",
    "nova_transit.js": "b32ed7b19bd642727d590d2f468a3648da337890dd96919ad8a3004edece2049",
}

DRIFT_HELP = (
    "These files are shared with nova-ios; change them there first, copy them here "
    "byte-identical, then update the constant."
)

# Every 17th object of the shipped catalogue sorted by right ascension: 20 objects
# spread across the whole sky, rather than the first twenty alphabetically.
OBJECT_STRIDE = 17
OBJECTS_WANTED = 20

# July puts Europe/London on BST, the DST night nova_transit.js's header warns about;
# January is the plain case.
NIGHTS = ("2026-01-15", "2026-07-15")

# Loads the two files the way the page does — plain scripts in one context, so
# nova_transit.js finds the NovaAstro global the page gives it — and writes the
# display string each case produces.
_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [astroFile, transitFile, inFile, outFile] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of [astroFile, transitFile]) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => ({
    id: c.id,
    display: context.NovaTransit.transit(c.ra, c.dec, c.lat, c.lon, c.tz, c.date).display,
}))));
"""

NODE = shutil.which("node")


# --- the sharing contract ----------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SHARED_WITH_NOVA_IOS))
def test_shared_js_is_the_nova_ios_copy(name):
    path = STATIC_JS / name
    assert path.is_file(), f"{path} is missing.\n{DRIFT_HELP}"

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == SHARED_WITH_NOVA_IOS[name], (
        f"{name} is not the copy nova-ios ships.\n"
        f"  expected sha256 {SHARED_WITH_NOVA_IOS[name]}\n"
        f"  actual   sha256 {digest}\n"
        f"{DRIFT_HELP}"
    )


# --- the port against the function it ports ----------------------------------------------

def _template():
    return yaml.safe_load(CONFIG_DEFAULT.read_text())


def _objects(template):
    """Every 17th object of the shipped catalogue, in right ascension order."""
    objects = sorted(template["objects"], key=lambda o: o["RA (hours)"])
    return objects[::OBJECT_STRIDE][:OBJECTS_WANTED]


def _sites(template):
    """The template's northern, southern and equatorial sites, by latitude."""
    sites = template["locations"]
    north = max(sites.items(), key=lambda kv: kv[1]["lat"])
    south = min(sites.items(), key=lambda kv: kv[1]["lat"])
    equator = min(sites.items(), key=lambda kv: abs(kv[1]["lat"]))
    assert len({north[0], south[0], equator[0]}) == 3, (
        "config_default.yaml no longer has a northern, a southern and an equatorial site"
    )
    return [north, south, equator]


def _as_minutes(hhmm):
    """'HH:MM' as minutes since midnight, or None when the string is not one."""
    parts = hhmm.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _agrees_within_a_minute(server, browser):
    """Nova's own pass mark: the same number of times, each within a printed minute.

    nova-ios's tools/compare_transit.cjs allows this much because the engine's apparent
    place differs from ephem's by about an arcsecond, which is enough to move a crossing
    across a minute boundary. 'N/A' is not one of those cases: it must match exactly.
    """
    if server == "N/A" or browser == "N/A":
        return False
    server_times = server.split(", ")
    browser_times = browser.split(", ")
    if len(server_times) != len(browser_times):
        return False
    for expected, actual in zip(server_times, browser_times):
        a, b = _as_minutes(expected), _as_minutes(actual)
        if a is None or b is None:
            return False
        gap = abs(a - b)
        if min(gap, 1440 - gap) > 1:  # the wrap: 23:59 and 00:00 are one minute apart
            return False
    return True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_transit_agrees_with_calculate_transit_time(tmp_path):
    template = _template()
    cases = [
        {
            "id": f"{obj['Object']} @ {name} {night}",
            "ra": obj["RA (hours)"],
            "dec": obj["DEC (degrees)"],
            "lat": site["lat"],
            "lon": site["lon"],
            "tz": site["timezone"],
            "date": night,
        }
        for obj in _objects(template)
        for name, site in _sites(template)
        for night in NIGHTS
    ]
    assert len(cases) == OBJECTS_WANTED * 3 * len(NIGHTS)

    driver = tmp_path / "transit_driver.js"
    driver.write_text(_DRIVER)
    cases_file = tmp_path / "cases.json"
    results_file = tmp_path / "results.json"
    cases_file.write_text(json.dumps(cases))

    run = subprocess.run(
        [NODE, str(driver), str(STATIC_JS / "nova_astro.js"),
         str(STATIC_JS / "nova_transit.js"), str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"

    got = {r["id"]: r["display"] for r in json.loads(results_file.read_text())}
    assert len(got) == len(cases), "the browser returned a different number of cases"

    within_a_minute = 0
    mismatches = []
    for case in cases:
        expected = calculate_transit_time(
            case["ra"], case["dec"], case["lat"], case["lon"], case["tz"], case["date"]
        )
        actual = got[case["id"]]
        if actual == expected:
            continue
        if _agrees_within_a_minute(expected, actual):
            within_a_minute += 1
            continue
        mismatches.append(f"{case['id']}: server {expected!r}, browser {actual!r}")

    assert not mismatches, (
        f"{len(mismatches)} of {len(cases)} transits differ between nova_transit.js and "
        f"calculate_transit_time ({within_a_minute} differed by a printed minute only, "
        f"which is allowed):\n  " + "\n  ".join(mismatches[:20])
    )
