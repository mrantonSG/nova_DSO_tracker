"""Checks for the Framing Assistant's survey choice in static/js/framing-utils.js, in node.

framing-utils.js runs as a plain script in a vm context whose `window` is an empty
object, the way the page loads it; the case table goes in as JSON and each case's
result comes back the same way.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UTILS = REPO / "static" / "js" / "framing-utils.js"
CHART = REPO / "static" / "js" / "graph_view_chart.js"
GRAPH_VIEW_HTML = REPO / "templates" / "graph_view.html"
NODE = shutil.which("node")

DSS = "P/DSS2/color"
NSNS = "https://www.simg.de/nebulae3/dr0_2/rgb8"
NSNS_OHS = "https://www.simg.de/nebulae3/dr0_2/ohs8"
ESA = "https://skies.esac.esa.int/DSSColor"
CASDA = "https://casda.csiro.au/hips/DSS2/color"

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const [utilsFile, inFile, outFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));

const ctx = { window: {}, console, URL };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(utilsFile, 'utf8'), ctx, { filename: utilsFile });
const fu = ctx.window.framingUtils;

const out = {
    fallback: fu.SURVEY_FALLBACK,
    mirrors: fu.DSS_MIRRORS,
    choose: input.choose.map(c => fu.chooseBaseSurvey(c)),
    isCds: input.isCds.map(u => fu.isCdsUrl(u)),
};
fs.writeFileSync(outFile, JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run_utils(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("framing_survey")
    driver = tmp / "driver.cjs"
    driver.write_text(_DRIVER, encoding="utf-8")

    def run(choose=(), is_cds=()):
        inp, out = tmp / "in.json", tmp / "out.json"
        inp.write_text(json.dumps({"choose": list(choose), "isCds": list(is_cds)}), encoding="utf-8")
        proc = subprocess.run([NODE, str(driver), str(UTILS), str(inp), str(out)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(out.read_text(encoding="utf-8"))

    return run


def _case(requested=DSS, *, on_cds=True, up=True, dec=40.0, fov=1.0, known=True, mirror=None):
    return {"requested": requested, "requestedOnCds": on_cds, "cdsReachable": up,
            "decDeg": dec, "fovDeg": fov, "known": known, "dssMirror": mirror}


def _choose(run_utils, *cases):
    return run_utils(choose=cases)["choose"]


def test_constants(run_utils):
    assert run_utils()["fallback"] == {"DEFAULT_ID": DSS, "NSNS_URL": NSNS, "NSNS_MIN_DEC_DEG": -16}


def test_cds_reachable_keeps_the_requested_survey(run_utils):
    assert _choose(run_utils,
                   _case(DSS, dec=40),
                   _case("P/2MASS/color", dec=-60),
                   _case(DSS, dec=None)) == [{"survey": DSS, "notice": False},
                                             {"survey": "P/2MASS/color", "notice": False},
                                             {"survey": DSS, "notice": False}]


def test_cds_down_north_falls_back_to_nsns(run_utils):
    assert _choose(run_utils, _case(DSS, up=False, dec=40, fov=1)) == [{"survey": NSNS, "notice": True}]


def test_cds_down_south_keeps_the_requested_survey_with_notice(run_utils):
    assert _choose(run_utils, _case(DSS, up=False, dec=-40, fov=1)) == [{"survey": DSS, "notice": True}]


def test_boundary_is_the_frame_edge_at_minus_16(run_utils):
    """Dec - FOV/2 >= -16 is north: exactly -16 included, anything below is south."""
    res = _choose(run_utils,
                  _case(up=False, dec=-15, fov=2),        # edge exactly -16
                  _case(up=False, dec=-15, fov=2.02),     # edge -16.01
                  _case(up=False, dec=-16, fov=0),        # no FOV: the centre decides
                  _case(up=False, dec=-16.001, fov=0),
                  _case(up=False, dec=-10, fov=12),       # edge exactly -16, wide field
                  _case(up=False, dec=-10, fov=12.1))
    assert [r["survey"] for r in res] == [NSNS, DSS, NSNS, DSS, NSNS, DSS]
    assert all(r["notice"] for r in res)


def test_missing_dec_is_south(run_utils):
    res = _choose(run_utils,
                  _case(up=False, dec=None),
                  {k: v for k, v in _case(up=False).items() if k != "decDeg"},
                  _case(up=False, dec=""),
                  _case(up=False, dec="not a number"))
    assert res == [{"survey": DSS, "notice": True}] * 4


def test_missing_or_bad_fov_counts_as_zero(run_utils):
    res = _choose(run_utils,
                  _case(up=False, dec=-15.9, fov=None),
                  _case(up=False, dec=-15.9, fov=-3),
                  _case(up=False, dec=-16.1, fov=None))
    assert [r["survey"] for r in res] == [NSNS, NSNS, DSS]


def test_nsns_requested_while_cds_down(run_utils):
    """NSNS is not on CDS: it is shown as requested, north or south, with no notice."""
    res = _choose(run_utils,
                  _case(NSNS_OHS, on_cds=False, up=False, dec=50),
                  _case(NSNS_OHS, on_cds=False, up=False, dec=-50),
                  _case(NSNS, on_cds=False, up=False, dec=None))
    assert res == [{"survey": NSNS_OHS, "notice": False},
                   {"survey": NSNS_OHS, "notice": False},
                   {"survey": NSNS, "notice": False}]


def test_unknown_id_uses_the_default_with_notice(run_utils):
    """An ID Nova has no URL for is never sent to the MocServer: the default stands in."""
    res = _choose(run_utils,
                  _case("P/Some/legacy", on_cds=False, known=False, up=True, dec=40),
                  _case("P/Some/legacy", on_cds=False, known=False, up=False, dec=40),
                  _case("P/Some/legacy", on_cds=False, known=False, up=False, dec=-40))
    assert res == [{"survey": DSS, "notice": True},
                   {"survey": NSNS, "notice": True},
                   {"survey": DSS, "notice": True}]


def test_dss_mirror_list(run_utils):
    """ESA first, CASDA second; neither is a CDS host (so neither is itself skipped)."""
    res = run_utils(is_cds=[ESA, CASDA])
    assert res["mirrors"] == [ESA, CASDA]
    assert res["isCds"] == [False, False]


def test_mirror_answered_replaces_dss_north_and_south(run_utils):
    res = _choose(run_utils,
                  _case(DSS, up=False, dec=40, mirror=ESA),
                  _case(DSS, up=False, dec=-40, mirror=ESA),
                  _case(DSS, up=False, dec=None, mirror=CASDA))
    assert res == [{"survey": ESA, "notice": True},
                   {"survey": ESA, "notice": True},
                   {"survey": CASDA, "notice": True}]


def test_no_mirror_answered_falls_back_as_before(run_utils):
    """No mirror (null or empty): NSNS in the north, the requested survey in the south."""
    res = _choose(run_utils,
                  _case(DSS, up=False, dec=40, mirror=None),
                  _case(DSS, up=False, dec=-40, mirror=None),
                  _case(DSS, up=False, dec=40, mirror=""))
    assert res == [{"survey": NSNS, "notice": True},
                   {"survey": DSS, "notice": True},
                   {"survey": NSNS, "notice": True}]


def test_other_cds_surveys_take_the_dss_mirror(run_utils):
    """DSS is the closer substitute for 2MASS, WISE, ... than NSNS, north or south."""
    res = _choose(run_utils,
                  _case("P/2MASS/color", up=False, dec=40, mirror=ESA),
                  _case("P/allWISE/color", up=False, dec=-40, mirror=CASDA))
    assert res == [{"survey": ESA, "notice": True},
                   {"survey": CASDA, "notice": True}]


def test_unknown_id_with_mirror_uses_the_mirror(run_utils):
    res = _choose(run_utils,
                  _case("P/Some/legacy", on_cds=False, known=False, up=False, dec=-40, mirror=ESA),
                  _case("P/Some/legacy", on_cds=False, known=False, up=True, dec=-40, mirror=ESA))
    assert res == [{"survey": ESA, "notice": True},
                   {"survey": DSS, "notice": True}]


def test_mirror_ignored_when_not_needed(run_utils):
    """CDS up: the requested survey, mirror or not. NSNS requested while CDS is down: NSNS."""
    res = _choose(run_utils,
                  _case(DSS, up=True, dec=-40, mirror=ESA),
                  _case("P/2MASS/color", up=True, dec=40, mirror=CASDA),
                  _case(NSNS_OHS, on_cds=False, up=False, dec=50, mirror=ESA),
                  _case(NSNS, on_cds=False, up=False, dec=-50, mirror=CASDA))
    assert res == [{"survey": DSS, "notice": False},
                   {"survey": "P/2MASS/color", "notice": False},
                   {"survey": NSNS_OHS, "notice": False},
                   {"survey": NSNS, "notice": False}]


def test_is_cds_url(run_utils):
    urls = ["https://alasky.cds.unistra.fr/DSS/DSSColor",
            "https://alaskybis.cds.unistra.fr/2MASS/Color",
            "https://alasky.unistra.fr/MocServer/query",
            "https://axel.u-strasbg.fr/HiPSCatService/SIMBAD",
            NSNS,
            "https://evilunistra.fr/x",
            "P/DSS2/color",
            ""]
    assert run_utils(is_cds=urls)["isCds"] == [True, True, True, True, False, False, False, False]


def test_every_mapped_survey_is_on_cds_and_nsns_options_are_not(run_utils):
    """The page's tables agree with isCdsUrl: every HIPS_ID_TO_URL target is a CDS URL
    (so the outage fallback covers it), every NSNS option is not, and the fallback URL
    is one of the NSNS options (so the credit and layer name resolve)."""
    chart = CHART.read_text(encoding="utf-8")
    table = re.search(r"HIPS_ID_TO_URL = Object\.freeze\(\{(.*?)\}\);", chart, re.S).group(1)
    mapped = re.findall(r'"(https?://[^"]+)"', table)
    html = GRAPH_VIEW_HTML.read_text(encoding="utf-8")
    nsns_options = sorted(set(re.findall(r'<option value="(https://www\.simg\.de/[^"]+)"', html)))
    assert mapped and nsns_options
    flags = run_utils(is_cds=mapped + nsns_options)["isCds"]
    assert flags == [True] * len(mapped) + [False] * len(nsns_options)
    assert NSNS in nsns_options
