"""Checks for static/js/nova_solar_rigfit.js, the Solar System page's planetary calculator.

The module runs in node as the page loads it, a plain script defining NovaSolarRigFit.
Every formula is checked against numbers worked out by hand (below each case), and the
metrics against the server's _compute_rig_metrics_from_components (the amplification in
the reducer factor's place), so the calculator agrees with what Nova shows for the same
telescope and camera elsewhere.
"""

import json
import math
import re
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


def _fit(tmp_path, tel, cam, body, diameter, amp=1):
    return _run(tmp_path, "result = NovaSolarRigFit.fit(input.tel, input.cam, input.amp, input.body, input.d);",
                {"tel": tel, "cam": cam, "amp": amp, "body": body, "d": diameter})


def _tel(**fields):
    """A telescope record as core.py _solar_equipment embeds it, with overrides."""
    tel = {"uid": "t1", "name": "Test scope", "focal_length_mm": 2000.0, "aperture_mm": 200.0}
    tel.update(fields)
    return tel


def _cam(**fields):
    """A camera record as core.py _solar_equipment embeds it, with overrides."""
    cam = {"uid": "c1", "name": "Test camera", "pixel_size_um": 2.9,
           "sensor_width_mm": 5.6, "sensor_height_mm": 3.2}
    cam.update(fields)
    return cam


def test_image_scale_and_disc_pixels(tmp_path):
    """206.265 x 2.9 / 2000 = 0.29908 "/px, so Jupiter at 45" is 150.46 px; a 2x
    amplification doubles the focal length and the disc, 300.92 px."""
    got = _fit(tmp_path, _tel(), _cam(), "jupiter", 45.0)
    assert got["disc"] == {"ok": True, "px": pytest.approx(150.4593, abs=1e-3), "ringsPx": None}
    assert got["fRatio"] == {"ok": True, "value": pytest.approx(10.0)}
    assert got["ideal"] == {"ok": True, "value": pytest.approx(14.5)}
    doubled = _fit(tmp_path, _tel(), _cam(), "jupiter", 45.0, amp=2)
    assert doubled["disc"]["px"] == pytest.approx(300.9186, abs=1e-3)
    assert doubled["fRatio"]["value"] == pytest.approx(20.0)


def test_saturn_disc_shows_the_rings(tmp_path):
    """Outer ring diameter = 136775 / 60268 = 2.2694 x the globe: Saturn's 18" globe at
    2800 mm with 2.9 µm pixels (0.213632 "/px) is 84.257 px, its rings 191.217 px."""
    got = _fit(tmp_path, _tel(focal_length_mm=2800.0), _cam(), "saturn", 18.0)
    assert got["disc"]["px"] == pytest.approx(84.2572, abs=1e-3)
    assert got["disc"]["ringsPx"] == pytest.approx(191.2172, abs=1e-3)


@pytest.mark.parametrize("f_ratio, ideal, status", [
    (14.5, 14.5, "onTarget"),
    (12.325, 14.5, "onTarget"),   # 0.85 x ideal: the band's lower edge
    (16.675, 14.5, "onTarget"),   # 1.15 x ideal: the upper edge
    (12.3, 14.5, "below"),        # just below 0.85 x
    (16.7, 14.5, "above"),        # just above 1.15 x
    (5.0, 14.5, "below"),
    (30.0, 14.5, "above"),
])
def test_focal_ratio_status(tmp_path, f_ratio, ideal, status):
    """On target within 15 % of the ideal focal ratio, else below or above it."""
    got = _run(tmp_path, "result = NovaSolarRigFit.ratioStatus(input.f, input.i);", {"f": f_ratio, "i": ideal})
    assert got == status


@pytest.mark.parametrize("diameter, aperture, verdict, elements", [
    (46.4, 100.0, "fine", 40.0),         # 46.4 / 1.16 = 40: the fine-detail threshold
    (46.0, 100.0, "main", 39.6552),      # just below 40
    (17.4, 100.0, "main", 15.0),         # the main-features threshold
    (17.0, 100.0, "discOnly", 14.6552),
    (3.5, 280.0, "discOnly", 8.4483),    # Neptune in a C11: 3.5 / 0.4143
    (44.0, 280.0, "fine", 106.2069),     # Jupiter in a C11
])
def test_detail_verdict(tmp_path, diameter, aperture, verdict, elements):
    """Resolution elements across the disc, diameter / (116 / aperture): at least 40 fine
    detail, at least 15 the main features, fewer the disc only."""
    got = _run(tmp_path, "result = NovaSolarRigFit.detail(input.d, input.a);", {"d": diameter, "a": aperture})
    assert got["value"] == verdict
    assert got["elements"] == pytest.approx(elements, abs=1e-3)


