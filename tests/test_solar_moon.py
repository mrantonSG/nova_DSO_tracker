"""Checks for the Moon card's rules (static/js/nova_solar_moon.js) and its data
(static/js/nova_moon_features.js), in node with the real engines.

The scripts load the way the page loads them: plain scripts in one vm context, in the
page's order, so NovaSolarMoon finds NovaAstro, NovaPlanets, NovaMoon, NovaMoonFeatures,
NovaDashboardRows and NovaSolarRows as globals.

nova_moon.js itself (libration, colongitude, the Sun's altitude over the surface) is
validated in nova-ios against the IAU rotation model of the Moon (tools/
test_moon_libration.cjs). Here the Lunar X instants are checked against that model
once more, computed in Python on ephem's positions: ephem's own colongitude is not used,
libastro's is up to 0.36 deg off near the quarters.
"""

import json
import math
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ephem
import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
TEMPLATE = REPO / "templates" / "solar_system.html"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
              "nova_solar_rows.js", "nova_solar_calendar.js", "nova_moon.js",
              "nova_moon_features.js", "nova_solar_moon.js")

LONDON = {"lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15}
SANTIAGO = {"lat": -33.45, "lon": -70.67, "timezone": "America/Santiago",
            "altitude_threshold": 20, "sampling_interval": 15}

# 2 Nov 2026 20:00 UTC: the wall clock the cases run at
NOW_MS = 1793649600000

TYPES = {"crater", "mare", "oceanus", "sinus", "lacus", "mons", "montes", "vallis", "rupes",
         "rima", "promontorium"}

# The well-known targets the selection must hold (Anton's list), and the Lunar X and V
NAMED = ["Copernicus", "Tycho", "Clavius", "Plato", "Archimedes", "Aristarchus", "Gassendi",
         "Ptolemaeus", "Alphonsus", "Arzachel", "Theophilus", "Cyrillus", "Catharina",
         "Posidonius", "Petavius", "Langrenus", "Rupes Recta", "Vallis Alpes",
         "Vallis Schröteri", "Montes Apenninus", "Sinus Iridum", "Mare Crisium",
         "Mare Orientale", "Mare Humboldtianum", "Mare Smythii", "Mare Marginis", "Werner",
         "Blanchinus", "La Caille", "Purbach", "Ukert"]

# Loads the scripts into one context, then runs the case's body, which returns the
# result from `input`; the driver writes it as JSON.
_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [bodyFile, inFile, outFile, ...files] = process.argv.slice(2);
const context = { console: console };
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
context.input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const result = vm.runInContext('(function () {' + fs.readFileSync(bodyFile, 'utf8') + '})()', context);
fs.writeFileSync(outFile, JSON.stringify(result));
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, body, order=PAGE_ORDER, **input_):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "body.js"
    case.write_text(body)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(input_))
    out = tmp_path / "out.json"
    run = subprocess.run([NODE, str(driver), str(case), str(inp), str(out)]
                         + [str(STATIC_JS / name) for name in order],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def _opts(loc=LONDON, sim=None):
    return {"inputs": {"location": loc}, "nowMs": NOW_MS, "simDate": sim}


# --- the data -----------------------------------------------------------------------------

def _features(tmp_path):
    return _run(tmp_path, "return { features: NovaMoonFeatures.FEATURES, source: NovaMoonFeatures.SOURCE };",
                order=("nova_moon_features.js",))


def test_feature_data_is_sound(tmp_path):
    got = _features(tmp_path)
    features = got["features"]
    assert len(features) == 80
    names = [f["name"] for f in features]
    assert len(set(names)) == len(names)
    for f in features:
        assert set(f) == {"name", "type", "lat", "lon", "diameterKm", "tier", "limb"}, f
        assert f["type"] in TYPES, f
        assert -90 <= f["lat"] <= 90 and -180 <= f["lon"] <= 180, f
        assert f["diameterKm"] > 0 and f["tier"] in (1, 2), f
        if f["limb"]:
            assert abs(f["lon"]) > 55, f"{f['name']} is flagged limb at longitude {f['lon']}"
    assert sum(f["limb"] for f in features) == 17
    assert got["source"] == {"credit": "Gazetteer of Planetary Nomenclature, IAU WGPSN / USGS Astrogeology",
                             "date": "2026-11-01"}


def test_feature_data_holds_the_named_targets_in_the_iau_convention(tmp_path):
    """Latin names with diacritics; longitude east-positive (Mare Crisium at +59)."""
    by = {f["name"]: f for f in _features(tmp_path)["features"]}
    assert [n for n in NAMED if n not in by] == []
    assert by["Mare Crisium"]["lon"] == pytest.approx(59.1, abs=0.05)
    assert by["Copernicus"]["lon"] == pytest.approx(-20.08, abs=0.05)
    assert by["Werner"]["type"] == "crater" and by["Montes Apenninus"]["type"] == "montes"
    assert by["Mons Pico"]["type"] == "mons" and by["Rupes Recta"]["type"] == "rupes"


def test_every_feature_type_has_a_translation_key():
    block = re.search(r"types: \{\n(.*?)\n\s*\}\n", TEMPLATE.read_text(), re.S).group(1)
    assert set(re.findall(r"^\s*(\w+):", block, re.M)) == TYPES


def test_moon_strings_never_say_east_or_west():
    """The Moon's sides are named by their features, never east or west (whose meaning
    on the Moon flipped in 1961)."""
    text = TEMPLATE.read_text()
    moon = text[text.index("    moon: {"):text.index("    seeing: {")]
    lunar = re.findall(r"_\((['\"])(.*?)\1", moon) + re.findall(r"lunar\w*:\s*\{\{ _\('(.*?)'\)", text)
    for match in lunar:
        s = match[1] if isinstance(match, tuple) else match
        assert not re.search(r"\b(east|west)", s, re.I), s


# --- tonight: the terminator -------------------------------------------------------------

_TONIGHT = r"""
const o = input.opts;
const t = NovaSolarMoon.tonight(o);
const M = NovaMoon, S = NovaSolarMoon;
// The rule, recomputed for every feature
const wide = S.WIDE_TYPES;
const rule = NovaMoonFeatures.FEATURES.map(f => {
    const h = M.sunAltitude(t.geometry, f.lat, f.lon);
    const r = Math.min(S.RADIUS_CAP_DEG, f.diameterKm / 2 / 1737.4 * 180 / Math.PI);
    const hMax = wide.indexOf(f.type) >= 0 ? S.BAND_WIDE_DEG : S.BAND_DEG;
    return { name: f.name, h: h, inBand: h >= -r && h <= hMax + r,
             facing: M.fromCentre(t.geometry, f.lat, f.lon) < S.VISIBLE_DEG,
             morning: M.isMorning(t.geometry, f.lat, f.lon) };
});
// The night's own Moon curve, for the instant
const N = NovaSolarRows._night, W = NovaDashboardRows._wall;
const s = N.site(o.inputs);
const win = N.nightWindow(t.date, s, NovaAstro, W, null);
const times = N.samples(win.start, win.end, s.interval);
const curve = NovaPlanets.altAzCurve('moon', times, s.lat, s.lon);
return { t: t, rule: rule, times: times, alts: Array.from(curve.alt), win: win };
"""


def _tonight(tmp_path, loc=LONDON, sim=None):
    return _run(tmp_path, _TONIGHT, opts=_opts(loc, sim))


def test_first_quarter_lists_the_morning_terminator(tmp_path):
    """17 Nov 2026, just past first quarter: the central craters and the Apennine-Alps
    region on the morning side, the Sun low over each."""
    got = _tonight(tmp_path, sim="2026-11-17")
    t = got["t"]
    names = [f["name"] for f in t["features"]]
    for name in ("Archimedes", "Rupes Recta", "Alphonsus", "Ptolemaeus", "Arzachel"):
        assert name in names
    assert all(f["morning"] for f in t["features"])
    assert t["empty"] is None and not t["muted"]


def test_waning_moon_lists_the_evening_terminator(tmp_path):
    got = _tonight(tmp_path, sim="2026-11-02")
    t = got["t"]
    assert t["features"] and not any(f["morning"] for f in t["features"])
    assert "Copernicus" in [f["name"] for f in t["features"]]


@pytest.mark.parametrize("loc,sim", [(LONDON, "2026-11-17"), (LONDON, "2026-11-02"),
                                     (SANTIAGO, "2026-11-19"), (SANTIAGO, "2027-03-12")])
def test_the_list_is_exactly_the_rule_ranked(tmp_path, loc, sim):
    """Every feature on the facing side whose Sun altitude is in its band, and no other;
    ranked by tier, closeness to the ideal altitude, then size; top is the first eight."""
    got = _tonight(tmp_path, loc, sim)
    t, rule = got["t"], got["rule"]
    expected = {r["name"] for r in rule if r["inBand"] and r["facing"]} if t["illuminated"] >= 0.05 else set()
    assert {f["name"] for f in t["features"]} == expected
    by = {r["name"]: r for r in rule}
    for f in t["features"]:
        assert f["sunAlt"] == pytest.approx(by[f["name"]]["h"], abs=1e-9)
        assert f["morning"] == by[f["name"]]["morning"]
        assert f["nearLimb"] == (f["fromCentre"] > 75)
    key = lambda f: (f["tier"], abs(f["sunAlt"] - 4), -f["diameterKm"], f["name"])
    assert t["features"] == sorted(t["features"], key=key)
    assert t["top"] == t["features"][:8] and t["more"] == t["features"][8:]


def test_tonight_is_the_moons_highest_sample_of_the_night(tmp_path):
    got = _tonight(tmp_path, sim="2026-11-17")
    t, alts, times = got["t"], got["alts"], got["times"]
    k = max(range(len(alts)), key=lambda i: alts[i])
    assert t["instantUtc"] == times[k] and t["maxAlt"] == pytest.approx(alts[k])
    assert got["win"]["start"] <= t["instantUtc"] <= got["win"]["end"]


def test_moon_below_the_horizon_all_night_is_muted_at_the_midpoint(tmp_path):
    """9 Dec 2026 from London: a day before New Moon, never above the horizon at night."""
    got = _tonight(tmp_path, sim="2026-12-09")
    t, win = got["t"], got["win"]
    assert t["muted"] and t["mutedReason"] == "notUp" and t["maxAlt"] < 0
    assert t["instantUtc"] == round((win["start"] + win["end"]) / 2)
    assert t["empty"] == "new" and t["features"] == []


def test_moon_above_the_horizon_but_below_the_threshold_is_muted(tmp_path):
    loc = dict(LONDON, altitude_threshold=60)
    t = _tonight(tmp_path, loc, "2026-11-17")["t"]
    assert 0 <= t["maxAlt"] < 60 and t["muted"] and t["mutedReason"] == "altitude"
    assert t["features"], "the features stay, muted"


def test_live_and_simulated_tonight_agree(tmp_path):
    """Live at 20:00 UTC on 2 Nov and simulated 2 Nov are the same night and instant."""
    live = _tonight(tmp_path)["t"]
    sim = _tonight(tmp_path, sim="2026-11-02")["t"]
    assert live["date"] == sim["date"] == "2026-11-02"
    assert live["instantUtc"] == sim["instantUtc"] and live["features"] == sim["features"]


# --- libration ---------------------------------------------------------------------------

def test_libration_levels():
    got = _run_levels()
    assert got == ["none", "moderate", "moderate", "favourable", "favourable", "excellent", "excellent"]


def _run_levels():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        return _run(Path(d), "return [0, 3, 4.99, 5, 6.99, 7, 9].map(NovaSolarMoon.levelOf);")


_FAKE = r"""
// A geometry with only what hint() reads: libration, the subsolar point, colongitude
const fake = (lon, lat, sunLon) => ({ libration: { lon: lon, lat: lat },
    subsolar: { lon: sunLon, lat: 0 }, colongitude: ((90 - sunLon) % 360 + 360) % 360 });
"""


def test_hint_names_the_limb_by_its_features(tmp_path):
    """Turned 6 deg to the Mare Crisium side with that side lit: named by Mare Crisium,
    then the other tier-1 limb features in the sector by size. Unlit, it says so."""
    got = _run(tmp_path, _FAKE + r"""
        return { lit: NovaSolarMoon.hint(fake(6, 0, 30)), dark: NovaSolarMoon.hint(fake(6, 0, -120)),
                 south: NovaSolarMoon.hint(fake(0, -6.5, 0)), other: NovaSolarMoon.hint(fake(-7.5, 0, -60)) };
    """)
    lit = got["lit"]
    assert lit["level"] == "favourable" and lit["amount"] == pytest.approx(6)
    assert lit["direction"] == pytest.approx(90)
    assert lit["side"] == "Mare Crisium" and lit["also"] == ["Mare Smythii", "Mare Marginis"]
    assert lit["litLimb"] is True
    assert got["dark"]["side"] == "Mare Crisium" and got["dark"]["litLimb"] is False
    assert got["south"]["side"] in ("Bailly", "Mare Australe", "Schickard", "Wargentin")
    assert got["other"]["side"] == "Mare Orientale" and got["other"]["level"] == "excellent"


def test_hint_always_names_a_side_over_a_year(tmp_path):
    """Every 13 hours through 2027: some visible limb feature always names the side, and
    the named features are within the sector unless it was the fallback."""
    got = _run(tmp_path, r"""
        const out = [];
        for (let t = Date.UTC(2027, 0, 1); t < Date.UTC(2028, 0, 1); t += 13 * 3600000) {
            const g = NovaMoon.geometry(t);
            const h = NovaSolarMoon.hint(g);
            const f = NovaMoonFeatures.FEATURES.find(x => x.name === h.side);
            const off = Math.abs(((NovaSolarMoon.directionOf(f.lat, f.lon) - h.direction + 540) % 360) - 180);
            out.push({ side: h.side, off: off, also: h.also.length, facing: NovaMoon.fromCentre(g, f.lat, f.lon) });
        }
        return out;
    """)
    assert len(got) > 600
    assert all(r["side"] and r["facing"] < 90 for r in got)
    in_sector = [r for r in got if r["off"] <= 45]
    assert len(in_sector) / len(got) > 0.95
    assert all(r["also"] == 0 for r in got if r["off"] > 45), "the fallback names one feature"


def test_hint_display_says_dark_when_the_side_is_unlit(tmp_path):
    """Turned towards the Mare Crisium side: favourable while it is lit, 'dark' with no
    other names while it is not; no libration to speak of stays 'none' either way."""
    got = _run(tmp_path, _FAKE + r"""
        return { lit: NovaSolarMoon.hint(fake(6, 0, 30)), dark: NovaSolarMoon.hint(fake(6, 0, -120)),
                 weakDark: NovaSolarMoon.hint(fake(2, 0, -120)) };
    """)
    assert got["lit"]["display"] == "favourable" and got["lit"]["litLimb"]
    assert got["dark"]["display"] == "dark" and got["dark"]["also"] == []
    assert got["weakDark"]["level"] == "none" and got["weakDark"]["display"] == "none"


def test_hint_rule_over_a_year(tmp_path):
    """Every 13 hours through 2027: litLimb is the Sun over the side's own feature, every
    other named feature is lit, and display is the level unless the side is dark."""
    got = _run(tmp_path, r"""
        const out = [];
        const by = Object.fromEntries(NovaMoonFeatures.FEATURES.map(f => [f.name, f]));
        for (let t = Date.UTC(2027, 0, 1); t < Date.UTC(2028, 0, 1); t += 13 * 3600000) {
            const g = NovaMoon.geometry(t);
            const h = NovaSolarMoon.hint(g);
            const alt = n => NovaMoon.sunAltitude(g, by[n].lat, by[n].lon);
            out.push({ h: h, sideAlt: alt(h.side), alsoAlts: h.also.map(alt) });
        }
        return out;
    """)
    assert {r["h"]["display"] for r in got} >= {"dark", "favourable", "excellent"}
    for r in got:
        h = r["h"]
        assert h["litLimb"] == (r["sideAlt"] > 0)
        assert all(a > 0 for a in r["alsoAlts"])
        if not h["litLimb"]:
            assert h["also"] == []
        assert h["display"] == (h["level"] if h["level"] == "none" or h["litLimb"] else "dark")


# --- next good nights --------------------------------------------------------------------

def test_next_nights(tmp_path):
    got = _run(tmp_path, r"""
        const o = input.opts;
        const n = NovaSolarMoon.nextNights(o);
        const checks = n.nights.map(x => {
            const g = NovaMoon.geometry(x.utcMs);
            return { date: x.date, alt: NovaPlanets.altAz('moon', x.utcMs, o.inputs.location.lat, o.inputs.location.lon).alt,
                     hits: NovaMoonFeatures.FEATURES.filter(f => {
                         const h = NovaMoon.sunAltitude(g, f.lat, f.lon);
                         const r = Math.min(5, f.diameterKm / 2 / 1737.4 * 180 / Math.PI);
                         const hMax = NovaSolarMoon.WIDE_TYPES.indexOf(f.type) >= 0 ? 15 : 10;
                         return h >= -r && h <= hMax + r && NovaMoon.fromCentre(g, f.lat, f.lon) < 85;
                     }).map(f => f.name) };
        });
        return { n: n, checks: checks };
    """, opts=_opts(sim="2026-11-02"))
    n, checks = got["n"], {c["date"]: c for c in got["checks"]}
    dates = [x["date"] for x in n["nights"]]
    assert dates == sorted(dates) and dates[0] > "2026-11-02" and dates[-1] <= "2026-12-02"
    assert all(c["alt"] >= 20 for c in checks.values()), "only nights with the Moon at or above the threshold"
    assert len(n["byName"]) == 80
    for name, ds in n["byName"].items():
        assert len(ds) <= 3 and ds == sorted(ds)
        for d in ds:
            assert name in checks[d]["hits"], (name, d)
    assert n["byName"]["Copernicus"], "Copernicus passes the terminator within a month"


# --- the calendar ------------------------------------------------------------------------

_CALENDAR = r"""
const o = input.opts;
const c = NovaSolarMoon.calendarRows(o);
const planets = NovaSolarCalendar.events(o);
const s = NovaSolarRows._night.site(o.inputs);
const sunAlt = t => {
    const p = NovaAstro.sunPosition(t);
    const ha = NovaAstro.gast(t, p.nut) + s.lon * Math.PI / 180 - p.ra;
    return Math.asin(Math.sin(s.lat * Math.PI / 180) * Math.sin(p.dec)
        + Math.cos(s.lat * Math.PI / 180) * Math.cos(p.dec) * Math.cos(ha)) * 180 / Math.PI;
};
const rows = c.rows.map(r => {
    const g = NovaMoon.geometry(r.utcMs);
    let seen = false;
    for (let t = r.utcMs - 7200000; t <= r.utcMs + 7200000; t += s.interval * 60000) {
        if (sunAlt(t) < NovaAstro.SUN_RISE_SET_ALT && NovaPlanets.altAz('moon', t, s.lat, s.lon).alt >= s.threshold) seen = true;
    }
    const amount = t => NovaMoon.tilt(NovaMoon.geometry(t)).amount;
    return Object.assign({}, r, { colong: g.colongitude, seen: seen,
        amount: amount(r.utcMs), before: amount(r.utcMs - 3600000), after: amount(r.utcMs + 3600000) });
});
return { window: c.window, rows: rows, planetWindow: planets.window, planetKeys: Object.keys(planets.rows[0]).sort() };
"""


def _calendar(tmp_path, loc=LONDON):
    return _run(tmp_path, _CALENDAR, opts=_opts(loc))


@pytest.fixture(scope="module")
def london_calendar(tmp_path_factory):
    return _calendar(tmp_path_factory.mktemp("cal"))


def test_calendar_rows_share_the_calendar_window_and_shape(london_calendar):
    got = london_calendar
    assert got["window"] == got["planetWindow"]
    for r in got["rows"]:
        assert got["window"]["start"] <= r["utcMs"] <= got["window"]["end"]
        assert r["body"] == "moon" and r["bodies"] == ["moon"]
        extra = {"colong", "seen", "amount", "before", "after", "libration"}
        assert sorted(set(r) - extra) == got["planetKeys"]
    assert [r["utcMs"] for r in got["rows"]] == sorted(r["utcMs"] for r in got["rows"])


def test_calendar_libration_rows_follow_the_cards_rule(london_calendar):
    """A libration night is one the Moon card would call excellent: never a dark side."""
    libs = [r for r in london_calendar["rows"] if r["kind"] == "libration"]
    assert libs and all(r["libration"]["display"] == "excellent" for r in libs)


def test_lunar_x_once_a_lunation_at_its_colongitude(london_calendar):
    xs = [r for r in london_calendar["rows"] if r["kind"] == "lunarX"]
    assert 24 <= len(xs) <= 28
    for r in xs:
        assert abs((r["colong"] - 358 + 180) % 360 - 180) < 0.01
    gaps = [(b["utcMs"] - a["utcMs"]) / 86400000 for a, b in zip(xs, xs[1:])]
    assert all(29.2 < g < 29.9 for g in gaps), gaps


def test_lunar_x_is_muted_unless_seen_in_a_dark_sky_near_it(london_calendar):
    xs = [r for r in london_calendar["rows"] if r["kind"] == "lunarX"]
    assert any(r["muted"] for r in xs) and any(not r["muted"] for r in xs)
    for r in xs:
        assert r["muted"] == (not r["seen"])
        assert r["mutedReason"] == ("lunarWindow" if r["muted"] else None)


def test_libration_nights_are_lit_peaks_of_seven_degrees(london_calendar):
    libs = [r for r in london_calendar["rows"] if r["kind"] == "libration"]
    assert 15 <= len(libs) <= 40
    for r in libs:
        assert r["amount"] >= 7 and r["before"] < r["amount"] and r["after"] < r["amount"]
        assert r["libration"]["litLimb"] and r["libration"]["level"] == "excellent"
        assert r["libration"]["side"]
        assert r["muted"] == (r["maxAlt"] < 20)
        assert r["mutedReason"] == ("altitude" if r["muted"] else None)


# The IAU rotation model of the Moon (Archinal et al. 2011), as in nova-ios's
# tools/gen_moon_libration_fixture.py: the colongitude from the sub-solar point.
def _iau_colongitude(utc):
    date = ephem.Date(utc)
    moon, sun = ephem.Moon(), ephem.Sun()
    moon.compute(date)
    sun.compute(date)
    au = 149597870.7
    dist = moon.earth_distance * au
    d = float(date) + 2415020.0 + ephem.delta_t(date) / 86400.0 - 2451545.0 - dist / 299792.458 / 86400.0
    T = d / 36525.0
    e = [None] + [math.radians(a + b * d) for a, b in [
        (125.045, -0.0529921), (250.089, -0.1059842), (260.008, 13.0120009), (176.625, 13.3407154),
        (357.529, 0.9856003), (311.589, 26.4057084), (134.963, 13.0649930), (276.617, 0.3287146),
        (34.226, 1.7484877), (15.134, -0.1589763), (119.743, 0.0036096), (239.961, 0.1643573),
        (25.053, 12.9590088)]]
    s, c = math.sin, math.cos
    a0 = math.radians(269.9949 + 0.0031 * T - 3.8787 * s(e[1]) - 0.1204 * s(e[2]) + 0.0700 * s(e[3])
                      - 0.0172 * s(e[4]) + 0.0072 * s(e[6]) - 0.0052 * s(e[10]) + 0.0043 * s(e[13]))
    d0 = math.radians(66.5392 + 0.0130 * T + 1.5419 * c(e[1]) + 0.0239 * c(e[2]) - 0.0278 * c(e[3])
                      + 0.0068 * c(e[4]) - 0.0029 * c(e[6]) + 0.0009 * c(e[7]) + 0.0008 * c(e[10])
                      - 0.0009 * c(e[13]))
    W = math.radians((38.3213 + 13.17635815 * d - 1.4e-12 * d * d + 3.5610 * s(e[1]) + 0.1208 * s(e[2])
                      - 0.0642 * s(e[3]) + 0.0158 * s(e[4]) + 0.0252 * s(e[5]) - 0.0066 * s(e[6])
                      - 0.0047 * s(e[7]) - 0.0046 * s(e[8]) + 0.0028 * s(e[9]) + 0.0052 * s(e[10])
                      + 0.0040 * s(e[11]) + 0.0019 * s(e[12]) - 0.0044 * s(e[13])) % 360.0)

    def vec(ra, dec, r):
        return [r * c(dec) * c(ra), r * c(dec) * s(ra), r * s(dec)]

    def rz(t, u):
        return [c(t) * u[0] + s(t) * u[1], -s(t) * u[0] + c(t) * u[1], u[2]]

    def rx(t, u):
        return [u[0], c(t) * u[1] + s(t) * u[2], -s(t) * u[1] + c(t) * u[2]]

    rm = vec(float(moon.a_ra), float(moon.a_dec), dist)
    rs = vec(float(sun.a_ra), float(sun.a_dec), sun.earth_distance * au)
    v = rz(W, rx(math.pi / 2 - d0, rz(math.pi / 2 + a0, [b - a for a, b in zip(rm, rs)])))
    return (90.0 - math.degrees(math.atan2(v[1], v[0]))) % 360.0


def test_lunar_x_instants_match_the_iau_model(london_calendar):
    """Each Lunar X instant against the moment the IAU model's colongitude reaches 358
    deg (bisected within 6 h of it): within 10 minutes (the engine is within about 0.015
    deg of the model, some 2 minutes of the Moon's motion)."""
    worst = 0.0
    for r in [r for r in london_calendar["rows"] if r["kind"] == "lunarX"]:
        at = datetime.fromtimestamp(r["utcMs"] / 1000, timezone.utc)
        lo, hi = at - timedelta(hours=6), at + timedelta(hours=6)
        f = lambda t: (_iau_colongitude(t) - 358 + 180) % 360 - 180
        assert f(lo) < 0 < f(hi)
        while (hi - lo).total_seconds() > 10:
            mid = lo + (hi - lo) / 2
            if f(mid) < 0:
                lo = mid
            else:
                hi = mid
        worst = max(worst, abs((lo - at).total_seconds()) / 60)
    assert worst < 10, f"worst {worst:.1f} min"


# --- the diagram ------------------------------------------------------------------------

def _arcs(path):
    """The path's commands: ('M', x, y) and ('A', rx, ry, rot, large, sweep, x, y)."""
    tokens = path.replace(",", " ").split()
    out, i = [], 0
    while i < len(tokens):
        cmd = tokens[i]
        if cmd == "M":
            out.append(("M", float(tokens[i + 1]), float(tokens[i + 2])))
            i += 3
        elif cmd == "A":
            out.append(("A",) + tuple(float(x) for x in tokens[i + 1:i + 8]))
            i += 8
        elif cmd == "Z":
            i += 1
        else:
            raise AssertionError(f"unexpected {cmd} in {path}")
    return out


def _arc_midpoint(x1, y1, rx, ry, rot, large, sweep, x2, y2):
    """The point halfway along an SVG elliptical arc (SVG 1.1, F.6.5 and F.6.6)."""
    phi = math.radians(rot)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = math.cos(phi) * dx + math.sin(phi) * dy
    y1p = -math.sin(phi) * dx + math.cos(phi) * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = math.cos(phi) * cxp - math.sin(phi) * cyp + (x1 + x2) / 2
    cy = math.sin(phi) * cxp + math.cos(phi) * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if sweep and dt < 0:
        dt += 2 * math.pi
    elif not sweep and dt > 0:
        dt -= 2 * math.pi
    t = t1 + dt / 2
    px, py = rx * math.cos(t), ry * math.sin(t)
    return (math.cos(phi) * px - math.sin(phi) * py + cx, math.sin(phi) * px + math.cos(phi) * py + cy)


_DIAGRAM = r"""
const out = [];
for (const t of input.times) {
    const g = NovaMoon.geometry(t);
    const sp = NovaMoon.project(g, g.subsolar.lat, g.subsolar.lon);
    const feats = NovaMoonFeatures.FEATURES.slice(0, 40);
    const views = {};
    for (const v of NovaSolarMoon.VIEWS) views[v] = NovaSolarMoon.diagram(g, v, feats);
    out.push({ sp: sp, tilt: NovaMoon.tilt(g), views: views,
               facing: feats.map(f => NovaMoon.project(g, f.lat, f.lon).z > 0) });
}
return out;
"""


def test_diagram_lit_part_faces_the_sun_with_the_terminator_at_its_phase(tmp_path):
    """Through a lunation: the limb arc's middle is the bright limb (the subsolar point's
    direction on the disc), the terminator's middle lies at -k along it (k the cosine of
    the Earth-Moon-Sun angle), in every view. Within 0.002 of the disc's radius (a
    quarter pixel at the card's 240 px): a half arc's centre is ill-conditioned in SVG's
    endpoint form, so the path's six decimals come back as about three."""
    start = 1794700800000  # 15 Nov 2026
    times = [start + k * 31 * 3600000 for k in range(22)]
    got = _run(tmp_path, _DIAGRAM, times=times)
    checked = 0
    for case in got:
        sp = case["sp"]
        n = math.hypot(sp["x"], sp["y"])
        for view, d in case["views"].items():
            sx = -1 if view != "normal" else 1
            sy = 1 if view == "inverted" else -1
            ux, uy = sx * sp["x"] / n, sy * sp["y"] / n
            k = sp["z"]
            if d["lit"] is None:
                assert k <= -0.9995
                continue
            cmds = _arcs(d["lit"])
            if len(cmds) == 3 and k >= 0.9995:
                continue
            m, limb, term = cmds
            mx, my = _arc_midpoint(m[1], m[2], *limb[1:])
            assert mx == pytest.approx(ux, abs=2e-3) and my == pytest.approx(uy, abs=2e-3), view
            tx, ty = _arc_midpoint(limb[6], limb[7], *term[1:])
            assert tx == pytest.approx(-k * ux, abs=2e-3) and ty == pytest.approx(-k * uy, abs=2e-3), view
            checked += 1
    assert checked >= 60


def test_diagram_views_mirror_and_turn_the_markers(tmp_path):
    got = _run(tmp_path, _DIAGRAM, times=[1794700800000])[0]
    v = got["views"]
    normal = {m["n"]: m for m in v["normal"]["markers"]}
    assert len(normal) == sum(got["facing"]), "a marker for every feature facing us"
    for view, sx, sy in (("diagonal", -1, 1), ("inverted", -1, -1)):
        for m in v[view]["markers"]:
            assert m["x"] == pytest.approx(sx * normal[m["n"]]["x"])
            assert m["y"] == pytest.approx(sy * normal[m["n"]]["y"])
    assert v["normal"]["north"]["y"] < -0.9 and v["inverted"]["north"]["y"] > 0.9
    crisium = next(m for m in v["normal"]["markers"] if m["name"] == "Mare Crisium")
    assert crisium["x"] > 0.7, "Mare Crisium on the right, north up"
    t = got["tilt"]
    a = v["normal"]["arrow"]
    assert a["x"] == pytest.approx(math.sin(math.radians(t["direction"])), abs=1e-6)
    assert a["y"] == pytest.approx(-math.cos(math.radians(t["direction"])), abs=1e-6)


def test_diagram_full_and_new(tmp_path):
    got = _run(tmp_path, r"""
        const g = s => ({ libration: { lon: 0, lat: 0 }, subsolar: { lon: s, lat: 0 }, colongitude: 90 - s });
        return { full: NovaSolarMoon.diagram(g(0), 'normal', []), fresh: NovaSolarMoon.diagram(g(180), 'normal', []) };
    """)
    assert got["full"]["lit"] and got["full"]["terminator"] is None
    assert got["fresh"]["lit"] is None and got["fresh"]["terminator"] is None


# --- errors ------------------------------------------------------------------------------

def test_missing_engines_and_bad_input_throw(tmp_path):
    got = _run(tmp_path, r"""
        const tries = {};
        const catchIt = (name, fn) => { try { fn(); tries[name] = null; } catch (e) { tries[name] = e.message; } };
        const ctx = this;
        const saved = ctx.NovaMoon;
        ctx.NovaMoon = undefined;
        catchIt('engine', () => NovaSolarMoon.tonight(input.opts));
        ctx.NovaMoon = saved;
        catchIt('lat', () => NovaSolarMoon.tonight(Object.assign({}, input.opts,
            { inputs: { location: Object.assign({}, input.opts.inputs.location, { lat: 'x' }) } })));
        catchIt('view', () => NovaSolarMoon.diagram(NovaMoon.geometry(input.opts.nowMs), 'sideways', []));
        return tries;
    """, opts=_opts())
    assert "nova_moon.js" in got["engine"]
    assert "lat" in got["lat"]
    assert "sideways" in got["view"]
