"""Checks for static/js/nova_solar_rigfit.js, the Solar System page's Rig fit card.

The module runs in node as the page loads it, a plain script defining NovaSolarRigFit.
Every formula is checked against numbers worked out by hand (below each case), and the
rig metrics against the server's _compute_rig_metrics_from_components, so the card
agrees with what Nova shows for the same rig elsewhere.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nova.helpers import _compute_rig_metrics_from_components

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "static" / "js" / "nova_solar_rigfit.js"
NODE = shutil.which("node")

_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');
const [moduleFile, bodyFile, inFile, outFile] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(moduleFile, 'utf8'), context, { filename: moduleFile });
context.input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
vm.runInContext(fs.readFileSync(bodyFile, 'utf8'), context, { filename: 'case.js' });
fs.writeFileSync(outFile, JSON.stringify(context.result));
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, body, payload):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "case.js"
    case.write_text(body)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(payload))
    out = tmp_path / "out.json"
    run = subprocess.run([NODE, str(driver), str(MODULE), str(case), str(inp), str(out)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def _fit(tmp_path, rig, body, diameter):
    return _run(tmp_path, "result = NovaSolarRigFit.fit(input.rig, input.body, input.d);",
                {"rig": rig, "body": body, "d": diameter})


def _rig(**fields):
    """A complete rig record as core.py _solar_rigs embeds it, with overrides."""
    rig = {"name": "Test rig", "has_telescope": True, "has_camera": True,
           "focal_length_mm": 2000.0, "aperture_mm": 200.0, "factor": None,
           "pixel_size_um": 2.9, "sensor_width_mm": 5.6, "sensor_height_mm": 3.2}
    rig.update(fields)
    return rig


def test_image_scale_and_disc_pixels(tmp_path):
    """206.265 x 2.9 / 2000 = 0.29908 "/px, so Jupiter at 45" is 150.46 px; a 2x
    extender doubles the focal length and the disc, 300.92 px."""
    got = _fit(tmp_path, _rig(), "jupiter", 45.0)
    assert got["disc"] == {"ok": True, "px": pytest.approx(150.4593, abs=1e-3), "ringsPx": None}
    assert got["fRatio"] == {"ok": True, "value": pytest.approx(10.0)}
    assert got["ideal"] == {"ok": True, "value": pytest.approx(14.5)}
    doubled = _fit(tmp_path, _rig(factor=2.0), "jupiter", 45.0)
    assert doubled["disc"]["px"] == pytest.approx(300.9186, abs=1e-3)
    assert doubled["fRatio"]["value"] == pytest.approx(20.0)


def test_saturn_disc_shows_the_rings(tmp_path):
    """Outer ring diameter = 136775 / 60268 = 2.2694 x the globe: Saturn's 18" globe at
    2800 mm with 2.9 µm pixels (0.213632 "/px) is 84.257 px, its rings 191.217 px."""
    got = _fit(tmp_path, _rig(focal_length_mm=2800.0), "saturn", 18.0)
    assert got["disc"]["px"] == pytest.approx(84.2572, abs=1e-3)
    assert got["disc"]["ringsPx"] == pytest.approx(191.2172, abs=1e-3)


@pytest.mark.parametrize("f_ratio, pixel, kind, factor", [
    (10, 2.9, "factor", 1.5),     # ideal 14.5: k 1.45, nearest 1.5
    (5, 2.9, "factor", 3),        # k 2.9: 3 (log distance 0.034) beats 2.5 (0.148)
    (6, 3.75, "factor", 3),       # 18.75 / 6 = 3.125: 3 beats 4
    (4, 3.76, "factor", 5),       # 18.8 / 4 = 4.7: 5 (0.062) beats 4 (0.161)
    (2, 3.76, "factor", 5),       # k 9.4: capped at the largest, 5
    (10, 2.3, "factor", 1.5),     # k 1.15, just past the 10 % band
    (10, 2.0, "none", None),      # k 1.0
    (10, 2.1, "none", None),      # k 1.05, inside the band
    (20, 2.9, "above", None),     # k 0.725: already slower than ideal
])
def test_ideal_ratio_and_barlow(tmp_path, f_ratio, pixel, kind, factor):
    got = _fit(tmp_path, _rig(focal_length_mm=200.0 * f_ratio, pixel_size_um=pixel), "jupiter", 45.0)
    exact = 5 * pixel / f_ratio
    assert got["barlow"]["ok"] and got["barlow"]["kind"] == kind
    assert got["barlow"]["exact"] == pytest.approx(exact)
    assert got["barlow"].get("factor") == factor


@pytest.mark.parametrize("body, aperture, diameter, seconds", [
    ("jupiter", 200.0, 45.0, 146.588),   # 0.58" x 35730 s / (pi x 45") = 2 min 27 s (~2.4 min)
    ("saturn", 200.0, 18.0, 389.917),    # 0.58" x 38016 s / (pi x 18") = 6 min 30 s
    ("mars", 100.0, 20.0, 1636.32),      # 1.16" x 88632 s / (pi x 20") = 27 min 16 s
])
def test_rotation_limit(tmp_path, body, aperture, diameter, seconds):
    """t = (116 / aperture) x P / (pi x diameter), P = 9.925 h, 10.56 h, 24.62 h."""
    got = _fit(tmp_path, _rig(aperture_mm=aperture), body, diameter)
    assert got["rotation"] == {"ok": True, "seconds": pytest.approx(seconds, abs=0.01)}
    assert got["mosaic"] is None


@pytest.mark.parametrize("diameter, fov_w, fov_h, cols, rows", [
    (1860, 60, 40, 1, 1),        # 31' inside both sides
    (1800, 30, 30, 1, 1),        # 30' on a 30' side: exactly fits
    (1860, 20, 15, 2, 3),        # 1 + ceil(11 / 16) = 2, 1 + ceil(16 / 12) = 3
    (1980, 30, 20, 2, 2),        # 33': 1 + ceil(3 / 24) = 2, 1 + ceil(13 / 16) = 2
    (1860, 15, 20, 3, 2),        # turned: the same count
])
def test_mosaic_panels(tmp_path, diameter, fov_w, fov_h, cols, rows):
    """1 + ceil((d - F) / (0.8 F)) frames per axis, d and F in arcminutes."""
    got = _run(tmp_path, "result = NovaSolarRigFit.mosaicPanels(input.d, input.w, input.h, 0.2);",
               {"d": diameter, "w": fov_w, "h": fov_h})
    assert got == {"cols": cols, "rows": rows, "panels": cols * rows}


def test_moon_mosaic_from_the_rigs_field(tmp_path):
    """An ASI462 (5.6 x 3.2 mm) at 1500 mm sees 12.83' x 7.33': the 31' Moon takes
    1 + ceil(18.17 / 10.27) = 3 by 1 + ceil(23.67 / 5.87) = 6 frames. An APS-C sensor
    (23.5 x 15.7 mm) at 1000 mm, 80.78' x 53.97', holds it in one."""
    small = _fit(tmp_path, _rig(focal_length_mm=1500.0), "moon", 1860.0)
    assert small["mosaic"] == {"ok": True, "cols": 3, "rows": 6, "panels": 18}
    assert small["rotation"] is None
    big = _fit(tmp_path, _rig(focal_length_mm=1000.0, sensor_width_mm=23.5, sensor_height_mm=15.7), "moon", 1860.0)
    assert big["mosaic"]["panels"] == 1


@pytest.mark.parametrize("fields, body, expected", [
    ({"has_telescope": False}, "jupiter",
     {"disc": "noTelescope", "fRatio": "noTelescope", "ideal": None, "barlow": "noTelescope", "rotation": "noTelescope"}),
    ({"has_camera": False}, "moon",
     {"disc": "noCamera", "fRatio": None, "ideal": "noCamera", "barlow": "noCamera", "mosaic": "noCamera"}),
    ({"focal_length_mm": None}, "jupiter",
     {"disc": "focalLength", "fRatio": "focalLength", "ideal": None, "barlow": "focalLength", "rotation": None}),
    # A missing aperture costs the focal ratio, Barlow and rotation limit only; the
    # server's helper would drop the image scale too.
    ({"aperture_mm": None}, "saturn",
     {"disc": None, "fRatio": "aperture", "ideal": None, "barlow": "aperture", "rotation": "aperture"}),
    ({"pixel_size_um": None}, "mars",
     {"disc": "pixelSize", "fRatio": None, "ideal": "pixelSize", "barlow": "pixelSize", "rotation": None}),
    ({"sensor_height_mm": None}, "moon",
     {"disc": None, "fRatio": None, "ideal": None, "barlow": None, "mosaic": "sensorSize"}),
])
def test_missing_fields_name_the_field(tmp_path, fields, body, expected):
    """Each value is missing only for its own inputs, and says which field it lacks
    (None: the value is there)."""
    got = _fit(tmp_path, _rig(**fields), body, 1860.0 if body == "moon" else 20.0)
    for key, code in expected.items():
        if code is None:
            assert got[key]["ok"] is True, key
        else:
            assert got[key] == {"ok": False, "missing": code}, key


def test_extra_values_per_body(tmp_path):
    """Rotation limit for Jupiter, Saturn and Mars only; mosaic for the Moon only; the
    ring width for Saturn only."""
    got = _run(tmp_path, """
        const rig = input.rig;
        result = ['mercury', 'venus', 'mars', 'jupiter', 'saturn', 'uranus', 'neptune', 'moon'].map(b => {
            const f = NovaSolarRigFit.fit(rig, b, b === 'moon' ? 1860 : 20);
            return [b, f.rotation !== null, f.mosaic !== null, f.disc.ringsPx !== null];
        });
    """, {"rig": _rig()})
    assert got == [
        ["mercury", False, False, False], ["venus", False, False, False],
        ["mars", True, False, False], ["jupiter", True, False, False],
        ["saturn", True, False, True], ["uranus", False, False, False],
        ["neptune", False, False, False], ["moon", False, True, False],
    ]


@pytest.mark.parametrize("tel, cam, factor", [
    ((2800.0, 280.0), (2.9, 5.6, 3.2), None),
    ((550.0, 100.0), (3.76, 23.5, 15.7), 0.8),
    ((1000.0, 127.0), (2.4, 11.1, 6.3), 2.5),
])
def test_metrics_match_the_server(tmp_path, tel, cam, factor):
    """Effective focal length, focal ratio, image scale and width of field as
    _compute_rig_metrics_from_components computes them, and the height of field as
    /get_rig_data does."""
    fl, ap = tel
    px, sw, sh = cam
    got = _run(tmp_path, "result = NovaSolarRigFit.metrics(input.rig);",
               {"rig": _rig(focal_length_mm=fl, aperture_mm=ap, factor=factor,
                            pixel_size_um=px, sensor_width_mm=sw, sensor_height_mm=sh)})
    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(
        SimpleNamespace(name="t", focal_length_mm=fl, aperture_mm=ap),
        SimpleNamespace(name="c", pixel_size_um=px, sensor_width_mm=sw, sensor_height_mm=sh),
        SimpleNamespace(factor=factor) if factor is not None else None)
    fov_h = math.degrees(2 * math.atan((sh / 2.0) / efl)) * 60.0
    assert got["efl"] == pytest.approx(efl, rel=1e-12)
    assert got["fRatio"] == pytest.approx(f_ratio, rel=1e-12)
    assert got["scale"] == pytest.approx(scale, rel=1e-12)
    assert got["fovW"] == pytest.approx(fov_w, rel=1e-12)
    assert got["fovH"] == pytest.approx(fov_h, rel=1e-12)