def test_detail_in_fit_needs_only_the_aperture(tmp_path):
    """The verdict comes from the aperture and the diameter alone (the camera and the
    amplification do not change it); a missing aperture is a dash; the Moon has none."""
    got = _fit(tmp_path, _tel(focal_length_mm=None), _cam(pixel_size_um=None), "jupiter", 44.0, amp=5)
    assert got["detail"] == {"ok": True, "value": "fine", "elements": pytest.approx(75.862, abs=1e-3)}
    assert _fit(tmp_path, _tel(aperture_mm=None), _cam(), "mars", 20.0)["detail"] == {"ok": False, "missing": "aperture"}
    assert _fit(tmp_path, _tel(), _cam(), "moon", 1860.0)["detail"] is None


@pytest.mark.parametrize("f_ratio, pixel, amp", [
    (10, 2.9, 1.5),     # ideal 14.5: k 1.45, nearest 1.5
    (5, 2.9, 3),        # k 2.9: 3 (log distance 0.034) beats 2.5 (0.148)
    (4, 3.76, 5),       # 18.8 / 4 = 4.7: 5 (0.062) beats 4 (0.161)
    (2, 3.76, 5),       # k 9.4: capped at the largest, 5
    (10, 2.0, 1),       # k 1.0: none
    (20, 2.9, 1),       # k 0.725: already slower than ideal, none
])
def test_best_amplification(tmp_path, f_ratio, pixel, amp):
    """The amplification closest to the ideal focal ratio on a log scale."""
    got = _run(tmp_path, "result = NovaSolarRigFit.bestAmplification(input.tel, input.cam);",
               {"tel": _tel(focal_length_mm=200.0 * f_ratio), "cam": _cam(pixel_size_um=pixel)})
    assert got == amp


def test_best_amplification_ties_and_missing_data(tmp_path):
    """On a log-scale tie the smaller one (k = sqrt(1.5 x 2) between 1.5 and 2); without
    the aperture, focal length or pixel size, none."""
    got = _run(tmp_path, """
        const R = NovaSolarRigFit;
        const k = Math.sqrt(3);
        result = [
            R.bestAmplification({focal_length_mm: 1000, aperture_mm: 100}, {pixel_size_um: 2 * k}),
            R.bestAmplification({focal_length_mm: 1000, aperture_mm: null}, {pixel_size_um: 2.9}),
            R.bestAmplification({focal_length_mm: null, aperture_mm: 100}, {pixel_size_um: 2.9}),
            R.bestAmplification({focal_length_mm: 1000, aperture_mm: 100}, {pixel_size_um: 0}),
            R.bestAmplification(null, null),
        ];
    """, {})
    assert got == [1.5, 1, 1, 1, 1]


def test_default_telescope_and_camera(tmp_path):
    """The largest aperture and the smallest pixel, the first on a tie; records without
    the value come last; an empty list gives null."""
    got = _run(tmp_path, """
        const R = NovaSolarRigFit;
        result = [
            R.defaultTelescope([{uid: 'a', aperture_mm: null}, {uid: 'b', aperture_mm: 80},
                                {uid: 'c', aperture_mm: 280}, {uid: 'd', aperture_mm: 280}]).uid,
            R.defaultTelescope([{uid: 'a', aperture_mm: null}, {uid: 'b', aperture_mm: -1}]).uid,
            R.defaultCamera([{uid: 'a', pixel_size_um: 0}, {uid: 'b', pixel_size_um: 3.76},
                             {uid: 'c', pixel_size_um: 2.9}, {uid: 'd', pixel_size_um: 2.9}]).uid,
            R.defaultTelescope([]), R.defaultCamera(null),
        ];
    """, {})
    assert got == ["c", "a", "c", None, None]


@pytest.mark.parametrize("amp", [0, 1.75, 6, -2, None, "2"])
def test_amplification_not_in_the_list_is_not_computable(tmp_path, amp):
    """Only the listed amplifications: anything else leaves the values that use the
    focal length not computable, never a silent 1x."""
    got = _fit(tmp_path, _tel(), _cam(), "jupiter", 40.0, amp=amp)
    for key in ("disc", "fRatio", "status"):
        assert got[key] == {"ok": False, "missing": "notComputable"}, key
    assert got["ideal"]["ok"] and got["detail"]["ok"] and got["rotation"]["ok"]
    moon = _fit(tmp_path, _tel(), _cam(), "moon", 1860.0, amp=amp)
    assert moon["mosaic"] == {"ok": False, "missing": "notComputable"}


@pytest.mark.parametrize("body, aperture, diameter, seconds", [
    ("jupiter", 200.0, 45.0, 146.588),   # 0.58" x 35730 s / (pi x 45") = 2 min 27 s (~2.4 min)
    ("saturn", 200.0, 18.0, 389.917),    # 0.58" x 38016 s / (pi x 18") = 6 min 30 s
    ("mars", 100.0, 20.0, 1636.32),      # 1.16" x 88632 s / (pi x 20") = 27 min 16 s
])
def test_rotation_limit(tmp_path, body, aperture, diameter, seconds):
    """t = (116 / aperture) x P / (pi x diameter), P = 9.925 h, 10.56 h, 24.62 h."""
    got = _fit(tmp_path, _tel(aperture_mm=aperture), _cam(), body, diameter)
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


def test_moon_mosaic_from_the_field(tmp_path):
    """An ASI462 (5.6 x 3.2 mm) at 1500 mm sees 12.83' x 7.33': the 31' Moon takes
    1 + ceil(18.17 / 10.27) = 3 by 1 + ceil(23.67 / 5.87) = 6 frames. An APS-C sensor
    (23.5 x 15.7 mm) at 1000 mm, 80.78' x 53.97', holds it in one; the same at 2x,
    40.6' x 27.1', takes 1 by 1 + ceil(3.9 / 21.7) = 2."""
    small = _fit(tmp_path, _tel(focal_length_mm=1500.0), _cam(), "moon", 1860.0)
    assert small["mosaic"] == {"ok": True, "cols": 3, "rows": 6, "panels": 18}
    assert small["rotation"] is None
    apsc = _cam(sensor_width_mm=23.5, sensor_height_mm=15.7)
    assert _fit(tmp_path, _tel(focal_length_mm=1000.0), apsc, "moon", 1860.0)["mosaic"]["panels"] == 1
    assert _fit(tmp_path, _tel(focal_length_mm=1000.0), apsc, "moon", 1860.0, amp=2)["mosaic"] == {
        "ok": True, "cols": 1, "rows": 2, "panels": 2}


@pytest.mark.parametrize("tel, cam, body, expected", [
    (None, {}, "jupiter",
     {"disc": "focalLength", "fRatio": "focalLength", "ideal": None, "status": "focalLength",
      "detail": "aperture", "rotation": "aperture"}),
    ({}, None, "moon",
     {"disc": "pixelSize", "fRatio": None, "ideal": "pixelSize", "status": "pixelSize", "mosaic": "sensorSize"}),
    ({"focal_length_mm": None}, {}, "jupiter",
     {"disc": "focalLength", "fRatio": "focalLength", "ideal": None, "status": "focalLength",
      "detail": None, "rotation": None}),
    # A missing aperture costs the focal ratio, its status, the verdict and the rotation
    # limit only; the server's helper would drop the image scale too.
    ({"aperture_mm": None}, {}, "saturn",
     {"disc": None, "fRatio": "aperture", "ideal": None, "status": "aperture",
      "detail": "aperture", "rotation": "aperture"}),
    ({}, {"pixel_size_um": None}, "mars",
     {"disc": "pixelSize", "fRatio": None, "ideal": "pixelSize", "status": "pixelSize",
      "detail": None, "rotation": None}),
    ({}, {"sensor_height_mm": None}, "moon",
     {"disc": None, "fRatio": None, "ideal": None, "status": None, "mosaic": "sensorSize"}),
])
def test_missing_fields_name_the_field(tmp_path, tel, cam, body, expected):
    """Each value is missing only for its own inputs, and says which field it lacks
    (None: the value is there). A null telescope or camera lacks every field."""
    t = None if tel is None else _tel(**tel)
    c = None if cam is None else _cam(**cam)
    got = _fit(tmp_path, t, c, body, 1860.0 if body == "moon" else 20.0)
    for key, code in expected.items():
        if code is None:
            assert got[key]["ok"] is True, key
        else:
            assert got[key] == {"ok": False, "missing": code}, key


def test_extra_values_per_body(tmp_path):
    """Rotation limit for Jupiter, Saturn and Mars only; mosaic for the Moon only; the
    detail verdict for every body but the Moon; the ring width for Saturn only."""
    got = _run(tmp_path, """
        result = ['mercury', 'venus', 'mars', 'jupiter', 'saturn', 'uranus', 'neptune', 'moon'].map(b => {
            const f = NovaSolarRigFit.fit(input.tel, input.cam, 1, b, b === 'moon' ? 1860 : 20);
            return [b, f.rotation !== null, f.mosaic !== null, f.detail !== null, f.disc.ringsPx !== null];
        });
    """, {"tel": _tel(), "cam": _cam()})
    assert got == [
        ["mercury", False, False, True, False], ["venus", False, False, True, False],
        ["mars", True, False, True, False], ["jupiter", True, False, True, False],
        ["saturn", True, False, True, True], ["uranus", False, False, True, False],
        ["neptune", False, False, True, False], ["moon", False, True, False, False],
    ]


@pytest.mark.parametrize("tel, cam, amp", [
    ((2800.0, 280.0), (2.9, 5.6, 3.2), 1),
    ((550.0, 100.0), (3.76, 23.5, 15.7), 1.5),
    ((1000.0, 127.0), (2.4, 11.1, 6.3), 2.5),
])
def test_metrics_match_the_server(tmp_path, tel, cam, amp):
    """Effective focal length, focal ratio, image scale and width of field as
    _compute_rig_metrics_from_components computes them with the amplification as the
    reducer/extender factor, and the height of field as /get_rig_data does."""
    fl, ap = tel
    px, sw, sh = cam
    got = _run(tmp_path, "result = NovaSolarRigFit.metrics(input.tel, input.cam, input.amp);",
               {"tel": _tel(focal_length_mm=fl, aperture_mm=ap), "amp": amp,
                "cam": _cam(pixel_size_um=px, sensor_width_mm=sw, sensor_height_mm=sh)})
    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(
        SimpleNamespace(name="t", focal_length_mm=fl, aperture_mm=ap),
        SimpleNamespace(name="c", pixel_size_um=px, sensor_width_mm=sw, sensor_height_mm=sh),
        SimpleNamespace(factor=amp) if amp != 1 else None)
    fov_h = math.degrees(2 * math.atan((sh / 2.0) / efl)) * 60.0
    assert got["efl"] == pytest.approx(efl, rel=1e-12)
    assert got["fRatio"] == pytest.approx(f_ratio, rel=1e-12)
    assert got["scale"] == pytest.approx(scale, rel=1e-12)
    assert got["fovW"] == pytest.approx(fov_w, rel=1e-12)
    assert got["fovH"] == pytest.approx(fov_h, rel=1e-12)


@pytest.mark.parametrize("diameter", [0, 0.0, -5.0])
def test_diameter_not_positive_is_not_computable(tmp_path, diameter):
    """No disc, verdict, rotation limit or mosaic from a diameter of 0 (or less); the
    focal ratio and its status do not depend on it."""
    jup = _fit(tmp_path, _tel(), _cam(), "jupiter", diameter)
    for key in ("disc", "detail", "rotation"):
        assert jup[key] == {"ok": False, "missing": "notComputable"}, key
    assert jup["fRatio"]["ok"] and jup["ideal"]["ok"] and jup["status"]["ok"]
    moon = _fit(tmp_path, _tel(), _cam(), "moon", diameter)
    assert moon["mosaic"] == {"ok": False, "missing": "notComputable"}


def test_overflowing_values_are_not_computable(tmp_path):
    """Values whose results overflow (a focal length near the largest double times an
    amplification) give 'notComputable', never Infinity or NaN: JSON would carry those as
    null, so every ok value here must be a finite number."""
    huge = _tel(focal_length_mm=1e308, aperture_mm=1.0)
    for body, d in (("jupiter", 40.0), ("saturn", 18.0), ("moon", 1860.0)):
        got = _fit(tmp_path, huge, _cam(), body, d, amp=5)
        for key in ("disc", "fRatio", "status") + (("mosaic",) if body == "moon" else ()):
            assert got[key] == {"ok": False, "missing": "notComputable"}, (body, key)
        assert got["ideal"] == {"ok": True, "value": pytest.approx(14.5)}
    big_px = _fit(tmp_path, _tel(), _cam(pixel_size_um=1e308), "jupiter", 40.0)
    assert big_px["ideal"] == {"ok": False, "missing": "notComputable"}
    assert big_px["status"] == {"ok": False, "missing": "notComputable"}
    tiny_ap = _fit(tmp_path, _tel(aperture_mm=1e-320), _cam(), "jupiter", 40.0)
    for key in ("fRatio", "status", "rotation"):
        assert tiny_ap[key] == {"ok": False, "missing": "notComputable"}, key
    assert tiny_ap["disc"]["ok"] is True


def test_ok_values_are_always_finite(tmp_path):
    """Across ordinary and extreme setups, amplifications and bodies, every number in an
    ok value is finite."""
    got = _run(tmp_path, """
        const tels = [input.tel,
            Object.assign({}, input.tel, {focal_length_mm: 1e308}),
            Object.assign({}, input.tel, {aperture_mm: 1e308}),
            Object.assign({}, input.tel, {aperture_mm: 1e-320})];
        const cams = [input.cam,
            Object.assign({}, input.cam, {pixel_size_um: 1e-320}),
            Object.assign({}, input.cam, {sensor_width_mm: 1e-320, sensor_height_mm: 1e-320})];
        const bad = [];
        for (const t of tels) for (const c of cams) for (const a of [1, 5])
            for (const b of ['mars', 'jupiter', 'saturn', 'venus', 'moon'])
                for (const d of [1e-300, 0.5, 40, 1860, 1e300]) {
                    const f = NovaSolarRigFit.fit(t, c, a, b, d);
                    for (const k of ['disc', 'fRatio', 'ideal', 'status', 'detail', 'rotation', 'mosaic']) {
                        const v = f[k];
                        if (!v || !v.ok) continue;
                        for (const [name, x] of Object.entries(v))
                            if (typeof x === 'number' && !isFinite(x)) bad.push([t.focal_length_mm, b, d, k, name, String(x)]);
                    }
                }
        result = bad;
    """, {"tel": _tel(), "cam": _cam()})
    assert got == []


def _summary_fit(status, detail):
    """A fit as summary() reads it: the status and, for a planet, the detail verdict
    (None: missing; 'moon': no verdict)."""
    return {"status": {"ok": True, "value": status} if status else {"ok": False, "missing": "aperture"},
            "detail": None if detail == "moon"
            else {"ok": True, "value": detail, "elements": 50} if detail
            else {"ok": False, "missing": "aperture"}}


@pytest.mark.parametrize("status, detail, alt, level", [
    ("onTarget", "fine", 30.0, "excellent"),     # the altitude threshold counts
    ("onTarget", "fine", 29.9, "limited"),
    ("onTarget", "main", 30.0, "good"),
    ("above", "fine", 30.0, "good"),
    ("below", "main", 45.0, "good"),             # main off target: good, not limited
    ("onTarget", "discOnly", 80.0, "limited"),
    (None, "fine", 60.0, "limited"),             # no focal-ratio status
    ("onTarget", None, 60.0, "limited"),         # no detail verdict for a planet
    ("onTarget", "fine", None, "limited"),       # no altitude
    ("onTarget", "moon", 30.0, "excellent"),     # the Moon: on target alone
    ("above", "moon", 30.0, "good"),
    ("onTarget", "moon", 29.9, "limited"),
    (None, "moon", 60.0, "limited"),
])
def test_summary_level(tmp_path, status, detail, alt, level):
    """Excellent: fine detail and on target, at least 30 degrees up (the Moon: on target);
    good: any other planet that shows more than its disc, at least 30 degrees up; limited:
    the disc only, low, or a needed value missing."""
    got = _run(tmp_path, "result = NovaSolarRigFit.summary(input.f, input.alt, 1.5, 1.5);",
               {"f": _summary_fit(status, detail), "alt": alt})
    assert got["level"] == level


@pytest.mark.parametrize("status, detail, alt, best, current, step", [
    ("below", "fine", 20.0, 2, 1, {"kind": "low", "altitude": 20.0}),     # low first
    ("onTarget", "discOnly", 10.0, 1, 1, {"kind": "low", "altitude": 10.0}),
    ("below", "fine", 45.0, 2, 1, {"kind": "amp", "amp": 2}),
    ("below", "fine", None, 2, 1, {"kind": "amp", "amp": 2}),              # no altitude: no low step
    ("above", "fine", 45.0, 1, 1, {"kind": "bestAlready"}),                # already the best
    ("below", "discOnly", 45.0, 3, 1, {"kind": "amp", "amp": 3}),          # amplification before small disc
    ("onTarget", "discOnly", 45.0, 1.5, 1.5, {"kind": "smallDisc"}),
    ("onTarget", "fine", 45.0, 1.5, 1.5, {"kind": "onTarget", "detail": "fine"}),
    ("onTarget", "main", 45.0, 1.5, 1.5, {"kind": "onTarget", "detail": "main"}),
    ("onTarget", "moon", 45.0, 1.5, 1.5, {"kind": "onTarget", "detail": None}),
    (None, "fine", 45.0, 1, 1, None),                                      # no status: no step
])
def test_summary_next_step(tmp_path, status, detail, alt, best, current, step):
    """The most useful next step: low, then the amplification (or that the current one
    is already the best), then the small disc, then on target."""
    got = _run(tmp_path, "result = NovaSolarRigFit.summary(input.f, input.alt, input.best, input.current);",
               {"f": _summary_fit(status, detail), "alt": alt, "best": best, "current": current})
    assert got["step"] == step


# ------------------------------------------------------------------ scale preview

def _preview(tmp_path, tel, cam, body, diameter, amp=1, illuminated=1.0, tilt=None):
    return _run(tmp_path, "result = NovaSolarRigFit.preview(input.tel, input.cam, input.amp, input.body, "
                          "input.d, input.k, input.tilt);",
                {"tel": tel, "cam": cam, "amp": amp, "body": body, "d": diameter, "k": illuminated, "tilt": tilt})


def _arc(path, n):
    """The n-th arc of an SVG path: (rx, ry, sweep)."""
    arcs = re.findall(r"A (\S+) (\S+) 0 0 ([01])", path)
    rx, ry, sweep = arcs[n]
    return float(rx), float(ry), int(sweep)


def test_preview_sensor_and_disc_at_scale(tmp_path):
    """The sensor in pixels, 5.6 mm / 2.9 µm = 1931 by 3.2 mm / 2.9 µm = 1103; Jupiter at
    45" and 2000 mm, 150.46 px across, drawn at exact scale at the sensor's centre."""
    got = _preview(tmp_path, _tel(), _cam(), "jupiter", 45.0)
    assert got["ok"] and got["sensor"] == {"w": 1931, "h": 1103}
    d = got["disc"]
    assert d["px"] == pytest.approx(150.4593, abs=1e-3) and d["r"] == pytest.approx(d["px"] / 2)
    assert (d["cx"], d["cy"]) == (1931 / 2, 1103 / 2) and d["notToScale"] is False
    assert got["phase"] is None and got["rings"] is None and got["mosaic"] is None
    vb = got["viewBox"]
    assert vb["x"] < 0 and vb["y"] < 0 and vb["x"] + vb["w"] > 1931 and vb["y"] + vb["h"] > 1103


@pytest.mark.parametrize("sw, sh, px, w, h", [
    (23.5, 15.7, 3.76, 6250, 4176),     # APS-C, 3:2
    (4.8, 3.6, 3.75, 1280, 960),        # ASI120-sized, 4:3
    (5.6, 5.6, 2.0, 2800, 2800),        # square
])
def test_preview_sensor_aspect(tmp_path, sw, sh, px, w, h):
    """The rectangle's pixel dimensions, so its aspect ratio, from sensor size / pixel size."""
    got = _preview(tmp_path, _tel(), _cam(sensor_width_mm=sw, sensor_height_mm=sh, pixel_size_um=px), "mars", 20.0)
    assert got["sensor"] == {"w": w, "h": h}
    assert w / h == pytest.approx(sw / sh, rel=1e-3)


@pytest.mark.parametrize("tilt, back_sweep, front_sweep", [
    (26.7, 1, 0),     # north face in view: the near half is below the centre, in front
    (-10.0, 0, 1),    # south face: the near half is above
    (0.0, 1, 0),      # edge-on: a line
])
def test_preview_saturn_rings(tmp_path, tilt, back_sweep, front_sweep):
    """Outer diameter SATURN_RING_FACTOR x the globe, minor half-axis rx |sin B|; the far
    half drawn behind the globe, the near half in front of it."""
    got = _preview(tmp_path, _tel(focal_length_mm=2800.0), _cam(), "saturn", 18.0, tilt=tilt)
    rings, d = got["rings"], got["disc"]
    assert rings["rx"] == pytest.approx(d["px"] * 136775 / 60268 / 2, rel=1e-9)
    assert rings["ry"] == pytest.approx(rings["rx"] * abs(math.sin(math.radians(tilt))), abs=1e-9)
    assert _arc(rings["back"], 0)[2] == back_sweep and _arc(rings["front"], 0)[2] == front_sweep
    assert _arc(rings["back"], 0)[:2] == pytest.approx((rings["rx"], rings["ry"]))
    inner = _arc(rings["back"], 1)
    assert inner[0] == pytest.approx(rings["rx"] * 92000 / 136775)


@pytest.mark.parametrize("fraction, minor, sweep", [
    (0.5, 0.0, 1),      # half: the terminator is a straight line
    (0.25, 0.5, 0),     # crescent: the terminator bulges to the lit (right) side
    (0.75, 0.5, 1),     # gibbous: it bulges to the left
    (0.0, 1.0, 0),      # new: nothing lit
    (0.9, 0.8, 1),      # Mars-like
])
def test_phase_path(tmp_path, fraction, minor, sweep):
    """The lit right half, back along the terminator: an ellipse of half-width r |2k - 1|."""
    path = _run(tmp_path, "result = NovaSolarRigFit.phasePath(100, 50, 40, input.k);", {"k": fraction})
    assert path.startswith("M 100 10 A 40 40 0 0 1 100 90 ")
    rx, ry, s = _arc(path, 1)
    assert rx == pytest.approx(40 * minor) and ry == 40 and s == sweep


def test_phase_only_for_phase_bodies_and_not_full(tmp_path):
    """Mercury, Venus, the Moon and Mars show their phase; full (>= 99.5 %) or another
    planet: a plain disc."""
    venus = _preview(tmp_path, _tel(), _cam(), "venus", 30.0, illuminated=0.4)
    assert venus["phase"]["fraction"] == 0.4 and venus["phase"]["path"].startswith("M ")
    assert _preview(tmp_path, _tel(), _cam(), "venus", 30.0, illuminated=0.996)["phase"] is None
    assert _preview(tmp_path, _tel(), _cam(), "jupiter", 40.0, illuminated=0.5)["phase"] is None
    assert _preview(tmp_path, _tel(), _cam(), "mars", 20.0, illuminated=0.9)["phase"] is not None


def test_preview_moon_mosaic_grid(tmp_path):
    """The Mosaic line's 3 x 6 grid (1500 mm, ASI462, 31'): one frame per panel, each the
    sensor's size, neighbours 80 % of a frame apart, covering the disc; no ROI. In one
    frame (APS-C at 1000 mm): no grid."""
    got = _preview(tmp_path, _tel(focal_length_mm=1500.0), _cam(), "moon", 1860.0)
    fit = _fit(tmp_path, _tel(focal_length_mm=1500.0), _cam(), "moon", 1860.0)
    m, d, W, H = got["mosaic"], got["disc"], got["sensor"]["w"], got["sensor"]["h"]
    assert (m["cols"], m["rows"]) == (fit["mosaic"]["cols"], fit["mosaic"]["rows"]) == (3, 6)
    assert len(m["frames"]) == 18 and all(f["w"] == W and f["h"] == H for f in m["frames"])
    xs = sorted({f["x"] for f in m["frames"]}); ys = sorted({f["y"] for f in m["frames"]})
    assert [b - a for a, b in zip(xs, xs[1:])] == pytest.approx([0.8 * W] * 2)
    assert [b - a for a, b in zip(ys, ys[1:])] == pytest.approx([0.8 * H] * 5)
    assert xs[0] <= d["cx"] - d["r"] and xs[-1] + W >= d["cx"] + d["r"]
    assert ys[0] <= d["cy"] - d["r"] and ys[-1] + H >= d["cy"] + d["r"]
    assert got["roi"] is None
    one = _preview(tmp_path, _tel(focal_length_mm=1000.0), _cam(sensor_width_mm=23.5, sensor_height_mm=15.7), "moon", 1860.0)
    assert one["mosaic"] is None and one["roi"] is None


@pytest.mark.parametrize("w, h, W, H, roi", [
    (50, 50, 1931, 1103, [320, 240]),
    (200, 200, 1931, 1103, [640, 480]),      # 250 px with the margin: taller than 240
    (309, 309, 1931, 1103, [640, 480]),
    (700, 300, 1931, 1103, [1024, 768]),     # Saturn with its rings: 875 px wide
    (900, 900, 1931, 1103, None),            # 1125 px: no common ROI holds it
    (300, 300, 640, 480, None),              # 640 x 480 is the whole sensor, not smaller
    (100, 100, 300, 200, None),              # the sensor is smaller than the smallest ROI
])
def test_roi_choice(tmp_path, w, h, W, H, roi):
    """The smallest common ROI with 25 % margin around the body, if smaller than the sensor."""
    got = _run(tmp_path, "result = NovaSolarRigFit.roiFor(input.w, input.h, input.W, input.H);",
               {"w": w, "h": h, "W": W, "H": H})
    assert (None if got is None else [got["w"], got["h"]]) == roi


def test_preview_roi_uses_saturns_rings_and_centres_it(tmp_path):
    """Saturn's ROI holds the rings (2.27 x the globe), so it is larger than the globe's own."""
    tel = _tel(focal_length_mm=4200.0, aperture_mm=280.0)
    sat = _preview(tmp_path, tel, _cam(), "saturn", 18.0, tilt=20.0)
    ringed = sat["rings"]["rx"] * 2
    assert sat["roi"]["w"] >= ringed * 1.25
    assert (sat["roi"]["x"] + sat["roi"]["w"] / 2, sat["roi"]["y"] + sat["roi"]["h"] / 2) == (
        sat["disc"]["cx"], sat["disc"]["cy"])
    globe_only = _run(tmp_path, "result = NovaSolarRigFit.roiFor(input.p, input.p, 1931, 1103);",
                      {"p": sat["disc"]["px"]})
    assert sat["roi"]["w"] > globe_only["w"]


def test_preview_tiny_disc_is_drawn_not_to_scale(tmp_path):
    """Neptune (2.3") at 500 mm with 3.76 µm pixels (1.55"/px) is 1.5 px on a 6250 px wide
    sensor, a speck at the laid-out width: drawn PREVIEW_MIN_DISC px
    across at PREVIEW_WIDTH (the drawing is the sensor plus 5 % each side) and flagged."""
    cam = _cam(sensor_width_mm=23.5, sensor_height_mm=15.7, pixel_size_um=3.76)
    got = _preview(tmp_path, _tel(focal_length_mm=500.0, aperture_mm=100.0), cam, "neptune", 2.3)
    d = got["disc"]
    assert d["px"] == pytest.approx(1.4828, abs=1e-3) and d["notToScale"] is True
    assert 2 * d["r"] == pytest.approx(4 * 6250 * 1.1 / 320)
    assert d["r"] * 2 * 320 / got["viewBox"]["w"] == pytest.approx(4.0)
    big = _preview(tmp_path, _tel(), _cam(), "jupiter", 45.0)
    assert big["disc"]["notToScale"] is False


@pytest.mark.parametrize("tel, cam, d, code", [
    ({}, {"sensor_width_mm": None}, 40.0, "sensorSize"),
    ({}, {"pixel_size_um": None}, 40.0, "pixelSize"),
    ({"focal_length_mm": None}, {}, 40.0, "focalLength"),
    ({}, {}, 0.0, "notComputable"),
])
def test_preview_missing_data(tmp_path, tel, cam, d, code):
    """Without the data for the disc or the sensor: no preview, the field that is missing."""
    got = _preview(tmp_path, _tel(**tel), _cam(**cam), "jupiter", d)
    assert got == {"ok": False, "missing": code}


@pytest.mark.parametrize("sw, sh, px, sensor", [
    (7.68, 4.32, 2.0, {"w": 3840, "h": 2160}),    # ZWO ASI678MC as specified
    (3840.0, 2160.0, 2.0, None),                  # its pixel counts entered as mm
    (77.0, 43.0, 2.0, None),                      # a side over 60 mm
    (50.0, 30.0, 2.0, None),                      # 25 000 px wide: over 20 000
    (60.0, 40.0, 3.0, {"w": 20000, "h": 13333}),  # both limits reached, not passed
])
def test_sensor_plausibility(tmp_path, sw, sh, px, sensor):
    """A sensor size that cannot be millimetres (a side over 60 mm or over 20 000 px) gives
    'sensorImplausible' for the preview instead of a sensor millions of pixels wide."""
    cam = _cam(sensor_width_mm=sw, sensor_height_mm=sh, pixel_size_um=px)
    got = _preview(tmp_path, _tel(focal_length_mm=4200.0, aperture_mm=280.0), cam, "jupiter", 44.0)
    if sensor is None:
        assert got == {"ok": False, "missing": "sensorImplausible"}
    else:
        assert got["ok"] and got["sensor"] == sensor


def test_implausible_sensor_keeps_the_values_that_do_not_use_it(tmp_path):
    """Pixel counts entered as mm cost only the sensor's values (the Moon's mosaic); the
    disc, focal ratio, detail and rotation limit stay, and a missing size still comes first."""
    cam = _cam(sensor_width_mm=3840.0, sensor_height_mm=2160.0, pixel_size_um=2.0)
    jup = _fit(tmp_path, _tel(), cam, "jupiter", 44.0)
    for key in ("disc", "fRatio", "ideal", "status", "detail", "rotation"):
        assert jup[key]["ok"] is True, key
    moon = _fit(tmp_path, _tel(), cam, "moon", 1860.0)
    assert moon["mosaic"] == {"ok": False, "missing": "sensorImplausible"}
    assert moon["disc"]["ok"] and moon["fRatio"]["ok"]
    no_height = _fit(tmp_path, _tel(), _cam(sensor_width_mm=3840.0, sensor_height_mm=None), "moon", 1860.0)
    assert no_height["mosaic"] == {"ok": False, "missing": "sensorSize"}
